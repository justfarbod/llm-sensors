# Open WebUI Research Toolbox

This standalone package turns the admin dashboard's **Full research session JSON export** into lossless,
analysis-ready pandas tables. It retains the original document, preserves open-ended JSON fields as Python
dictionaries/lists, and adds explicit lineage columns so records can be joined without reconstructing database
relationships from reference IDs. Retrieval helpers then return tidy, joined views: answers, surveys, essays,
chats, request timing and interaction telemetry.

Use the participant dashboard's full session export (`/export/sessions`), not its flat participant summary
CSV/JSON. The toolbox follows the current export contract (schema 1.x: workflow identity, demo status,
session/task usage attribution, extension telemetry schema 4) and still loads historical exports without those
sections. Empty and incomplete sessions — runs without essays, questions, chats or telemetry, abandoned tasks,
unsubmitted drafts — are supported.

The tutorial notebook `notebooks/full_session_analysis.ipynb` walks through every helper. Set `RESEARCH_EXPORT_PATH`
before launching Jupyter (or edit `EXPORT_PATH`) to analyze your own file; otherwise it uses the synthetic Grade 10
cohort in `examples/grade10_cohort/` when present, then the small bundled example. Timelines default to the first
exported session; change `SESSION_ID` to inspect another run.

The case-study notebook `notebooks/grade10_condition_case_study.ipynb` shows a complete analysis of the cohort, from
a research question to conclusions. It asks whether the four AI-delivery conditions (ordinary, tutor prompt,
verification reminder, 3-second delay) change how students use the AI, how they score, and how they rate trust and
workload. It covers manipulation checks, rule-based exclusions, count and ANCOVA models with robust standard errors,
linking telemetry to the chat log, robustness checks and a power analysis. It is committed with its outputs, so it
can be read without running it, and it needs the `analysis` extra (scipy, statsmodels).

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
python -m pytest            # includes executing the notebook against several export shapes
```

For the case-study notebook:

```bash
python3 -m pip install -e ".[notebook,analysis]"
jupyter lab notebooks/grade10_condition_case_study.ipynb
```

## Quick start

```python
from research_toolbox import load_export

data = load_export("experiment-full-sessions-20260930-115623.json")

print(data.metadata)
print(data.validation_warnings)

overview = data.session_overview()          # one row per session, raw + dashboard fields + labels
activity = data.session_activity()          # completion, wall/away/active time, data coverage
answers = data.question_answers()           # one row per question response, readable answers and scores
surveys = data.survey_answers()             # one row per survey answer with prompt and typed value
essays = data.essay_texts()                 # submitted essays and unsubmitted drafts
transcript = data.chat_transcript("a-session-id")
timing = data.llm_request_timing()          # latency components per LLM request
typing = data.keystroke_summary()           # per session × task × field
pastes = data.clipboard_events()            # copy/cut/paste with captured text
absences = data.focus_periods()             # time away from the page and where the participant went

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

All exported `record` keys become columns, so columns added to the platform's database models appear without toolbox
changes. Parent identifiers and `_session_order`, `_parent_order`, and `_record_order` preserve lineage and
deterministic traversal order. Shared definitions are deduplicated by ID. If repeated definitions conflict, tolerant
mode keeps the first and records a warning; strict mode raises.

**Timestamps.** Raw timestamps remain unchanged: nanoseconds since the Unix epoch for experiment records (sessions,
plans, tasks, submissions, essays, telemetry, LLM requests, warning states, extension presence) and seconds for chats,
messages, files, feedback, participants, groups and memberships. Recognized timestamp fields gain a `<field>_dt` UTC
companion. `session_summaries` holds the dashboard's derived values, which the platform exports in whole seconds
(`session_start_time`, `session_completion_time` and every `*_at` milestone such as `writing_started_at`,
`pre_survey_submitted_at`, `task_submitted_at`); durations there are float seconds. Client-originated times
(`telemetry_events.event_time`, `llm_requests.client_*_visible_at`) come from the participant's browser clock and can
differ from server times by a few seconds.

**Telemetry payloads.** `telemetry_events` keeps each event's type-specific fields inside its nested `payload_json`:
`keystroke` events carry the literal key as `payload_json["key_value"]` plus `key_class`, `inter_key_interval_ms`
(absent for the first key in a field), `hold_duration_ms` and `modifiers`; `copy`/`cut`/`paste` carry the selected or
pasted text (truncated to 20,000 characters) as `text_value` with `text_length` and `line_count`; extension tab events
(schema 2+) carry `browser_session_id`, `sequence` and a `tab` snapshot. `telemetry_events_flat()` promotes these to
columns. Server runtime events (`warning_modal_*`, `response_*_visible`, `response_navigated_away`) use schema 1 and
link to `request_id`/`message_id`.

