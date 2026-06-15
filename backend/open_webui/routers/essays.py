from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.essays import (
    EssayForm,
    EssayModel,
    EssayTopicForm,
    EssayTopicModel,
    EssayTopicAssignments,
    EssayTopics,
    Essays,
    resolve_user_topic,
)
from open_webui.models.experiments import Experiments, require_experiment_chat_access
from open_webui.models.experiments import ExperimentState
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


async def require_essay_sidebar_access(request: Request, user, db: AsyncSession):
    if user.role != 'admin' and not await has_permission(
        user.id, 'features.essay_sidebar', request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


class EssayWorkspaceResponse(BaseModel):
    topic: Optional[EssayTopicModel] = None
    latest_essay: Optional[EssayModel] = None


@router.get('/workspace', response_model=EssayWorkspaceResponse)
async def get_essay_workspace(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await require_experiment_chat_access(user, db=db)
    await require_essay_sidebar_access(request, user, db)
    experiment_state, experiment_session, _ = await Experiments.get_current(user, db=db)
    return EssayWorkspaceResponse(
        topic=(
            Experiments.topic_from_session(experiment_session)
            if experiment_state == ExperimentState.IN_PROGRESS and experiment_session
            else await resolve_user_topic(user, db)
        ),
        latest_essay=await Essays.get_latest_essay_by_user_id(user.id, db=db),
    )


@router.post('/submit', response_model=EssayModel)
async def submit_essay(
    request: Request,
    form_data: EssayForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await require_experiment_chat_access(user, db=db)
    await require_essay_sidebar_access(request, user, db)

    content = form_data.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.EMPTY_CONTENT,
        )

    experiment_state, _, _ = await Experiments.get_current(user, db=db)
    if experiment_state == ExperimentState.IN_PROGRESS:
        # Persist the essay and advance the experiment in one transaction.
        return await Experiments.submit_essay(user, content, db)
    return await Essays.insert_new_essay(user.id, content, topic=await resolve_user_topic(user, db), db=db)


@router.get('/topics', response_model=list[EssayTopicModel])
async def get_topics(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    return await EssayTopics.get_topics(db=db)


@router.post('/topics/create', response_model=EssayTopicModel)
async def create_topic(
    form_data: EssayTopicForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    title = form_data.title.strip()
    question = form_data.question.strip()
    if not title or not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.EMPTY_CONTENT)
    return await EssayTopics.insert_new_topic(EssayTopicForm(title=title, question=question), db=db)


@router.post('/topics/{topic_id}/update', response_model=Optional[EssayTopicModel])
async def update_topic(
    topic_id: str,
    form_data: EssayTopicForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    title = form_data.title.strip()
    question = form_data.question.strip()
    if not title or not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.EMPTY_CONTENT)
    topic = await EssayTopics.update_topic_by_id(topic_id, EssayTopicForm(title=title, question=question), db=db)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return topic


@router.delete('/topics/{topic_id}/delete', response_model=bool)
async def delete_topic(
    topic_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    deleted = await EssayTopics.delete_topic_by_id(topic_id, db=db)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return True
