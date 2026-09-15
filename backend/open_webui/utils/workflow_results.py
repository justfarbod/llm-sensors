"""Shared workflow identity, message attribution and step-based result projections."""
from collections import Counter, defaultdict
import json
import statistics
import time
from sqlalchemy import select, or_
from fastapi import HTTPException
from open_webui.models.experiment_plans import ExperimentPlan, ExperimentPlanItem, ExperimentSessionTask
from open_webui.models.experiment_perturbations import ExperimentCondition, ExperimentLLMRequest
from open_webui.models.experiments import ExperimentSession
from open_webui.models.chats import Chat
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.users import User
from open_webui.models.groups import Group
from open_webui.models.essays import Essay
from open_webui.models.question_submissions import QuestionSubmission

NS = 1_000_000_000


def workflow_id(plan):
    return plan.source_workflow_id or f'legacy:{plan.id}'


def configuration_id(plan):
    return plan.configuration_fingerprint or f'legacy:{plan.id}'


def condition_key(condition):
    return condition.source_condition_key or f'position:{condition.position}'


def step_key(item):
    return item.source_step_key or f'position:{item.position}'


def identity(plan, group=None):
    if not plan:
        return {'workflow_id': None, 'workflow_name': 'Historical experiment', 'configuration_id': None,
                'source_revision': None, 'workflow_origin': 'unknown', 'plan_version': None}
    return {'workflow_id': workflow_id(plan),
            'workflow_name': plan.source_workflow_name or f'Historical workflow — {group.name if group else plan.group_id} / v{plan.version}',
            'configuration_id': configuration_id(plan), 'source_revision': plan.source_workflow_revision,
            'workflow_origin': plan.workflow_origin or 'unknown', 'plan_version': plan.version}


def demo(user):
    return bool(user and isinstance(user.info, dict) and user.info.get('synthetic') is True)


def empty_usage():
    return dict(prompts=0, responses=0, input_tokens=0, output_tokens=0, total_tokens=0)


def add_usage(target, source):
    for key in empty_usage():
        target[key] += source.get(key, 0)


