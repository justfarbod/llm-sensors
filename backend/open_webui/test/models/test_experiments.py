import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from open_webui.models.experiments import (
    ExperimentSession,
    ExperimentSessionModel,
    ExperimentState,
    Experiments,
    PostSurveyForm,
    PreSurveyForm,
    require_experiment_chat_access,
)
from open_webui.routers.experiments import response_for
from open_webui.utils.experiments import require_chat_access_dependency, require_non_experiment_user_dependency

@asynccontextmanager
async def fake_db_context(db=None):
    yield db or AsyncMock()


def run(coro):
    return asyncio.run(coro)


def test_admin_is_never_eligible():
    state, session, error = run(Experiments.get_current(SimpleNamespace(id='admin', role='admin')))
    assert state == ExperimentState.NOT_APPLICABLE
    assert session is None
    assert error is None


def test_admin_state_check_does_not_query_groups_or_create_session(monkeypatch):
    groups = AsyncMock()
    monkeypatch.setattr('open_webui.models.experiments.Groups.get_groups_by_member_id', groups)

    state, session, _ = run(Experiments.get_current(SimpleNamespace(id='admin', role='admin'), db=AsyncMock()))
    assert state == ExperimentState.NOT_APPLICABLE
    assert session is None
    groups.assert_not_awaited()


def test_required_survey_validation():
    with pytest.raises(ValidationError):
        PreSurveyForm(
            school_class='',
            ai_familiarity=0,
            ai_schoolwork_frequency='Never',
            essay_writing_confidence=5,
            age_range='Prefer not to say',
        )
    with pytest.raises(ValidationError):
        PostSurveyForm(
            ai_helpfulness=6,
            essay_satisfaction=5,
            ai_improvement='A lot',
            chat_ease=5,
        )


def test_chat_access_allows_only_not_applicable_or_in_progress(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    monkeypatch.setattr(Experiments, 'get_current', AsyncMock(return_value=(ExperimentState.IN_PROGRESS, None, None)))
    run(require_experiment_chat_access(user))

    Experiments.get_current = AsyncMock(return_value=(ExperimentState.CONSENT_REQUIRED, None, None))
    with pytest.raises(HTTPException) as exc:
        run(require_experiment_chat_access(user))
    assert exc.value.status_code == 403


def test_chat_access_rejects_admin_even_when_experiment_is_not_applicable():
    with pytest.raises(HTTPException) as exc:
        run(require_experiment_chat_access(SimpleNamespace(id='admin', role='admin')))
    assert exc.value.status_code == 403
    assert 'Admin Panel' in exc.value.detail


def test_admin_chat_router_policy_preserves_only_admin_database_export():
    admin = SimpleNamespace(id='admin', role='admin')
    blocked = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/chats', 'headers': []})
    allowed = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/chats/all/db', 'headers': []})

    with pytest.raises(HTTPException) as exc:
        run(require_chat_access_dependency(blocked, admin, AsyncMock()))
    assert exc.value.status_code == 403
    assert run(require_chat_access_dependency(allowed, admin, AsyncMock())) == ExperimentState.NOT_APPLICABLE


def test_experiment_user_cannot_access_archived_chats(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/chats/archived', 'headers': []})
    monkeypatch.setattr(
        'open_webui.utils.experiments.require_experiment_chat_access',
        AsyncMock(return_value=ExperimentState.IN_PROGRESS),
    )

    with pytest.raises(HTTPException) as exc:
        run(require_chat_access_dependency(request, user, AsyncMock()))
    assert exc.value.status_code == 403


def test_experiment_and_admin_users_cannot_access_personal_settings(monkeypatch):
    admin = SimpleNamespace(id='admin', role='admin')
    with pytest.raises(HTTPException):
        run(require_non_experiment_user_dependency(admin, AsyncMock()))

    participant = SimpleNamespace(id='user', role='user')
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.IN_PROGRESS, None, None)),
    )
    with pytest.raises(HTTPException):
        run(require_non_experiment_user_dependency(participant, AsyncMock()))


