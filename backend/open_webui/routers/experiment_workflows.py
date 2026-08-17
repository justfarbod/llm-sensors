import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_workflows import (
    MAX_ARCHIVE_BYTES,
    ExperimentWorkflows,
    WorkflowApplyForm,
    WorkflowWriteForm,
    default_workflow_definition,
)
from open_webui.utils.auth import get_admin_user
from open_webui.utils.task import task_model_available


router = APIRouter()


@router.get('')
async def list_workflows(
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await ExperimentWorkflows.list(db, await task_model_available(request, user))


@router.post('')
async def create_workflow(
    form: WorkflowWriteForm,
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not form.definition:
        form.definition = default_workflow_definition()
    workflow = await ExperimentWorkflows.create(form, user.id, db)
    return await ExperimentWorkflows.get(
        workflow.id, db, await task_model_available(request, user)
    )


@router.post('/import')
async def import_workflow(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    data = await file.read(MAX_ARCHIVE_BYTES + 1)
    model_available = await task_model_available(request, user)
    workflow = await ExperimentWorkflows.import_archive(
        data,
        user.id,
        model_available,
        db,
    )
    return await ExperimentWorkflows.get(workflow.id, db, model_available)


@router.get('/{workflow_id}')
async def get_workflow(
    workflow_id: str,
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    workflow = await ExperimentWorkflows.get(
        workflow_id, db, await task_model_available(request, user)
    )
    if not workflow:
        raise HTTPException(status_code=404, detail='Workflow not found.')
    return workflow


@router.put('/{workflow_id}')
async def update_workflow(
    workflow_id: str,
    form: WorkflowWriteForm,
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    workflow = await ExperimentWorkflows.update(workflow_id, form, user.id, db)
    if not workflow:
        raise HTTPException(status_code=404, detail='Workflow not found.')
    return await ExperimentWorkflows.get(
        workflow_id, db, await task_model_available(request, user)
    )


@router.delete('/{workflow_id}')
async def delete_workflow(
    workflow_id: str,
    revision: int = Query(..., gt=0),
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not await ExperimentWorkflows.delete(workflow_id, revision, db):
        raise HTTPException(status_code=404, detail='Workflow not found.')
    return {'status': True}


@router.get('/{workflow_id}/export')
async def export_workflow(
    workflow_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    result = await ExperimentWorkflows.export(workflow_id, db)
    if not result:
        raise HTTPException(status_code=404, detail='Workflow not found.')
    data, filename = result
    return StreamingResponse(
        io.BytesIO(data),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@router.post('/{workflow_id}/apply')
async def apply_workflow(
    workflow_id: str,
    form: WorkflowApplyForm,
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await ExperimentWorkflows.apply(
        workflow_id,
        form,
        user.id,
        await task_model_available(request, user),
        db,
    )
