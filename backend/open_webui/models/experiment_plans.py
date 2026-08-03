import random
import time
import uuid
from enum import StrEnum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.essays import EssayTopic, EssayTopicModel
from open_webui.models.question_tasks import QuestionTask, QuestionTaskStatus
from open_webui.models.survey_tasks import SurveyTask, SurveyTaskStatus


class ExperimentTaskType(StrEnum):
    ESSAY = 'ESSAY'
    QUESTION = 'QUESTION'
    SURVEY = 'SURVEY'


class ProgressionMode(StrEnum):
    STRICT_SEQUENTIAL = 'STRICT_SEQUENTIAL'
    FREE_NAVIGATION = 'FREE_NAVIGATION'
    SEQUENTIAL_REVIEW = 'SEQUENTIAL_REVIEW'


class ExperimentChatMode(StrEnum):
    SHARED_EXPERIMENT = 'SHARED_EXPERIMENT'
    FRESH_PER_TASK = 'FRESH_PER_TASK'


class EssayTopicMode(StrEnum):
    SPECIFIC = 'SPECIFIC'
    RANDOM_ALL = 'RANDOM_ALL'
    RANDOM_SELECTED = 'RANDOM_SELECTED'


class SessionTaskStatus(StrEnum):
    LOCKED = 'LOCKED'
    AVAILABLE = 'AVAILABLE'
    ACTIVE = 'ACTIVE'
    COMPLETED = 'COMPLETED'
    FINALIZED = 'FINALIZED'
    SKIPPED = 'SKIPPED'


class ExperimentPlan(Base):
    __tablename__ = 'experiment_plan'

    id = Column(Text, primary_key=True)
    group_id = Column(Text, ForeignKey('group.id', ondelete='CASCADE'), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(Text, nullable=False, default='DRAFT')
    progression_mode = Column(Text, nullable=False, default=ProgressionMode.STRICT_SEQUENTIAL.value)
    chat_mode = Column(Text, nullable=False, default=ExperimentChatMode.SHARED_EXPERIMENT.value)
    survey_variant = Column(Text, nullable=False, default='ESSAY')
    consent_enabled = Column(Boolean, nullable=False, default=True)
    locked_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class ExperimentPlanItem(Base):
    __tablename__ = 'experiment_plan_item'

    id = Column(Text, primary_key=True)
    plan_id = Column(Text, ForeignKey('experiment_plan.id', ondelete='CASCADE'), nullable=False)
    position = Column(Integer, nullable=False)
    task_type = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    question_task_id = Column(Text, ForeignKey('question_task.id', ondelete='RESTRICT'), nullable=True)
    essay_topic_mode = Column(Text, nullable=True)
    essay_topic_id = Column(Text, ForeignKey('essay_topic.id', ondelete='RESTRICT'), nullable=True)
    survey_task_id = Column(Text, ForeignKey('survey_task.id', ondelete='RESTRICT'), nullable=True)
    survey_required = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)


class ExperimentPlanItemTopic(Base):
    __tablename__ = 'experiment_plan_item_topic'

    plan_item_id = Column(Text, ForeignKey('experiment_plan_item.id', ondelete='CASCADE'), primary_key=True)
    topic_id = Column(Text, ForeignKey('essay_topic.id', ondelete='RESTRICT'), primary_key=True)


class ExperimentSessionTask(Base):
    __tablename__ = 'experiment_session_task'

    id = Column(Text, primary_key=True)
    experiment_session_id = Column(Text, ForeignKey('experiment_session.id', ondelete='CASCADE'), nullable=False)
    plan_item_id = Column(Text, ForeignKey('experiment_plan_item.id', ondelete='RESTRICT'), nullable=True)
    position = Column(Integer, nullable=False)
    task_type = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    essay_topic_id = Column(Text, nullable=True)
    essay_topic_title = Column(Text, nullable=True)
    essay_topic_question = Column(Text, nullable=True)
    question_task_id = Column(Text, ForeignKey('question_task.id', ondelete='RESTRICT'), nullable=True)
    survey_task_id = Column(Text, ForeignKey('survey_task.id', ondelete='RESTRICT'), nullable=True)
    survey_required = Column(Boolean, nullable=False, default=True)
    essay_id = Column(Text, ForeignKey('essay.id', ondelete='SET NULL'), nullable=True)
    essay_draft = Column(Text, nullable=True)
    started_at = Column(BigInteger, nullable=True)
    completed_at = Column(BigInteger, nullable=True)
    finalized_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class PlanItemForm(BaseModel):
    id: Optional[str] = None
    task_type: ExperimentTaskType
    title: str = Field(min_length=1, max_length=500)
    question_task_id: Optional[str] = None
    essay_topic_mode: Optional[EssayTopicMode] = None
    essay_topic_id: Optional[str] = None
    essay_topic_ids: list[str] = Field(default_factory=list, max_length=1000)
    survey_task_id: Optional[str] = None
    survey_required: bool = True
    enabled: bool = True

    @model_validator(mode='after')
    def validate_item(self):
        self.title = self.title.strip()
        if self.task_type == ExperimentTaskType.QUESTION:
            if not self.question_task_id:
                raise ValueError('Question Task is required.')
        elif self.task_type == ExperimentTaskType.SURVEY:
            if not self.survey_task_id:
                raise ValueError('Survey Task is required.')
        else:
            if self.essay_topic_mode is None:
                raise ValueError('Essay topic mode is required.')
            if self.essay_topic_mode == EssayTopicMode.SPECIFIC and not self.essay_topic_id:
                raise ValueError('A specific essay topic is required.')
            if self.essay_topic_mode == EssayTopicMode.RANDOM_SELECTED and len(set(self.essay_topic_ids)) < 2:
                raise ValueError('A selected random pool requires at least two essay topics.')
        return self


