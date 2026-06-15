import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Index, Text, UniqueConstraint, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class Essay(Base):
    __tablename__ = 'essay'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    topic_id = Column(Text, nullable=True)
    topic_title = Column(Text, nullable=True)
    topic_question = Column(Text, nullable=True)
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
    created_at: int
    updated_at: int


class EssayForm(BaseModel):
    content: str


class EssayTopic(Base):
    __tablename__ = 'essay_topic'

    id = Column(Text, primary_key=True)
    title = Column(Text, nullable=False)
    question = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class EssayTopicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    question: str
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
            essay = Essay(
                id=str(uuid.uuid4()),
                user_id=user_id,
                content=content,
                topic_id=topic.id if topic else None,
                topic_title=topic.title if topic else None,
                topic_question=topic.question if topic else None,
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
            result = await db.execute(select(EssayTopic).order_by(EssayTopic.created_at.desc()))
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
            await db.execute(
                update(EssayTopic)
                .filter_by(id=topic_id)
                .values(**form_data.model_dump(), updated_at=int(time.time_ns()))
            )
            await db.commit()
            return await self.get_topic_by_id(topic_id, db=db)

    async def delete_topic_by_id(self, topic_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
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
