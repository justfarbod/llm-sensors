"""Task budget configuration and durable, independently accounted generations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Text

from open_webui.internal.db import Base

PromptLimit = Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class LLMPromptBudget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['TASK', 'PER_QUESTION'] = 'TASK'
    limit: PromptLimit | None = 100
    question_limits: dict[str, PromptLimit] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_mode(self):
        if self.mode == 'TASK':
            if self.limit is None or self.question_limits:
                raise ValueError('A task budget requires a limit and no question limits.')
        elif self.limit is not None or not self.question_limits:
            raise ValueError('A per-question budget requires question limits and a null task limit.')
        return self


def default_prompt_budget():
    return LLMPromptBudget().model_dump()


def remap_prompt_budget(value, question_ids):
    budget = LLMPromptBudget.model_validate(value or default_prompt_budget()).model_dump()
    if budget['mode'] == 'PER_QUESTION':
        if not set(budget['question_limits']).issubset(question_ids):
            raise ValueError('Prompt budget references an unavailable question.')
        budget['question_limits'] = {question_ids[key]: limit for key, limit in budget['question_limits'].items()}
    return budget


class ExperimentPromptBucket(Base):
    __tablename__ = 'experiment_prompt_bucket'
    id = Column(Text, primary_key=True)
    session_task_id = Column(Text, ForeignKey('experiment_session_task.id', ondelete='CASCADE'), nullable=False)
    scope_key = Column(Text, nullable=False)
    used = Column(Integer, nullable=False, default=0)
    pending = Column(Integer, nullable=False, default=0)


class ExperimentPromptReservation(Base):
    __tablename__ = 'experiment_prompt_reservation'
    id = Column(Text, primary_key=True)
    bucket_id = Column(Text, ForeignKey('experiment_prompt_bucket.id', ondelete='CASCADE'), nullable=False, index=True)
    status = Column(Text, nullable=False, default='PENDING')
    lease_expires_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)
