#!/usr/bin/env python3
"""Generate a synthetic 40-participant Grade 10 cohort and export it like the dashboard.

Run with backend/venv/bin/python from the repository root. The script never writes to the
source database: it copies it (read-only source) into a temporary directory, replays each
participant against that copy through the platform's own routes and model functions with a
simulated clock, reviews the manual items as a synthetic administrator, and finally calls the
real ``POST /api/v1/analytics/experiments/export/sessions`` route (anonymized, the UI default).
The response bytes are written unchanged under the Content-Disposition filename. The
temporary database is deleted afterwards unless ``--keep-database`` is given.

Authored participant content lives in scripts/fixtures/grade10_cohort40/runs-0*.json and the
published plan snapshot in scripts/fixtures/grade10_runs.json. These are fictional examples,
not collected student data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_FIXTURE = ROOT / 'scripts/fixtures/grade10_runs.json'
RUNS_DIR = ROOT / 'scripts/fixtures/grade10_cohort40'
DEFAULT_OUTPUT = ROOT / 'research-toolbox/examples/grade10_cohort'

BATCH_ID = 'grade10-cohort40-v1'
NAMESPACE = uuid.UUID('b68ab008-2f70-4abe-8c51-94353387b21c')
# Synthetic instance secret. It drives the platform's condition assignment and the export's
# P-… participant hashes; chosen among arbitrary candidates for a plausible (unequal) split.
SECRET_KEY = 'grade10-cohort40-synthetic-key-0'
MODEL_ID = 'synthetic-grade10-demo'
ORIGIN = 'https://llmscribe.example.edu'
EXTENSION_ID = 'hjmcenbpkgoaldfinbglcmepfkadhnoj'
EXTENSION_VERSION = '2.2.0'
SCHEMA_VERSION = 4
NS = 1_000_000_000
MS = 1_000_000

# Class periods (UTC). A US Grade 10 class meeting at 09:10 / 11:05 EDT.
PERIODS = [
    (range(1, 15), datetime(2026, 9, 22, 13, 10, tzinfo=timezone.utc)),
    (range(15, 28), datetime(2026, 9, 22, 15, 5, tzinfo=timezone.utc)),
    (range(28, 41), datetime(2026, 9, 23, 13, 10, tzinfo=timezone.utc)),
]
ACCOUNTS_CREATED = datetime(2026, 9, 18, 19, 42, tzinfo=timezone.utc)
REVIEW_START = datetime(2026, 9, 24, 18, 30, tzinfo=timezone.utc)

# Generator-side scenario details that are not authored content.
CLOSE_TAB_ON_DROPOUT = {15, 27}
NETWORK_OUTAGE = {16: (2, 0.45, 230)}  # run: (task position, fraction into task, seconds offline)
UNREVIEWED_TASKS = {23: {4}, 38: {1}}  # manual items still awaiting review at export time

SCREENS = [(1366, 657), (1366, 657), (1280, 631), (1536, 729), (1440, 789), (1920, 937)]
BACKGROUND_TABS = [
    ('Stream - Grade 10 Biology', 'https://classroom.google.com/u/0/c/NzE4MjQ1MDk2MzI0'),
    ('Inbox (3) - student mail', 'https://mail.google.com/mail/u/0/#inbox'),
    ('Desmos | Graphing Calculator', 'https://www.desmos.com/calculator'),
    ('New Tab', 'chrome://newtab/'),
    ('Scientific Calculator - Desmos', 'https://www.desmos.com/scientific'),
]
CHAT_TITLES = {
    'B1': ['Identifying the Independent Variable', '🥔 Osmosis Variables'],
    'B2': ['Designing a Fair Osmosis Test', 'Fair Test Controls'],
    'B3': ['Percent Change in Mass', '🧮 Percent Mass Change'],
    'B4': ['Osmosis at Equilibrium', 'Zero Net Water Movement'],
    'B5': ['Predicting Mass Change at 0.50 mol/L', 'Osmosis Prediction Check'],
    'G1': ['Corresponding Sides in Similar Triangles'],
    'G2': ['Scale Factor Calculation', '📐 Similar Triangle Scale Factor'],
    'G3': ['Triangle Similarity Criteria'],
    'G4': ['Area Scaling With Similar Shapes', 'Area Scale Factor'],
    'G5': ['Slopes and Right Angles'],
    'G6': ['Midpoint of a Diagonal'],
    'G7': ['Rectangle vs Square Properties'],
    'G8': ['Checking a Floor Area'],
    'G9': ['Sector Area vs Arc Length'],
    'G10': ['Storage Width Constraint'],
    None: ['Library Grant Essay Help', '📚 Books vs Media Space', 'Library Plan Argument'],
    'biology': ['Osmosis Lab Questions'],
    'geometry': ['Geometry Exam Help', '📐 Geometry Practice Help'],
}
NO_MODIFIERS = {'ctrl': False, 'shift': False, 'alt': False, 'meta': False}
SHIFTED = set('~!@#$%^&*()_+{}|:"<>?')


def uid(*parts):
    return str(uuid.uuid5(NAMESPACE, ':'.join(map(str, parts))))


def ns_of(moment: datetime) -> int:
    return int(moment.timestamp()) * NS


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{ms % 1000:03d}Z'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


class Clock:
    """Simulated wall clock shared by every platform call. Each read advances a few µs."""

    def __init__(self):
        self.now = time.time_ns()
        self.rng = random.Random('clock')

    def time_ns(self):
        self.now += self.rng.randint(8_000, 180_000)
        return self.now

    def time(self):
        return self.time_ns() / NS

    def set(self, value: int, *, force=False):
        if force or value > self.now:
            self.now = int(value)


CLOCK = Clock()
UUID_RNG = random.Random(f'{BATCH_ID}:uuid4')
_REAL = {'time_ns': time.time_ns, 'time': time.time, 'uuid4': uuid.uuid4}


def patch_runtime():
    time.time_ns = CLOCK.time_ns
    time.time = CLOCK.time
    uuid.uuid4 = lambda: uuid.UUID(int=UUID_RNG.getrandbits(128), version=4)


def restore_runtime():
    time.time_ns = _REAL['time_ns']
    time.time = _REAL['time']
    uuid.uuid4 = _REAL['uuid4']


def load_content():
    fixture = json.loads(SNAPSHOT_FIXTURE.read_text())
    runs = []
    for path in sorted(RUNS_DIR.glob('runs-0*.json')):
        runs.extend(json.loads(path.read_text()))
    runs.sort(key=lambda run: run['number'])
    if [run['number'] for run in runs] != list(range(1, 41)):
        raise SystemExit('Expected runs 1–40 in scripts/fixtures/grade10_cohort40/')
    return fixture['snapshot'], runs


def validate_content(snapshot, runs):
    questions = snapshot['questions']
    task_of = {key: (1 if key.startswith('B') else 4) for key in questions}
    maxima = {'B4': [1, 2, 2, 1], 'B5': [1] * 6, 'G4': [1, 2, 1, 1, 1], 'G8': [2, 2, 1, 1], 'G10': [2, 2, 1, 1, 1, 1]}
    problems = []
    for run in runs:
        n = run['number']
        outcome = run['outcome']
        expected_tasks = {'never_started': 0, 'dropout': (run['stop_task'] or 0) + 1}.get(outcome, 6)
        if len(run['task_minutes']) != expected_tasks:
            problems.append(f'{n}: task_minutes length')
        for key, answer in run['answers'].items():
            q = questions[key]
            if q['choices'] and not set(answer.get('choices', [])) <= set(range(len(q['choices']))):
                problems.append(f'{n}: {key} invalid choice')
            if q['blanks'] and set(answer.get('blanks', {})) - {b['blank_key'] for b in q['blanks']}:
                problems.append(f'{n}: {key} invalid blank')
            if key in maxima and 'rubric_points' in answer:
                points = answer['rubric_points']
                if len(points) != len(maxima[key]) or any(not 0 <= p <= m for p, m in zip(points, maxima[key])):
                    problems.append(f'{n}: {key} rubric points')
        previous = 0
        for chat in run['chats']:
            if chat['task'] < previous:
                problems.append(f'{n}: chat order')
            previous = chat['task']
            if chat.get('question') and task_of[chat['question']] != chat['task']:
                problems.append(f'{n}: chat question task')
            if chat.get('paste_into_essay') and chat['paste_into_essay'] not in (run['essay'] or run['essay_draft'] or ''):
                problems.append(f'{n}: essay paste missing')
            if chat.get('paste_into_answer') and chat['paste_into_answer'] not in run['answers'][chat['question']].get('text', ''):
                problems.append(f'{n}: answer paste missing')
    if problems:
        raise SystemExit('Invalid cohort content:\n' + '\n'.join(problems))


class Tab:
    def __init__(self, tab_id, window_id, index, title, url, width, height, accessed_ms, active=False):
        self.tab_id, self.window_id, self.index = tab_id, window_id, index
        self.title, self.url, self.status = title, url, 'complete'
        self.width, self.height = width, height
        self.last_accessed = accessed_ms
        self.active = active
        self.opener = None

    def fav_icon(self):
        if self.url.startswith('chrome://'):
            return None
        base = self.url.split('/', 3)
        return f'{base[0]}//{base[2]}/favicon.ico' if not self.url.startswith(ORIGIN) else f'{ORIGIN}/static/favicon.ico'

    def payload(self):
        data = {
            'tab_id': self.tab_id,
            'window_id': self.window_id,
            'index': self.index,
            'group_id': -1,
            'split_view_id': -1,
            'active': self.active,
            'highlighted': self.active,
            'pinned': False,
            'incognito': False,
            'audible': False,
            'auto_discardable': True,
            'discarded': False,
            'frozen': False,
            'muted_info': {'muted': False},
            'status': self.status,
            'url': self.url,
            'title': self.title,
            'width': self.width,
            'height': self.height,
            'last_accessed': self.last_accessed,
            'truncated_fields': [],
        }
        if self.opener is not None:
            data['opener_tab_id'] = self.opener
        icon = self.fav_icon()
        if icon:
            data['fav_icon_url'] = icon
        return data


class Participant:
    def __init__(self, ctx, run, start_ns):
        self.ctx = ctx
        self.run = run
        self.n = run['number']
        self.rng = random.Random(f'{BATCH_ID}:{self.n}:behaviour')
        self.event_rng = random.Random(f'{BATCH_ID}:{self.n}:event-ids')
        self.user_id = uid(BATCH_ID, self.n, 'user')
        self.user = None
        self.t = start_ns
        self.skew_ms = self.rng.randint(-1800, 2400)
        self.session_id = None
        self.tasks = []
        self.submissions = {}
        self.extension = run['telemetry'] == 'extension'
        self.extension_until = None
        self.tracking = False
        # Telemetry transport state
        self.queue = []  # (ready_ns, event)
        self.flush_due = None
        self.offline = None
        self.browser_session_id = str(uuid.UUID(int=self.event_rng.getrandbits(128), version=4))
        self.sequence = 0
        self.last_key_context = None
        self.last_key_ns = None
        # Periodic client behaviour
        self.heartbeat_next = None
        self.activity_next = None
        self.active_since = None
        self.away = False
        self.warning = False
        # Chat state
        self.chat_id = None
        self.chat_title_set = False
        self.last_message_id = None
        self.context_chars = 0
        self.prompt_count = 0
        self.request_rows = []
        self.away_intervals = []
        self.adjustments = []
        self.accepted_events = 0
        self.rejected_events = 0
        self.last_request_ns = start_ns
        width, height = self.rng.choice(SCREENS)
        window = self.rng.randint(1_020_000_000, 2_140_000_000)
        self.window_id = window
        accessed = start_ns // MS - self.rng.randint(20_000, 400_000) + self.rng.random()
        self.app_tab = Tab(window + 1, window, 0, 'Open WebUI', f'{ORIGIN}/', width, height, round(accessed, 3), active=True)
        self.tabs = [self.app_tab]
        for index, (title, url) in enumerate(self.rng.sample(BACKGROUND_TABS, self.rng.choice([0, 1, 1, 2, 2, 3]))):
            tab = Tab(window + 2 + index, window, index + 1, title, url, width, height,
                      round(accessed - self.rng.randint(60_000, 3_000_000) + self.rng.random(), 3))
            self.tabs.append(tab)
        self.next_tab_id = window + 10

    # ------------------------------------------------------------------ transport
    async def request(self, method, path, body=None, admin=False):
        self.ctx.current_user = self.ctx.reviewer if admin else self.user
        response = await self.ctx.client.request(method, path, json=body)
        self.last_request_ns = CLOCK.now
        return response

    async def call(self, method, path, body=None, expect=200):
        await self.advance(self.t)
        response = await self.request(method, path, body)
        if expect is not None and response.status_code != expect:
            raise RuntimeError(f'Run {self.n}: {method} {path} -> {response.status_code} {response.text[:400]}')
        return response

    async def advance(self, target):
        """Run due background work (telemetry flushes, heartbeats, activity) up to target."""
        while True:
            due = [
                (self.flush_due, 'flush'),
                (self.heartbeat_next, 'heartbeat'),
                (self.activity_next, 'activity'),
            ]
            due = [(when, kind) for when, kind in due if when is not None and when <= target]
            if not due:
                break
            when, kind = min(due)
            CLOCK.set(when)
            if kind == 'flush':
                await self.flush(when)
            elif kind == 'heartbeat':
                await self.heartbeat(when)
            else:
                await self.activity(when)
        CLOCK.set(target)

    async def heartbeat(self, when):
        self.heartbeat_next = when + 20 * NS + self.rng.randint(-40, 40) * MS
        if self.extension_until is not None and when > self.extension_until:
            self.heartbeat_next = None
            return
        body = {
            'extension_version': EXTENSION_VERSION,
            'extension_id': EXTENSION_ID,
            'extension_name': 'Open WebUI Experiment Telemetry',
            'schema_version': SCHEMA_VERSION,
            'tabs_permission': True,
            'incognito_allowed': False,
            'origin': ORIGIN,
        }
        response = await self.request('POST', '/api/v1/experiments/telemetry/extension/heartbeat', body)
        if response.status_code == 409:
            self.heartbeat_next = None

    async def activity(self, when):
        self.activity_next = when + 10 * NS + self.rng.randint(0, 30) * MS
        if self.away or self.active_since is None:
            return
        elapsed = min((when - self.active_since) // MS, 30000)
        self.active_since = when
        if elapsed > 0:
            response = await self.request('POST', '/api/v1/experiments/current/runtime/activity', {'active_ms': elapsed})
            if response.status_code != 200:
                self.activity_next = None

    def client_ms(self, true_ns):
        return true_ns // MS + self.skew_ms

    def event(self, true_ns, kind, field='unknown', task=None, question=None, ready_ns=None, **fields):
        if not self.tracking:
            return
        data = {
            'event_id': str(uuid.UUID(int=self.event_rng.getrandbits(128), version=4)),
            'type': kind,
            'timestamp': iso_ms(self.client_ms(true_ns)),
            'field': field,
        }
        if task is not None:
            data['session_task_id'] = task['id']
        if question is not None:
            data['question_id'] = question['id']
            submission = self.submissions.get(task['position'])
            if submission:
                data['submission_id'] = submission
        data.update(fields)
        ready = ready_ns if ready_ns is not None else true_ns
        self.queue.append((ready, data))
        if self.flush_due is None:
            # content.js batches for 1 s, background.js flushes 1.5 s after enqueueing.
            self.flush_due = ready + 2_500 * MS + self.rng.randint(5, 180) * MS

    def tab_event(self, true_ns, kind, tab, **fields):
        self.sequence += 1
        self.event(true_ns, kind, browser_session_id=self.browser_session_id, sequence=self.sequence,
                   tab=tab.payload() if tab else None, **fields)

    def window_event(self, true_ns, focused):
        self.sequence += 1
        self.event(true_ns, 'window_focus_changed', browser_session_id=self.browser_session_id,
                   sequence=self.sequence, window_id=self.window_id if focused else -1, window_focused=focused)

    async def flush(self, when, force=False):
        self.flush_due = None
        if self.offline and self.offline[0] <= when < self.offline[1]:
            # Background retries fail while offline; the periodic 30 s alarm tries again later.
            self.flush_due = self.offline[1] + self.rng.randint(1, 30) * NS
            return
        cutoff = when if force else when - 1_000 * MS
        ready = [item for item in self.queue if item[0] <= cutoff]
        if not ready:
            if self.queue:
                self.flush_due = min(item[0] for item in self.queue) + 2_500 * MS
            return
        self.queue = [item for item in self.queue if item[0] > cutoff]
        events = [event for _, event in sorted(ready, key=lambda item: item[0])]
        for start in range(0, len(events), 100):
            batch = events[start:start + 100]
            response = await self.request('POST', '/api/v1/experiments/telemetry/events', {
                'experiment_session_id': self.session_id,
                'schema_version': SCHEMA_VERSION,
                'events': batch,
            })
            if response.status_code == 200:
                self.accepted_events += response.json()['accepted']
            elif response.status_code == 409:
                self.rejected_events += len(batch)
            else:
                raise RuntimeError(f'Run {self.n}: telemetry rejected {response.status_code} {response.text[:300]}')
            CLOCK.set(CLOCK.now + self.rng.randint(4, 30) * MS)
        if self.queue:
            self.flush_due = min(item[0] for item in self.queue) + 2_500 * MS

    async def flush_now(self):
        await self.advance(self.t)
        await self.flush(self.t + 40 * MS, force=True)

    # ------------------------------------------------------------------ keyboard
    def plan_typing(self, text, speed=1.0):
        """Return keystroke plan [(offset_ms, key, class, hold_ms, shift)] and total duration in ms."""
        rng = self.rng
        mean = self.run['typing_interval_ms'] * speed
        careless = self.run['typing_interval_ms'] < 140
        typo_rate = 0.008 + max(0, self.run['typing_interval_ms'] - 150) / 9000
        keys = []
        at = 0.0

        def interval():
            return max(38, rng.lognormvariate(math.log(mean), 0.38))

        def press(key, cls, shift=False, gap=None):
            nonlocal at
            at += gap if gap is not None else interval()
            hold = rng.randint(55, 150) if cls != 'modifier' else rng.randint(160, 420)
            keys.append((int(at), key, cls, hold, {'ctrl': False, 'shift': shift, 'alt': False, 'meta': False}))

        previous = ''
        for index, char in enumerate(text):
            if char == '\n':
                press('Enter', 'enter', gap=interval() + (rng.randint(2500, 14000) if previous == '\n' else 400))
                previous = char
                continue
            if previous in '.?!' and char == ' ' and not careless and rng.random() < 0.35:
                at += rng.randint(1800, 7500)
            elif char == ' ' and not careless and rng.random() < 0.012:
                at += rng.randint(2100, 5200)
            if char.isalpha() and rng.random() < typo_rate:
                wrong = rng.choice('asdfghjklqwertyuiopzxcvbnm')
                press(wrong, 'printable')
                press('Backspace', 'backspace', gap=rng.randint(160, 520))
            needs_shift = char.isupper() or char in SHIFTED
            if needs_shift:
                press('Shift', 'modifier', shift=True)
                press(char, 'printable', shift=True, gap=rng.randint(60, 160))
            else:
                press(char, 'whitespace' if char in ' \t' else 'printable')
            previous = char
        return keys, int(at) + 200

    def emit_typing(self, start_ns, plan, field, task, question=None):
        context = f'{field}:{task["id"]}:{question["id"] if question else ""}'
        for offset, key, cls, hold, modifiers in plan:
            at = start_ns + offset * MS
            fields = {
                'key_class': cls,
                'key_value': key,
                'modifiers': modifiers,
                'hold_duration_ms': hold,
            }
            if self.last_key_context == context and self.last_key_ns is not None:
                fields['inter_key_interval_ms'] = int((at - self.last_key_ns) // MS)
            self.last_key_context = context
            self.last_key_ns = at
            self.event(at, 'keystroke', field, task, question, ready_ns=at + hold * MS, **fields)

    def paste_keys(self, at, field, task, question=None):
        ctrl = {'ctrl': True, 'shift': False, 'alt': False, 'meta': False}
        self.emit_typing(at, [(0, 'Control', 'modifier', self.rng.randint(180, 400), ctrl),
                              (90, 'v', 'shortcut', self.rng.randint(60, 110), ctrl)], field, task, question)

    def reset_key_context(self):
        self.last_key_context = None
        self.last_key_ns = None

    # ------------------------------------------------------------------ platform helpers
    async def db_session_row(self):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.experiments import ExperimentSession
        async with get_async_db_context() as db:
            return await db.get(ExperimentSession, self.session_id)

    async def db_task_rows(self):
        from sqlalchemy import select
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.experiment_plans import ExperimentSessionTask
        async with get_async_db_context() as db:
            rows = (await db.execute(
                select(ExperimentSessionTask)
                .where(ExperimentSessionTask.experiment_session_id == self.session_id)
                .order_by(ExperimentSessionTask.position)
            )).scalars().all()
            return list(rows)

    def question_form(self, keys):
        answers = []
        for key in keys:
            q = self.ctx.questions[key]
            answer = self.run['answers'][key]
            if q['choices']:
                answers.append({'question_id': q['id'], 'choice_ids': [q['choices'][i]['id'] for i in answer.get('choices', [])]})
            elif q['blanks']:
                values = answer.get('blanks', {})
                answers.append({'question_id': q['id'], 'blank_answers': [
                    {'blank_id': b['id'], 'value': values.get(b['blank_key'], '')} for b in q['blanks']]})
            else:
                answers.append({'question_id': q['id'], 'text': answer.get('text', '')})
        return {'answers': answers}

    def survey_form(self, position):
        answers = []
        for q, value in zip(self.ctx.surveys[str(position)], self.run['surveys'][str(position)]):
            if value is None:
                continue
            if isinstance(value, list):
                answers.append({'question_id': q['id'], 'choice_ids': [q['choices'][i]['id'] for i in value]})
            elif isinstance(value, int):
                answers.append({'question_id': q['id'], 'scale': value})
            else:
                answers.append({'question_id': q['id'], 'text': value})
        return {'answers': answers}

    # ------------------------------------------------------------------ focus / tabs
    async def go_away(self, seconds, title, url):
        """Switch to another browser tab for a while, then come back."""
        start = self.t
        self.reset_key_context()
        self.event(start, 'window_blur')
        self.event(start, 'focus_away')
        self.event(start + self.rng.randint(15, 45) * MS, 'visibility_change', visibility='hidden')
        await self.flush(start + 60 * MS, force=True)
        self.away = True
        target = next((tab for tab in self.tabs if tab is not self.app_tab and (tab.url == url or tab.title == title)), None)
        at = start + self.rng.randint(20, 90) * MS
        self.app_tab.active = False
        if target is None:
            target = Tab(self.next_tab_id, self.window_id, len(self.tabs), 'New Tab', 'chrome://newtab/',
                         self.app_tab.width, self.app_tab.height, round(at / MS + self.rng.random(), 3), active=True)
            self.next_tab_id += 1
            self.tabs.append(target)
            self.tab_event(at, 'tab_created', target)
            self.tab_event(at + 4 * MS, 'tab_activated', target)
            self.tab_event(at + 6 * MS, 'tab_highlighted', target)
            nav = at + self.rng.randint(2500, 7000) * MS
            target.url, target.status, target.title = url, 'loading', url.split('/')[2] if '//' in url else url
            self.tab_event(nav, 'tab_updated', target, changed_fields=['status', 'url'])
            target.title = title
            self.tab_event(nav + self.rng.randint(150, 700) * MS, 'tab_updated', target, changed_fields=['title'])
            target.status = 'complete'
            self.tab_event(nav + self.rng.randint(700, 1600) * MS, 'tab_updated', target, changed_fields=['status'])
            if target.fav_icon():
                self.tab_event(nav + self.rng.randint(1600, 2200) * MS, 'tab_updated', target, changed_fields=['favIconUrl'])
        else:
            target.active = True
            target.last_accessed = round(at / MS + self.rng.random(), 3)
            self.tab_event(at, 'tab_activated', target)
            self.tab_event(at + 3 * MS, 'tab_highlighted', target)
        back = start + seconds * NS + self.rng.randint(0, 900) * MS
        await self.advance(back)
        target.active = False
        self.app_tab.active = True
        self.app_tab.last_accessed = round(back / MS + self.rng.random(), 3)
        self.tab_event(back, 'tab_activated', self.app_tab)
        self.tab_event(back + 3 * MS, 'tab_highlighted', self.app_tab)
        self.event(back + self.rng.randint(8, 30) * MS, 'visibility_change', visibility='visible')
        self.event(back + self.rng.randint(31, 60) * MS, 'window_focus')
        self.event(back + self.rng.randint(61, 90) * MS, 'focus_return', away_duration_ms=int((back - start) // MS))
        self.away_intervals.append((start, back))
        self.away = False
        self.active_since = back
        self.t = back + self.rng.randint(300, 1500) * MS

    async def blip(self):
        """A notification or second app briefly takes focus from the browser window."""
        start = self.t
        away = self.rng.randint(400, 6000)
        self.reset_key_context()
        self.event(start, 'window_blur')
        self.event(start, 'focus_away')
        self.window_event(start - self.rng.randint(0, 1400) * MS, False)
        back = start + away * MS
        self.window_event(back, True)
        self.event(back + self.rng.randint(1, 12) * MS, 'window_focus')
        self.event(back + self.rng.randint(13, 20) * MS, 'focus_return', away_duration_ms=away)
        await self.flush(start + 50 * MS, force=True)
        self.away_intervals.append((start, back))
        self.t = back + self.rng.randint(200, 900) * MS

    # ------------------------------------------------------------------ chat
    def chat_duration_ms(self, chat, retry=False):
        typing = 0 if retry else self.plan_typing(chat['user'])[1] + 300
        latency = 2600 + (3000 if self.run['condition'] == 'Delay' else 0)
        reading = int(len(chat.get('assistant') or '') / self.rng.uniform(18, 32) * 1000)
        return typing + latency + reading + 1500

    async def chat(self, chat, task, question_key=None, retry_of=None):
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.chats import ChatForm, Chats
        from open_webui.models.experiment_plans import ExperimentSessionTask
        from open_webui.utils.experiment_perturbations import prepare_request, update_request
        from open_webui.utils.experiment_prompt_budgets import reserve, settle
        from open_webui.utils.response import normalize_usage
        from open_webui.utils.middleware import serialize_output

        question = self.ctx.questions.get(question_key) if question_key else None
        if retry_of is None:
            plan, duration = self.plan_typing(chat['user'])
            self.t += self.rng.randint(600, 2500) * MS  # click into the chat input
            self.emit_typing(self.t, plan, 'chat', task)
            self.t += duration * MS
            enter = self.t
            self.emit_typing(enter, [(0, 'Enter', 'enter', self.rng.randint(60, 120), NO_MODIFIERS)], 'chat', task)
            self.t = enter + self.rng.randint(15, 60) * MS
        else:
            self.t += self.rng.randint(2000, 6000) * MS  # read the error, press "Regenerate"
        await self.advance(self.t)
        send = CLOCK.now
        user_message_id = retry_of['user_message_id'] if retry_of else str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        async with get_async_db_context() as db:
            task_row = await db.get(ExperimentSessionTask, task['id'])
        session_row = await self.db_session_row()
        self.ctx.current_user = self.user
        reservation = await reserve(task_row, question['id'] if question else None)
        new_chat = self.chat_id is None
        if new_chat:
            self.chat_id = str(uuid.uuid4())
        context = await prepare_request(session_row, self.ctx.plan, task_row, {
            'user_message_id': user_message_id,
            'chat_id': self.chat_id,
            'message_id': assistant_message_id,
        })
        self.prompt_count += 0 if retry_of else 1
        timestamp = int(time.time())
        user_message = {
            'id': user_message_id,
            'parentId': self.last_message_id if not retry_of else retry_of['parent_id'],
            'childrenIds': [assistant_message_id],
            'role': 'user',
            'content': chat['user'],
            'timestamp': timestamp,
            'models': [MODEL_ID],
            'experiment_session_task_id': task['id'],
        }
        placeholder = {
            'id': assistant_message_id,
            'parentId': user_message_id,
            'childrenIds': [],
            'role': 'assistant',
            'content': '',
            'done': False,
            'model': MODEL_ID,
            'timestamp': int(time.time()),
        }
        if new_chat:
            await Chats.insert_new_chat(self.chat_id, self.user.id, ChatForm(
                chat={
                    'id': self.chat_id,
                    'title': 'New Chat',
                    'models': [MODEL_ID],
                    'history': {'currentId': assistant_message_id,
                                'messages': {user_message_id: user_message, assistant_message_id: placeholder}},
                    'messages': [{'role': 'user', 'content': chat['user']}],
                    'files': [],
                    'tags': [],
                    'timestamp': int(time.time() * 1000),
                    'params': {},
                },
                experiment_session_id=self.session_id,
                experiment_session_task_id=None,
                initial_message_experiment_session_task_id=task['id'],
            ))
        else:
            if not retry_of:
                await Chats.upsert_message_to_chat_by_id_and_message_id(self.chat_id, user_message_id, user_message)
                grandparent = await Chats.get_message_by_id_and_message_id(self.chat_id, user_message['parentId'])
                if grandparent is not None:
                    children = grandparent.get('childrenIds', [])
                    if user_message_id not in children:
                        await Chats.upsert_message_to_chat_by_id_and_message_id(
                            self.chat_id, user_message['parentId'], {'childrenIds': children + [user_message_id]})
            existing = await Chats.get_message_by_id_and_message_id(self.chat_id, user_message_id)
            children = (existing or {}).get('childrenIds', [])
            if assistant_message_id not in children:
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    self.chat_id, user_message_id, {'childrenIds': children + [assistant_message_id]})
            await Chats.upsert_message_to_chat_by_id_and_message_id(
                self.chat_id, assistant_message_id, {**placeholder, 'timestamp': int(time.time())})
        request_id = context.request_id
        await Chats.upsert_message_to_chat_by_id_and_message_id(
            self.chat_id, assistant_message_id, {'experiment_request_id': request_id})
        delayed = self.run['condition'] == 'Delay'
        text = chat.get('assistant') or ''
        status = chat.get('status')
        provider_start = send + self.rng.randint(45, 280) * MS
        CLOCK.set(provider_start)
        await update_request(request_id, provider_started_at=time.time_ns())
        if status == 'FAILED':
            CLOCK.set(provider_start + self.rng.randint(400, 2600) * MS)
            await update_request(request_id, provider_completed_at=time.time_ns(), status='FAILED',
                                 error_type='ProviderStreamError', streaming_completed_normally=False)
            await Chats.upsert_message_to_chat_by_id_and_message_id(self.chat_id, assistant_message_id, {
                'error': {'content': 'The model provider returned an error (503 Service Unavailable). Please try again.'}})
            await settle(reservation, False)
            self.t = CLOCK.now + self.rng.randint(300, 900) * MS
            self.request_rows.append(request_id)
            self.last_message_id = assistant_message_id
            return {'user_message_id': user_message_id, 'parent_id': user_message['parentId']}
        first_token = provider_start + self.rng.randint(260, 950) * MS
        stream_ms = int(len(text) / self.rng.uniform(170, 420) * 1000) + 40
        provider_done = first_token + stream_ms * MS
        cancel_at = None
        if status == 'CANCELLED':
            cancel_at = first_token + int(stream_ms * self.rng.uniform(0.3, 0.6)) * MS + (3 * NS if delayed else 0)
        CLOCK.set(first_token)
        if delayed:
            await update_request(request_id, provider_first_token_at=time.time_ns(), artificial_delay_started_at=time.time_ns())
        else:
            await update_request(request_id, provider_first_token_at=time.time_ns(), artificial_delay_started_at=None)
            CLOCK.set(first_token + self.rng.randint(1, 4) * MS)
            await update_request(request_id, server_first_emit_at=time.time_ns())
        first_visible_server = None
        if delayed:
            delay_end = max(first_token + 3 * NS, provider_done + self.rng.randint(1, 8) * MS)
            if cancel_at is None or cancel_at > delay_end:
                CLOCK.set(provider_done)
                await update_request(request_id, provider_completed_at=time.time_ns())
                await update_request(request_id, buffered=True, buffered_output={'content': text}, reveal_cursor=0)
                CLOCK.set(delay_end)
                await update_request(request_id, artificial_delay_ended_at=time.time_ns())
                await update_request(request_id, server_first_emit_at=time.time_ns())
                first_visible_server = CLOCK.now
                await update_request(request_id, reveal_cursor=len(text))
        else:
            first_visible_server = first_token
        partial = text
        if cancel_at is not None:
            # The participant reloads the page mid-stream: pagehide reports NAVIGATED_AWAY, the stream is cancelled.
            partial = text[: max(1, int(len(text) * self.rng.uniform(0.35, 0.65)))]
            if first_visible_server is not None:
                shown = first_visible_server + self.rng.randint(25, 90) * MS
                await self.visible(shown, request_id, 'FIRST_VISIBLE')
            CLOCK.set(cancel_at)
            await self.visible(cancel_at, request_id, 'NAVIGATED_AWAY')
            CLOCK.set(cancel_at + self.rng.randint(20, 90) * MS)
            await update_request(request_id, status='CANCELLED', streaming_completed_normally=False)
            await Chats.upsert_message_to_chat_by_id_and_message_id(self.chat_id, assistant_message_id, {'content': partial})
            await settle(reservation, False)
            await self.reload_page(cancel_at)
        else:
            if not delayed:
                CLOCK.set(provider_done)
                await update_request(request_id, provider_completed_at=time.time_ns())
            completed_emit = CLOCK.now + self.rng.randint(1, 6) * MS
            CLOCK.set(completed_emit)
            await update_request(request_id, server_completed_emit_at=time.time_ns(), streaming_completed_normally=True,
                                 status='COMPLETED', buffered_output=None)
            system_chars = 1350 if self.run['condition'] == 'Tutor' else 0
            prompt_chars = self.context_chars + len(chat['user']) + system_chars
            usage = normalize_usage({
                'prompt_tokens': math.ceil(prompt_chars / 4) + 7,
                'completion_tokens': math.ceil(len(text) / 4),
                'total_tokens': math.ceil(prompt_chars / 4) + 7 + math.ceil(len(text) / 4),
            })
            output = [{
                'type': 'message',
                'id': f'msg_{uuid.uuid4().hex[:24]}',
                'status': 'completed',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': text}],
            }]
            await Chats.upsert_message_to_chat_by_id_and_message_id(self.chat_id, assistant_message_id, {
                'done': True, 'content': serialize_output(output), 'output': output, 'usage': usage})
            await settle(reservation, True)
            if new_chat or not self.chat_title_set:
                # The /c/<id> navigation happens once the first response starts to render.
                self.app_tab.url, self.app_tab.status = f'{ORIGIN}/c/{self.chat_id}', 'loading'
                self.tab_event(first_visible_server + 30 * MS, 'tab_updated', self.app_tab, changed_fields=['status', 'url'])
                self.app_tab.status = 'complete'
                self.tab_event(first_visible_server + 33 * MS, 'tab_updated', self.app_tab, changed_fields=['status'])
                self.tab_event(first_visible_server + 38 * MS, 'tab_updated', self.app_tab, changed_fields=['favIconUrl'])
            shown = first_visible_server + self.rng.randint(25, 90) * MS
            done_shown = completed_emit + self.rng.randint(30, 140) * MS
            await self.visible(shown, request_id, 'FIRST_VISIBLE')
            await self.visible(done_shown, request_id, 'COMPLETED_VISIBLE')
            if not self.chat_title_set:
                CLOCK.set(done_shown + self.rng.randint(900, 2600) * MS)
                title_key = question_key if task['position'] != 2 else None
                if title_key is None and task['position'] != 2:
                    title_key = 'biology' if task['position'] == 1 else 'geometry'
                title = self.rng.choice(CHAT_TITLES[title_key])
                await Chats.update_chat_title_by_id(self.chat_id, title)
                self.app_tab.title = f'{title} • Open WebUI'
                self.tab_event(CLOCK.now + 40 * MS, 'tab_updated', self.app_tab, changed_fields=['title'])
                self.chat_title_set = True
            self.context_chars += len(chat['user']) + len(text)
            self.t = done_shown
        self.last_message_id = assistant_message_id
        self.request_rows.append(request_id)
        reading = int(len(text) / self.rng.uniform(18, 32) * 1000)
        self.t = max(self.t, CLOCK.now) + reading * MS
        return None

    async def visible(self, when, request_id, event):
        CLOCK.set(max(CLOCK.now, when + self.rng.randint(15, 70) * MS))
        response = await self.request('POST', '/api/v1/experiments/current/runtime/visible-timing', {
            'request_id': request_id, 'event': event, 'timestamp': iso_ms(self.client_ms(when))})
        if response.status_code != 200:
            raise RuntimeError(f'Run {self.n}: visible timing {response.status_code} {response.text[:200]}')

    async def reload_page(self, when):
        self.reset_key_context()
        self.app_tab.status = 'loading'
        self.tab_event(when + 120 * MS, 'tab_updated', self.app_tab, changed_fields=['status'])
        self.app_tab.status = 'complete'
        self.tab_event(when + 1400 * MS, 'tab_updated', self.app_tab, changed_fields=['status'])
        self.tab_event(when + 1460 * MS, 'tab_updated', self.app_tab, changed_fields=['favIconUrl'])
        self.t = when + self.rng.randint(2500, 5000) * MS
        await self.advance(self.t)
        await self.call('GET', '/api/v1/experiments/current')

    # ------------------------------------------------------------------ task runners
    def spread(self, ops, available_ms, lead_ms):
        """Spread think/reading time between ops; stretch if needed. Returns per-op gaps."""
        needed = sum(op['min_ms'] for op in ops) + lead_ms
        slack = available_ms - needed
        if slack < 0:
            self.adjustments.append((ops[0]['task'] if ops else None, round(available_ms / 60000, 2), round((needed * 1.08) / 60000, 2)))
            slack = int(needed * 0.08)
        weights = [op.get('weight', 1.0) * self.rng.uniform(0.6, 1.4) for op in ops] or [1.0]
        total = sum(weights) + 0.5
        return [int(slack * w / total) for w in weights]

    async def open_task(self, task):
        self.t += self.rng.randint(400, 1600) * MS
        response = await self.call('GET', f'/api/v1/experiments/current/tasks/{task["id"]}')
        submission = response.json().get('submission') or {}
        if task['task_type'] == 'QUESTION' and submission.get('id'):
            self.submissions[task['position']] = submission['id']

    async def survey_task(self, task, minutes):
        started = self.t
        await self.open_task(task)
        end = started + int(minutes * 60 * NS)
        if self.rng.random() < 0.15:
            self.t = started + (end - started) // 2
            await self.advance(self.t)
            await self.blip()
        self.t = max(end, self.t + 5 * NS)
        await self.call('POST', f'/api/v1/experiments/current/tasks/{task["id"]}/survey', self.survey_form(task['position']))

    def away_ops(self, position):
        ops = []
        for away in self.run['focus_away']:
            if away['task'] == position:
                ops.append({'kind': 'away', 'min_ms': away['seconds'] * 1000 + 800, 'weight': 0.3, 'data': away, 'task': position})
        return ops

    def insert_randomly(self, ops, extra):
        for op in extra:
            index = self.rng.randint(1 if len(ops) > 1 else 0, max(0, len(ops) - 1))
            ops.insert(index, op)
        return ops

    async def question_task(self, task, minutes, stop=False):
        position = task['position']
        started = self.t
        await self.open_task(task)
        keys = [key for key, q in self.ctx.questions.items() if q['task_id'] == task['question_task_id']]
        keys.sort(key=lambda key: self.ctx.questions[key]['position'])
        answered_keys = [key for key in keys if key in self.run['answers']]
        chats = [chat for chat in self.run['chats'] if chat['task'] == position]
        ops = []
        done = set()

        def answer_op(key):
            q = self.ctx.questions[key]
            answer = self.run['answers'][key]
            reading = min(90_000, int(len(q['description'] or '') / self.rng.uniform(14, 26) * 1000))
            op = {'kind': 'answer', 'key': key, 'task': position, 'weight': 1.0, 'reading': reading}
            if q['choices']:
                op['min_ms'] = reading + 1500 * max(1, len(answer.get('choices', [])))
                op['weight'] = 1.0
            elif q['blanks']:
                texts = [answer.get('blanks', {}).get(b['blank_key'], '') for b in q['blanks']]
                op['plans'] = [self.plan_typing(text) for text in texts]
                op['min_ms'] = reading + sum(plan[1] + 1200 for plan in op['plans'])
                op['weight'] = 1.6
            else:
                text = answer.get('text', '')
                pastes = [c['paste_into_answer'] for c in chats if c.get('question') == key and c.get('paste_into_answer')]
                segments = split_segments(text, pastes)
                op['segments'] = [(kind, value, self.plan_typing(value) if kind == 'type' and value else None) for kind, value in segments]
                op['min_ms'] = reading + sum((plan[1] if plan else 900) for _, _, plan in op['segments'])
                op['weight'] = 3.0
            return op

        for index, chat in enumerate(chats):
            target = chat.get('question')
            later = {c.get('question') for c in chats[index:]}
            if target:
                # Answer earlier questions first, but skip ones the student will still ask about.
                for key in answered_keys:
                    if key == target:
                        break
                    if key not in done and key not in later:
                        ops.append(answer_op(key))
                        done.add(key)
            ops.append({'kind': 'chat', 'chat': chat, 'task': position, 'min_ms': self.chat_duration_ms(chat), 'weight': 0.4})
            upcoming = chats[index + 1].get('question') if index + 1 < len(chats) else None
            if target and target not in done and target in self.run['answers'] and upcoming != target:
                ops.append(answer_op(target))
                done.add(target)
        for key in answered_keys:
            if key not in done:
                ops.append(answer_op(key))
                done.add(key)
        self.insert_randomly(ops, self.away_ops(position))
        if self.rng.random() < 0.35:
            self.insert_randomly(ops, [{'kind': 'blip', 'min_ms': 3000, 'weight': 0.1, 'task': position}])
        available = int(minutes * 60_000) - (self.t - started) // MS
        gaps = self.spread(ops, available - 4000, 3000)
        for op, gap in zip(ops, gaps):
            self.t += gap * MS
            await self.run_op(op, task)
        end = started + int(minutes * 60 * NS)
        self.t = max(self.t + 2 * NS, end)
        await self.advance(self.t)
        if stop:
            return
        await self.flush_now()
        self.t += self.rng.randint(150, 600) * MS
        await self.call('POST', f'/api/v1/experiments/current/tasks/{task["id"]}/finalize/questions', self.question_form(keys))

    async def essay_task(self, task, minutes, stop=False):
        position = task['position']
        started = self.t
        await self.open_task(task)
        text = self.run['essay'] if not stop else (self.run['essay_draft'] or '')
        chats = [chat for chat in self.run['chats'] if chat['task'] == position]
        pastes = [chat['paste_into_essay'] for chat in chats if chat.get('paste_into_essay')]
        pieces = []
        for kind, value in split_segments(text, pastes):
            if kind == 'paste':
                pieces.append(('paste', value))
            else:
                parts = value.split('\n\n')
                for index, part in enumerate(parts):
                    chunk = part + ('\n\n' if index < len(parts) - 1 else '')
                    if chunk:
                        pieces.append(('type', chunk))
        # Chat placement: before the piece that consumes a paste, otherwise spread over the draft.
        slots = []
        for index, chat in enumerate(chats):
            if chat.get('paste_into_essay'):
                slot = next(i for i, piece in enumerate(pieces) if piece == ('paste', chat['paste_into_essay']))
            else:
                slot = round(index * len(pieces) / (len(chats) + 1)) if index else 0
            slots.append(slot)
        for i in range(1, len(slots)):
            slots[i] = max(slots[i], slots[i - 1])
        ops = []
        for index, piece in enumerate(pieces + [None]):
            for chat, slot in zip(chats, slots):
                if slot == index:
                    ops.append({'kind': 'chat', 'chat': chat, 'task': position, 'min_ms': self.chat_duration_ms(chat), 'weight': 0.5})
            if piece is None:
                break
            if piece[0] == 'type':
                plan = self.plan_typing(piece[1])
                ops.append({'kind': 'essay_type', 'text': piece[1], 'plan': plan, 'task': position, 'min_ms': plan[1], 'weight': 1.2})
            else:
                ops.append({'kind': 'essay_paste', 'text': piece[1], 'task': position, 'min_ms': 1500, 'weight': 0.3})
        extra = self.away_ops(position)
        for copy in self.run.get('copies') or []:
            if copy['task'] == 2 and copy['text'] in text:
                extra.append({'kind': 'copy', 'text': copy['text'], 'field': 'essay', 'task': position, 'min_ms': 1200, 'weight': 0.1})
        # Copies and absences happen after some drafting, never before the first piece.
        for op in extra:
            ops.insert(self.rng.randint(1, max(1, len(ops) - 1)) if len(ops) > 1 else len(ops), op)
        available = int(minutes * 60_000) - (self.t - started) // MS
        gaps = self.spread(ops, available - 3000, 20_000)
        self.essay_so_far = ''
        self.t += self.rng.randint(8, 40) * NS  # reading the packet before typing
        outage = NETWORK_OUTAGE.get(self.n)
        for op, gap in zip(ops, gaps):
            self.t += gap * MS
            if outage and outage[0] == position and self.offline is None and self.t >= started + outage[1] * minutes * 60 * NS:
                self.offline = (self.t, self.t + outage[2] * NS)
            await self.run_op(op, task)
        end = started + int(minutes * 60 * NS)
        if stop:
            self.t = max(self.t + NS, end)
            await self.advance(self.t)
            return
        self.t = max(self.t + 3 * NS, end)
        await self.advance(self.t)
        await self.call('PATCH', f'/api/v1/experiments/current/tasks/{task["id"]}/essay-draft', {'content': text})
        await self.flush_now()
        self.t += self.rng.randint(200, 900) * MS
        await self.call('POST', f'/api/v1/experiments/current/tasks/{task["id"]}/finalize/essay', {'content': text})

    async def save_question_draft(self, task, keys):
        self.t += 700 * MS + self.rng.randint(0, 120) * MS
        await self.call('PUT', f'/api/v1/experiments/current/tasks/{task["id"]}/question-draft', self.question_form(keys))

    async def run_op(self, op, task):
        kind = op['kind']
        if kind == 'away':
            await self.advance(self.t)
            await self.go_away(op['data']['seconds'], op['data'].get('tab_title') or 'Google', op['data'].get('url') or 'https://www.google.com/')
        elif kind == 'blip':
            await self.advance(self.t)
            await self.blip()
        elif kind == 'chat':
            chat = op['chat']
            chats = self.run['chats']
            index = chats.index(chat)
            retry = None
            if index and chats[index - 1].get('status') == 'FAILED' and chats[index - 1]['user'] == chat['user']:
                retry = getattr(self, 'last_failed', None)
            result = await self.chat(chat, task, chat.get('question'), retry_of=retry)
            self.last_failed = result
        elif kind == 'answer':
            key = op['key']
            q = self.ctx.questions[key]
            answer = self.run['answers'][key]
            self.t += op['reading'] * MS
            if q['choices']:
                control = 'single_choice' if q['question_type'] == 'SINGLE_CHOICE' else 'multiple_select'
                choices = list(answer.get('choices', []))
                if control == 'single_choice' and self.rng.random() < 0.18:
                    self.event(self.t, 'answer_change', 'question', task, q, control_type=control, answered=True)
                    self.t += self.rng.randint(3000, 15000) * MS
                for _ in choices:
                    self.t += self.rng.randint(600, 4200) * MS
                    self.event(self.t, 'answer_change', 'question', task, q, control_type=control, answered=True)
                if control == 'multiple_select' and self.rng.random() < 0.2:
                    self.t += self.rng.randint(1500, 6000) * MS
                    self.event(self.t, 'answer_change', 'question', task, q, control_type=control, answered=True)
                    self.t += self.rng.randint(800, 3000) * MS
                    self.event(self.t, 'answer_change', 'question', task, q, control_type=control, answered=True)
            elif q['blanks']:
                for plan, duration in op['plans']:
                    self.t += self.rng.randint(500, 1500) * MS
                    if plan:
                        self.emit_typing(self.t, plan, 'question', task, q)
                        self.t += duration * MS
                        self.event(self.t + self.rng.randint(100, 900) * MS, 'answer_change', 'question', task, q,
                                   control_type='fill_blank', answered=True)
            else:
                for segment_kind, value, plan in op['segments']:
                    if segment_kind == 'paste':
                        self.t += self.rng.randint(500, 1500) * MS
                        self.paste_keys(self.t - 95 * MS, 'question', task, q)
                        self.event(self.t, 'paste', 'question', task, q, text_length=len(value),
                                   line_count=len(value.splitlines()) or 1, text_value=value[:20000])
                        self.t += self.rng.randint(400, 1200) * MS
                    elif plan:
                        self.emit_typing(self.t, plan[0], 'question', task, q)
                        self.t += plan[1] * MS
                if answer.get('text'):
                    self.event(self.t + self.rng.randint(200, 2500) * MS, 'answer_change', 'question', task, q,
                               control_type='free_text', answered=True)
            await self.advance(self.t)
            self.answered.append(key)
            await self.save_question_draft(task, [k for k in self.answered_order(task) if k in self.answered])
        elif kind == 'essay_type':
            self.emit_typing(self.t, op['plan'][0], 'essay', task)
            self.t += op['plan'][1] * MS
            self.essay_so_far += op['text']
            await self.advance(self.t)
            self.t += 700 * MS
            await self.call('PATCH', f'/api/v1/experiments/current/tasks/{task["id"]}/essay-draft', {'content': self.essay_so_far})
        elif kind == 'essay_paste':
            value = op['text']
            self.paste_keys(self.t, 'essay', task)
            self.event(self.t + 95 * MS, 'paste', 'essay', task, text_length=len(value),
                       line_count=len(value.splitlines()) or 1, text_value=value[:20000])
            self.essay_so_far += value
            self.t += self.rng.randint(900, 2500) * MS
            await self.advance(self.t)
            self.t += 700 * MS
            await self.call('PATCH', f'/api/v1/experiments/current/tasks/{task["id"]}/essay-draft', {'content': self.essay_so_far})
        elif kind == 'copy':
            self.event(self.t, 'copy', op['field'], task, text_length=len(op['text']),
                       line_count=len(op['text'].splitlines()) or 1, text_value=op['text'])
            self.t += op['min_ms'] * MS

    def answered_order(self, task):
        keys = [key for key, q in self.ctx.questions.items() if q['task_id'] == task['question_task_id']]
        return sorted(keys, key=lambda key: self.ctx.questions[key]['position'])

    # ------------------------------------------------------------------ session
    async def simulate(self):
        from open_webui.models.users import Users
        self.user = await Users.get_user_by_id(self.user_id)
        run = self.run
        CLOCK.set(self.t, force=True)
        self.answered = []
        await self.call('GET', '/api/v1/experiments/current')
        session = await self.db_session_row_by_user()
        self.session_id = session.id
        # Consent screen reading time.
        self.t += self.rng.randint(20, 90) * NS + self.rng.randint(0, 999) * MS
        await self.call('POST', '/api/v1/experiments/current/consent')
        self.heartbeat_next = self.t + self.rng.randint(250, 700) * MS
        if run['outcome'] == 'never_started':
            # Reads the task overview, then closes the laptop before starting.
            self.t += self.rng.randint(40, 110) * NS
            await self.advance(self.t)
            self.heartbeat_next = None
            return
        self.t += self.rng.randint(5, 40) * NS + self.rng.randint(0, 999) * MS
        await self.advance(self.t - 300 * MS)
        await self.heartbeat(self.t - 300 * MS)
        await self.call('POST', '/api/v1/experiments/current/start')
        self.tracking = self.extension
        if not self.extension:
            # The extension was paused right after the start check; no telemetry reaches the server.
            self.extension_until = self.t + 40 * NS
        else:
            snap = self.t + self.rng.randint(350, 900) * MS
            for tab in self.tabs:
                self.tab_event(snap, 'tab_snapshot', tab, snapshot_phase='initial')
        self.tasks = [
            {'id': row.id, 'position': row.position, 'task_type': row.task_type, 'question_task_id': row.question_task_id}
            for row in await self.db_task_rows()
        ]
        self.active_since = self.t
        if run['condition'] == 'Reminder':
            self.t += self.rng.randint(200, 600) * MS
            runtime = (await self.call('GET', '/api/v1/experiments/current/runtime')).json()
            token = runtime['warning']['token']
            self.t += self.rng.randint(80, 300) * MS
            await self.call('POST', '/api/v1/experiments/current/runtime/warning/display', {'token': token})
            self.t += self.rng.randint(3, 22) * NS + self.rng.randint(0, 999) * MS
            await self.call('POST', '/api/v1/experiments/current/runtime/warning/acknowledge', {'token': token})
            self.activity_next = self.active_since + 10 * NS
        stop = run['stop_task'] if run['outcome'] == 'dropout' else None
        for task, minutes in zip(self.tasks, run['task_minutes']):
            leaving = stop is not None and task['position'] == stop
            if task['task_type'] == 'SURVEY':
                await self.survey_task(task, minutes)
            elif task['task_type'] == 'QUESTION':
                self.answered = []
                await self.question_task(task, minutes, stop=leaving)
            else:
                await self.essay_task(task, minutes, stop=leaving)
            if leaving:
                await self.leave()
                return
        # After the post survey the state is THANK_YOU_REQUIRED; telemetry and heartbeats stop.
        self.heartbeat_next = None
        self.activity_next = None
        self.flush_due = None
        if self.queue:
            await self.flush(self.t + 1500 * MS, force=True)
        if run['outcome'] == 'completed':
            self.t += self.rng.randint(5, 30) * NS
            await self.call('POST', '/api/v1/experiments/current/complete')
        else:
            self.t += self.rng.randint(5, 20) * NS
            await self.advance(self.t)

    async def db_session_row_by_user(self):
        from sqlalchemy import select
        from open_webui.internal.db import get_async_db_context
        from open_webui.models.experiments import ExperimentSession
        async with get_async_db_context() as db:
            return (await db.execute(select(ExperimentSession).where(ExperimentSession.user_id == self.user_id))).scalars().first()

    async def leave(self):
        """Abandon the session mid-task."""
        if self.n in CLOSE_TAB_ON_DROPOUT and self.tracking:
            await self.flush(self.t, force=True)
            self.tab_event(self.t, 'tab_removed', self.app_tab, is_window_closing=len(self.tabs) == 1)
            remaining = [tab for tab in self.tabs if tab is not self.app_tab]
            if remaining:
                remaining[-1].active = True
            for tab in remaining:
                self.tab_event(self.t + 20 * MS, 'tab_snapshot', tab, snapshot_phase='final')
            await self.flush(self.t + 900 * MS, force=True)
            self.t += NS
        else:
            await self.advance(self.t)
            # The laptop lid is closed: queued events never leave the browser.
        self.heartbeat_next = self.activity_next = self.flush_due = None
        self.queue = []


def split_segments(text, pastes):
    """Split text into [('type'|'paste', value)] preserving order, using exact paste substrings."""
    segments = []
    rest = text
    for paste in pastes:
        index = rest.find(paste)
        if index < 0:
            continue
        if index:
            segments.append(('type', rest[:index]))
        segments.append(('paste', paste))
        rest = rest[index + len(paste):]
    if rest:
        segments.append(('type', rest))
    return segments


class Context:
    def __init__(self):
        self.current_user = None
        self.reviewer = None
        self.client = None
        self.plan = None
        self.questions = {}
        self.surveys = {}


async def create_accounts(runs, group_id):
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.groups import GroupMember
    from open_webui.models.users import User
    rng = random.Random(f'{BATCH_ID}:accounts')
    created = int(ACCOUNTS_CREATED.timestamp())
    reviewer_id = uid(BATCH_ID, 'reviewer')
    async with get_async_db_context() as db:
        db.add(User(
            id=reviewer_id, email='research-reviewer@example.invalid', role='admin', name='Research Reviewer',
            profile_image_url='/user.png', created_at=created - 86400, updated_at=created - 86400,
            last_active_at=int(REVIEW_START.timestamp()), info={'synthetic': True, 'seed_batch': BATCH_ID},
            settings={}, bio='Synthetic reviewer account for the Grade 10 cohort example.'))
        for index, run in enumerate(runs):
            at = created + index * rng.randint(1, 3)
            user_id = uid(BATCH_ID, run['number'], 'user')
            db.add(User(
                id=user_id, email=f'g10-student-{run["number"]:02}@example.invalid', role='user',
                name=f'G10 Student {run["number"]:02}', profile_image_url='/user.png',
                created_at=at, updated_at=at, last_active_at=at,
                info={
                    'synthetic': True,
                    'seed_batch': BATCH_ID,
                    'profile': run['profile'],
                    'note': 'Fictional participant generated for analysis examples; not collected student data.',
                },
                settings={}))
            db.add(GroupMember(id=uid(user_id, 'membership'), group_id=group_id, user_id=user_id,
                               created_at=at + 5, updated_at=at + 5))
        await db.commit()
    return reviewer_id


async def review_manual_items(ctx, runs, sessions):
    """A synthetic reviewer scores manual free-text items through the real score route."""
    from sqlalchemy import select
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.question_submissions import QuestionResponse, QuestionSubmission
    from open_webui.models.experiment_plans import ExperimentSessionTask
    rng = random.Random(f'{BATCH_ID}:review')
    at = ns_of(REVIEW_START)
    by_id = {q['id']: key for key, q in ctx.questions.items()}
    reviewed = 0
    day_two = False
    for run in runs:
        sid = sessions.get(run['number'])
        if not sid:
            continue
        if run['number'] >= 28 and not day_two:
            at = max(at, ns_of(REVIEW_START) + 22 * 3600 * NS)
            day_two = True
        async with get_async_db_context() as db:
            rows = (await db.execute(
                select(QuestionResponse, ExperimentSessionTask.position)
                .join(QuestionSubmission, QuestionSubmission.id == QuestionResponse.submission_id)
                .join(ExperimentSessionTask, ExperimentSessionTask.id == QuestionSubmission.session_task_id)
                .where(ExperimentSessionTask.experiment_session_id == sid,
                       QuestionSubmission.status == 'FINALIZED',
                       QuestionResponse.grading_status == 'AWAITING_REVIEW')
            )).all()
        rows = sorted(rows, key=lambda row: (row[1], ctx.questions[by_id[row[0].question_id]]['position']))
        for response, position in rows:
            if position in UNREVIEWED_TASKS.get(run['number'], set()):
                continue
            answer = run['answers'][by_id[response.question_id]]
            at += rng.randint(35, 160) * NS + rng.randint(0, 999) * MS
            CLOCK.set(at, force=True)
            ctx.current_user = ctx.reviewer
            result = await ctx.client.put(f'/api/v1/analytics/experiments/question-responses/{response.id}/score', json={
                'score': str(sum(answer.get('rubric_points', [0]))), 'note': answer.get('rationale')})
            if result.status_code != 200:
                raise RuntimeError(f'Review failed for run {run["number"]}: {result.status_code} {result.text[:300]}')
            reviewed += 1
    return reviewed


async def run_cohort(args, temp_db, data_dir):
    import httpx
    from fastapi import FastAPI
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.experiment_plans import ExperimentPlans
    from open_webui.models.users import Users
    from open_webui.routers import experiment_analytics, experiment_telemetry, experiments
    from open_webui.utils.auth import get_admin_user, get_verified_user
    from open_webui.utils.experiment_perturbations import assign_condition
    from sqlalchemy import select, update
    from open_webui.models.experiments import ExperimentSession
    from open_webui.models.users import User

    snapshot, runs = load_content()
    validate_content(snapshot, runs)
    ctx = Context()
    ctx.questions = snapshot['questions']
    ctx.surveys = snapshot['surveys']
    plan_id = snapshot['plan']['id']
    group_id = snapshot['plan']['group_id']
    ctx.plan = await ExperimentPlans.get_plan(plan_id)
    if ctx.plan is None or str(getattr(ctx.plan.status, 'value', ctx.plan.status)) != 'PUBLISHED':
        raise SystemExit('The Grade 10 plan is not published in this database.')
    names = {condition.id: condition.name.split(' ')[0] for condition in ctx.plan.conditions}
    mapping = {'Control': 'Control', 'Tutor': 'Tutor', 'Reminder': 'Reminder', 'Timing': 'Delay'}
    for run in runs:
        condition_id, _, _ = assign_condition(ctx.plan, uid(BATCH_ID, run['number'], 'user'))
        if mapping[names[condition_id]] != run['condition']:
            raise SystemExit(f'Run {run["number"]}: authored for {run["condition"]}, assigned {names[condition_id]}')

    app = FastAPI()
    app.include_router(experiments.router, prefix='/api/v1/experiments')
    app.include_router(experiment_telemetry.router, prefix='/api/v1/experiments/telemetry')
    app.include_router(experiment_analytics.router, prefix='/api/v1/analytics/experiments')
    app.dependency_overrides[get_verified_user] = lambda: ctx.current_user
    app.dependency_overrides[get_admin_user] = lambda: ctx.reviewer

    CLOCK.set(ns_of(ACCOUNTS_CREATED), force=True)
    reviewer_id = await create_accounts(runs, group_id)
    ctx.reviewer = await Users.get_user_by_id(reviewer_id)

    starts = {}
    for members, period in PERIODS:
        rng = random.Random(f'{BATCH_ID}:period:{period.isoformat()}')
        for number in members:
            starts[number] = ns_of(period) + rng.randint(20, 390) * NS + rng.randint(0, 999_999_999)
    order = sorted(runs, key=lambda run: starts[run['number']])
    report = []
    sessions = {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver', timeout=None) as client:
        ctx.client = client
        for run in order:
            participant = Participant(ctx, run, starts[run['number']])
            await participant.simulate()
            sessions[run['number']] = participant.session_id
            end = max(CLOCK.now, participant.last_request_ns)
            async with get_async_db_context() as db:
                await db.execute(update(User).where(User.id == participant.user_id).values(last_active_at=end // NS))
                await db.commit()
            report.append({
                'run': run['number'],
                'condition': run['condition'],
                'outcome': run['outcome'],
                'session_id': participant.session_id,
                'requests': len(participant.request_rows),
                'telemetry_events': participant.accepted_events,
                'telemetry_rejected': participant.rejected_events,
                'stretched_tasks': participant.adjustments,
            })
            print(f'run {run["number"]:02} {run["condition"]:<8} {run["outcome"]:<17} '
                  f'requests={len(participant.request_rows):<3} events={participant.accepted_events:<6} '
                  f'lost={participant.rejected_events:<4} stretched={participant.adjustments}', flush=True)
        reviewed = await review_manual_items(ctx, runs, sessions)
        print(f'manual reviews: {reviewed}', flush=True)

        # Export exactly as the dashboard does: participant list order, then the full-session export.
        restore_runtime()
        ctx.current_user = ctx.reviewer
        ordered = []
        page = 1
        while True:
            listing = (await client.get('/api/v1/analytics/experiments/participants',
                                        params={'group_id': group_id, 'page': page, 'limit': 100})).json()
            ordered.extend(item['session_id'] for item in listing['items'] if item.get('session_id') in sessions.values())
            if page * 100 >= listing['total']:
                break
            page += 1
        if sorted(ordered) != sorted(sessions.values()):
            raise RuntimeError('Participant listing did not return all 40 sessions')
        exported = await client.post('/api/v1/analytics/experiments/export/sessions',
                                     json={'ids': ordered, 'anonymized': True})
        if exported.status_code != 200:
            raise RuntimeError(f'Export failed: {exported.status_code} {exported.text[:500]}')
        disposition = exported.headers['content-disposition']
        filename = disposition.split('filename="', 1)[1].rstrip('"')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        target = args.output_dir / filename
        target.write_bytes(exported.content)
    return target, report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', type=Path, default=ROOT / 'backend/data/webui.db')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--keep-database', type=Path, help='Also copy the populated temporary database here')
    args = parser.parse_args()
    source = args.database.resolve()
    if not source.is_file():
        parser.error(f'Database does not exist: {source}')
    before = sha256(source)
    work = Path(tempfile.mkdtemp(prefix='grade10-cohort-'))
    try:
        temp_db = work / 'webui.db'
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(temp_db) as dst:
            src.backup(dst)
        os.environ.update(
            ENABLE_DB_MIGRATIONS='False',
            DATABASE_URL=f'sqlite:///{temp_db}',
            DATA_DIR=str(work),
            OFFLINE_MODE='true',
            DATABASE_ENABLE_SQLITE_WAL='False',
            STATIC_DIR=str(work / 'static'),
            FRONTEND_BUILD_DIR=str(work / 'frontend'),
            CORS_ALLOW_ORIGIN='http://localhost',
            WEBUI_SECRET_KEY=SECRET_KEY,
            EXPERIMENT_TELEMETRY_EXTENSION_ENABLED='true',
            EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=ORIGIN,
            EXPERIMENT_TELEMETRY_EXTENSION_ID=EXTENSION_ID,
            EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL=f'https://chromewebstore.google.com/detail/{EXTENSION_ID}',
            GLOBAL_LOG_LEVEL='WARNING',
        )
        sys.path.insert(0, str(ROOT / 'backend'))
        patch_runtime()
        target, report = asyncio.run(run_cohort(args, temp_db, work))
        restore_runtime()
        if args.keep_database:
            shutil.copy2(temp_db, args.keep_database)
    finally:
        restore_runtime()
        shutil.rmtree(work, ignore_errors=True)
    after = sha256(source)
    if before != after:
        raise SystemExit('Source database changed unexpectedly!')
    summary = Counter(row['outcome'] for row in report)
    print(json.dumps({
        'export': str(target),
        'bytes': target.stat().st_size,
        'source_database_unchanged': True,
        'outcomes': summary,
        'conditions': Counter(row['condition'] for row in report),
    }, indent=2, default=dict))


if __name__ == '__main__':
    main()
