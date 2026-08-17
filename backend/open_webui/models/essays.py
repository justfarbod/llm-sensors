import time
import uuid
import random
from typing import Optional
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, Text, UniqueConstraint, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.groups import Groups
from open_webui.utils.essay_text import essay_text_metrics


class Essay(Base):
    __tablename__ = 'essay'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    topic_id = Column(Text, nullable=True)
    topic_title = Column(Text, nullable=True)
    topic_question = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=True)
    character_count = Column(Integer, nullable=True)
    experiment_session_task_id = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_essay_user_created_at', 'user_id', 'created_at'),)


class EssayModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    content: str
    topic_id: Optional[str] = None
    topic_title: Optional[str] = None
    topic_question: Optional[str] = None
    word_count: Optional[int] = None
    character_count: Optional[int] = None
    experiment_session_task_id: Optional[str] = None
    created_at: int
    updated_at: int


class EssayForm(BaseModel):
    content: str


class EssayTopic(Base):
    __tablename__ = 'essay_topic'

    id = Column(Text, primary_key=True)
    title = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    locked_at = Column(BigInteger, nullable=True)
    workflow_managed = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class EssayTopicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    question: str
    locked_at: Optional[int] = None
    workflow_managed: bool = False
    created_at: int
    updated_at: int


class EssayTopicForm(BaseModel):
    title: str
    question: str


class EssayTopicAssignment(Base):
    __tablename__ = 'essay_topic_assignment'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    group_id = Column(Text, nullable=False)
    topic_id = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_essay_topic_assignment_user_group'),
        Index('ix_essay_topic_assignment_topic', 'topic_id'),
    )


class EssayTopicAssignmentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    group_id: str
    topic_id: str
    created_at: int
    updated_at: int


class EssayTable:
    async def insert_new_essay(
        self,
        user_id: str,
        content: str,
        topic: Optional[EssayTopicModel] = None,
        db: Optional[AsyncSession] = None,
    ) -> EssayModel:
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            word_count, character_count = essay_text_metrics(content)
            essay = Essay(
                id=str(uuid.uuid4()),
                user_id=user_id,
                content=content,
                topic_id=topic.id if topic else None,
                topic_title=topic.title if topic else None,
                topic_question=topic.question if topic else None,
                word_count=word_count,
                character_count=character_count,
                created_at=now,
                updated_at=now,
            )
            db.add(essay)
            await db.commit()
            await db.refresh(essay)
            return EssayModel.model_validate(essay)

    async def get_latest_essay_by_user_id(
        self, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[EssayModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Essay).filter_by(user_id=user_id).order_by(Essay.created_at.desc()).limit(1)
            )
            essay = result.scalars().first()
            return EssayModel.model_validate(essay) if essay else None


Essays = EssayTable()


