#!/usr/bin/env python3
"""Back up, migrate and verify workflow provenance without changing existing data."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
NEW_COLUMNS = {'experiment_plan': {'source_workflow_id', 'source_workflow_name', 'source_workflow_revision', 'workflow_origin', 'configuration_fingerprint'}, 'experiment_plan_item': {'source_step_key'}, 'experiment_condition': {'source_condition_key'}}


def baseline(db):
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"):
        columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")') if r[1] not in NEW_COLUMNS.get(table, set())]
        fields = ','.join('"'+c+'"' for c in columns)
        records = sorted(json.dumps(row, default=str) for row in db.execute(f'SELECT {fields} FROM "{table}"'))
        result[table] = hashlib.sha256('\n'.join(records).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'backend/data/webui.db')
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error('Database does not exist')
    backup = path.parent / 'backups' / f'{path.stem}-before-workflow-dashboard-{time.time_ns()}.db'
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as dest:
        source.backup(dest)
        before = baseline(source)
    backup.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='workflow-migration-') as temp:
        os.environ.update(ENABLE_DB_MIGRATIONS='False', DATABASE_URL=f'sqlite:///{path}', DATA_DIR=temp,
                          STATIC_DIR=f'{temp}/static', FRONTEND_BUILD_DIR=f'{temp}/frontend', OFFLINE_MODE='true',
                          DATABASE_ENABLE_SQLITE_WAL='True', WEBUI_SECRET_KEY='local-migration-only')
        sys.path.insert(0, str(ROOT / 'backend'))
        from alembic.config import Config
        from alembic import command
        config = Config()
        config.set_main_option('script_location', str(ROOT / 'backend/open_webui/migrations'))
        command.upgrade(config, 'f9a0b1c2d3e4')
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from open_webui.models import experiment_workflows  # Complete config imports before opening a transaction.
        from open_webui.utils.workflow_provenance import backfill_provenance
        async def backfill():
            engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
            try:
                async with AsyncSession(engine, expire_on_commit=False) as db:
                    async with db.begin():
                        return await backfill_provenance(db)
            finally:
                await engine.dispose()
        result = asyncio.run(backfill())
    with sqlite3.connect(path) as db:
        assert baseline(db) == before, 'Existing data changed during migration'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    print(json.dumps({'database': str(path), 'backup': str(backup), 'existing_data_preserved': True, **result}, indent=2))


if __name__ == '__main__':
    main()
