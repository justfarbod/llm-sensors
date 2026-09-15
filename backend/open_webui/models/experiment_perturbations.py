import time
import uuid
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, Column, Float, ForeignKey, Index, Integer, Text, UniqueConstraint

from open_webui.internal.db import Base, JSONField


class PromptInsertionPosition(StrEnum):
    SYSTEM = 'SYSTEM'
    BEFORE_PARTICIPANT = 'BEFORE_PARTICIPANT'
    AFTER_PARTICIPANT = 'AFTER_PARTICIPANT'


class ActivationMode(StrEnum):
    EVERY_REQUEST = 'EVERY_REQUEST'
    FIRST_REQUEST = 'FIRST_REQUEST'
    AFTER_PROMPT_COUNT = 'AFTER_PROMPT_COUNT'
    EVERY_N_PROMPTS = 'EVERY_N_PROMPTS'
    PROMPT_RANGE = 'PROMPT_RANGE'


class PerturbationScope(StrEnum):
    ALL_TASKS = 'ALL_TASKS'
    SELECTED_TASKS = 'SELECTED_TASKS'


class WarningCadence(StrEnum):
    BEGINNING = 'BEGINNING'
    EVERY_N_PROMPTS = 'EVERY_N_PROMPTS'
    PROMPT_LIST = 'PROMPT_LIST'
    ONCE_AFTER_PROMPT = 'ONCE_AFTER_PROMPT'
    EVERY_N_ACTIVE_MINUTES = 'EVERY_N_ACTIVE_MINUTES'
    AFTER_ACTIVE_MINUTES = 'AFTER_ACTIVE_MINUTES'
    ONCE_AFTER_ACTIVE_DELAY = 'ONCE_AFTER_ACTIVE_DELAY'


class ResponseTimingMode(StrEnum):
    NORMAL = 'NORMAL'
    DELAYED = 'DELAYED'
    SLOW = 'SLOW'
    FAST = 'FAST'


class RevealStyle(StrEnum):
    FULL = 'FULL'
    QUICK_STREAM = 'QUICK_STREAM'


class StreamUnit(StrEnum):
    CHARACTER = 'CHARACTER'
    WORD = 'WORD'
    CHUNK = 'CHUNK'


class FastRateUnit(StrEnum):
    CHARACTERS_PER_SECOND = 'CHARACTERS_PER_SECOND'
    WORDS_PER_SECOND = 'WORDS_PER_SECOND'
    TARGET_DURATION = 'TARGET_DURATION'


class ActivationRuleForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mode: ActivationMode = ActivationMode.EVERY_REQUEST
    count: Optional[int] = Field(default=None, ge=1, le=100000)
    range_start: Optional[int] = Field(default=None, ge=1, le=100000)
    range_end: Optional[int] = Field(default=None, ge=1, le=100000)
    probability: float = Field(default=1.0, ge=0, le=1)
    scope: PerturbationScope = PerturbationScope.ALL_TASKS
    plan_item_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def validate_rule(self):
        if self.mode in {ActivationMode.AFTER_PROMPT_COUNT, ActivationMode.EVERY_N_PROMPTS} and self.count is None:
            raise ValueError('This activation mode requires a prompt count.')
        if self.mode == ActivationMode.PROMPT_RANGE:
            if self.range_start is None or self.range_end is None or self.range_start > self.range_end:
                raise ValueError('Prompt range requires an ordered start and end.')
        if self.scope == PerturbationScope.SELECTED_TASKS and not self.plan_item_ids:
            raise ValueError('Selected-task scope requires at least one task.')
        if self.scope == PerturbationScope.ALL_TASKS:
            self.plan_item_ids = []
        return self


class PromptInjectionForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    instruction: str = Field(default='', max_length=50000)
    position: PromptInsertionPosition = PromptInsertionPosition.SYSTEM
    activation: ActivationRuleForm = Field(default_factory=ActivationRuleForm)

    @model_validator(mode='after')
    def validate_prompt(self):
        self.instruction = self.instruction.strip()
        if self.enabled and not self.instruction:
            raise ValueError('Enabled prompt injection requires an instruction.')
        return self


class WarningModalForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    title: str = Field(default='Important reminder', max_length=500)
    message: str = Field(default='LLMs can make mistakes. Double-check important answers.', max_length=10000)
    confirmation_text: str = Field(default='Continue', max_length=200)
    must_acknowledge: bool = True
    cadence: WarningCadence = WarningCadence.BEGINNING
    cadence_value: Optional[int] = Field(default=None, ge=1, le=100000)
    prompt_numbers: list[int] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def validate_warning(self):
        self.title = self.title.strip()
        self.message = self.message.strip()
        self.confirmation_text = self.confirmation_text.strip()
        if self.enabled and (not self.title or not self.message or not self.confirmation_text):
            raise ValueError('Enabled warning modal requires title, message, and confirmation text.')
        if (
            self.cadence
            in {
                WarningCadence.EVERY_N_PROMPTS,
                WarningCadence.ONCE_AFTER_PROMPT,
                WarningCadence.EVERY_N_ACTIVE_MINUTES,
                WarningCadence.AFTER_ACTIVE_MINUTES,
                WarningCadence.ONCE_AFTER_ACTIVE_DELAY,
            }
            and self.cadence_value is None
        ):
            raise ValueError('This warning cadence requires a value.')
        if self.cadence == WarningCadence.PROMPT_LIST:
            if not self.prompt_numbers or any(number < 1 for number in self.prompt_numbers):
                raise ValueError('Prompt-list cadence requires positive prompt numbers.')
            self.prompt_numbers = sorted(set(self.prompt_numbers))
        else:
            self.prompt_numbers = []
        return self


class ResponseTimingForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mode: ResponseTimingMode = ResponseTimingMode.NORMAL
    delay_seconds: Optional[float] = Field(default=None, ge=0.1, le=300)
    show_loading: bool = True
    reveal_style: RevealStyle = RevealStyle.FULL
    target_duration_seconds: Optional[float] = Field(default=None, ge=1, le=600)
    stream_unit: StreamUnit = StreamUnit.CHARACTER
    minimum_chunk_size: int = Field(default=1, ge=1, le=1000)
    maximum_chunk_size: int = Field(default=20, ge=1, le=1000)
    punctuation_pauses: bool = False
    rate_value: Optional[float] = Field(default=None, gt=0, le=5000)
    rate_unit: FastRateUnit = FastRateUnit.CHARACTERS_PER_SECOND

    @model_validator(mode='after')
    def validate_timing(self):
        if self.minimum_chunk_size > self.maximum_chunk_size:
            raise ValueError('Minimum chunk size cannot exceed maximum chunk size.')
        if self.mode == ResponseTimingMode.NORMAL:
            self.delay_seconds = None
            self.target_duration_seconds = None
            self.rate_value = None
        elif self.mode == ResponseTimingMode.DELAYED:
            if self.delay_seconds is None:
                raise ValueError('Delayed reveal requires a delay.')
            self.target_duration_seconds = None
            self.rate_value = None
        elif self.mode == ResponseTimingMode.SLOW:
            if self.target_duration_seconds is None:
                raise ValueError('Slow streaming requires a target duration.')
            self.delay_seconds = None
            self.rate_value = None
        else:
            self.delay_seconds = None
            if self.rate_unit == FastRateUnit.TARGET_DURATION:
                if self.target_duration_seconds is None:
                    raise ValueError('Target-duration fast reveal requires a target duration.')
                self.rate_value = None
            elif self.rate_value is None:
                raise ValueError('Fast reveal requires a rate.')
            else:
                self.target_duration_seconds = None
        return self


class ExperimentConditionForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=500)
    allocation_percent: int = Field(ge=0, le=100)
    enabled: bool = True
    is_control: bool = False
    prompt_injection: PromptInjectionForm = Field(default_factory=PromptInjectionForm)
    warning_modal: WarningModalForm = Field(default_factory=WarningModalForm)
    response_timing: ResponseTimingForm = Field(default_factory=ResponseTimingForm)

    @model_validator(mode='after')
    def validate_condition(self):
        self.name = self.name.strip()
        if self.is_control and (
            self.prompt_injection.enabled
            or self.warning_modal.enabled
            or self.response_timing.mode != ResponseTimingMode.NORMAL
        ):
            raise ValueError('The control condition must use normal, non-perturbed behavior.')
        return self


def default_control_condition() -> ExperimentConditionForm:
    return ExperimentConditionForm(name='Control', allocation_percent=100, enabled=True, is_control=True)


class ExperimentCondition(Base):
    __tablename__ = 'experiment_condition'

    id = Column(Text, primary_key=True)
    source_condition_key = Column(Text, nullable=True)
    plan_id = Column(Text, ForeignKey('experiment_plan.id', ondelete='CASCADE'), nullable=False)
    name = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    allocation_percent = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    is_control = Column(Boolean, nullable=False, default=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('plan_id', 'position', name='uq_experiment_condition_plan_position'),
        Index('ix_experiment_condition_plan_enabled', 'plan_id', 'enabled'),
    )


