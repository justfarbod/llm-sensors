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
from open_webui.models.experiment_perturbations import (
    ExperimentCondition,
    ExperimentConditionForm,
    ExperimentConditionModel,
    ExperimentConditionTaskScope,
    ExperimentMemoryInjection,
    ExperimentPromptInjection,
    ExperimentResponseTiming,
    ExperimentWarningModal,
    default_control_condition,
)


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
    conditions: list[ExperimentConditionForm] = Field(default_factory=lambda: [default_control_condition()])

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
        enabled_conditions = [condition for condition in self.conditions if condition.enabled]
        controls = [condition for condition in enabled_conditions if condition.is_control]
        if len(controls) != 1:
            raise ValueError('An experiment plan must contain exactly one enabled control condition.')
        if controls[0].allocation_percent < 1:
            raise ValueError('The control condition must receive at least one percent allocation.')
        if sum(condition.allocation_percent for condition in enabled_conditions) != 100:
            raise ValueError('Enabled condition allocation percentages must total 100.')
        item_ids = {item.id for item in self.items if item.id}
        for condition in enabled_conditions:
            for settings in (condition.prompt_injection, condition.memory_injection):
                if settings.activation.scope.value == 'SELECTED_TASKS' and not set(
                    settings.activation.plan_item_ids
                ).issubset(item_ids):
                    raise ValueError('Condition task scope references an unavailable plan item.')
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
    conditions: list[ExperimentConditionModel] = []


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
            condition_rows = list(
                (
                    await db.execute(
                        select(ExperimentCondition)
                        .where(ExperimentCondition.plan_id == plan_id)
                        .order_by(ExperimentCondition.position)
                    )
                )
                .scalars()
                .all()
            )
            conditions = []
            for condition in condition_rows:
                prompt = await db.get(ExperimentPromptInjection, condition.id)
                memory = await db.get(ExperimentMemoryInjection, condition.id)
                warning = await db.get(ExperimentWarningModal, condition.id)
                timing = await db.get(ExperimentResponseTiming, condition.id)
                scopes = list(
                    (
                        await db.execute(
                            select(ExperimentConditionTaskScope).where(
                                ExperimentConditionTaskScope.condition_id == condition.id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                prompt_scope = [row.plan_item_id for row in scopes if row.perturbation_type == 'PROMPT']
                memory_scope = [row.plan_item_id for row in scopes if row.perturbation_type == 'MEMORY']
                conditions.append(
                    ExperimentConditionModel.model_validate(
                        {
                            'id': condition.id,
                            'name': condition.name,
                            'allocation_percent': condition.allocation_percent,
                            'enabled': condition.enabled,
                            'is_control': condition.is_control,
                            'prompt_injection': {
                                'enabled': prompt.enabled if prompt else False,
                                'instruction': prompt.instruction if prompt else '',
                                'position': prompt.position if prompt else 'SYSTEM',
                                'activation': {
                                    'mode': prompt.activation_mode if prompt else 'EVERY_REQUEST',
                                    'count': prompt.activation_count if prompt else None,
                                    'range_start': prompt.range_start if prompt else None,
                                    'range_end': prompt.range_end if prompt else None,
                                    'probability': prompt.probability if prompt else 1,
                                    'scope': prompt.scope if prompt else 'ALL_TASKS',
                                    'plan_item_ids': prompt_scope,
                                },
                            },
                            'memory_injection': {
                                'enabled': memory.enabled if memory else False,
                                'content': memory.content if memory else '',
                                'persist_for_session': memory.persist_for_session if memory else False,
                                'activation': {
                                    'mode': memory.activation_mode if memory else 'EVERY_REQUEST',
                                    'count': memory.activation_count if memory else None,
                                    'range_start': memory.range_start if memory else None,
                                    'range_end': memory.range_end if memory else None,
                                    'probability': memory.probability if memory else 1,
                                    'scope': memory.scope if memory else 'ALL_TASKS',
                                    'plan_item_ids': memory_scope,
                                },
                            },
                            'warning_modal': {
                                'enabled': warning.enabled if warning else False,
                                'title': warning.title if warning else 'Important reminder',
                                'message': (
                                    warning.message
                                    if warning
                                    else 'LLMs can make mistakes. Double-check important answers.'
                                ),
                                'confirmation_text': warning.confirmation_text if warning else 'Continue',
                                'must_acknowledge': warning.must_acknowledge if warning else True,
                                'cadence': warning.cadence if warning else 'BEGINNING',
                                'cadence_value': warning.cadence_value if warning else None,
                                'prompt_numbers': warning.prompt_numbers if warning else [],
                            },
                            'response_timing': {
                                'mode': timing.mode if timing else 'NORMAL',
                                'delay_seconds': timing.delay_seconds if timing else None,
                                'show_loading': timing.show_loading if timing else True,
                                'reveal_style': timing.reveal_style if timing else 'FULL',
                                'target_duration_seconds': timing.target_duration_seconds if timing else None,
                                'stream_unit': timing.stream_unit if timing else 'CHARACTER',
                                'minimum_chunk_size': timing.minimum_chunk_size if timing else 1,
                                'maximum_chunk_size': timing.maximum_chunk_size if timing else 20,
                                'punctuation_pauses': timing.punctuation_pauses if timing else False,
                                'rate_value': timing.rate_value if timing else None,
                                'rate_unit': timing.rate_unit if timing else 'CHARACTERS_PER_SECOND',
                            },
                        }
                    )
                )
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
                conditions=conditions
                or [ExperimentConditionModel(id='implicit-control', **default_control_condition().model_dump())],
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
            saved_item_pairs = []
            for position, form_item in enumerate(form.items):
                # A locked plan is immutable and its item IDs may already be referenced by
                # participant sessions. New versions therefore receive new item identities.
                item_id = str(uuid.uuid4()) if creating_new_version else (form_item.id or str(uuid.uuid4()))
                saved_item_pairs.append((form_item, item_id))
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
            await db.flush()
            await self._save_conditions(plan.id, form.conditions, saved_item_pairs, creating_new_version, db)
            await db.commit()
            return await self.get_plan(plan.id, db=db)

    async def _save_conditions(self, plan_id, conditions, saved_item_pairs, creating_new_version, db):
        if not creating_new_version:
            old_ids = list(
                (await db.execute(select(ExperimentCondition.id).where(ExperimentCondition.plan_id == plan_id)))
                .scalars()
                .all()
            )
            if old_ids:
                await db.execute(
                    delete(ExperimentConditionTaskScope).where(ExperimentConditionTaskScope.condition_id.in_(old_ids))
                )
                await db.execute(
                    delete(ExperimentResponseTiming).where(ExperimentResponseTiming.condition_id.in_(old_ids))
                )
                await db.execute(delete(ExperimentWarningModal).where(ExperimentWarningModal.condition_id.in_(old_ids)))
                await db.execute(
                    delete(ExperimentMemoryInjection).where(ExperimentMemoryInjection.condition_id.in_(old_ids))
                )
                await db.execute(
                    delete(ExperimentPromptInjection).where(ExperimentPromptInjection.condition_id.in_(old_ids))
                )
                await db.execute(delete(ExperimentCondition).where(ExperimentCondition.id.in_(old_ids)))
        item_map = {form_item.id: saved_id for form_item, saved_id in saved_item_pairs if form_item.id}
        saved_item_ids = {saved_id for _, saved_id in saved_item_pairs}
        now = int(time.time_ns())
        for position, form in enumerate(conditions):
            condition_id = str(uuid.uuid4()) if creating_new_version or not form.id else form.id
            db.add(
                ExperimentCondition(
                    id=condition_id,
                    plan_id=plan_id,
                    name=form.name,
                    position=position,
                    allocation_percent=form.allocation_percent,
                    enabled=form.enabled,
                    is_control=form.is_control,
                    created_at=now,
                )
            )
            prompt = form.prompt_injection
            memory = form.memory_injection
            db.add(
                ExperimentPromptInjection(
                    condition_id=condition_id,
                    enabled=prompt.enabled,
                    instruction=prompt.instruction,
                    position=prompt.position.value,
                    activation_mode=prompt.activation.mode.value,
                    activation_count=prompt.activation.count,
                    range_start=prompt.activation.range_start,
                    range_end=prompt.activation.range_end,
                    probability=prompt.activation.probability,
                    scope=prompt.activation.scope.value,
                )
            )
            db.add(
                ExperimentMemoryInjection(
                    condition_id=condition_id,
                    enabled=memory.enabled,
                    content=memory.content,
                    persist_for_session=memory.persist_for_session,
                    activation_mode=memory.activation.mode.value,
                    activation_count=memory.activation.count,
                    range_start=memory.activation.range_start,
                    range_end=memory.activation.range_end,
                    probability=memory.activation.probability,
                    scope=memory.activation.scope.value,
                )
            )
            warning = form.warning_modal
            db.add(
                ExperimentWarningModal(
                    condition_id=condition_id,
                    enabled=warning.enabled,
                    title=warning.title,
                    message=warning.message,
                    confirmation_text=warning.confirmation_text,
                    must_acknowledge=warning.must_acknowledge,
                    cadence=warning.cadence.value,
                    cadence_value=warning.cadence_value,
                    prompt_numbers=warning.prompt_numbers,
                )
            )
            timing = form.response_timing
            db.add(
                ExperimentResponseTiming(
                    condition_id=condition_id,
                    mode=timing.mode.value,
                    delay_seconds=timing.delay_seconds,
                    show_loading=timing.show_loading,
                    reveal_style=timing.reveal_style.value,
                    target_duration_seconds=timing.target_duration_seconds,
                    stream_unit=timing.stream_unit.value,
                    minimum_chunk_size=timing.minimum_chunk_size,
                    maximum_chunk_size=timing.maximum_chunk_size,
                    punctuation_pauses=timing.punctuation_pauses,
                    rate_value=timing.rate_value,
                    rate_unit=timing.rate_unit.value,
                )
            )
            for perturbation_type, ids in (
                ('PROMPT', prompt.activation.plan_item_ids),
                ('MEMORY', memory.activation.plan_item_ids),
            ):
                for plan_item_id in ids:
                    mapped_id = item_map.get(plan_item_id, plan_item_id)
                    if mapped_id in saved_item_ids:
                        db.add(
                            ExperimentConditionTaskScope(
                                condition_id=condition_id,
                                perturbation_type=perturbation_type,
                                plan_item_id=mapped_id,
                            )
                        )

    async def _delete_items(self, plan_id: str, db: AsyncSession):
        ids = list(
            (await db.execute(select(ExperimentPlanItem.id).where(ExperimentPlanItem.plan_id == plan_id)))
            .scalars()
            .all()
        )
        if ids:
            await db.execute(
                delete(ExperimentConditionTaskScope).where(ExperimentConditionTaskScope.plan_item_id.in_(ids))
            )
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