**Message task attribution.** `messages.task_id` is resolved in order from the message's own
`experiment_session_task_id`, the LLM request that produced it (`llm_requests.assistant_message_id`), its parent
message, and finally the chat's task; `task_id_source` records which was used. In shared experiment chats the platform
stamps only user messages and the first reply, so later replies are recovered from their request.
`history_message_id` is the message ID used inside the chat history and by `llm_requests` (the stored `message_id` is
`{chat_id}-{history_message_id}`). The export's own `usage.by_task` (`task_usage`) does not perform this recovery and
under-counts replies per task; use `chat_usage_summary(by=("session_id", "task_id"))` for per-task usage.

`sessions.is_demo` preserves the export's demo flag when present. `session_workflows` stores workflow and
configuration identity per session, and `session_overview()` includes these fields. `session_usage` preserves
the exported totals, attribution method, message IDs, nested `by_task` map, and `unattributed` totals;
`task_usage` exposes one row per entry in `by_task`. Question definitions use `title` and `description`; the notebook
also accepts the older example's `prompt` field. Conditions remain grouped by assigned `condition_id`,
so different plans' conditions are not automatically pooled across workflow configurations.

## Helpers

Retrieval helpers join records to `session_context()` (participant, condition name/key, control flag, state, demo
flag) and to readable task and question titles.

| Helper | Returns |
| --- | --- |
| `session_overview()` | Raw and dashboard-derived session fields with group, condition and workflow labels. |
| `session_context()` | One compact row per session: participant, condition name/key, `is_control`, state, `is_demo`. |
| `session_activity()` | State, finished tasks, wall time (to completion or last activity), away and active time, telemetry/extension coverage. |
| `task_progress()` | Ordered session tasks with completion flag and elapsed seconds. |
| `question_answers()` | One row per question response: title, `answer_text`, selected choices, blank answers, correct/incorrect selections, scores and fraction, submission status (abandoned tasks stay `DRAFT`), latest review score/note. |
| `survey_answers()` | One row per survey answer: survey title, prompt, type, typed `value` (choice texts, scale, text), status; legacy `pre_survey`/`post_survey` JSON as `legacy_*` rows. |
| `essay_texts(include_drafts=True)` | Submitted essays and unsubmitted drafts left in essay tasks, with platform and calculated counts. |
| `chat_transcript(session_id=None)` | Messages in conversational order with text, task, tokens, errors and the linked request (status, timing mode, prompt injection). |
| `llm_request_timing()` | Requests with queue, time-to-first-token, generation, artificial delay, server-emit and client-visible latencies (ms). |
| `telemetry_events_flat(event_types=None)` | Telemetry with payload fields as columns (`key_value`, `text_value`, `tab_*`, `modifier_*`, …). |
| `keystrokes()`, `keystroke_summary(by=…, pause_threshold_ms=2000)` | Typing events and per-group counts, deletions, intervals, pauses, hold times. |
| `clipboard_events()` | Copy, cut and paste events with field, task and captured text. |
| `tab_activity()` | Extension tab, window-focus and telemetry-loss events. |
| `focus_periods()` | Each absence from the page (`focus_away` → `focus_return`), its duration, the interrupted task and destination tab. |
| `condition_summary(metrics=None)` | Descriptive counts, means and medians per assigned condition, with labels. |
| `question_score_summary()` | Per-question counts, mean/total effective scores, fraction of maximum, reviews and pending reviews. |
| `survey_response_summary()` | Per-choice selection counts and scale means with prompts. |
| `essay_summary()` | Essay records with text-derived word and character counts. |
| `chat_usage_summary(by=("session_id",))` | Message counts and token usage from message rows. |
| `telemetry_timeline(session_id)`, `session_timeline(session_id)` | Chronologically ordered event views. |

These helpers are descriptive: they expose what the export contains (including incomplete runs) and leave inclusion
and exclusion decisions to the analysis.

## Examples

- `examples/full_sessions.synthetic.json` — a small hand-written example used by the unit tests.
- `examples/grade10_cohort/` — a platform-produced export of 40 synthetic Grade 10 participants with planted
  data-quality issues; see its README for provenance and the issue answer key.
- `tests/fixtures/platform_export_sample.json` — five sessions copied from that export (keystrokes subsampled), used to
  test the toolbox against the platform's real export shape.

## Privacy

The toolbox does not anonymize data. Identified exports trigger a warning, and free-form essay, chat, survey,
telemetry (including captured keystrokes and clipboard text), and URL content may contain identifying information even
when the export's structural anonymization option was enabled. The bundled examples are synthetic and anonymized.
