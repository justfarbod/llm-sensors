#!/usr/bin/env python3
"""Run focused backend regression tests with isolated app import directories."""
import os
from pathlib import Path
import sys
import sqlite3
import tempfile
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='workflow-dashboard-check-') as temp:
    with sqlite3.connect((ROOT/'backend/data/webui.db').as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(f'{temp}/bootstrap.db') as target:
        source.backup(target)
    os.environ.update(DATABASE_URL=f'sqlite:///{temp}/bootstrap.db', ENABLE_DB_MIGRATIONS='False', DATA_DIR=temp,
                      STATIC_DIR=f'{temp}/static', FRONTEND_BUILD_DIR=f'{temp}/frontend', OFFLINE_MODE='true',
                      DATABASE_ENABLE_SQLITE_WAL='True', WEBUI_SECRET_KEY='isolated-workflow-test-key', CORS_ALLOW_ORIGIN='http://localhost')
    sys.path.insert(0,str(ROOT/'backend'))
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:] or ['-q','backend/open_webui/test/routers/test_experiment_analytics.py','backend/open_webui/test/models/test_condition_workflow_roundtrip.py','backend/open_webui/test/models/test_prompt_budget_workflow_roundtrip.py','scripts/test_seed_grade10_runs.py']))
