import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from open_webui.models.experiment_plans import (
    ExperimentPlan,
    ExperimentPlanForm,
    ExperimentPlans,
    ExperimentSessionTask,
    ProgressionMode,
    SessionTaskStatus,
)
from open_webui.models.experiments import ExperimentSession, ExperimentState, Experiments
from open_webui.models.survey_submissions import (
    SurveyDraftForm,
    SurveyResponse,
    SurveyResponseChoice,
    SurveySubmission,
    SurveySubmissions,
)
from open_webui.models.survey_tasks import (
    SurveyCloneMode,
    SurveyQuestionForm,
    SurveyTaskModel,
    SurveyTask,
    SurveyTasks,
)


def run(coro):
    return asyncio.run(coro)


def scalar_result(rows):
    scalars = MagicMock()
    scalars.all.return_value = rows
    scalars.first.return_value = rows[0] if rows else None
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def survey_item(title='Survey', task_id='survey'):
    return {'task_type': 'SURVEY', 'title': title, 'survey_task_id': task_id}


def essay_item(title='Essay'):
    return {
        'task_type': 'ESSAY',
        'title': title,
        'essay_topic_mode': 'SPECIFIC',
        'essay_topic_id': 'topic',
    }


def survey_task_model():
    return SurveyTaskModel.model_validate(
        {
            'id': 'survey',
            'family_id': 'family',
            'version': 1,
            'latest_version': 1,
            'title': 'Reusable survey',
            'description': '',
            'status': 'PUBLISHED',
            'created_at': 1,
            'updated_at': 1,
            'questions': [
                {
                    'id': 'single',
                    'prompt': 'Choose one',
                    'description': '',
                    'question_type': 'SINGLE_CHOICE',
                    'position': 0,
                    'required': True,
                    'enabled': True,
                    'choices': [
                        {'id': 'a', 'text': 'A', 'position': 0},
                        {'id': 'b', 'text': 'B', 'position': 1},
                    ],
                },
                {
                    'id': 'multi',
                    'prompt': 'Choose several',
                    'description': '',
                    'question_type': 'MULTIPLE_SELECT',
                    'position': 1,
                    'required': False,
                    'enabled': True,
                    'choices': [
                        {'id': 'c', 'text': 'C', 'position': 0},
                        {'id': 'd', 'text': 'D', 'position': 1},
                    ],
                },
                {
                    'id': 'scale',
                    'prompt': 'Rate this',
                    'description': '',
                    'question_type': 'SCALE',
                    'position': 2,
                    'required': True,
                    'enabled': True,
                    'scale_low_label': 'Low',
                    'scale_high_label': 'High',
                },
                {
                    'id': 'short',
                    'prompt': 'Short answer',
                    'description': '',
                    'question_type': 'SHORT_TEXT',
                    'position': 3,
                    'required': False,
                    'enabled': True,
                },
                {
                    'id': 'long',
                    'prompt': 'Long answer',
                    'description': '',
                    'question_type': 'LONG_TEXT',
                    'position': 4,
                    'required': False,
                    'enabled': True,
                },
                {
                    'id': 'disabled',
                    'prompt': 'Hidden question',
                    'description': '',
                    'question_type': 'SHORT_TEXT',
                    'position': 5,
                    'required': True,
                    'enabled': False,
                },
            ],
        }
    )


def session_task(position, task_type, status='LOCKED', *, task_id=None, required=True):
    return ExperimentSessionTask(
        id=task_id or f'task-{position}',
        experiment_session_id='session',
        position=position,
        task_type=task_type,
        title=f'{task_type} {position}',
        status=status,
        survey_task_id='survey' if task_type == 'SURVEY' else None,
        survey_required=required,
        created_at=1,
        updated_at=1,
    )


