from __future__ import annotations

import pandas as pd

from research_toolbox import load_export


def test_session_overview_and_task_progress(fixture_path):
    data = load_export(fixture_path)
    overview = data.session_overview()
    progress = data.task_progress()

    assert overview["session_id"].tolist() == ["session-complete", "session-incomplete"]
    assert overview.loc[0, "group_name"] == "Synthetic research group"
    assert overview.loc[0, "condition_name"] == "Delayed warning"
    assert progress.loc[progress["task_id"] == "task-essay", "elapsed_seconds"].item() == 30.0
    assert not bool(progress.loc[progress["task_id"] == "task-incomplete", "is_complete"].item())


def test_descriptive_summaries(fixture_path):
    data = load_export(fixture_path)

    conditions = data.condition_summary(["total_tokens"])
    assert conditions.loc[0, "session_count"] == 1
    assert conditions.loc[0, "total_tokens_mean"] == 50

    scores = data.question_score_summary()
    assert scores["response_count"].sum() == 2
    assert scores["grading_attempt_count"].sum() == 1
    assert scores["score_override_count"].sum() == 1

    surveys = data.survey_response_summary()
    assert surveys["mean_scale_answer"].dropna().item() == 4
    assert surveys["selection_count"].sum() == 1

    essays = data.essay_summary()
    assert essays.loc[0, "calculated_word_count"] == 8

    chat = data.chat_usage_summary()
    assert chat.loc[0, "user_messages"] == 1
    assert chat.loc[0, "assistant_messages"] == 1
    assert chat.loc[0, "input_tokens"] == 20
    assert chat.loc[0, "output_tokens"] == 30


def test_ordered_timelines(fixture_path):
    data = load_export(fixture_path)
    telemetry = data.telemetry_timeline("session-complete")
    timeline = data.session_timeline("session-complete")

    assert telemetry["telemetry_event_id"].tolist() == ["telemetry-1", "telemetry-2"]
    assert timeline["occurred_at_dt"].dropna().is_monotonic_increasing
    assert {"message", "telemetry", "llm_request", "task_started", "task_completed", "warning_displayed"}.issubset(
        set(timeline["event_kind"])
    )
    assert isinstance(timeline.loc[0, "details"], dict)
    assert pd.api.types.is_datetime64_any_dtype(timeline["occurred_at_dt"])
