import time
import uuid
from enum import StrEnum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, Index, Text, UniqueConstraint, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.models.essays import Essay, EssayModel, EssayTopicModel, EssayTopics, resolve_topic_for_group
from open_webui.models.groups import Groups


class ExperimentState(StrEnum):
    NOT_APPLICABLE = 'NOT_APPLICABLE'
    CONSENT_REQUIRED = 'CONSENT_REQUIRED'
    PRE_SURVEY_REQUIRED = 'PRE_SURVEY_REQUIRED'
    TOPIC_REQUIRED = 'TOPIC_REQUIRED'
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
    topic_id = Column(Text, nullable=False)
    topic_title = Column(Text, nullable=False)
    topic_question = Column(Text, nullable=False)
    essay_id = Column(Text, nullable=True)
    state = Column(Text, nullable=False)
    pre_survey = Column(JSONField, nullable=True)
    post_survey = Column(JSONField, nullable=True)
    consented_at = Column(BigInteger, nullable=True)
    topic_shown_at = Column(BigInteger, nullable=True)
    writing_started_at = Column(BigInteger, nullable=True)
    essay_submitted_at = Column(BigInteger, nullable=True)
    post_survey_submitted_at = Column(BigInteger, nullable=True)
    completed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'group_id', name='uq_experiment_session_user_group'),
        Index('ix_experiment_session_user_state', 'user_id', 'state'),
    )


class ExperimentSessionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    group_id: str
    topic_id: str
    topic_title: str
    topic_question: str
    essay_id: Optional[str] = None
    state: ExperimentState
    consented_at: Optional[int] = None
    topic_shown_at: Optional[int] = None
    writing_started_at: Optional[int] = None
    essay_submitted_at: Optional[int] = None
    post_survey_submitted_at: Optional[int] = None
    completed_at: Optional[int] = None
    created_at: int
    updated_at: int


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


PRE_FREQUENCIES = {'Never', 'Rarely', 'Sometimes', 'Often', 'Very often'}
AGE_RANGES = {'Under 13', '13–14', '15–16', '17–18', 'Over 18', 'Prefer not to say'}
AI_IMPROVEMENTS = {'Not at all', 'A little', 'A moderate amount', 'A lot', 'A great deal'}
ACTIVE_STATES = {
    ExperimentState.CONSENT_REQUIRED,
    ExperimentState.PRE_SURVEY_REQUIRED,
    ExperimentState.TOPIC_REQUIRED,
    ExperimentState.IN_PROGRESS,
    ExperimentState.POST_SURVEY_REQUIRED,
    ExperimentState.THANK_YOU_REQUIRED,
}


