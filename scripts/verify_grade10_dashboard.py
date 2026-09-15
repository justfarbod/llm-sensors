#!/usr/bin/env python3
"""Read-only verification of Grade 10 data through the real dashboard routes.

Run with backend/venv/bin/python and --database PATH. An in-process FastAPI
harness supplies a synthetic administrator for read/export routes only; it does
not create authentication tokens, expose a server, or call grading mutations.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'backend/data/webui.db')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error('Database is missing')
    with tempfile.TemporaryDirectory(prefix='grade10-api-check-') as data_dir:
        # App configuration imports set SQLite journal pragmas. Give those imports
        # a private bootstrap copy; every tested route receives a read-only session
        # on the actual target below.
        bootstrap = Path(data_dir) / 'bootstrap.db'
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(bootstrap) as destination:
            source.backup(destination)
        os.environ.update(
            ENABLE_DB_MIGRATIONS='False',
            DATABASE_URL=f'sqlite:///{bootstrap}',
            DATA_DIR=data_dir,
            OFFLINE_MODE='true',
            DATABASE_ENABLE_SQLITE_WAL='False',
            STATIC_DIR=str(Path(data_dir) / 'static'),
            FRONTEND_BUILD_DIR=str(Path(data_dir) / 'frontend'),
            CORS_ALLOW_ORIGIN='http://localhost',
            WEBUI_SECRET_KEY='synthetic-readonly-test-harness',
        )
        sys.path.insert(0, str(ROOT / 'backend'))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from open_webui.internal.db import get_async_session
        from open_webui.routers import experiment_analytics as analytics
        from open_webui.routers.experiment_telemetry import TelemetryEventForm
        from open_webui.utils.auth import get_admin_user
        from open_webui.models.question_tasks import grade_multiple_select, grade_fill_blanks

        logging.getLogger('httpx').setLevel(logging.WARNING)

        spec = importlib.util.spec_from_file_location('grade10_verify_seed', ROOT / 'scripts/seed_grade10_runs.py')
        seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed)
        fixture = json.loads(seed.FIXTURE.read_text())
        report = seed.run_seed(path, fixture)
        assert report['status'] == 'already_present'
        # Validate the generated forms with the application's Pydantic contract.
        with seed.connect(path) as db:
            before = seed.database_baseline(db)
            for run in report['runs']:
                records = seed.rows(
                    db, 'SELECT * FROM experiment_telemetry_event WHERE experiment_session_id=?', (run['session_id'],)
                )
                for row in records:
                    if row['event_type'].startswith(('warning_', 'response_')):
                        continue  # Runtime events have their own server-side contract.
                    form = dict(
                        event_id=row['id'],
                        type=row['event_type'],
                        timestamp=datetime.fromtimestamp(row['event_time'] / seed.NS, timezone.utc),
                        field=row['field_context'],
                        session_task_id=row['session_task_id'],
                        question_id=row['question_id'],
                        submission_id=row['submission_id'],
                        **json.loads(row['payload_json']),
                    )
                    TelemetryEventForm.model_validate(form)
            # Compare extracted helpers to normal model imports on actual fixture answers.
            for run in fixture['runs']:
                for key, q in fixture['snapshot']['questions'].items():
                    a = run['answers'][key]
                    if q['question_type'] == 'MULTIPLE_SELECT':
                        from decimal import Decimal

                        expected = grade_multiple_select(
                            Decimal(str(q['max_score'])),
                            {q['choices'][i]['id'] for i in a['choices']},
                            {c['id'] for c in q['choices'] if c['is_correct']},
                            {c['id'] for c in q['choices']},
                        )
                        assert seed.grade(q, a) == expected
                    elif q['question_type'] == 'FILL_BLANK':
                        from decimal import Decimal

                        expected = grade_fill_blanks(
                            Decimal(str(q['max_score'])),
                            {b['id']: a['blanks'][b['blank_key']] for b in q['blanks']},
                            {b['id']: [v['answer'] for v in b['accepted']] for b in q['blanks']},
                            bool(q['case_sensitive']),
                        )
                        assert seed.grade(q, a) == expected
        engine = create_async_engine(f'sqlite+aiosqlite:///file:{path}?mode=ro&uri=true')

        async def readonly_db():
            async with AsyncSession(engine, expire_on_commit=False) as db:
                yield db

        app = FastAPI()
        app.include_router(analytics.router, prefix='/api/v1/analytics/experiments')
        app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
            id='synthetic-readonly-verifier', role='admin'
        )
        app.dependency_overrides[get_async_session] = readonly_db
        base = '/api/v1/analytics/experiments'
        checked = []
        with TestClient(app) as client:

            def get(route, **params):
                response = client.get(base + route, params=params)
                assert response.status_code == 200, (route, response.status_code, response.text[:500])
                return response.json()

            participants = get(
                '/participants', group_id=fixture['snapshot']['plan']['group_id'], search='Grade 10 Demo', limit=100
            )
            assert participants['total'] == 10
            assert {r['name'] for r in participants['items']} == {r['participant'] for r in report['runs']}
            for section in ('filters', 'overview', 'essays', 'usage', 'essay-stats', 'surveys', 'question-results'):
                get('/' + section, group_id=fixture['snapshot']['plan']['group_id'])
            workflow_id = '640b0bdc-df29-4695-9a16-51944ff5a340'
            workflow = get('/workflows/' + workflow_id)
            assert workflow['runs'] == 11 and len(workflow['steps']) == 6
            assert [s['position'] for s in workflow['steps']] == list(range(6))
            demo_filters = dict(workflow_id=workflow_id, data_kind='demo')
            assert get('/participants', **demo_filters)['total'] == 10
            assert get('/overview', **demo_filters)['metrics']['total_sessions'] == 10
            usage = get('/usage', **demo_filters)
            assert usage['metrics']['total_prompts'] == usage['metrics']['total_responses'] == 106
            assert sum(s['prompts'] for s in usage['by_step']) == 106
            assert get('/participants', workflow_id=workflow_id, data_kind='non_demo')['total'] == 1
            for condition, count in [('control', 3), ('tutor', 3), ('reminder', 2), ('delay', 2)]:
                assert get('/participants', **demo_filters, condition_key='condition-' + condition)['total'] == count
            assert get('/overview', **demo_filters, state='IN_PROGRESS')['metrics']['total_sessions'] == 0
            assert get('/overview', **demo_filters, date_to=1)['metrics']['total_sessions'] == 0
            assert get('/overview', **demo_filters)['task_progress'] == {'total': 60, 'completed': 60, 'skipped': 0}
            page_one = get('/participants', **demo_filters, page=1, limit=5)
            page_two = get('/participants', **demo_filters, page=2, limit=5)
            assert len({r['session_id'] for r in page_one['items'] + page_two['items']}) == 10
            for step in workflow['steps']:
                results = get(f'/workflows/{workflow_id}/steps/{step["key"]}', data_kind='demo', limit=100)
                assert results['total'] == 10 and results['step']['completed'] == 10
            for run in report['runs']:
                detail = get('/sessions/' + run['session_id'])
                assert detail['state'] == 'COMPLETED'
                profile = next(r for r in fixture['runs'] if r['number'] == int(run['participant'][-2:]))
                assert detail['prompts'] == sum(profile['request_counts'])
                assert len(detail['tasks']) == 6 and all(t['status'] == 'FINALIZED' for t in detail['tasks'])
                assert detail['essay']['word_count'] == run['essay_words']
                assert len(detail['perturbation_requests']) == run['ai_requests']
                assert detail['timeline']['pre_survey_submitted_at'] and detail['timeline']['post_survey_submitted_at']
                for task in detail['tasks']:
                    if task['task_type'] == 'SURVEY':
                        assert len(task['survey_submission']['answers']) in (3, 5)
                        assert all(answer['value'] is not None for answer in task['survey_submission']['answers'])
                    if task['task_type'] == 'QUESTION':
                        result = get('/question-submissions/' + task['question_submission']['submission_id'])
                        assert result['grading_status'] == 'GRADED'
                        expected_score = run['biology_score'] if task['position'] == 1 else run['geometry_score']
                        assert result['score'] == expected_score
                        assert len(result['responses']) == (5 if task['position'] == 1 else 10)
                checked.append(
                    {
                        'participant': run['participant'],
                        'session_id': run['session_id'],
                        'score': run['total_score'],
                        'requests': run['ai_requests'],
                    }
                )
            exported = client.post(
                base + '/export/sessions', json={'ids': [r['session_id'] for r in report['runs']], 'anonymized': False}
            )
            assert exported.status_code == 200, exported.text[:500]
            full = exported.json()
            assert len(full['sessions']) == 10
            for session in full['sessions']:
                assert session['workflow']['workflow_id'] == workflow_id
                assert session['workflow']['workflow_origin'] == 'inferred'
                assert session['workflow']['source_revision'] is None
                assert len(session['tasks']) == 6
                assert len(session['chats']) == 1
                assert session['telemetry']['summary']['total_keystrokes'] > 0
            assert sum(s['usage']['prompts'] for s in full['sessions']) == 106
            assert sum(s['usage']['responses'] for s in full['sessions']) == 106
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(full, ensure_ascii=False, indent=2) + '\n')
        asyncio.run(engine.dispose())
        assert seed.run_seed(path, fixture, apply=True)['status'] == 'already_present'
        with seed.connect(path) as db:
            assert seed.database_baseline(db) == before
        print(
            json.dumps(
                {
                    'status': 'passed',
                    'database': str(path),
                    'dashboard_sections': 8,
                    'sessions_verified': 10,
                    'question_submissions_verified': 20,
                    'full_export_sessions': 10,
                    'readonly': True,
                    'whole_run_prompts': 106,
                    'runs': checked,
                },
                indent=2,
            )
        )


if __name__ == '__main__':
    main()
