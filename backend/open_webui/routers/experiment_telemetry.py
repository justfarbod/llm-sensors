import json
import time
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_telemetry import (
    ExperimentTelemetryEvent,
    ExperimentTelemetrySummary,
    empty_summary,
)
from open_webui.models.experiments import ExperimentSession, ExperimentState, Experiments
from open_webui.models.experiment_plans import ExperimentSessionTask
from open_webui.models.question_tasks import QuestionTaskQuestion
from open_webui.models.question_submissions import QuestionSubmission
from open_webui.utils.auth import get_verified_user

router = APIRouter()

MAX_BATCH_SIZE = 100
MAX_PAYLOAD_BYTES = 256 * 1024
FORBIDDEN_KEYS = {'text', 'content', 'raw', 'key', 'clipboard', 'clipboard_text', 'password'}
EVENT_TYPES = {
    'keystroke',
    'copy',
    'cut',
    'paste',
    'visibility_change',
    'window_blur',
    'window_focus',
    'focus_away',
    'focus_return',
    'answer_change',
}
KEY_CLASSES = {
    'printable',
    'whitespace',
    'backspace',
    'delete',
    'enter',
    'arrow/navigation',
    'modifier',
    'shortcut',
    'other',
}


def _reject_forbidden_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f'Forbidden telemetry field: {key}')
            _reject_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child)


class ModifierFlags(BaseModel):
    model_config = ConfigDict(extra='forbid')

    ctrl: bool
    shift: bool
    alt: bool
    meta: bool


class TelemetryEventForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    event_id: UUID
    type: str
    timestamp: datetime
    field: Literal['essay', 'question', 'chat', 'unknown']
    session_task_id: Optional[str] = None
    question_id: Optional[str] = None
    submission_id: Optional[str] = None
    key_class: Optional[str] = None
    inter_key_interval_ms: Optional[int] = Field(default=None, ge=0, le=3_600_000)
    hold_duration_ms: Optional[int] = Field(default=None, ge=0, le=600_000)
    modifiers: Optional[ModifierFlags] = None
    text_length: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    line_count: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    visibility: Optional[Literal['hidden', 'visible']] = None
    away_duration_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    control_type: Optional[Literal['single_choice', 'multiple_select', 'fill_blank', 'free_text']] = None
    answered: Optional[bool] = None

    @model_validator(mode='after')
    def validate_event_shape(self):
        if self.timestamp.tzinfo is None:
            raise ValueError('Telemetry timestamps must include a timezone.')
        if self.type not in EVENT_TYPES:
            raise ValueError('Unsupported telemetry event type.')
        present = set(self.model_fields_set) - {'event_id', 'type', 'timestamp', 'field', 'session_task_id', 'question_id', 'submission_id'}
        allowed = {
            'keystroke': {'key_class', 'inter_key_interval_ms', 'hold_duration_ms', 'modifiers'},
            'copy': {'text_length', 'line_count'},
            'cut': {'text_length', 'line_count'},
            'paste': {'text_length', 'line_count'},
            'visibility_change': {'visibility'},
            'window_blur': set(),
            'window_focus': set(),
            'focus_away': set(),
            'focus_return': {'away_duration_ms'},
            'answer_change': {'control_type', 'answered'},
        }[self.type]
        if present - allowed:
            raise ValueError('Event contains fields that are not valid for its type.')
        if self.type == 'keystroke' and (self.key_class not in KEY_CLASSES or self.modifiers is None):
            raise ValueError('Keystroke events require a valid key class and modifier flags.')
        if self.type == 'visibility_change' and self.visibility is None:
            raise ValueError('Visibility events require a visibility state.')
        if self.type == 'focus_return' and self.away_duration_ms is None:
            raise ValueError('Focus return events require an away duration.')
        if self.type == 'answer_change' and (self.field != 'question' or self.control_type is None or self.answered is None):
            raise ValueError('Question answer changes require control type and answered state.')
        if self.field == 'question' and (not self.session_task_id or not self.question_id):
            raise ValueError('Question telemetry requires task and question context.')
        return self


class TelemetryBatchForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    experiment_session_id: str = Field(min_length=1, max_length=100)
    events: list[TelemetryEventForm] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @model_validator(mode='before')
    @classmethod
    def validate_privacy_and_size(cls, value):
        _reject_forbidden_keys(value)
        if len(json.dumps(value, separators=(',', ':'), default=str).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError('Telemetry batch exceeds maximum payload size.')
        return value


class TelemetryStatusResponse(BaseModel):
    enabled: bool
    reason: Optional[str] = None
    experiment_session_id: Optional[str] = None
    state: Optional[ExperimentState] = None
    allowed_contexts: Optional[list[Literal['essay', 'question', 'chat']]] = None


def _payload(event: TelemetryEventForm):
    return event.model_dump(
        exclude={'event_id', 'type', 'timestamp', 'field', 'session_task_id', 'question_id', 'submission_id'},
        exclude_none=True,
        mode='json',
    )


def _apply_to_summary(summary: ExperimentTelemetrySummary, event: TelemetryEventForm):
    if event.type == 'keystroke':
        summary.total_keystrokes += 1
        if event.inter_key_interval_ms is not None:
            summary.inter_key_interval_total_ms += event.inter_key_interval_ms
            summary.inter_key_interval_sample_count += 1
            summary.avg_inter_key_interval_ms = round(
                summary.inter_key_interval_total_ms / summary.inter_key_interval_sample_count, 2
            )
            if event.inter_key_interval_ms > 2_000:
                summary.pause_count += 1
                summary.longest_pause_ms = max(summary.longest_pause_ms, event.inter_key_interval_ms)
        if event.hold_duration_ms is not None:
            summary.key_hold_duration_total_ms += event.hold_duration_ms
            summary.key_hold_duration_sample_count += 1
            summary.avg_key_hold_duration_ms = round(
                summary.key_hold_duration_total_ms / summary.key_hold_duration_sample_count, 2
            )
    elif event.type == 'copy':
        summary.copy_count += 1
    elif event.type == 'cut':
        summary.cut_count += 1
    elif event.type == 'paste':
        summary.paste_count += 1
        summary.total_pasted_chars += event.text_length or 0
    elif event.type == 'focus_away':
        summary.tab_switch_count += 1
    elif event.type == 'focus_return':
        summary.total_time_away_ms += event.away_duration_ms or 0
    summary.updated_at = int(time.time_ns())


@router.get('/status', response_model=TelemetryStatusResponse, response_model_exclude_none=True)
async def telemetry_status(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    if user.role == 'admin':
        return TelemetryStatusResponse(enabled=False, reason='not_applicable')
    state, session, _ = await Experiments.get_current(user, db=db)
    if state != ExperimentState.IN_PROGRESS or session is None:
        return TelemetryStatusResponse(enabled=False, reason='not_applicable')
    return TelemetryStatusResponse(
        enabled=True,
        experiment_session_id=session.id,
        state=state,
        allowed_contexts=['essay', 'question', 'chat'],
    )


@router.post('/events')
async def telemetry_events(
    form: TelemetryBatchForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if user.role == 'admin':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Administrators cannot submit telemetry.')
    session = (
        (
            await db.execute(
                select(ExperimentSession).where(
                    ExperimentSession.id == form.experiment_session_id,
                    ExperimentSession.user_id == user.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Experiment session is not owned by user.')
    if session.state != ExperimentState.IN_PROGRESS.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Experiment writing session is not active.')

    for session_task_id in {
        event.session_task_id for event in form.events if event.session_task_id
    }:
        valid_task = (
            await db.execute(
                select(ExperimentSessionTask.id).where(
                    ExperimentSessionTask.id == session_task_id,
                    ExperimentSessionTask.experiment_session_id == session.id,
                )
            )
        ).first()
        if not valid_task:
            raise HTTPException(status_code=403, detail='Telemetry task context is not assigned to this session.')

    question_contexts = [
        (event.session_task_id, event.question_id, event.submission_id)
        for event in form.events
        if event.field == 'question'
    ]
    for session_task_id, question_id, submission_id in set(question_contexts):
        valid = (
            await db.execute(
                select(QuestionTaskQuestion.id)
                .join(ExperimentSessionTask, ExperimentSessionTask.question_task_id == QuestionTaskQuestion.task_id)
                .where(
                    ExperimentSessionTask.id == session_task_id,
                    ExperimentSessionTask.experiment_session_id == session.id,
                    QuestionTaskQuestion.id == question_id,
                )
            )
        ).first()
        if not valid:
            raise HTTPException(status_code=403, detail='Question telemetry context is not assigned to this session.')
        if submission_id:
            valid_submission = (
                await db.execute(
                    select(QuestionSubmission.id).where(
                        QuestionSubmission.id == submission_id,
                        QuestionSubmission.session_task_id == session_task_id,
                        QuestionSubmission.user_id == user.id,
                    )
                )
            ).first()
            if not valid_submission:
                raise HTTPException(status_code=403, detail='Question telemetry submission is not owned by user.')

    summary = (
        (
            await db.execute(
                select(ExperimentTelemetrySummary).where(ExperimentTelemetrySummary.experiment_session_id == session.id)
            )
        )
        .scalars()
        .first()
    )
    if summary is None:
        summary = empty_summary(session.id, user.id)
        db.add(summary)

    event_ids = [str(event.event_id) for event in form.events]
    existing_ids = set(
        (await db.execute(select(ExperimentTelemetryEvent.id).where(ExperimentTelemetryEvent.id.in_(event_ids))))
        .scalars()
        .all()
    )
    stored = 0
    seen_ids = set(existing_ids)
    for event in form.events:
        event_id = str(event.event_id)
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        telemetry_event = ExperimentTelemetryEvent(
            id=event_id,
            user_id=user.id,
            experiment_session_id=session.id,
            event_type=event.type,
            event_time=int(event.timestamp.timestamp() * 1_000_000_000),
            field_context=event.field,
            payload_json=_payload(event),
            created_at=int(time.time_ns()),
            session_task_id=event.session_task_id,
            question_id=event.question_id,
            submission_id=event.submission_id,
        )
        db.add(telemetry_event)
        _apply_to_summary(summary, event)
        stored += 1

    await db.commit()
    return {'accepted': stored, 'duplicates': len(form.events) - stored}
