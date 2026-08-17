import time
import uuid
from enum import StrEnum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, Float, Index, Text, UniqueConstraint, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.models.essays import Essay, EssayModel, EssayTopicModel, EssayTopics, resolve_topic_for_group
from open_webui.models.experiment_plans import (
    ExperimentPlan,
    ExperimentPlans,
    ExperimentSessionTask,
    ExperimentTaskType,
    ProgressionMode,
    SessionTaskStatus,
)
from open_webui.models.groups import Groups
from open_webui.utils.essay_text import essay_text_metrics


class ExperimentState(StrEnum):
    NOT_APPLICABLE = 'NOT_APPLICABLE'
    CONSENT_REQUIRED = 'CONSENT_REQUIRED'
    PRE_SURVEY_REQUIRED = 'PRE_SURVEY_REQUIRED'
    TOPIC_REQUIRED = 'TOPIC_REQUIRED'
    TASK_REQUIRED = 'TASK_REQUIRED'
    IN_PROGRESS = 'IN_PROGRESS'
    POST_SURVEY_REQUIRED = 'POST_SURVEY_REQUIRED'
    THANK_YOU_REQUIRED = 'THANK_YOU_REQUIRED'
    COMPLETED = 'COMPLETED'
    CONFIGURATION_ERROR = 'CONFIGURATION_ERROR'


class ExperimentSession(Base):
    __tablename__ = 'experiment_session'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    group_id = Column(Text, nullable=False)
    topic_id = Column(Text, nullable=True)
    topic_title = Column(Text, nullable=True)
    topic_question = Column(Text, nullable=True)
    essay_id = Column(Text, nullable=True)
    state = Column(Text, nullable=False)
    pre_survey = Column(JSONField, nullable=True)
    post_survey = Column(JSONField, nullable=True)
    consented_at = Column(BigInteger, nullable=True)
    pre_survey_submitted_at = Column(BigInteger, nullable=True)
    topic_shown_at = Column(BigInteger, nullable=True)
    writing_started_at = Column(BigInteger, nullable=True)
    essay_submitted_at = Column(BigInteger, nullable=True)
    post_survey_submitted_at = Column(BigInteger, nullable=True)
    completed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    plan_id = Column(Text, nullable=True)
    task_type = Column(Text, nullable=False, default=ExperimentTaskType.ESSAY.value)
    task_submitted_at = Column(BigInteger, nullable=True)
    condition_id = Column(Text, nullable=True)
    condition_assignment_id = Column(Text, nullable=True)
    condition_assignment_draw = Column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_experiment_session_user_group'),
        Index('ix_experiment_session_user_state', 'user_id', 'state'),
        Index('ix_experiment_session_group_created', 'group_id', 'created_at'),
        Index('ix_experiment_session_topic_created', 'topic_id', 'created_at'),
        Index('ix_experiment_session_state_created', 'state', 'created_at'),
        Index('ix_experiment_session_condition', 'condition_id'),
    )


class ExperimentSessionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    group_id: str
    topic_id: Optional[str] = None
    topic_title: Optional[str] = None
    topic_question: Optional[str] = None
    essay_id: Optional[str] = None
    state: ExperimentState
    consented_at: Optional[int] = None
    pre_survey_submitted_at: Optional[int] = None
    topic_shown_at: Optional[int] = None
    writing_started_at: Optional[int] = None
    essay_submitted_at: Optional[int] = None
    post_survey_submitted_at: Optional[int] = None
    completed_at: Optional[int] = None
    created_at: int
    updated_at: int
    plan_id: Optional[str] = None
    task_type: ExperimentTaskType = ExperimentTaskType.ESSAY
    condition_id: Optional[str] = None
    condition_assignment_id: Optional[str] = None
    condition_assignment_draw: Optional[float] = None


class PreSurveyForm(BaseModel):
    school_class: str = Field(min_length=1, max_length=200)
    ai_familiarity: int = Field(ge=1, le=5)
    ai_schoolwork_frequency: str
    essay_writing_confidence: int = Field(ge=1, le=5)
    age_range: str


