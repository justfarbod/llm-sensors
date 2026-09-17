# Open WebUI Research Toolbox

This standalone package turns the admin dashboard's **Full research session JSON export** into lossless,
analysis-ready pandas tables. It retains the original document, preserves open-ended JSON fields as Python
dictionaries/lists, and adds explicit lineage columns so records can be joined without reconstructing database
relationships from reference IDs.

Use the participant dashboard's full session export (`/export/sessions`), not its flat participant summary
CSV/JSON. The notebook supports current schema 1.x exports, including workflow identity, demo status, and
session/task usage attribution, as well as historical exports without those sections. Set `EXPORT_PATH`
in the notebook (or `RESEARCH_EXPORT_PATH` before launching Jupyter) to analyze your own file. Timelines
default to the first exported session; change `SESSION_ID` to inspect another run. Empty and incomplete
sessions, including runs without essays, questions, chats, or telemetry, are supported.

The toolbox is a standalone source package. It runs separately from the Open WebUI development servers and should be
installed in its own Python 3.11-or-newer environment.

## Install

From this directory:

```bash
python3 -m pip install -e .
```

From the repository root, the equivalent isolated setup is:

```bash
python3 -m venv .venv-research
. .venv-research/bin/activate
python -m pip install -e './research-toolbox[notebook,test]'
```

For the tutorial notebook and development tools:

```bash
python3 -m pip install -e ".[notebook,test]"
jupyter lab notebooks/full_session_analysis.ipynb
```

## Quick start

```python
from research_toolbox import load_export

data = load_export("experiment-full-sessions-2026-08-17.json")

print(data.metadata)
print(data.validation_warnings)
print(data.sessions.head())
print(data.question_responses.merge(data.questions, on="question_id"))

overview = data.session_overview()
condition_metrics = data.condition_summary()
timeline = data.session_timeline("a-session-id")

data.materialize("processed", format="parquet")
data.materialize("processed-csv", format="csv")
```

`load_export` accepts a path, an open text file, or a decoded mapping. Structural envelope failures and unsupported
schema major versions always raise `ExportValidationError`. The default tolerant mode records incomplete historical
sections in `validation_warnings`; use `load_export(path, strict=True)` to fail on those issues and conflicting repeated
definitions.

## Table model

`data.tables` contains every documented table, including empty tables. Use `data.table("name")` or the corresponding
attribute such as `data.telemetry_events`.

| Area | Tables |
| --- | --- |
| Session context | `sessions`, `session_summaries`, `session_workflows`, `participants`, `groups`, `memberships`, `topics`, `topic_assignments` |
| Exported usage | `session_usage`, `task_usage` |
| Plans and conditions | `plans`, `plan_items`, `plan_item_topics`, `conditions`, `session_conditions`, `condition_task_scopes`, `prompt_injections`, `warning_modals`, `response_timings` |
| Definitions | `question_tasks`, `questions`, `question_choices`, `question_blanks`, `accepted_blank_answers`, `free_text_configs`, `survey_tasks`, `survey_questions`, `survey_choices` |
| Work and responses | `session_tasks`, `essays`, `question_submissions`, `question_responses`, `question_response_choices`, `question_blank_answers`, `grading_attempts`, `score_overrides`, `survey_submissions`, `survey_responses`, `survey_response_choices` |
| Conversations | `chats`, `messages`, `chat_attachments`, `files`, `chat_embedded_files`, `feedback` |
| Instrumentation | `telemetry_summaries`, `extension_presence`, `telemetry_events`, `llm_requests`, `warning_states` |

All exported `record` keys become columns. Parent identifiers and `_session_order`, `_parent_order`, and
`_record_order` preserve lineage and deterministic traversal order. Raw timestamps remain unchanged; recognized
timestamp fields gain a `<field>_dt` UTC companion. Arbitrary payload/configuration fields remain nested rather than
being expanded into unstable, export-specific columns.

Shared definitions are deduplicated by ID. If repeated definitions conflict, tolerant mode keeps the first and records
a warning; strict mode raises. Materialization writes one file per selected table and a `manifest.json`. Nested columns
are encoded as canonical JSON strings in files and listed in the manifest.

`sessions.is_demo` preserves the export's demo flag when present. `session_workflows` stores workflow and
configuration identity per session, and `session_overview()` includes these fields. `session_usage` preserves
the exported totals, attribution method, message IDs, nested `by_task` map, and `unattributed` totals;
`task_usage` exposes one row per entry in `by_task`. Message `task_id` uses message-level provenance when
available, falling back to the chat's task. Question definitions use `title` and `description`; the notebook
also accepts the older example's `prompt` field. Conditions remain grouped by assigned `condition_id`,
so different plans' conditions are not automatically pooled across workflow configurations.

## Analysis helpers

- `session_overview()` combines raw and dashboard-derived session fields with group and condition labels.
- `task_progress()` adds completion and elapsed-time fields to ordered session tasks.
- `condition_summary()` produces descriptive counts, means, and medians only.
- `question_score_summary()` summarizes effective scores, grading attempts, and overrides.
- `survey_response_summary()` reports selection counts and scale means.
- `essay_summary()` adds text-derived word and character counts without NLP processing.
- `chat_usage_summary()` counts messages and token usage from canonical message records.
- `telemetry_timeline()` and `session_timeline()` return chronologically ordered event views.

## Privacy

The toolbox does not anonymize data. Identified exports trigger a warning, and free-form essay, chat, survey,
telemetry, and URL content may contain identifying information even when the export's structural anonymization option
was enabled. The bundled example is synthetic and anonymized.
