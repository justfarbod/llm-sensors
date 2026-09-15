import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from open_webui.internal.db import Base
from open_webui.models.groups import Group, GroupMember
from open_webui.models.question_submissions import QuestionSubmission
from open_webui.models.survey_submissions import SurveySubmission, SurveyResponse, SurveyResponseChoice
from open_webui.models.survey_tasks import SurveyTask, SurveyQuestion, SurveyChoice
from open_webui.routers import chats, tasks, users
from open_webui.routers import experiment_analytics as experiment_analytics_router
from open_webui.routers.experiment_analytics import (
    ExportRequest,
    FullSessionExportRequest,
    _anonymous_id,
    _duration,
    _export_response,
    _file_metadata_payload,
    _flatten_tab_event,
    _full_session_json_stream,
    _histogram,
    _json_value,
    _paginate_rows,
    _participant_payload,
    _row_payload,
    _session_tasks_export,
    _session_row,
    _tab_event_row,
    export_full_sessions,
    router,
)
from open_webui.models.experiments import ExperimentSession
from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.files import File
from open_webui.models.users import User
from open_webui.utils.auth import get_admin_user
from open_webui.utils.experiments import require_chat_access_dependency, require_non_experiment_user_dependency


def test_dashboard_routes_are_admin_only_and_match_contract():
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {route.path for route in routes} == {
        '/workflows',
        '/workflows/{workflow_id}',
        '/workflows/{workflow_id}/steps/{step_key}',
        '/filters',
        '/overview',
        '/participants',
        '/sessions/{session_id}',
        '/sessions/{session_id}/tab-activity',
        '/essays',
        '/usage',
        '/essay-stats',
        '/surveys',
        '/export/participants',
        '/export/sessions',
        '/export/essays',
        '/export/perturbations',
        '/export/surveys',
        '/export/tab-activity',
        '/question-results',
        '/question-submissions/{submission_id}',
        '/question-responses/{response_id}/score',
        '/question-responses/{response_id}/retry',
    }
    for route in routes:
        assert any(dependency.call is get_admin_user for dependency in route.dependant.dependencies)


def test_restricted_chat_task_and_settings_policies_are_wired_to_routes():
    assert any(dependency.dependency is require_chat_access_dependency for dependency in chats.router.dependencies)

    task_routes = {route.path: route for route in tasks.router.routes if isinstance(route, APIRoute)}
    for path in ('/follow_up/completions', '/auto/completions'):
        assert any(
            dependency.call is require_non_experiment_user_dependency
            for dependency in task_routes[path].dependant.dependencies
        )

    for route in [
        route for route in users.router.routes if isinstance(route, APIRoute) and route.path == '/user/settings'
    ]:
        assert any(
            dependency.call is require_non_experiment_user_dependency for dependency in route.dependant.dependencies
        )


def test_anonymous_participant_ids_are_stable_and_do_not_expose_user_id():
    first = _anonymous_id('private-user-id')
    assert first == _anonymous_id('private-user-id')
    assert first.startswith('P-')
    assert 'private-user-id' not in first


def test_full_export_request_defaults_to_anonymized_and_allows_large_selections():
    form = FullSessionExportRequest(ids=['session'])
    assert form.anonymized is True
    assert form.ids == ['session']


def test_full_export_serialization_preserves_raw_fields_and_json_numbers():
    session = ExperimentSession(
        id='session',
        user_id='private-user-id',
        group_id='group',
        state='IN_PROGRESS',
        created_at=1_000_000_000,
        updated_at=2_000_000_000,
    )
    identified = _row_payload(session)
    anonymized = _row_payload(session, anonymized=True)
    assert identified['created_at'] == 1_000_000_000
    assert identified['user_id'] == 'private-user-id'
    assert anonymized['user_id'] == _anonymous_id('private-user-id')
    assert _json_value(Decimal('12.34')) == 12.34