async def usage_by_session(db, session_ids):
    if not session_ids:
        return {}
    sessions = list((await db.execute(select(ExperimentSession).where(ExperimentSession.id.in_(session_ids)))).scalars())
    tasks = list((await db.execute(select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id.in_(session_ids)))).scalars())
    task_map = {task.id: task.experiment_session_id for task in tasks}
    users = {session.user_id for session in sessions}
    records = (await db.execute(select(ChatMessage, Chat).outerjoin(Chat, Chat.id == ChatMessage.chat_id)
                              .where(ChatMessage.user_id.in_(users)))).all()
    result = {s.id: {**empty_usage(), 'by_task': {}, 'unattributed': empty_usage(), 'attribution': 'none', 'message_ids': []} for s in sessions}
    session_map = {s.id: s for s in sessions}
    linked, legacy = [], []
    for message, chat in records:
        task_id = message.experiment_session_task_id
        explicit = bool(task_id or (chat and (chat.experiment_session_id or chat.experiment_session_task_id)))
        sid = task_map.get(task_id) if task_id else (
            (chat.experiment_session_id or task_map.get(chat.experiment_session_task_id)) if chat else None)
        if not task_id and chat and chat.experiment_session_task_id:
            task_id = chat.experiment_session_task_id
        if sid in session_map and session_map[sid].user_id == message.user_id:
            linked.append((sid, task_id if task_id in task_map else None, message))
        elif not explicit:
            legacy.append(message)
    linked_sessions = {sid for sid, _, _ in linked}
    # A linked empty chat is still evidence of modern attribution. Never pull an
    # unrelated personal chat into a modern run merely because no prompt was sent.
    explicit_sessions = set((await db.execute(select(Chat.experiment_session_id).where(Chat.user_id.in_(users), Chat.experiment_session_id.isnot(None)))).scalars())
    all_sessions = list((await db.execute(select(ExperimentSession).where(ExperimentSession.user_id.in_(users)))).scalars())
    for message in legacy:
        candidates = [s.id for s in all_sessions if not s.plan_id and s.id not in explicit_sessions and s.id not in linked_sessions and s.user_id == message.user_id
                      and (s.writing_started_at or s.created_at) // NS <= (message.created_at or 0)
                      <= (s.completed_at or s.essay_submitted_at or time.time_ns()) // NS]
        if len(candidates) == 1 and candidates[0] in result:
            linked.append((candidates[0], None, message))
    seen = set()
    for sid, task_id, message in linked:
        if message.id in seen:
            continue
        seen.add(message.id)
        row = result[sid]
        row['message_ids'].append(message.id)
        row['attribution'] = 'linked' if sid in linked_sessions else 'legacy_time_window'
        usage = empty_usage()
        usage['prompts'] = int(message.role == 'user')
        usage['responses'] = int(message.role == 'assistant')
        if message.role == 'assistant':
            tokens = message.usage or {}
            usage['input_tokens'] = tokens.get('input_tokens', tokens.get('prompt_tokens', 0)) or 0
            usage['output_tokens'] = tokens.get('output_tokens', tokens.get('completion_tokens', 0)) or 0
            usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        add_usage(row, usage)
        if task_id:
            add_usage(row['by_task'].setdefault(task_id, empty_usage()), usage)
        else:
            add_usage(row['unattributed'], usage)
    return result


async def catalog(db):
    plans = list((await db.execute(select(ExperimentPlan).order_by(ExperimentPlan.created_at.desc()))).scalars())
    groups = {g.id: g for g in (await db.execute(select(Group))).scalars()}
    conditions = list((await db.execute(select(ExperimentCondition).order_by(ExperimentCondition.position))).scalars())
    return plans, groups, conditions


async def enrich_participants(db, rows, filters):
    plans, groups, conditions = await catalog(db)
    plan_map = {p.id: p for p in plans}
    current = {p.group_id: p for p in reversed(plans) if p.status == 'PUBLISHED'}
    sessions = {s.id: s for s in (await db.execute(select(ExperimentSession).where(ExperimentSession.id.in_([r['session_id'] for r in rows if r['session_id']])))).scalars()}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_([r['user_id'] for r in rows])))).scalars()}
    condition_map = {c.id: c for c in conditions}
    task_objects = list((await db.execute(select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id.in_(sessions)))).scalars())
    task_states = await native_task_states(db, task_objects)
    result = []
    for row in rows:
        session = sessions.get(row['session_id'])
        plan = plan_map.get(session.plan_id) if session else current.get(row['group_id'])
        context = identity(plan, groups.get(row['group_id']))
        condition = condition_map.get(session.condition_id) if session else None
        is_demo = demo(users.get(row['user_id']))
        if filters.workflow_id and context['workflow_id'] != filters.workflow_id:
            continue
        if filters.configuration_id and context['configuration_id'] != filters.configuration_id:
            continue
        if filters.condition_key and (not condition or condition_key(condition) != filters.condition_key):
            continue
        if filters.data_kind != 'all' and is_demo != (filters.data_kind == 'demo'):
            continue
        task_rows = row.get('tasks', [])
        for task in task_rows:
            task['native_status'] = task['status']
            task['status'] = task_states.get(task['session_task_id'], task['status'])
        row.update(context, is_demo=is_demo, condition_name=condition.name if condition else None,
                   condition_key=condition_key(condition) if condition else None,
                   task_progress={'total': len(task_rows), 'completed': sum(t['status'] == 'FINALIZED' for t in task_rows),
                                  'skipped': sum(t['status'] == 'SKIPPED' for t in task_rows),
                                  'resolved': sum(t['status'] in ('FINALIZED', 'SKIPPED') for t in task_rows)})
        result.append(row)
    return result


