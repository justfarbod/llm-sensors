import asyncio
import hashlib
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import BigInteger, Column, ForeignKey, Integer, JSON, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base
from open_webui.models.essays import EssayTopic
from open_webui.models.experiment_perturbations import default_control_condition
from open_webui.models.experiment_perturbations import ExperimentCondition
from open_webui.models.experiment_plans import (
    EssayTopicMode,
    ExperimentChatMode,
    ExperimentPlan,
    ExperimentPlanForm,
    ExperimentPlanItem,
    ExperimentPlanItemTopic,
    ExperimentPlans,
    ProgressionMode,
)
from open_webui.models.experiment_prompt_budgets import LLMPromptBudget, default_prompt_budget, remap_prompt_budget
from open_webui.models.files import File
from open_webui.models.groups import Group
from open_webui.models.question_tasks import (
    GradingMode,
    QuestionTask,
    QuestionTaskForm,
    QuestionTaskStatus,
    QuestionTasks,
)
from open_webui.models.survey_tasks import (
    SurveyTask,
    SurveyTaskForm,
    SurveyTaskStatus,
    SurveyTasks,
)
from open_webui.storage.provider import Storage


WORKFLOW_FORMAT = 'open-webui-experiment-workflow'
WORKFLOW_SCHEMA_VERSION = 4
LEGACY_WORKFLOW_SCHEMA_VERSION = 2
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_FORMATS = {
    'PNG': ('image/png', '.png'),
    'JPEG': ('image/jpeg', '.jpg'),
    'WEBP': ('image/webp', '.webp'),
    'GIF': ('image/gif', '.gif'),
}


