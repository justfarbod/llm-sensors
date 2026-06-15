import csv
import hashlib
import hmac
import io
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Integer, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import get_async_session
from open_webui.models.chat_messages import ChatMessage, _token_columns
from open_webui.models.essays import Essay, EssayTopics
from open_webui.models.experiment_telemetry import ExperimentTelemetrySummary, ExperimentTelemetrySummaryModel
from open_webui.models.experiments import ACTIVE_STATES, ExperimentSession, ExperimentState
from open_webui.models.groups import Group, GroupMember, Groups
from open_webui.models.users import User
from open_webui.utils.auth import get_admin_user

router = APIRouter()
NS = 1_000_000_000


class DashboardFilters(BaseModel):
    group_id: Optional[str] = None
    topic_id: Optional[str] = None
    date_from: Optional[int] = None
    date_to: Optional[int] = None
    state: Optional[str] = None
    completed: Optional[bool] = None


class ExportRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=10000)
    format: Literal['csv', 'json'] = 'csv'
    anonymized: bool = False


def _anonymous_id(user_id: str) -> str:
    digest = hmac.new(str(WEBUI_SECRET_KEY).encode(), user_id.encode(), hashlib.sha256).hexdigest()
    return f'P-{digest[:12].upper()}'


def _seconds(value: Optional[int]) -> Optional[int]:
    return value // NS if value else None


def _duration(start: Optional[int], end: Optional[int]) -> Optional[float]:
    return round((end - start) / NS, 2) if start and end and end >= start else None


def _date_bucket(value: int) -> str:
    return datetime.fromtimestamp(value / NS).strftime('%Y-%m-%d')


def _enabled_groups(groups):
    return [group for group in groups if (group.data or {}).get('config', {}).get('experiment_mode_enabled', False)]


async def _group_context(db: AsyncSession):
    groups = _enabled_groups(await Groups.get_all_groups(db=db))
    return groups, {group.id: group for group in groups}


def _apply_session_filters(stmt, filters: DashboardFilters):
    if filters.group_id:
        stmt = stmt.where(ExperimentSession.group_id == filters.group_id)
    if filters.topic_id:
        stmt = stmt.where(ExperimentSession.topic_id == filters.topic_id)
    if filters.date_from:
        stmt = stmt.where(ExperimentSession.created_at >= filters.date_from * NS)
    if filters.date_to:
        stmt = stmt.where(ExperimentSession.created_at < (filters.date_to + 1) * NS)
    if filters.state and filters.state != 'NOT_STARTED':
        stmt = stmt.where(ExperimentSession.state == filters.state)
    if filters.completed is True:
        stmt = stmt.where(ExperimentSession.state == ExperimentState.COMPLETED.value)
    elif filters.completed is False:
        stmt = stmt.where(ExperimentSession.state != ExperimentState.COMPLETED.value)
    return stmt