async def workflow_list(db, filters):
    from open_webui.routers import experiment_analytics as a
    plans, groups, conditions = await catalog(db)
    sessions = await a._sessions(db, filters)
    counts = Counter(s.plan_id for s in sessions)
    all_counts = Counter((await db.execute(select(ExperimentSession.plan_id))).scalars())
    by_workflow = {}
    for plan in plans:
        if not all_counts[plan.id] and plan.status != 'PUBLISHED':
            continue
        info = identity(plan, groups.get(plan.group_id))
        if filters.workflow_id and info['workflow_id'] != filters.workflow_id:
            continue
        if filters.group_id and plan.group_id != filters.group_id:
            continue
        if filters.configuration_id and info['configuration_id'] != filters.configuration_id:
            continue
        workflow = by_workflow.setdefault(info['workflow_id'], {**info, 'id': info['workflow_id'], 'name': info['workflow_name'],
            'runs': 0, 'completed': 0, 'active': 0, 'configurations': [], 'groups': []})
        workflow['runs'] += counts[plan.id]
        workflow['completed'] += sum(s.plan_id == plan.id and s.state == 'COMPLETED' for s in sessions)
        workflow['active'] += sum(s.plan_id == plan.id and s.state in {v.value for v in a.ACTIVE_STATES} for s in sessions)
        config = next((c for c in workflow['configurations'] if c['id'] == info['configuration_id']), None)
        if config is None:
            config = {'id': info['configuration_id'], 'source_revision': plan.source_workflow_revision,
                      'origin': plan.workflow_origin or 'unknown', 'created_at': plan.created_at // NS, 'runs': 0, 'plan_ids': []}
            workflow['configurations'].append(config)
        config['runs'] += counts[plan.id]
        config['plan_ids'].append(plan.id)
        group = {'id': plan.group_id, 'name': groups[plan.group_id].name if plan.group_id in groups else plan.group_id}
        if group not in workflow['groups']:
            workflow['groups'].append(group)
    return {'items': list(by_workflow.values()), 'total': len(by_workflow)}


async def workflow_detail(db, workflow_key, filters):
    from open_webui.routers import experiment_analytics as a
    scoped = filters.model_copy(update={'workflow_id': workflow_key})
    listing = await workflow_list(db, scoped.model_copy(update={'configuration_id': None}))
    if not listing['items']:
        raise HTTPException(404, 'Workflow has no matching deployments')
    result = listing['items'][0]
    chosen = filters.configuration_id or next((c['id'] for c in result['configurations'] if c['runs']), result['configurations'][0]['id'])
    if chosen not in {c['id'] for c in result['configurations']}:
        raise HTTPException(404, 'Workflow configuration is unavailable')
    scoped.configuration_id = chosen
    sessions = await a._sessions(db, scoped)
    ids = [s.id for s in sessions]
    plans, groups, conditions = await catalog(db)
    matching = [p for p in plans if workflow_id(p) == workflow_key and configuration_id(p) == chosen
                and (not filters.group_id or p.group_id == filters.group_id)]
    pids = [p.id for p in matching]
    items = list((await db.execute(select(ExperimentPlanItem).where(ExperimentPlanItem.plan_id.in_(pids)).order_by(ExperimentPlanItem.position))).scalars())
    tasks = list((await db.execute(select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id.in_(ids)))).scalars())
    submissions = {s.session_task_id: s for s in (await db.execute(select(QuestionSubmission).where(QuestionSubmission.session_task_id.in_([t.id for t in tasks])))).scalars()}
    essays = {e.id: e for e in (await db.execute(select(Essay).where(Essay.id.in_([t.essay_id for t in tasks if t.essay_id])))).scalars()}
    usage = await usage_by_session(db, ids)
    task_states = await native_task_states(db, tasks)
    steps = []
    for position in sorted({i.position for i in items if i.enabled}):
        sources = [i for i in items if i.position == position and i.enabled]
        item = sources[0]
        relevant = [t for t in tasks if t.plan_item_id in {i.id for i in sources}]
        elapsed = [(t.finalized_at or t.completed_at) / NS - t.started_at / NS for t in relevant if t.started_at and (t.finalized_at or t.completed_at)]
        grades = [submissions[t.id] for t in relevant if t.id in submissions]
        final_grades = [s for s in grades if s.grading_status == 'GRADED' and s.current_score is not None]
        totals = empty_usage()
        for task in relevant:
            add_usage(totals, usage.get(task.experiment_session_id, {}).get('by_task', {}).get(task.id, {}))
        topics = defaultdict(list)
        for task in relevant:
            if task.essay_id in essays:
                essay = essays[task.essay_id]
                topics[(task.essay_topic_title, task.essay_topic_question)].append(essay.word_count or 0)
        steps.append({'key': step_key(item), 'position': position, 'title': item.title, 'task_type': item.task_type,
            'required': item.survey_required if item.task_type == 'SURVEY' else True,
            'llm_prompt_budget': item.llm_prompt_budget, 'assigned': len(relevant),
            'started': sum(t.started_at is not None for t in relevant),
            'completed': sum(task_states[t.id] == 'FINALIZED' for t in relevant), 'skipped': sum(task_states[t.id] == 'SKIPPED' for t in relevant),
            'pending': sum(task_states[t.id] not in ('FINALIZED', 'SKIPPED') for t in relevant),
            'average_elapsed_seconds': round(statistics.mean(elapsed), 2) if elapsed else None,
            'average_score': round(statistics.mean(float(s.current_score) for s in final_grades), 2) if final_grades else None,
            'maximum_score': float(grades[0].maximum_score) if grades else None,
            'graded': len(final_grades), 'pending_grades': len(grades) - len(final_grades),
            'essay_topics': [{'title': title, 'question': question, 'submissions': len(words), 'average_words': round(statistics.mean(words), 1),
                             'minimum_words': min(words), 'maximum_words': max(words)} for (title, question), words in topics.items()],
            'usage': totals})
    unique_conditions = {}
    for condition in conditions:
        if condition.plan_id in pids:
            unique_conditions.setdefault(condition_key(condition), {'key': condition_key(condition), 'name': condition.name,
                'allocation_percent': condition.allocation_percent, 'runs': 0})['runs'] += sum(s.condition_id == condition.id for s in sessions)
    result.update(configuration_id=chosen, runs=len(sessions), completed=sum(s.state == 'COMPLETED' for s in sessions),
                  progression_mode=matching[0].progression_mode, chat_mode=matching[0].chat_mode,
                  consent_enabled=matching[0].consent_enabled, steps=steps, conditions=list(unique_conditions.values()))
    return result


