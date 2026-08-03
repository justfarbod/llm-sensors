import re
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Integer, Numeric, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context

SCORE_QUANTUM = Decimal('0.01')
MAX_QUESTION_SCORE = Decimal('10000.00')
BLANK_TOKEN = re.compile(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}')


class QuestionType(StrEnum):
    SINGLE_CHOICE = 'SINGLE_CHOICE'
    MULTIPLE_SELECT = 'MULTIPLE_SELECT'
    FILL_BLANK = 'FILL_BLANK'
    FREE_TEXT = 'FREE_TEXT'


class GradingMode(StrEnum):
    AUTOMATIC = 'AUTOMATIC'
    MANUAL = 'MANUAL'
    LLM_ASSISTED = 'LLM_ASSISTED'


class GradingStrictness(StrEnum):
    LENIENT = 'LENIENT'
    BALANCED = 'BALANCED'
    STRICT = 'STRICT'


class QuestionTaskStatus(StrEnum):
    DRAFT = 'DRAFT'
    PUBLISHED = 'PUBLISHED'
    ARCHIVED = 'ARCHIVED'


class QuestionCloneMode(StrEnum):
    VERSION = 'VERSION'
    DUPLICATE = 'DUPLICATE'


class GradingStatus(StrEnum):
    NOT_STARTED = 'NOT_STARTED'
    AWAITING_REVIEW = 'AWAITING_REVIEW'
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    GRADED = 'GRADED'
    FAILED = 'FAILED'


class QuestionTask(Base):
    __tablename__ = 'question_task'

    id = Column(Text, primary_key=True)
    family_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default='')
    status = Column(Text, nullable=False, default=QuestionTaskStatus.DRAFT.value)
    locked_at = Column(BigInteger, nullable=True)
    archived_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class QuestionTaskQuestion(Base):
    __tablename__ = 'question_task_question'

    id = Column(Text, primary_key=True)
    task_id = Column(Text, ForeignKey('question_task.id', ondelete='CASCADE'), nullable=False)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default='')
    question_type = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    max_score = Column(Numeric(10, 2), nullable=False)
    grading_mode = Column(Text, nullable=False)
    image_file_id = Column(Text, ForeignKey('file.id', ondelete='SET NULL'), nullable=True)
    case_sensitive = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class QuestionChoice(Base):
    __tablename__ = 'question_choice'

    id = Column(Text, primary_key=True)
    question_id = Column(Text, ForeignKey('question_task_question.id', ondelete='CASCADE'), nullable=False)
    text = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    is_correct = Column(Boolean, nullable=False, default=False)


class QuestionBlank(Base):
    __tablename__ = 'question_blank'

    id = Column(Text, primary_key=True)
    question_id = Column(Text, ForeignKey('question_task_question.id', ondelete='CASCADE'), nullable=False)
    blank_key = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)


class QuestionBlankAcceptedAnswer(Base):
    __tablename__ = 'question_blank_accepted_answer'

    id = Column(Text, primary_key=True)
    blank_id = Column(Text, ForeignKey('question_blank.id', ondelete='CASCADE'), nullable=False)
    answer = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)


class QuestionFreeTextConfig(Base):
    __tablename__ = 'question_free_text_config'

    question_id = Column(Text, ForeignKey('question_task_question.id', ondelete='CASCADE'), primary_key=True)
    expected_answer = Column(Text, nullable=False)
    strictness = Column(Text, nullable=False)


class ChoiceForm(BaseModel):
    id: Optional[str] = None
    text: str = Field(min_length=1, max_length=4000)
    is_correct: bool = False

    @field_validator('text')
    @classmethod
    def strip_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Choice text is required.')
        return value


class BlankForm(BaseModel):
    id: Optional[str] = None
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]*$', max_length=80)
    accepted_answers: list[str] = Field(min_length=1, max_length=100)

    @field_validator('accepted_answers')
    @classmethod
    def normalize_answers(cls, values):
        normalized = []
        for value in values:
            value = value.strip()
            if value and value not in normalized:
                normalized.append(value)
        if not normalized:
            raise ValueError('At least one accepted answer is required.')
        return normalized