class PostSurveyForm(BaseModel):
    ai_helpfulness: int = Field(ge=1, le=5)
    essay_satisfaction: int = Field(ge=1, le=5)
    ai_improvement: str
    chat_ease: int = Field(ge=1, le=5)
    comments: Optional[str] = Field(default=None, max_length=4000)


class TaskNeutralPreSurveyForm(BaseModel):
    school_class: str = Field(min_length=1, max_length=200)
    ai_familiarity: int = Field(ge=1, le=5)
    ai_schoolwork_frequency: str
    task_confidence: int = Field(ge=1, le=5)
    age_range: str


class TaskNeutralPostSurveyForm(BaseModel):
    ai_helpfulness: int = Field(ge=1, le=5)
    task_satisfaction: int = Field(ge=1, le=5)
    ai_improvement: str
    chat_ease: int = Field(ge=1, le=5)
    comments: Optional[str] = Field(default=None, max_length=4000)


PRE_FREQUENCIES = {'Never', 'Rarely', 'Sometimes', 'Often', 'Very often'}
AGE_RANGES = {'Under 13', '13–14', '15–16', '17–18', 'Over 18', 'Prefer not to say'}
AI_IMPROVEMENTS = {'Not at all', 'A little', 'A moderate amount', 'A lot', 'A great deal'}
ACTIVE_STATES = {
    ExperimentState.CONSENT_REQUIRED,
    ExperimentState.PRE_SURVEY_REQUIRED,
    ExperimentState.TOPIC_REQUIRED,
    ExperimentState.TASK_REQUIRED,
    ExperimentState.IN_PROGRESS,
    ExperimentState.POST_SURVEY_REQUIRED,
    ExperimentState.THANK_YOU_REQUIRED,
}


