import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Numeric, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.experiment_plans import ExperimentSessionTask, ExperimentTaskType
from open_webui.models.question_tasks import (
    GradingMode,
    GradingStatus,
    QuestionTasks,
    QuestionTaskQuestion,
    QuestionType,
    grade_fill_blanks,
    grade_multiple_select,
    grade_single_choice,
)


@asynccontextmanager
async def _submission_db_context(db: Optional[AsyncSession] = None):
    """Always reuse an explicitly supplied session for atomic submission transitions."""
    if db is not None:
        yield db
    else:
        async with get_async_db_context() as owned_db:
            yield owned_db


class QuestionSubmission(Base):
    __tablename__ = 'question_submission'

    id = Column(Text, primary_key=True)
    session_task_id = Column(Text, ForeignKey('experiment_session_task.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='DRAFT')
    grading_status = Column(Text, nullable=False, default=GradingStatus.NOT_STARTED.value)
    current_score = Column(Numeric(10, 2), nullable=True)
    provisional_score = Column(Numeric(10, 2), nullable=False, default=0)
    has_grading_error = Column(Boolean, nullable=False, default=False)
    maximum_score = Column(Numeric(10, 2), nullable=False, default=0)
    submitted_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class QuestionResponse(Base):
    __tablename__ = 'question_response'

    id = Column(Text, primary_key=True)
    submission_id = Column(Text, ForeignKey('question_submission.id', ondelete='CASCADE'), nullable=False)
    question_id = Column(Text, ForeignKey('question_task_question.id', ondelete='RESTRICT'), nullable=False)
    free_text_answer = Column(Text, nullable=True)
    is_answered = Column(Boolean, nullable=False, default=False)
    grading_status = Column(Text, nullable=False, default=GradingStatus.NOT_STARTED.value)
    generated_score = Column(Numeric(10, 2), nullable=True)
    effective_score = Column(Numeric(10, 2), nullable=True)
    grading_method = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class QuestionResponseChoice(Base):
    __tablename__ = 'question_response_choice'

    response_id = Column(Text, ForeignKey('question_response.id', ondelete='CASCADE'), primary_key=True)
    choice_id = Column(Text, ForeignKey('question_choice.id', ondelete='RESTRICT'), primary_key=True)


class QuestionResponseBlank(Base):
    __tablename__ = 'question_response_blank'

    response_id = Column(Text, ForeignKey('question_response.id', ondelete='CASCADE'), primary_key=True)
    blank_id = Column(Text, ForeignKey('question_blank.id', ondelete='RESTRICT'), primary_key=True)
    answer = Column(Text, nullable=False, default='')


class QuestionGradingAttempt(Base):
    __tablename__ = 'question_grading_attempt'

    id = Column(Text, primary_key=True)
    response_id = Column(Text, ForeignKey('question_response.id', ondelete='CASCADE'), nullable=False)
    method = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    model_id = Column(Text, nullable=True)
    awarded_score = Column(Numeric(10, 2), nullable=True)
    rationale = Column(Text, nullable=True)
    error_code = Column(Text, nullable=True)
    started_at = Column(BigInteger, nullable=True)
    completed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class QuestionScoreOverride(Base):
    __tablename__ = 'question_score_override'

    id = Column(Text, primary_key=True)
    response_id = Column(Text, ForeignKey('question_response.id', ondelete='CASCADE'), nullable=False)
    admin_id = Column(Text, nullable=False)
    previous_score = Column(Numeric(10, 2), nullable=True)
    new_score = Column(Numeric(10, 2), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class BlankAnswerForm(BaseModel):
    blank_id: str
    value: str = Field(default='', max_length=20000)


class QuestionAnswerForm(BaseModel):
    question_id: str
    choice_ids: list[str] = Field(default_factory=list, max_length=100)
    blank_answers: list[BlankAnswerForm] = Field(default_factory=list, max_length=100)
    text: Optional[str] = Field(default=None, max_length=100000)

    @model_validator(mode='after')
    def unique_values(self):
        if len(self.choice_ids) != len(set(self.choice_ids)):
            raise ValueError('Choice IDs must be unique.')
        ids = [answer.blank_id for answer in self.blank_answers]
        if len(ids) != len(set(ids)):
            raise ValueError('Blank IDs must be unique.')
        return self


class QuestionDraftForm(BaseModel):
    answers: list[QuestionAnswerForm] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def unique_questions(self):
        ids = [answer.question_id for answer in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError('Question answers must be unique.')
        return self


class SubmissionResponseModel(BaseModel):
    id: str
    question_id: str
    is_answered: bool
    grading_status: GradingStatus
    selected_choice_ids: list[str] = []
    blank_answers: list[BlankAnswerForm] = []
    text: Optional[str] = None


class ParticipantSubmissionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_task_id: str
    status: str
    grading_status: GradingStatus
    submitted_at: Optional[int] = None
    responses: list[SubmissionResponseModel] = []


class QuestionSubmissionTable:
    async def get_or_create(self, session_task: ExperimentSessionTask, user_id: str, db: AsyncSession):
        submission = (
            (await db.execute(select(QuestionSubmission).where(QuestionSubmission.session_task_id == session_task.id)))
            .scalars()
            .first()
        )
        if submission:
            if submission.user_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Submission is not owned by user.')
            return submission
        task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
        if not task:
            raise HTTPException(status_code=404, detail='Question Task is unavailable.')
        now = int(time.time_ns())
        submission = QuestionSubmission(
            id=str(uuid.uuid4()),
            session_task_id=session_task.id,
            user_id=user_id,
            status='DRAFT',
            grading_status=GradingStatus.NOT_STARTED.value,
            current_score=None,
            provisional_score=Decimal('0'),
            has_grading_error=False,
            maximum_score=sum((question.max_score for question in task.questions), Decimal('0')),
            created_at=now,
            updated_at=now,
        )
        db.add(submission)
        await db.flush()
        for question in task.questions:
            db.add(
                QuestionResponse(
                    id=str(uuid.uuid4()),
                    submission_id=submission.id,
                    question_id=question.id,
                    is_answered=False,
                    grading_status=GradingStatus.NOT_STARTED.value,
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.flush()
        return submission

    async def save_draft(
        self,
        session_task_id: str,
        user_id: str,
        form: QuestionDraftForm,
        db: Optional[AsyncSession] = None,
        *,
        commit: bool = True,
    ):
        async with _submission_db_context(db) as db:
            session_task = await db.get(ExperimentSessionTask, session_task_id)
            if not session_task or session_task.task_type != ExperimentTaskType.QUESTION.value:
                raise HTTPException(status_code=404, detail='Question session task not found.')
            submission = await self.get_or_create(session_task, user_id, db)
            if submission.status == 'FINALIZED':
                raise HTTPException(status_code=409, detail='This submission is finalized.')
            task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
            question_map = {question.id: question for question in task.questions}
            if not {answer.question_id for answer in form.answers}.issubset(question_map):
                raise HTTPException(status_code=422, detail='Submission contains a question outside this task.')
            responses = list(
                (await db.execute(select(QuestionResponse).where(QuestionResponse.submission_id == submission.id)))
                .scalars()
                .all()
            )
            response_map = {response.question_id: response for response in responses}
            answer_map = {answer.question_id: answer for answer in form.answers}
            now = int(time.time_ns())
            for question_id, question in question_map.items():
                response = response_map[question_id]
                answer = answer_map.get(question_id, QuestionAnswerForm(question_id=question_id))
                await db.execute(
                    delete(QuestionResponseChoice).where(QuestionResponseChoice.response_id == response.id)
                )
                await db.execute(delete(QuestionResponseBlank).where(QuestionResponseBlank.response_id == response.id))
                response.free_text_answer = None
                if question.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_SELECT}:
                    valid_ids = {choice.id for choice in question.choices}
                    if not set(answer.choice_ids).issubset(valid_ids):
                        raise HTTPException(status_code=422, detail='Submission contains an invalid choice.')
                    if question.question_type == QuestionType.SINGLE_CHOICE and len(answer.choice_ids) > 1:
                        raise HTTPException(
                            status_code=422, detail='Single-choice questions accept at most one choice.'
                        )
                    for choice_id in answer.choice_ids:
                        db.add(QuestionResponseChoice(response_id=response.id, choice_id=choice_id))
                    response.is_answered = bool(answer.choice_ids)
                elif question.question_type == QuestionType.FILL_BLANK:
                    valid_ids = {blank.id for blank in question.blanks}
                    supplied = {item.blank_id: item.value for item in answer.blank_answers}
                    if not set(supplied).issubset(valid_ids):
                        raise HTTPException(status_code=422, detail='Submission contains an invalid blank.')
                    for blank in question.blanks:
                        db.add(
                            QuestionResponseBlank(
                                response_id=response.id, blank_id=blank.id, answer=supplied.get(blank.id, '')
                            )
                        )
                    response.is_answered = any(value.strip() for value in supplied.values())
                else:
                    response.free_text_answer = answer.text or ''
                    response.is_answered = bool((answer.text or '').strip())
                response.updated_at = now
            submission.updated_at = now
            if commit:
                await db.commit()
            else:
                await db.flush()
            return await self.participant_model(submission.id, user_id, db=db)

    async def finalize(
        self,
        submission_id: str,
        user_id: str,
        db: Optional[AsyncSession] = None,
        *,
        commit: bool = True,
    ):
        async with _submission_db_context(db) as db:
            submission = await db.get(QuestionSubmission, submission_id)
            if not submission or submission.user_id != user_id:
                raise HTTPException(status_code=404, detail='Question submission not found.')
            if submission.status == 'FINALIZED':
                return [], await self.participant_model(submission.id, user_id, db=db)
            session_task = await db.get(ExperimentSessionTask, submission.session_task_id)
            task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
            questions = {question.id: question for question in task.questions}
            responses = list(
                (await db.execute(select(QuestionResponse).where(QuestionResponse.submission_id == submission.id)))
                .scalars()
                .all()
            )
            response_ids = [response.id for response in responses]
            selected_rows = (
                list(
                    (
                        await db.execute(
                            select(QuestionResponseChoice).where(QuestionResponseChoice.response_id.in_(response_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if response_ids
                else []
            )
            blank_rows = (
                list(
                    (
                        await db.execute(
                            select(QuestionResponseBlank).where(QuestionResponseBlank.response_id.in_(response_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if response_ids
                else []
            )
            selected: dict[str, set[str]] = {}
            for row in selected_rows:
                selected.setdefault(row.response_id, set()).add(row.choice_id)
            blanks: dict[str, dict[str, str]] = {}
            for row in blank_rows:
                blanks.setdefault(row.response_id, {})[row.blank_id] = row.answer
            pending_llm = []
            now = int(time.time_ns())
            for response in responses:
                question = questions[response.question_id]
                if question.grading_mode == GradingMode.MANUAL:
                    response.grading_status = GradingStatus.AWAITING_REVIEW.value
                    response.grading_method = GradingMode.MANUAL.value
                elif question.grading_mode == GradingMode.LLM_ASSISTED:
                    if not response.is_answered:
                        response.generated_score = Decimal('0')
                        response.effective_score = Decimal('0')
                        response.grading_status = GradingStatus.GRADED.value
                        response.grading_method = GradingMode.LLM_ASSISTED.value
                        response.rationale = 'No answer was submitted.'
                    else:
                        response.grading_status = GradingStatus.PENDING.value
                        response.grading_method = GradingMode.LLM_ASSISTED.value
                        attempt = QuestionGradingAttempt(
                            id=str(uuid.uuid4()),
                            response_id=response.id,
                            method=GradingMode.LLM_ASSISTED.value,
                            status=GradingStatus.PENDING.value,
                            created_at=now,
                        )
                        db.add(attempt)
                        pending_llm.append(response.id)
                elif question.question_type == QuestionType.SINGLE_CHOICE:
                    correct_id = next(choice.id for choice in question.choices if choice.is_correct)
                    result = grade_single_choice(question.max_score, selected.get(response.id, set()), correct_id)
                    self._apply_auto(response, result)
                elif question.question_type == QuestionType.MULTIPLE_SELECT:
                    all_ids = {choice.id for choice in question.choices}
                    correct_ids = {choice.id for choice in question.choices if choice.is_correct}
                    result = grade_multiple_select(
                        question.max_score, selected.get(response.id, set()), correct_ids, all_ids
                    )
                    self._apply_auto(response, result)
                elif question.question_type == QuestionType.FILL_BLANK:
                    accepted = {blank.id: blank.accepted_answers for blank in question.blanks}
                    result = grade_fill_blanks(
                        question.max_score, blanks.get(response.id, {}), accepted, question.case_sensitive
                    )
                    self._apply_auto(response, result)
                response.updated_at = now
            submission.status = 'FINALIZED'
            submission.submitted_at = now
            submission.updated_at = now
            await self.recalculate(submission.id, db)
            if commit:
                await db.commit()
            else:
                await db.flush()
            return pending_llm, await self.participant_model(submission.id, user_id, db=db)

    @staticmethod
    def _apply_auto(response: QuestionResponse, result: Decimal):
        response.generated_score = result
        response.effective_score = result
        response.grading_status = GradingStatus.GRADED.value
        response.grading_method = GradingMode.AUTOMATIC.value

    async def recalculate(self, submission_id: str, db: AsyncSession):
        submission = await db.get(QuestionSubmission, submission_id)
        rows = list(
            (await db.execute(select(QuestionResponse).where(QuestionResponse.submission_id == submission_id)))
            .scalars()
            .all()
        )
        provisional = sum((row.effective_score or Decimal('0') for row in rows), Decimal('0'))
        submission.provisional_score = provisional
        statuses = {row.grading_status for row in rows}
        submission.has_grading_error = GradingStatus.FAILED.value in statuses
        if submission.status != 'FINALIZED':
            submission.grading_status = GradingStatus.NOT_STARTED.value
        elif (
            GradingStatus.RUNNING.value in statuses
            or GradingStatus.PENDING.value in statuses
            or GradingStatus.FAILED.value in statuses
        ):
            submission.grading_status = GradingStatus.PENDING.value
        elif GradingStatus.AWAITING_REVIEW.value in statuses:
            submission.grading_status = GradingStatus.AWAITING_REVIEW.value
        else:
            submission.grading_status = GradingStatus.GRADED.value
        submission.current_score = provisional if submission.grading_status == GradingStatus.GRADED.value else None

    async def participant_model(self, submission_id: str, user_id: str, db: Optional[AsyncSession] = None):
        async with _submission_db_context(db) as db:
            submission = await db.get(QuestionSubmission, submission_id)
            if not submission or submission.user_id != user_id:
                return None
            rows = list(
                (
                    await db.execute(
                        select(QuestionResponse)
                        .join(QuestionTaskQuestion, QuestionTaskQuestion.id == QuestionResponse.question_id)
                        .where(QuestionResponse.submission_id == submission_id)
                        .order_by(QuestionTaskQuestion.position)
                    )
                )
                .scalars()
                .all()
            )
            response_ids = [row.id for row in rows]
            selected_rows = (
                list(
                    (
                        await db.execute(
                            select(QuestionResponseChoice).where(QuestionResponseChoice.response_id.in_(response_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if response_ids
                else []
            )
            blank_rows = (
                list(
                    (
                        await db.execute(
                            select(QuestionResponseBlank).where(QuestionResponseBlank.response_id.in_(response_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if response_ids
                else []
            )
            selected: dict[str, list[str]] = {}
            blanks: dict[str, list[BlankAnswerForm]] = {}
            for item in selected_rows:
                selected.setdefault(item.response_id, []).append(item.choice_id)
            for item in blank_rows:
                blanks.setdefault(item.response_id, []).append(
                    BlankAnswerForm(blank_id=item.blank_id, value=item.answer)
                )
            return ParticipantSubmissionModel(
                id=submission.id,
                session_task_id=submission.session_task_id,
                status=submission.status,
                grading_status=submission.grading_status,
                submitted_at=submission.submitted_at,
                responses=[
                    SubmissionResponseModel(
                        id=row.id,
                        question_id=row.question_id,
                        is_answered=row.is_answered,
                        grading_status=row.grading_status,
                        selected_choice_ids=selected.get(row.id, []),
                        blank_answers=blanks.get(row.id, []),
                        text=row.free_text_answer,
                    )
                    for row in rows
                ],
            )


QuestionSubmissions = QuestionSubmissionTable()