def test_pre_survey_records_submission_timestamp(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    transition = AsyncMock(return_value=SimpleNamespace(id='session'))
    monkeypatch.setattr(Experiments, '_transition', transition)
    form = PreSurveyForm(
        school_class=' Grade 10 ',
        ai_familiarity=3,
        ai_schoolwork_frequency='Sometimes',
        essay_writing_confidence=4,
        age_range='15–16',
    )

    run(Experiments.submit_pre_survey(user, form, AsyncMock()))

    values = transition.await_args.args[3]
    assert values['pre_survey']['school_class'] == 'Grade 10'
    assert isinstance(values['pre_survey_submitted_at'], int)


def test_out_of_order_and_duplicate_transitions_are_rejected(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.PRE_SURVEY_REQUIRED, None, None)),
    )
    with pytest.raises(HTTPException) as exc:
        run(Experiments.consent(user, AsyncMock()))
    assert exc.value.status_code == 409


def test_multiple_enabled_groups_are_a_configuration_error(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    groups = [
        SimpleNamespace(data={'config': {'experiment_mode_enabled': True}}),
        SimpleNamespace(data={'config': {'experiment_mode_enabled': True}}),
    ]
    monkeypatch.setattr('open_webui.models.experiments.get_async_db_context', fake_db_context)
    monkeypatch.setattr('open_webui.models.experiments.Groups.get_groups_by_member_id', AsyncMock(return_value=groups))

    state, session, error = run(Experiments.get_current(user, db=AsyncMock()))
    assert state == ExperimentState.CONFIGURATION_ERROR
    assert session is None
    assert 'multiple' in error.lower()


def test_repeated_non_experiment_state_checks_do_not_create_sessions(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    db = AsyncMock()
    monkeypatch.setattr('open_webui.models.experiments.get_async_db_context', fake_db_context)
    monkeypatch.setattr('open_webui.models.experiments.Groups.get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(Experiments, '_sessions_for_user', AsyncMock(return_value=[]))

    first = run(Experiments.get_current(user, db=db))
    second = run(Experiments.get_current(user, db=db))

    assert first[0] == second[0] == ExperimentState.NOT_APPLICABLE
    assert first[1] is second[1] is None
    db.add.assert_not_called()


def test_repeated_active_state_checks_do_not_create_duplicate_sessions(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    db = AsyncMock()
    active = ExperimentSession(
        id='session',
        user_id='user',
        group_id='group',
        topic_id='topic',
        topic_title='Title',
        topic_question='Question',
        state=ExperimentState.CONSENT_REQUIRED.value,
        created_at=1,
        updated_at=1,
    )
    monkeypatch.setattr('open_webui.models.experiments.get_async_db_context', fake_db_context)
    monkeypatch.setattr('open_webui.models.experiments.Groups.get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(Experiments, '_sessions_for_user', AsyncMock(return_value=[active]))

    first = run(Experiments.get_current(user, db=db))
    second = run(Experiments.get_current(user, db=db))

    assert first[1].id == second[1].id == 'session'
    db.add.assert_not_called()


def test_not_applicable_response_is_small_and_has_no_nested_collections(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.NOT_APPLICABLE, None, None)),
    )

    response = run(response_for(user, AsyncMock()))
    payload = response.model_dump()
    assert len(response.model_dump_json()) < 500
    assert set(payload) == {'state', 'session_id', 'group_id', 'topic', 'agreement_text', 'error'}
    assert not {'user', 'group', 'chats', 'essays', 'surveys'} & set(payload)


def test_duplicate_experiment_essay_submission_is_rejected(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    session = ExperimentSessionModel(
        id='session',
        user_id='user',
        group_id='group',
        topic_id='topic',
        topic_title='Title',
        topic_question='Question',
        state=ExperimentState.IN_PROGRESS,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.execute.return_value.rowcount = 0
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.IN_PROGRESS, session, None)),
    )

    with pytest.raises(HTTPException) as exc:
        run(Experiments.submit_essay(user, 'Essay', db))
    assert exc.value.status_code == 409
    db.rollback.assert_awaited_once()


def test_experiment_essay_stores_word_and_character_counts(monkeypatch):
    user = SimpleNamespace(id='user', role='user')
    session = ExperimentSessionModel(
        id='session',
        user_id='user',
        group_id='group',
        topic_id='topic',
        topic_title='Title',
        topic_question='Question',
        state=ExperimentState.IN_PROGRESS,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.execute.return_value.rowcount = 1
    db.refresh.side_effect = lambda essay: setattr(essay, 'id', essay.id)
    monkeypatch.setattr(
        Experiments,
        'get_current',
        AsyncMock(return_value=(ExperimentState.IN_PROGRESS, session, None)),
    )

    essay = run(Experiments.submit_essay(user, 'One two three.', db))

    assert essay.word_count == 3
    assert essay.character_count == len('One two three.')