def test_full_export_participant_payload_excludes_account_secrets():
    participant = User(
        id='user',
        email='person@example.com',
        username='person',
        role='user',
        name='Person',
        oauth={'secret': 'not-exported'},
        scim={'secret': 'not-exported'},
        settings={'private': True},
        info={'private': True},
        last_active_at=1,
        created_at=2,
        updated_at=3,
    )
    identified = _participant_payload(participant, anonymized=False)
    anonymized = _participant_payload(participant, anonymized=True)
    assert identified['email'] == 'person@example.com'
    assert not {'oauth', 'scim', 'settings', 'info'} & identified.keys()
    assert anonymized['id'] == _anonymous_id('user')
    assert not {'name', 'username', 'email'} & anonymized.keys()


def test_full_export_stream_has_stable_envelope_and_session_order(monkeypatch):
    async def payload(_db, session, anonymized):
        return {'session': {'id': session.id}, 'anonymized': anonymized}

    monkeypatch.setattr(experiment_analytics_router, '_full_session_payload', payload)
    sessions = [SimpleNamespace(id='second'), SimpleNamespace(id='first')]

    async def collect_chunks():
        return [
            chunk
            async for chunk in _full_session_json_stream(
                SimpleNamespace(), sessions, anonymized=True, exported_at='2026-08-17T00:00:00+00:00'
            )
        ]

    chunks = asyncio.run(collect_chunks())
    document = json.loads(''.join(chunks))
    assert document['schema_version'] == '1.0'
    assert document['anonymized'] is True
    assert [item['session']['id'] for item in document['sessions']] == ['second', 'first']


def test_full_export_endpoint_deduplicates_ids_and_rejects_missing_sessions(monkeypatch):
    class ScalarRows:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return ScalarRows(self.rows)

    class Database:
        def __init__(self, rows):
            self.rows = rows

        async def execute(self, _statement):
            return Result(self.rows)

    async def payload(_db, session, anonymized):
        return {'session': {'id': session.id}, 'anonymized': anonymized}

    monkeypatch.setattr(experiment_analytics_router, '_full_session_payload', payload)
    rows = [SimpleNamespace(id='second'), SimpleNamespace(id='first')]
    response = asyncio.run(
        export_full_sessions(
            FullSessionExportRequest(ids=['second', 'second', 'first'], anonymized=False),
            user=SimpleNamespace(),
            db=Database(rows),
        )
    )

    async def response_document():
        return json.loads(''.join([chunk async for chunk in response.body_iterator]))

    document = asyncio.run(response_document())
    assert [item['session']['id'] for item in document['sessions']] == ['second', 'first']

    try:
        asyncio.run(
            export_full_sessions(
                FullSessionExportRequest(ids=['second', 'missing']),
                user=SimpleNamespace(),
                db=Database([rows[0]]),
            )
        )
    except HTTPException as error:
        assert error.status_code == 404
        assert error.detail['missing_ids'] == ['missing']
    else:
        raise AssertionError('Missing session IDs must fail the full export.')


def test_full_export_keeps_incomplete_session_tasks_structured():
    task = ExperimentSessionTask(
        id='task',
        experiment_session_id='session',
        position=0,
        task_type='ESSAY',
        title='Draft essay',
        status='ACTIVE',
        survey_required=True,
        created_at=1,
        updated_at=2,
    )
    payloads = asyncio.run(
        _session_tasks_export(
            SimpleNamespace(),
            [task],
            {},
            {'question_tasks': {}, 'survey_tasks': {}, 'topics': {}, 'files': {}},
            anonymized=True,
        )
    )
    assert payloads[0]['record']['id'] == 'task'
    assert payloads[0]['question_task'] is None
    assert payloads[0]['survey_task'] is None
    assert payloads[0]['essay'] is None
    assert payloads[0]['question_submission'] is None
    assert payloads[0]['survey_submission'] is None


