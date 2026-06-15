import random
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
)
from open_webui.models.groups import Groups
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


async def resolve_user_topic(user, db: AsyncSession) -> Optional[EssayTopicModel]:
    topics = await EssayTopics.get_topics(db=db)
    if not topics:
        return None

    topic_by_id = {topic.id: topic for topic in topics}

    if user.role == 'admin':
        group_id = '__admin__'
        mode = 'random'
        topic_id = None
    else:
        groups = await Groups.get_groups_by_member_id(user.id, db=db)
        eligible_groups = [
            group for group in groups if (group.permissions or {}).get('features', {}).get('essay_sidebar', False)
        ]
        eligible_groups.sort(key=lambda group: group.id)
        configured_groups = [
            group
            for group in eligible_groups
            if (group.data or {}).get('config', {}).get('essay_topic_mode') in ('random', 'specific')
        ]

        if eligible_groups:
            group = (configured_groups or eligible_groups)[0]
            group_id = group.id
            config = (group.data or {}).get('config', {})
            mode = config.get('essay_topic_mode', 'random')
            topic_id = config.get('essay_topic_id')
        else:
            group_id = '__default__'
            mode = 'random'
            topic_id = None

    if mode == 'specific' and topic_id in topic_by_id:
        return topic_by_id[topic_id]

    assignment = await EssayTopicAssignments.get_assignment(user.id, group_id, db=db)
    if assignment and assignment.topic_id in topic_by_id:
        return topic_by_id[assignment.topic_id]

    topic = random.choice(topics)
    await EssayTopicAssignments.set_assignment(user.id, group_id, topic.id, db=db)
    return topic


class EssayWorkspaceResponse(BaseModel):
    topic: Optional[EssayTopicModel] = None
    latest_essay: Optional[EssayModel] = None


@router.get('/workspace', response_model=EssayWorkspaceResponse)
async def get_essay_workspace(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await require_essay_sidebar_access(request, user, db)
    return EssayWorkspaceResponse(
        topic=await resolve_user_topic(user, db),
        latest_essay=await Essays.get_latest_essay_by_user_id(user.id, db=db),
    )


@router.post('/submit', response_model=EssayModel)
async def submit_essay(
    request: Request,
    form_data: EssayForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    await require_essay_sidebar_access(request, user, db)

    content = form_data.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.EMPTY_CONTENT,
        )

    topic = await resolve_user_topic(user, db)
    return await Essays.insert_new_essay(user.id, content, topic=topic, db=db)


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