class EssayTopicTable:
    async def insert_new_topic(self, form_data: EssayTopicForm, db: Optional[AsyncSession] = None) -> EssayTopicModel:
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            topic = EssayTopic(
                id=str(uuid.uuid4()),
                title=form_data.title,
                question=form_data.question,
                created_at=now,
                updated_at=now,
            )
            db.add(topic)
            await db.commit()
            await db.refresh(topic)
            return EssayTopicModel.model_validate(topic)

    async def get_topics(self, db: Optional[AsyncSession] = None) -> list[EssayTopicModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(EssayTopic)
                .where(EssayTopic.workflow_managed.is_(False))
                .order_by(EssayTopic.created_at.desc())
            )
            return [EssayTopicModel.model_validate(topic) for topic in result.scalars().all()]

    async def get_topic_by_id(self, topic_id: str, db: Optional[AsyncSession] = None) -> Optional[EssayTopicModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(EssayTopic).filter_by(id=topic_id))
            topic = result.scalars().first()
            return EssayTopicModel.model_validate(topic) if topic else None

    async def update_topic_by_id(
        self, topic_id: str, form_data: EssayTopicForm, db: Optional[AsyncSession] = None
    ) -> Optional[EssayTopicModel]:
        async with get_async_db_context(db) as db:
            topic = await db.get(EssayTopic, topic_id)
            if topic and topic.locked_at:
                raise HTTPException(status_code=409, detail='Applied workflow topics are immutable.')
            await db.execute(
                update(EssayTopic)
                .filter_by(id=topic_id)
                .values(**form_data.model_dump(), updated_at=int(time.time_ns()))
            )
            await db.commit()
            return await self.get_topic_by_id(topic_id, db=db)

    async def delete_topic_by_id(self, topic_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            topic = await db.get(EssayTopic, topic_id)
            if topic and topic.locked_at:
                raise HTTPException(status_code=409, detail='Applied workflow topics are immutable.')
            await db.execute(delete(EssayTopicAssignment).filter_by(topic_id=topic_id))
            result = await db.execute(delete(EssayTopic).filter_by(id=topic_id))
            await db.commit()
            return bool(result.rowcount)


class EssayTopicAssignmentTable:
    async def get_assignment(
        self, user_id: str, group_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[EssayTopicAssignmentModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(EssayTopicAssignment).filter_by(user_id=user_id, group_id=group_id))
            assignment = result.scalars().first()
            return EssayTopicAssignmentModel.model_validate(assignment) if assignment else None

    async def set_assignment(
        self, user_id: str, group_id: str, topic_id: str, db: Optional[AsyncSession] = None
    ) -> EssayTopicAssignmentModel:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(EssayTopicAssignment).filter_by(user_id=user_id, group_id=group_id))
            assignment = result.scalars().first()
            now = int(time.time_ns())
            if assignment:
                assignment.topic_id = topic_id
                assignment.updated_at = now
            else:
                assignment = EssayTopicAssignment(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    group_id=group_id,
                    topic_id=topic_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(assignment)
            await db.commit()
            await db.refresh(assignment)
            return EssayTopicAssignmentModel.model_validate(assignment)


EssayTopics = EssayTopicTable()
EssayTopicAssignments = EssayTopicAssignmentTable()


async def resolve_topic_for_group(
    user_id: str, group, db: Optional[AsyncSession] = None
) -> Optional[EssayTopicModel]:
    topics = await EssayTopics.get_topics(db=db)
    if not topics:
        return None
    topic_by_id = {topic.id: topic for topic in topics}
    config = (group.data or {}).get('config', {})
    topic_id = config.get('essay_topic_id')
    if config.get('essay_topic_mode', 'random') == 'specific' and topic_id in topic_by_id:
        return topic_by_id[topic_id]
    assignment = await EssayTopicAssignments.get_assignment(user_id, group.id, db=db)
    if assignment and assignment.topic_id in topic_by_id:
        return topic_by_id[assignment.topic_id]
    topic = random.choice(topics)
    await EssayTopicAssignments.set_assignment(user_id, group.id, topic.id, db=db)
    return topic


async def resolve_user_topic(user, db: Optional[AsyncSession] = None) -> Optional[EssayTopicModel]:
    if user.role == 'admin':
        return await resolve_topic_for_group(
            user.id, SimpleNamespace(id='__admin__', data={'config': {'essay_topic_mode': 'random'}}), db=db
        )
    groups = await Groups.get_groups_by_member_id(user.id, db=db)
    eligible = [
        group for group in groups if (group.permissions or {}).get('features', {}).get('essay_sidebar', False)
    ]
    eligible.sort(key=lambda group: group.id)
    configured = [
        group for group in eligible
        if (group.data or {}).get('config', {}).get('essay_topic_mode') in ('random', 'specific')
    ]
    if eligible:
        return await resolve_topic_for_group(user.id, (configured or eligible)[0], db=db)
    return await resolve_topic_for_group(
        user.id, SimpleNamespace(id='__default__', data={'config': {'essay_topic_mode': 'random'}}), db=db
    )
