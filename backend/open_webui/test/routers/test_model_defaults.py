import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups
from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models
from open_webui.routers import models as models_router
from open_webui.utils import access_control
from open_webui.utils import models as model_utils

PUBLIC = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]


def run(coro):
    return asyncio.run(coro)


def request(default_models='', order=None):
    config = SimpleNamespace(DEFAULT_MODELS=default_models, MODEL_ORDER_LIST=order or [])
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config, MODELS={})))


def model(model_id, **extra):
    return {'id': model_id, 'name': model_id, **extra}


ACTIVE = [
    model('arena-model', arena=True),
    model('hidden', info={'meta': {'hidden': True}}),
    model('zeta'),
    model('alpha'),
]


def test_empty_default_is_filled_with_the_first_active_visible_model():
    req = request()
    model_utils.ensure_default_model(req, ACTIVE)
    assert req.app.state.config.DEFAULT_MODELS == 'alpha'

    ordered = request(order=['zeta', 'alpha'])
    model_utils.ensure_default_model(ordered, ACTIVE)
    assert ordered.app.state.config.DEFAULT_MODELS == 'zeta'


def test_configured_default_is_kept_and_unavailable_defaults_fall_back_without_overwriting():
    req = request('zeta,removed')
    model_utils.ensure_default_model(req, ACTIVE)
    assert req.app.state.config.DEFAULT_MODELS == 'zeta,removed'
    assert model_utils.get_effective_default_models(req, ACTIVE) == 'zeta'

    offline = request('removed')
    model_utils.ensure_default_model(offline, ACTIVE)
    assert offline.app.state.config.DEFAULT_MODELS == 'removed'
    assert model_utils.get_effective_default_models(offline, ACTIVE) == 'alpha'
    assert model_utils.get_effective_default_models(request('hidden'), ACTIVE) == 'alpha'
    assert model_utils.get_effective_default_models(request(), []) == ''


def test_models_without_a_record_are_public_to_users(monkeypatch):
    user = SimpleNamespace(id='user-1', role='user')
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value=set()))
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))

    private = model('private', info={'id': 'private', 'user_id': 'admin'})
    filtered = run(model_utils.get_filtered_models([model('unregistered'), private], user))
    assert [m['id'] for m in filtered] == ['unregistered']

    run(model_utils.check_model_access(user, model('unregistered')))
    run(access_control.check_model_access(user, None))


def test_private_record_still_denies_users(monkeypatch):
    user = SimpleNamespace(id='user-1', role='user')
    record = SimpleNamespace(id='private', user_id='admin', base_model_id=None)
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(AccessGrants, 'has_access', AsyncMock(return_value=False))
    with pytest.raises(HTTPException):
        run(access_control.check_model_access(user, record))


def test_new_model_defaults_to_public_unless_grants_are_given(monkeypatch):
    insert = AsyncMock(return_value=SimpleNamespace(id='m'))
    monkeypatch.setattr(Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(Models, 'insert_new_model', insert)
    admin = SimpleNamespace(id='admin', role='admin')
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(USER_PERMISSIONS={}))))

    def form(**extra):
        return ModelForm(id='m', name='M', meta=ModelMeta(), params=ModelParams(), **extra)

    run(models_router.create_new_model(req, form(), admin, db=None))
    assert insert.await_args.args[0].access_grants == PUBLIC

    run(models_router.create_new_model(req, form(access_grants=[]), admin, db=None))
    assert insert.await_args.args[0].access_grants == []