class ExperimentPromptInjection(Base):
    __tablename__ = 'experiment_prompt_injection'

    condition_id = Column(Text, ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    instruction = Column(Text, nullable=False, default='')
    position = Column(Text, nullable=False, default=PromptInsertionPosition.SYSTEM.value)
    activation_mode = Column(Text, nullable=False, default=ActivationMode.EVERY_REQUEST.value)
    activation_count = Column(Integer, nullable=True)
    range_start = Column(Integer, nullable=True)
    range_end = Column(Integer, nullable=True)
    probability = Column(Float, nullable=False, default=1.0)
    scope = Column(Text, nullable=False, default=PerturbationScope.ALL_TASKS.value)


class ExperimentWarningModal(Base):
    __tablename__ = 'experiment_warning_modal'

    condition_id = Column(Text, ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    title = Column(Text, nullable=False, default='Important reminder')
    message = Column(Text, nullable=False, default='LLMs can make mistakes. Double-check important answers.')
    confirmation_text = Column(Text, nullable=False, default='Continue')
    must_acknowledge = Column(Boolean, nullable=False, default=True)
    cadence = Column(Text, nullable=False, default=WarningCadence.BEGINNING.value)
    cadence_value = Column(Integer, nullable=True)
    prompt_numbers = Column(JSONField, nullable=False, default=list)


class ExperimentResponseTiming(Base):
    __tablename__ = 'experiment_response_timing'

    condition_id = Column(Text, ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True)
    mode = Column(Text, nullable=False, default=ResponseTimingMode.NORMAL.value)
    delay_seconds = Column(Float, nullable=True)
    show_loading = Column(Boolean, nullable=False, default=True)
    reveal_style = Column(Text, nullable=False, default=RevealStyle.FULL.value)
    target_duration_seconds = Column(Float, nullable=True)
    stream_unit = Column(Text, nullable=False, default=StreamUnit.CHARACTER.value)
    minimum_chunk_size = Column(Integer, nullable=False, default=1)
    maximum_chunk_size = Column(Integer, nullable=False, default=20)
    punctuation_pauses = Column(Boolean, nullable=False, default=False)
    rate_value = Column(Float, nullable=True)
    rate_unit = Column(Text, nullable=False, default=FastRateUnit.CHARACTERS_PER_SECOND.value)


class ExperimentConditionTaskScope(Base):
    __tablename__ = 'experiment_condition_task_scope'

    condition_id = Column(Text, ForeignKey('experiment_condition.id', ondelete='CASCADE'), primary_key=True)
    perturbation_type = Column(Text, primary_key=True)
    plan_item_id = Column(Text, ForeignKey('experiment_plan_item.id', ondelete='CASCADE'), primary_key=True)


class ExperimentLLMRequest(Base):
    __tablename__ = 'experiment_llm_request'

    id = Column(Text, primary_key=True)
    experiment_session_id = Column(Text, nullable=False)
    condition_id = Column(Text, nullable=True)
    plan_id = Column(Text, nullable=True)
    plan_version = Column(Integer, nullable=True)
    session_task_id = Column(Text, nullable=True)
    chat_id = Column(Text, nullable=True)
    user_message_id = Column(Text, nullable=True)
    assistant_message_id = Column(Text, nullable=True)
    request_sequence = Column(Integer, nullable=False)
    prompt_number = Column(Integer, nullable=False)
    assignment_identifier = Column(Text, nullable=True)
    prompt_active = Column(Boolean, nullable=False, default=False)
    prompt_draw = Column(Float, nullable=True)
    prompt_randomization_id = Column(Text, nullable=True)
    timing_mode = Column(Text, nullable=False, default=ResponseTimingMode.NORMAL.value)
    timing_parameters = Column(JSONField, nullable=False, default=dict)
    request_at = Column(BigInteger, nullable=False)
    provider_started_at = Column(BigInteger, nullable=True)
    provider_first_token_at = Column(BigInteger, nullable=True)
    provider_completed_at = Column(BigInteger, nullable=True)
    artificial_delay_started_at = Column(BigInteger, nullable=True)
    artificial_delay_ended_at = Column(BigInteger, nullable=True)
    server_first_emit_at = Column(BigInteger, nullable=True)
    server_completed_emit_at = Column(BigInteger, nullable=True)
    client_first_visible_at = Column(BigInteger, nullable=True)
    client_completed_visible_at = Column(BigInteger, nullable=True)
    buffered = Column(Boolean, nullable=False, default=False)
    streaming_completed_normally = Column(Boolean, nullable=True)
    navigated_away = Column(Boolean, nullable=False, default=False)
    status = Column(Text, nullable=False, default='PENDING')
    error_type = Column(Text, nullable=True)
    buffered_output = Column(JSONField, nullable=True)
    reveal_cursor = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('experiment_session_id', 'request_sequence', name='uq_experiment_llm_request_sequence'),
        Index('ix_experiment_llm_request_session_prompt', 'experiment_session_id', 'prompt_number'),
        Index('ix_experiment_llm_request_message', 'assistant_message_id'),
    )


class ExperimentWarningState(Base):
    __tablename__ = 'experiment_warning_state'

    id = Column(Text, primary_key=True)
    experiment_session_id = Column(Text, nullable=False)
    condition_id = Column(Text, nullable=False)
    plan_id = Column(Text, nullable=False)
    active_elapsed_ms = Column(BigInteger, nullable=False, default=0)
    last_trigger_key = Column(Text, nullable=True)
    last_token = Column(Text, nullable=True)
    pending_token = Column(Text, nullable=True)
    pending_reason = Column(Text, nullable=True)
    pending_trigger_key = Column(Text, nullable=True)
    pending_prompt_count = Column(Integer, nullable=True)
    pending_active_elapsed_ms = Column(BigInteger, nullable=True)
    displayed_at = Column(BigInteger, nullable=True)
    acknowledged_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('experiment_session_id', 'condition_id', 'plan_id', name='uq_experiment_warning_state'),
    )


class ExperimentConditionModel(ExperimentConditionForm):
    model_config = ConfigDict(from_attributes=True)

    id: str


def new_warning_state(session_id: str, condition_id: str, plan_id: str) -> ExperimentWarningState:
    now = time.time_ns()
    return ExperimentWarningState(
        id=str(uuid.uuid4()),
        experiment_session_id=session_id,
        condition_id=condition_id,
        plan_id=plan_id,
        active_elapsed_ms=0,
        created_at=now,
        updated_at=now,
    )
