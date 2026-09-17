import json
import time
from datetime import datetime
from typing import Annotated, Any, Literal, Optional
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import get_async_session
from open_webui.models.experiment_telemetry import (
    ExperimentTelemetryEvent,
    ExperimentTelemetryExtensionPresence,
    ExperimentTelemetrySummary,
    empty_summary,
)
from open_webui.config import (
    EXPERIMENT_TELEMETRY_EXTENSION_ENABLED,
    EXPERIMENT_TELEMETRY_EXTENSION_ID,
    EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION,
    EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN,
    EXPERIMENT_TELEMETRY_EXTENSION_STALE_SECONDS,
    EXPERIMENT_TELEMETRY_SCHEMA_VERSION,
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
    'tab_snapshot',
    'tab_created',
    'tab_updated',
    'tab_activated',
    'tab_highlighted',
    'tab_moved',
    'tab_attached',
    'tab_detached',
    'tab_replaced',
    'tab_removed',
    'window_focus_changed',
    'telemetry_loss',
}
TAB_EVENT_TYPES = {
    'tab_snapshot',
    'tab_created',
    'tab_updated',
    'tab_activated',
    'tab_highlighted',
    'tab_moved',
    'tab_attached',
    'tab_detached',
    'tab_replaced',
    'tab_removed',
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
ChromeId = Annotated[int, Field(ge=-1, le=2_147_483_647)]
BoundedFieldName = Annotated[str, Field(min_length=1, max_length=128)]


def _reject_forbidden_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise ValueError(f'Forbidden telemetry field: {key}')
            if str(key).lower() == 'incognito' and child is not False:
                raise ValueError('Incognito telemetry is forbidden.')
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


class MutedInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')

    muted: bool
    reason: Optional[Literal['user', 'capture', 'extension']] = None
    extension_id: Optional[str] = Field(default=None, max_length=128)


class TabSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')

    tab_id: ChromeId
    window_id: ChromeId
    index: int = Field(ge=-1, le=1_000_000)
    opener_tab_id: Optional[ChromeId] = None
    group_id: Optional[ChromeId] = None
    split_view_id: Optional[ChromeId] = None
    active: bool
    highlighted: bool
    pinned: bool
    incognito: bool
    audible: Optional[bool] = None
    auto_discardable: Optional[bool] = None
    discarded: Optional[bool] = None
    frozen: Optional[bool] = None
    muted_info: Optional[MutedInfo] = None
    status: Optional[Literal['unloaded', 'loading', 'complete']] = None
    url: Optional[str] = Field(default=None, max_length=16_384)
    pending_url: Optional[str] = Field(default=None, max_length=16_384)
    title: Optional[str] = Field(default=None, max_length=8_192)
    fav_icon_url: Optional[str] = Field(default=None, max_length=16_384)
    width: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    height: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    last_accessed: Optional[float] = Field(default=None, ge=0, le=10_000_000_000_000_000)
    session_id: Optional[str] = Field(default=None, max_length=256)
    truncated_fields: list[Literal['url', 'pending_url', 'title', 'fav_icon_url']] = Field(
        default_factory=list, max_length=4
    )

    @model_validator(mode='after')
    def exclude_incognito(self):
        if self.incognito:
            raise ValueError('Incognito tabs are not accepted for experiment telemetry.')
        return self


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
    key_value: Optional[str] = Field(default=None, min_length=1, max_length=40)
    inter_key_interval_ms: Optional[int] = Field(default=None, ge=0, le=3_600_000)
    hold_duration_ms: Optional[int] = Field(default=None, ge=0, le=600_000)
    modifiers: Optional[ModifierFlags] = None
    text_length: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    line_count: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    visibility: Optional[Literal['hidden', 'visible']] = None
    away_duration_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    control_type: Optional[Literal['single_choice', 'multiple_select', 'fill_blank', 'free_text']] = None
    answered: Optional[bool] = None
    browser_session_id: Optional[UUID] = None
    sequence: Optional[int] = Field(default=None, ge=1, le=9_007_199_254_740_991)
    tab: Optional[TabSnapshot] = None
    snapshot_phase: Optional[Literal['initial', 'final']] = None
    changed_fields: Optional[list[BoundedFieldName]] = Field(default=None, max_length=64)
    from_index: Optional[int] = Field(default=None, ge=-1, le=1_000_000)
    to_index: Optional[int] = Field(default=None, ge=-1, le=1_000_000)
    old_window_id: Optional[ChromeId] = None
    new_window_id: Optional[ChromeId] = None
    replaced_tab_id: Optional[ChromeId] = None
    is_window_closing: Optional[bool] = None
    window_id: Optional[ChromeId] = None
    window_focused: Optional[bool] = None
    dropped_event_count: Optional[int] = Field(default=None, ge=1, le=10_000_000)

    @model_validator(mode='after')
    def validate_event_shape(self):
        if self.timestamp.tzinfo is None:
            raise ValueError('Telemetry timestamps must include a timezone.')
        if self.type not in EVENT_TYPES:
            raise ValueError('Unsupported telemetry event type.')
        present = set(self.model_fields_set) - {'event_id', 'type', 'timestamp', 'field', 'session_task_id', 'question_id', 'submission_id'}
        allowed = {
            'keystroke': {'key_class', 'inter_key_interval_ms', 'hold_duration_ms', 'modifiers', 'key_value'},
            'copy': {'text_length', 'line_count'},
            'cut': {'text_length', 'line_count'},
            'paste': {'text_length', 'line_count'},
            'visibility_change': {'visibility'},
            'window_blur': set(),
            'window_focus': set(),
            'focus_away': set(),
            'focus_return': {'away_duration_ms'},
            'answer_change': {'control_type', 'answered'},
            'tab_snapshot': {'browser_session_id', 'sequence', 'tab', 'snapshot_phase'},
            'tab_created': {'browser_session_id', 'sequence', 'tab'},
            'tab_updated': {'browser_session_id', 'sequence', 'tab', 'changed_fields'},
            'tab_activated': {'browser_session_id', 'sequence', 'tab'},
            'tab_highlighted': {'browser_session_id', 'sequence', 'tab'},
            'tab_moved': {'browser_session_id', 'sequence', 'tab', 'from_index', 'to_index'},
            'tab_attached': {'browser_session_id', 'sequence', 'tab', 'old_window_id', 'new_window_id'},
            'tab_detached': {'browser_session_id', 'sequence', 'tab', 'old_window_id', 'new_window_id'},
            'tab_replaced': {'browser_session_id', 'sequence', 'tab', 'replaced_tab_id'},
            'tab_removed': {'browser_session_id', 'sequence', 'tab', 'is_window_closing'},
            'window_focus_changed': {
                'browser_session_id', 'sequence', 'window_id', 'window_focused'
            },
            'telemetry_loss': {'browser_session_id', 'sequence', 'dropped_event_count'},
        }[self.type]
        if present - allowed:
            raise ValueError('Event contains fields that are not valid for its type.')
        if self.type == 'keystroke' and (
            self.key_class not in KEY_CLASSES or self.modifiers is None or not self.key_value
        ):
            raise ValueError('Keystroke events require a valid key class, key value, and modifier flags.')
        if self.type == 'visibility_change' and self.visibility is None:
            raise ValueError('Visibility events require a visibility state.')
        if self.type == 'focus_return' and self.away_duration_ms is None:
            raise ValueError('Focus return events require an away duration.')
        if self.type == 'answer_change' and (self.field != 'question' or self.control_type is None or self.answered is None):
            raise ValueError('Question answer changes require control type and answered state.')
        if self.field == 'question' and (not self.session_task_id or not self.question_id):
            raise ValueError('Question telemetry requires task and question context.')
        if self.type in TAB_EVENT_TYPES:
            if self.browser_session_id is None or self.sequence is None or self.tab is None:
                raise ValueError('Tab telemetry requires browser session, sequence, and tab snapshot.')
        if self.type == 'tab_snapshot' and self.snapshot_phase is None:
            raise ValueError('Tab snapshots require an initial or final phase.')
        if self.type == 'tab_updated' and self.changed_fields is None:
            raise ValueError('Tab updates require a changed-fields list.')
        if self.type == 'tab_moved' and (self.from_index is None or self.to_index is None):
            raise ValueError('Tab move telemetry requires original and destination positions.')
        if self.type == 'tab_attached' and self.new_window_id is None:
            raise ValueError('Tab attachment telemetry requires the destination window.')
        if self.type == 'tab_detached' and self.old_window_id is None:
            raise ValueError('Tab detachment telemetry requires the original window.')
        if self.type == 'tab_replaced' and self.replaced_tab_id is None:
            raise ValueError('Tab replacement telemetry requires the replaced tab ID.')
        if self.type == 'tab_removed' and self.is_window_closing is None:
            raise ValueError('Tab removal telemetry requires the window-closing state.')
        if self.type == 'window_focus_changed' and (
            self.browser_session_id is None
            or self.sequence is None
            or self.window_id is None
            or self.window_focused is None
        ):
            raise ValueError('Window focus telemetry requires browser session, sequence, window, and focus state.')
        if self.type == 'telemetry_loss' and (
            self.browser_session_id is None or self.sequence is None or self.dropped_event_count is None
        ):
            raise ValueError('Telemetry loss events require browser session, sequence, and dropped count.')
        return self


class TelemetryBatchForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    experiment_session_id: str = Field(min_length=1, max_length=100)
    schema_version: int = Field(default=1, ge=1, le=EXPERIMENT_TELEMETRY_SCHEMA_VERSION)
    events: list[TelemetryEventForm] = Field(min_length=1, max_length=MAX_BATCH_SIZE)

    @model_validator(mode='before')
    @classmethod
    def validate_privacy_and_size(cls, value):
        _reject_forbidden_keys(value)
        if len(json.dumps(value, separators=(',', ':'), default=str).encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError('Telemetry batch exceeds maximum payload size.')
        return value

    @model_validator(mode='after')
    def validate_schema_version(self):
        if self.schema_version < 2 and any(event.type in TAB_EVENT_TYPES for event in self.events):
            raise ValueError('Tab telemetry requires schema version 2.')
        return self


class ExtensionHeartbeatForm(BaseModel):
    model_config = ConfigDict(extra='forbid')

    extension_version: str = Field(min_length=1, max_length=32)
    extension_id: str = Field(min_length=1, max_length=64)
    schema_version: int = Field(ge=EXPERIMENT_TELEMETRY_SCHEMA_VERSION, le=EXPERIMENT_TELEMETRY_SCHEMA_VERSION)
    tabs_permission: bool
    incognito_allowed: bool
    origin: str = Field(min_length=1, max_length=2048)


def _version_tuple(value: str):
    try:
        parts = [int(part) for part in value.split('.')]
    except ValueError:
        return ()
    if not 1 <= len(parts) <= 4 or any(part < 0 or part > 65_535 for part in parts):
        return ()
    return tuple(parts + [0] * (4 - len(parts)))


def extension_presence_ready(presence: Optional[ExperimentTelemetryExtensionPresence]) -> bool:
    if not EXPERIMENT_TELEMETRY_EXTENSION_ENABLED:
        return True
    if presence is None:
        return False
    age_ns = time.time_ns() - presence.last_seen_at
    return (
        age_ns <= EXPERIMENT_TELEMETRY_EXTENSION_STALE_SECONDS * 1_000_000_000
        and presence.tabs_permission
        and not presence.incognito_allowed
        and presence.schema_version == EXPERIMENT_TELEMETRY_SCHEMA_VERSION
        and bool(_version_tuple(presence.extension_version))
        and bool(_version_tuple(EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION))
        and _version_tuple(presence.extension_version) >= _version_tuple(EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION)
        and presence.origin.rstrip('/') == EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN
    )


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


@router.post('/extension/heartbeat')
async def extension_heartbeat(
    form: ExtensionHeartbeatForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not EXPERIMENT_TELEMETRY_EXTENSION_ENABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Experiment telemetry extension is disabled.')
    if user.role == 'admin':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Administrators cannot report telemetry.')
    state, session, _ = await Experiments.get_current(user, db=db)
    if state not in {
        ExperimentState.TOPIC_REQUIRED,
        ExperimentState.TASK_REQUIRED,
        ExperimentState.IN_PROGRESS,
    } or session is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Experiment extension is not required now.')
    try:
        normalized_origin = f'{urlsplit(form.origin).scheme}://{urlsplit(form.origin).netloc}'.rstrip('/')
    except ValueError as error:
        raise HTTPException(status_code=422, detail='Invalid extension origin.') from error
    if (
        not normalized_origin
        or (
            urlsplit(normalized_origin).scheme != 'https'
            and not (
                urlsplit(normalized_origin).scheme == 'http'
                and urlsplit(normalized_origin).hostname in {'localhost', '127.0.0.1', '::1'}
            )
        )
        or not urlsplit(normalized_origin).netloc
        or normalized_origin != form.origin.rstrip('/')
    ):
        raise HTTPException(
            status_code=422,
            detail='Extension origin must be one HTTPS origin, or HTTP loopback for development.',
        )
    if EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN and normalized_origin != EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN:
        raise HTTPException(status_code=403, detail='Extension origin does not match this deployment.')
    if EXPERIMENT_TELEMETRY_EXTENSION_ID and form.extension_id != EXPERIMENT_TELEMETRY_EXTENSION_ID:
        raise HTTPException(status_code=403, detail='Unexpected telemetry extension identity.')
    now = time.time_ns()
    presence = await db.get(ExperimentTelemetryExtensionPresence, session.id)
    if presence is None:
        presence = ExperimentTelemetryExtensionPresence(
            experiment_session_id=session.id,
            user_id=user.id,
            connected_at=now,
        )
        db.add(presence)
    presence.extension_version = form.extension_version
    presence.schema_version = form.schema_version
    presence.tabs_permission = form.tabs_permission
    presence.incognito_allowed = form.incognito_allowed
    presence.origin = normalized_origin
    presence.last_seen_at = now
    await db.commit()
    return {
        'ready': extension_presence_ready(presence),
        'experiment_session_id': session.id,
        'state': state,
    }


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
            schema_version=form.schema_version,
        )
        db.add(telemetry_event)
        _apply_to_summary(summary, event)
        stored += 1

    await db.commit()
    return {'accepted': stored, 'duplicates': len(form.events) - stored}
