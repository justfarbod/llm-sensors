import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from open_webui.models.experiment_telemetry import ExperimentTelemetryEvent
from open_webui.models.experiments import ExperimentState, Experiments
from open_webui.routers.experiment_telemetry import (
    TelemetryBatchForm,
    router,
    telemetry_events,
    telemetry_status,
)
from open_webui.utils.auth import get_verified_user


def run(coro):
    return asyncio.run(coro)


def event(event_type='keystroke', **values):
    base = {
        'event_id': str(uuid4()),
        'type': event_type,
        'timestamp': '2026-06-15T12:00:01.000Z',
        'field': 'essay',
    }
    if event_type == 'keystroke':
        base.update(
            {
                'key_class': 'printable',
                'inter_key_interval_ms': 2500,
                'hold_duration_ms': 80,
                'modifiers': {'ctrl': False, 'shift': False, 'alt': False, 'meta': False},
            }
        )
    elif event_type == 'paste':
        base.update({'text_length': 42, 'line_count': 2})
    elif event_type == 'focus_return':
        base.update({'field': 'unknown', 'away_duration_ms': 95000})
    elif event_type == 'focus_away':
        base.update({'field': 'unknown'})
    base.update(values)
    return base


def result(*, first=None, all_rows=None):
    scalars = MagicMock()
    scalars.first.return_value = first
    scalars.all.return_value = all_rows or []
    response = MagicMock()
    response.scalars.return_value = scalars
    return response


def test_routes_require_authenticated_verified_user():
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {route.path for route in routes} == {'/status', '/events'}
    for route in routes:
        assert any(dependency.call is get_verified_user for dependency in route.dependant.dependencies)


def test_status_is_disabled_for_admin_normal_and_inactive_users(monkeypatch):
    admin = run(telemetry_status(SimpleNamespace(id='admin', role='admin'), AsyncMock()))
    assert admin.enabled is False

    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.NOT_APPLICABLE, None, None)),
    )
    normal = run(telemetry_status(SimpleNamespace(id='user', role='user'), AsyncMock()))
    assert normal.enabled is False


def test_status_is_enabled_only_for_in_progress_owner(monkeypatch):
    session = SimpleNamespace(id='session')
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.IN_PROGRESS, session, None)),
    )
    response = run(telemetry_status(SimpleNamespace(id='user', role='user'), AsyncMock()))
    assert response.enabled is True
    assert response.experiment_session_id == 'session'
    assert response.allowed_contexts == ['essay', 'chat']


def test_schema_rejects_raw_text_unknown_fields_and_batch_limits():
    with pytest.raises(ValidationError):
        TelemetryBatchForm.model_validate({'experiment_session_id': 'session', 'events': [event(text='secret')]})
    with pytest.raises(ValidationError):
        TelemetryBatchForm.model_validate({'experiment_session_id': 'session', 'events': [event(unexpected=True)]})
    with pytest.raises(ValidationError):
        TelemetryBatchForm.model_validate({'experiment_session_id': 'session', 'events': [event() for _ in range(101)]})
    with pytest.raises(ValidationError):
        TelemetryBatchForm.model_validate({'experiment_session_id': 'x' * 300_000, 'events': [event()]})
    with pytest.raises(ValidationError):
        TelemetryBatchForm.model_validate(
            {
                'experiment_session_id': 'session',
                'events': [event(timestamp='2026-06-15T12:00:01')],
            }
        )


def test_valid_batch_is_idempotent_and_updates_summary():
    async def scenario():
        form = TelemetryBatchForm.model_validate(
            {
                'experiment_session_id': 'session',
                'events': [event(), event('paste'), event('focus_away'), event('focus_return')],
            }
        )
        session = SimpleNamespace(id='session', user_id='user', state=ExperimentState.IN_PROGRESS.value)
        user = SimpleNamespace(id='user', role='user')
        first_db = AsyncMock()
        first_db.add = MagicMock()
        first_db.execute.side_effect = [result(first=session), result(first=None), result(all_rows=[])]
        first = await telemetry_events(form, user, first_db)
        summary = next(
            call.args[0]
            for call in first_db.add.call_args_list
            if not isinstance(call.args[0], ExperimentTelemetryEvent)
        )
        stored_events = [
            call.args[0] for call in first_db.add.call_args_list if isinstance(call.args[0], ExperimentTelemetryEvent)
        ]

        second_db = AsyncMock()
        second_db.add = MagicMock()
        second_db.execute.side_effect = [
            result(first=session),
            result(first=summary),
            result(all_rows=[str(item.event_id) for item in form.events]),
        ]
        second = await telemetry_events(form, user, second_db)
        return first, second, stored_events, summary, second_db

    first, second, rows, summary, second_db = run(scenario())
    assert first == {'accepted': 4, 'duplicates': 0}
    assert second == {'accepted': 0, 'duplicates': 4}
    assert len(rows) == 4
    assert all(
        not {'text', 'content', 'raw', 'key', 'clipboard', 'clipboard_text', 'password'} & row.payload_json.keys()
        for row in rows
    )
    assert summary.total_keystrokes == 1
    assert summary.avg_inter_key_interval_ms == 2500
    assert summary.avg_key_hold_duration_ms == 80
    assert summary.pause_count == 1
    assert summary.longest_pause_ms == 2500
    assert summary.paste_count == 1
    assert summary.total_pasted_chars == 42
    assert summary.tab_switch_count == 1
    assert summary.total_time_away_ms == 95000
    second_db.add.assert_not_called()


def test_events_reject_admin_wrong_owner_and_inactive_session():
    form = TelemetryBatchForm.model_validate({'experiment_session_id': 'session', 'events': [event()]})

    async def scenario():
        db = AsyncMock()
        with pytest.raises(HTTPException) as admin:
            await telemetry_events(form, SimpleNamespace(id='admin', role='admin'), db)

        db.execute.return_value = result(first=None)
        with pytest.raises(HTTPException) as owner:
            await telemetry_events(form, SimpleNamespace(id='other', role='user'), db)

        inactive_session = SimpleNamespace(
            id='session', user_id='user', state=ExperimentState.POST_SURVEY_REQUIRED.value
        )
        db.execute.return_value = result(first=inactive_session)
        with pytest.raises(HTTPException) as inactive:
            await telemetry_events(form, SimpleNamespace(id='user', role='user'), db)
        return admin.value.status_code, owner.value.status_code, inactive.value.status_code

    assert run(scenario()) == (403, 403, 409)
