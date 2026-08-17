import csv
import hashlib
import hmac
import io
import json
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from enum import Enum
from typing import AsyncIterator, Literal, Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Integer, and_, case, cast, func, inspect as sqlalchemy_inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import get_async_session
from open_webui.models.chat_messages import ChatMessage, _token_columns
from open_webui.models.chats import Chat, ChatFile
from open_webui.models.essays import Essay, EssayTopic, EssayTopicAssignment, EssayTopics
from open_webui.models.experiment_telemetry import (
    ExperimentTelemetryEvent,
    ExperimentTelemetryExtensionPresence,
    ExperimentTelemetrySummary,
    ExperimentTelemetrySummaryModel,
)
from open_webui.models.experiment_perturbations import (
    ExperimentCondition,
    ExperimentConditionTaskScope,
    ExperimentLLMRequest,
    ExperimentPromptInjection,
    ExperimentResponseTiming,
    ExperimentWarningState,
    ExperimentWarningModal,
)
from open_webui.models.experiments import ACTIVE_STATES, ExperimentSession, ExperimentState
from open_webui.models.experiment_plans import (
    ExperimentPlan,
    ExperimentPlanItem,
    ExperimentPlanItemTopic,
    ExperimentSessionTask,
)
from open_webui.models.feedbacks import Feedback
from open_webui.models.files import File
from open_webui.models.question_submissions import (
    QuestionGradingAttempt,
    QuestionResponse,
    QuestionResponseBlank,
    QuestionResponseChoice,
    QuestionScoreOverride,
    QuestionSubmission,
    QuestionSubmissions,
)
from open_webui.models.question_tasks import (
    GradingStatus,
    QuestionBlank,
    QuestionBlankAcceptedAnswer,
    QuestionChoice,
    QuestionFreeTextConfig,
    QuestionTask,
    QuestionTaskQuestion,
    QuestionTasks,
)
from open_webui.models.survey_submissions import (
    SurveyResponse,
    SurveyResponseChoice,
    SurveySubmission,
)
from open_webui.models.survey_tasks import SurveyChoice, SurveyQuestion, SurveyTask
from open_webui.models.groups import Group, GroupMember, Groups
from open_webui.models.users import User
from open_webui.utils.auth import get_admin_user
from open_webui.utils.essay_text import essay_text_metrics
from open_webui.tasks import create_task
from open_webui.utils.question_grading import grade_response

router = APIRouter()
NS = 1_000_000_000
TAB_TELEMETRY_EVENT_TYPES = {
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


class FullSessionExportRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=10000)
    anonymized: bool = True


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def _anonymize_identity_fields(value):
    if isinstance(value, list):
        return [_anonymize_identity_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    anonymized = {}
    for key, item in value.items():
        if key in {'user_id', 'admin_id'} and item:
            anonymized[key] = _anonymous_id(str(item))
        else:
            anonymized[key] = _anonymize_identity_fields(item)
    return anonymized


def _row_payload(row, anonymized: bool = False):
    if row is None:
        return None
    payload = {
        column.key: _json_value(getattr(row, column.key)) for column in sqlalchemy_inspect(row).mapper.column_attrs
    }
    return _anonymize_identity_fields(payload) if anonymized else payload


def _participant_payload(participant: Optional[User], anonymized: bool):
    if participant is None:
        return None
    participant_id = _anonymous_id(participant.id)
    payload = {
        'id': participant_id if anonymized else participant.id,
        'participant_id': participant_id,
        'role': participant.role,
        'last_active_at': participant.last_active_at,
        'created_at': participant.created_at,
        'updated_at': participant.updated_at,
    }
    if not anonymized:
        payload.update(
            {
                'name': participant.name,
                'username': participant.username,
                'email': participant.email,
            }
        )
    return payload


def _group_payload(group: Optional[Group], anonymized: bool):
    if group is None:
        return None
    payload = {
        key: _json_value(getattr(group, key))
        for key in ('id', 'name', 'description', 'data', 'meta', 'permissions', 'created_at', 'updated_at')
    }
    return _anonymize_identity_fields(payload) if anonymized else payload


def _derived_payload(row: dict, anonymized: bool):
    payload = _json_value(row)
    if anonymized:
        payload = _anonymize_identity_fields(payload)
        for key in ('name', 'username', 'email'):
            payload.pop(key, None)
    return payload


async def _file_metadata_payload(
    db: AsyncSession,
    file_id: Optional[str],
    cache: dict[str, Optional[dict]],
    anonymized: bool,
):
    if not file_id:
        return None
    if file_id not in cache:
        row = await db.get(File, file_id)
        if row is None:
            cache[file_id] = None
        else:
            meta = _json_value(row.meta) if isinstance(row.meta, dict) else {}
            if anonymized:
                meta = _anonymize_identity_fields(meta)
            cache[file_id] = {
                'id': row.id,
                'filename': row.filename,
                'hash': row.hash,
                'content_type': meta.get('content_type'),
                'size': meta.get('size'),
                'meta': meta,
                'created_at': row.created_at,
                'updated_at': row.updated_at,
            }
    return cache[file_id]


def _embedded_file_ids(files) -> set[str]:
    ids: set[str] = set()
    if not isinstance(files, list):
        return ids
    for item in files:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict):
            value = item.get('file_id') or item.get('id')
            if isinstance(value, str):
                ids.add(value)
    return ids


async def _question_task_export(
    db: AsyncSession,
    task_id: Optional[str],
    task_cache: dict[str, Optional[dict]],
    file_cache: dict[str, Optional[dict]],
    anonymized: bool,
):
    if not task_id:
        return None
    if task_id in task_cache:
        return task_cache[task_id]
    task = await db.get(QuestionTask, task_id)
    if task is None:
        task_cache[task_id] = None
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
                    .order_by(QuestionChoice.question_id, QuestionChoice.position)
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
                    .order_by(QuestionBlank.question_id, QuestionBlank.position)
                )
            )
            .scalars()
            .all()
        )
        if question_ids
        else []
    )
    blank_ids = [blank.id for blank in blanks]
    accepted_answers = (
        list(
            (
                await db.execute(
                    select(QuestionBlankAcceptedAnswer)
                    .where(QuestionBlankAcceptedAnswer.blank_id.in_(blank_ids))
                    .order_by(QuestionBlankAcceptedAnswer.blank_id, QuestionBlankAcceptedAnswer.position)
                )
            )
            .scalars()
            .all()
        )
        if blank_ids
        else []
    )
    free_text_configs = (
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
    choice_map: dict[str, list] = defaultdict(list)
    blank_map: dict[str, list] = defaultdict(list)
    answer_map: dict[str, list] = defaultdict(list)
    for choice in choices:
        choice_map[choice.question_id].append(choice)
    for blank in blanks:
        blank_map[blank.question_id].append(blank)
    for answer in accepted_answers:
        answer_map[answer.blank_id].append(answer)
    config_map = {config.question_id: config for config in free_text_configs}
    payload = {
        'record': _row_payload(task),
        'questions': [
            {
                'record': _row_payload(question),
                'choices': [_row_payload(choice) for choice in choice_map[question.id]],
                'blanks': [
                    {
                        'record': _row_payload(blank),
                        'accepted_answers': [_row_payload(answer) for answer in answer_map[blank.id]],
                    }
                    for blank in blank_map[question.id]
                ],
                'free_text_config': _row_payload(config_map.get(question.id)),
                'image': await _file_metadata_payload(db, question.image_file_id, file_cache, anonymized),
            }
            for question in questions
        ],
    }
    task_cache[task_id] = payload
    return payload


async def _survey_task_export(db: AsyncSession, task_id: Optional[str], task_cache: dict[str, Optional[dict]]):
    if not task_id:
        return None
    if task_id in task_cache:
        return task_cache[task_id]
    task = await db.get(SurveyTask, task_id)
    if task is None:
        task_cache[task_id] = None
        return None
    questions = list(
        (
            await db.execute(
                select(SurveyQuestion).where(SurveyQuestion.task_id == task_id).order_by(SurveyQuestion.position)
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
                    select(SurveyChoice)
                    .where(SurveyChoice.question_id.in_(question_ids))
                    .order_by(SurveyChoice.question_id, SurveyChoice.position)
                )
            )
            .scalars()
            .all()
        )
        if question_ids
        else []
    )
    choice_map: dict[str, list] = defaultdict(list)
    for choice in choices:
        choice_map[choice.question_id].append(choice)
    payload = {
        'record': _row_payload(task),
        'questions': [
            {
                'record': _row_payload(question),
                'choices': [_row_payload(choice) for choice in choice_map[question.id]],
            }
            for question in questions
        ],
    }
    task_cache[task_id] = payload
    return payload