class ExperimentPlanForm(BaseModel):
    progression_mode: ProgressionMode = ProgressionMode.STRICT_SEQUENTIAL
    chat_mode: ExperimentChatMode = ExperimentChatMode.SHARED_EXPERIMENT
    consent_enabled: bool = True
    items: list[PlanItemForm] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_survey_positions(self):
        enabled = [item for item in self.items if item.enabled]
        if self.progression_mode != ProgressionMode.STRICT_SEQUENTIAL:
            task_positions = [
                index for index, item in enumerate(enabled) if item.task_type != ExperimentTaskType.SURVEY
            ]
            if task_positions:
                first, last = min(task_positions), max(task_positions)
                if any(item.task_type == ExperimentTaskType.SURVEY for item in enabled[first : last + 1]):
                    raise ValueError(
                        'Free-navigation and sequential-review plans may place surveys only before or after the task block.'
                    )
        if not enabled:
            raise ValueError('An experiment plan must contain an enabled item.')
        return self


class PlanItemModel(BaseModel):
    id: str
    position: int
    task_type: ExperimentTaskType
    title: str
    question_task_id: Optional[str] = None
    essay_topic_mode: Optional[EssayTopicMode] = None
    essay_topic_id: Optional[str] = None
    essay_topic_ids: list[str] = []
    survey_task_id: Optional[str] = None
    survey_required: bool = True
    enabled: bool = True


class ExperimentPlanModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    version: int
    status: str
    progression_mode: ProgressionMode
    chat_mode: ExperimentChatMode
    survey_variant: str
    consent_enabled: bool = True
    locked_at: Optional[int] = None
    created_at: int
    updated_at: int
    items: list[PlanItemModel] = []


class SessionTaskModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    experiment_session_id: str
    position: int
    task_type: ExperimentTaskType
    title: str
    status: SessionTaskStatus
    essay_topic_id: Optional[str] = None
    essay_topic_title: Optional[str] = None
    essay_topic_question: Optional[str] = None
    question_task_id: Optional[str] = None
    survey_task_id: Optional[str] = None
    survey_required: bool = True
    essay_id: Optional[str] = None
    essay_draft: Optional[str] = None
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    finalized_at: Optional[int] = None