def test_survey_question_validation_supports_all_types_and_flags():
    payloads = [
        {
            'prompt': 'Single',
            'question_type': 'SINGLE_CHOICE',
            'choices': [{'text': 'A'}, {'text': 'B'}],
        },
        {
            'prompt': 'Multiple',
            'question_type': 'MULTIPLE_SELECT',
            'choices': [{'text': 'A'}, {'text': 'B'}],
            'required': False,
        },
        {
            'prompt': 'Scale',
            'question_type': 'SCALE',
            'scale_low_label': 'Never',
            'scale_high_label': 'Always',
        },
        {'prompt': 'Short', 'question_type': 'SHORT_TEXT'},
        {'prompt': 'Long', 'question_type': 'LONG_TEXT', 'enabled': False},
    ]

    questions = [SurveyQuestionForm.model_validate(payload) for payload in payloads]

    assert [question.question_type.value for question in questions] == [
        'SINGLE_CHOICE',
        'MULTIPLE_SELECT',
        'SCALE',
        'SHORT_TEXT',
        'LONG_TEXT',
    ]
    assert questions[1].required is False
    assert questions[-1].enabled is False


@pytest.mark.parametrize(
    'payload',
    [
        {'prompt': 'Too few', 'question_type': 'SINGLE_CHOICE', 'choices': [{'text': 'A'}]},
        {
            'prompt': 'Duplicate',
            'question_type': 'MULTIPLE_SELECT',
            'choices': [{'text': ' Same '}, {'text': 'same'}],
        },
        {'prompt': 'Unlabelled scale', 'question_type': 'SCALE'},
        {'prompt': 'Text with choices', 'question_type': 'SHORT_TEXT', 'choices': [{'text': 'A'}, {'text': 'B'}]},
    ],
)
def test_invalid_survey_question_configuration_is_rejected(payload):
    with pytest.raises(ValidationError):
        SurveyQuestionForm.model_validate(payload)


@pytest.mark.parametrize('mode', ['FREE_NAVIGATION', 'SEQUENTIAL_REVIEW'])
def test_non_sequential_plans_allow_multiple_prefix_and_suffix_surveys(mode):
    form = ExperimentPlanForm.model_validate(
        {
            'progression_mode': mode,
            'consent_enabled': False,
            'items': [
                survey_item('Prefix 1', 'pre-one'),
                survey_item('Prefix 2', 'pre-two'),
                essay_item(),
                survey_item('Suffix 1', 'post-one'),
                survey_item('Suffix 2', 'post-two'),
            ],
        }
    )
    assert form.consent_enabled is False


@pytest.mark.parametrize('mode', ['FREE_NAVIGATION', 'SEQUENTIAL_REVIEW'])
def test_non_sequential_plans_reject_surveys_between_tasks(mode):
    with pytest.raises(ValidationError):
        ExperimentPlanForm.model_validate(
            {
                'progression_mode': mode,
                'items': [essay_item('One'), survey_item(), essay_item('Two')],
            }
        )


def test_strict_plans_allow_repeated_surveys_anywhere():
    form = ExperimentPlanForm.model_validate(
        {
            'progression_mode': 'STRICT_SEQUENTIAL',
            'items': [
                survey_item('Pre'),
                essay_item('One'),
                survey_item('Middle'),
                essay_item('Two'),
                survey_item('Post'),
            ],
        }
    )
    assert [item.task_type.value for item in form.items].count('SURVEY') == 3


