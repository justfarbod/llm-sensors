# Grade 10 synthetic runs

`grade10_runs.json` contains ten curated completed demonstrations for **experiment Group A**, published plan version 13 (`7848f5d2-0c2b-4c15-83d5-140432bfd4c7`). The embedded snapshot pins the published questions, source packet, survey choices, task budgets, and experimental conditions. These are fictional examples, not collected student data or evidence of a condition's effectiveness.

The batch creates `Grade 10 Demo 01` through `Grade 10 Demo 10`. It includes six finalized tasks per participant, 150 academic answers, ten original essays, 30 survey submissions, 106 simulated AI requests, shared conversations, and typing/clipboard/focus telemetry. Conditions deliberately cover three Control, three Tutor, two Reminder, and two Delay runs. No participant has authentication credentials.

Manual scores are synthetic rubric reviews, with points and explanations stored on responses and grading attempts. Automatic marks use the repository's existing grading functions. Essays have no numeric grade. Simulated messages use `synthetic-grade10-demo`; token usage is a character-count estimate, not a provider measurement. All synthetic provenance is recorded in participant metadata, chat metadata, condition assignment identifiers, and review rationales.

From the repository root:

```bash
# Read-only preview; reject a changed plan or incompatible prior batch.
backend/venv/bin/python scripts/seed_grade10_runs.py

# Back up SQLite and atomically insert all ten runs.
backend/venv/bin/python scripts/seed_grade10_runs.py --apply

# Verify the real dashboard routes and full export without changing data.
backend/venv/bin/python scripts/verify_grade10_dashboard.py

# Run rollback, preservation, idempotence, fixture, and consistency tests.
backend/venv/bin/python -m pytest scripts/test_seed_grade10_runs.py -q
```

The default database is `backend/data/webui.db`; `--database PATH` supports testing a copy. Apply mode creates a private SQLite backup in `backend/data/backups/` before inserting. Its JSON report contains the backup path, participant/session IDs, scores, and counts. Save the report when applying. Repeating the command verifies the exact existing batch and returns `already_present`; it does not create another ten participants. A partial or changed batch is rejected rather than repaired automatically.

The seeder verifies every pre-existing row remains unchanged and rolls back the transaction if validation fails. It does not import application startup or run migrations. The dashboard verifier isolates configuration imports in temporary files and gives all tested routes read-only database connections. Some execution sandboxes require that verifier to run outside the sandbox because FastAPI's thread-based test harness stalls inside it.

The workflow dashboard counts all 106 prompts and responses across biology, essay, and geometry. Its demo filter selects exactly these ten runs. Workflow provenance columns are excluded from the seed compatibility comparison; the fixture and its batch hashes remain unchanged.

The integration tests use temporary copies of the local instance, preferring the preserved pre-seed backup after insertion. They never modify the original database. They are intentionally tied to this published plan and fixture, rather than a general-purpose demo generator.
