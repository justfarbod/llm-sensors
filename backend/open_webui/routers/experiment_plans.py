from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_plans import ExperimentPlanForm, ExperimentPlans
from open_webui.models.groups import Groups
from open_webui.models.experiment_perturbations import default_control_condition
from open_webui.utils.auth import get_admin_user

router = APIRouter()


@router.get('/groups/{group_id}')
async def get_group_experiment_plan(
    group_id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    if not await Groups.get_group_by_id(group_id, db=db):
        raise HTTPException(status_code=404, detail='Group not found.')
    plan = await ExperimentPlans.get_active_for_group(group_id, db=db)
    return plan or {
        'group_id': group_id,
        'status': 'DRAFT',
        'version': 0,
        'progression_mode': 'STRICT_SEQUENTIAL',
        'chat_mode': 'SHARED_EXPERIMENT',
        'consent_enabled': True,
        'survey_variant': 'ESSAY',
        'items': [],
        'conditions': [{'id': 'draft-control', **default_control_condition().model_dump(mode='json')}],
    }


@router.put('/groups/{group_id}')
async def save_group_experiment_plan(
    group_id: str, form: ExperimentPlanForm, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    group = await Groups.get_group_by_id(group_id, db=db)
    if not group:
        raise HTTPException(status_code=404, detail='Group not found.')
    return await ExperimentPlans.save_for_group(group_id, form, db=db)
