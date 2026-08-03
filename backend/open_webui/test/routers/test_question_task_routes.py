"""Question Task route, image, and participant-access tests."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from PIL import Image

from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.experiments import ExperimentSession, ExperimentState
from open_webui.models.question_submissions import QuestionSubmission, QuestionSubmissions
from open_webui.routers.experiments import (
    _repair_finalized_question_submissions,
    _session_task_for_user,
)
from open_webui.routers.question_tasks import (
    _can_read_image,
    get_question_image,
    router,
    upload_question_image,
)
from open_webui.utils.auth import get_admin_user


def run(coro):
    return asyncio.run(coro)


def row_result(first=None):
    result = MagicMock()
    result.first.return_value = first
    return result


def scalar_rows(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


class InMemoryUpload:
    def __init__(self, data: bytes, filename: str, content_type: str):
        self.data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


def upload(data: bytes, filename: str, content_type: str):
    return InMemoryUpload(data, filename, content_type)


def png_bytes():
    output = io.BytesIO()
    Image.new('RGB', (20, 10), color='blue').save(output, format='PNG')
    return output.getvalue()


def test_question_task_management_routes_are_admin_only():
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert {
        '',
        '/{task_id}',
        '/{task_id}/clone',
        '/{task_id}/publish',
        '/images',
        '/images/{file_id}/content',
        '/images/{file_id}',
    } == {route.path for route in routes}
    for route in routes:
        if route.path == '/images/{file_id}/content':
            continue
        assert any(dependency.call is get_admin_user for dependency in route.dependant.dependencies)


@pytest.mark.parametrize(
    'file',
    [
        upload(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'image.svg', 'image/svg+xml'),
        upload(png_bytes(), 'image.png', 'image/jpeg'),
        upload(png_bytes(), 'image.jpg', 'image/png'),
    ],
)
def test_question_image_validation_rejects_unsupported_or_mismatched_files(file):
    with pytest.raises(HTTPException) as exc:
        run(
            upload_question_image(
                file=file,
                user=SimpleNamespace(id='admin', role='admin'),
                db=AsyncMock(),
            )
        )
    assert exc.value.status_code == 422


def test_question_image_upload_uses_scoped_storage(monkeypatch):
    stored = SimpleNamespace(id='file')
    storage = MagicMock(return_value=(png_bytes(), '/tmp/file_question.png'))
    insert = AsyncMock(return_value=stored)

    async def immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr('open_webui.routers.question_tasks.asyncio.to_thread', immediate)
    monkeypatch.setattr('open_webui.routers.question_tasks.Storage.upload_file', storage)
    monkeypatch.setattr('open_webui.routers.question_tasks.Files.insert_new_file', insert)

    result = run(
        upload_question_image(
            file=upload(png_bytes(), 'image.png', 'image/png'),
            user=SimpleNamespace(id='admin', role='admin'),
            db=AsyncMock(),
        )
    )

    assert result == {'id': 'file', 'content_type': 'image/png', 'size': len(png_bytes())}
    form = insert.await_args.args[1]
    assert form.meta['scope'] == 'question-task'
    assert form.meta['content_type'] == 'image/png'


def test_question_image_authorization_is_admin_or_assigned_participant_only(monkeypatch):
    admin_db = AsyncMock()
    assert run(_can_read_image('file', SimpleNamespace(role='admin'), admin_db)) is True
    admin_db.execute.assert_not_awaited()

    participant_db = AsyncMock()
    participant_db.execute.return_value = row_result(first=('question',))
    assert run(_can_read_image('file', SimpleNamespace(id='participant', role='user'), participant_db)) is True

    denied_db = AsyncMock()
    denied_db.execute.return_value = row_result(first=None)
    assert run(_can_read_image('file', SimpleNamespace(id='other', role='user'), denied_db)) is False

    monkeypatch.setattr(
        'open_webui.routers.question_tasks.Files.get_file_by_id',
        AsyncMock(return_value=SimpleNamespace(id='file', path='path', meta={'scope': 'question-task'})),
    )
    with pytest.raises(HTTPException) as exc:
        run(get_question_image('file', SimpleNamespace(id='other', role='user'), denied_db))
    assert exc.value.status_code == 404


def test_participant_task_access_rejects_other_users_locked_tasks_and_completed_sessions():
    user = SimpleNamespace(id='participant', role='user')

    missing_db = AsyncMock()
    missing_db.execute.return_value = row_result(first=None)
    with pytest.raises(HTTPException) as missing:
        run(_session_task_for_user('task', user, missing_db))
    assert missing.value.status_code == 404

    task = ExperimentSessionTask(
        id='task',
        experiment_session_id='session',
        position=0,
        task_type='QUESTION',
        title='Questions',
        status='LOCKED',
        question_task_id='question-task',
        created_at=1,
        updated_at=1,
    )
    session = ExperimentSession(
        id='session',
        user_id='participant',
        group_id='group',
        task_type='QUESTION',
        state=ExperimentState.IN_PROGRESS.value,
        created_at=1,
        updated_at=1,
    )
    locked_db = AsyncMock()
    locked_db.execute.return_value = row_result(first=(task, session))
    with pytest.raises(HTTPException) as locked:
        run(_session_task_for_user('task', user, locked_db))
    assert locked.value.status_code == 409

    task.status = 'AVAILABLE'
    session.state = ExperimentState.COMPLETED.value
    completed_db = AsyncMock()
    completed_db.execute.return_value = row_result(first=(task, session))
    with pytest.raises(HTTPException) as completed:
        run(_session_task_for_user('task', user, completed_db))
    assert completed.value.status_code == 409


def test_current_state_repairs_finalized_task_with_draft_submission_and_queues_grading(monkeypatch):
    submission = QuestionSubmission(
        id='submission',
        session_task_id='task',
        user_id='participant',
        status='DRAFT',
        grading_status='NOT_STARTED',
        maximum_score=5,
        created_at=1,
        updated_at=1,
    )
    db = AsyncMock()
    db.execute.return_value = scalar_rows([submission])
    finalize = AsyncMock(return_value=(['response'], SimpleNamespace(status='FINALIZED')))
    monkeypatch.setattr(QuestionSubmissions, 'finalize', finalize)
    schedule = AsyncMock()
    monkeypatch.setattr('open_webui.routers.experiments._schedule_grading', schedule)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    user = SimpleNamespace(id='participant', role='user')

    run(_repair_finalized_question_submissions(request, user, db))

    finalize.assert_awaited_once_with(
        submission.id,
        user.id,
        db=db,
        commit=False,
    )
    db.commit.assert_awaited_once()
    schedule.assert_awaited_once_with(request, user, ['response'], submission.id)