async def step_results(db, workflow_key, key, filters, page, limit, search=None):
    from open_webui.routers import experiment_analytics as a
    detail = await workflow_detail(db, workflow_key, filters)
    step = next((s for s in detail['steps'] if s['key'] == key), None)
    if not step:
        raise HTTPException(404, 'Workflow step is unavailable')
    scoped = filters.model_copy(update={'workflow_id': workflow_key, 'configuration_id': detail['configuration_id']})
    sessions = await a._sessions(db, scoped)
    session_map = {s.id: s for s in sessions}
    users = await a._users_by_ids(db, [s.user_id for s in sessions])
    tasks = list((await db.execute(select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id.in_(session_map),
                                                                 ExperimentSessionTask.position == step['position']).order_by(ExperimentSessionTask.created_at.desc(), ExperimentSessionTask.id))).scalars())
    if search:
        tasks = [t for t in tasks if search.casefold() in (getattr(users.get(session_map[t.experiment_session_id].user_id), 'name', '') or '').casefold()]
    total = len(tasks)
    # Aggregate surveys across matching configurations, keeping question positions
    # scoped to this step. Full response text is loaded only for the selected page.
    distributions = {}
    if step['task_type'] == 'SURVEY':
        from open_webui.models.survey_submissions import SurveySubmission, SurveyResponse, SurveyResponseChoice
        from open_webui.models.survey_tasks import SurveyQuestion, SurveyChoice
        survey_rows = (await db.execute(select(SurveyQuestion, SurveyResponse).join(SurveyResponse, SurveyResponse.question_id == SurveyQuestion.id)
            .join(SurveySubmission, SurveySubmission.id == SurveyResponse.submission_id)
            .where(SurveySubmission.session_task_id.in_([t.id for t in tasks]), SurveySubmission.status == 'SUBMITTED'))).all()
        choices = (await db.execute(select(SurveyResponseChoice, SurveyChoice).join(SurveyChoice, SurveyChoice.id == SurveyResponseChoice.choice_id)
                                  .where(SurveyResponseChoice.response_id.in_([r.id for _, r in survey_rows])))).all()
        by_response = defaultdict(list)
        for selection, choice in choices:
            by_response[selection.response_id].append(choice.text)
        for q, response in survey_rows:
            group = distributions.setdefault(q.position, {'position': q.position, 'prompt': q.prompt, 'question_type': q.question_type,
                                                          'answered': 0, 'counts': Counter()})
            group['answered'] += int(response.is_answered)
            if q.question_type == 'SCALE' and response.scale_answer is not None:
                group['counts'][str(response.scale_answer)] += 1
            for label in by_response[response.id]:
                group['counts'][label] += 1
    selected = tasks[(page - 1) * limit:page * limit]
    details = {r['session_task_id']: r for r in await a._session_task_details(db, selected)}
    essay_ids = [t.essay_id for t in selected if t.essay_id]
    essays = await a._essays_by_ids(db, essay_ids)
    rows = []
    for task in selected:
        session = session_map[task.experiment_session_id]
        user = users.get(session.user_id)
        record = details[task.id]
        record.update(session_id=session.id, name=user.name if user else 'Unavailable', participant_id=a._anonymous_id(session.user_id),
                      is_demo=demo(user), group_id=session.group_id)
        if task.task_type == 'ESSAY':
            essay = essays.get(task.essay_id)
            record['essay'] = {'content': essay.content if essay else task.essay_draft, 'topic_title': task.essay_topic_title,
                               'topic_question': task.essay_topic_question, 'word_count': essay.word_count if essay else None, 'is_draft': essay is None}
        rows.append(record)
    return {'step': step, 'configuration_id': detail['configuration_id'], 'items': rows, 'total': total, 'page': page, 'limit': limit,
            'survey_distributions': [{**d, 'counts': dict(d['counts'])} for _, d in sorted(distributions.items())]}