def test_full_export_attachment_metadata_excludes_content_and_server_path():
    file = File(
        id='file',
        user_id='user',
        hash='hash',
        filename='notes.txt',
        path='/private/server/path',
        data={'content': 'extracted content'},
        meta={'content_type': 'text/plain', 'size': 42, 'user_id': 'user'},
        created_at=1,
        updated_at=2,
    )

    class Database:
        async def get(self, _model, _file_id):
            return file

    payload = asyncio.run(_file_metadata_payload(Database(), 'file', {}, anonymized=True))
    assert payload['filename'] == 'notes.txt'
    assert payload['content_type'] == 'text/plain'
    assert payload['size'] == 42
    assert payload['meta']['user_id'] == _anonymous_id('user')
    assert not {'path', 'data', 'user_id'} & payload.keys()


def test_duration_handles_missing_and_invalid_historical_values():
    assert _duration(None, 2) is None
    assert _duration(2, 1) is None
    assert _duration(1_000_000_000, 4_000_000_000) == 3


def test_participant_pagination_search_and_sorting():
    rows = [
        {'participant_id': 'P-B', 'name': 'Beta', 'state': 'NOT_STARTED'},
        {'participant_id': 'P-A', 'name': 'Alpha', 'state': 'COMPLETED'},
    ]
    items, total = _paginate_rows(rows, page=1, limit=1, search='alpha', order_by='name', direction='asc')
    assert total == 1
    assert items[0]['participant_id'] == 'P-A'


def test_session_dto_shape_handles_missing_historical_data():
    session = SimpleNamespace(
        id='session',
        user_id='user',
        group_id='group',
        topic_id='topic',
        topic_title='Topic',
        state='IN_PROGRESS',
        consented_at=None,
        pre_survey=None,
        essay_id=None,
        post_survey=None,
        created_at=1_000_000_000,
        completed_at=None,
        writing_started_at=None,
        essay_submitted_at=None,
        post_survey_submitted_at=None,
    )
    row = _session_row(session, None, None, None, {})
    assert row['session_id'] == 'session'
    assert row['participant_id'].startswith('P-')
    assert row['session_duration'] is None
    assert row['essay_word_count'] is None
    assert row['total_tokens'] == 0
    assert row['total_keystrokes'] == 0
    assert row['telemetry_summary']['total_time_away_ms'] == 0


def test_session_dto_uses_ordered_essay_task_topic_and_submission():
    session = SimpleNamespace(
        id='session',
        user_id='user',
        group_id='group',
        topic_id=None,
        topic_title=None,
        state='COMPLETED',
        consented_at=None,
        pre_survey=None,
        essay_id=None,
        post_survey=None,
        created_at=1_000_000_000,
        completed_at=None,
        writing_started_at=None,
        essay_submitted_at=None,
        post_survey_submitted_at=None,
    )
    task = SimpleNamespace(
        essay_topic_id='topic',
        essay_topic_title='Task topic',
        essay_id='essay',
    )
    essay = SimpleNamespace(word_count=12, character_count=64)
    row = _session_row(session, None, None, essay, {}, essay_task=task)
    assert row['topic_id'] == 'topic'
    assert row['topic_title'] == 'Task topic'
    assert row['essay_id'] == 'essay'
    assert row['essay_submitted'] is True
    assert row['essay_word_count'] == 12


