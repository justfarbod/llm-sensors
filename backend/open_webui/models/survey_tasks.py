import time
import uuid
from enum import StrEnum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context


class SurveyQuestionType(StrEnum):
    SINGLE_CHOICE = 'SINGLE_CHOICE'
    MULTIPLE_SELECT = 'MULTIPLE_SELECT'
    SCALE = 'SCALE'
    SHORT_TEXT = 'SHORT_TEXT'
    LONG_TEXT = 'LONG_TEXT'


class SurveyTaskStatus(StrEnum):
    DRAFT = 'DRAFT'
    PUBLISHED = 'PUBLISHED'
    ARCHIVED = 'ARCHIVED'


class SurveyCloneMode(StrEnum):
    VERSION = 'VERSION'
    DUPLICATE = 'DUPLICATE'


class SurveyTask(Base):
    __tablename__ = 'survey_task'

    id = Column(Text, primary_key=True)
    family_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default='')
    status = Column(Text, nullable=False, default=SurveyTaskStatus.DRAFT.value)
    locked_at = Column(BigInteger, nullable=True)
    archived_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SurveyQuestion(Base):
    __tablename__ = 'survey_question'

    id = Column(Text, primary_key=True)
    task_id = Column(Text, ForeignKey('survey_task.id', ondelete='CASCADE'), nullable=False)
    prompt = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default='')
    question_type = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    required = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
    scale_low_label = Column(Text, nullable=True)
    scale_high_label = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SurveyChoice(Base):
    __tablename__ = 'survey_choice'

    id = Column(Text, primary_key=True)
    question_id = Column(Text, ForeignKey('survey_question.id', ondelete='CASCADE'), nullable=False)
    text = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)


class SurveyChoiceForm(BaseModel):
    id: Optional[str] = None
    text: str = Field(min_length=1, max_length=4000)

    @field_validator('text')
    @classmethod
    def strip_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Choice text is required.')
        return value


class SurveyQuestionForm(BaseModel):
    id: Optional[str] = None
    prompt: str = Field(min_length=1, max_length=1000)
    description: str = Field(default='', max_length=20000)
    question_type: SurveyQuestionType
    required: bool = True
    enabled: bool = True
    choices: list[SurveyChoiceForm] = Field(default_factory=list, max_length=100)
    scale_low_label: Optional[str] = Field(default=None, max_length=200)
    scale_high_label: Optional[str] = Field(default=None, max_length=200)

    @field_validator('prompt', 'description')
    @classmethod
    def strip_strings(cls, value):
        return value.strip()

    @field_validator('scale_low_label', 'scale_high_label')
    @classmethod
    def strip_optional(cls, value):
        return value.strip() if value else None

    @model_validator(mode='after')
    def validate_configuration(self):
        if self.question_type in {SurveyQuestionType.SINGLE_CHOICE, SurveyQuestionType.MULTIPLE_SELECT}:
            if len(self.choices) < 2:
                raise ValueError('Choice survey questions require at least two choices.')
            texts = [choice.text.casefold() for choice in self.choices]
            if len(texts) != len(set(texts)):
                raise ValueError('Survey choices must be unique.')
        elif self.choices:
            raise ValueError('Only choice survey questions may define choices.')
        if self.question_type == SurveyQuestionType.SCALE:
            if not self.scale_low_label or not self.scale_high_label:
                raise ValueError('Scale survey questions require both endpoint labels.')
        elif self.scale_low_label or self.scale_high_label:
            raise ValueError('Only scale survey questions may define endpoint labels.')
        return self


class SurveyTaskForm(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=20000)
    questions: list[SurveyQuestionForm] = Field(default_factory=list, max_length=500)

    @field_validator('title', 'description')
    @classmethod
    def strip_strings(cls, value):
        return value.strip()


class SurveyCloneForm(BaseModel):
    mode: SurveyCloneMode


class SurveyChoiceModel(BaseModel):
    id: str
    text: str
    position: int


class SurveyQuestionModel(BaseModel):
    id: str
    prompt: str
    description: str
    question_type: SurveyQuestionType
    position: int
    required: bool
    enabled: bool
    choices: list[SurveyChoiceModel] = []
    scale_low_label: Optional[str] = None
    scale_high_label: Optional[str] = None


class SurveyTaskModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    version: int
    latest_version: int
    title: str
    description: str
    status: SurveyTaskStatus
    locked_at: Optional[int] = None
    archived_at: Optional[int] = None
    created_at: int
    updated_at: int
    questions: list[SurveyQuestionModel] = []


class ParticipantSurveyQuestionModel(BaseModel):
    id: str
    prompt: str
    description: str
    question_type: SurveyQuestionType
    position: int
    required: bool
    choices: list[SurveyChoiceModel] = []
    scale_low_label: Optional[str] = None
    scale_high_label: Optional[str] = None


class ParticipantSurveyTaskModel(BaseModel):
    id: str
    title: str
    description: str
    version: int
    questions: list[ParticipantSurveyQuestionModel]


class SurveyTaskTable:
    async def list_tasks(self, db: Optional[AsyncSession] = None, include_archived: bool = False):
        async with get_async_db_context(db) as db:
            stmt = select(SurveyTask).order_by(SurveyTask.updated_at.desc())
            if not include_archived:
                stmt = stmt.where(SurveyTask.status != SurveyTaskStatus.ARCHIVED.value)
            rows = list((await db.execute(stmt)).scalars().all())
            return [await self.get_task(row.id, db=db) for row in rows]

    async def get_task(self, task_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            task = await db.get(SurveyTask, task_id)
            if not task:
                return None
            questions = list(
                (
                    await db.execute(
                        select(SurveyQuestion)
                        .where(SurveyQuestion.task_id == task_id)
                        .order_by(SurveyQuestion.position)
                    )
                )
                .scalars()
                .all()
            )
            ids = [question.id for question in questions]
            choices = (
                list(
                    (
                        await db.execute(
                            select(SurveyChoice)
                            .where(SurveyChoice.question_id.in_(ids))
                            .order_by(SurveyChoice.position)
                        )
                    )
                    .scalars()
                    .all()
                )
                if ids
                else []
            )
            choice_map: dict[str, list[SurveyChoice]] = {}
            for choice in choices:
                choice_map.setdefault(choice.question_id, []).append(choice)
            latest = (
                await db.execute(select(func.max(SurveyTask.version)).where(SurveyTask.family_id == task.family_id))
            ).scalar() or task.version
            return SurveyTaskModel(
                id=task.id,
                family_id=task.family_id,
                version=task.version,
                latest_version=latest,
                title=task.title,
                description=task.description,
                status=task.status,
                locked_at=task.locked_at,
                archived_at=task.archived_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
                questions=[
                    SurveyQuestionModel(
                        id=question.id,
                        prompt=question.prompt,
                        description=question.description,
                        question_type=question.question_type,
                        position=question.position,
                        required=question.required,
                        enabled=question.enabled,
                        choices=[
                            SurveyChoiceModel(id=choice.id, text=choice.text, position=choice.position)
                            for choice in choice_map.get(question.id, [])
                        ],
                        scale_low_label=question.scale_low_label,
                        scale_high_label=question.scale_high_label,
                    )
                    for question in questions
                ],
            )

    async def create_task(self, form: SurveyTaskForm, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            task_id = str(uuid.uuid4())
            row = SurveyTask(
                id=task_id,
                family_id=task_id,
                version=1,
                title=form.title,
                description=form.description,
                status=SurveyTaskStatus.DRAFT.value,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            await self._replace_questions(row, form.questions, db)
            await db.commit()
            return await self.get_task(row.id, db=db)

    async def update_task(self, task_id: str, form: SurveyTaskForm, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            row = await db.get(SurveyTask, task_id)
            if not row:
                return None
            if row.status != SurveyTaskStatus.DRAFT.value or row.locked_at:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='Published Survey Tasks are immutable. Create a new version or duplicate instead.',
                )
            row.title = form.title
            row.description = form.description
            row.updated_at = int(time.time_ns())
            await self._delete_questions(row.id, db)
            await self._replace_questions(row, form.questions, db)
            await db.commit()
            return await self.get_task(row.id, db=db)

    async def clone(self, task_id: str, mode: SurveyCloneMode, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            source = await self.get_task(task_id, db=db)
            if not source:
                return None
            now = int(time.time_ns())
            new_id = str(uuid.uuid4())
            family_id = source.family_id if mode == SurveyCloneMode.VERSION else new_id
            version = (
                (
                    await db.execute(select(func.max(SurveyTask.version)).where(SurveyTask.family_id == family_id))
                ).scalar()
                or 0
            ) + 1
            if mode == SurveyCloneMode.DUPLICATE:
                version = 1
            row = SurveyTask(
                id=new_id,
                family_id=family_id,
                version=version,
                title=source.title if mode == SurveyCloneMode.VERSION else f'{source.title} (copy)',
                description=source.description,
                status=SurveyTaskStatus.DRAFT.value,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            forms = [
                SurveyQuestionForm(
                    prompt=question.prompt,
                    description=question.description,
                    question_type=question.question_type,
                    required=question.required,
                    enabled=question.enabled,
                    choices=[SurveyChoiceForm(text=choice.text) for choice in question.choices],
                    scale_low_label=question.scale_low_label,
                    scale_high_label=question.scale_high_label,
                )
                for question in source.questions
            ]
            await self._replace_questions(row, forms, db)
            await db.commit()
            return await self.get_task(row.id, db=db)

    async def publish(self, task_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            task = await self.get_task(task_id, db=db)
            if not task:
                return None
            if task.status != SurveyTaskStatus.DRAFT:
                return task
            if not any(question.enabled for question in task.questions):
                raise HTTPException(status_code=422, detail='A Survey Task must contain an enabled question.')
            row = await db.get(SurveyTask, task_id)
            row.status = SurveyTaskStatus.PUBLISHED.value
            row.updated_at = int(time.time_ns())
            await db.commit()
            return await self.get_task(task_id, db=db)

    async def archive(self, task_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            row = await db.get(SurveyTask, task_id)
            if not row:
                return False
            row.status = SurveyTaskStatus.ARCHIVED.value
            row.archived_at = row.updated_at = int(time.time_ns())
            await db.commit()
            return True

    async def participant_task(self, task_id: str, db: Optional[AsyncSession] = None):
        task = await self.get_task(task_id, db=db)
        if not task:
            return None
        return ParticipantSurveyTaskModel(
            id=task.id,
            title=task.title,
            description=task.description,
            version=task.version,
            questions=[
                ParticipantSurveyQuestionModel(
                    id=question.id,
                    prompt=question.prompt,
                    description=question.description,
                    question_type=question.question_type,
                    position=question.position,
                    required=question.required,
                    choices=question.choices,
                    scale_low_label=question.scale_low_label,
                    scale_high_label=question.scale_high_label,
                )
                for question in task.questions
                if question.enabled
            ],
        )

    async def _delete_questions(self, task_id: str, db: AsyncSession):
        ids = list(
            (await db.execute(select(SurveyQuestion.id).where(SurveyQuestion.task_id == task_id))).scalars().all()
        )
        if ids:
            await db.execute(delete(SurveyChoice).where(SurveyChoice.question_id.in_(ids)))
            await db.execute(delete(SurveyQuestion).where(SurveyQuestion.id.in_(ids)))

    async def _replace_questions(self, task: SurveyTask, forms: list[SurveyQuestionForm], db: AsyncSession):
        now = int(time.time_ns())
        for position, form in enumerate(forms):
            question_id = form.id or str(uuid.uuid4())
            db.add(
                SurveyQuestion(
                    id=question_id,
                    task_id=task.id,
                    prompt=form.prompt,
                    description=form.description,
                    question_type=form.question_type.value,
                    position=position,
                    required=form.required,
                    enabled=form.enabled,
                    scale_low_label=form.scale_low_label,
                    scale_high_label=form.scale_high_label,
                    created_at=now,
                    updated_at=now,
                )
            )
            for choice_position, choice in enumerate(form.choices):
                db.add(
                    SurveyChoice(
                        id=choice.id or str(uuid.uuid4()),
                        question_id=question_id,
                        text=choice.text,
                        position=choice_position,
                    )
                )


SurveyTasks = SurveyTaskTable()
