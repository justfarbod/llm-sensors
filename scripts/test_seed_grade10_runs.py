"""Integration checks for the one-off, instance-specific Grade 10 seed.

The local database is read only; all mutations happen in temporary copies.
"""

import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

import pytest

spec = importlib.util.spec_from_file_location('grade10_seed', Path(__file__).with_name('seed_grade10_runs.py'))
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


@pytest.fixture
def fixture():
    return json.loads(seed.FIXTURE.read_text())


@pytest.fixture
def database(tmp_path, fixture):
    path = tmp_path / 'webui.db'
    # Copy an unseeded baseline. After deployment, use the preserved pre-seed backup.
    source = seed.ROOT / 'backend/data/webui.db'
    backups = sorted((source.parent / 'backups').glob('webui-before-grade10-demo-v1-*.db'))
    if backups:
        source = backups[0]
    with seed.connect(source) as original, sqlite3.connect(path) as dest:
        original.backup(dest)
    return path


def test_preview_is_readonly_and_complete(database, fixture):
    before = database.read_bytes()
    report = seed.run_seed(database, fixture)
    assert report['status'] == 'preview'
    assert report['counts']['experiment_session'] == 10
    assert report['counts']['question_response'] == 150
    assert report['counts']['survey_response'] == 130
    assert sum(run['ai_requests'] for run in report['runs']) == 106
    assert {run['total_score'] for run in report['runs']} == {58.5, 49.5, 29, 57.5, 58, 29.5, 56, 60, 51}
    assert database.read_bytes() == before


def test_apply_backup_preservation_idempotence_and_corruption_detection(database, fixture, tmp_path):
    with seed.connect(database) as db:
        baseline = seed.database_baseline(db)
    report = seed.run_seed(database, fixture, apply=True, backup_dir=tmp_path / 'backups')
    assert report['status'] == 'inserted'
    assert report['existing_records_unchanged']
    with seed.connect(Path(report['backup'])) as backup:
        assert seed.database_baseline(backup) == baseline
    with seed.connect(database) as db:
        assert not list(db.execute('PRAGMA foreign_key_check'))
        assert db.execute('SELECT count(*) FROM experiment_session').fetchone()[0] == 11
        assert db.execute("SELECT count(*) FROM auth WHERE email LIKE 'grade10-demo-%'").fetchone()[0] == 0
        after = seed.database_baseline(db)
    rerun = seed.run_seed(database, fixture, apply=True)
    assert rerun['status'] == 'already_present'
    with seed.connect(database) as db:
        assert seed.database_baseline(db) == after
    with sqlite3.connect(database) as db:
        db.execute(
            'UPDATE question_response SET effective_score=0 WHERE id=(SELECT id FROM question_response WHERE rationale LIKE ? LIMIT 1)',
            ('Synthetic rubric reviewer%',),
        )
    with pytest.raises(ValueError, match='Incomplete/conflicting batch row'):
        seed.run_seed(database, fixture, apply=True)


def test_failure_rolls_back_all_inserts(database, fixture, monkeypatch, tmp_path):
    with seed.connect(database) as db:
        before = seed.database_baseline(db)

    def fail_after_insert(db, batch):
        raise ValueError('injected validation failure')

    monkeypatch.setattr(seed, 'verify_batch', fail_after_insert)
    with pytest.raises(ValueError, match='injected validation failure'):
        seed.run_seed(database, fixture, apply=True, backup_dir=tmp_path / 'backups')
    with seed.connect(database) as db:
        assert seed.database_baseline(db) == before


def test_changed_plan_and_partial_batch_abort(database, fixture):
    with sqlite3.connect(database) as db:
        db.execute('UPDATE experiment_plan SET version=14 WHERE id=?', (fixture['snapshot']['plan']['id'],))
    with pytest.raises(ValueError, match='Published plan'):
        seed.run_seed(database, fixture, apply=True)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE experiment_plan SET version=13 WHERE id=?', (fixture['snapshot']['plan']['id'],))
        db.execute(
            "INSERT INTO user(id,name,email,role,profile_image_url,created_at,updated_at,last_active_at,info) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                seed.identifier(fixture['batch_id'], 1, 'user'),
                'Partial demo',
                'grade10-demo-01@example.invalid',
                'user',
                '/user.png',
                1,
                1,
                1,
                seed.dump({'seed_batch': fixture['batch_id']}),
            ),
        )
    with pytest.raises(ValueError, match='Conflicting/incomplete'):
        seed.run_seed(database, fixture, apply=True)


def test_events_scores_chats_and_surveys_are_consistent(fixture):
    batch = seed.Batch(fixture, time.time_ns() - 3 * 3600 * seed.NS).build()
    batch.validate(time.time_ns())
    for summary in batch.tables['experiment_telemetry_summary']:
        events = [
            row
            for row in batch.tables['experiment_telemetry_event']
            if row['experiment_session_id'] == summary['experiment_session_id']
        ]
        keys = [json.loads(row['payload_json']) for row in events if row['event_type'] == 'keystroke']
        assert summary['total_keystrokes'] == len(keys)
        assert summary['pause_count'] == sum((key.get('inter_key_interval_ms') or 0) > 2000 for key in keys)
        assert summary['total_time_away_ms'] == sum(
            json.loads(row['payload_json'])['away_duration_ms'] for row in events if row['event_type'] == 'focus_return'
        )
        # No typing occurs while the participant is away.
        away = None
        for event in sorted(events, key=lambda r: r['event_time']):
            if event['event_type'] == 'focus_away':
                away = event['event_time']
            elif event['event_type'] == 'focus_return':
                assert away is not None
                away = None
            elif event['event_type'] == 'keystroke':
                assert away is None
    for chat in batch.tables['chat']:
        payload = json.loads(chat['chat'])
        history = payload['history']['messages']
        assert len(history) == sum(row['chat_id'] == chat['id'] for row in batch.tables['chat_message'])
        last = history[payload['history']['currentId']]
        assert last['role'] == 'assistant' and last['childrenIds'] == []
        for mid, message in history.items():
            if message['parentId']:
                assert mid in history[message['parentId']]['childrenIds']
    bad = copy.deepcopy(fixture)
    bad['runs'][0]['surveys']['5'][3] = [0, 4]
    with pytest.raises(ValueError, match='Exclusive checking'):
        seed.check_fixture(bad)
    bad = copy.deepcopy(fixture)
    bad['runs'][0]['answers']['B4']['rubric_points'][0] = 5
    with pytest.raises(ValueError, match='Invalid rubric'):
        seed.check_fixture(bad)