def test_participant_list_and_detail_include_pipeline_tasks_and_survey_answers(monkeypatch):
    monkeypatch.setattr('open_webui.internal.db.DATABASE_ENABLE_SESSION_SHARING', True)

    async def check():
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                timestamps = {'created_at': 1_000_000_000, 'updated_at': 5_000_000_000}
                db.add_all(
                    [
                        User(id='participant', name='Participant', role='user'),
                        Group(
                            id='group',
                            user_id='admin',
                            name='Study',
                            description='',
                            data={'config': {'experiment_mode_enabled': True}},
                            **timestamps,
                        ),
                        GroupMember(id='member', group_id='group', user_id='participant'),
                        ExperimentSession(
                            id='session',
                            user_id='participant',
                            group_id='group',
                            state='COMPLETED',
                            completed_at=5_000_000_000,
                            **timestamps,
                        ),
                        SurveyTask(id='survey-template', family_id='survey-family', title='Survey', **timestamps),
                    ]
                )
                stages = []
                for position, (task_id, task_type, title) in enumerate(
                    [
                        ('pre', 'SURVEY', 'Pre-survey'),
                        ('essay', 'ESSAY', 'Essay'),
                        ('questions', 'QUESTION', 'Knowledge task'),
                        ('post', 'SURVEY', 'Post-survey'),
                        ('optional', 'SURVEY', 'Optional follow-up'),
                    ]
                ):
                    stage = ExperimentSessionTask(
                        id=task_id,
                        experiment_session_id='session',
                        position=position,
                        task_type=task_type,
                        title=title,
                        status='SKIPPED' if task_id == 'optional' else 'FINALIZED',
                        survey_task_id='survey-template' if task_type == 'SURVEY' else None,
                        survey_required=task_id != 'optional',
                        started_at=1_000_000_000,
                        finalized_at=4_000_000_000,
                        **timestamps,
                    )
                    stages.append(stage)
                db.add_all(stages)
                db.add(
                    QuestionSubmission(
                        id='question-submission',
                        session_task_id='questions',
                        user_id='participant',
                        status='SUBMITTED',
                        grading_status='COMPLETED',
                        current_score=Decimal('0'),
                        maximum_score=Decimal('5'),
                        submitted_at=3_000_000_000,
                        **timestamps,
                    )
                )
                for position, (question_id, question_type, prompt) in enumerate(
                    [
                        ('choice', 'SINGLE_CHOICE', 'Experience?'),
                        ('scale', 'SCALE', 'Helpfulness?'),
                        ('text', 'SHORT_TEXT', 'Comments?'),
                    ]
                ):
                    db.add(
                        SurveyQuestion(
                            id=question_id,
                            task_id='survey-template',
                            question_type=question_type,
                            prompt=prompt,
                            position=position,
                            **timestamps,
                        )
                    )
                db.add(SurveyChoice(id='often', question_id='choice', text='Often', position=0))
                for task_id in ('pre', 'post', 'optional'):
                    db.add(
                        SurveySubmission(
                            id=f'{task_id}-submission',
                            session_task_id=task_id,
                            user_id='participant',
                            status='SKIPPED' if task_id == 'optional' else 'SUBMITTED',
                            submitted_at=None if task_id == 'optional' else 3_000_000_000,
                            skipped_at=4_000_000_000 if task_id == 'optional' else None,
                            **timestamps,
                        )
                    )
                # Insert responses out of order: detail must follow the survey's question order.
                for question_id, values in [
                    ('text', {'text_answer': 'Helpful feedback'}),
                    ('choice', {}),
                    ('scale', {'scale_answer': 4}),
                ]:
                    db.add(
                        SurveyResponse(
                            id=f'post-{question_id}',
                            submission_id='post-submission',
                            question_id=question_id,
                            is_answered=True,
                            **values,
                            **timestamps,
                        )
                    )
                db.add(SurveyResponseChoice(response_id='post-choice', choice_id='often'))
                await db.commit()

                rows = await experiment_analytics_router._participant_rows(
                    db, experiment_analytics_router.DashboardFilters()
                )
                assert len(rows) == 1
                assert [task['title'] for task in rows[0]['tasks']] == [stage.title for stage in stages]
                assert rows[0]['tasks'][-1]['status'] == 'SKIPPED'
                # Pipeline answers do not live in the legacy JSON survey columns.
                assert rows[0]['pre_survey_completed'] is True
                assert rows[0]['post_survey_completed'] is True
                assert rows[0]['pre_survey_submitted_at'] == 3
                assert rows[0]['post_survey_submitted_at'] == 3
                detail = await experiment_analytics_router.session_detail('session', user=SimpleNamespace(), db=db)
                assert detail['pre_survey'] is None
                assert detail['post_survey'] is None
                assert detail['pre_survey_completed'] is True
                assert detail['post_survey_completed'] is True
                assert detail['timeline']['pre_survey_submitted_at'] == 3
                assert detail['timeline']['post_survey_submitted_at'] == 3
                assert detail['timeline']['pre_survey_started_at'] == 1
                assert detail['timeline']['post_survey_started_at'] == 1
                assert detail['timeline']['essay_submitted_at'] == 4
                assert detail['timeline']['task_submitted_at'] == 4
                assert detail['pre_survey_duration'] == 2
                assert detail['post_survey_duration'] == 2
                assert detail['tasks'][0]['survey_submission']['status'] == 'SUBMITTED'
                question = detail['tasks'][2]['question_submission']
                assert question['submission_id'] == 'question-submission'
                assert question['score'] == 0
                assert question['maximum_score'] == 5
                assert question['submitted_at'] == 3
                survey = detail['tasks'][3]['survey_submission']
                assert [answer['prompt'] for answer in survey['answers']] == [
                    'Experience?',
                    'Helpfulness?',
                    'Comments?',
                ]
                assert [answer['value'] for answer in survey['answers']] == [['Often'], 4, 'Helpful feedback']
                assert detail['tasks'][4]['survey_submission']['skipped_at'] == 4

                overview = await experiment_analytics_router.overview(user=SimpleNamespace(), db=db)
                assert overview['metrics']['pre_survey_completed'] == 1
                assert overview['metrics']['post_survey_completed'] == 1
                assert overview['metrics']['essays_submitted'] == 1
                survey_results = await experiment_analytics_router.surveys(
                    page=1, limit=25, user=SimpleNamespace(), db=db
                )
                assert survey_results['responses']['items'][0]['pre_survey_completed'] is True
                assert survey_results['responses']['items'][0]['post_survey_completed'] is True
                exported = await experiment_analytics_router.export_participants(
                    ExportRequest(ids=['session'], format='json'), user=SimpleNamespace(), db=db
                )
                exported_rows = json.loads(''.join([chunk async for chunk in exported.body_iterator]))
                assert exported_rows[0]['post_survey_completed'] is True
                assert exported_rows[0]['post_survey_submitted_at'] == 3

                unfinished = ExperimentSessionTask(
                    id='unfinished', position=0, title='Questions', task_type='QUESTION', status='LOCKED'
                )
                empty = await experiment_analytics_router._session_task_details(db, [unfinished])
                assert empty[0]['question_submission'] is None
                assert empty[0]['started_at'] is None
        finally:
            await engine.dispose()

    asyncio.run(check())


