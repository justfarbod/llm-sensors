from fastapi.routing import APIRoute

from open_webui.routers.experiments import router as experiment_router
from open_webui.routers.survey_tasks import router as survey_router
from open_webui.utils.auth import get_admin_user


def test_survey_task_library_routes_are_admin_only():
    routes = [route for route in survey_router.routes if isinstance(route, APIRoute)]
    assert {
        '',
        '/{task_id}',
        '/{task_id}/clone',
        '/{task_id}/preview',
        '/{task_id}/publish',
        '/{task_id}/results',
    } == {route.path for route in routes}
    assert all(
        any(dependency.call is get_admin_user for dependency in route.dependant.dependencies) for route in routes
    )


def test_participant_survey_routes_are_scoped_to_session_task_identity():
    routes = [route for route in experiment_router.routes if isinstance(route, APIRoute)]
    paths = {route.path for route in routes}
    assert '/current/tasks/{task_id}' in paths
    assert '/current/tasks/{task_id}/survey' in paths
    assert '/current/tasks/{task_id}/skip-survey' in paths