class ExperimentPlanTable:
    async def get_plan(self, plan_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            plan = await db.get(ExperimentPlan, plan_id)
            if not plan:
                return None
            items = list(
                (
                    await db.execute(
                        select(ExperimentPlanItem)
                        .where(ExperimentPlanItem.plan_id == plan_id)
                        .order_by(ExperimentPlanItem.position)
                    )
                )
                .scalars()
                .all()
            )
            item_ids = [item.id for item in items]
            pools = (
                list(
                    (
                        await db.execute(
                            select(ExperimentPlanItemTopic).where(ExperimentPlanItemTopic.plan_item_id.in_(item_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if item_ids
                else []
            )
            pool_map: dict[str, list[str]] = {}
            for pool in pools:
                pool_map.setdefault(pool.plan_item_id, []).append(pool.topic_id)
            return ExperimentPlanModel(
                **{
                    column: getattr(plan, column)
                    for column in (
                        'id',
                        'group_id',
                        'version',
                        'status',
                        'progression_mode',
                        'chat_mode',
                        'survey_variant',
                        'consent_enabled',
                        'locked_at',
                        'created_at',
                        'updated_at',
                    )
                },
                items=[
                    PlanItemModel(
                        id=item.id,
                        position=item.position,
                        task_type=item.task_type,
                        title=item.title,
                        question_task_id=item.question_task_id,
                        essay_topic_mode=item.essay_topic_mode,
                        essay_topic_id=item.essay_topic_id,
                        essay_topic_ids=pool_map.get(item.id, []),
                        survey_task_id=item.survey_task_id,
                        survey_required=item.survey_required,
                        enabled=item.enabled,
                    )
                    for item in items
                ],
            )

    async def get_active_for_group(self, group_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            plan = (
                (
                    await db.execute(
                        select(ExperimentPlan)
                        .where(ExperimentPlan.group_id == group_id, ExperimentPlan.status == 'PUBLISHED')
                        .order_by(ExperimentPlan.version.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return await self.get_plan(plan.id, db=db) if plan else None

    async def save_for_group(self, group_id: str, form: ExperimentPlanForm, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            topic_ids = set((await db.execute(select(EssayTopic.id))).scalars().all())
            question_tasks = {row.id: row for row in (await db.execute(select(QuestionTask))).scalars().all()}
            survey_tasks = {row.id: row for row in (await db.execute(select(SurveyTask))).scalars().all()}
            for item in form.items:
                if item.task_type == ExperimentTaskType.QUESTION:
                    task = question_tasks.get(item.question_task_id)
                    if not task or task.status != QuestionTaskStatus.PUBLISHED.value:
                        raise HTTPException(
                            status_code=422, detail='Every Question Task item must reference a published task.'
                        )
                elif item.task_type == ExperimentTaskType.SURVEY:
                    task = survey_tasks.get(item.survey_task_id)
                    if not task or task.status != SurveyTaskStatus.PUBLISHED.value:
                        raise HTTPException(
                            status_code=422, detail='Every Survey Task item must reference a published survey.'
                        )
                elif item.essay_topic_mode == EssayTopicMode.SPECIFIC and item.essay_topic_id not in topic_ids:
                    raise HTTPException(status_code=422, detail='Essay Task references an unavailable topic.')
                elif item.essay_topic_mode == EssayTopicMode.RANDOM_SELECTED and not set(item.essay_topic_ids).issubset(
                    topic_ids
                ):
                    raise HTTPException(status_code=422, detail='Essay Task pool contains an unavailable topic.')
                elif item.essay_topic_mode == EssayTopicMode.RANDOM_ALL and not topic_ids:
                    raise HTTPException(status_code=422, detail='Random Essay Tasks require at least one essay topic.')

            current = (
                (
                    await db.execute(
                        select(ExperimentPlan)
                        .where(ExperimentPlan.group_id == group_id, ExperimentPlan.status.in_(['DRAFT', 'PUBLISHED']))
                        .order_by(ExperimentPlan.version.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            creating_new_version = current is None or current.status != 'DRAFT' or bool(current.locked_at)
            if current and current.status == 'DRAFT' and not current.locked_at:
                plan = current
                await self._delete_items(plan.id, db)
            else:
                if current:
                    current.status = 'SUPERSEDED'
                    current.updated_at = int(time.time_ns())
                version = (
                    await db.execute(
                        select(func.max(ExperimentPlan.version)).where(ExperimentPlan.group_id == group_id)
                    )
                ).scalar() or 0
                now = int(time.time_ns())
                plan = ExperimentPlan(
                    id=str(uuid.uuid4()),
                    group_id=group_id,
                    version=version + 1,
                    status='DRAFT',
                    progression_mode=form.progression_mode.value,
                    chat_mode=form.chat_mode.value,
                    survey_variant=(
                        'TASK_NEUTRAL'
                        if any(item.task_type == ExperimentTaskType.QUESTION for item in form.items)
                        or len(form.items) > 1
                        else 'ESSAY'
                    ),
                    consent_enabled=form.consent_enabled,
                    created_at=now,
                    updated_at=now,
                )
                db.add(plan)
            plan.progression_mode = form.progression_mode.value
            plan.chat_mode = form.chat_mode.value
            plan.consent_enabled = form.consent_enabled
            plan.survey_variant = (
                'TASK_NEUTRAL'
                if any(item.task_type == ExperimentTaskType.QUESTION for item in form.items) or len(form.items) > 1
                else 'ESSAY'
            )
            plan.status = 'PUBLISHED'
            plan.updated_at = int(time.time_ns())
            for position, form_item in enumerate(form.items):
                # A locked plan is immutable and its item IDs may already be referenced by
                # participant sessions. New versions therefore receive new item identities.
                item_id = str(uuid.uuid4()) if creating_new_version else (form_item.id or str(uuid.uuid4()))
                db.add(
                    ExperimentPlanItem(
                        id=item_id,
                        plan_id=plan.id,
                        position=position,
                        task_type=form_item.task_type.value,
                        title=form_item.title,
                        question_task_id=(
                            form_item.question_task_id if form_item.task_type == ExperimentTaskType.QUESTION else None
                        ),
                        essay_topic_mode=(
                            form_item.essay_topic_mode.value
                            if form_item.task_type == ExperimentTaskType.ESSAY
                            else None
                        ),
                        essay_topic_id=(
                            form_item.essay_topic_id
                            if form_item.task_type == ExperimentTaskType.ESSAY
                            and form_item.essay_topic_mode == EssayTopicMode.SPECIFIC
                            else None
                        ),
                        survey_task_id=(
                            form_item.survey_task_id if form_item.task_type == ExperimentTaskType.SURVEY else None
                        ),
                        survey_required=form_item.survey_required,
                        enabled=form_item.enabled,
                    )
                )
                if (
                    form_item.task_type == ExperimentTaskType.ESSAY
                    and form_item.essay_topic_mode == EssayTopicMode.RANDOM_SELECTED
                ):
                    for topic_id in dict.fromkeys(form_item.essay_topic_ids):
                        db.add(ExperimentPlanItemTopic(plan_item_id=item_id, topic_id=topic_id))
            await db.commit()
            return await self.get_plan(plan.id, db=db)

    async def _delete_items(self, plan_id: str, db: AsyncSession):
        ids = list(
            (await db.execute(select(ExperimentPlanItem.id).where(ExperimentPlanItem.plan_id == plan_id)))
            .scalars()
            .all()
        )
        if ids:
            await db.execute(delete(ExperimentPlanItemTopic).where(ExperimentPlanItemTopic.plan_item_id.in_(ids)))
            await db.execute(delete(ExperimentPlanItem).where(ExperimentPlanItem.id.in_(ids)))

    async def instantiate_session_tasks(self, session_id: str, plan_id: str, db: AsyncSession):
        existing = list(
            (
                await db.execute(
                    select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        if existing:
            return [SessionTaskModel.model_validate(row) for row in existing]
        plan = await self.get_plan(plan_id, db=db)
        if not plan:
            raise HTTPException(status_code=422, detail='Experiment plan is unavailable.')
        all_topics = list((await db.execute(select(EssayTopic))).scalars().all())
        topic_map = {topic.id: topic for topic in all_topics}
        now = int(time.time_ns())
        rows = []
        enabled_items = [item for item in plan.items if item.enabled]
        for session_position, item in enumerate(enabled_items):
            topic: Optional[EssayTopic] = None
            if item.task_type == ExperimentTaskType.ESSAY:
                if item.essay_topic_mode == EssayTopicMode.SPECIFIC:
                    topic = topic_map.get(item.essay_topic_id)
                elif item.essay_topic_mode == EssayTopicMode.RANDOM_SELECTED:
                    candidates = [topic_map[topic_id] for topic_id in item.essay_topic_ids if topic_id in topic_map]
                    topic = random.choice(candidates) if candidates else None
                else:
                    topic = random.choice(all_topics) if all_topics else None
                if not topic:
                    raise HTTPException(status_code=422, detail='An Essay Task has no available topic.')
            elif item.task_type == ExperimentTaskType.QUESTION:
                question_task = await db.get(QuestionTask, item.question_task_id)
                if not question_task or question_task.status != QuestionTaskStatus.PUBLISHED.value:
                    raise HTTPException(status_code=422, detail='A Question Task in this experiment is unavailable.')
                question_task.locked_at = question_task.locked_at or now
            else:
                survey_task = await db.get(SurveyTask, item.survey_task_id)
                if not survey_task or survey_task.status != SurveyTaskStatus.PUBLISHED.value:
                    raise HTTPException(status_code=422, detail='A Survey Task in this experiment is unavailable.')
                survey_task.locked_at = survey_task.locked_at or now
            row = ExperimentSessionTask(
                id=str(uuid.uuid4()),
                experiment_session_id=session_id,
                plan_item_id=item.id,
                position=session_position,
                task_type=item.task_type.value,
                title=item.title,
                status=SessionTaskStatus.LOCKED.value,
                essay_topic_id=topic.id if topic else None,
                essay_topic_title=topic.title if topic else None,
                essay_topic_question=topic.question if topic else None,
                question_task_id=item.question_task_id,
                survey_task_id=item.survey_task_id,
                survey_required=item.survey_required,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            rows.append(row)
        plan_row = await db.get(ExperimentPlan, plan_id)
        plan_row.locked_at = plan_row.locked_at or now
        await db.flush()
        return [SessionTaskModel.model_validate(row) for row in rows]

    async def session_tasks(self, session_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            rows = list(
                (
                    await db.execute(
                        select(ExperimentSessionTask)
                        .where(ExperimentSessionTask.experiment_session_id == session_id)
                        .order_by(ExperimentSessionTask.position)
                    )
                )
                .scalars()
                .all()
            )
            return [SessionTaskModel.model_validate(row) for row in rows]


ExperimentPlans = ExperimentPlanTable()