class QuestionForm(BaseModel):
    id: Optional[str] = None
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=20000)
    question_type: QuestionType
    max_score: Decimal = Field(gt=0, le=MAX_QUESTION_SCORE)
    grading_mode: GradingMode
    image_file_id: Optional[str] = None
    case_sensitive: bool = False
    choices: list[ChoiceForm] = Field(default_factory=list, max_length=100)
    blanks: list[BlankForm] = Field(default_factory=list, max_length=100)
    expected_answer: Optional[str] = Field(default=None, max_length=20000)
    strictness: Optional[GradingStrictness] = None

    @field_validator('title', 'description')
    @classmethod
    def strip_strings(cls, value):
        return value.strip()

    @field_validator('max_score')
    @classmethod
    def score_precision(cls, value):
        if value.quantize(SCORE_QUANTUM) != value:
            raise ValueError('Scores support at most two decimal places.')
        return value

    @model_validator(mode='after')
    def validate_configuration(self):
        if self.question_type == QuestionType.SINGLE_CHOICE:
            if len(self.choices) < 2 or sum(choice.is_correct for choice in self.choices) != 1:
                raise ValueError('Single-choice questions require at least two choices and exactly one correct choice.')
            if self.grading_mode not in {GradingMode.AUTOMATIC, GradingMode.MANUAL}:
                raise ValueError('Single-choice grading must be automatic or manual.')
        elif self.question_type == QuestionType.MULTIPLE_SELECT:
            correct = sum(choice.is_correct for choice in self.choices)
            if len(self.choices) < 2 or correct < 1 or correct == len(self.choices):
                raise ValueError('Multiple-select questions require at least one correct and one incorrect choice.')
            if self.grading_mode not in {GradingMode.AUTOMATIC, GradingMode.MANUAL}:
                raise ValueError('Multiple-select grading must be automatic or manual.')
        elif self.question_type == QuestionType.FILL_BLANK:
            keys = [blank.key for blank in self.blanks]
            tokens = BLANK_TOKEN.findall(self.description)
            if not keys or len(keys) != len(set(keys)) or sorted(tokens) != sorted(keys):
                raise ValueError('Every configured blank token must appear exactly once in the instructions.')
            if self.grading_mode not in {GradingMode.AUTOMATIC, GradingMode.MANUAL}:
                raise ValueError('Fill-in grading must be automatic or manual.')
        elif self.question_type == QuestionType.FREE_TEXT:
            if self.grading_mode == GradingMode.AUTOMATIC:
                raise ValueError('Free-text questions must use manual or LLM-assisted grading.')
            if self.grading_mode == GradingMode.LLM_ASSISTED:
                self.expected_answer = (self.expected_answer or '').strip()
                if not self.expected_answer or self.strictness is None:
                    raise ValueError('LLM-assisted grading requires an expected answer and strictness.')
        return self


class QuestionTaskForm(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=20000)
    questions: list[QuestionForm] = Field(default_factory=list, max_length=500)

    @field_validator('title', 'description')
    @classmethod
    def strip_strings(cls, value):
        return value.strip()


class QuestionCloneForm(BaseModel):
    mode: QuestionCloneMode


class ChoiceModel(BaseModel):
    id: str
    text: str
    position: int
    is_correct: bool


class BlankModel(BaseModel):
    id: str
    key: str
    position: int
    accepted_answers: list[str]


class QuestionModel(BaseModel):
    id: str
    title: str
    description: str
    question_type: QuestionType
    position: int
    max_score: Decimal
    grading_mode: GradingMode
    image_file_id: Optional[str] = None
    case_sensitive: bool = False
    choices: list[ChoiceModel] = []
    blanks: list[BlankModel] = []
    expected_answer: Optional[str] = None
    strictness: Optional[GradingStrictness] = None


class QuestionTaskModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    version: int
    latest_version: int
    title: str
    description: str
    status: QuestionTaskStatus
    locked_at: Optional[int] = None
    archived_at: Optional[int] = None
    created_at: int
    updated_at: int
    questions: list[QuestionModel] = []


class ParticipantChoiceModel(BaseModel):
    id: str
    text: str
    position: int


class ParticipantBlankModel(BaseModel):
    id: str
    key: str
    position: int


class ParticipantQuestionModel(BaseModel):
    id: str
    title: str
    description: str
    question_type: QuestionType
    position: int
    max_score: Decimal
    image_file_id: Optional[str] = None
    choices: list[ParticipantChoiceModel] = []
    blanks: list[ParticipantBlankModel] = []


class ParticipantQuestionTaskModel(BaseModel):
    id: str
    title: str
    description: str
    questions: list[ParticipantQuestionModel]