async def _sessions(db: AsyncSession, filters: DashboardFilters):
    stmt = _apply_session_filters(select(ExperimentSession), filters).order_by(ExperimentSession.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def _usage_by_session(db: AsyncSession, session_ids: list[str]):
    if not session_ids:
        return {}

    connection = await db.connection()
    input_tokens, output_tokens = _token_columns(connection.dialect.name)
    start_seconds = cast(ExperimentSession.writing_started_at / NS, Integer)
    end_ns = case(
        (ExperimentSession.essay_submitted_at.isnot(None), ExperimentSession.essay_submitted_at),
        (ExperimentSession.completed_at.isnot(None), ExperimentSession.completed_at),
        else_=int(time.time_ns()),
    )
    end_seconds = cast(end_ns / NS, Integer)

    stmt = (
        select(
            ExperimentSession.id.label('session_id'),
            func.count(case((ChatMessage.role == 'user', 1))).label('prompts'),
            func.count(case((ChatMessage.role == 'assistant', 1))).label('responses'),
            func.coalesce(func.sum(case((ChatMessage.role == 'assistant', input_tokens), else_=0)), 0).label(
                'input_tokens'
            ),
            func.coalesce(func.sum(case((ChatMessage.role == 'assistant', output_tokens), else_=0)), 0).label(
                'output_tokens'
            ),
        )
        .outerjoin(
            ChatMessage,
            and_(
                ChatMessage.user_id == ExperimentSession.user_id,
                ExperimentSession.writing_started_at.isnot(None),
                ChatMessage.created_at >= start_seconds,
                ChatMessage.created_at <= end_seconds,
            ),
        )
        .where(ExperimentSession.id.in_(session_ids))
        .group_by(ExperimentSession.id)
    )
    rows = (await db.execute(stmt)).all()
    return {
        row.session_id: {
            'prompts': row.prompts or 0,
            'responses': row.responses or 0,
            'input_tokens': row.input_tokens or 0,
            'output_tokens': row.output_tokens or 0,
            'total_tokens': (row.input_tokens or 0) + (row.output_tokens or 0),
        }
        for row in rows
    }


async def _users_by_ids(db: AsyncSession, user_ids: list[str]):
    if not user_ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    return {row.id: row for row in rows}


async def _essays_by_ids(db: AsyncSession, essay_ids: list[str]):
    if not essay_ids:
        return {}
    rows = (await db.execute(select(Essay).where(Essay.id.in_(essay_ids)))).scalars().all()
    return {row.id: row for row in rows}


async def _telemetry_by_session(db: AsyncSession, session_ids: list[str]):
    if not session_ids:
        return {}
    rows = (
        await db.execute(
            select(ExperimentTelemetrySummary).where(
                ExperimentTelemetrySummary.experiment_session_id.in_(session_ids)
            )
        )
    ).scalars().all()
    return {row.experiment_session_id: row for row in rows}


def _telemetry_payload(summary):
    return (
        ExperimentTelemetrySummaryModel.model_validate(summary).model_dump()
        if summary
        else ExperimentTelemetrySummaryModel().model_dump()
    )


def _session_row(session, group, user, essay, usage, telemetry=None):
    telemetry_payload = _telemetry_payload(telemetry)
    return {
        'session_id': session.id,
        'user_id': session.user_id,
        'participant_id': _anonymous_id(session.user_id),
        'name': user.name if user else None,
        'username': user.username if user else None,
        'email': user.email if user else None,
        'group_id': session.group_id,
        'group_name': group.name if group else None,
        'topic_id': session.topic_id,
        'topic_title': session.topic_title,
        'state': session.state,
        'consented': session.consented_at is not None,
        'pre_survey_completed': session.pre_survey is not None,
        'essay_submitted': session.essay_id is not None,
        'post_survey_completed': session.post_survey is not None,
        'session_start_time': _seconds(session.created_at),
        'session_completion_time': _seconds(session.completed_at),
        'session_duration': _duration(session.created_at, session.completed_at),
        'writing_duration': _duration(session.writing_started_at, session.essay_submitted_at),
        'post_survey_duration': _duration(session.essay_submitted_at, session.post_survey_submitted_at),
        'prompts': usage.get('prompts', 0),
        'responses': usage.get('responses', 0),
        'input_tokens': usage.get('input_tokens', 0),
        'output_tokens': usage.get('output_tokens', 0),
        'total_tokens': usage.get('total_tokens', 0),
        'essay_id': session.essay_id,
        'essay_word_count': essay.word_count if essay else None,
        'essay_character_count': essay.character_count if essay else None,
        **telemetry_payload,
        'telemetry_summary': telemetry_payload,
    }


async def _participant_rows(db: AsyncSession, filters: DashboardFilters):
    groups, group_map = await _group_context(db)
    group_ids = [group.id for group in groups]
    if filters.group_id:
        group_ids = [group_id for group_id in group_ids if group_id == filters.group_id]
    if not group_ids:
        return []

    stmt = (
        select(GroupMember, User, ExperimentSession)
        .join(User, User.id == GroupMember.user_id)
        .outerjoin(
            ExperimentSession,
            and_(
                ExperimentSession.user_id == GroupMember.user_id,
                ExperimentSession.group_id == GroupMember.group_id,
            ),
        )
        .where(GroupMember.group_id.in_(group_ids), User.role != 'admin')
    )
    if filters.topic_id:
        stmt = stmt.where(ExperimentSession.topic_id == filters.topic_id)
    if filters.date_from:
        stmt = stmt.where(ExperimentSession.created_at >= filters.date_from * NS)
    if filters.date_to:
        stmt = stmt.where(ExperimentSession.created_at < (filters.date_to + 1) * NS)
    if filters.state == 'NOT_STARTED':
        stmt = stmt.where(ExperimentSession.id.is_(None))
    elif filters.state:
        stmt = stmt.where(ExperimentSession.state == filters.state)
    if filters.completed is True:
        stmt = stmt.where(ExperimentSession.state == ExperimentState.COMPLETED.value)
    elif filters.completed is False:
        stmt = stmt.where(
            or_(ExperimentSession.id.is_(None), ExperimentSession.state != ExperimentState.COMPLETED.value)
        )

    records = (await db.execute(stmt)).all()
    sessions = [record[2] for record in records if record[2]]
    usage = await _usage_by_session(db, [session.id for session in sessions])
    telemetry = await _telemetry_by_session(db, [session.id for session in sessions])
    essays = await _essays_by_ids(db, [session.essay_id for session in sessions if session.essay_id])
    rows = []
    for member, user, session in records:
        if session:
            rows.append(
                _session_row(
                    session,
                    group_map.get(member.group_id),
                    user,
                    essays.get(session.essay_id),
                    usage.get(session.id, {}),
                    telemetry.get(session.id),
                )
            )
        else:
            rows.append(
                {
                    'session_id': None,
                    'user_id': user.id,
                    'participant_id': _anonymous_id(user.id),
                    'name': user.name,
                    'username': user.username,
                    'email': user.email,
                    'group_id': member.group_id,
                    'group_name': group_map.get(member.group_id).name if group_map.get(member.group_id) else None,
                    'topic_id': None,
                    'topic_title': None,
                    'state': 'NOT_STARTED',
                    'consented': False,
                    'pre_survey_completed': False,
                    'essay_submitted': False,
                    'post_survey_completed': False,
                    'session_start_time': None,
                    'session_completion_time': None,
                    'session_duration': None,
                    'writing_duration': None,
                    'post_survey_duration': None,
                    'prompts': 0,
                    'responses': 0,
                    'input_tokens': 0,
                    'output_tokens': 0,
                    'total_tokens': 0,
                    'essay_id': None,
                    'essay_word_count': None,
                    'essay_character_count': None,
                    **_telemetry_payload(None),
                    'telemetry_summary': _telemetry_payload(None),
                }
            )
    return rows


def _paginate_rows(rows, page: int, limit: int, search: Optional[str], order_by: str, direction: str):
    if search:
        query = search.lower()
        rows = [
            row
            for row in rows
            if query
            in ' '.join(
                str(row.get(key) or '').lower()
                for key in ('participant_id', 'name', 'username', 'email', 'group_name', 'topic_title', 'state')
            )
        ]
    reverse = direction != 'asc'
    rows.sort(key=lambda row: (row.get(order_by) is None, row.get(order_by) or ''), reverse=reverse)
    total = len(rows)
    start = (page - 1) * limit
    return rows[start : start + limit], total


@router.get('/filters')
async def filters(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    groups, _ = await _group_context(db)
    topics = await EssayTopics.get_topics(db=db)
    return {
        'groups': [{'id': group.id, 'name': group.name} for group in groups],
        'topics': [{'id': topic.id, 'title': topic.title} for topic in topics],
        'states': ['NOT_STARTED'] + [state.value for state in ExperimentState if state != ExperimentState.NOT_APPLICABLE],
    }


@router.get('/overview')
async def overview(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    sessions = await _sessions(db, dashboard_filters)
    participants = await _participant_rows(db, dashboard_filters)
    state_counts = Counter(session.state for session in sessions)
    dates = Counter(_date_bucket(session.created_at) for session in sessions)
    complete = [session for session in sessions if session.completed_at]
    durations_by_date = defaultdict(list)
    for session in complete:
        duration = _duration(session.created_at, session.completed_at)
        if duration is not None:
            durations_by_date[_date_bucket(session.created_at)].append(duration)

    def average(values):
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 2) if values else None

    return {
        'metrics': {
            'total_sessions': len(sessions),
            'active_sessions': sum(session.state in {state.value for state in ACTIVE_STATES} for session in sessions),
            'completed_sessions': len(complete),
            'assigned_participants': len({participant['user_id'] for participant in participants}),
            'consented': sum(session.consented_at is not None for session in sessions),
            'pre_survey_completed': sum(session.pre_survey is not None for session in sessions),
            'essays_submitted': sum(session.essay_id is not None for session in sessions),
            'post_survey_completed': sum(session.post_survey is not None for session in sessions),
            'completion_rate': round((len(complete) / len(sessions)) * 100, 1) if sessions else 0,
            'average_session_duration': average(_duration(session.created_at, session.completed_at) for session in complete),
            'average_time_to_essay': average(
                _duration(session.writing_started_at, session.essay_submitted_at) for session in sessions
            ),
            'average_essay_to_post_survey': average(
                _duration(session.essay_submitted_at, session.post_survey_submitted_at) for session in sessions
            ),
        },
        'funnel': [
            {'label': 'Sessions', 'value': len(sessions)},
            {'label': 'Consented', 'value': sum(session.consented_at is not None for session in sessions)},
            {'label': 'Pre-survey', 'value': sum(session.pre_survey is not None for session in sessions)},
            {'label': 'Essay submitted', 'value': sum(session.essay_id is not None for session in sessions)},
            {'label': 'Completed', 'value': len(complete)},
        ],
        'sessions_over_time': [{'label': key, 'value': value} for key, value in sorted(dates.items())],
        'states': [{'label': key, 'value': value} for key, value in state_counts.items()],
        'average_duration_over_time': [
            {'label': key, 'value': round(sum(values) / len(values), 2)}
            for key, values in sorted(durations_by_date.items())
        ],
    }


@router.get('/participants')
async def participants(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = None,
    order_by: str = 'session_start_time',
    direction: Literal['asc', 'desc'] = 'desc',
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    rows = await _participant_rows(db, dashboard_filters)
    allowed = {
        'participant_id',
        'name',
        'email',
        'group_name',
        'topic_title',
        'state',
        'session_start_time',
        'session_duration',
        'prompts',
        'total_tokens',
        'essay_word_count',
    }
    items, total = _paginate_rows(rows, page, limit, search, order_by if order_by in allowed else 'session_start_time', direction)
    return {'items': items, 'total': total, 'page': page, 'limit': limit}


@router.get('/sessions/{session_id}')
async def session_detail(
    session_id: str,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    session = (await db.execute(select(ExperimentSession).where(ExperimentSession.id == session_id))).scalars().first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Experiment session not found.')
    group = (await db.execute(select(Group).where(Group.id == session.group_id))).scalars().first()
    participant = (await db.execute(select(User).where(User.id == session.user_id))).scalars().first()
    essay = (
        (await db.execute(select(Essay).where(Essay.id == session.essay_id))).scalars().first()
        if session.essay_id
        else None
    )
    usage = (await _usage_by_session(db, [session.id])).get(session.id, {})
    telemetry = (await _telemetry_by_session(db, [session.id])).get(session.id)
    row = _session_row(session, group, participant, essay, usage, telemetry)
    row.update(
        {
            'topic_question': session.topic_question,
            'pre_survey': session.pre_survey,
            'post_survey': session.post_survey,
            'timeline': {
                'created_at': _seconds(session.created_at),
                'consented_at': _seconds(session.consented_at),
                'pre_survey_submitted_at': _seconds(session.pre_survey_submitted_at),
                'topic_shown_at': _seconds(session.topic_shown_at),
                'writing_started_at': _seconds(session.writing_started_at),
                'essay_submitted_at': _seconds(session.essay_submitted_at),
                'post_survey_submitted_at': _seconds(session.post_survey_submitted_at),
                'completed_at': _seconds(session.completed_at),
                'logout_at': None,
            },
            'essay': (
                {
                    'id': essay.id,
                    'submitted_at': _seconds(essay.created_at),
                    'word_count': essay.word_count,
                    'character_count': essay.character_count,
                }
                if essay
                else None
            ),
        }
    )
    return row


@router.get('/essays')
async def essays(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = None,
    order_by: str = 'submitted_at',
    direction: Literal['asc', 'desc'] = 'desc',
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    sessions = [session for session in await _sessions(db, dashboard_filters) if session.essay_id]
    users = await _users_by_ids(db, [session.user_id for session in sessions])
    essay_map = await _essays_by_ids(db, [session.essay_id for session in sessions])
    _, group_map = await _group_context(db)
    rows = []
    for session in sessions:
        essay = essay_map.get(session.essay_id)
        participant = users.get(session.user_id)
        if not essay:
            continue
        rows.append(
            {
                'essay_id': essay.id,
                'session_id': session.id,
                'user_id': session.user_id,
                'participant_id': _anonymous_id(session.user_id),
                'name': participant.name if participant else None,
                'username': participant.username if participant else None,
                'email': participant.email if participant else None,
                'group_id': session.group_id,
                'group_name': group_map.get(session.group_id).name if group_map.get(session.group_id) else None,
                'topic_id': session.topic_id,
                'topic_title': session.topic_title,
                'state': session.state,
                'word_count': essay.word_count,
                'character_count': essay.character_count,
                'submitted_at': _seconds(essay.created_at),
            }
        )
    allowed = {'participant_id', 'name', 'group_name', 'topic_title', 'state', 'word_count', 'character_count', 'submitted_at'}
    items, total = _paginate_rows(rows, page, limit, search, order_by if order_by in allowed else 'submitted_at', direction)
    return {'items': items, 'total': total, 'page': page, 'limit': limit}


@router.get('/usage')
async def usage(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    sessions = await _sessions(db, dashboard_filters)
    usage_map = await _usage_by_session(db, [session.id for session in sessions])
    users = await _users_by_ids(db, [session.user_id for session in sessions])
    _, group_map = await _group_context(db)
    group_totals = defaultdict(lambda: {'prompts': 0, 'tokens': 0})
    topic_totals = defaultdict(lambda: {'prompts': 0, 'tokens': 0})
    participant_rows = []
    for session in sessions:
        item = usage_map.get(session.id, {})
        group_name = group_map.get(session.group_id).name if group_map.get(session.group_id) else session.group_id
        group_totals[group_name]['prompts'] += item.get('prompts', 0)
        group_totals[group_name]['tokens'] += item.get('total_tokens', 0)
        topic_totals[session.topic_title]['prompts'] += item.get('prompts', 0)
        topic_totals[session.topic_title]['tokens'] += item.get('total_tokens', 0)
        participant_rows.append(
            {
                'session_id': session.id,
                'participant_id': _anonymous_id(session.user_id),
                'name': users.get(session.user_id).name if users.get(session.user_id) else None,
                **item,
            }
        )
    completed_rows = [usage_map.get(session.id, {}) for session in sessions if session.essay_id]

    def average(key):
        return round(sum(row.get(key, 0) for row in completed_rows) / len(completed_rows), 2) if completed_rows else 0

    return {
        'metrics': {
            'total_prompts': sum(row.get('prompts', 0) for row in usage_map.values()),
            'total_responses': sum(row.get('responses', 0) for row in usage_map.values()),
            'total_tokens': sum(row.get('total_tokens', 0) for row in usage_map.values()),
            'average_prompts_per_completed_essay': average('prompts'),
            'average_tokens_per_completed_essay': average('total_tokens'),
        },
        'prompt_distribution': _histogram([row.get('prompts', 0) for row in usage_map.values()]),
        'token_distribution': _histogram([row.get('total_tokens', 0) for row in usage_map.values()]),
        'by_group': [{'label': key, **value} for key, value in group_totals.items()],
        'by_topic': [{'label': key, **value} for key, value in topic_totals.items()],
        'top_participants': sorted(participant_rows, key=lambda row: row.get('total_tokens', 0), reverse=True)[:20],
    }


def _histogram(values: list[int], buckets: int = 8):
    if not values:
        return []
    maximum = max(values)
    width = max(1, (maximum + buckets - 1) // buckets)
    counts = Counter(min(value // width, buckets - 1) for value in values)
    return [
        {'label': f'{index * width}-{((index + 1) * width) - 1}', 'value': counts.get(index, 0)}
        for index in range(buckets)
    ]


@router.get('/essay-stats')
async def essay_stats(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    sessions = [session for session in await _sessions(db, dashboard_filters) if session.essay_id]
    essay_map = await _essays_by_ids(db, [session.essay_id for session in sessions])
    usage_map = await _usage_by_session(db, [session.id for session in sessions])
    _, group_map = await _group_context(db)
    words = [essay_map[session.essay_id].word_count or 0 for session in sessions if session.essay_id in essay_map]
    by_group = defaultdict(list)
    by_topic = defaultdict(list)
    by_ai_usage = defaultdict(list)
    scatter = []
    for session in sessions:
        essay = essay_map.get(session.essay_id)
        if not essay:
            continue
        group_name = group_map.get(session.group_id).name if group_map.get(session.group_id) else session.group_id
        by_group[group_name].append(essay.word_count or 0)
        by_topic[session.topic_title].append(essay.word_count or 0)
        prompts = usage_map.get(session.id, {}).get('prompts', 0)
        if prompts == 0:
            usage_level = 'No prompts'
        elif prompts <= 3:
            usage_level = 'Low (1-3)'
        elif prompts <= 8:
            usage_level = 'Medium (4-8)'
        else:
            usage_level = 'High (9+)'
        by_ai_usage[usage_level].append(essay.word_count or 0)
        if len(scatter) < 200:
            usage_item = usage_map.get(session.id, {})
            scatter.append(
                {
                    'session_id': session.id,
                    'prompts': usage_item.get('prompts', 0),
                    'tokens': usage_item.get('total_tokens', 0),
                    'duration': _duration(session.writing_started_at, session.essay_submitted_at),
                    'word_count': essay.word_count or 0,
                }
            )
    return {
        'metrics': {
            'average_word_count': round(sum(words) / len(words), 2) if words else None,
            'median_word_count': statistics.median(words) if words else None,
            'minimum_word_count': min(words) if words else None,
            'maximum_word_count': max(words) if words else None,
        },
        'distribution': _histogram(words),
        'by_group': [{'label': key, 'value': round(sum(values) / len(values), 2)} for key, values in by_group.items()],
        'by_topic': [{'label': key, 'value': round(sum(values) / len(values), 2)} for key, values in by_topic.items()],
        'by_ai_usage': [
            {'label': key, 'value': round(sum(values) / len(values), 2)} for key, values in by_ai_usage.items()
        ],
        'comparisons': scatter,
    }


def _distribution(sessions, field: str, survey_name: str):
    values = [
        getattr(session, survey_name).get(field)
        if getattr(session, survey_name).get(field) is not None
        else 'Not available'
        for session in sessions
        if getattr(session, survey_name)
    ]
    counts = Counter(values)
    total = len(values)
    return [
        {'label': str(key), 'count': value, 'percentage': round((value / total) * 100, 1) if total else 0}
        for key, value in counts.items()
    ]


@router.get('/surveys')
async def surveys(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    dashboard_filters = DashboardFilters(**locals())
    sessions = await _sessions(db, dashboard_filters)
    responses = [
        {
            'session_id': session.id,
            'participant_id': _anonymous_id(session.user_id),
            'state': session.state,
            'pre_survey_completed': session.pre_survey is not None,
            'post_survey_completed': session.post_survey is not None,
            'comments': session.post_survey.get('comments') if session.post_survey else None,
        }
        for session in sessions
        if session.pre_survey or session.post_survey
    ]
    start = (page - 1) * limit
    return {
        'pre': {
            field: _distribution(sessions, field, 'pre_survey')
            for field in ('school_class', 'ai_familiarity', 'ai_schoolwork_frequency', 'essay_writing_confidence', 'age_range')
        },
        'post': {
            field: _distribution(sessions, field, 'post_survey')
            for field in ('ai_helpfulness', 'essay_satisfaction', 'ai_improvement', 'chat_ease')
        },
        'responses': {
            'items': responses[start : start + limit],
            'total': len(responses),
            'page': page,
            'limit': limit,
        },
    }


def _csv_stream(headers, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction='ignore')
    writer.writeheader()
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def _json_stream(rows):
    yield '[\n'
    for index, row in enumerate(rows):
        yield (',' if index else '') + json.dumps(row, ensure_ascii=False) + '\n'
    yield ']\n'


def _export_response(rows, form: ExportRequest, filename: str):
    if form.anonymized:
        for row in rows:
            row.pop('user_id', None)
            row.pop('name', None)
            row.pop('username', None)
            row.pop('email', None)
    if form.format == 'csv':
        for row in rows:
            row.pop('telemetry_summary', None)
        headers = list(rows[0].keys()) if rows else []
        return StreamingResponse(
            _csv_stream(headers, rows),
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{filename}.csv"'},
        )
    return StreamingResponse(
        _json_stream(rows),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}.json"'},
    )


@router.post('/export/participants')
async def export_participants(
    form: ExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    sessions = list(
        (await db.execute(select(ExperimentSession).where(ExperimentSession.id.in_(form.ids)))).scalars().all()
    )
    users = await _users_by_ids(db, [session.user_id for session in sessions])
    essays = await _essays_by_ids(db, [session.essay_id for session in sessions if session.essay_id])
    usage = await _usage_by_session(db, [session.id for session in sessions])
    telemetry = await _telemetry_by_session(db, [session.id for session in sessions])
    _, groups = await _group_context(db)
    rows = [
        _session_row(
            session,
            groups.get(session.group_id),
            users.get(session.user_id),
            essays.get(session.essay_id),
            usage.get(session.id, {}),
            telemetry.get(session.id),
        )
        for session in sessions
    ]
    return _export_response(rows, form, 'experiment-participants')


@router.post('/export/essays')
async def export_essays(
    form: ExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    records = (
        await db.execute(
            select(ExperimentSession, Essay, User, Group)
            .join(Essay, Essay.id == ExperimentSession.essay_id)
            .join(User, User.id == ExperimentSession.user_id)
            .join(Group, Group.id == ExperimentSession.group_id)
            .where(Essay.id.in_(form.ids))
        )
    ).all()
    rows = [
        {
            'session_id': session.id,
            'user_id': session.user_id,
            'participant_id': _anonymous_id(session.user_id),
            'name': participant.name,
            'username': participant.username,
            'email': participant.email,
            'group_id': group.id,
            'group_name': group.name,
            'topic_id': session.topic_id,
            'topic_title': session.topic_title,
            'essay_text': essay.content,
            'word_count': essay.word_count,
            'character_count': essay.character_count,
            'submitted_at': _seconds(essay.created_at),
            'session_state': session.state,
        }
        for session, essay, participant, group in records
    ]
    return _export_response(rows, form, 'experiment-essays')


@router.post('/export/surveys')
async def export_surveys(
    form: ExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    sessions = list(
        (await db.execute(select(ExperimentSession).where(ExperimentSession.id.in_(form.ids)))).scalars().all()
    )
    users = await _users_by_ids(db, [session.user_id for session in sessions])
    rows = [
        {
            'session_id': session.id,
            'user_id': session.user_id,
            'participant_id': _anonymous_id(session.user_id),
            'name': users.get(session.user_id).name if users.get(session.user_id) else None,
            'username': users.get(session.user_id).username if users.get(session.user_id) else None,
            'email': users.get(session.user_id).email if users.get(session.user_id) else None,
            'group_id': session.group_id,
            'topic_id': session.topic_id,
            'state': session.state,
            'pre_survey': json.dumps(session.pre_survey, ensure_ascii=False) if form.format == 'csv' else session.pre_survey,
            'post_survey': json.dumps(session.post_survey, ensure_ascii=False) if form.format == 'csv' else session.post_survey,
        }
        for session in sessions
    ]
    return _export_response(rows, form, 'experiment-surveys')