def test_survey_milestones_follow_positions_and_distinguish_skipped_and_unfinished_surveys():
    session = ExperimentSession(id='session')
    tasks = [
        ExperimentSessionTask(id='pre-1', position=0, task_type='SURVEY', started_at=1_000_000_000),
        ExperimentSessionTask(id='pre-2', position=1, task_type='SURVEY', started_at=2_000_000_000),
        ExperimentSessionTask(
            id='question', position=2, task_type='QUESTION', status='FINALIZED', finalized_at=5_000_000_000
        ),
        ExperimentSessionTask(id='between', position=3, task_type='SURVEY'),
        ExperimentSessionTask(
            id='question-2', position=4, task_type='QUESTION', status='FINALIZED', finalized_at=7_000_000_000
        ),
        ExperimentSessionTask(id='post', position=5, task_type='SURVEY', started_at=8_000_000_000),
    ]
    submissions = {
        'pre-1': SurveySubmission(status='SUBMITTED', submitted_at=2_000_000_000),
        'pre-2': SurveySubmission(status='DRAFT'),
        'between': SurveySubmission(status='SUBMITTED', submitted_at=6_000_000_000),
        'post': SurveySubmission(status='SKIPPED', skipped_at=9_000_000_000),
    }
    milestone = experiment_analytics_router._session_milestones(session, tasks, submissions)
    assert milestone['pre_survey_completed'] is False
    assert milestone['pre_survey_submitted_at'] is None
    assert milestone['post_survey_completed'] is False
    assert milestone['post_survey_submitted_at'] is None
    assert milestone['post_survey_skipped_at'] == 9
    assert milestone['essay_submitted'] is False
    assert milestone['task_submitted_at'] == 7

    submissions['pre-2'] = SurveySubmission(status='SUBMITTED', submitted_at=3_000_000_000)
    milestone = experiment_analytics_router._session_milestones(session, tasks, submissions)
    assert milestone['pre_survey_completed'] is True
    assert milestone['pre_survey_submitted_at'] == 3
    assert milestone['pre_survey_duration'] == 2

    # A survey between work tasks must not become a pre- or post-survey.
    middle_only = experiment_analytics_router._session_milestones(session, tasks[2:5], submissions)
    assert middle_only['pre_survey_completed'] is False
    assert middle_only['post_survey_completed'] is False


