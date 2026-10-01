# Grade 10 cohort — synthetic full-session export (40 runs)

`experiment-full-sessions-20260930-115623.json` is a **synthetic** dataset for analysis tutorials. It is byte-for-byte what the admin dashboard's **Export full sessions (JSON)** button produces: anonymized (the UI default), in the Participants list order.

It contains 40 runs of the Grade 10 workflow *Learning with AI: Evidence, Arguments, and Geometry* (plan v13, "experiment Group A").

- **None of it is collected student data.** Every participant, essay, answer, chat and survey response is fictional.
- Every session carries `is_demo: true`.
- The file is about 87 MB. Almost all of that is 111k keystroke events.

## How it was produced

`scripts/generate_grade10_cohort_export.py` builds the dataset in these steps:

1. **Copies the database.** It copies `backend/data/webui.db` (source opened read-only) into a temporary directory. The source database is left unchanged, and its SHA-256 is checked before and after.
2. **Replays each participant against the copy.** It uses the platform's own routes and model functions with a simulated clock:
   - the consent → start → task → finish flow
   - survey, question-draft and finalize requests, and essay drafts and finalization
   - `prepare_request`/`update_request` for every AI request, prompt-budget reservations, and `Chats.*` persistence
   - warning-modal display, acknowledgement and activity reporting
   - visible-timing reports
   - extension heartbeats and telemetry batches, sent the way extension 2.2.0 (schema 4) batches them
3. **Scores the manual free-text items.** A synthetic admin scores them through the real score-override route, over two review sessions on 24–25 Sep 2026.
4. **Exports.** It calls the real `/export/sessions` route and saves the response unchanged.

The authored content lives in `scripts/fixtures/grade10_cohort40/runs-0*.json`:
- answers, rubric points, essays, chats, survey answers, timing profiles and tab switches.

To regenerate (the output filename carries the export time):

```bash
backend/venv/bin/python scripts/generate_grade10_cohort_export.py [--output-dir DIR] [--keep-database copy.db]
```

## Cohort

**Conditions.** The platform assigned conditions probabilistically, using its real HMAC assignment and a synthetic instance secret:

| Condition | Participants |
| --- | --- |
| Control | 8 |
| Tutor | 9 |
| Reminder | 11 |
| Delay | 12 |

**Timing.** The class periods were:
- 22 Sep 2026 at 13:10 UTC and 15:05 UTC
- 23 Sep 2026 at 13:10 UTC

Logins are staggered over the first 6 minutes of each period.

**Deployment settings.** The telemetry extension is required. The AI model is `synthetic-grade10-demo`. Its token usage is a character-count estimate, not a provider measurement.

**Ability and AI use.** Participants range from strong to struggling. AI use ranges from none, through selective and critical, to heavy and uncritical. Some AI replies are subtly wrong. Careful students catch the error in chat, while weaker students copy it into their answers.

## Data-quality issues planted for preprocessing (answer key)

Participant ids are the export's anonymized `participant_id` values.

| Participant | Condition | Issue | How it shows up |
| --- | --- | --- | --- |
| P-38B9DCB70D5F | Delay | Speed-runner / low effort | COMPLETED in 12.7 min. 41-word essay, one-line free texts, every survey scale answer is 3, no AI use. |
| P-C1D5C947BDB8 | Control | Consented, never started | `TASK_REQUIRED`, all tasks LOCKED, no events. Extension presence only. |
| P-7CB5853CA80F | Reminder | Dropout in biology | `IN_PROGRESS`. Question submission DRAFT with 2 of 5 answered. Tab closed, so there is a `tab_removed` event and a final `tab_snapshot`. |
| P-7ECF654F2415 | Delay | Dropout mid-essay | `IN_PROGRESS`. Essay task ACTIVE with an 87-word unfinished `essay_draft`, and no essay row. |
| P-3BE0F1CFF7C8 | Tutor | Dropout in geometry | `IN_PROGRESS`. Geometry DRAFT with 4 of 10 answered. |
| P-686D3F43E1FB | Control | Never clicked *Finish* | All tasks FINALIZED, but the state is `THANK_YOU_REQUIRED` and `completed_at` is null. |
| P-6032C4E88957 | Control | Idle outlier | 3 h 30 min session that includes a 2 h 15 min absence during geometry. Use active time, not wall time. |
| P-C96E0F259C90 | Delay | Paste-heavy | The essay and three free texts were pasted from AI replies (3,053 pasted characters, 953 keystrokes). The essay also contains an invented statistic. |
| P-CB6641F149FE | Control | Heavy uncritical use | 14 prompts. The essay and five free texts were pasted from AI replies (2,666 pasted characters), including a wrong AI area answer. |
| P-EED7E3B0F9D8 | Reminder | Telemetry missing | Completed normally, but the extension disconnected right after start. There are zero client events and a null telemetry summary; only server response events remain. |

Minor noise, spread across normal runs:

- **Essay length outside the advisory 200–300 words.**
  - P-0C4B17997AB5 wrote 173 words.
  - P-D5129A0043A8 wrote 336 words.
- **Unreviewed manual scores.** Some questions are still `AWAITING_REVIEW`, so `current_score` is null.
  - P-1CC95996A619: geometry.
  - P-45C4C959F35E: biology.
- **Unusual AI requests.**
  - One `FAILED` request (`ProviderStreamError`) followed by a regeneration with the same `prompt_number`: P-88F357702936.
  - Two `CANCELLED` requests with `navigated_away`, where the page was reloaded mid-stream: P-BDB1EE4C6543 and P-14F283031AFA.
- **A late telemetry upload.** P-71560AA48219 was offline for about 230 s during the essay. The events were uploaded late, so `created_at − event_time` reaches about 229 s.
- **Survey and AI-use gaps.**
  - The optional post-survey question ("waiting interrupted…") is left unanswered in several runs.
  - One participant never used AI: P-C542848B89D2.
- **Client clock skew.** Each browser clock is off by −1.8 to +2.4 s. As on the real platform, `client_*_visible_at` and telemetry `event_time` use the client clock, while server timestamps do not. So a client-visible time can appear to come before the matching server emit time.

## Load it

```python
from research_toolbox import load_export

data = load_export("examples/grade10_cohort/experiment-full-sessions-20260930-115623.json")
overview = data.session_overview()
```