class ExperimentTable:
    @staticmethod
    def topic_from_session(session: ExperimentSessionModel) -> EssayTopicModel:
        return EssayTopicModel(
            id=session.topic_id,
            title=session.topic_title,
            question=session.topic_question,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    async def _sessions_for_user(self, user_id: str, db: AsyncSession) -> list[ExperimentSession]:
        result = await db.execute(
            select(ExperimentSession)
            .filter_by(user_id=user_id)
            .order_by(ExperimentSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_current(self, user, db: Optional[AsyncSession] = None) -> tuple[ExperimentState, Optional[ExperimentSessionModel], Optional[str]]:
        if user.role == 'admin':
            return ExperimentState.NOT_APPLICABLE, None, None

        async with get_async_db_context(db) as db:
            groups = await Groups.get_groups_by_member_id(user.id, db=db)
            enabled = [
                group for group in groups if (group.data or {}).get('config', {}).get('experiment_mode_enabled', False)
            ]
            if len(enabled) > 1:
                return ExperimentState.CONFIGURATION_ERROR, None, 'You belong to multiple Experiment Mode groups. Please contact an administrator.'

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
            if config.get('essay_topic_mode', 'random') == 'specific':
                topic_id = config.get('essay_topic_id')
                if not topic_id or await EssayTopics.get_topic_by_id(topic_id, db=db) is None:
                    return ExperimentState.CONFIGURATION_ERROR, None, 'The configured essay topic is unavailable.'

            topic = await resolve_topic_for_group(user.id, group, db=db)
            if topic is None:
                return ExperimentState.CONFIGURATION_ERROR, None, 'No essay topic is available for this experiment.'

            now = int(time.time_ns())
            session = ExperimentSession(
                id=str(uuid.uuid4()),
                user_id=user.id,
                group_id=group.id,
                topic_id=topic.id,
                topic_title=topic.title,
                topic_question=topic.question,
                state=ExperimentState.CONSENT_REQUIRED.value,
                created_at=now,
                updated_at=now,
            )
            db.add(session)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                result = await db.execute(
                    select(ExperimentSession).filter_by(user_id=user.id, group_id=group.id)
                )
                session = result.scalars().first()
                if session is None:
                    raise
            await db.refresh(session)
            return ExperimentState(session.state), ExperimentSessionModel.model_validate(session), None

    async def _transition(self, user, expected: ExperimentState, target: ExperimentState, values: dict, db: AsyncSession):
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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Experiment state changed. Refresh and try again.')
        await db.commit()
        result = await db.execute(select(ExperimentSession).filter_by(id=session_model.id))
        session = result.scalars().first()
        return ExperimentSessionModel.model_validate(session)

    async def consent(self, user, db: AsyncSession):
        return await self._transition(
            user, ExperimentState.CONSENT_REQUIRED, ExperimentState.PRE_SURVEY_REQUIRED,
            {'consented_at': int(time.time_ns())}, db,
        )

    async def submit_pre_survey(self, user, form: PreSurveyForm, db: AsyncSession):
        if form.ai_schoolwork_frequency not in PRE_FREQUENCIES or form.age_range not in AGE_RANGES:
            raise HTTPException(status_code=422, detail='Invalid pre-survey response.')
        data = form.model_dump()
        data['school_class'] = data['school_class'].strip()
        if not data['school_class']:
            raise HTTPException(status_code=422, detail='School class is required.')
        return await self._transition(
            user, ExperimentState.PRE_SURVEY_REQUIRED, ExperimentState.TOPIC_REQUIRED,
            {'pre_survey': data}, db,
        )

    async def start(self, user, db: AsyncSession):
        now = int(time.time_ns())
        return await self._transition(
            user, ExperimentState.TOPIC_REQUIRED, ExperimentState.IN_PROGRESS,
            {'topic_shown_at': now, 'writing_started_at': now}, db,
        )

    async def submit_essay(self, user, content: str, db: AsyncSession) -> EssayModel:
        current, session_model, _ = await self.get_current(user, db=db)
        if current != ExperimentState.IN_PROGRESS or session_model is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Experiment writing session is not active.')
        now = int(time.time_ns())
        essay = Essay(
            id=str(uuid.uuid4()),
            user_id=user.id,
            content=content,
            topic_id=session_model.topic_id,
            topic_title=session_model.topic_title,
            topic_question=session_model.topic_question,
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

    async def submit_post_survey(self, user, form: PostSurveyForm, db: AsyncSession):
        if form.ai_improvement not in AI_IMPROVEMENTS:
            raise HTTPException(status_code=422, detail='Invalid post-survey response.')
        data = form.model_dump()
        if data['comments'] is not None:
            data['comments'] = data['comments'].strip()
        return await self._transition(
            user, ExperimentState.POST_SURVEY_REQUIRED, ExperimentState.THANK_YOU_REQUIRED,
            {'post_survey': data, 'post_survey_submitted_at': int(time.time_ns())}, db,
        )

    async def complete(self, user, db: AsyncSession):
        return await self._transition(
            user, ExperimentState.THANK_YOU_REQUIRED, ExperimentState.COMPLETED,
            {'completed_at': int(time.time_ns())}, db,
        )


Experiments = ExperimentTable()


async def require_experiment_chat_access(user, db: Optional[AsyncSession] = None):
    state, _, _ = await Experiments.get_current(user, db=db)
    if state not in {ExperimentState.NOT_APPLICABLE, ExperimentState.IN_PROGRESS}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'Experiment step required: {state.value}')
