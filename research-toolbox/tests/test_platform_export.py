"""Contract tests against a platform-produced export (current backend schema).

``fixtures/platform_export_sample.json`` holds five sessions copied unchanged from a real
``/export/sessions`` response of the synthetic Grade 10 cohort, except that keystroke events are
subsampled (at most 25 per task, field and question) to keep the file small.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from research_toolbox import load_export

SAMPLE = Path(__file__).parent / "fixtures" / "platform_export_sample.json"
REMINDER = "P-374FEC3F74B5"  # completed, warning modal, focus absences
DELAY_RETRY = "P-88F357702936"  # completed, failed request then regeneration
TUTOR_DROPOUT = "P-3BE0F1CFF7C8"  # abandoned in geometry with a draft submission
NEVER_STARTED = "P-C1D5C947BDB8"  # consented, never started
PASTE_HEAVY = "P-C96E0F259C90"  # essay and answers pasted from AI replies


@pytest.fixture(scope="module")
def data():
    return load_export(SAMPLE, strict=True)


def _session(data, participant_id):
    context = data.session_context()
    return context.loc[context["participant_id"] == participant_id, "session_id"].item()


def test_platform_export_loads_strictly_with_all_sections(data):
    assert data.validation_warnings == []
    assert len(data.sessions) == 5
    assert data.sessions["is_demo"].all()
    assert set(data.session_workflows["workflow_origin"]) == {"inferred"}
    assert len(data.conditions) == 4 and data.conditions["is_control"].sum() == 1
    assert {"source_step_key", "llm_prompt_budget"}.issubset(data.plan_items.columns)
    assert {"source_condition_key", "allocation_percent"}.issubset(data.conditions.columns)


def test_derived_summary_milestones_are_converted_from_seconds(data):
    summaries = data.session_summaries.set_index("participant_id")
    assert summaries.loc[REMINDER, "writing_started_at_dt"] == pd.Timestamp("2026-09-22 13:32:23+00:00")
    for column in ("session_start_time", "pre_survey_submitted_at", "post_survey_submitted_at", "task_submitted_at"):
        assert pd.api.types.is_datetime64_any_dtype(data.session_summaries[f"{column}_dt"])
    assert pd.isna(summaries.loc[NEVER_STARTED, "writing_started_at_dt"])
    # Raw records stay in nanoseconds.
    assert data.sessions["created_at"].iloc[0] > 10**18


def test_messages_are_attributed_to_tasks_including_unstamped_replies(data):
    messages = data.messages
    assert messages["task_id"].notna().all()
    replies = messages.loc[messages["role"] == "assistant"]
    # The platform stamps only the first reply of a shared chat; later ones are linked by LLM request.
    assert set(replies["task_id_source"]) == {"message", "llm_request"}
    assert (messages["history_message_id"] != messages["message_id"]).all()
    per_task = data.chat_usage_summary(by=("session_id", "task_id"))
    assert per_task["assistant_messages"].sum() == len(replies)
    exported = data.task_usage["responses"].sum()
    assert exported < len(replies)


def test_question_answers_render_choices_blanks_free_text_and_reviews(data):
    answers = data.question_answers()
    assert len(answers) == len(data.question_responses)
    reminder = answers.loc[answers["participant_id"] == REMINDER].set_index(
        answers.loc[answers["participant_id"] == REMINDER, "question_title"].str.split(" ").str[0]
    )
    assert reminder.loc["B3", "answer_text"] == "percent=8; direction=into"
    assert reminder.loc["B3", "blank_answers"] == {"percent": "8", "direction": "into"}
    assert reminder.loc["B1", "selected_correct"] == 1 and reminder.loc["B1", "selected_incorrect"] == 0
    assert reminder.loc["B1", "answer_text"].startswith("Sucrose concentration")
    assert reminder.loc["B4", "review_score"] == reminder.loc["B4", "effective_score"]
    assert reminder.loc["B4", "review_note"].startswith("Synthetic rubric review")
    assert reminder.loc["B4", "condition_name"].startswith("Reminder")
    dropout = answers.loc[(answers["participant_id"] == TUTOR_DROPOUT) & (answers["submission_status"] == "DRAFT")]
    assert len(dropout) == 10 and dropout["is_answered"].sum() == 4
    assert dropout["effective_score"].isna().all()


def test_survey_answers_have_prompts_and_typed_values(data):
    answers = data.survey_answers()
    assert answers["prompt"].notna().all()
    scales = answers.loc[answers["question_type"] == "SCALE", "value"]
    assert scales.map(lambda value: isinstance(value, int) and 1 <= value <= 5).all()
    choices = answers.loc[answers["question_type"].isin(["SINGLE_CHOICE", "MULTIPLE_SELECT"]), "value"]
    assert choices.map(lambda value: isinstance(value, list) and all(isinstance(text, str) for text in value)).all()
    assert set(answers["source"]) == {"task"}


def test_essay_texts_and_drafts(data):
    essays = data.essay_texts()
    assert (essays["status"] == "submitted").sum() == 4
    assert (essays["word_count"] == essays["calculated_word_count"]).all()
    assert len(data.essay_texts(include_drafts=False)) == 4


def test_chat_transcript_links_requests_failures_and_regenerations(data):
    transcript = data.chat_transcript(_session(data, DELAY_RETRY))
    assistant = transcript.loc[transcript["role"] == "assistant"]
    failed = assistant.loc[assistant["request_status"] == "FAILED"]
    assert len(failed) == 1 and "503" in failed["error_text"].item()
    regenerated = assistant.loc[assistant["prompt_number"] == failed["prompt_number"].item()]
    assert list(regenerated["request_status"]) == ["FAILED", "COMPLETED"]
    assert transcript["text"].notna().all()
    assert transcript["task_title"].notna().all()


def test_llm_request_timing_components(data):
    timing = data.llm_request_timing()
    delayed = timing.loc[(timing["timing_mode"] == "DELAYED") & (timing["status"] == "COMPLETED")]
    assert (delayed["artificial_delay_ms"] >= 2990).all()
    normal = timing.loc[(timing["timing_mode"] == "NORMAL") & (timing["status"] == "COMPLETED")]
    assert normal["artificial_delay_ms"].isna().all()
    assert (normal["server_first_emit_ms"] >= normal["time_to_first_token_ms"]).all()
    assert timing["task_title"].notna().all()


def test_flat_telemetry_keystrokes_clipboard_and_tabs(data):
    keys = data.keystrokes()
    assert keys["key_value"].notna().all()
    assert {"key_class", "inter_key_interval_ms", "hold_duration_ms", "modifier_shift"}.issubset(keys.columns)
    assert keys["schema_version"].eq(4).all()
    clipboard = data.clipboard_events()
    assert list(clipboard["event_type"].unique()) == ["paste"]
    assert (clipboard["text_value"].str.len() == clipboard["text_length"]).all()
    assert set(clipboard["participant_id"]) == {PASTE_HEAVY}
    tabs = data.tab_activity()
    assert {"tab_snapshot", "tab_activated", "window_focus_changed", "tab_removed"}.issubset(set(tabs["event_type"]))
    assert tabs.loc[tabs["event_type"] == "tab_snapshot", "tab_url"].notna().all()
    runtime = data.telemetry_events_flat(["warning_modal_acknowledged"])
    assert runtime["display_reason"].eq("beginning").all()


def test_focus_periods_and_keystroke_summary(data):
    periods = data.focus_periods()
    assert periods["returned"].all()
    assert periods["task_title"].notna().all()
    destinations = set(periods["destination_title"].dropna())
    assert {"Instagram", "Desmos | Graphing Calculator"}.issubset(destinations)
    summaries = data.telemetry_summaries.set_index("session_id")["total_time_away_ms"]
    measured = periods.groupby("session_id")["away_ms"].sum()
    assert all(measured[session] == summaries[session] for session in measured.index)
    typing = data.keystroke_summary()
    assert set(typing["field_context"]) == {"chat", "essay", "question"}
    assert (typing["keystrokes"] >= typing["backspaces"]).all()


def test_session_activity_describes_completion_and_coverage(data):
    activity = data.session_activity().set_index("participant_id")
    assert activity.loc[REMINDER, "is_completed"] and activity.loc[REMINDER, "tasks_finished"] == 6
    assert activity.loc[TUTOR_DROPOUT, "state"] == "IN_PROGRESS" and activity.loc[TUTOR_DROPOUT, "tasks_finished"] == 4
    assert activity.loc[NEVER_STARTED, "state"] == "TASK_REQUIRED"
    assert 0 < activity.loc[NEVER_STARTED, "wall_seconds"] < 600
    assert not activity.loc[NEVER_STARTED, "has_telemetry_summary"]
    assert activity.loc[NEVER_STARTED, "extension_connected"]
    assert (activity["active_seconds"] <= activity["wall_seconds"]).all()


def test_summaries_carry_readable_labels(data):
    conditions = data.condition_summary()
    assert conditions["condition_name"].notna().all() and "condition_key" in conditions
    scores = data.question_score_summary()
    assert scores["question_title"].notna().all()
    assert scores["mean_score_fraction"].between(0, 1).all()
    assert data.survey_response_summary()["prompt"].notna().all()


def test_materializes_platform_tables(data, tmp_path):
    manifest = data.materialize(tmp_path, format="parquet", tables=["messages", "telemetry_events", "llm_requests"])
    assert manifest["tables"]["messages"]["rows"] == len(data.messages)
    assert "payload_json" in manifest["tables"]["telemetry_events"]["json_encoded_columns"]
    assert pd.read_parquet(tmp_path / "messages.parquet")["task_id_source"].notna().all()
