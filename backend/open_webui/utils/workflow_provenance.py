"""Content-based identity for immutable applied workflow configurations."""
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from open_webui.models.experiment_plans import ExperimentPlan, ExperimentPlanItem, ExperimentPlans
from open_webui.models.experiment_perturbations import ExperimentCondition
from open_webui.models.essays import EssayTopic
from open_webui.models.files import File
from open_webui.models.question_tasks import QuestionTasks
from open_webui.models.survey_tasks import SurveyTasks


def content_hash(value):
    # JSON treats 1 and 1.0 differently; resolved Pydantic numeric defaults do not.
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(value) for key, value in item.items()}
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    value = normalize(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


async def configuration_content(db, *, plan_id=None, definition=None):
    from open_webui.models.experiment_workflows import _strip_question_task, _strip_survey_task
    if plan_id:
        plan = await ExperimentPlans.get_plan(plan_id, db=db)
        definition = plan.model_dump(mode='json')
    result = {key: definition.get(key, default) for key, default in
              [('progression_mode', 'STRICT_SEQUENTIAL'), ('chat_mode', 'SHARED_EXPERIMENT'), ('consent_enabled', True)]}
    items = definition.get('items', [])
    positions = {item.get('key') or item.get('id'): i for i, item in enumerate(items)}
    result['items'] = []
    for item in items:
        entry = {key: item.get(key, default) for key, default in
                 [('task_type', None), ('title', ''), ('enabled', True), ('survey_required', True)]}
        if item['task_type'] == 'QUESTION':
            task = await QuestionTasks.get_task(item['question_task_id'], db=db)
            if not task:
                raise ValueError('Workflow question task is unavailable')
            entry['task'] = _strip_question_task(task)
            for question in entry['task']['questions']:
                file_id = question.pop('image_file_id', None)
                if file_id:
                    file = await db.get(File, file_id)
                    if file and file.path and Path(file.path).is_file():
                        question['image_content'] = hashlib.sha256(Path(file.path).read_bytes()).hexdigest()
                    else:
                        question['image_content'] = (file.hash if file else None) or f'unverified:{file_id}'
            budget = dict(item.get('llm_prompt_budget') or {'mode': 'TASK', 'limit': 100, 'question_limits': {}})
            if budget['mode'] == 'PER_QUESTION':
                mapping = {question.id: str(i) for i, question in enumerate(task.questions)}
                budget['question_limits'] = {mapping[key]: value for key, value in budget['question_limits'].items()}
            entry['llm_prompt_budget'] = budget
        elif item['task_type'] == 'SURVEY':
            task = await SurveyTasks.get_task(item['survey_task_id'], db=db)
            if not task:
                raise ValueError('Workflow survey task is unavailable')
            entry['task'] = _strip_survey_task(task)
        else:
            entry['essay_topic_mode'] = item.get('essay_topic_mode')
            ids = [item['essay_topic_id']] if item.get('essay_topic_mode') == 'SPECIFIC' else item.get('essay_topic_ids', [])
            topics = []
            for topic_id in ids:
                topic = await db.get(EssayTopic, topic_id)
                if not topic:
                    raise ValueError('Workflow essay topic is unavailable')
                topics.append({'title': topic.title, 'question': topic.question})
            entry['topics'] = sorted(topics, key=lambda topic: (topic['title'], topic['question']))
            entry['llm_prompt_budget'] = item.get('llm_prompt_budget') or {'mode': 'TASK', 'limit': 100, 'question_limits': {}}
        result['items'].append(entry)
    result['conditions'] = []
    for condition in definition.get('conditions', []):
        condition = json.loads(json.dumps(condition))
        for key in ('id', 'key', 'source_condition_key'):
            condition.pop(key, None)
        activation = condition['prompt_injection']['activation']
        activation['plan_item_ids'] = sorted(positions[key] for key in activation.get('plan_item_ids', []))
        result['conditions'].append(condition)
    return result


async def fingerprint_plan(db, plan_id):
    return content_hash(await configuration_content(db, plan_id=plan_id))


async def backfill_provenance(db):
    """Resolve legacy fingerprints; infer only the previously identified Grade 10 origin.

    Revision numbers cannot be reconstructed from a mutable library, so inferred
    origins deliberately retain a null source revision.
    """
    from open_webui.models.experiment_workflows import ExperimentWorkflow
    plans = list((await db.execute(select(ExperimentPlan))).scalars())
    for plan in plans:
        if not plan.configuration_fingerprint:
            try:
                plan.configuration_fingerprint = await fingerprint_plan(db, plan.id)
            except (ValueError, KeyError):
                # Missing old content must not prevent other histories being displayed.
                continue
    plan = next((p for p in plans if p.id == '7848f5d2-0c2b-4c15-83d5-140432bfd4c7'), None)
    source = await db.get(ExperimentWorkflow, '640b0bdc-df29-4695-9a16-51944ff5a340')
    linked = False
    if plan and source and not plan.source_workflow_id:
        signature = content_hash(await configuration_content(db, definition=source.definition))
        if signature == plan.configuration_fingerprint:
            plan.source_workflow_id = source.id
            plan.source_workflow_name = source.name
            plan.source_workflow_revision = None
            plan.workflow_origin = 'inferred'
            items = list((await db.execute(select(ExperimentPlanItem).where(ExperimentPlanItem.plan_id == plan.id).order_by(ExperimentPlanItem.position))).scalars())
            for item, original in zip(items, source.definition['items']):
                item.source_step_key = original['key']
            conditions = list((await db.execute(select(ExperimentCondition).where(ExperimentCondition.plan_id == plan.id).order_by(ExperimentCondition.position))).scalars())
            for condition, original in zip(conditions, source.definition['conditions']):
                condition.source_condition_key = original['key']
            linked = True
    await db.flush()
    return {'fingerprinted_plans': sum(bool(p.configuration_fingerprint) for p in plans), 'grade10_linked': linked}
