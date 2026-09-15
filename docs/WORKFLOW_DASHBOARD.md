# Workflow researcher dashboard

The researcher dashboard has four views: Overview, Workflows, Participants, and LLM Usage. Shared URL filters are `workflow_id`, `configuration_id`, `group_id`, `condition_key`, `data_kind` (`all`, `demo`, `non_demo`), dates, and status. Retired essay/question/survey/statistics URLs redirect to Workflows with their query parameters retained.

Applied plans store captured workflow identity separately from the editable library. The semantic fingerprint covers resolved question/survey content, essay topic pools, task ordering, budgets, grading, conditions, progression and chat settings. Numeric representations such as `3` and `3.0` are normalized. Deployment IDs, source keys and timestamps are excluded. Historical plans without an identified origin remain separate workflow entries. Grade 10's origin is inferred only after matching its resolved content; its source revision remains unknown.

Results combine steps only within matching configurations. Essay statistics remain separated by assigned topic, and survey distributions by step and question. Native submissions determine completion; missing work is pending, optional survey skips are separate, and ungraded answers are not zero scores. Question grading and retries continue through the existing admin-only routes.

Usage follows message/task and chat/session links, deduplicates messages, and leaves unassigned messages in run totals. Empty modern chats do not cause unrelated personal messages to be counted. The labeled time-window fallback applies only to legacy runs without an applied plan or explicit chat links; ambiguous overlapping windows are excluded. Export, detail, and analytics share the same attribution. Request records supply delay measurements. Simulated provider identifiers remain visibly synthetic.

## Migration and verification

Back up and migrate the local database:

```bash
backend/venv/bin/python scripts/migrate_workflow_dashboard.py
```

The script uses SQLite's backup API, applies the additive migration, backfills fingerprints and the verified Grade 10 identity, then compares every existing table's original columns against the pre-migration baseline. It reports the backup location. `--database PATH` supports a disposable copy. No sessions, responses, messages, telemetry, fixture contents or batch hashes are rewritten.

```bash
backend/venv/bin/python scripts/verify_grade10_dashboard.py
backend/venv/bin/python scripts/check_workflow_dashboard.py -q backend/open_webui/test/routers/test_workflow_results.py backend/open_webui/test/routers/test_experiment_analytics.py backend/open_webui/test/models/test_condition_workflow_roundtrip.py backend/open_webui/test/models/test_prompt_budget_workflow_roundtrip.py scripts/test_seed_grade10_runs.py
```

Use targeted checks on memory-constrained systems; do not run full builds in this WSL workspace.

The Grade 10 verifier checks the live database read-only, including 11 runs, six ordered steps, ten demos, 150 question answers, ten essays, 30 surveys, 106 prompts and responses, condition/date/status filters, pagination, session details and full exports. Seed apply mode is also checked for idempotence.

For Chromium component integration checks, first create and migrate a copy at `.generated/workflow-dashboard/validation.db`. Run the local-only Vite fixture with `node_modules/.bin/vite --config scripts/fixtures/workflow-dashboard-browser/vite.config.mjs`, then `backend/venv/bin/python scripts/browser_check_workflow_dashboard.py`. The browser harness routes analytics requests through read-only FastAPI sessions and exercises the four tabs, filters, grading inspection, essays, task chats, sensitive telemetry, downloads, retired URLs and mobile rendering. Browser shared libraries must be available in the environment. It creates no live login credentials or external provider requests.
