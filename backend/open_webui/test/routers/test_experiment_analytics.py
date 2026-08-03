from types import SimpleNamespace

from fastapi.routing import APIRoute

from open_webui.routers import chats, tasks, users
from open_webui.routers.experiment_analytics import (
    ExportRequest,
    _anonymous_id,
    _duration,
    _export_response,
    _histogram,
    _paginate_rows,
    _session_row,
    router,
)
from open_webui.utils.auth import get_admin_user
from open_webui.utils.experiments import require_chat_access_dependency, require_non_experiment_user_dependency


def test_dashboard_routes_are_admin_only_and_match_contract():
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {route.path for route in routes} == {
        '/filters',
        '/overview',
        '/participants',
        '/sessions/{session_id}',
        '/essays',
        '/usage',
        '/essay-stats',
        '/surveys',
        '/export/participants',
        '/export/essays',
        '/export/perturbations',
        '/export/surveys',
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