def score_value(value: Decimal) -> Decimal:
    return max(Decimal('0'), value).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def grade_single_choice(max_score: Decimal, selected_ids: set[str], correct_id: str) -> Decimal:
    return score_value(max_score if selected_ids == {correct_id} else Decimal('0'))


def grade_multiple_select(
    max_score: Decimal, selected_ids: set[str], correct_ids: set[str], all_ids: set[str]
) -> Decimal:
    incorrect_ids = all_ids - correct_ids
    credit = Decimal(len(selected_ids & correct_ids)) / Decimal(len(correct_ids))
    penalty = Decimal(len(selected_ids & incorrect_ids)) / Decimal(len(incorrect_ids))
    # Selecting every option earns zero: full correct credit is cancelled by full incorrect penalty.
    return score_value(max_score * max(Decimal('0'), credit - penalty))


def normalize_blank(value: str, case_sensitive: bool) -> str:
    value = value.strip()
    return value if case_sensitive else value.casefold()


def grade_fill_blanks(
    max_score: Decimal,
    answers: dict[str, str],
    accepted: dict[str, list[str]],
    case_sensitive: bool,
) -> Decimal:
    correct = 0
    for blank_id, candidates in accepted.items():
        supplied = normalize_blank(answers.get(blank_id, ''), case_sensitive)
        if supplied and supplied in {normalize_blank(candidate, case_sensitive) for candidate in candidates}:
            correct += 1
    return score_value(max_score * Decimal(correct) / Decimal(len(accepted)))