async def usage_dimensions(db, sessions, usage):
    plans, groups, conditions = await catalog(db)
    plan_map = {p.id: p for p in plans}
    condition_map = {c.id: c for c in conditions}
    tasks = list((await db.execute(select(ExperimentSessionTask).where(ExperimentSessionTask.experiment_session_id.in_([s.id for s in sessions])))).scalars())
    task_map = {t.id: t for t in tasks}
    dimensions = {key: {} for key in ('workflow', 'condition', 'step')}
    unattributed = empty_usage()
    for session in sessions:
        plan = plan_map.get(session.plan_id)
        context = identity(plan, groups.get(session.group_id))
        condition = condition_map.get(session.condition_id)
        row = usage.get(session.id, {})
        for dimension, key, label in [('workflow', context['workflow_id'], context['workflow_name']),
                                       ('condition', (context['configuration_id'], condition_key(condition) if condition else None), condition.name if condition else 'Implicit control')]:
            target = dimensions[dimension].setdefault(str(key), {'label': label, **context, **empty_usage()})
            add_usage(target, row)
        add_usage(unattributed, row.get('unattributed', {}))
        for task_id, values in row.get('by_task', {}).items():
            task = task_map[task_id]
            key = (context['workflow_id'], context['configuration_id'], task.position)
            target = dimensions['step'].setdefault(str(key), {'label': task.title, 'position': task.position, **context, **empty_usage()})
            add_usage(target, values)
    from open_webui.models.experiment_perturbations import ExperimentLLMRequest
    requests = list((await db.execute(select(ExperimentLLMRequest).where(ExperimentLLMRequest.experiment_session_id.in_([s.id for s in sessions])))).scalars())
    delays = [(r.artificial_delay_ended_at-r.artificial_delay_started_at)/NS for r in requests if r.artificial_delay_started_at and r.artificial_delay_ended_at]
    return {**{f'by_{key}': list(values.values()) for key, values in dimensions.items()},
            'unattributed': unattributed, 'legacy_fallback_sessions': sum(r.get('attribution') == 'legacy_time_window' for r in usage.values()),
            'request_timing': {'requests': len(requests), 'delayed_responses': len(delays), 'average_artificial_delay_seconds': statistics.mean(delays) if delays else None}}