def test_republishing_a_plan_creates_a_new_version_and_leaves_old_items_unchanged(monkeypatch):
    current = ExperimentPlan(
        id='old-plan',
        group_id='group',
        version=2,
        status='PUBLISHED',
        progression_mode='STRICT_SEQUENTIAL',
        chat_mode='SHARED_EXPERIMENT',
        survey_variant='ESSAY',
        consent_enabled=True,
        created_at=1,
        updated_at=1,
    )
    survey = SurveyTask(
        id='survey',
        family_id='survey-family',
        version=1,
        title='Survey',
        description='',
        status='PUBLISHED',
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.add = MagicMock()
    group_result = MagicMock()
    group_result.scalar_one_or_none.return_value = SimpleNamespace(id='group')
    topic_result = scalar_result([])
    question_result = scalar_result([])
    survey_result = scalar_result([survey])
    current_result = scalar_result([current])
    max_result = MagicMock()
    max_result.scalar.return_value = 2
    db.execute.side_effect = [
        group_result,
        topic_result,
        question_result,
        survey_result,
        current_result,
        max_result,
    ]

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    monkeypatch.setattr('open_webui.models.experiment_plans.get_async_db_context', context)
    saved = object()
    monkeypatch.setattr(ExperimentPlans, 'get_plan', AsyncMock(return_value=saved))
    form = ExperimentPlanForm.model_validate({'items': [survey_item()], 'consent_enabled': False})

    assert run(ExperimentPlans.save_for_group('group', form, db=db)) is saved

    added = [call.args[0] for call in db.add.call_args_list]
    new_plan = next(row for row in added if isinstance(row, ExperimentPlan))
    assert current.status == 'SUPERSEDED'
    assert new_plan.id != current.id
    assert new_plan.version == 3
    assert new_plan.consent_enabled is False


def test_participant_survey_omits_disabled_questions(monkeypatch):
    monkeypatch.setattr(SurveyTasks, 'get_task', AsyncMock(return_value=survey_task_model()))

    result = run(SurveyTasks.participant_task('survey'))

    assert [question.id for question in result.questions] == ['single', 'multi', 'scale', 'short', 'long']


def test_survey_version_clone_preserves_family_configuration_and_disabled_questions(monkeypatch):
    source = survey_task_model()
    cloned = object()
    db = AsyncMock()
    db.add = MagicMock()
    max_result = MagicMock()
    max_result.scalar.return_value = 1
    db.execute.return_value = max_result

    @asynccontextmanager
    async def context(passed=None):
        yield passed or db

    monkeypatch.setattr('open_webui.models.survey_tasks.get_async_db_context', context)
    monkeypatch.setattr(SurveyTasks, 'get_task', AsyncMock(side_effect=[source, cloned]))
    replace = AsyncMock()
    monkeypatch.setattr(SurveyTasks, '_replace_questions', replace)

    assert run(SurveyTasks.clone(source.id, SurveyCloneMode.VERSION, db=db)) is cloned

    row = db.add.call_args.args[0]
    assert row.family_id == source.family_id
    assert row.version == 2
    assert row.status == 'DRAFT'
    forms = replace.await_args.args[1]
    assert [question.enabled for question in forms][-1] is False
    assert forms[2].scale_low_label == 'Low'
    assert [(choice.text) for choice in forms[1].choices] == ['C', 'D']


def test_required_survey_cannot_be_skipped():
    with pytest.raises(HTTPException) as exc:
        run(SurveySubmissions.skip(session_task(0, 'SURVEY', status='ACTIVE'), 'user', AsyncMock()))
    assert exc.value.status_code == 409


def test_survey_answer_payload_rejects_duplicates_and_out_of_range_scale():
    with pytest.raises(ValidationError):
        SurveyDraftForm.model_validate({'answers': [{'question_id': 'single'}, {'question_id': 'single'}]})
    with pytest.raises(ValidationError):
        SurveyDraftForm.model_validate({'answers': [{'question_id': 'scale', 'scale': 6}]})
    with pytest.raises(ValidationError):
        SurveyDraftForm.model_validate({'answers': [{'question_id': 'multi', 'choice_ids': ['a', 'a']}]})


def test_survey_draft_normalizes_every_answer_type_and_ignores_disabled_question(monkeypatch):
    task = session_task(0, 'SURVEY', status='ACTIVE')
    task.survey_required = False
    submission = SurveySubmission(
        id='submission',
        session_task_id=task.id,
        user_id='user',
        status='DRAFT',
        created_at=1,
        updated_at=1,
    )
    responses = [
        SurveyResponse(
            id=f'response-{question_id}',
            submission_id=submission.id,
            question_id=question_id,
            is_answered=False,
            created_at=1,
            updated_at=1,
        )
        for question_id in ['single', 'multi', 'scale', 'short', 'long']
    ]
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [scalar_result([submission]), scalar_result(responses)] + [MagicMock()] * 5
    monkeypatch.setattr(SurveyTasks, 'get_task', AsyncMock(return_value=survey_task_model()))
    monkeypatch.setattr(
        SurveySubmissions,
        'participant_model',
        AsyncMock(return_value=SimpleNamespace(id=submission.id)),
    )
    form = SurveyDraftForm.model_validate(
        {
            'answers': [
                {'question_id': 'single', 'choice_ids': ['a']},
                {'question_id': 'multi', 'choice_ids': ['c', 'd']},
                {'question_id': 'scale', 'scale': 4},
                {'question_id': 'short', 'text': '  concise  '},
                {'question_id': 'long', 'text': '  detailed response  '},
            ]
        }
    )

    run(SurveySubmissions.save_draft(task, 'user', form, db))

    row_map = {row.question_id: row for row in responses}
    assert row_map['scale'].scale_answer == 4
    assert row_map['short'].text_answer == 'concise'
    assert row_map['long'].text_answer == 'detailed response'
    assert all(row.is_answered for row in responses)
    selected = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], SurveyResponseChoice)]
    assert {choice.choice_id for choice in selected} == {'a', 'c', 'd'}
    assert 'disabled' not in row_map


