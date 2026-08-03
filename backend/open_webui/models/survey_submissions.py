import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.survey_tasks import SurveyChoice, SurveyQuestion, SurveyQuestionType, SurveyTasks


@asynccontextmanager
async def _submission_db_context(db: Optional[AsyncSession] = None):
    """Always reuse an explicitly supplied session for atomic submission transitions."""
    if db is not None:
        yield db
    else:
        async with get_async_db_context() as owned_db:
            yield owned_db


class SurveySubmission(Base):
    __tablename__ = 'survey_submission'

    id = Column(Text, primary_key=True)
    session_task_id = Column(Text, ForeignKey('experiment_session_task.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='DRAFT')
    submitted_at = Column(BigInteger, nullable=True)
    skipped_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SurveyResponse(Base):
    __tablename__ = 'survey_response'

    id = Column(Text, primary_key=True)
    submission_id = Column(Text, ForeignKey('survey_submission.id', ondelete='CASCADE'), nullable=False)
    question_id = Column(Text, ForeignKey('survey_question.id', ondelete='RESTRICT'), nullable=False)
    text_answer = Column(Text, nullable=True)
    scale_answer = Column(Integer, nullable=True)
    is_answered = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class SurveyResponseChoice(Base):
    __tablename__ = 'survey_response_choice'

    response_id = Column(Text, ForeignKey('survey_response.id', ondelete='CASCADE'), primary_key=True)
    choice_id = Column(Text, ForeignKey('survey_choice.id', ondelete='RESTRICT'), primary_key=True)


class SurveyAnswerForm(BaseModel):
    question_id: str
    choice_ids: list[str] = Field(default_factory=list, max_length=100)
    text: Optional[str] = Field(default=None, max_length=4000)
    scale: Optional[int] = Field(default=None, ge=1, le=5)

    @model_validator(mode='after')
    def unique_choices(self):
        if len(self.choice_ids) != len(set(self.choice_ids)):
            raise ValueError('Choice IDs must be unique.')
        return self


class SurveyDraftForm(BaseModel):
    answers: list[SurveyAnswerForm] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def unique_questions(self):
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError('Question IDs must be unique.')
        return self


class ParticipantSurveyResponseModel(BaseModel):
    question_id: str
    choice_ids: list[str] = []
    text: Optional[str] = None
    scale: Optional[int] = None


class ParticipantSurveySubmissionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_task_id: str
    status: str
    submitted_at: Optional[int] = None
    skipped_at: Optional[int] = None
    responses: list[ParticipantSurveyResponseModel] = []


class SurveySubmissionTable:
    async def for_session_task(self, session_task_id: str, user_id: str, db: Optional[AsyncSession] = None):
        async with _submission_db_context(db) as db:
            submission = (
                (await db.execute(select(SurveySubmission).where(SurveySubmission.session_task_id == session_task_id)))
                .scalars()
                .first()
            )
            if not submission or submission.user_id != user_id:
                return None
            return await self.participant_model(submission.id, user_id, db=db)

    async def get_or_create(self, session_task: ExperimentSessionTask, user_id: str, db: AsyncSession):
        submission = (
            (await db.execute(select(SurveySubmission).where(SurveySubmission.session_task_id == session_task.id)))
            .scalars()
            .first()
        )
        if submission:
            if submission.user_id != user_id:
                raise HTTPException(status_code=403, detail='Survey submission is not available.')
            return submission
        now = int(time.time_ns())
        submission = SurveySubmission(
            id=str(uuid.uuid4()),
            session_task_id=session_task.id,
            user_id=user_id,
            status='DRAFT',
            created_at=now,
            updated_at=now,
        )
        db.add(submission)
        await db.flush()
        task = await SurveyTasks.get_task(session_task.survey_task_id, db=db)
        for question in task.questions:
            if question.enabled:
                db.add(
                    SurveyResponse(
                        id=str(uuid.uuid4()),
                        submission_id=submission.id,
                        question_id=question.id,
                        is_answered=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
        await db.flush()
        return submission

    async def save_draft(
        self,
        session_task: ExperimentSessionTask,
        user_id: str,
        form: SurveyDraftForm,
        db: AsyncSession,
        *,
        commit: bool = True,
    ):
        submission = await self.get_or_create(session_task, user_id, db)
        if submission.status != 'DRAFT':
            raise HTTPException(status_code=409, detail='Survey is already finalized.')
        task = await SurveyTasks.get_task(session_task.survey_task_id, db=db)
        questions = {question.id: question for question in task.questions if question.enabled}
        supplied = {answer.question_id: answer for answer in form.answers}
        if not set(supplied).issubset(questions):
            raise HTTPException(status_code=422, detail='Survey contains an unknown or disabled question.')
        rows = list(
            (await db.execute(select(SurveyResponse).where(SurveyResponse.submission_id == submission.id)))
            .scalars()
            .all()
        )
        row_map = {row.question_id: row for row in rows}
        now = int(time.time_ns())
        for question_id, question in questions.items():
            answer = supplied.get(question_id, SurveyAnswerForm(question_id=question_id))
            row = row_map[question_id]
            await db.execute(delete(SurveyResponseChoice).where(SurveyResponseChoice.response_id == row.id))
            row.text_answer = None
            row.scale_answer = None
            row.is_answered = False
            if question.question_type in {
                SurveyQuestionType.SINGLE_CHOICE,
                SurveyQuestionType.MULTIPLE_SELECT,
            }:
                if answer.text is not None or answer.scale is not None:
                    raise HTTPException(status_code=422, detail='Choice survey answers accept only choice IDs.')
                valid_ids = {choice.id for choice in question.choices}
                if not set(answer.choice_ids).issubset(valid_ids):
                    raise HTTPException(status_code=422, detail='Survey answer contains an unknown choice.')
                if question.question_type == SurveyQuestionType.SINGLE_CHOICE and len(answer.choice_ids) > 1:
                    raise HTTPException(status_code=422, detail='Single-choice survey questions accept one choice.')
                for choice_id in answer.choice_ids:
                    db.add(SurveyResponseChoice(response_id=row.id, choice_id=choice_id))
                row.is_answered = bool(answer.choice_ids)
            elif question.question_type == SurveyQuestionType.SCALE:
                if answer.choice_ids or answer.text is not None:
                    raise HTTPException(status_code=422, detail='Scale survey answers accept only a value from 1 to 5.')
                row.scale_answer = answer.scale
                row.is_answered = answer.scale is not None
            else:
                if answer.choice_ids or answer.scale is not None:
                    raise HTTPException(status_code=422, detail='Text survey answers accept only text.')
                limit = 500 if question.question_type == SurveyQuestionType.SHORT_TEXT else 4000
                value = (answer.text or '').strip()
                if len(value) > limit:
                    raise HTTPException(
                        status_code=422, detail=f'Survey text answer must be {limit} characters or fewer.'
                    )
                row.text_answer = value or None
                row.is_answered = bool(value)
            row.updated_at = now
        submission.updated_at = now
        if commit:
            await db.commit()
        else:
            await db.flush()
        return await self.participant_model(submission.id, user_id, db=db)

    async def finalize(
        self,
        session_task: ExperimentSessionTask,
        user_id: str,
        form: SurveyDraftForm,
        db: AsyncSession,
        *,
        commit: bool = True,
    ):
        await self.save_draft(session_task, user_id, form, db, commit=False)
        submission = (
            (await db.execute(select(SurveySubmission).where(SurveySubmission.session_task_id == session_task.id)))
            .scalars()
            .first()
        )
        task = await SurveyTasks.get_task(session_task.survey_task_id, db=db)
        required_ids = {question.id for question in task.questions if question.enabled and question.required}
        answered_ids = set(
            (
                await db.execute(
                    select(SurveyResponse.question_id).where(
                        SurveyResponse.submission_id == submission.id,
                        SurveyResponse.is_answered.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not required_ids.issubset(answered_ids):
            raise HTTPException(status_code=422, detail='Answer every required survey question.')
        now = int(time.time_ns())
        submission.status = 'SUBMITTED'
        submission.submitted_at = now
        submission.updated_at = now
        if commit:
            await db.commit()
        else:
            await db.flush()
        return await self.participant_model(submission.id, user_id, db=db)

    async def skip(
        self,
        session_task: ExperimentSessionTask,
        user_id: str,
        db: AsyncSession,
        *,
        commit: bool = True,
    ):
        if session_task.survey_required:
            raise HTTPException(status_code=409, detail='This survey is required and cannot be skipped.')
        submission = await self.get_or_create(session_task, user_id, db)
        now = int(time.time_ns())
        submission.status = 'SKIPPED'
        submission.skipped_at = now
        submission.updated_at = now
        if commit:
            await db.commit()
        else:
            await db.flush()
        return await self.participant_model(submission.id, user_id, db=db)

    async def participant_model(self, submission_id: str, user_id: str, db: Optional[AsyncSession] = None):
        async with _submission_db_context(db) as db:
            submission = await db.get(SurveySubmission, submission_id)
            if not submission or submission.user_id != user_id:
                return None
            responses = list(
                (
                    await db.execute(
                        select(SurveyResponse)
                        .join(SurveyQuestion, SurveyQuestion.id == SurveyResponse.question_id)
                        .where(SurveyResponse.submission_id == submission.id)
                        .order_by(SurveyQuestion.position)
                    )
                )
                .scalars()
                .all()
            )
            ids = [row.id for row in responses]
            selected = (
                list(
                    (await db.execute(select(SurveyResponseChoice).where(SurveyResponseChoice.response_id.in_(ids))))
                    .scalars()
                    .all()
                )
                if ids
                else []
            )
            selected_map: dict[str, list[str]] = {}
            for row in selected:
                selected_map.setdefault(row.response_id, []).append(row.choice_id)
            return ParticipantSurveySubmissionModel(
                id=submission.id,
                session_task_id=submission.session_task_id,
                status=submission.status,
                submitted_at=submission.submitted_at,
                skipped_at=submission.skipped_at,
                responses=[
                    ParticipantSurveyResponseModel(
                        question_id=row.question_id,
                        choice_ids=selected_map.get(row.id, []),
                        text=row.text_answer,
                        scale=row.scale_answer,
                    )
                    for row in responses
                ],
            )


SurveySubmissions = SurveySubmissionTable()