class ExperimentWorkflow(Base):
    __tablename__ = 'experiment_workflow'

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default='')
    definition = Column(JSON, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(Text, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_by = Column(Text, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class WorkflowIssue(BaseModel):
    path: str
    message: str


class WorkflowWriteForm(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=20000)
    definition: dict[str, Any] = Field(default_factory=dict)
    revision: Optional[int] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Workflow name is required.')
        return value

    @field_validator('description')
    @classmethod
    def strip_description(cls, value):
        return value.strip()


class WorkflowApplyForm(BaseModel):
    group_id: str
    workflow_revision: int = Field(gt=0)
    expected_current_plan_version: int = Field(default=0, ge=0)


class ExperimentWorkflowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    definition: dict[str, Any]
    revision: int
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: int
    updated_at: int
    status: str = 'DRAFT'
    issues: list[WorkflowIssue] = Field(default_factory=list)


def default_workflow_definition() -> dict[str, Any]:
    return {
        'progression_mode': 'STRICT_SEQUENTIAL',
        'chat_mode': 'SHARED_EXPERIMENT',
        'consent_enabled': True,
        'items': [],
        'conditions': [
            {
                'key': str(uuid.uuid4()),
                **default_control_condition().model_dump(mode='json'),
            }
        ],
    }


def normalize_definition(definition: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise HTTPException(status_code=422, detail='Workflow definition must be an object.')
    conditions = definition.get('conditions', [])
    if isinstance(conditions, list):
        for condition in conditions:
            if isinstance(condition, dict) and 'memory_injection' in condition:
                raise HTTPException(
                    status_code=422,
                    detail='Memory or context injection is no longer supported.',
                )
    normalized = default_workflow_definition()
    normalized.update(json.loads(json.dumps(definition)))
    for field in ('items', 'conditions'):
        if not isinstance(normalized.get(field), list):
            raise HTTPException(status_code=422, detail=f'Workflow field {field} must be an array.')
    for item in normalized['items']:
        if isinstance(item, dict) and item.get('task_type') in ('ESSAY', 'QUESTION'):
            item.setdefault('llm_prompt_budget', default_prompt_budget())
    condition_keys: set[str] = set()
    for condition in normalized['conditions']:
        if isinstance(condition, dict):
            key = (
                condition.get('key')
                or condition.get('id')
                or str(uuid.uuid4())
            )
            if not isinstance(key, str) or not key or key in condition_keys:
                key = str(uuid.uuid4())
            condition_keys.add(key)
            condition['key'] = key
            condition.pop('id', None)
    return normalized


def _upgrade_v2_definition(definition: Any) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise HTTPException(status_code=422, detail='Workflow definition must be an object.')
    upgraded = json.loads(json.dumps(definition))
    conditions = upgraded.get('conditions', [])
    if not isinstance(conditions, list):
        return upgraded
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        memory = condition.pop('memory_injection', None)
        if memory is not None and (
            not isinstance(memory, dict) or memory.get('enabled') is not False
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    'Schema-version 2 workflows may be imported only when memory injection '
                    'is absent or explicitly disabled.'
                ),
            )
    return upgraded


def _issue(path: str, message: str) -> WorkflowIssue:
    return WorkflowIssue(path=path, message=message)


def _pydantic_issues(prefix: str, error: ValidationError) -> list[WorkflowIssue]:
    return [
        _issue(
            '.'.join([prefix, *[str(part) for part in item['loc']]]).strip('.'),
            item['msg'],
        )
        for item in error.errors()
    ]


def _condition_forms(definition: dict[str, Any]) -> list[Any]:
    conditions = json.loads(json.dumps(definition.get('conditions', [])))
    if not isinstance(conditions, list):
        return []
    for condition in conditions:
        if isinstance(condition, dict):
            condition['id'] = condition.pop('key', None) or condition.get('id')
    return conditions


def validate_definition(definition: Any) -> list[WorkflowIssue]:
    if not isinstance(definition, dict):
        return [_issue('definition', 'Workflow definition must be an object.')]
    issues: list[WorkflowIssue] = []
    items = definition.get('items', [])
    if not isinstance(items, list):
        return [_issue('items', 'Must be an array.')]
    if not isinstance(definition.get('conditions', []), list):
        issues.append(_issue('conditions', 'Must be an array.'))
    item_keys: set[str] = set()
    plan_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append(_issue(f'items.{index}', 'Must be an object.'))
            continue
        key = item.get('key')
        if not isinstance(key, str) or not key or key in item_keys:
            issues.append(_issue(f'items.{index}.key', 'A unique stable key is required.'))
            continue
        item_keys.add(key)
        mapped = {
            'id': key,
            'task_type': item.get('task_type'),
            'title': item.get('title', ''),
            'enabled': item.get('enabled', True),
            'question_task_id': item.get('question_task_id'),
            'llm_prompt_budget': item.get('llm_prompt_budget', default_prompt_budget()),
            'essay_topic_mode': item.get('essay_topic_mode'),
            'essay_topic_id': item.get('essay_topic_id'),
            'essay_topic_ids': item.get('essay_topic_ids', []),
            'survey_task_id': item.get('survey_task_id'),
            'survey_required': item.get('survey_required', True),
        }
        if mapped['essay_topic_mode'] == EssayTopicMode.RANDOM_ALL.value and not mapped['essay_topic_ids']:
            issues.append(
                _issue(
                    f'items.{index}.essay_topic_ids',
                    'Random from all requires at least one saved essay topic.',
                )
            )
        plan_items.append(mapped)
    try:
        ExperimentPlanForm.model_validate(
            {
                'progression_mode': definition.get(
                    'progression_mode', ProgressionMode.STRICT_SEQUENTIAL.value
                ),
                'chat_mode': definition.get(
                    'chat_mode', ExperimentChatMode.SHARED_EXPERIMENT.value
                ),
                'consent_enabled': definition.get('consent_enabled', True),
                'items': plan_items,
                'conditions': _condition_forms(definition),
            }
        )
    except ValidationError as error:
        issues.extend(_pydantic_issues('', error))
    return issues


def _referenced_ids(definition: dict[str, Any]):
    topic_ids: set[str] = set()
    question_ids: set[str] = set()
    survey_ids: set[str] = set()
    for item in definition.get('items', []):
        if not isinstance(item, dict):
            continue
        if item.get('task_type') == 'ESSAY':
            if item.get('essay_topic_mode') == EssayTopicMode.SPECIFIC.value:
                if isinstance(item.get('essay_topic_id'), str) and item.get(
                    'essay_topic_id'
                ):
                    topic_ids.add(item['essay_topic_id'])
            else:
                pool = item.get('essay_topic_ids', [])
                if not isinstance(pool, list):
                    pool = []
                topic_ids.update(
                    value for value in pool if isinstance(value, str) and value
                )
        elif (
            item.get('task_type') == 'QUESTION'
            and isinstance(item.get('question_task_id'), str)
            and item.get('question_task_id')
        ):
            question_ids.add(item['question_task_id'])
        elif (
            item.get('task_type') == 'SURVEY'
            and isinstance(item.get('survey_task_id'), str)
            and item.get('survey_task_id')
        ):
            survey_ids.add(item['survey_task_id'])
    return topic_ids, question_ids, survey_ids


async def _validation_issues(
    definition: dict[str, Any],
    db: AsyncSession,
    task_model_available: bool = True,
) -> list[WorkflowIssue]:
    issues = validate_definition(definition)
    topic_ids, question_ids, survey_ids = _referenced_ids(definition)
    topics = {
        row.id: row
        for row in (
            await db.execute(select(EssayTopic).where(EssayTopic.id.in_(topic_ids)))
        ).scalars()
    } if topic_ids else {}
    for topic_id in topic_ids:
        topic = topics.get(topic_id)
        if not topic or topic.workflow_managed:
            issues.append(_issue('items', f'Essay topic {topic_id} is unavailable.'))

    questions = {
        row.id: row
        for row in (
            await db.execute(select(QuestionTask).where(QuestionTask.id.in_(question_ids)))
        ).scalars()
    } if question_ids else {}
    for task_id in question_ids:
        row = questions.get(task_id)
        if (
            not row
            or row.workflow_managed
            or row.status != QuestionTaskStatus.PUBLISHED.value
        ):
            issues.append(
                _issue('items', f'Published Question Task {task_id} is unavailable.')
            )
            continue
        task = await QuestionTasks.get_task(task_id, db=db)
        for index, item in enumerate(definition.get('items', [])):
            if item.get('question_task_id') == task_id:
                try:
                    budget = LLMPromptBudget.model_validate(item.get('llm_prompt_budget') or default_prompt_budget())
                    if task and budget.mode == 'PER_QUESTION' and set(budget.question_limits) != {q.id for q in task.questions}:
                        issues.append(_issue(f'items.{index}.llm_prompt_budget', 'Prompt limits must cover exactly the selected task questions.'))
                except ValidationError:
                    pass  # Structural errors are already reported by validate_definition.
        if (
            not task_model_available
            and task
            and any(question.grading_mode == GradingMode.LLM_ASSISTED for question in task.questions)
        ):
            issues.append(
                _issue(
                    'items',
                    f'Question Task {task.title} requires an available global task model.',
                )
            )

    surveys = {
        row.id: row
        for row in (
            await db.execute(select(SurveyTask).where(SurveyTask.id.in_(survey_ids)))
        ).scalars()
    } if survey_ids else {}
    for task_id in survey_ids:
        row = surveys.get(task_id)
        if (
            not row
            or row.workflow_managed
            or row.status != SurveyTaskStatus.PUBLISHED.value
        ):
            issues.append(
                _issue('items', f'Published Survey Task {task_id} is unavailable.')
            )
    return issues


async def _model(
    row: ExperimentWorkflow,
    db: AsyncSession,
    task_model_available: bool = True,
) -> ExperimentWorkflowModel:
    issues = await _validation_issues(row.definition, db, task_model_available)
    return ExperimentWorkflowModel(
        **{
            field: getattr(row, field)
            for field in (
                'id',
                'name',
                'description',
                'definition',
                'revision',
                'created_by',
                'updated_by',
                'created_at',
                'updated_at',
            )
        },
        status='READY' if not issues else 'DRAFT',
        issues=issues,
    )


def _safe_filename(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', value.strip()).strip('-')
    return value or 'experiment-workflow'


def _validate_image(data: bytes, content_type: str) -> tuple[str, str]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=422,
            detail='Workflow images must be between 1 byte and 10 MB.',
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=422,
                    detail='Workflow images may contain at most 20 megapixels.',
                )
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(status_code=422, detail='A workflow image is invalid.')
    expected = IMAGE_FORMATS.get(image_format)
    if not expected or content_type != expected[0]:
        raise HTTPException(
            status_code=422,
            detail='Workflow image MIME type does not match its content.',
        )
    return expected


def _validate_image_filename(filename: str, extension: str):
    suffix = Path(filename).suffix.lower()
    allowed = {extension, '.jpeg'} if extension == '.jpg' else {extension}
    if suffix not in allowed:
        raise HTTPException(
            status_code=422,
            detail='Workflow image filename extension does not match its content.',
        )


async def _read_stored_file(row: File) -> bytes:
    path = Path(await asyncio.to_thread(Storage.get_file, row.path))
    if not path.is_file():
        raise HTTPException(status_code=422, detail='A workflow image is unavailable.')
    return await asyncio.to_thread(path.read_bytes)


async def _store_file(
    data: bytes,
    filename: str,
    content_type: str,
    user_id: str,
    db: AsyncSession,
) -> tuple[File, str]:
    _, extension = _validate_image(data, content_type)
    _validate_image_filename(filename, extension)
    file_id = str(uuid.uuid4())
    stored_name = f'{file_id}_question_task{extension}'
    _, path = await asyncio.to_thread(
        Storage.upload_file,
        io.BytesIO(data),
        stored_name,
        {
            'OpenWebUI-User-Id': user_id,
            'OpenWebUI-File-Id': file_id,
            'OpenWebUI-File-Scope': 'question-task',
        },
    )
    now = int(time.time())
    row = File(
        id=file_id,
        user_id=user_id,
        filename=filename,
        path=path,
        data={},
        meta={
            'name': filename,
            'content_type': content_type,
            'size': len(data),
            'scope': 'question-task',
        },
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row, path


async def _expand_random_all(definition: dict[str, Any], db: AsyncSession):
    topic_ids = list(
        (
            await db.execute(
                select(EssayTopic.id)
                .where(EssayTopic.workflow_managed.is_(False))
                .order_by(EssayTopic.created_at, EssayTopic.id)
            )
        ).scalars()
    )
    definition = json.loads(json.dumps(definition))
    for item in definition.get('items', []):
        if (
            isinstance(item, dict)
            and item.get('task_type') == 'ESSAY'
            and item.get('essay_topic_mode') == EssayTopicMode.RANDOM_ALL.value
        ):
            item['essay_topic_ids'] = topic_ids
    return definition


def _strip_question_task(task) -> dict[str, Any]:
    data = task.model_dump(
        mode='json',
        exclude={
            'id',
            'family_id',
            'version',
            'latest_version',
            'status',
            'locked_at',
            'archived_at',
            'created_at',
            'updated_at',
        },
    )
    for question in data.get('questions', []):
        question.pop('id', None)
        question.pop('position', None)
        for choice in question.get('choices', []):
            choice.pop('id', None)
            choice.pop('position', None)
        for blank in question.get('blanks', []):
            blank.pop('id', None)
            blank.pop('position', None)
    return data


def _strip_survey_task(task) -> dict[str, Any]:
    data = task.model_dump(
        mode='json',
        exclude={
            'id',
            'family_id',
            'version',
            'latest_version',
            'status',
            'locked_at',
            'archived_at',
            'created_at',
            'updated_at',
        },
    )
    for question in data.get('questions', []):
        question.pop('id', None)
        question.pop('position', None)
        for choice in question.get('choices', []):
            choice.pop('id', None)
            choice.pop('position', None)
    return data


class ExperimentWorkflowTable:
    async def list(self, db: AsyncSession, task_model_available: bool = True):
        rows = list(
            (
                await db.execute(
                    select(ExperimentWorkflow).order_by(
                        ExperimentWorkflow.updated_at.desc()
                    )
                )
            ).scalars()
        )
        return [await _model(row, db, task_model_available) for row in rows]

    async def get(
        self,
        workflow_id: str,
        db: AsyncSession,
        task_model_available: bool = True,
    ):
        row = await db.get(ExperimentWorkflow, workflow_id)
        return await _model(row, db, task_model_available) if row else None

    async def create(self, form: WorkflowWriteForm, user_id: str, db: AsyncSession):
        definition = normalize_definition(
            form.definition or default_workflow_definition()
        )
        definition = await _expand_random_all(definition, db)
        now = int(time.time_ns())
        row = ExperimentWorkflow(
            id=str(uuid.uuid4()),
            name=form.name.strip(),
            description=form.description.strip(),
            definition=definition,
            revision=1,
            created_by=user_id,
            updated_by=user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return await _model(row, db)

    async def update(
        self,
        workflow_id: str,
        form: WorkflowWriteForm,
        user_id: str,
        db: AsyncSession,
    ):
        row = (
            await db.execute(
                select(ExperimentWorkflow)
                .where(ExperimentWorkflow.id == workflow_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            return None
        if form.revision is None or form.revision != row.revision:
            raise HTTPException(
                status_code=409,
                detail='This workflow changed after it was opened. Reload it first.',
            )
        definition = normalize_definition(form.definition)
        definition = await _expand_random_all(definition, db)
        row.name = form.name.strip()
        row.description = form.description.strip()
        row.definition = definition
        row.revision += 1
        row.updated_by = user_id
        row.updated_at = int(time.time_ns())
        await db.commit()
        await db.refresh(row)
        return await _model(row, db)

    async def delete(self, workflow_id: str, revision: int, db: AsyncSession):
        row = (
            await db.execute(
                select(ExperimentWorkflow)
                .where(ExperimentWorkflow.id == workflow_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            return False
        if revision != row.revision:
            raise HTTPException(
                status_code=409,
                detail='This workflow changed after it was opened. Reload it first.',
            )
        await db.delete(row)
        await db.commit()
        return True

    async def export(self, workflow_id: str, db: AsyncSession):
        workflow = await db.get(ExperimentWorkflow, workflow_id)
        if not workflow:
            return None
        definition = json.loads(json.dumps(workflow.definition))
        topic_ids, question_ids, survey_ids = _referenced_ids(definition)
        topic_keys = {value: str(uuid.uuid4()) for value in topic_ids}
        question_keys = {value: str(uuid.uuid4()) for value in question_ids}
        survey_keys = {value: str(uuid.uuid4()) for value in survey_ids}
        resources: dict[str, list[dict[str, Any]]] = {
            'essay_topics': [],
            'question_tasks': [],
            'survey_tasks': [],
            'assets': [],
        }
        output = io.BytesIO()
        asset_keys: dict[str, str] = {}
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for topic_id in sorted(topic_ids):
                topic = await db.get(EssayTopic, topic_id)
                if not topic or topic.workflow_managed:
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced essay topic is unavailable.',
                    )
                resources['essay_topics'].append(
                    {
                        'key': topic_keys[topic_id],
                        'title': topic.title,
                        'question': topic.question,
                    }
                )
            portable_question_ids = {}
            for task_id in sorted(question_ids):
                row = await db.get(QuestionTask, task_id)
                task = await QuestionTasks.get_task(task_id, db=db)
                if (
                    not row
                    or row.workflow_managed
                    or row.status != QuestionTaskStatus.PUBLISHED.value
                    or not task
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced Question Task is unavailable.',
                    )
                data = _strip_question_task(task)
                for source_question, question in zip(task.questions, data['questions']):
                    question['key'] = str(uuid.uuid4())
                    portable_question_ids[source_question.id] = question['key']
                data['key'] = question_keys[task_id]
                for question in data.get('questions', []):
                    file_id = question.pop('image_file_id', None)
                    question['image_asset_key'] = None
                    if not file_id:
                        continue
                    if file_id not in asset_keys:
                        file = await db.get(File, file_id)
                        if not file:
                            raise HTTPException(
                                status_code=422,
                                detail='A referenced question image is unavailable.',
                            )
                        image_data = await _read_stored_file(file)
                        content_type = (file.meta or {}).get('content_type', '')
                        _, extension = _validate_image(image_data, content_type)
                        key = str(uuid.uuid4())
                        path = f'assets/{key}{extension}'
                        archive.writestr(path, image_data)
                        asset_keys[file_id] = key
                        resources['assets'].append(
                            {
                                'key': key,
                                'path': path,
                                'filename': file.filename,
                                'content_type': content_type,
                                'size': len(image_data),
                                'sha256': hashlib.sha256(image_data).hexdigest(),
                            }
                        )
                    question['image_asset_key'] = asset_keys[file_id]
                resources['question_tasks'].append(data)
            for task_id in sorted(survey_ids):
                row = await db.get(SurveyTask, task_id)
                task = await SurveyTasks.get_task(task_id, db=db)
                if (
                    not row
                    or row.workflow_managed
                    or row.status != SurveyTaskStatus.PUBLISHED.value
                    or not task
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced Survey Task is unavailable.',
                    )
                data = _strip_survey_task(task)
                data['key'] = survey_keys[task_id]
                resources['survey_tasks'].append(data)

            portable_definition = {
                'progression_mode': definition.get(
                    'progression_mode', ProgressionMode.STRICT_SEQUENTIAL.value
                ),
                'chat_mode': definition.get(
                    'chat_mode', ExperimentChatMode.SHARED_EXPERIMENT.value
                ),
                'consent_enabled': definition.get('consent_enabled', True),
                'items': [],
                'conditions': json.loads(
                    json.dumps(definition.get('conditions', []))
                ),
            }
            for item in definition.get('items', []):
                portable_item = {
                    'key': item.get('key'),
                    'task_type': item.get('task_type'),
                    'title': item.get('title', ''),
                    'enabled': item.get('enabled', True),
                }
                if item.get('task_type') in ('ESSAY', 'QUESTION'):
                    try:
                        portable_item['llm_prompt_budget'] = remap_prompt_budget(
                            item.get('llm_prompt_budget'), portable_question_ids
                        )
                    except ValueError as error:
                        raise HTTPException(status_code=422, detail=str(error))
                if item.get('task_type') == 'ESSAY':
                    topic_id = item.get('essay_topic_id')
                    topic_pool = item.get('essay_topic_ids', [])
                    if not isinstance(topic_pool, list):
                        topic_pool = []
                    portable_item.update(
                        essay_topic_mode=item.get('essay_topic_mode'),
                        essay_topic_key=topic_keys.get(topic_id),
                        essay_topic_keys=[
                            topic_keys[value]
                            for value in topic_pool
                            if value in topic_keys
                        ],
                    )
                elif item.get('task_type') == 'QUESTION':
                    portable_item['question_task_key'] = question_keys.get(
                        item.get('question_task_id')
                    )
                elif item.get('task_type') == 'SURVEY':
                    portable_item.update(
                        survey_task_key=survey_keys.get(
                            item.get('survey_task_id')
                        ),
                        survey_required=item.get('survey_required', True),
                    )
                portable_definition['items'].append(portable_item)
            manifest = {
                'format': WORKFLOW_FORMAT,
                'schema_version': WORKFLOW_SCHEMA_VERSION,
                'workflow': {
                    'name': workflow.name,
                    'description': workflow.description,
                    'definition': portable_definition,
                },
                'resources': resources,
            }
            archive.writestr(
                'manifest.json',
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        return output.getvalue(), f'{_safe_filename(workflow.name)}.owui-workflow.zip'

    async def import_archive(
        self,
        data: bytes,
        user_id: str,
        task_model_available: bool,
        db: AsyncSession,
    ):
        if not data or len(data) > MAX_ARCHIVE_BYTES:
            raise HTTPException(
                status_code=413,
                detail='Workflow archives must be 100 MB or smaller.',
            )
        stored_paths: list[str] = []
        try:
            try:
                archive = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise HTTPException(
                    status_code=422,
                    detail='The uploaded file is not a valid ZIP archive.',
                )
            infos = archive.infolist()
            if (
                len(infos) > MAX_ARCHIVE_ENTRIES
                or sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES
            ):
                raise HTTPException(
                    status_code=413,
                    detail='Workflow archive expands beyond the allowed limit.',
                )
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise HTTPException(
                    status_code=422,
                    detail='Workflow archive contains duplicate paths.',
                )
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name:
                    raise HTTPException(
                        status_code=422,
                        detail='Workflow archive contains an unsafe path.',
                    )
            if 'manifest.json' not in names:
                raise HTTPException(
                    status_code=422,
                    detail='Workflow archive is missing manifest.json.',
                )
            try:
                manifest = json.loads(archive.read('manifest.json'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise HTTPException(
                    status_code=422,
                    detail='Workflow manifest is invalid JSON.',
                )
            if not isinstance(manifest, dict):
                raise HTTPException(
                    status_code=422,
                    detail='Workflow manifest must be a JSON object.',
                )
            schema_version = manifest.get('schema_version')
            if manifest.get('format') != WORKFLOW_FORMAT or schema_version not in {
                LEGACY_WORKFLOW_SCHEMA_VERSION,
                3,
                WORKFLOW_SCHEMA_VERSION,
            }:
                raise HTTPException(
                    status_code=422,
                    detail='Workflow format or schema version is unsupported.',
                )
            payload = manifest.get('workflow')
            resources = manifest.get('resources')
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get('definition'), dict)
                or not isinstance(resources, dict)
            ):
                raise HTTPException(
                    status_code=422,
                    detail='Workflow manifest is incomplete.',
                )
            if schema_version == LEGACY_WORKFLOW_SCHEMA_VERSION:
                payload = {
                    **payload,
                    'definition': _upgrade_v2_definition(payload['definition']),
                }
            for field in ('essay_topics', 'question_tasks', 'survey_tasks', 'assets'):
                if not isinstance(resources.get(field), list):
                    raise HTTPException(
                        status_code=422,
                        detail=f'Workflow resource field {field} must be an array.',
                    )

            keyed: dict[str, dict[str, dict[str, Any]]] = {}
            for field in ('essay_topics', 'question_tasks', 'survey_tasks', 'assets'):
                values: dict[str, dict[str, Any]] = {}
                for value in resources[field]:
                    key = value.get('key') if isinstance(value, dict) else None
                    if not isinstance(key, str) or not key or key in values:
                        raise HTTPException(
                            status_code=422,
                            detail=f'Workflow {field} keys must be unique strings.',
                        )
                    values[key] = value
                keyed[field] = values

            referenced_paths = {'manifest.json'}
            asset_files: dict[str, str] = {}
            for key, asset in keyed['assets'].items():
                asset_path = asset.get('path')
                if (
                    not isinstance(asset_path, str)
                    or asset_path not in names
                    or not asset_path.startswith('assets/')
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='Workflow manifest references a missing asset.',
                    )
                referenced_paths.add(asset_path)
                image_data = archive.read(asset_path)
                if (
                    len(image_data) != asset.get('size')
                    or hashlib.sha256(image_data).hexdigest() != asset.get('sha256')
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='Workflow asset checksum or size does not match.',
                    )
                content_type = asset.get('content_type', '')
                filename = asset.get('filename', Path(asset_path).name)
                if not isinstance(content_type, str) or not isinstance(filename, str):
                    raise HTTPException(
                        status_code=422,
                        detail='Workflow asset metadata is invalid.',
                    )
                _validate_image(image_data, content_type)
                file, path = await _store_file(
                    image_data,
                    filename,
                    content_type,
                    user_id,
                    db,
                )
                stored_paths.append(path)
                asset_files[key] = file.id
            if set(names) != referenced_paths:
                raise HTTPException(
                    status_code=422,
                    detail='Workflow archive contains unreferenced files.',
                )

            now = int(time.time_ns())
            topic_ids: dict[str, str] = {}
            for key, topic in keyed['essay_topics'].items():
                title = str(topic.get('title') or '').strip()
                question = str(topic.get('question') or '').strip()
                if not title or not question:
                    raise HTTPException(
                        status_code=422,
                        detail='Imported essay topics require a title and question.',
                    )
                topic_id = str(uuid.uuid4())
                topic_ids[key] = topic_id
                db.add(
                    EssayTopic(
                        id=topic_id,
                        title=title,
                        question=question,
                        locked_at=None,
                        workflow_managed=False,
                        created_at=now,
                        updated_at=now,
                    )
                )

            question_ids: dict[str, str] = {}
            imported_question_ids = {}
            referenced_asset_keys: set[str] = set()
            for key, task in keyed['question_tasks'].items():
                task_id = str(uuid.uuid4())
                forms = []
                raw_questions = task.get('questions', [])
                if not isinstance(raw_questions, list):
                    raise HTTPException(
                        status_code=422,
                        detail='Question Task questions must be an array.',
                    )
                for question in raw_questions:
                    if not isinstance(question, dict):
                        raise HTTPException(
                            status_code=422,
                            detail='Question Task questions must be objects.',
                        )
                    question = dict(question)
                    question_key = question.pop('key', None)
                    question['id'] = str(uuid.uuid4())
                    if schema_version == WORKFLOW_SCHEMA_VERSION:
                        if not isinstance(question_key, str) or not question_key or question_key in imported_question_ids:
                            raise HTTPException(status_code=422, detail='Imported questions require unique keys.')
                        imported_question_ids[question_key] = question['id']
                    asset_key = question.pop('image_asset_key', None)
                    if asset_key:
                        if asset_key not in asset_files:
                            raise HTTPException(
                                status_code=422,
                                detail='A Question Task references a missing image.',
                            )
                        referenced_asset_keys.add(asset_key)
                    question['image_file_id'] = asset_files.get(asset_key)
                    forms.append(question)
                parsed = QuestionTaskForm.model_validate(
                    {
                        'title': task.get('title'),
                        'description': task.get('description', ''),
                        'questions': forms,
                    }
                )
                if (
                    not task_model_available
                    and any(
                        question.grading_mode == GradingMode.LLM_ASSISTED
                        for question in parsed.questions
                    )
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='An imported Question Task requires an available global task model.',
                    )
                question_ids[key] = task_id
                row = QuestionTask(
                    id=task_id,
                    family_id=task_id,
                    version=1,
                    title=parsed.title,
                    description=parsed.description,
                    status=QuestionTaskStatus.PUBLISHED.value,
                    workflow_managed=False,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
                await QuestionTasks._replace_questions(row, parsed.questions, db)
            if set(asset_files) != referenced_asset_keys:
                raise HTTPException(
                    status_code=422,
                    detail='Workflow archive contains unreferenced assets.',
                )

            survey_ids: dict[str, str] = {}
            for key, task in keyed['survey_tasks'].items():
                parsed = SurveyTaskForm.model_validate(
                    {field: value for field, value in task.items() if field != 'key'}
                )
                task_id = str(uuid.uuid4())
                survey_ids[key] = task_id
                row = SurveyTask(
                    id=task_id,
                    family_id=task_id,
                    version=1,
                    title=parsed.title,
                    description=parsed.description,
                    status=SurveyTaskStatus.PUBLISHED.value,
                    workflow_managed=False,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
                await SurveyTasks._replace_questions(row, parsed.questions, db)

            definition = normalize_definition(payload['definition'])
            used_topics: set[str] = set()
            used_questions: set[str] = set()
            used_surveys: set[str] = set()
            for item in definition.get('items', []):
                if not isinstance(item, dict):
                    continue
                if item.get('task_type') in ('ESSAY', 'QUESTION'):
                    try:
                        item['llm_prompt_budget'] = remap_prompt_budget(item.get('llm_prompt_budget'), imported_question_ids)
                    except ValueError as error:
                        raise HTTPException(status_code=422, detail=str(error))
                if item.get('task_type') == 'ESSAY':
                    topic_key = item.pop('essay_topic_key', None)
                    topic_keys = item.pop('essay_topic_keys', [])
                    if not isinstance(topic_keys, list):
                        raise HTTPException(
                            status_code=422,
                            detail='Workflow essay topic keys must be an array.',
                        )
                    if topic_key:
                        used_topics.add(topic_key)
                        if topic_key not in topic_ids:
                            raise HTTPException(
                                status_code=422,
                                detail='Workflow references a missing essay topic.',
                            )
                    for value in topic_keys:
                        used_topics.add(value)
                        if value not in topic_ids:
                            raise HTTPException(
                                status_code=422,
                                detail='Workflow references a missing essay topic.',
                            )
                    item['essay_topic_id'] = topic_ids.get(topic_key)
                    item['essay_topic_ids'] = [topic_ids[value] for value in topic_keys]
                elif item.get('task_type') == 'QUESTION':
                    task_key = item.pop('question_task_key', None)
                    if task_key:
                        used_questions.add(task_key)
                        if task_key not in question_ids:
                            raise HTTPException(
                                status_code=422,
                                detail='Workflow references a missing Question Task.',
                            )
                    item['question_task_id'] = question_ids.get(task_key)
                elif item.get('task_type') == 'SURVEY':
                    task_key = item.pop('survey_task_key', None)
                    if task_key:
                        used_surveys.add(task_key)
                        if task_key not in survey_ids:
                            raise HTTPException(
                                status_code=422,
                                detail='Workflow references a missing Survey Task.',
                            )
                    item['survey_task_id'] = survey_ids.get(task_key)
            if (
                used_topics != set(topic_ids)
                or used_questions != set(question_ids)
                or used_surveys != set(survey_ids)
            ):
                raise HTTPException(
                    status_code=422,
                    detail='Workflow archive contains unreferenced resources.',
                )

            workflow_id = str(uuid.uuid4())
            row = ExperimentWorkflow(
                id=workflow_id,
                name=str(payload.get('name') or '').strip()[:500]
                or 'Imported workflow',
                description=str(payload.get('description') or '').strip()[:20000],
                definition=definition,
                revision=1,
                created_by=user_id,
                updated_by=user_id,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
            result = await _model(row, db, task_model_available)
            await db.commit()
            return result
        except ValidationError as error:
            await db.rollback()
            for path in stored_paths:
                try:
                    await asyncio.to_thread(Storage.delete_file, path)
                except Exception:
                    pass
            raise HTTPException(
                status_code=422,
                detail={'message': 'Workflow resource definition is invalid.', 'issues': error.errors()},
            )
        except Exception:
            await db.rollback()
            for path in stored_paths:
                try:
                    await asyncio.to_thread(Storage.delete_file, path)
                except Exception:
                    pass
            raise

    async def apply(
        self,
        workflow_id: str,
        form: WorkflowApplyForm,
        user_id: str,
        task_model_available: bool,
        db: AsyncSession,
    ):
        workflow = (
            await db.execute(
                select(ExperimentWorkflow)
                .where(ExperimentWorkflow.id == workflow_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not workflow:
            raise HTTPException(status_code=404, detail='Workflow not found.')
        if workflow.revision != form.workflow_revision:
            raise HTTPException(
                status_code=409,
                detail='This workflow changed after review. Review it again.',
            )
        issues = await _validation_issues(
            workflow.definition, db, task_model_available
        )
        if issues:
            raise HTTPException(
                status_code=422,
                detail={
                    'message': 'Workflow is not ready to apply.',
                    'issues': [issue.model_dump() for issue in issues],
                },
            )
        group = (
            await db.execute(
                select(Group).where(Group.id == form.group_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=404, detail='Group not found.')
        if not (group.permissions or {}).get('features', {}).get(
            'essay_sidebar', False
        ):
            raise HTTPException(
                status_code=422,
                detail='Experiment Task Sidebar permission is required.',
            )
        current = (
            (
                await db.execute(
                    select(ExperimentPlan)
                    .where(
                        ExperimentPlan.group_id == form.group_id,
                        ExperimentPlan.status == 'PUBLISHED',
                    )
                    .order_by(ExperimentPlan.version.desc())
                    .limit(1)
                )
            ).scalars().first()
        )
        current_version = current.version if current else 0
        if current_version != form.expected_current_plan_version:
            raise HTTPException(
                status_code=409,
                detail='The group plan changed after review. Review it again.',
            )

        definition = workflow.definition
        topic_source_ids, question_source_ids, survey_source_ids = _referenced_ids(
            definition
        )
        now = int(time.time_ns())
        stored_paths: list[str] = []
        try:
            topic_ids: dict[str, str] = {}
            for source_id in sorted(topic_source_ids):
                source = (
                    await db.execute(
                        select(EssayTopic)
                        .where(EssayTopic.id == source_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if not source or source.workflow_managed:
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced essay topic became unavailable.',
                    )
                topic_id = str(uuid.uuid4())
                topic_ids[source_id] = topic_id
                db.add(
                    EssayTopic(
                        id=topic_id,
                        title=source.title,
                        question=source.question,
                        locked_at=now,
                        workflow_managed=True,
                        created_at=now,
                        updated_at=now,
                    )
                )

            copied_question_ids = {}
            copied_files: dict[str, str] = {}
            question_ids: dict[str, str] = {}
            for source_id in sorted(question_source_ids):
                source_row = (
                    await db.execute(
                        select(QuestionTask)
                        .where(QuestionTask.id == source_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if (
                    not source_row
                    or source_row.workflow_managed
                    or source_row.status != QuestionTaskStatus.PUBLISHED.value
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced Question Task became unavailable.',
                    )
                source = await QuestionTasks.get_task(source_id, db=db)
                task_id = str(uuid.uuid4())
                question_ids[source_id] = task_id
                row = QuestionTask(
                    id=task_id,
                    family_id=task_id,
                    version=1,
                    title=source.title,
                    description=source.description,
                    status=QuestionTaskStatus.PUBLISHED.value,
                    locked_at=now,
                    workflow_managed=True,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
                data = _strip_question_task(source)
                for source_question, question in zip(source.questions, data['questions']):
                    question['id'] = str(uuid.uuid4())
                    copied_question_ids[source_question.id] = question['id']
                for question in data.get('questions', []):
                    source_file_id = question.get('image_file_id')
                    if source_file_id and source_file_id not in copied_files:
                        source_file = await db.get(File, source_file_id)
                        if not source_file:
                            raise HTTPException(
                                status_code=422,
                                detail='A Question Task image is unavailable.',
                            )
                        image_data = await _read_stored_file(source_file)
                        content_type = (source_file.meta or {}).get(
                            'content_type', ''
                        )
                        copied, path = await _store_file(
                            image_data,
                            source_file.filename,
                            content_type,
                            user_id,
                            db,
                        )
                        stored_paths.append(path)
                        copied_files[source_file_id] = copied.id
                    question['image_file_id'] = copied_files.get(source_file_id)
                parsed = QuestionTaskForm.model_validate(data)
                await QuestionTasks._replace_questions(row, parsed.questions, db)

            survey_ids: dict[str, str] = {}
            for source_id in sorted(survey_source_ids):
                source_row = (
                    await db.execute(
                        select(SurveyTask)
                        .where(SurveyTask.id == source_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if (
                    not source_row
                    or source_row.workflow_managed
                    or source_row.status != SurveyTaskStatus.PUBLISHED.value
                ):
                    raise HTTPException(
                        status_code=422,
                        detail='A referenced Survey Task became unavailable.',
                    )
                source = await SurveyTasks.get_task(source_id, db=db)
                task_id = str(uuid.uuid4())
                survey_ids[source_id] = task_id
                row = SurveyTask(
                    id=task_id,
                    family_id=task_id,
                    version=1,
                    title=source.title,
                    description=source.description,
                    status=SurveyTaskStatus.PUBLISHED.value,
                    locked_at=now,
                    workflow_managed=True,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.flush()
                parsed = SurveyTaskForm.model_validate(_strip_survey_task(source))
                await SurveyTasks._replace_questions(row, parsed.questions, db)

            if current:
                current.status = 'SUPERSEDED'
                current.updated_at = now
            version = (
                await db.execute(
                    select(func.max(ExperimentPlan.version)).where(
                        ExperimentPlan.group_id == form.group_id
                    )
                )
            ).scalar() or 0
            plan = ExperimentPlan(
                id=str(uuid.uuid4()),
                source_workflow_id=workflow.id,
                source_workflow_name=workflow.name,
                source_workflow_revision=workflow.revision,
                workflow_origin='recorded',
                group_id=form.group_id,
                version=version + 1,
                status='PUBLISHED',
                progression_mode=definition.get(
                    'progression_mode', ProgressionMode.STRICT_SEQUENTIAL.value
                ),
                chat_mode=definition.get(
                    'chat_mode', ExperimentChatMode.SHARED_EXPERIMENT.value
                ),
                survey_variant=(
                    'TASK_NEUTRAL'
                    if len(definition.get('items', [])) > 1
                    or any(
                        item.get('task_type') == 'QUESTION'
                        for item in definition.get('items', [])
                    )
                    else 'ESSAY'
                ),
                consent_enabled=definition.get('consent_enabled', True),
                created_at=now,
                updated_at=now,
            )
            db.add(plan)
            item_pairs = []
            for position, item in enumerate(definition.get('items', [])):
                item_id = str(uuid.uuid4())
                item_pairs.append((item, item_id))
                db.add(
                    ExperimentPlanItem(
                        id=item_id,
                        source_step_key=item['key'],
                        plan_id=plan.id,
                        position=position,
                        task_type=item['task_type'],
                        title=item['title'].strip(),
                        llm_prompt_budget=remap_prompt_budget(item.get('llm_prompt_budget'), copied_question_ids) if item['task_type'] != 'SURVEY' else None,
                        question_task_id=question_ids.get(
                            item.get('question_task_id')
                        ),
                        essay_topic_mode=item.get('essay_topic_mode'),
                        essay_topic_id=topic_ids.get(item.get('essay_topic_id')),
                        survey_task_id=survey_ids.get(item.get('survey_task_id')),
                        survey_required=item.get('survey_required', True),
                        enabled=item.get('enabled', True),
                    )
                )
                for source_topic_id in dict.fromkeys(
                    item.get('essay_topic_ids', [])
                ):
                    if source_topic_id in topic_ids:
                        db.add(
                            ExperimentPlanItemTopic(
                                plan_item_id=item_id,
                                topic_id=topic_ids[source_topic_id],
                            )
                        )
            await db.flush()
            item_key_map = {
                item['key']: item_id for item, item_id in item_pairs
            }
            conditions = json.loads(
                json.dumps(definition.get('conditions', []))
            )
            for condition in conditions:
                condition['id'] = None
                condition.pop('key', None)
                activation = condition['prompt_injection']['activation']
                activation['plan_item_ids'] = [
                    item_key_map[key]
                    for key in activation.get('plan_item_ids', [])
                    if key in item_key_map
                ]
            plan_form = ExperimentPlanForm.model_validate(
                {
                    'progression_mode': plan.progression_mode,
                    'chat_mode': plan.chat_mode,
                    'consent_enabled': plan.consent_enabled,
                    'items': [
                        {
                            'id': item_id,
                            'task_type': item['task_type'],
                            'title': item['title'],
                            'enabled': item.get('enabled', True),
                            'question_task_id': question_ids.get(
                                item.get('question_task_id')
                            ),
                            'llm_prompt_budget': remap_prompt_budget(item.get('llm_prompt_budget'), copied_question_ids) if item['task_type'] != 'SURVEY' else None,
                            'survey_task_id': survey_ids.get(
                                item.get('survey_task_id')
                            ),
                            'survey_required': item.get(
                                'survey_required', True
                            ),
                            'essay_topic_mode': item.get('essay_topic_mode'),
                            'essay_topic_id': topic_ids.get(
                                item.get('essay_topic_id')
                            ),
                            'essay_topic_ids': [
                                topic_ids[value]
                                for value in item.get('essay_topic_ids', [])
                                if value in topic_ids
                            ],
                        }
                        for item, item_id in item_pairs
                    ],
                    'conditions': conditions,
                }
            )
            await ExperimentPlans._save_conditions(
                plan.id,
                plan_form.conditions,
                list(zip(plan_form.items, [value for _, value in item_pairs])),
                True,
                db,
            )
            await db.flush()
            applied_conditions = list((await db.execute(select(ExperimentCondition).where(
                ExperimentCondition.plan_id == plan.id
            ).order_by(ExperimentCondition.position))).scalars())
            for applied, original in zip(applied_conditions, definition.get('conditions', [])):
                applied.source_condition_key = original.get('key')
            from open_webui.utils.workflow_provenance import fingerprint_plan
            plan.configuration_fingerprint = await fingerprint_plan(db, plan.id)
            result = await ExperimentPlans.get_plan(plan.id, db=db)
            await db.commit()
            return result
        except Exception:
            await db.rollback()
            for path in stored_paths:
                try:
                    await asyncio.to_thread(Storage.delete_file, path)
                except Exception:
                    pass
            raise


ExperimentWorkflows = ExperimentWorkflowTable()