class ExperimentTable:
    @staticmethod
    def topic_from_session(session: ExperimentSessionModel) -> EssayTopicModel:
        if not session.topic_id or not session.topic_title or not session.topic_question:
            raise ValueError('Experiment session does not contain an essay topic.')
        return EssayTopicModel(
            id=session.topic_id,
            title=session.topic_title,
            question=session.topic_question,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    async def _sessions_for_user(self, user_id: str, db: AsyncSession) -> list[ExperimentSession]:
        result = await db.execute(
            select(ExperimentSession).filter_by(user_id=user_id).order_by(ExperimentSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_current(
        self, user, db: Optional[AsyncSession] = None
    ) -> tuple[ExperimentState, Optional[ExperimentSessionModel], Optional[str]]:
        if user.role == 'admin':
            return ExperimentState.NOT_APPLICABLE, None, None

        async with get_async_db_context(db) as db:
            groups = await Groups.get_groups_by_member_id(user.id, db=db)
            enabled = [
                group for group in groups if (group.data or {}).get('config', {}).get('experiment_mode_enabled', False)
            ]
            if len(enabled) > 1:
                return (
                    ExperimentState.CONFIGURATION_ERROR,
                    None,
                    'You belong to multiple Experiment Mode groups. Please contact an administrator.',
                )

            sessions = await self._sessions_for_user(user.id, db)
            active = [session for session in sessions if ExperimentState(session.state) in ACTIVE_STATES]
            if len(active) > 1:
                return ExperimentState.CONFIGURATION_ERROR, None, 'Multiple active experiment sessions were found.'
            if active:
                session = active[0]
                return ExperimentState(session.state), ExperimentSessionModel.model_validate(session), None

            if not enabled:
                return ExperimentState.NOT_APPLICABLE, None, None

            group = enabled[0]
            existing = next((session for session in sessions if session.group_id == group.id), None)
            if existing:
                return ExperimentState(existing.state), ExperimentSessionModel.model_validate(existing), None

            if not (group.permissions or {}).get('features', {}).get('essay_sidebar', False):
                return ExperimentState.CONFIGURATION_ERROR, None, 'Experiment Mode requires Essay Sidebar access.'
            config = (group.data or {}).get('config', {})
            plan = await ExperimentPlans.get_active_for_group(group.id, db=db)
            topic = None
            if plan is None:
                if config.get('essay_topic_mode', 'random') == 'specific':
                    topic_id = config.get('essay_topic_id')
                    if not topic_id or await EssayTopics.get_topic_by_id(topic_id, db=db) is None:
                        return ExperimentState.CONFIGURATION_ERROR, None, 'The configured essay topic is unavailable.'
                topic = await resolve_topic_for_group(user.id, group, db=db)
                if topic is None:
                    return ExperimentState.CONFIGURATION_ERROR, None, 'No essay topic is available for this experiment.'

            now = int(time.time_ns())
            condition_id = assignment_id = assignment_draw = None
            if plan:
                from open_webui.utils.experiment_perturbations import assign_condition

                condition_id, assignment_id, assignment_draw = assign_condition(plan, user.id)
            session = ExperimentSession(
                id=str(uuid.uuid4()),
                user_id=user.id,
                group_id=group.id,
                topic_id=topic.id if topic else None,
                topic_title=topic.title if topic else None,
                topic_question=topic.question if topic else None,
                plan_id=plan.id if plan else None,
                task_type=(plan.items[0].task_type.value if plan else ExperimentTaskType.ESSAY.value),
                condition_id=condition_id,
                condition_assignment_id=assignment_id,
                condition_assignment_draw=assignment_draw,
                state=(
                    ExperimentState.CONSENT_REQUIRED.value
                    if not plan or plan.consent_enabled
                    else ExperimentState.TASK_REQUIRED.value
                ),
                created_at=now,
                updated_at=now,
            )
            db.add(session)
            try:
                await db.flush()
                if plan:
                    tasks = await ExperimentPlans.instantiate_session_tasks(session.id, plan.id, db)
                    first = tasks[0]
                    session.task_type = first.task_type.value
                    if first.task_type == ExperimentTaskType.ESSAY:
                        session.topic_id = first.essay_topic_id
                        session.topic_title = first.essay_topic_title
                        session.topic_question = first.essay_topic_question
                await db.commit()
            except Exception:
                await db.rollback()
                result = await db.execute(select(ExperimentSession).filter_by(user_id=user.id, group_id=group.id))
                session = result.scalars().first()
                if session is None:
                    raise
            await db.refresh(session)
            return ExperimentState(session.state), ExperimentSessionModel.model_validate(session), None

    async def _transition(
        self, user, expected: ExperimentState, target: ExperimentState, values: dict, db: AsyncSession
    ):
        current, session_model, _ = await self.get_current(user, db=db)
        if current != expected or session_model is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Experiment state must be {expected.value}. Current state is {current.value}.',
            )
        result = await db.execute(
            update(ExperimentSession)
            .where(ExperimentSession.id == session_model.id, ExperimentSession.state == expected.value)
            .values(**values, state=target.value, updated_at=int(time.time_ns()))
        )
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='Experiment state changed. Refresh and try again.'
            )
        await db.commit()
        result = await db.execute(select(ExperimentSession).filter_by(id=session_model.id))
        session = result.scalars().first()
        return ExperimentSessionModel.model_validate(session)

    async def consent(self, user, db: AsyncSession):
        _, session, _ = await self.get_current(user, db=db)
        plan = await ExperimentPlans.get_plan(session.plan_id, db=db) if session and session.plan_id else None
        target = (
            ExperimentState.TASK_REQUIRED
            if plan
            and (plan.status != 'SUPERSEDED' or any(item.task_type == ExperimentTaskType.SURVEY for item in plan.items))
            else ExperimentState.PRE_SURVEY_REQUIRED
        )
        return await self._transition(
            user,
            ExperimentState.CONSENT_REQUIRED,
            target,
            {'consented_at': int(time.time_ns())},
            db,
        )

    async def submit_pre_survey(self, user, form: PreSurveyForm | TaskNeutralPreSurveyForm, db: AsyncSession):
        if form.ai_schoolwork_frequency not in PRE_FREQUENCIES or form.age_range not in AGE_RANGES:
            raise HTTPException(status_code=422, detail='Invalid pre-survey response.')
        data = form.model_dump()
        data['survey_version'] = 'task-neutral-v1' if isinstance(form, TaskNeutralPreSurveyForm) else 'essay-v1'
        data['school_class'] = data['school_class'].strip()
        if not data['school_class']:
            raise HTTPException(status_code=422, detail='School class is required.')
        current, session, _ = await self.get_current(user, db=db)
        if current != ExperimentState.PRE_SURVEY_REQUIRED or session is None:
            raise HTTPException(status_code=409, detail='Experiment pre-survey is not available.')
        target = ExperimentState.TOPIC_REQUIRED
        if session.plan_id:
            plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
            if plan and plan.survey_variant == 'TASK_NEUTRAL':
                target = ExperimentState.TASK_REQUIRED
                if not isinstance(form, TaskNeutralPreSurveyForm):
                    raise HTTPException(status_code=422, detail='This experiment requires the task-neutral pre-survey.')
            elif isinstance(form, TaskNeutralPreSurveyForm):
                raise HTTPException(status_code=422, detail='This experiment requires the essay pre-survey.')
        return await self._transition(
            user,
            ExperimentState.PRE_SURVEY_REQUIRED,
            target,
            {'pre_survey': data, 'pre_survey_submitted_at': int(time.time_ns())},
            db,
        )

    async def start(self, user, db: AsyncSession):
        now = int(time.time_ns())
        current, session, _ = await self.get_current(user, db=db)
        if current not in {ExperimentState.TOPIC_REQUIRED, ExperimentState.TASK_REQUIRED} or session is None:
            raise HTTPException(status_code=409, detail='Experiment task is not ready to start.')
        if session.plan_id:
            plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
            rows = list(
                (
                    await db.execute(
                        select(ExperimentSessionTask)
                        .where(ExperimentSessionTask.experiment_session_id == session.id)
                        .order_by(ExperimentSessionTask.position)
                    )
                )
                .scalars()
                .all()
            )
            await self._activate_initial(plan, rows, now)
        return await self._transition(
            user, current, ExperimentState.IN_PROGRESS, {'topic_shown_at': now, 'writing_started_at': now}, db
        )

    async def submit_essay(self, user, content: str, db: AsyncSession) -> EssayModel:
        current, session_model, _ = await self.get_current(user, db=db)
        if current != ExperimentState.IN_PROGRESS or session_model is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='Experiment writing session is not active.'
            )
        if session_model.plan_id:
            plan = await ExperimentPlans.get_plan(session_model.plan_id, db=db)
            tasks = list(
                (
                    await db.execute(
                        select(ExperimentSessionTask)
                        .where(ExperimentSessionTask.experiment_session_id == session_model.id)
                        .order_by(ExperimentSessionTask.position)
                    )
                )
                .scalars()
                .all()
            )
            active = next((task for task in tasks if task.status == SessionTaskStatus.ACTIVE.value), None)
            if (
                len(tasks) != 1
                or not active
                or active.task_type != ExperimentTaskType.ESSAY.value
                or plan.progression_mode != ProgressionMode.STRICT_SEQUENTIAL
            ):
                raise HTTPException(status_code=409, detail='Use the ordered experiment task submission endpoint.')
            return await self.finalize_essay_task(user, active, content, db)
        now = int(time.time_ns())
        word_count, character_count = essay_text_metrics(content)
        essay = Essay(
            id=str(uuid.uuid4()),
            user_id=user.id,
            content=content,
            topic_id=session_model.topic_id,
            topic_title=session_model.topic_title,
            topic_question=session_model.topic_question,
            word_count=word_count,
            character_count=character_count,
            created_at=now,
            updated_at=now,
        )
        result = await db.execute(
            update(ExperimentSession)
            .where(
                ExperimentSession.id == session_model.id,
                ExperimentSession.state == ExperimentState.IN_PROGRESS.value,
            )
            .values(
                essay_id=essay.id,
                essay_submitted_at=now,
                state=ExperimentState.POST_SURVEY_REQUIRED.value,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='An essay has already been submitted.')
        db.add(essay)
        await db.commit()
        await db.refresh(essay)
        return EssayModel.model_validate(essay)

    async def finalize_essay_task(self, user, session_task: ExperimentSessionTask, content: str, db: AsyncSession):
        session = await db.get(ExperimentSession, session_task.experiment_session_id)
        if not session or session.user_id != user.id or session.state != ExperimentState.IN_PROGRESS.value:
            raise HTTPException(status_code=403, detail='Experiment task is not available.')
        if session_task.status == SessionTaskStatus.FINALIZED.value and session_task.essay_id:
            essay = await db.get(Essay, session_task.essay_id)
            return EssayModel.model_validate(essay)
        if session_task.status not in {
            SessionTaskStatus.ACTIVE.value,
            SessionTaskStatus.AVAILABLE.value,
            SessionTaskStatus.COMPLETED.value,
        }:
            raise HTTPException(status_code=409, detail='Essay Task is locked or finalized.')
        now = int(time.time_ns())
        word_count, character_count = essay_text_metrics(content)
        essay = Essay(
            id=str(uuid.uuid4()),
            user_id=user.id,
            content=content,
            topic_id=session_task.essay_topic_id,
            topic_title=session_task.essay_topic_title,
            topic_question=session_task.essay_topic_question,
            word_count=word_count,
            character_count=character_count,
            experiment_session_task_id=session_task.id,
            created_at=now,
            updated_at=now,
        )
        db.add(essay)
        await db.flush()
        session_task.essay_id = essay.id
        session_task.essay_draft = content
        session_task.status = SessionTaskStatus.FINALIZED.value
        session_task.completed_at = now
        session_task.finalized_at = now
        session_task.updated_at = now
        if not session.essay_id:
            session.essay_id = essay.id
            session.essay_submitted_at = now
        await self.advance_after_stage(session, session_task, db)
        await db.commit()
        await db.refresh(essay)
        return EssayModel.model_validate(essay)

    @staticmethod
    def _done(row: ExperimentSessionTask):
        return row.status in {SessionTaskStatus.FINALIZED.value, SessionTaskStatus.SKIPPED.value}

    async def _activate_initial(self, plan, rows: list[ExperimentSessionTask], now: int):
        for row in rows:
            row.status = SessionTaskStatus.LOCKED.value
            row.started_at = None
            row.updated_at = now
        if not rows:
            return
        if rows[0].task_type == ExperimentTaskType.SURVEY.value:
            rows[0].status = SessionTaskStatus.ACTIVE.value
            rows[0].started_at = now
            return
        await self._activate_task_block(plan, rows, 0, now)

    async def _activate_task_block(self, plan, rows: list[ExperimentSessionTask], start: int, now: int):
        block = []
        for row in rows[start:]:
            if row.task_type == ExperimentTaskType.SURVEY.value:
                break
            if not self._done(row):
                block.append(row)
        if not block:
            return
        if plan.progression_mode == ProgressionMode.FREE_NAVIGATION:
            for row in block:
                row.status = SessionTaskStatus.AVAILABLE.value
                row.started_at = row.started_at or now
                row.updated_at = now
        else:
            block[0].status = SessionTaskStatus.ACTIVE.value
            block[0].started_at = block[0].started_at or now
            block[0].updated_at = now

    async def advance_after_stage(
        self,
        session: ExperimentSession,
        completed: ExperimentSessionTask,
        db: AsyncSession,
        task_block_finalized: bool = False,
    ):
        rows = list(
            (
                await db.execute(
                    select(ExperimentSessionTask)
                    .where(ExperimentSessionTask.experiment_session_id == session.id)
                    .order_by(ExperimentSessionTask.position)
                )
            )
            .scalars()
            .all()
        )
        plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
        now = int(time.time_ns())
        unfinished = [row for row in rows if not self._done(row)]
        if not unfinished:
            legacy_plan = plan.status == 'SUPERSEDED' and not any(
                row.task_type == ExperimentTaskType.SURVEY.value for row in rows
            )
            session.state = (
                ExperimentState.POST_SURVEY_REQUIRED.value if legacy_plan else ExperimentState.THANK_YOU_REQUIRED.value
            )
            session.task_submitted_at = session.task_submitted_at or now
            session.updated_at = now
            return
        if plan.progression_mode == ProgressionMode.STRICT_SEQUENTIAL:
            next_row = next((row for row in rows if row.position > completed.position and not self._done(row)), None)
            if next_row:
                next_row.status = SessionTaskStatus.ACTIVE.value
                next_row.started_at = next_row.started_at or now
                next_row.updated_at = now
            return
        if completed.task_type == ExperimentTaskType.SURVEY.value:
            next_row = next((row for row in rows if row.position > completed.position and not self._done(row)), None)
            if next_row and next_row.task_type == ExperimentTaskType.SURVEY.value:
                next_row.status = SessionTaskStatus.ACTIVE.value
                next_row.started_at = next_row.started_at or now
                next_row.updated_at = now
            elif next_row:
                await self._activate_task_block(plan, rows, rows.index(next_row), now)
            return
        if task_block_finalized:
            next_survey = next(
                (
                    row
                    for row in rows
                    if row.task_type == ExperimentTaskType.SURVEY.value
                    and row.position > completed.position
                    and not self._done(row)
                ),
                None,
            )
            if next_survey:
                next_survey.status = SessionTaskStatus.ACTIVE.value
                next_survey.started_at = next_survey.started_at or now
                next_survey.updated_at = now
            else:
                session.state = ExperimentState.THANK_YOU_REQUIRED.value
                session.task_submitted_at = session.task_submitted_at or now
                session.updated_at = now

    # Compatibility alias for older route callers.
    async def _advance_after_task(self, session, completed_task, db):
        return await self.advance_after_stage(session, completed_task, db)

    async def submit_post_survey(self, user, form: PostSurveyForm | TaskNeutralPostSurveyForm, db: AsyncSession):
        if form.ai_improvement not in AI_IMPROVEMENTS:
            raise HTTPException(status_code=422, detail='Invalid post-survey response.')
        data = form.model_dump()
        data['survey_version'] = 'task-neutral-v1' if isinstance(form, TaskNeutralPostSurveyForm) else 'essay-v1'
        if data['comments'] is not None:
            data['comments'] = data['comments'].strip()
        current, session, _ = await self.get_current(user, db=db)
        if current != ExperimentState.POST_SURVEY_REQUIRED or session is None:
            raise HTTPException(status_code=409, detail='Experiment post-survey is not available.')
        if session.plan_id:
            plan = await ExperimentPlans.get_plan(session.plan_id, db=db)
            neutral = bool(plan and plan.survey_variant == 'TASK_NEUTRAL')
            if neutral != isinstance(form, TaskNeutralPostSurveyForm):
                raise HTTPException(status_code=422, detail='The submitted survey does not match this experiment.')
        return await self._transition(
            user,
            ExperimentState.POST_SURVEY_REQUIRED,
            ExperimentState.THANK_YOU_REQUIRED,
            {'post_survey': data, 'post_survey_submitted_at': int(time.time_ns())},
            db,
        )

    async def complete(self, user, db: AsyncSession):
        return await self._transition(
            user,
            ExperimentState.THANK_YOU_REQUIRED,
            ExperimentState.COMPLETED,
            {'completed_at': int(time.time_ns())},
            db,
        )


Experiments = ExperimentTable()


async def require_experiment_chat_access(user, db: Optional[AsyncSession] = None):
    if user.role == 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Administrators can only access the Admin Panel.',
        )
    state, session, _ = await Experiments.get_current(user, db=db)
    if state not in {ExperimentState.NOT_APPLICABLE, ExperimentState.IN_PROGRESS}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Experiment step required: {state.value}')
    if state == ExperimentState.IN_PROGRESS and session and session.plan_id:
        async with get_async_db_context(db) as active_db:
            active_survey = (
                (
                    await active_db.execute(
                        select(ExperimentSessionTask.id).where(
                            ExperimentSessionTask.experiment_session_id == session.id,
                            ExperimentSessionTask.task_type == ExperimentTaskType.SURVEY.value,
                            ExperimentSessionTask.status == SessionTaskStatus.ACTIVE.value,
                        )
                    )
                )
                .scalars()
                .first()
            )
        if active_survey:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'code': 'EXPERIMENT_SURVEY_ACTIVE',
                    'message': 'Complete or skip the active survey before using chat.',
                },
            )
    return state
