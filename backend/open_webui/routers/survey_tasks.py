from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_plans import ExperimentPlan, ExperimentPlanItem, ExperimentSessionTask
from open_webui.models.survey_submissions import (
    SurveyResponse,
    SurveyResponseChoice,
    SurveySubmission,
)
from open_webui.models.survey_tasks import SurveyCloneForm, SurveyTaskForm, SurveyTasks
from open_webui.utils.auth import get_admin_user

router = APIRouter()


async def _published_plan_reference(task_id: str, db: AsyncSession):
    return (
        await db.execute(
            select(ExperimentPlanItem.id)
            .join(ExperimentPlan, ExperimentPlan.id == ExperimentPlanItem.plan_id)
            .where(ExperimentPlanItem.survey_task_id == task_id, ExperimentPlan.status == 'PUBLISHED')
            .limit(1)
        )
    ).first()


@router.get('')
async def list_survey_tasks(
    include_archived: bool = False,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await SurveyTasks.list_tasks(db=db, include_archived=include_archived)


@router.post('')
async def create_survey_task(
    form: SurveyTaskForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    return await SurveyTasks.create_task(form, db=db)


@router.get('/{task_id}')
async def get_survey_task(
    task_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.get_task(task_id, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return task


@router.put('/{task_id}')
async def update_survey_task(
    task_id: str,
    form: SurveyTaskForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.update_task(task_id, form, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return task


@router.post('/{task_id}/clone')
async def clone_survey_task(
    task_id: str,
    form: SurveyCloneForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.clone(task_id, form.mode, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return task


@router.get('/{task_id}/preview')
async def preview_survey_task(
    task_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.participant_task(task_id, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return task


@router.get('/{task_id}/results')
async def survey_task_results(
    task_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.get_task(task_id, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    submissions = list(
        (
            await db.execute(
                select(SurveySubmission)
                .join(ExperimentSessionTask, ExperimentSessionTask.id == SurveySubmission.session_task_id)
                .where(ExperimentSessionTask.survey_task_id == task_id)
                .order_by(SurveySubmission.created_at)
            )
        )
        .scalars()
        .all()
    )
    submission_ids = [submission.id for submission in submissions]
    responses = (
        list(
            (await db.execute(select(SurveyResponse).where(SurveyResponse.submission_id.in_(submission_ids))))
            .scalars()
            .all()
        )
        if submission_ids
        else []
    )
    response_ids = [response.id for response in responses]
    selected = (
        list(
            (await db.execute(select(SurveyResponseChoice).where(SurveyResponseChoice.response_id.in_(response_ids))))
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    selected_map: dict[str, list[str]] = {}
    for selection in selected:
        selected_map.setdefault(selection.response_id, []).append(selection.choice_id)
    response_map: dict[str, list[dict]] = {}
    for response in responses:
        response_map.setdefault(response.submission_id, []).append(
            {
                'question_id': response.question_id,
                'choice_ids': selected_map.get(response.id, []),
                'text': response.text_answer,
                'scale': response.scale_answer,
                'is_answered': response.is_answered,
            }
        )
    return {
        'survey_task': task,
        'submission_count': len(submissions),
        'submissions': [
            {
                'id': submission.id,
                'session_task_id': submission.session_task_id,
                'user_id': submission.user_id,
                'status': submission.status,
                'submitted_at': submission.submitted_at,
                'skipped_at': submission.skipped_at,
                'responses': response_map.get(submission.id, []),
            }
            for submission in submissions
        ],
    }


@router.post('/{task_id}/publish')
async def publish_survey_task(
    task_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    task = await SurveyTasks.publish(task_id, db=db)
    if not task:
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return task


@router.delete('/{task_id}')
async def archive_survey_task(
    task_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    if await _published_plan_reference(task_id, db):
        raise HTTPException(status_code=409, detail='Survey Task is used by a published experiment plan.')
    if not await SurveyTasks.archive(task_id, db=db):
        raise HTTPException(status_code=404, detail='Survey Task not found.')
    return {'status': True}
