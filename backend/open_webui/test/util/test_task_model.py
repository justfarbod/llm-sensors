import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from open_webui.utils.task import task_model_available


def run(coro):
    return asyncio.run(coro)


def make_request(models=None, task_model='grader', external_task_model=''):
    state = SimpleNamespace(
        MODELS=models or {},
        config=SimpleNamespace(
            TASK_MODEL=task_model,
            TASK_MODEL_EXTERNAL=external_task_model,
        ),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_task_model_availability_initializes_empty_cache(monkeypatch):
    request = make_request()
    user = SimpleNamespace(id='admin')

    async def load_models(*args, **kwargs):
        request.app.state.MODELS = {'grader': {'id': 'grader'}}
        return list(request.app.state.MODELS.values())

    loader = AsyncMock(side_effect=load_models)
    monkeypatch.setattr('open_webui.utils.models.get_all_models', loader)

    assert run(task_model_available(request, user)) is True
    loader.assert_awaited_once_with(request, user=user)


def test_task_model_availability_reuses_populated_cache(monkeypatch):
    request = make_request(models={'grader': {'id': 'grader'}})
    loader = AsyncMock()
    monkeypatch.setattr('open_webui.utils.models.get_all_models', loader)

    assert run(task_model_available(request)) is True
    loader.assert_not_awaited()


def test_task_model_availability_requires_a_configured_model(monkeypatch):
    request = make_request(task_model='', external_task_model='')
    loader = AsyncMock()
    monkeypatch.setattr('open_webui.utils.models.get_all_models', loader)

    assert run(task_model_available(request)) is False
    loader.assert_not_awaited()
