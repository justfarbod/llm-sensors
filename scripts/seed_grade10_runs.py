#!/usr/bin/env python3
"""Seed curated Grade 10 demonstrations into the inspected SQLite plan.

Run with backend/venv/bin/python. Default is read-only preview; --apply creates
an online SQLite backup and inserts atomically. No application startup, account
credentials, provider calls, migrations, or edits to existing records occur.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sqlite3
import time
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'scripts/fixtures/grade10_runs.json'
NS = 1_000_000_000
MS = 1_000_000
NAMESPACE = uuid.UUID('b68ab008-2f70-4abe-8c51-94353387b21c')
RUBRIC_MAXIMA = {
    'B4': [1, 2, 2, 1],
    'B5': [1, 1, 1, 1, 1, 1],
    'G4': [1, 2, 1, 1, 1],
    'G8': [2, 2, 1, 1],
    'G10': [2, 2, 1, 1, 1, 1],
}


def require(test, message):
    if not test:
        raise ValueError(message)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def identifier(*parts):
    return str(uuid.uuid5(NAMESPACE, ':'.join(map(str, parts))))


def read_helpers():
    """Load the actual pure repository helpers without importing DB startup.

    AST selection is deliberately limited to named functions; their bodies and
    annotations are executed unchanged. This avoids importing models which can
    migrate the default database even during a preview.
    """
    env = {
        'Decimal': Decimal,
        'ROUND_HALF_UP': ROUND_HALF_UP,
        'SCORE_QUANTUM': Decimal('0.01'),
        'ExperimentTelemetrySummary': object,
        'TelemetryEventForm': object,
        'time': time,
    }
    sources = {
        'models/question_tasks.py': {
            'score_value',
            'grade_single_choice',
            'grade_multiple_select',
            'normalize_blank',
            'grade_fill_blanks',
        },
        'routers/experiment_telemetry.py': {'_apply_to_summary'},
    }
    for relative, names in sources.items():
        path = ROOT / 'backend/open_webui' / relative
        tree = ast.parse(path.read_text())
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        require({node.name for node in body} == names, f'Repository helpers changed: {relative}')
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), env)
    spec = importlib.util.spec_from_file_location('seed_essay_metrics', ROOT / 'backend/open_webui/utils/essay_text.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env['essay_text_metrics'] = module.essay_text_metrics
    return env


HELPERS = read_helpers()


def connect(path, readonly=True):
    require(path.is_file(), f'Database does not exist: {path}')
    db = sqlite3.connect(path.resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if readonly:
        db.execute('PRAGMA query_only=ON')
    return db


def rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]


def snapshot(db, plan_id):
    plans = rows(db, 'SELECT * FROM experiment_plan WHERE id=?', (plan_id,))
    require(len(plans) == 1, 'Expected published plan is missing')
    items = rows(db, 'SELECT * FROM experiment_plan_item WHERE plan_id=? ORDER BY position', (plan_id,))
    result = {
        'plan': plans[0],
        'items': items,
        'conditions': rows(db, 'SELECT * FROM experiment_condition WHERE plan_id=? ORDER BY position', (plan_id,)),
        'questions': {},
        'surveys': {},
        'condition_settings': {},
    }
    for item in items:
        if item['question_task_id']:
            for question in rows(
                db,
                'SELECT * FROM question_task_question WHERE task_id=? ORDER BY position',
                (item['question_task_id'],),
            ):
                qid = question['id']
                question['choices'] = rows(
                    db, 'SELECT * FROM question_choice WHERE question_id=? ORDER BY position', (qid,)
                )
                question['blanks'] = rows(
                    db, 'SELECT * FROM question_blank WHERE question_id=? ORDER BY position', (qid,)
                )
                for blank in question['blanks']:
                    blank['accepted'] = rows(
                        db,
                        'SELECT * FROM question_blank_accepted_answer WHERE blank_id=? ORDER BY position',
                        (blank['id'],),
                    )
                question['rubric'] = rows(db, 'SELECT * FROM question_free_text_config WHERE question_id=?', (qid,))
                result['questions'][question['title'].split(' ')[0]] = question
        elif item['survey_task_id']:
            questions = rows(
                db, 'SELECT * FROM survey_question WHERE task_id=? ORDER BY position', (item['survey_task_id'],)
            )
            for question in questions:
                question['choices'] = rows(
                    db, 'SELECT * FROM survey_choice WHERE question_id=? ORDER BY position', (question['id'],)
                )
            result['surveys'][str(item['position'])] = questions
        elif item['essay_topic_id']:
            result['essay_topic'] = rows(db, 'SELECT * FROM essay_topic WHERE id=?', (item['essay_topic_id'],))[0]
    for condition in result['conditions']:
        result['condition_settings'][condition['id']] = {
            table: rows(db, f'SELECT * FROM {table} WHERE condition_id=?', (condition['id'],))
            for table in (
                'experiment_prompt_injection',
                'experiment_warning_modal',
                'experiment_response_timing',
                'experiment_condition_task_scope',
            )
        }
    return result


def check_target(db, fixture):
    expected = fixture['snapshot']
    actual = snapshot(db, expected['plan']['id'])
    # Provenance is additive metadata, not experiment content. Keep fixture hashes immutable.
    for field in ('source_workflow_id', 'source_workflow_name', 'source_workflow_revision', 'workflow_origin', 'configuration_fingerprint'):
        actual['plan'].pop(field, None)
    for item in actual['items']:
        item.pop('source_step_key', None)
    for condition in actual['conditions']:
        condition.pop('source_condition_key', None)
    require(
        actual == expected,
        'Published plan, questions, sources, or conditions changed; review the fixture before applying',
    )
    plan = actual['plan']
    require(plan['version'] == 13 and plan['status'] == 'PUBLISHED', 'Expected published plan version 13')
    published = rows(db, "SELECT id FROM experiment_plan WHERE group_id=? AND status='PUBLISHED'", (plan['group_id'],))
    require(published == [{'id': plan['id']}], 'Target group has a different active plan')
    group = rows(db, 'SELECT name,data FROM "group" WHERE id=?', (plan['group_id'],))
    require(group and group[0]['name'] == 'experiment Group A', 'Expected experiment Group A')
    require(json.loads(group[0]['data'])['config'].get('experiment_mode_enabled'), 'Group experiment mode is disabled')
    require(not list(db.execute('PRAGMA foreign_key_check')), 'Database has existing foreign-key violations')


def check_fixture(fixture):
    require(fixture['synthetic'] is True and fixture['batch_id'] == 'grade10-demo-v1', 'Expected synthetic batch')
    require([run['number'] for run in fixture['runs']] == list(range(1, 11)), 'Expected exactly ten numbered runs')
    snap = fixture['snapshot']
    require(len(snap['items']) == 6 and len(snap['questions']) == 15, 'Unexpected task topology')
    for run in fixture['runs']:
        require(set(run['answers']) == set(snap['questions']), 'Missing or extra academic answers')
        wc, _ = HELPERS['essay_text_metrics'](run['essay'])
        require(200 <= wc <= 300, f'Essay length out of bounds: {run["number"]}')
        require(len(run['chats']) == sum(run['request_counts']), 'Request count mismatch')
        require(
            [sum(chat['task'] == position for chat in run['chats']) for position in (1, 2, 4)] == run['request_counts'],
            'Task request count mismatch',
        )
        require(
            [chat['task'] for chat in run['chats']] == sorted(chat['task'] for chat in run['chats']),
            'Chat tasks must be sequential',
        )
        for chat in run['chats']:
            if 'question' in chat:
                require(
                    snap['questions'][chat['question']]['task_id'] == snap['items'][chat['task']]['question_task_id'],
                    'Chat question belongs to another task',
                )
            if 'paste_into_essay' in chat:
                require(
                    chat['paste_into_essay'] in run['essay'] and chat['paste_into_essay'] in chat['assistant'],
                    'Paste must match both assistant text and essay',
                )
        for key, answer in run['answers'].items():
            q = snap['questions'][key]
            if q['choices']:
                require(
                    len(answer['choices']) == len(set(answer['choices']))
                    and set(answer['choices']) <= set(range(len(q['choices']))),
                    'Invalid choices',
                )
                if q['question_type'] == 'SINGLE_CHOICE':
                    require(len(answer['choices']) == 1, 'Single-choice answer must choose exactly one')
            elif q['blanks']:
                require(set(answer['blanks']) == {b['blank_key'] for b in q['blanks']}, 'Invalid blanks')
            else:
                points = answer['rubric_points']
                maximum = RUBRIC_MAXIMA[key]
                require(
                    len(points) == len(maximum) and all(0 <= p <= m for p, m in zip(points, maximum)),
                    'Invalid rubric points',
                )
                require(sum(maximum) == q['max_score'], 'Rubric maximum mismatch')
                require(
                    answer['text'] and answer['rationale'].startswith('Synthetic'),
                    'Missing synthetic review provenance',
                )
        for position, questions in snap['surveys'].items():
            values = run['surveys'][position]
            require(len(values) == len(questions), 'Survey answer count mismatch')
            for q, value in zip(questions, values):
                if q['choices']:
                    require(
                        value and len(value) == len(set(value)) and set(value) <= set(range(len(q['choices']))),
                        'Invalid survey choices',
                    )
                    if q['question_type'] == 'SINGLE_CHOICE':
                        require(len(value) == 1, 'Invalid single-choice survey response')
                elif q['question_type'] == 'SCALE':
                    require(isinstance(value, int) and 1 <= value <= 5, 'Invalid survey scale')
                else:
                    require(isinstance(value, str) and bool(value.strip()), 'Missing survey text')
            if position == '0':
                require(3 not in values[0] or values[0] == [3], 'Exclusive topic option combined with others')
            if position == '5':
                require(
                    not set(values[3]) & {4, 5} or len(values[3]) == 1, 'Exclusive checking option combined with others'
                )
    require(len({run['essay'] for run in fixture['runs']}) == 10, 'Essays must be distinct')


def grade(question, answer):
    maximum = Decimal(str(question['max_score']))
    if question['choices']:
        selected = {question['choices'][i]['id'] for i in answer['choices']}
        correct = {choice['id'] for choice in question['choices'] if choice['is_correct']}
        if question['question_type'] == 'SINGLE_CHOICE':
            return HELPERS['grade_single_choice'](maximum, selected, next(iter(correct)))
        return HELPERS['grade_multiple_select'](maximum, selected, correct, {x['id'] for x in question['choices']})
    if question['blanks']:
        supplied = {b['id']: answer['blanks'][b['blank_key']] for b in question['blanks']}
        accepted = {b['id']: [a['answer'] for a in b['accepted']] for b in question['blanks']}
        return HELPERS['grade_fill_blanks'](maximum, supplied, accepted, bool(question['case_sensitive']))
    return Decimal(sum(answer['rubric_points']))


class Batch:
    def __init__(self, fixture, anchor):
        self.fixture = fixture
        self.anchor = anchor
        self.tables = defaultdict(list)
        self.report = []
        self.fixture_hash = digest(fixture)
        self.task_intervals = {}

    def add(self, table, **values):
        self.tables[table].append(values)
        return values

    def build(self):
        for run in self.fixture['runs']:
            self.build_run(run)
        return self

    def build_run(self, run):
        f = self.fixture
        snap = f['snapshot']
        plan = snap['plan']
        n = run['number']
        uid = identifier(f['batch_id'], n, 'user')
        sid = identifier(f['batch_id'], n, 'session')
        chatid = identifier(f['batch_id'], n, 'chat')
        eid = identifier(f['batch_id'], n, 'essay')
        name = f'Grade 10 Demo {n:02}'
        # All profiles form one synthetic class, starting at small offsets.
        start = self.anchor + (n - 1) * 27 * NS
        finish = start + run['duration_minutes'] * 60 * NS
        rng = random.Random(f'{f["batch_id"]}:{n}')
        conditions = dict(zip(('Control', 'Tutor', 'Reminder', 'Delay'), snap['conditions']))
        condition = conditions[run['condition']]
        cid = condition['id']
        assignment = 'synthetic-selected:' + identifier(sid, 'assignment')
        info = {
            'synthetic': True,
            'seed_batch': f['batch_id'],
            'fixture_sha256': self.fixture_hash,
            'seed_anchor_ns': self.anchor,
            'profile': run['profile'],
            'condition_selection': 'deliberate demonstration coverage, not randomized assignment',
            'reviewer': f['reviewer'],
            'simulated_model': f['model_id'],
            'token_usage': 'Synthetic estimates: ceiling of character count divided by four; not provider measurements',
        }
        self.add(
            'user',
            id=uid,
            name=name,
            email=f'grade10-demo-{n:02}@example.invalid',
            role='user',
            profile_image_url='/user.png',
            created_at=(start - 60 * NS) // NS,
            updated_at=finish // NS,
            last_active_at=finish // NS,
            bio='Synthetic Grade 10 demonstration participant. No real student or login credentials.',
            info=dump(info),
            settings='{}',
        )
        self.add(
            'group_member',
            id=identifier(uid, 'membership'),
            group_id=plan['group_id'],
            user_id=uid,
            created_at=(start - 30 * NS) // NS,
            updated_at=(start - 30 * NS) // NS,
        )
        task_ids = [identifier(sid, 'task', i) for i in range(6)]
        # Leave 75 seconds for consent/orientation and 15 for the final acknowledgment.
        available = finish - start - 90 * NS
        weights = [3, 12, 15, 2, 25, 3]
        cursor = start + 75 * NS
        tasks = []
        for item, weight in zip(snap['items'], weights):
            position = item['position']
            end = cursor + available * weight // 60 if position < 5 else finish - 15 * NS
            values = {
                key: item[key]
                for key in (
                    'position',
                    'task_type',
                    'title',
                    'question_task_id',
                    'survey_task_id',
                    'survey_required',
                    'llm_prompt_budget',
                )
            }
            values.update(
                id=task_ids[position],
                experiment_session_id=sid,
                plan_item_id=item['id'],
                status='FINALIZED',
                created_at=start,
                updated_at=end,
                started_at=cursor,
                completed_at=end,
                finalized_at=end,
            )
            if position == 2:
                topic = snap['essay_topic']
                values.update(
                    essay_topic_id=topic['id'],
                    essay_topic_title=topic['title'],
                    essay_topic_question=topic['question'],
                    essay_id=eid,
                    essay_draft=run['essay'],
                )
            tasks.append(self.add('experiment_session_task', **values))
            self.task_intervals[task_ids[position]] = (cursor, end)
            cursor = end
        topic = snap['essay_topic']
        self.add(
            'experiment_session',
            id=sid,
            user_id=uid,
            group_id=plan['group_id'],
            plan_id=plan['id'],
            state='COMPLETED',
            task_type='SURVEY',
            topic_id=topic['id'],
            topic_title=topic['title'],
            topic_question=topic['question'],
            essay_id=eid,
            consented_at=start + 45 * NS,
            pre_survey=None,
            post_survey=None,
            # Native survey milestones are stored on task submissions, not legacy survey columns.
            writing_started_at=tasks[2]['started_at'],
            topic_shown_at=tasks[2]['started_at'],
            essay_submitted_at=tasks[2]['completed_at'],
            task_submitted_at=tasks[5]['completed_at'],
            completed_at=finish,
            created_at=start,
            updated_at=finish,
            condition_id=cid,
            condition_assignment_id=assignment,
            condition_assignment_draw=None,
        )
        wc, cc = HELPERS['essay_text_metrics'](run['essay'])
        self.add(
            'essay',
            id=eid,
            user_id=uid,
            topic_id=topic['id'],
            topic_title=topic['title'],
            topic_question=topic['question'],
            content=run['essay'],
            word_count=wc,
            character_count=cc,
            experiment_session_task_id=task_ids[2],
            created_at=tasks[2]['completed_at'],
            updated_at=tasks[2]['completed_at'],
        )
        submissions = {}
        scores = {}
        for position in (1, 4):
            task = tasks[position]
            subid = identifier(task['id'], 'submission')
            submissions[position] = subid
            relevant = {key: q for key, q in snap['questions'].items() if q['task_id'] == task['question_task_id']}
            score = sum((grade(q, run['answers'][key]) for key, q in relevant.items()), Decimal(0))
            scores[position] = float(score)
            self.add(
                'question_submission',
                id=subid,
                session_task_id=task['id'],
                user_id=uid,
                status='FINALIZED',
                grading_status='GRADED',
                current_score=float(score),
                provisional_score=float(score),
                maximum_score=sum(q['max_score'] for q in relevant.values()),
                has_grading_error=0,
                submitted_at=task['completed_at'],
                created_at=task['started_at'],
                updated_at=finish + NS,
            )
            for key, q in relevant.items():
                answer = run['answers'][key]
                rid = identifier(subid, key)
                mark = float(grade(q, answer))
                manual = q['grading_mode'] == 'MANUAL'
                rationale = f'{f["reviewer"]}. {answer["rationale"]}' if manual else None
                self.add(
                    'question_response',
                    id=rid,
                    submission_id=subid,
                    question_id=q['id'],
                    free_text_answer=answer.get('text'),
                    is_answered=1,
                    grading_status='GRADED',
                    generated_score=None if manual else mark,
                    effective_score=mark,
                    grading_method='MANUAL' if manual else 'AUTOMATIC',
                    rationale=rationale,
                    created_at=task['started_at'],
                    updated_at=finish + NS if manual else task['completed_at'],
                )
                if manual:
                    # The reviewer is explicitly synthetic; do not attribute an override to a real admin.
                    self.add(
                        'question_grading_attempt',
                        id=identifier(rid, 'review'),
                        response_id=rid,
                        method='MANUAL',
                        status='GRADED',
                        model_id=None,
                        awarded_score=mark,
                        rationale=rationale,
                        started_at=finish,
                        completed_at=finish + NS,
                        created_at=finish,
                    )
                for index in answer.get('choices', []):
                    self.add('question_response_choice', response_id=rid, choice_id=q['choices'][index]['id'])
                for blank in q['blanks']:
                    self.add(
                        'question_response_blank',
                        response_id=rid,
                        blank_id=blank['id'],
                        answer=answer['blanks'][blank['blank_key']],
                    )
        for pos, questions in snap['surveys'].items():
            task = tasks[int(pos)]
            subid = identifier(task['id'], 'survey')
            self.add(
                'survey_submission',
                id=subid,
                session_task_id=task['id'],
                user_id=uid,
                status='SUBMITTED',
                submitted_at=task['completed_at'],
                created_at=task['started_at'],
                updated_at=task['completed_at'],
            )
            for q, value in zip(questions, run['surveys'][pos]):
                rid = identifier(subid, q['id'])
                self.add(
                    'survey_response',
                    id=rid,
                    submission_id=subid,
                    question_id=q['id'],
                    text_answer=value if isinstance(value, str) else None,
                    scale_answer=value if isinstance(value, int) else None,
                    is_answered=1,
                    created_at=task['started_at'],
                    updated_at=task['completed_at'],
                )
                if isinstance(value, list):
                    for index in value:
                        self.add('survey_response_choice', response_id=rid, choice_id=q['choices'][index]['id'])
        events = []

        def event(kind, at, pos=None, field='unknown', question=None, payload=None, **context):
            tid = task_ids[pos] if pos is not None else None
            qid = snap['questions'][question]['id'] if question else None
            record = dict(
                id=identifier(sid, 'event', len(events)),
                user_id=uid,
                experiment_session_id=sid,
                event_type=kind,
                event_time=int(at),
                field_context=field,
                payload_json=dump(payload or {}),
                created_at=int(at),
                session_task_id=tid,
                question_id=qid,
                submission_id=submissions.get(pos) if question else None,
                condition_id=cid,
                plan_id=plan['id'],
                schema_version=1,
                **context,
            )
            events.append(record)

        if run['condition'] == 'Reminder':
            displayed, acknowledged = start + 50 * NS, start + 66 * NS
            self.add(
                'experiment_warning_state',
                id=identifier(sid, 'warning'),
                experiment_session_id=sid,
                condition_id=cid,
                plan_id=plan['id'],
                active_elapsed_ms=0,
                last_trigger_key='beginning',
                last_token=identifier(sid, 'warning-token'),
                displayed_at=displayed,
                acknowledged_at=acknowledged,
                created_at=displayed,
                updated_at=acknowledged,
            )
            event(
                'warning_modal_displayed',
                displayed,
                payload={'display_reason': 'beginning', 'prompt_count': 0, 'active_elapsed_ms': 0},
            )
            event(
                'warning_modal_acknowledged',
                acknowledged,
                payload={
                    'display_reason': 'beginning',
                    'prompt_count': 0,
                    'active_elapsed_ms': 0,
                    'displayed_at': displayed,
                    'acknowledged_at': acknowledged,
                },
            )
        history = {}
        parent = None
        sequence = 0
        for pos in (1, 2, 4):
            task = tasks[pos]
            task_chats = [chat for chat in run['chats'] if chat['task'] == pos]
            bucket = task['id'] + ':TASK'
            self.add(
                'experiment_prompt_bucket',
                id=bucket,
                session_task_id=task['id'],
                scope_key='TASK',
                used=len(task_chats),
                pending=0,
            )
            # Compile operations first, then spread thinking time between them.
            operations = []

            def typing(text, field, question=None):
                presses = []
                for index, char in enumerate(text):
                    interval = rng.randint(max(60, run['typing_interval_ms'] - 80), run['typing_interval_ms'] + 95)
                    if index and index % 137 == 0:
                        interval += rng.randint(2100, 4400)
                    presses.append((interval, 'whitespace' if char.isspace() else 'printable', rng.randint(55, 145)))
                    if index and index % 89 == 0:
                        # One temporary typo, then backspace, then the intended next character.
                        presses.extend([(130, 'printable', 70), (170, 'backspace', 75)])
                operations.append(('typing', sum(p[0] for p in presses) * MS, (presses, field, question)))

            for chat in task_chats:
                typing(chat['user'], 'chat')
                duration = (2400 + rng.randint(0, 1600) + (3000 if run['condition'] == 'Delay' else 0)) * MS
                operations.append(('request', duration, chat))
            # Focus periods occur during independent drafting, with no overlapping keystrokes.
            if pos == 2:
                for seconds in run['focus_away_seconds']:
                    operations.append(('away', seconds * NS, None))
                pasted = ''.join(chat.get('paste_into_essay', '') for chat in task_chats)
                if pasted:
                    operations.append(('paste', NS, pasted))
                typing(run['essay'].replace(pasted, '', 1) if pasted else run['essay'], 'essay')
                if n in (1, 4, 7, 8, 9):
                    operations.append(('copy', NS, run['essay'].split('.')[0] + '.'))
            else:
                relevant = [
                    (key, q) for key, q in snap['questions'].items() if q['task_id'] == task['question_task_id']
                ]
                for key, q in relevant:
                    answer = run['answers'][key]
                    content = answer.get('text') or ' '.join(answer.get('blanks', {}).values())
                    if content:
                        typing(content, 'question', key)
                    operations.append(('answer', NS, (key, q['question_type'].lower())))
            needed = sum(op[1] for op in operations)
            span = task['completed_at'] - task['started_at']
            require(needed + 10 * NS < span, f'Typing and requests exceed task duration: participant {n}, task {pos}')
            gap = (span - needed) // (len(operations) + 1)
            at = task['started_at'] + gap
            for kind, duration, data in operations:
                if kind == 'typing':
                    presses, field, question = data
                    for index, (interval, keyclass, hold) in enumerate(presses):
                        at += interval * MS
                        event(
                            'keystroke',
                            at,
                            pos,
                            field,
                            question,
                            {
                                'key_class': keyclass,
                                'inter_key_interval_ms': interval if index else None,
                                'hold_duration_ms': hold,
                                'modifiers': {'ctrl': False, 'alt': False, 'shift': False, 'meta': False},
                            },
                        )
                elif kind == 'request':
                    sequence += 1
                    request_id = identifier(sid, 'request', sequence)
                    umid, amid = identifier(request_id, 'user'), identifier(request_id, 'assistant')
                    delayed = run['condition'] == 'Delay'
                    provider_start = at + 60 * MS
                    first_token = at + 500 * MS
                    provider_end = at + duration - (3200 if delayed else 200) * MS
                    visible_start = provider_end + 3 * NS if delayed else first_token + 40 * MS
                    emit_end = provider_end + (3000 if delayed else 20) * MS
                    visible_end = emit_end + 100 * MS
                    timing = {
                        key: value
                        for key, value in snap['condition_settings'][cid]['experiment_response_timing'][0].items()
                        if key != 'condition_id'
                    }
                    self.add(
                        'experiment_llm_request',
                        id=request_id,
                        experiment_session_id=sid,
                        condition_id=cid,
                        plan_id=plan['id'],
                        plan_version=plan['version'],
                        session_task_id=task['id'],
                        chat_id=chatid,
                        user_message_id=umid,
                        assistant_message_id=amid,
                        request_sequence=sequence,
                        prompt_number=sequence,
                        assignment_identifier=assignment,
                        prompt_active=int(run['condition'] == 'Tutor'),
                        prompt_draw=None,
                        prompt_randomization_id=None,
                        timing_mode='DELAYED' if delayed else 'NORMAL',
                        timing_parameters=dump(timing),
                        request_at=at,
                        provider_started_at=provider_start,
                        provider_first_token_at=first_token,
                        provider_completed_at=provider_end,
                        artificial_delay_started_at=provider_end if delayed else None,
                        artificial_delay_ended_at=provider_end + 3 * NS if delayed else None,
                        server_first_emit_at=provider_end + 3 * NS if delayed else first_token + 20 * MS,
                        server_completed_emit_at=emit_end,
                        client_first_visible_at=visible_start,
                        client_completed_visible_at=visible_end,
                        buffered=int(delayed),
                        streaming_completed_normally=1,
                        navigated_away=0,
                        status='COMPLETED',
                        reveal_cursor=len(data['assistant']),
                        created_at=at,
                        updated_at=visible_end,
                    )
                    self.add(
                        'experiment_prompt_reservation',
                        id=identifier(request_id, 'reservation'),
                        bucket_id=bucket,
                        status='COMPLETED',
                        lease_expires_at=at + 120 * NS,
                        created_at=at,
                    )
                    for event_type, stamp in [
                        ('response_first_visible', visible_start),
                        ('response_completed_visible', visible_end),
                    ]:
                        event(
                            event_type,
                            stamp,
                            pos,
                            'chat',
                            payload={
                                'client_timestamp': datetime.fromtimestamp(stamp / NS, timezone.utc).isoformat(),
                                'received_at': stamp,
                                'source': 'client',
                            },
                            request_id=request_id,
                            chat_id=chatid,
                            message_id=amid,
                        )
                    context_chars = sum(len(message['content']) for message in history.values()) + len(data['user'])
                    token_usage = {
                        'input_tokens': (context_chars + 3) // 4,
                        'output_tokens': (len(data['assistant']) + 3) // 4,
                    }
                    token_usage['total_tokens'] = sum(token_usage.values())
                    for mid, role, text, stamp in [
                        (umid, 'user', data['user'], at),
                        (amid, 'assistant', data['assistant'], visible_end),
                    ]:
                        if parent:
                            history[parent]['childrenIds'].append(mid)
                        msg = {
                            'id': mid,
                            'role': role,
                            'content': text,
                            'parentId': parent,
                            'childrenIds': [],
                            'timestamp': stamp // NS,
                            'done': True,
                            'experiment_session_task_id': task['id'],
                        }
                        if role == 'assistant':
                            msg.update(
                                model=f['model_id'], modelName='Synthetic Grade 10 demo', modelIdx=0, usage=token_usage
                            )
                        else:
                            msg['models'] = [f['model_id']]
                        history[mid] = msg
                        self.add(
                            'chat_message',
                            id=mid,
                            chat_id=chatid,
                            user_id=uid,
                            role=role,
                            parent_id=parent,
                            content=dump(text),
                            model_id=f['model_id'] if role == 'assistant' else None,
                            usage=dump(token_usage) if role == 'assistant' else None,
                            done=1,
                            created_at=stamp // NS,
                            updated_at=stamp // NS,
                            experiment_session_task_id=task['id'],
                        )
                        parent = mid
                    at += duration
                elif kind == 'away':
                    event('focus_away', at, pos, 'essay')
                    event('visibility_change', at, pos, 'essay', payload={'visibility': 'hidden'})
                    at += duration
                    event('focus_return', at, pos, 'essay', payload={'away_duration_ms': duration // MS})
                    event('visibility_change', at, pos, 'essay', payload={'visibility': 'visible'})
                elif kind in ('copy', 'paste'):
                    event(
                        kind, at, pos, 'essay', payload={'text_length': len(data), 'line_count': len(data.splitlines())}
                    )
                    at += duration
                elif kind == 'answer':
                    key, control = data
                    event('answer_change', at, pos, 'question', key, {'control_type': control, 'answered': True})
                    at += duration
                at += gap
            require(at <= task['completed_at'], 'Task timeline overflow')
        chat_payload = {
            'id': chatid,
            'title': f'{name} · Synthetic experiment',
            'models': [f['model_id']],
            'history': {'messages': history, 'currentId': parent},
            'messages': list(history.values()),
            'timestamp': start // NS,
            'params': {},
            'files': [],
        }
        self.add(
            'chat',
            id=chatid,
            user_id=uid,
            title=chat_payload['title'],
            archived=0,
            pinned=0,
            created_at=min(m['timestamp'] for m in history.values()),
            updated_at=max(m['timestamp'] for m in history.values()),
            chat=dump(chat_payload),
            meta=dump({'synthetic': True, 'seed_batch': f['batch_id'], 'fixture_sha256': self.fixture_hash}),
            experiment_session_id=sid,
            experiment_session_task_id=None,
        )
        self.tables['experiment_telemetry_event'].extend(
            sorted(events, key=lambda event: (event['event_time'], event['id']))
        )
        summary = SimpleNamespace(
            **{
                key: 0
                for key in (
                    'total_keystrokes',
                    'pause_count',
                    'longest_pause_ms',
                    'copy_count',
                    'cut_count',
                    'paste_count',
                    'total_pasted_chars',
                    'tab_switch_count',
                    'total_time_away_ms',
                    'inter_key_interval_total_ms',
                    'inter_key_interval_sample_count',
                    'key_hold_duration_total_ms',
                    'key_hold_duration_sample_count',
                )
            },
            avg_inter_key_interval_ms=None,
            avg_key_hold_duration_ms=None,
        )
        for record in events:
            payload = json.loads(record['payload_json'])
            event_obj = SimpleNamespace(type=record['event_type'], **payload)
            HELPERS['_apply_to_summary'](summary, event_obj)
        summary.updated_at = finish
        self.add(
            'experiment_telemetry_summary',
            id=sid,
            user_id=uid,
            experiment_session_id=sid,
            created_at=start,
            **vars(summary),
        )
        self.report.append(
            {
                'participant': name,
                'user_id': uid,
                'session_id': sid,
                'condition': run['condition'],
                'biology_score': scores[1],
                'geometry_score': scores[4],
                'total_score': scores[1] + scores[4],
                'maximum_score': 60,
                'essay_words': wc,
                'duration_minutes': run['duration_minutes'],
                'ai_requests': sequence,
                'telemetry_events': len(events),
            }
        )

    def validate(self, now):
        counts = {table: len(values) for table, values in self.tables.items()}
        for table, count in {
            'user': 10,
            'group_member': 10,
            'experiment_session': 10,
            'experiment_session_task': 60,
            'question_submission': 20,
            'question_response': 150,
            'essay': 10,
            'survey_submission': 30,
            'survey_response': 130,
            'chat': 10,
            'chat_message': 212,
            'experiment_llm_request': 106,
            'experiment_prompt_bucket': 30,
            'experiment_prompt_reservation': 106,
            'question_grading_attempt': 50,
            'experiment_warning_state': 2,
        }.items():
            require(counts.get(table) == count, f'Unexpected {table} count: {counts.get(table)}')
        require(self.anchor > self.fixture['snapshot']['plan']['created_at'] + 60 * NS, 'Runs precede plan publication')
        require(
            all(row['status'] == 'SUBMITTED' for row in self.tables['survey_submission']),
            'Survey submissions must use the native SUBMITTED status',
        )
        for session in self.tables['experiment_session']:
            require(
                session['created_at'] < session['consented_at'] < session['completed_at'] < now - NS,
                'Session timestamp out of bounds',
            )
        for event in self.tables['experiment_telemetry_event']:
            if event['session_task_id']:
                lo, hi = self.task_intervals[event['session_task_id']]
                require(lo <= event['event_time'] <= hi, 'Event outside task')
            if event['field_context'] == 'question':
                require(event['question_id'] and event['submission_id'], 'Missing question event context')
            payload = json.loads(event['payload_json'])
            require(
                not {'text', 'content', 'raw', 'key', 'clipboard', 'password'} & set(payload), 'Raw input in telemetry'
            )
        for request in self.tables['experiment_llm_request']:
            lo, hi = self.task_intervals[request['session_task_id']]
            require(
                lo
                <= request['request_at']
                < request['provider_started_at']
                < request['provider_first_token_at']
                < request['provider_completed_at']
                <= request['server_completed_emit_at']
                <= request['client_completed_visible_at']
                < hi,
                'Invalid request timeline',
            )
            if request['timing_mode'] == 'DELAYED':
                require(
                    request['artificial_delay_ended_at'] - request['artificial_delay_started_at'] == 3 * NS,
                    'Delay is not three seconds',
                )
        return counts


INSERT_ORDER = [
    'user',
    'group_member',
    'experiment_session',
    'essay',
    'experiment_session_task',
    'question_submission',
    'question_response',
    'question_response_choice',
    'question_response_blank',
    'question_grading_attempt',
    'survey_submission',
    'survey_response',
    'survey_response_choice',
    'chat',
    'chat_message',
    'experiment_llm_request',
    'experiment_prompt_bucket',
    'experiment_prompt_reservation',
    'experiment_warning_state',
    'experiment_telemetry_event',
    'experiment_telemetry_summary',
]


def insert_batch(db, batch):
    for table in INSERT_ORDER:
        records = batch.tables.get(table, [])
        if not records:
            continue
        # Optional fields differ by row, so group inserts by their explicit columns.
        groups = defaultdict(list)
        for record in records:
            columns = tuple(sorted(record))
            groups[columns].append(tuple(record[column] for column in columns))
        for columns, values in groups.items():
            names = ','.join('"' + column + '"' for column in columns)
            placeholders = ','.join('?' for _ in columns)
            db.executemany(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', values)


def database_baseline(db):
    baseline = {}
    for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        table = row[0]
        records = rows(db, f'SELECT rowid AS _seed_rowid,* FROM "{table}" ORDER BY rowid')
        baseline[table] = (max((record['_seed_rowid'] for record in records), default=0), len(records), digest(records))
    return baseline


def verify_existing(db, baseline, batch):
    for table, (maximum, count, before_hash) in baseline.items():
        existing = rows(db, f'SELECT rowid AS _seed_rowid,* FROM "{table}" WHERE rowid<=? ORDER BY rowid', (maximum,))
        require(len(existing) == count and digest(existing) == before_hash, f'Existing records changed in {table}')
        after_count = db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        require(after_count == count + len(batch.tables.get(table, [])), f'Unexpected additions in {table}')


def verify_batch(db, batch):
    require(not list(db.execute('PRAGMA foreign_key_check')), 'Foreign-key validation failed')
    for table, records in batch.tables.items():
        for record in records:
            # Verify exact row values, including all descendants and telemetry.
            keys = (
                ('response_id', 'choice_id')
                if table.endswith('_response_choice')
                else ('response_id', 'blank_id') if table == 'question_response_blank' else ('id',)
            )
            actual = rows(
                db,
                f'SELECT * FROM "{table}" WHERE ' + ' AND '.join(f'"{key}"=?' for key in keys),
                tuple(record[key] for key in keys),
            )
            require(
                len(actual) == 1 and all(actual[0][key] == value for key, value in record.items()),
                f'Incomplete/conflicting batch row in {table}: {[record[k] for k in keys]}',
            )
    expected_users = {r['id'] for r in batch.tables['user']}
    marked = {
        r['id']
        for r in rows(db, "SELECT id FROM user WHERE json_extract(info,'$.seed_batch')=?", (batch.fixture['batch_id'],))
    }
    require(marked == expected_users, 'Unexpected participants associated with batch')
    # Exact descendant counts detect extra rows as well as missing/corrupted rows on rerun.
    scopes = {
        'group_member': ('user_id', 'user'),
        'experiment_session': ('user_id', 'user'),
        'experiment_session_task': ('experiment_session_id', 'experiment_session'),
        'essay': ('user_id', 'user'),
        'question_submission': ('user_id', 'user'),
        'question_response': ('submission_id', 'question_submission'),
        'question_response_choice': ('response_id', 'question_response'),
        'question_response_blank': ('response_id', 'question_response'),
        'question_grading_attempt': ('response_id', 'question_response'),
        'survey_submission': ('user_id', 'user'),
        'survey_response': ('submission_id', 'survey_submission'),
        'survey_response_choice': ('response_id', 'survey_response'),
        'chat': ('user_id', 'user'),
        'chat_message': ('chat_id', 'chat'),
        'experiment_llm_request': ('experiment_session_id', 'experiment_session'),
        'experiment_prompt_bucket': ('session_task_id', 'experiment_session_task'),
        'experiment_prompt_reservation': ('bucket_id', 'experiment_prompt_bucket'),
        'experiment_warning_state': ('experiment_session_id', 'experiment_session'),
        'experiment_telemetry_event': ('experiment_session_id', 'experiment_session'),
        'experiment_telemetry_summary': ('experiment_session_id', 'experiment_session'),
    }
    for table, (column, parent) in scopes.items():
        ids = [record['id'] for record in batch.tables[parent]]
        count = db.execute(
            f'SELECT count(*) FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in ids)})', ids
        ).fetchone()[0]
        require(count == len(batch.tables[table]), f'Unexpected batch descendants in {table}')
    require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'SQLite integrity check failed')


def existing_anchor(db, fixture):
    users = rows(
        db,
        "SELECT id,info FROM user WHERE json_extract(info,'$.seed_batch')=? OR email LIKE 'grade10-demo-%@example.invalid'",
        (fixture['batch_id'],),
    )
    expected = {identifier(fixture['batch_id'], n, 'user') for n in range(1, 11)}
    ids = [*expected]
    collisions = rows(db, 'SELECT id,info FROM user WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)
    if not users and not collisions:
        return None
    require(
        {u['id'] for u in users} == expected and len(users) == 10, 'Conflicting/incomplete existing seed participants'
    )
    metadata = [json.loads(user['info'] or '{}') for user in users]
    require(
        all(m.get('fixture_sha256') == digest(fixture) for m in metadata),
        'Existing batch has different fixture content',
    )
    anchors = {m.get('seed_anchor_ns') for m in metadata}
    require(len(anchors) == 1 and None not in anchors, 'Existing batch has inconsistent timestamps')
    return anchors.pop()


def run_seed(database, fixture, apply=False, backup_dir=None):
    check_fixture(fixture)
    with connect(database) as db:
        check_target(db, fixture)
        anchor = existing_anchor(db, fixture)
        exists = anchor is not None
        if not exists:
            # Use a recent class period; keep every run and simulated review in the past.
            anchor = (time.time_ns() // NS - 3 * 3600) * NS
        batch = Batch(fixture, anchor).build()
        counts = batch.validate(time.time_ns())
        if exists:
            verify_batch(db, batch)
        if not apply or exists:
            return {
                'status': 'already_present' if exists else 'preview',
                'database': str(database),
                'batch_id': fixture['batch_id'],
                'counts': counts,
                'runs': batch.report,
            }
    db = connect(database, readonly=False)
    backup_path = None
    try:
        db.execute('BEGIN IMMEDIATE')
        check_target(db, fixture)
        require(existing_anchor(db, fixture) is None, 'Batch appeared after preview; rerun for verification')
        baseline = database_baseline(db)
        backup_dir = backup_dir or database.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f'webui-before-{fixture["batch_id"]}-{time.time_ns()}.db'
        fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        # A second connection reads the committed snapshot while this writer holds
        # its RESERVED lock. No other writer can race between backup and insertion.
        with connect(database) as source, sqlite3.connect(backup_path) as destination:
            source.backup(destination)
        with connect(backup_path) as backup:
            require(database_baseline(backup) == baseline, 'Backup differs from pre-insertion snapshot')
        insert_batch(db, batch)
        verify_batch(db, batch)
        verify_existing(db, baseline, batch)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    with connect(database) as check:
        verify_batch(check, batch)
    return {
        'status': 'inserted',
        'database': str(database),
        'batch_id': fixture['batch_id'],
        'backup': str(backup_path),
        'existing_records_unchanged': True,
        'counts': counts,
        'runs': batch.report,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'backend/data/webui.db')
    parser.add_argument('--fixture', type=Path, default=FIXTURE)
    parser.add_argument('--apply', action='store_true', help='Back up and insert the complete batch')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    try:
        report = run_seed(args.database.resolve(), json.loads(args.fixture.read_text()), args.apply, args.backup_dir)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'Seed aborted: {exc}\n')


if __name__ == '__main__':
    main()