def test_legacy_survey_completion_times_are_preserved():
    session = ExperimentSession(
        pre_survey={'answer': 1},
        post_survey={'answer': 2},
        pre_survey_submitted_at=2_000_000_000,
        post_survey_submitted_at=8_000_000_000,
        essay_id='essay',
        writing_started_at=3_000_000_000,
        essay_submitted_at=6_000_000_000,
    )
    milestone = experiment_analytics_router._session_milestones(session, [], {})
    assert milestone['pre_survey_completed'] is True
    assert milestone['post_survey_completed'] is True
    assert milestone['pre_survey_submitted_at'] == 2
    assert milestone['post_survey_submitted_at'] == 8
    assert milestone['post_survey_duration'] == 2
    assert milestone['writing_duration'] == 3


def test_histogram_handles_empty_and_zero_usage():
    assert _histogram([]) == []
    assert sum(item['value'] for item in _histogram([0, 0, 0])) == 3


def test_anonymized_exports_remove_identity_fields():
    rows = [
        {
            'session_id': 'session',
            'user_id': 'user',
            'participant_id': 'P-123',
            'name': 'Name',
            'username': 'name',
            'email': 'name@example.com',
        }
    ]
    response = _export_response(rows, ExportRequest(ids=['session'], format='json', anonymized=True), 'test')
    assert response.media_type == 'application/json'
    assert rows == [{'session_id': 'session', 'participant_id': 'P-123'}]


def test_participant_exports_keep_summary_json_but_flatten_csv():
    json_rows = [{'session_id': 'session', 'total_keystrokes': 12, 'telemetry_summary': {'total_keystrokes': 12}}]
    _export_response(json_rows, ExportRequest(ids=['session'], format='json'), 'test')
    assert json_rows[0]['telemetry_summary'] == {'total_keystrokes': 12}
    assert 'telemetry_events' not in json_rows[0]

    csv_rows = [{'session_id': 'session', 'total_keystrokes': 12, 'telemetry_summary': {'total_keystrokes': 12}}]
    _export_response(csv_rows, ExportRequest(ids=['session'], format='csv'), 'test')
    assert csv_rows == [{'session_id': 'session', 'total_keystrokes': 12}]


def test_tab_activity_keeps_structured_json_and_flattens_csv_without_redacting_urls():
    event = SimpleNamespace(
        id='event',
        experiment_session_id='session',
        user_id='private-user',
        event_type='tab_updated',
        event_time=2_000_000_000,
        schema_version=2,
        payload_json={
            'browser_session_id': 'browser',
            'sequence': 4,
            'changed_fields': ['url'],
            'tab': {
                'tab_id': 8,
                'window_id': 3,
                'url': 'https://example.com/private?complete=yes#fragment',
                'title': 'Private title',
                'incognito': False,
            },
        },
    )
    structured = _tab_event_row(event)
    assert structured['payload']['tab']['url'].endswith('?complete=yes#fragment')
    flattened = _flatten_tab_event(structured)
    assert flattened['tab_url'] == structured['url']
    assert flattened['tab_title'] == 'Private title'

    _export_response([flattened], ExportRequest(ids=['session'], format='csv', anonymized=True), 'tabs')
    assert 'user_id' not in flattened
    assert flattened['tab_url'].startswith('https://example.com/private')