async def _topic_export(db: AsyncSession, topic_id: Optional[str], topic_cache: dict[str, Optional[dict]]):
    if not topic_id:
        return None
    if topic_id not in topic_cache:
        topic_cache[topic_id] = _row_payload(await db.get(EssayTopic, topic_id))
    return topic_cache[topic_id]


async def _plan_export(
    db: AsyncSession,
    plan_id: Optional[str],
    assigned_condition_id: Optional[str],
    caches: dict,
):
    if not plan_id:
        return None, {}
    plan = await db.get(ExperimentPlan, plan_id)
    if plan is None:
        return None, {}
    items = list(
        (
            await db.execute(
                select(ExperimentPlanItem)
                .where(ExperimentPlanItem.plan_id == plan_id)
                .order_by(ExperimentPlanItem.position)
            )
        )
        .scalars()
        .all()
    )
    item_ids = [item.id for item in items]
    pools = (
        list(
            (
                await db.execute(
                    select(ExperimentPlanItemTopic)
                    .where(ExperimentPlanItemTopic.plan_item_id.in_(item_ids))
                    .order_by(ExperimentPlanItemTopic.plan_item_id, ExperimentPlanItemTopic.topic_id)
                )
            )
            .scalars()
            .all()
        )
        if item_ids
        else []
    )
    pool_map: dict[str, list] = defaultdict(list)
    for pool in pools:
        pool_map[pool.plan_item_id].append(pool)
    item_payloads = []
    for item in items:
        item_payloads.append(
            {
                'record': _row_payload(item),
                'topic': await _topic_export(db, item.essay_topic_id, caches['topics']),
                'topic_pool': [
                    {
                        'record': _row_payload(pool),
                        'topic': await _topic_export(db, pool.topic_id, caches['topics']),
                    }
                    for pool in pool_map[item.id]
                ],
                'question_task': await _question_task_export(
                    db, item.question_task_id, caches['question_tasks'], caches['files'], caches['anonymized']
                ),
                'survey_task': await _survey_task_export(db, item.survey_task_id, caches['survey_tasks']),
            }
        )
    item_map = {item['record']['id']: item for item in item_payloads}
    conditions = list(
        (
            await db.execute(
                select(ExperimentCondition)
                .where(ExperimentCondition.plan_id == plan_id)
                .order_by(ExperimentCondition.position)
            )
        )
        .scalars()
        .all()
    )
    condition_ids = [condition.id for condition in conditions]
    scopes = (
        list(
            (
                await db.execute(
                    select(ExperimentConditionTaskScope)
                    .where(ExperimentConditionTaskScope.condition_id.in_(condition_ids))
                    .order_by(
                        ExperimentConditionTaskScope.condition_id,
                        ExperimentConditionTaskScope.perturbation_type,
                        ExperimentConditionTaskScope.plan_item_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if condition_ids
        else []
    )
    scope_map: dict[str, list] = defaultdict(list)
    for scope in scopes:
        scope_map[scope.condition_id].append(scope)
    condition_payloads = []
    for condition in conditions:
        condition_payloads.append(
            {
                'record': _row_payload(condition),
                'assigned_to_session': condition.id == assigned_condition_id,
                'prompt_injection': _row_payload(await db.get(ExperimentPromptInjection, condition.id)),
                'warning_modal': _row_payload(await db.get(ExperimentWarningModal, condition.id)),
                'response_timing': _row_payload(await db.get(ExperimentResponseTiming, condition.id)),
                'task_scopes': [
                    {
                        'record': _row_payload(scope),
                        'plan_item': item_map.get(scope.plan_item_id),
                    }
                    for scope in scope_map[condition.id]
                ],
            }
        )
    return (
        {
            'record': _row_payload(plan),
            'assigned_condition_id': assigned_condition_id,
            'items': item_payloads,
            'conditions': condition_payloads,
        },
        item_map,
    )


async def _question_submission_export(
    db: AsyncSession,
    session_task: ExperimentSessionTask,
    question_task: Optional[dict],
    anonymized: bool,
):
    submission = (
        (await db.execute(select(QuestionSubmission).where(QuestionSubmission.session_task_id == session_task.id)))
        .scalars()
        .first()
    )
    if submission is None:
        return None
    responses = list(
        (await db.execute(select(QuestionResponse).where(QuestionResponse.submission_id == submission.id)))
        .scalars()
        .all()
    )
    response_ids = [response.id for response in responses]
    selected = (
        list(
            (
                await db.execute(
                    select(QuestionResponseChoice)
                    .where(QuestionResponseChoice.response_id.in_(response_ids))
                    .order_by(QuestionResponseChoice.response_id, QuestionResponseChoice.choice_id)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    blanks = (
        list(
            (
                await db.execute(
                    select(QuestionResponseBlank)
                    .where(QuestionResponseBlank.response_id.in_(response_ids))
                    .order_by(QuestionResponseBlank.response_id, QuestionResponseBlank.blank_id)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    attempts = (
        list(
            (
                await db.execute(
                    select(QuestionGradingAttempt)
                    .where(QuestionGradingAttempt.response_id.in_(response_ids))
                    .order_by(QuestionGradingAttempt.created_at)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    overrides = (
        list(
            (
                await db.execute(
                    select(QuestionScoreOverride)
                    .where(QuestionScoreOverride.response_id.in_(response_ids))
                    .order_by(QuestionScoreOverride.created_at)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    selected_map: dict[str, list] = defaultdict(list)
    blank_map: dict[str, list] = defaultdict(list)
    attempt_map: dict[str, list] = defaultdict(list)
    override_map: dict[str, list] = defaultdict(list)
    for row in selected:
        selected_map[row.response_id].append(row)
    for row in blanks:
        blank_map[row.response_id].append(row)
    for row in attempts:
        attempt_map[row.response_id].append(row)
    for row in overrides:
        override_map[row.response_id].append(row)
    question_map = {item['record']['id']: item for item in (question_task or {}).get('questions', [])}
    choice_map = {choice['id']: choice for question in question_map.values() for choice in question.get('choices', [])}
    blank_definition_map = {
        blank['record']['id']: blank for question in question_map.values() for blank in question.get('blanks', [])
    }
    responses.sort(
        key=lambda response: (question_map.get(response.question_id) or {}).get('record', {}).get('position', 0)
    )
    return {
        'record': _row_payload(submission, anonymized),
        'responses': [
            {
                'record': _row_payload(response, anonymized),
                'question': question_map.get(response.question_id),
                'selected_choices': [
                    {
                        'record': _row_payload(row),
                        'choice': choice_map.get(row.choice_id),
                    }
                    for row in selected_map[response.id]
                ],
                'blank_answers': [
                    {
                        'record': _row_payload(row),
                        'blank': blank_definition_map.get(row.blank_id),
                    }
                    for row in blank_map[response.id]
                ],
                'grading_attempts': [_row_payload(row) for row in attempt_map[response.id]],
                'score_overrides': [_row_payload(row, anonymized) for row in override_map[response.id]],
            }
            for response in responses
        ],
    }


async def _survey_submission_export(
    db: AsyncSession,
    session_task: ExperimentSessionTask,
    survey_task: Optional[dict],
    anonymized: bool,
):
    submission = (
        (await db.execute(select(SurveySubmission).where(SurveySubmission.session_task_id == session_task.id)))
        .scalars()
        .first()
    )
    if submission is None:
        return None
    responses = list(
        (await db.execute(select(SurveyResponse).where(SurveyResponse.submission_id == submission.id))).scalars().all()
    )
    response_ids = [response.id for response in responses]
    selections = (
        list(
            (
                await db.execute(
                    select(SurveyResponseChoice)
                    .where(SurveyResponseChoice.response_id.in_(response_ids))
                    .order_by(SurveyResponseChoice.response_id, SurveyResponseChoice.choice_id)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    selection_map: dict[str, list] = defaultdict(list)
    for selection in selections:
        selection_map[selection.response_id].append(selection)
    question_map = {item['record']['id']: item for item in (survey_task or {}).get('questions', [])}
    choice_map = {choice['id']: choice for question in question_map.values() for choice in question.get('choices', [])}
    responses.sort(
        key=lambda response: (question_map.get(response.question_id) or {}).get('record', {}).get('position', 0)
    )
    return {
        'record': _row_payload(submission, anonymized),
        'responses': [
            {
                'record': _row_payload(response),
                'question': question_map.get(response.question_id),
                'selected_choices': [
                    {
                        'record': _row_payload(selection),
                        'choice': choice_map.get(selection.choice_id),
                    }
                    for selection in selection_map[response.id]
                ],
            }
            for response in responses
        ],
    }


async def _session_tasks_export(
    db: AsyncSession,
    tasks: list[ExperimentSessionTask],
    item_map: dict[str, dict],
    caches: dict,
    anonymized: bool,
):
    payloads = []
    for task in tasks:
        question_task = await _question_task_export(
            db, task.question_task_id, caches['question_tasks'], caches['files'], anonymized
        )
        survey_task = await _survey_task_export(db, task.survey_task_id, caches['survey_tasks'])
        essay = await db.get(Essay, task.essay_id) if task.essay_id else None
        payloads.append(
            {
                'record': _row_payload(task),
                'plan_item': item_map.get(task.plan_item_id),
                'topic': await _topic_export(db, task.essay_topic_id, caches['topics']),
                'question_task': question_task,
                'survey_task': survey_task,
                'essay': _row_payload(essay, anonymized),
                'question_submission': (
                    await _question_submission_export(db, task, question_task, anonymized)
                    if task.question_task_id
                    else None
                ),
                'survey_submission': (
                    await _survey_submission_export(db, task, survey_task, anonymized) if task.survey_task_id else None
                ),
            }
        )
    return payloads


async def _chats_export(
    db: AsyncSession,
    session: ExperimentSession,
    task_ids: list[str],
    anonymized: bool,
    file_cache: dict[str, Optional[dict]],
):
    task_clause = Chat.experiment_session_task_id.in_(task_ids) if task_ids else False
    chats = list(
        (
            await db.execute(
                select(Chat)
                .where(or_(Chat.experiment_session_id == session.id, task_clause))
                .order_by(Chat.created_at, Chat.id)
            )
        )
        .scalars()
        .all()
    )
    chat_ids = [chat.id for chat in chats]
    if not chat_ids:
        return []
    messages = list(
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id.in_(chat_ids))
                .order_by(ChatMessage.chat_id, ChatMessage.created_at, ChatMessage.id)
            )
        )
        .scalars()
        .all()
    )
    chat_files = list(
        (
            await db.execute(
                select(ChatFile).where(ChatFile.chat_id.in_(chat_ids)).order_by(ChatFile.created_at, ChatFile.id)
            )
        )
        .scalars()
        .all()
    )
    feedback_rows = list(
        (
            await db.execute(
                select(Feedback).where(Feedback.user_id == session.user_id).order_by(Feedback.created_at, Feedback.id)
            )
        )
        .scalars()
        .all()
    )
    feedback_rows = [row for row in feedback_rows if isinstance(row.meta, dict) and row.meta.get('chat_id') in chat_ids]
    message_map: dict[str, list] = defaultdict(list)
    file_link_map: dict[str, list] = defaultdict(list)
    feedback_map: dict[str, list] = defaultdict(list)
    for message in messages:
        message_map[message.chat_id].append(message)
    for link in chat_files:
        file_link_map[link.chat_id].append(link)
    for feedback in feedback_rows:
        feedback_map[feedback.meta.get('chat_id')].append(feedback)
    payloads = []
    for chat in chats:
        record = _row_payload(chat, anonymized)
        record.pop('share_id', None)
        file_ids = {link.file_id for link in file_link_map[chat.id]}
        for message in message_map[chat.id]:
            file_ids.update(_embedded_file_ids(message.files))
        embedded_metadata = []
        for file_id in sorted(file_ids):
            metadata = await _file_metadata_payload(db, file_id, file_cache, anonymized)
            if metadata is not None:
                embedded_metadata.append(metadata)
        payloads.append(
            {
                'record': record,
                'messages': [_row_payload(message, anonymized) for message in message_map[chat.id]],
                'attachments': [
                    {
                        'record': _row_payload(link, anonymized),
                        'file': await _file_metadata_payload(db, link.file_id, file_cache, anonymized),
                    }
                    for link in file_link_map[chat.id]
                ],
                'embedded_file_metadata': embedded_metadata,
                'feedback': [_row_payload(feedback, anonymized) for feedback in feedback_map[chat.id]],
            }
        )
    return payloads


async def _full_session_payload(db: AsyncSession, session: ExperimentSession, anonymized: bool):
    participant = await db.get(User, session.user_id)
    group = await db.get(Group, session.group_id)
    membership = (
        (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == session.group_id,
                    GroupMember.user_id == session.user_id,
                )
            )
        )
        .scalars()
        .first()
    )
    tasks = list(
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
    caches = {'question_tasks': {}, 'survey_tasks': {}, 'topics': {}, 'files': {}, 'anonymized': anonymized}
    plan, item_map = await _plan_export(db, session.plan_id, session.condition_id, caches)
    task_payloads = await _session_tasks_export(db, tasks, item_map, caches, anonymized)
    legacy_essay = await db.get(Essay, session.essay_id) if session.essay_id else None
    primary_task_essay = None
    for task in tasks:
        if task.essay_id:
            primary_task_essay = await db.get(Essay, task.essay_id)
            if primary_task_essay is not None:
                break
    usage = (await _usage_by_session(db, [session.id])).get(session.id, {})
    telemetry_summary = (await _telemetry_by_session(db, [session.id])).get(session.id)
    derived = _session_row(
        session,
        group,
        participant,
        legacy_essay or primary_task_essay,
        usage,
        telemetry_summary,
        next((task for task in tasks if task.task_type == 'ESSAY'), None),
    )
    telemetry_events = list(
        (
            await db.execute(
                select(ExperimentTelemetryEvent)
                .where(ExperimentTelemetryEvent.experiment_session_id == session.id)
                .order_by(ExperimentTelemetryEvent.event_time, ExperimentTelemetryEvent.id)
            )
        )
        .scalars()
        .all()
    )
    telemetry_presence = await db.get(ExperimentTelemetryExtensionPresence, session.id)
    llm_requests = list(
        (
            await db.execute(
                select(ExperimentLLMRequest)
                .where(ExperimentLLMRequest.experiment_session_id == session.id)
                .order_by(ExperimentLLMRequest.request_sequence, ExperimentLLMRequest.id)
            )
        )
        .scalars()
        .all()
    )
    warning_states = list(
        (
            await db.execute(
                select(ExperimentWarningState)
                .where(ExperimentWarningState.experiment_session_id == session.id)
                .order_by(ExperimentWarningState.created_at, ExperimentWarningState.id)
            )
        )
        .scalars()
        .all()
    )
    topic_assignment = (
        (
            await db.execute(
                select(EssayTopicAssignment).where(
                    EssayTopicAssignment.user_id == session.user_id,
                    EssayTopicAssignment.group_id == session.group_id,
                )
            )
        )
        .scalars()
        .first()
    )
    session_topic_id = session.topic_id or (topic_assignment.topic_id if topic_assignment else None)
    return {
        'session': _row_payload(session, anonymized),
        'derived_summary': _derived_payload(derived, anonymized),
        'participant': _participant_payload(participant, anonymized),
        'group': _group_payload(group, anonymized),
        'membership': _row_payload(membership, anonymized),
        'topic': await _topic_export(db, session_topic_id, caches['topics']),
        'topic_assignment': (
            {
                'record': _row_payload(topic_assignment, anonymized),
                'topic': await _topic_export(db, topic_assignment.topic_id, caches['topics']),
            }
            if topic_assignment
            else None
        ),
        'legacy_essay': _row_payload(legacy_essay, anonymized),
        'plan': plan,
        'tasks': task_payloads,
        'chats': await _chats_export(db, session, [task.id for task in tasks], anonymized, caches['files']),
        'telemetry': {
            'summary': _row_payload(telemetry_summary, anonymized),
            'extension_presence': _row_payload(telemetry_presence, anonymized),
            'events': [_row_payload(event, anonymized) for event in telemetry_events],
        },
        'perturbations': {
            'llm_requests': [_row_payload(request_row) for request_row in llm_requests],
            'warning_states': [_row_payload(warning_state) for warning_state in warning_states],
        },
    }


async def _full_session_json_stream(
    db: AsyncSession,
    sessions: list[ExperimentSession],
    anonymized: bool,
    exported_at: str,
) -> AsyncIterator[str]:
    metadata = {
        'schema_version': '1.0',
        'exported_at': exported_at,
        'anonymized': anonymized,
        'timestamp_units': {
            'experiment_session_plan_task_submission_telemetry': 'nanoseconds since Unix epoch',
            'chat_message_file_user_group': 'seconds since Unix epoch unless the source row documents otherwise',
            'values': 'Raw database timestamp integers are preserved without conversion.',
        },
    }
    prefix = json.dumps(metadata, ensure_ascii=False)[:-1] + ',"sessions":['
    yield prefix
    for index, session in enumerate(sessions):
        if index:
            yield ','
        payload = await _full_session_payload(db, session, anonymized)
        yield json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    yield ']}'


def _tab_event_row(event: ExperimentTelemetryEvent):
    payload = event.payload_json or {}
    tab = payload.get('tab') or {}
    return {
        'event_id': event.id,
        'session_id': event.experiment_session_id,
        'user_id': event.user_id,
        'event_type': event.event_type,
        'event_time': event.event_time / NS,
        'event_time_ns': event.event_time,
        'schema_version': getattr(event, 'schema_version', 1),
        'browser_session_id': payload.get('browser_session_id'),
        'sequence': payload.get('sequence'),
        'tab_id': tab.get('tab_id'),
        'window_id': tab.get('window_id', payload.get('window_id')),
        'title': tab.get('title'),
        'url': tab.get('url'),
        'payload': payload,
    }


def _flatten_tab_event(row):
    payload = row.get('payload') or {}
    tab = payload.get('tab') or {}
    flat = {key: value for key, value in row.items() if key != 'payload'}
    for key, value in tab.items():
        flat[f'tab_{key}'] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
    for key, value in payload.items():
        if key in {'tab', 'browser_session_id', 'sequence'}:
            continue
        flat[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
    return flat


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
        (
            await db.execute(
                select(ExperimentTelemetrySummary).where(
                    ExperimentTelemetrySummary.experiment_session_id.in_(session_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.experiment_session_id: row for row in rows}


def _telemetry_payload(summary):
    return (
        ExperimentTelemetrySummaryModel.model_validate(summary).model_dump()
        if summary
        else ExperimentTelemetrySummaryModel().model_dump()
    )


def _session_row(session, group, user, essay, usage, telemetry=None, essay_task=None):
    telemetry_payload = _telemetry_payload(telemetry)
    topic_id = session.topic_id or (essay_task.essay_topic_id if essay_task else None)
    topic_title = session.topic_title or (essay_task.essay_topic_title if essay_task else None)
    essay_id = session.essay_id or (essay_task.essay_id if essay_task else None)
    return {
        'session_id': session.id,
        'user_id': session.user_id,
        'participant_id': _anonymous_id(session.user_id),
        'name': user.name if user else None,
        'username': user.username if user else None,
        'email': user.email if user else None,
        'group_id': session.group_id,
        'group_name': group.name if group else None,
        'condition_id': getattr(session, 'condition_id', None),
        'configuration_revision': getattr(session, 'plan_id', None),
        'topic_id': topic_id,
        'topic_title': topic_title,
        'state': session.state,
        'consented': session.consented_at is not None,
        'pre_survey_completed': session.pre_survey is not None,
        'essay_submitted': essay_id is not None,
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
        'essay_id': essay_id,
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
        task_topic_sessions = select(ExperimentSessionTask.experiment_session_id).where(
            ExperimentSessionTask.task_type == 'ESSAY',
            ExperimentSessionTask.essay_topic_id == filters.topic_id,
        )
        stmt = stmt.where(
            or_(
                ExperimentSession.topic_id == filters.topic_id,
                ExperimentSession.id.in_(task_topic_sessions),
            )
        )
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
    session_ids = [session.id for session in sessions]
    essay_task_rows = (
        list(
            (
                await db.execute(
                    select(ExperimentSessionTask)
                    .where(
                        ExperimentSessionTask.experiment_session_id.in_(session_ids),
                        ExperimentSessionTask.task_type == 'ESSAY',
                    )
                    .order_by(ExperimentSessionTask.experiment_session_id, ExperimentSessionTask.position)
                )
            )
            .scalars()
            .all()
        )
        if session_ids
        else []
    )
    essay_task_by_session = {}
    for task in essay_task_rows:
        essay_task_by_session.setdefault(task.experiment_session_id, task)
    usage = await _usage_by_session(db, [session.id for session in sessions])
    telemetry = await _telemetry_by_session(db, [session.id for session in sessions])
    essay_ids = [session.essay_id for session in sessions if session.essay_id]
    essay_ids.extend(task.essay_id for task in essay_task_rows if task.essay_id)
    essays = await _essays_by_ids(db, list(dict.fromkeys(essay_ids)))
    rows = []
    for member, user, session in records:
        if session:
            essay_task = essay_task_by_session.get(session.id)
            essay_id = session.essay_id or (essay_task.essay_id if essay_task else None)
            rows.append(
                _session_row(
                    session,
                    group_map.get(member.group_id),
                    user,
                    essays.get(essay_id),
                    usage.get(session.id, {}),
                    telemetry.get(session.id),
                    essay_task,
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
        'states': ['NOT_STARTED']
        + [state.value for state in ExperimentState if state != ExperimentState.NOT_APPLICABLE],
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
            'average_session_duration': average(
                _duration(session.created_at, session.completed_at) for session in complete
            ),
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
    items, total = _paginate_rows(
        rows, page, limit, search, order_by if order_by in allowed else 'session_start_time', direction
    )
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
    session_tasks = list(
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
    essay_tasks = [task for task in session_tasks if task.task_type == 'ESSAY']
    task_essay_ids = [task.essay_id for task in essay_tasks if task.essay_id]
    task_essays = (
        list((await db.execute(select(Essay).where(Essay.id.in_(task_essay_ids)))).scalars().all())
        if task_essay_ids
        else []
    )
    task_essay_map = {item.id: item for item in task_essays}
    essay_task_payloads = []
    for task in essay_tasks:
        task_essay = task_essay_map.get(task.essay_id)
        content = task_essay.content if task_essay else (task.essay_draft or '')
        draft_word_count, draft_character_count = essay_text_metrics(content)
        essay_task_payloads.append(
            {
                'session_task_id': task.id,
                'position': task.position,
                'title': task.title,
                'status': task.status,
                'topic_id': task.essay_topic_id or (task_essay.topic_id if task_essay else None),
                'topic_title': task.essay_topic_title or (task_essay.topic_title if task_essay else None),
                'topic_question': task.essay_topic_question or (task_essay.topic_question if task_essay else None),
                'essay_id': task_essay.id if task_essay else None,
                'content': content,
                'is_draft': task_essay is None,
                'word_count': task_essay.word_count if task_essay else draft_word_count,
                'character_count': task_essay.character_count if task_essay else draft_character_count,
                'submitted_at': _seconds(task_essay.created_at) if task_essay else None,
                'updated_at': _seconds(task_essay.updated_at if task_essay else task.updated_at),
            }
        )
    primary_essay_task = essay_task_payloads[0] if essay_task_payloads else None
    usage = (await _usage_by_session(db, [session.id])).get(session.id, {})
    telemetry = (await _telemetry_by_session(db, [session.id])).get(session.id)
    row = _session_row(session, group, participant, essay, usage, telemetry)
    condition = await db.get(ExperimentCondition, session.condition_id) if session.condition_id else None
    prompt_settings = await db.get(ExperimentPromptInjection, session.condition_id) if session.condition_id else None
    warning_settings = await db.get(ExperimentWarningModal, session.condition_id) if session.condition_id else None
    timing_settings = await db.get(ExperimentResponseTiming, session.condition_id) if session.condition_id else None
    perturbation_requests = list(
        (
            await db.execute(
                select(ExperimentLLMRequest)
                .where(ExperimentLLMRequest.experiment_session_id == session.id)
                .order_by(ExperimentLLMRequest.request_sequence)
            )
        )
        .scalars()
        .all()
    )
    perturbation_events = list(
        (
            await db.execute(
                select(ExperimentTelemetryEvent)
                .where(
                    ExperimentTelemetryEvent.experiment_session_id == session.id,
                    ExperimentTelemetryEvent.event_type.in_(
                        [
                            'warning_modal_displayed',
                            'warning_modal_acknowledged',
                            'response_first_visible',
                            'response_completed_visible',
                            'response_tab_hidden',
                            'response_navigated_away',
                        ]
                    ),
                )
                .order_by(ExperimentTelemetryEvent.event_time)
            )
        )
        .scalars()
        .all()
    )
    row.update(
        {
            'topic_id': session.topic_id or (primary_essay_task or {}).get('topic_id'),
            'topic_title': session.topic_title or (primary_essay_task or {}).get('topic_title'),
            'topic_question': session.topic_question or (primary_essay_task or {}).get('topic_question'),
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
                    'content': essay.content,
                    'submitted_at': _seconds(essay.created_at),
                    'word_count': essay.word_count,
                    'character_count': essay.character_count,
                }
                if essay
                else (
                    {
                        'id': primary_essay_task['essay_id'],
                        'content': primary_essay_task['content'],
                        'submitted_at': primary_essay_task['submitted_at'],
                        'word_count': primary_essay_task['word_count'],
                        'character_count': primary_essay_task['character_count'],
                        'is_draft': primary_essay_task['is_draft'],
                    }
                    if primary_essay_task
                    else None
                )
            ),
            'essay_tasks': essay_task_payloads,
            'condition': (
                {
                    'id': condition.id,
                    'name': condition.name,
                    'is_control': condition.is_control,
                    'plan_id': condition.plan_id,
                }
                if condition
                else {'name': 'Implicit control', 'is_control': True, 'plan_id': session.plan_id}
            ),
            'configuration_summary': {
                'prompt_injection_enabled': bool(prompt_settings and prompt_settings.enabled),
                'warning_modal_enabled': bool(warning_settings and warning_settings.enabled),
                'response_timing_mode': timing_settings.mode if timing_settings else 'NORMAL',
            },
            'perturbation_requests': [
                {
                    key: getattr(request_row, key)
                    for key in (
                        'id',
                        'condition_id',
                        'plan_id',
                        'plan_version',
                        'session_task_id',
                        'request_sequence',
                        'prompt_number',
                        'chat_id',
                        'user_message_id',
                        'assistant_message_id',
                        'prompt_active',
                        'prompt_draw',
                        'prompt_randomization_id',
                        'timing_mode',
                        'timing_parameters',
                        'request_at',
                        'provider_started_at',
                        'provider_first_token_at',
                        'provider_completed_at',
                        'artificial_delay_started_at',
                        'artificial_delay_ended_at',
                        'server_first_emit_at',
                        'server_completed_emit_at',
                        'client_first_visible_at',
                        'client_completed_visible_at',
                        'buffered',
                        'streaming_completed_normally',
                        'navigated_away',
                        'status',
                        'error_type',
                    )
                }
                for request_row in perturbation_requests
            ],
            'perturbation_events': [
                {
                    'id': event.id,
                    'type': event.event_type,
                    'event_time': event.event_time,
                    'request_id': event.request_id,
                    'chat_id': event.chat_id,
                    'message_id': event.message_id,
                    'payload': event.payload_json,
                }
                for event in perturbation_events
            ],
        }
    )
    return row


@router.get('/sessions/{session_id}/tab-activity')
async def tab_activity(
    session_id: str,
    event_type: Optional[str] = None,
    browser_tab_id: Optional[int] = None,
    browser_window_id: Optional[int] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not await db.get(ExperimentSession, session_id):
        raise HTTPException(status_code=404, detail='Experiment session not found.')
    if event_type and event_type not in TAB_TELEMETRY_EVENT_TYPES:
        raise HTTPException(status_code=422, detail='Unsupported tab activity event type.')
    stmt = select(ExperimentTelemetryEvent).where(
        ExperimentTelemetryEvent.experiment_session_id == session_id,
        ExperimentTelemetryEvent.event_type.in_(TAB_TELEMETRY_EVENT_TYPES),
    )
    if event_type:
        stmt = stmt.where(ExperimentTelemetryEvent.event_type == event_type)
    if date_from:
        stmt = stmt.where(ExperimentTelemetryEvent.event_time >= date_from * NS)
    if date_to:
        stmt = stmt.where(ExperimentTelemetryEvent.event_time < (date_to + 1) * NS)
    events = list((await db.execute(stmt.order_by(ExperimentTelemetryEvent.event_time))).scalars().all())
    rows = [_tab_event_row(event) for event in events]
    if browser_tab_id is not None:
        rows = [row for row in rows if row['tab_id'] == browser_tab_id]
    if browser_window_id is not None:
        rows = [row for row in rows if row['window_id'] == browser_window_id]
    total = len(rows)
    start = (page - 1) * limit
    return {'items': rows[start : start + limit], 'total': total, 'page': page, 'limit': limit}


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
    allowed = {
        'participant_id',
        'name',
        'group_name',
        'topic_title',
        'state',
        'word_count',
        'character_count',
        'submitted_at',
    }
    items, total = _paginate_rows(
        rows, page, limit, search, order_by if order_by in allowed else 'submitted_at', direction
    )
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
        (
            getattr(session, survey_name).get(field)
            if getattr(session, survey_name).get(field) is not None
            else 'Not available'
        )
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
    session_ids = [session.id for session in sessions]
    dynamic_records = (
        (
            await db.execute(
                select(SurveySubmission, ExperimentSessionTask, ExperimentSession, SurveyTask)
                .join(ExperimentSessionTask, ExperimentSessionTask.id == SurveySubmission.session_task_id)
                .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
                .join(SurveyTask, SurveyTask.id == ExperimentSessionTask.survey_task_id)
                .where(ExperimentSession.id.in_(session_ids))
                .order_by(SurveySubmission.created_at.desc())
            )
        ).all()
        if session_ids
        else []
    )
    submission_ids = [record[0].id for record in dynamic_records]
    dynamic_responses = (
        list(
            (await db.execute(select(SurveyResponse).where(SurveyResponse.submission_id.in_(submission_ids))))
            .scalars()
            .all()
        )
        if submission_ids
        else []
    )
    response_ids = [response.id for response in dynamic_responses]
    selected_rows = (
        list(
            (await db.execute(select(SurveyResponseChoice).where(SurveyResponseChoice.response_id.in_(response_ids))))
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    task_ids = list({record[3].id for record in dynamic_records})
    questions = (
        list(
            (
                await db.execute(
                    select(SurveyQuestion).where(SurveyQuestion.task_id.in_(task_ids)).order_by(SurveyQuestion.position)
                )
            )
            .scalars()
            .all()
        )
        if task_ids
        else []
    )
    question_ids = [question.id for question in questions]
    choices = (
        list(
            (
                await db.execute(
                    select(SurveyChoice)
                    .where(SurveyChoice.question_id.in_(question_ids))
                    .order_by(SurveyChoice.position)
                )
            )
            .scalars()
            .all()
        )
        if question_ids
        else []
    )
    response_map: dict[str, list[SurveyResponse]] = defaultdict(list)
    for response in dynamic_responses:
        response_map[response.submission_id].append(response)
    selected_map: dict[str, list[str]] = defaultdict(list)
    for selected in selected_rows:
        selected_map[selected.response_id].append(selected.choice_id)
    choice_map = {choice.id: choice for choice in choices}
    question_map: dict[str, list[SurveyQuestion]] = defaultdict(list)
    for question in questions:
        question_map[question.task_id].append(question)
    grouped: dict[str, dict] = {}
    dynamic_by_session: dict[str, list[dict]] = defaultdict(list)
    for submission, session_task, session, survey_task in dynamic_records:
        group = grouped.setdefault(
            survey_task.id,
            {
                'survey_task_id': survey_task.id,
                'family_id': survey_task.family_id,
                'version': survey_task.version,
                'title': survey_task.title,
                'submission_count': 0,
                'skipped_count': 0,
                'questions': {},
            },
        )
        group['submission_count'] += submission.status == 'SUBMITTED'
        group['skipped_count'] += submission.status == 'SKIPPED'
        answer_payload = []
        for response in response_map.get(submission.id, []):
            question = next((item for item in question_map[survey_task.id] if item.id == response.question_id), None)
            if not question:
                continue
            labels = [choice_map[choice_id].text for choice_id in selected_map[response.id] if choice_id in choice_map]
            value = response.scale_answer if response.scale_answer is not None else response.text_answer
            if labels:
                value = labels
            question_data = group['questions'].setdefault(
                question.id,
                {
                    'id': question.id,
                    'prompt': question.prompt,
                    'question_type': question.question_type,
                    'position': question.position,
                    'values': [],
                },
            )
            if response.is_answered:
                question_data['values'].extend(value if isinstance(value, list) else [value])
            answer_payload.append({'question_id': question.id, 'prompt': question.prompt, 'value': value})
        dynamic_by_session[session.id].append(
            {
                'submission_id': submission.id,
                'title': survey_task.title,
                'version': survey_task.version,
                'status': submission.status,
                'answers': answer_payload,
            }
        )
    dynamic_surveys = []
    for group in grouped.values():
        question_results = []
        for question in sorted(group.pop('questions').values(), key=lambda item: item['position']):
            counts = Counter(str(value) for value in question.pop('values') if value is not None)
            total = sum(counts.values())
            question['distribution'] = [
                {'label': label, 'count': count, 'percentage': round(count / total * 100, 1) if total else 0}
                for label, count in counts.items()
            ]
            question_results.append(question)
        group['questions'] = question_results
        dynamic_surveys.append(group)
    responses = [
        {
            'session_id': session.id,
            'participant_id': _anonymous_id(session.user_id),
            'state': session.state,
            'pre_survey_completed': session.pre_survey is not None,
            'post_survey_completed': session.post_survey is not None,
            'comments': session.post_survey.get('comments') if session.post_survey else None,
            'survey_submissions': dynamic_by_session.get(session.id, []),
        }
        for session in sessions
        if session.pre_survey or session.post_survey or dynamic_by_session.get(session.id)
    ]
    start = (page - 1) * limit
    return {
        'pre': {
            field: _distribution(sessions, field, 'pre_survey')
            for field in (
                'school_class',
                'ai_familiarity',
                'ai_schoolwork_frequency',
                'essay_writing_confidence',
                'age_range',
            )
        },
        'post': {
            field: _distribution(sessions, field, 'post_survey')
            for field in ('ai_helpfulness', 'essay_satisfaction', 'ai_improvement', 'chat_ease')
        },
        'dynamic': dynamic_surveys,
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
        headers = list(dict.fromkeys(key for row in rows for key in row))
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


@router.post('/export/sessions')
async def export_full_sessions(
    form: FullSessionExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    session_ids = list(dict.fromkeys(form.ids))
    rows = list(
        (await db.execute(select(ExperimentSession).where(ExperimentSession.id.in_(session_ids)))).scalars().all()
    )
    session_map = {row.id: row for row in rows}
    missing = [session_id for session_id in session_ids if session_id not in session_map]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={'message': 'One or more experiment sessions no longer exist.', 'missing_ids': missing},
        )
    sessions = [session_map[session_id] for session_id in session_ids]
    now = datetime.now(timezone.utc)
    filename = f'experiment-full-sessions-{now.strftime("%Y%m%d-%H%M%S")}.json'
    return StreamingResponse(
        _full_session_json_stream(db, sessions, form.anonymized, now.isoformat()),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
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


@router.post('/export/tab-activity')
async def export_tab_activity(
    form: ExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    events = list(
        (
            await db.execute(
                select(ExperimentTelemetryEvent)
                .where(
                    ExperimentTelemetryEvent.experiment_session_id.in_(form.ids),
                    ExperimentTelemetryEvent.event_type.in_(TAB_TELEMETRY_EVENT_TYPES),
                )
                .order_by(ExperimentTelemetryEvent.experiment_session_id, ExperimentTelemetryEvent.event_time)
            )
        )
        .scalars()
        .all()
    )
    rows = [_tab_event_row(event) for event in events]
    if form.format == 'csv':
        rows = [_flatten_tab_event(row) for row in rows]
    return _export_response(rows, form, 'experiment-tab-activity')


@router.post('/export/perturbations')
async def export_perturbations(
    form: ExportRequest,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    requests = list(
        (
            await db.execute(
                select(ExperimentLLMRequest)
                .where(ExperimentLLMRequest.experiment_session_id.in_(form.ids))
                .order_by(ExperimentLLMRequest.experiment_session_id, ExperimentLLMRequest.request_sequence)
            )
        )
        .scalars()
        .all()
    )
    events = list(
        (
            await db.execute(
                select(ExperimentTelemetryEvent)
                .where(
                    ExperimentTelemetryEvent.experiment_session_id.in_(form.ids),
                    ExperimentTelemetryEvent.event_type.in_(
                        [
                            'warning_modal_displayed',
                            'warning_modal_acknowledged',
                            'response_first_visible',
                            'response_completed_visible',
                            'response_tab_hidden',
                            'response_navigated_away',
                        ]
                    ),
                )
                .order_by(ExperimentTelemetryEvent.experiment_session_id, ExperimentTelemetryEvent.event_time)
            )
        )
        .scalars()
        .all()
    )
    condition_ids = {request_row.condition_id for request_row in requests if request_row.condition_id}
    prompt_by_condition = {
        row.condition_id: row
        for row in (
            (
                await db.execute(
                    select(ExperimentPromptInjection).where(ExperimentPromptInjection.condition_id.in_(condition_ids))
                )
            )
            .scalars()
            .all()
            if condition_ids
            else []
        )
    }
    warning_by_condition = {
        row.condition_id: row
        for row in (
            (
                await db.execute(
                    select(ExperimentWarningModal).where(ExperimentWarningModal.condition_id.in_(condition_ids))
                )
            )
            .scalars()
            .all()
            if condition_ids
            else []
        )
    }
    rows = [
        {
            'record_type': 'request',
            **{
                key: getattr(request_row, key)
                for key in (
                    'id',
                    'experiment_session_id',
                    'condition_id',
                    'plan_id',
                    'plan_version',
                    'session_task_id',
                    'chat_id',
                    'user_message_id',
                    'assistant_message_id',
                    'request_sequence',
                    'prompt_number',
                    'assignment_identifier',
                    'prompt_active',
                    'prompt_draw',
                    'prompt_randomization_id',
                    'timing_mode',
                    'timing_parameters',
                    'request_at',
                    'provider_started_at',
                    'provider_first_token_at',
                    'provider_completed_at',
                    'artificial_delay_started_at',
                    'artificial_delay_ended_at',
                    'server_first_emit_at',
                    'server_completed_emit_at',
                    'client_first_visible_at',
                    'client_completed_visible_at',
                    'buffered',
                    'streaming_completed_normally',
                    'navigated_away',
                    'status',
                    'error_type',
                )
            },
            'event_type': None,
            'event_time': None,
            'event_payload': None,
            'configuration_summary': {
                'prompt_injection_enabled': bool(
                    prompt_by_condition.get(request_row.condition_id)
                    and prompt_by_condition[request_row.condition_id].enabled
                ),
                'warning_modal_enabled': bool(
                    warning_by_condition.get(request_row.condition_id)
                    and warning_by_condition[request_row.condition_id].enabled
                ),
                'response_timing_mode': request_row.timing_mode,
            },
        }
        for request_row in requests
    ]
    rows.extend(
        {
            'record_type': 'event',
            'id': event.id,
            'experiment_session_id': event.experiment_session_id,
            'condition_id': event.condition_id,
            'plan_id': event.plan_id,
            'request_id': event.request_id,
            'chat_id': event.chat_id,
            'message_id': event.message_id,
            'event_type': event.event_type,
            'event_time': event.event_time,
            'event_payload': event.payload_json,
        }
        for event in events
    )
    return _export_response(rows, form, 'experiment-perturbations')


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
    session_ids = [session.id for session in sessions]
    dynamic_records = (
        (
            await db.execute(
                select(SurveySubmission, ExperimentSessionTask, SurveyTask)
                .join(ExperimentSessionTask, ExperimentSessionTask.id == SurveySubmission.session_task_id)
                .join(SurveyTask, SurveyTask.id == ExperimentSessionTask.survey_task_id)
                .where(ExperimentSessionTask.experiment_session_id.in_(session_ids))
            )
        ).all()
        if session_ids
        else []
    )
    submission_ids = [record[0].id for record in dynamic_records]
    dynamic_answers = (
        list(
            (
                await db.execute(
                    select(SurveyResponse, SurveyQuestion)
                    .join(SurveyQuestion, SurveyQuestion.id == SurveyResponse.question_id)
                    .where(SurveyResponse.submission_id.in_(submission_ids))
                )
            ).all()
        )
        if submission_ids
        else []
    )
    dynamic_response_ids = [response.id for response, _ in dynamic_answers]
    selected_answers = (
        list(
            (
                await db.execute(
                    select(SurveyResponseChoice, SurveyChoice)
                    .join(SurveyChoice, SurveyChoice.id == SurveyResponseChoice.choice_id)
                    .where(SurveyResponseChoice.response_id.in_(dynamic_response_ids))
                )
            ).all()
        )
        if dynamic_response_ids
        else []
    )
    selected_answer_map: dict[str, list[str]] = defaultdict(list)
    for selected, choice in selected_answers:
        selected_answer_map[selected.response_id].append(choice.text)
    answer_map: dict[str, list[dict]] = defaultdict(list)
    for response, question in dynamic_answers:
        answer_map[response.submission_id].append(
            {
                'question_id': question.id,
                'prompt': question.prompt,
                'question_type': question.question_type,
                'text': response.text_answer,
                'scale': response.scale_answer,
                'choices': selected_answer_map.get(response.id, []),
                'answered': response.is_answered,
            }
        )
    dynamic_map: dict[str, list[dict]] = defaultdict(list)
    for submission, task, survey_task in dynamic_records:
        dynamic_map[task.experiment_session_id].append(
            {
                'submission_id': submission.id,
                'survey_task_id': survey_task.id,
                'title': survey_task.title,
                'version': survey_task.version,
                'status': submission.status,
                'answers': answer_map.get(submission.id, []),
            }
        )
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
            'pre_survey': (
                json.dumps(session.pre_survey, ensure_ascii=False) if form.format == 'csv' else session.pre_survey
            ),
            'post_survey': (
                json.dumps(session.post_survey, ensure_ascii=False) if form.format == 'csv' else session.post_survey
            ),
            'pipeline_surveys': (
                json.dumps(dynamic_map.get(session.id, []), ensure_ascii=False)
                if form.format == 'csv'
                else dynamic_map.get(session.id, [])
            ),
        }
        for session in sessions
    ]
    return _export_response(rows, form, 'experiment-surveys')


class ScoreOverrideForm(BaseModel):
    score: Decimal = Field(ge=0, decimal_places=2)
    note: Optional[str] = Field(default=None, max_length=2000)


@router.get('/question-results')
async def question_results(
    group_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    state: Optional[str] = None,
    completed: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    stmt = (
        select(QuestionSubmission, ExperimentSessionTask, ExperimentSession, User, Group)
        .join(ExperimentSessionTask, ExperimentSessionTask.id == QuestionSubmission.session_task_id)
        .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
        .join(User, User.id == ExperimentSession.user_id)
        .join(Group, Group.id == ExperimentSession.group_id)
        .order_by(QuestionSubmission.created_at.desc())
    )
    stmt = _apply_session_filters(
        stmt,
        DashboardFilters(
            group_id=group_id,
            topic_id=topic_id,
            date_from=date_from,
            date_to=date_to,
            state=state,
            completed=completed,
        ),
    )
    records = (await db.execute(stmt)).all()
    rows = [
        {
            'submission_id': submission.id,
            'session_id': session.id,
            'session_task_id': task.id,
            'participant_id': _anonymous_id(session.user_id),
            'name': participant.name,
            'email': participant.email,
            'group_id': group.id,
            'group_name': group.name,
            'task_title': task.title,
            'task_position': task.position,
            'status': submission.status,
            'grading_status': submission.grading_status,
            'score': float(submission.current_score) if submission.current_score is not None else None,
            'provisional_score': float(submission.provisional_score or 0),
            'has_grading_error': submission.has_grading_error,
            'maximum_score': float(submission.maximum_score or 0),
            'submitted_at': _seconds(submission.submitted_at),
        }
        for submission, task, session, participant, group in records
    ]
    if search:
        query = search.casefold()
        rows = [
            row
            for row in rows
            if query
            in ' '.join(
                str(row.get(key) or '').casefold()
                for key in ('participant_id', 'name', 'email', 'group_name', 'task_title', 'grading_status')
            )
        ]
    total = len(rows)
    start = (page - 1) * limit
    return {'items': rows[start : start + limit], 'total': total, 'page': page, 'limit': limit}


@router.get('/question-submissions/{submission_id}')
async def question_submission_detail(
    submission_id: str, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    record = (
        await db.execute(
            select(QuestionSubmission, ExperimentSessionTask, ExperimentSession, User, Group)
            .join(ExperimentSessionTask, ExperimentSessionTask.id == QuestionSubmission.session_task_id)
            .join(ExperimentSession, ExperimentSession.id == ExperimentSessionTask.experiment_session_id)
            .join(User, User.id == ExperimentSession.user_id)
            .join(Group, Group.id == ExperimentSession.group_id)
            .where(QuestionSubmission.id == submission_id)
        )
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail='Question submission not found.')
    submission, session_task, session, participant, group = record
    task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
    responses = list(
        (await db.execute(select(QuestionResponse).where(QuestionResponse.submission_id == submission.id)))
        .scalars()
        .all()
    )
    response_ids = [response.id for response in responses]
    selected = (
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
    blanks = (
        list(
            (await db.execute(select(QuestionResponseBlank).where(QuestionResponseBlank.response_id.in_(response_ids))))
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    attempts = (
        list(
            (
                await db.execute(
                    select(QuestionGradingAttempt)
                    .where(QuestionGradingAttempt.response_id.in_(response_ids))
                    .order_by(QuestionGradingAttempt.created_at)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    overrides = (
        list(
            (
                await db.execute(
                    select(QuestionScoreOverride)
                    .where(QuestionScoreOverride.response_id.in_(response_ids))
                    .order_by(QuestionScoreOverride.created_at)
                )
            )
            .scalars()
            .all()
        )
        if response_ids
        else []
    )
    selected_map: dict[str, list[str]] = defaultdict(list)
    blank_map: dict[str, list[dict]] = defaultdict(list)
    attempt_map: dict[str, list[dict]] = defaultdict(list)
    override_map: dict[str, list[dict]] = defaultdict(list)
    for row in selected:
        selected_map[row.response_id].append(row.choice_id)
    for row in blanks:
        blank_map[row.response_id].append({'blank_id': row.blank_id, 'answer': row.answer})
    for row in attempts:
        attempt_map[row.response_id].append(
            {
                'id': row.id,
                'method': row.method,
                'status': row.status,
                'model_id': row.model_id,
                'awarded_score': float(row.awarded_score) if row.awarded_score is not None else None,
                'rationale': row.rationale,
                'error_code': row.error_code,
                'created_at': _seconds(row.created_at),
                'completed_at': _seconds(row.completed_at),
            }
        )
    for row in overrides:
        override_map[row.response_id].append(
            {
                'id': row.id,
                'admin_id': row.admin_id,
                'previous_score': float(row.previous_score) if row.previous_score is not None else None,
                'new_score': float(row.new_score),
                'note': row.note,
                'created_at': _seconds(row.created_at),
            }
        )
    question_map = {question.id: question for question in task.questions}
    question_position = {question.id: question.position for question in task.questions}
    responses.sort(key=lambda response: question_position.get(response.question_id, 0))
    return {
        'submission_id': submission.id,
        'session_id': session.id,
        'participant_id': _anonymous_id(session.user_id),
        'participant': {'name': participant.name, 'email': participant.email},
        'group': {'id': group.id, 'name': group.name},
        'task': {'id': task.id, 'title': task.title, 'description': task.description},
        'status': submission.status,
        'grading_status': submission.grading_status,
        'score': float(submission.current_score) if submission.current_score is not None else None,
        'provisional_score': float(submission.provisional_score or 0),
        'has_grading_error': submission.has_grading_error,
        'maximum_score': float(submission.maximum_score or 0),
        'submitted_at': _seconds(submission.submitted_at),
        'responses': [
            {
                'id': response.id,
                'question': question_map[response.question_id].model_dump(mode='json'),
                'selected_choice_ids': selected_map[response.id],
                'blank_answers': blank_map[response.id],
                'text_answer': response.free_text_answer,
                'is_answered': response.is_answered,
                'grading_status': response.grading_status,
                'grading_method': response.grading_method,
                'generated_score': float(response.generated_score) if response.generated_score is not None else None,
                'effective_score': float(response.effective_score) if response.effective_score is not None else None,
                'rationale': response.rationale,
                'attempts': attempt_map[response.id],
                'overrides': override_map[response.id],
            }
            for response in responses
        ],
    }


@router.put('/question-responses/{response_id}/score')
async def override_question_score(
    response_id: str,
    form: ScoreOverrideForm,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    response = await db.get(QuestionResponse, response_id)
    if not response:
        raise HTTPException(status_code=404, detail='Question response not found.')
    submission = await db.get(QuestionSubmission, response.submission_id)
    session_task = await db.get(ExperimentSessionTask, submission.session_task_id)
    task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
    question = next(question for question in task.questions if question.id == response.question_id)
    if form.score > question.max_score:
        raise HTTPException(status_code=422, detail='Score cannot exceed the question maximum.')
    now = int(time.time_ns())
    previous = response.effective_score
    db.add(
        QuestionScoreOverride(
            id=str(uuid.uuid4()),
            response_id=response.id,
            admin_id=user.id,
            previous_score=previous,
            new_score=form.score,
            note=form.note.strip() if form.note else None,
            created_at=now,
        )
    )
    response.effective_score = form.score
    response.grading_method = (
        'MANUAL'
        if response.grading_status == GradingStatus.AWAITING_REVIEW.value and previous is None
        else 'ADMIN_OVERRIDE'
    )
    response.grading_status = GradingStatus.GRADED.value
    response.updated_at = now
    await QuestionSubmissions.recalculate(submission.id, db)
    await db.commit()
    return await question_submission_detail(submission.id, user=user, db=db)


@router.post('/question-responses/{response_id}/retry')
async def retry_question_grading(
    response_id: str, request: Request, user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)
):
    response = await db.get(QuestionResponse, response_id)
    if not response:
        raise HTTPException(status_code=404, detail='Question response not found.')
    submission = await db.get(QuestionSubmission, response.submission_id)
    session_task = await db.get(ExperimentSessionTask, submission.session_task_id)
    task = await QuestionTasks.get_task(session_task.question_task_id, db=db)
    question = next((item for item in task.questions if item.id == response.question_id), None)
    if not question or question.grading_mode.value != 'LLM_ASSISTED':
        raise HTTPException(status_code=422, detail='Only LLM-assisted responses can be retried.')
    active_attempt = (
        (
            await db.execute(
                select(QuestionGradingAttempt)
                .where(
                    QuestionGradingAttempt.response_id == response.id,
                    QuestionGradingAttempt.status.in_(
                        [
                            GradingStatus.PENDING.value,
                            GradingStatus.RUNNING.value,
                        ]
                    ),
                )
                .order_by(QuestionGradingAttempt.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if active_attempt:
        detail = await question_submission_detail(submission.id, user=user, db=db)
        return {'status': active_attempt.status, 'attempt_id': active_attempt.id, 'submission': detail}
    now = int(time.time_ns())
    overridden = response.grading_method == 'ADMIN_OVERRIDE' and response.effective_score is not None
    if not overridden:
        response.generated_score = None
        response.effective_score = None
        response.rationale = None
        response.grading_status = GradingStatus.PENDING.value
    attempt_id = str(uuid.uuid4())
    db.add(
        QuestionGradingAttempt(
            id=attempt_id,
            response_id=response.id,
            method='LLM_ASSISTED',
            status=GradingStatus.PENDING.value,
            created_at=now,
        )
    )
    await QuestionSubmissions.recalculate(response.submission_id, db)
    await db.commit()
    await create_task(request.app.state.redis, grade_response(request, user, response.id), response.submission_id)
    detail = await question_submission_detail(submission.id, user=user, db=db)
    return {'status': 'PENDING', 'attempt_id': attempt_id, 'submission': detail}