async def attach_task_activity(db, session, tasks, usage):
    from open_webui.models.experiment_telemetry import ExperimentTelemetryEvent
    events = list((await db.execute(select(ExperimentTelemetryEvent).where(ExperimentTelemetryEvent.experiment_session_id == session.id).order_by(ExperimentTelemetryEvent.event_time))).scalars())
    records = (await db.execute(select(ChatMessage, Chat).outerjoin(Chat, Chat.id == ChatMessage.chat_id).where(
        ChatMessage.id.in_(usage.get('message_ids', []))).order_by(ChatMessage.created_at, ChatMessage.id))).all()
    grouped = defaultdict(list)
    request_rows = list((await db.execute(select(ExperimentLLMRequest).where(ExperimentLLMRequest.experiment_session_id == session.id))).scalars())
    request_map = {r.assistant_message_id: r for r in request_rows if r.assistant_message_id}
    tids = {task['session_task_id'] for task in tasks}
    for message, chat in records:
        tid = message.experiment_session_task_id or (chat.experiment_session_task_id if chat else None)
        request = request_map.get(message.id)
        grouped[tid if tid in tids else None].append({'id': message.id, 'role': message.role, 'content': message.content,
            'model_id': message.model_id, 'created_at': message.created_at, 'request_id': request.id if request else None})
    for task in tasks:
        tid = task['session_task_id']
        task['usage'] = usage.get('by_task', {}).get(tid, empty_usage())
        task['messages'] = grouped[tid]
        task['telemetry'] = {'event_counts': dict(Counter(e.event_type for e in events if e.session_task_id == tid)),
            'events': [{'type': e.event_type, 'field': e.field_context, 'time': e.event_time/NS, 'payload': e.payload_json} for e in events if e.session_task_id == tid]}
    return grouped[None]


async def native_task_states(db, tasks):
    """A finalized task without its native submission is still missing work."""
    from open_webui.models.survey_submissions import SurveySubmission
    ids = [t.id for t in tasks]
    questions = {s.session_task_id: s for s in (await db.execute(select(QuestionSubmission).where(QuestionSubmission.session_task_id.in_(ids)))).scalars()}
    surveys = {s.session_task_id: s for s in (await db.execute(select(SurveySubmission).where(SurveySubmission.session_task_id.in_(ids)))).scalars()}
    essays = set((await db.execute(select(Essay.id).where(Essay.id.in_([t.essay_id for t in tasks if t.essay_id])))).scalars())
    result = {}
    for task in tasks:
        submission = (questions if task.task_type == 'QUESTION' else surveys).get(task.id)
        if task.task_type == 'SURVEY' and not task.survey_required and (task.status == 'SKIPPED' or (submission and submission.status == 'SKIPPED')):
            result[task.id] = 'SKIPPED'
        elif task.status == 'FINALIZED' and (task.essay_id in essays if task.task_type == 'ESSAY' else submission and submission.status in ({'SUBMITTED', 'FINALIZED'} if task.task_type == 'QUESTION' else {'SUBMITTED'})):
            result[task.id] = 'FINALIZED'
        else:
            result[task.id] = 'IN_PROGRESS' if task.started_at else 'PENDING'
    return result


async def overview_steps(db, filters):
    workflows = (await workflow_list(db, filters))['items']
    result = []
    for workflow in workflows:
        for config in workflow['configurations']:
            if config['runs']:
                detail = await workflow_detail(db, workflow['id'], filters.model_copy(update={'configuration_id': config['id']}))
                result.append({'workflow_id': workflow['id'], 'name': workflow['name'], 'configuration_id': config['id'], 'steps': detail['steps']})
    return result