class QuestionTaskTable:
    async def list_tasks(self, db: Optional[AsyncSession] = None, include_archived: bool = False):
        async with get_async_db_context(db) as db:
            stmt = select(QuestionTask).order_by(QuestionTask.updated_at.desc())
            if not include_archived:
                stmt = stmt.where(QuestionTask.status != QuestionTaskStatus.ARCHIVED.value)
            tasks = (await db.execute(stmt)).scalars().all()
            return [await self.get_task(task.id, db=db) for task in tasks]

    async def get_task(self, task_id: str, db: Optional[AsyncSession] = None) -> Optional[QuestionTaskModel]:
        async with get_async_db_context(db) as db:
            task = await db.get(QuestionTask, task_id)
            if not task:
                return None
            questions = list(
                (
                    await db.execute(
                        select(QuestionTaskQuestion)
                        .where(QuestionTaskQuestion.task_id == task_id)
                        .order_by(QuestionTaskQuestion.position)
                    )
                )
                .scalars()
                .all()
            )
            question_ids = [question.id for question in questions]
            choices = (
                list(
                    (
                        await db.execute(
                            select(QuestionChoice)
                            .where(QuestionChoice.question_id.in_(question_ids))
                            .order_by(QuestionChoice.position)
                        )
                    )
                    .scalars()
                    .all()
                )
                if question_ids
                else []
            )
            blanks = (
                list(
                    (
                        await db.execute(
                            select(QuestionBlank)
                            .where(QuestionBlank.question_id.in_(question_ids))
                            .order_by(QuestionBlank.position)
                        )
                    )
                    .scalars()
                    .all()
                )
                if question_ids
                else []
            )
            blank_ids = [blank.id for blank in blanks]
            accepted = (
                list(
                    (
                        await db.execute(
                            select(QuestionBlankAcceptedAnswer)
                            .where(QuestionBlankAcceptedAnswer.blank_id.in_(blank_ids))
                            .order_by(QuestionBlankAcceptedAnswer.position)
                        )
                    )
                    .scalars()
                    .all()
                )
                if blank_ids
                else []
            )
            configs = (
                list(
                    (
                        await db.execute(
                            select(QuestionFreeTextConfig).where(QuestionFreeTextConfig.question_id.in_(question_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
                if question_ids
                else []
            )
            choice_map: dict[str, list] = {}
            for choice in choices:
                choice_map.setdefault(choice.question_id, []).append(choice)
            answer_map: dict[str, list[str]] = {}
            for answer in accepted:
                answer_map.setdefault(answer.blank_id, []).append(answer.answer)
            blank_map: dict[str, list] = {}
            for blank in blanks:
                blank_map.setdefault(blank.question_id, []).append(blank)
            config_map = {config.question_id: config for config in configs}
            latest = (
                await db.execute(select(func.max(QuestionTask.version)).where(QuestionTask.family_id == task.family_id))
            ).scalar() or task.version
            return QuestionTaskModel(
                **{
                    column: getattr(task, column)
                    for column in (
                        'id',
                        'family_id',
                        'version',
                        'title',
                        'description',
                        'status',
                        'locked_at',
                        'archived_at',
                        'created_at',
                        'updated_at',
                    )
                },
                latest_version=latest,
                questions=[
                    QuestionModel(
                        id=question.id,
                        title=question.title,
                        description=question.description,
                        question_type=question.question_type,
                        position=question.position,
                        max_score=question.max_score,
                        grading_mode=question.grading_mode,
                        image_file_id=question.image_file_id,
                        case_sensitive=question.case_sensitive,
                        choices=[
                            ChoiceModel(
                                id=choice.id, text=choice.text, position=choice.position, is_correct=choice.is_correct
                            )
                            for choice in choice_map.get(question.id, [])
                        ],
                        blanks=[
                            BlankModel(
                                id=blank.id,
                                key=blank.blank_key,
                                position=blank.position,
                                accepted_answers=answer_map.get(blank.id, []),
                            )
                            for blank in blank_map.get(question.id, [])
                        ],
                        expected_answer=config_map[question.id].expected_answer if question.id in config_map else None,
                        strictness=config_map[question.id].strictness if question.id in config_map else None,
                    )
                    for question in questions
                ],
            )

    async def create_task(self, form: QuestionTaskForm, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            now = int(time.time_ns())
            task_id = str(uuid.uuid4())
            task = QuestionTask(
                id=task_id,
                family_id=task_id,
                version=1,
                title=form.title,
                description=form.description,
                status=QuestionTaskStatus.DRAFT.value,
                created_at=now,
                updated_at=now,
            )
            db.add(task)
            await db.flush()
            await self._replace_questions(task, form.questions, db)
            await db.commit()
            return await self.get_task(task.id, db=db)

    async def update_task(self, task_id: str, form: QuestionTaskForm, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            task = await db.get(QuestionTask, task_id)
            if not task:
                return None
            if task.status != QuestionTaskStatus.DRAFT.value or task.locked_at:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='Published Question Tasks are immutable. Create a new version or duplicate instead.',
                )
            task.title = form.title
            task.description = form.description
            task.status = QuestionTaskStatus.DRAFT.value
            task.updated_at = int(time.time_ns())
            await self._delete_questions(task_id, db)
            await self._replace_questions(task, form.questions, db)
            await db.commit()
            return await self.get_task(task_id, db=db)

    async def clone(self, task_id: str, mode: QuestionCloneMode, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            source = await self.get_task(task_id, db=db)
            if not source:
                return None
            new_id = str(uuid.uuid4())
            family_id = source.family_id if mode == QuestionCloneMode.VERSION else new_id
            version = (
                (
                    await db.execute(select(func.max(QuestionTask.version)).where(QuestionTask.family_id == family_id))
                ).scalar()
                or 0
            ) + 1
            if mode == QuestionCloneMode.DUPLICATE:
                version = 1
            now = int(time.time_ns())
            row = QuestionTask(
                id=new_id,
                family_id=family_id,
                version=version,
                title=source.title if mode == QuestionCloneMode.VERSION else f'{source.title} (copy)',
                description=source.description,
                status=QuestionTaskStatus.DRAFT.value,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            forms = [
                QuestionForm(
                    title=question.title,
                    description=question.description,
                    question_type=question.question_type,
                    max_score=question.max_score,
                    grading_mode=question.grading_mode,
                    image_file_id=question.image_file_id,
                    case_sensitive=question.case_sensitive,
                    choices=[ChoiceForm(text=choice.text, is_correct=choice.is_correct) for choice in question.choices],
                    blanks=[
                        BlankForm(key=blank.key, accepted_answers=blank.accepted_answers) for blank in question.blanks
                    ],
                    expected_answer=question.expected_answer,
                    strictness=question.strictness,
                )
                for question in source.questions
            ]
            await self._replace_questions(row, forms, db)
            await db.commit()
            return await self.get_task(new_id, db=db)

    async def _delete_questions(self, task_id: str, db: AsyncSession):
        ids = list(
            (await db.execute(select(QuestionTaskQuestion.id).where(QuestionTaskQuestion.task_id == task_id)))
            .scalars()
            .all()
        )
        if not ids:
            return
        blank_ids = list(
            (await db.execute(select(QuestionBlank.id).where(QuestionBlank.question_id.in_(ids)))).scalars().all()
        )
        if blank_ids:
            await db.execute(
                delete(QuestionBlankAcceptedAnswer).where(QuestionBlankAcceptedAnswer.blank_id.in_(blank_ids))
            )
        await db.execute(delete(QuestionBlank).where(QuestionBlank.question_id.in_(ids)))
        await db.execute(delete(QuestionChoice).where(QuestionChoice.question_id.in_(ids)))
        await db.execute(delete(QuestionFreeTextConfig).where(QuestionFreeTextConfig.question_id.in_(ids)))
        await db.execute(delete(QuestionTaskQuestion).where(QuestionTaskQuestion.id.in_(ids)))

    async def _replace_questions(self, task: QuestionTask, forms: list[QuestionForm], db: AsyncSession):
        now = int(time.time_ns())
        for position, form in enumerate(forms):
            question_id = form.id or str(uuid.uuid4())
            db.add(
                QuestionTaskQuestion(
                    id=question_id,
                    task_id=task.id,
                    title=form.title,
                    description=form.description,
                    question_type=form.question_type.value,
                    position=position,
                    max_score=form.max_score,
                    grading_mode=form.grading_mode.value,
                    image_file_id=form.image_file_id,
                    case_sensitive=form.case_sensitive,
                    created_at=now,
                    updated_at=now,
                )
            )
            for choice_position, choice in enumerate(form.choices):
                db.add(
                    QuestionChoice(
                        id=choice.id or str(uuid.uuid4()),
                        question_id=question_id,
                        text=choice.text,
                        position=choice_position,
                        is_correct=choice.is_correct,
                    )
                )
            for blank_position, blank in enumerate(form.blanks):
                blank_id = blank.id or str(uuid.uuid4())
                db.add(
                    QuestionBlank(id=blank_id, question_id=question_id, blank_key=blank.key, position=blank_position)
                )
                for answer_position, answer in enumerate(blank.accepted_answers):
                    db.add(
                        QuestionBlankAcceptedAnswer(
                            id=str(uuid.uuid4()), blank_id=blank_id, answer=answer, position=answer_position
                        )
                    )
            if form.question_type == QuestionType.FREE_TEXT:
                db.add(
                    QuestionFreeTextConfig(
                        question_id=question_id,
                        expected_answer=form.expected_answer or '',
                        strictness=(form.strictness or GradingStrictness.BALANCED).value,
                    )
                )

    async def publish(self, task_id: str, task_model_available: bool, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            task = await self.get_task(task_id, db=db)
            if not task:
                return None
            if not task.questions:
                raise HTTPException(status_code=422, detail='A Question Task must contain at least one question.')
            if (
                any(question.grading_mode == GradingMode.LLM_ASSISTED for question in task.questions)
                and not task_model_available
            ):
                raise HTTPException(
                    status_code=422,
                    detail='Configure an available global task model before publishing LLM-graded questions.',
                )
            row = await db.get(QuestionTask, task_id)
            row.status = QuestionTaskStatus.PUBLISHED.value
            row.archived_at = None
            row.updated_at = int(time.time_ns())
            await db.commit()
            return await self.get_task(task_id, db=db)

    async def archive(self, task_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            task = await db.get(QuestionTask, task_id)
            if not task:
                return False
            now = int(time.time_ns())
            task.status = QuestionTaskStatus.ARCHIVED.value
            task.archived_at = now
            task.updated_at = now
            await db.commit()
            return True

    async def participant_task(self, task_id: str, db: Optional[AsyncSession] = None):
        task = await self.get_task(task_id, db=db)
        if not task:
            return None
        return ParticipantQuestionTaskModel(
            id=task.id,
            title=task.title,
            description=task.description,
            questions=[
                ParticipantQuestionModel(
                    id=question.id,
                    title=question.title,
                    description=question.description,
                    question_type=question.question_type,
                    position=question.position,
                    max_score=question.max_score,
                    image_file_id=question.image_file_id,
                    choices=[
                        ParticipantChoiceModel(id=choice.id, text=choice.text, position=choice.position)
                        for choice in question.choices
                    ],
                    blanks=[
                        ParticipantBlankModel(id=blank.id, key=blank.key, position=blank.position)
                        for blank in question.blanks
                    ],
                )
                for question in task.questions
            ],
        )


QuestionTasks = QuestionTaskTable()
