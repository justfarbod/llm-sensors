import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.routers import auths as auths_router
from open_webui.utils import auth as auth_utils


def run(coro):
    return asyncio.run(coro)


def request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(JWT_EXPIRES_IN='1h', USER_PERMISSIONS={}))),
        headers={},
        cookies={},
        state=SimpleNamespace(),
    )


def user(role='user'):
    return SimpleNamespace(id='user-1', email='user@test', name='User', role=role)


@pytest.fixture
def membership(monkeypatch):
    has_group = AsyncMock(return_value=False)
    monkeypatch.setattr(Groups, 'has_any_group', has_group)
    monkeypatch.setattr(auths_router, 'get_permissions', AsyncMock(return_value={}))
    return has_group


def test_no_session_is_issued_to_a_user_without_a_group(membership):
    response = MagicMock()
    with pytest.raises(HTTPException) as error:
        run(auths_router.create_session_response(request(), user(), None, response, set_cookie=True))
    assert error.value.status_code == 403
    assert error.value.detail == ERROR_MESSAGES.NO_GROUP
    response.set_cookie.assert_not_called()


def test_group_members_and_admins_receive_a_session(membership):
    admin = run(auths_router.create_session_response(request(), user('admin'), None))
    assert admin['token']
    membership.assert_not_awaited()

    membership.return_value = True
    member = run(auths_router.create_session_response(request(), user(), None))
    assert member['token'] and member['role'] == 'user'


def test_existing_session_is_rejected_once_the_user_has_no_group(monkeypatch, membership):
    monkeypatch.setattr(Users, 'get_user_by_id', AsyncMock(return_value=user()))
    monkeypatch.setattr(Users, 'update_last_active_by_id', AsyncMock())
    monkeypatch.setattr(auth_utils, 'is_valid_token', AsyncMock(return_value=True))
    token = auth_utils.create_token(data={'id': 'user-1'})
    response = MagicMock()
    req = request()
    req.cookies = {'token': token}

    async def current_user():
        return await auth_utils.get_current_user(
            req, response, MagicMock(), SimpleNamespace(credentials=token)
        )

    with pytest.raises(HTTPException) as error:
        run(current_user())
    assert error.value.status_code == 403
    response.delete_cookie.assert_called_with('token')

    membership.return_value = True
    assert run(current_user()).id == 'user-1'
