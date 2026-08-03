import asyncio
import io
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_plans import ExperimentPlan, ExperimentPlanItem, ExperimentSessionTask
from open_webui.models.experiments import ExperimentSession
from open_webui.models.files import FileForm, Files
from open_webui.models.question_tasks import (
    QuestionCloneForm,
    QuestionTaskForm,
    QuestionTaskQuestion,
    QuestionTasks,
)
from open_webui.storage.provider import Storage
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_FORMATS = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}
EXTENSIONS = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'GIF': '.gif'}


def _task_model_available(request: Request):
    models = request.app.state.MODELS or {}
    return any(
        model_id and model_id in models
        for model_id in (request.app.state.config.TASK_MODEL, request.app.state.config.TASK_MODEL_EXTERNAL)
    )


async def _active_plan_reference(task_id: str, db: AsyncSession):
    return (
        await db.execute(
            select(ExperimentPlanItem.id)
            .join(ExperimentPlan, ExperimentPlan.id == ExperimentPlanItem.plan_id)
            .where(
                ExperimentPlanItem.question_task_id == task_id,
                ExperimentPlan.status == 'PUBLISHED',
            )
            .limit(1)
        )
    ).first()


async def _validate_image_references(form: QuestionTaskForm, db: AsyncSession):
    for file_id in {question.image_file_id for question in form.questions if question.image_file_id}:
        item = await Files.get_file_by_id(file_id, db=db)
        if not item or (item.meta or {}).get('scope') != 'question-task':
            raise HTTPException(status_code=422, detail='A question references an unavailable image.')


@router.get('')
async def list_question_tasks(
    include_archived: bool = False, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    return await QuestionTasks.list_tasks(db=db, include_archived=include_archived)


@router.post('')
async def create_question_task(
    form: QuestionTaskForm, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    await _validate_image_references(form, db)
    return await QuestionTasks.create_task(form, db=db)


@router.get('/{task_id}')
async def get_question_task(task_id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    task = await QuestionTasks.get_task(task_id, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Question Task not found.')
    return task


@router.put('/{task_id}')
async def update_question_task(
    task_id: str, form: QuestionTaskForm, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    await _validate_image_references(form, db)
    task = await QuestionTasks.update_task(task_id, form, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Question Task not found.')
    return task


@router.post('/{task_id}/clone')
async def clone_question_task(
    task_id: str,
    form: QuestionCloneForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await QuestionTasks.clone(task_id, form.mode, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Question Task not found.')
    return task


@router.post('/{task_id}/publish')
async def publish_question_task(
    task_id: str, request: Request, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    task = await QuestionTasks.publish(task_id, _task_model_available(request), db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Question Task not found.')
    return task


@router.delete('/{task_id}')
async def archive_question_task(
    task_id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    if await _active_plan_reference(task_id, db):
        raise HTTPException(
            status_code=409,
            detail='Question Task is used by a published experiment plan and cannot be archived.',
        )
    if not await QuestionTasks.archive(task_id, db=db):
        raise HTTPException(status_code=404, detail='Question Task not found.')
    return {'status': True}


@router.post('/images')
async def upload_question_image(
    file: UploadFile = File(...),
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail='Question images must be 10 MB or smaller.')
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=422, detail='Question images may contain at most 20 megapixels.')
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(status_code=422, detail='The uploaded file is not a valid supported image.')
    if image_format not in IMAGE_FORMATS or file.content_type != IMAGE_FORMATS[image_format]:
        raise HTTPException(status_code=422, detail='Image content and MIME type do not match.')
    original_extension = Path(file.filename or '').suffix.lower()
    allowed_extensions = {EXTENSIONS[image_format]}
    if image_format == 'JPEG':
        allowed_extensions.add('.jpeg')
    if original_extension not in allowed_extensions:
        raise HTTPException(status_code=422, detail='Image filename extension does not match its content.')
    file_id = str(uuid.uuid4())
    filename = f'{file_id}_question{EXTENSIONS[image_format]}'
    contents, file_path = await asyncio.to_thread(
        Storage.upload_file,
        io.BytesIO(data),
        filename,
        {'OpenWebUI-User-Id': user.id, 'OpenWebUI-File-Id': file_id, 'OpenWebUI-File-Scope': 'question-task'},
    )
    item = await Files.insert_new_file(
        user.id,
        FileForm(
            id=file_id,
            filename=file.filename or filename,
            path=file_path,
            data={},
            meta={
                'name': file.filename or filename,
                'content_type': IMAGE_FORMATS[image_format],
                'size': len(contents),
                'scope': 'question-task',
            },
        ),
        db=db,
    )
    if not item:
        await asyncio.to_thread(Storage.delete_file, file_path)
        raise HTTPException(status_code=500, detail='Question image could not be stored.')
    return {'id': item.id, 'content_type': IMAGE_FORMATS[image_format], 'size': len(contents)}


async def _can_read_image(file_id: str, user, db: AsyncSession):
    if user.role == 'admin':
        return True
    row = (
        await db.execute(
            select(QuestionTaskQuestion.id)
            .join(ExperimentSessionTask, ExperimentSessionTask.question_task_id == QuestionTaskQuestion.task_id)
            .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
            .where(QuestionTaskQuestion.image_file_id == file_id, ExperimentSession.user_id == user.id)
            .limit(1)
        )
    ).first()
    return row is not None


@router.get('/images/{file_id}/content')
async def get_question_image(
    file_id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(file_id, db=db)
    if not file or (file.meta or {}).get('scope') != 'question-task' or not await _can_read_image(file_id, user, db):
        raise HTTPException(status_code=404, detail='Question image not found.')
    path = Path(await asyncio.to_thread(Storage.get_file, file.path))
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Question image not found.')
    return FileResponse(
        path, media_type=(file.meta or {}).get('content_type'), headers={'Content-Disposition': 'inline'}
    )


@router.delete('/images/{file_id}')
async def delete_question_image(
    file_id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    file = await Files.get_file_by_id(file_id, db=db)
    if not file or (file.meta or {}).get('scope') != 'question-task':
        raise HTTPException(status_code=404, detail='Question image not found.')
    referenced = (
        await db.execute(select(QuestionTaskQuestion.id).where(QuestionTaskQuestion.image_file_id == file_id).limit(1))
    ).first()
    if referenced:
        raise HTTPException(status_code=409, detail='Remove the image from its question before deleting it.')
    if await Files.delete_file_by_id(file_id, db=db):
        await asyncio.to_thread(Storage.delete_file, file.path)
    return {'status': True}