def test_strict_progression_uses_one_transition_path_for_surveys_and_tasks(monkeypatch):
    rows = [
        session_task(0, 'SURVEY'),
        session_task(1, 'ESSAY'),
        session_task(2, 'SURVEY'),
    ]
    plan = SimpleNamespace(id='plan', status='PUBLISHED', progression_mode=ProgressionMode.STRICT_SEQUENTIAL)
    session = ExperimentSession(
        id='session',
        user_id='user',
        group_id='group',
        plan_id='plan',
        task_type='SURVEY',
        state=ExperimentState.IN_PROGRESS.value,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.execute.return_value = scalar_result(rows)
    monkeypatch.setattr('open_webui.models.experiments.ExperimentPlans.get_plan', AsyncMock(return_value=plan))

    run(Experiments._activate_initial(plan, rows, 2))
    assert [row.status for row in rows] == ['ACTIVE', 'LOCKED', 'LOCKED']

    rows[0].status = SessionTaskStatus.FINALIZED.value
    run(Experiments.advance_after_stage(session, rows[0], db))
    assert rows[1].status == SessionTaskStatus.ACTIVE.value

    rows[1].status = SessionTaskStatus.FINALIZED.value
    run(Experiments.advance_after_stage(session, rows[1], db))
    assert rows[2].status == SessionTaskStatus.ACTIVE.value

    rows[2].status = SessionTaskStatus.FINALIZED.value
    run(Experiments.advance_after_stage(session, rows[2], db))
    assert session.state == ExperimentState.THANK_YOU_REQUIRED.value


def test_free_navigation_prefix_surveys_then_task_block_then_suffix(monkeypatch):
    rows = [
        session_task(0, 'SURVEY'),
        session_task(1, 'SURVEY'),
        session_task(2, 'ESSAY'),
        session_task(3, 'QUESTION'),
        session_task(4, 'SURVEY'),
    ]
    plan = SimpleNamespace(id='plan', status='PUBLISHED', progression_mode=ProgressionMode.FREE_NAVIGATION)
    session = ExperimentSession(
        id='session',
        user_id='user',
        group_id='group',
        plan_id='plan',
        task_type='SURVEY',
        state=ExperimentState.IN_PROGRESS.value,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.execute.return_value = scalar_result(rows)
    monkeypatch.setattr('open_webui.models.experiments.ExperimentPlans.get_plan', AsyncMock(return_value=plan))

    run(Experiments._activate_initial(plan, rows, 2))
    rows[0].status = SessionTaskStatus.FINALIZED.value
    run(Experiments.advance_after_stage(session, rows[0], db))
    assert rows[1].status == SessionTaskStatus.ACTIVE.value

    rows[1].status = SessionTaskStatus.SKIPPED.value
    run(Experiments.advance_after_stage(session, rows[1], db))
    assert rows[2].status == rows[3].status == SessionTaskStatus.AVAILABLE.value
    assert rows[4].status == SessionTaskStatus.LOCKED.value

    rows[2].status = rows[3].status = SessionTaskStatus.FINALIZED.value
    run(Experiments.advance_after_stage(session, rows[3], db, task_block_finalized=True))
    assert rows[4].status == SessionTaskStatus.ACTIVE.value
