from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from .dataset import ExperimentDataset


def _available(frame: pd.DataFrame, columns: Iterable[str]) -> list[str]:
    return [column for column in columns if column in frame.columns]


def session_overview(dataset: ExperimentDataset) -> pd.DataFrame:
    sessions = dataset.sessions.copy()
    summaries = dataset.session_summaries.copy()
    if not summaries.empty:
        drop = [column for column in ("_session_order", "_record_order") if column in summaries]
        sessions = sessions.merge(summaries.drop(columns=drop), on="session_id", how="left", suffixes=("", "_summary"))

    groups = dataset.groups
    if not groups.empty and "group_id" in sessions:
        group_columns = _available(groups, ["group_id", "name"])
        if "name" in group_columns:
            group_names = groups[group_columns].rename(columns={"name": "_definition_group_name"})
            sessions = sessions.merge(group_names, on="group_id", how="left")
            if "group_name" in sessions:
                sessions["group_name"] = sessions["group_name"].fillna(sessions["_definition_group_name"])
            else:
                sessions["group_name"] = sessions["_definition_group_name"]
            sessions = sessions.drop(columns="_definition_group_name")
    conditions = dataset.conditions
    if not conditions.empty and "condition_id" in sessions:
        condition_columns = _available(conditions, ["condition_id", "name", "label"])
        rename = {name: f"condition_{name}" for name in ("name", "label") if name in condition_columns}
        condition_values = conditions[condition_columns].rename(columns=rename)
        duplicate_labels = [column for column in rename.values() if column in sessions]
        if duplicate_labels:
            condition_values = condition_values.rename(
                columns={column: f"_definition_{column}" for column in duplicate_labels}
            )
        sessions = sessions.merge(condition_values, on="condition_id", how="left")
        for column in duplicate_labels:
            definition_column = f"_definition_{column}"
            sessions[column] = sessions[column].fillna(sessions[definition_column])
            sessions = sessions.drop(columns=definition_column)
    return sessions.sort_values(["_session_order", "session_id"], kind="stable").reset_index(drop=True)


def task_progress(dataset: ExperimentDataset) -> pd.DataFrame:
    tasks = dataset.session_tasks.copy()
    if tasks.empty:
        return tasks
    status = tasks["status"].astype("string") if "status" in tasks else pd.Series(pd.NA, index=tasks.index)
    tasks["is_complete"] = status.isin(["COMPLETED", "FINALIZED", "SKIPPED"])
    if {"started_at_dt", "completed_at_dt"}.issubset(tasks.columns):
        tasks["elapsed_seconds"] = (tasks["completed_at_dt"] - tasks["started_at_dt"]).dt.total_seconds()
    return tasks.sort_values(["_session_order", "_parent_order", "_record_order"], kind="stable").reset_index(drop=True)


def condition_summary(
    dataset: ExperimentDataset,
    metrics: Iterable[str] | None = None,
) -> pd.DataFrame:
    overview = session_overview(dataset)
    if "condition_id" not in overview or overview.empty:
        return pd.DataFrame(columns=["condition_id", "session_count"])
    default_metrics = [
        "session_duration",
        "writing_duration",
        "total_tokens",
        "prompts",
        "responses",
        "essay_word_count",
        "total_keystrokes",
        "total_time_away_ms",
    ]
    selected = _available(overview, metrics or default_metrics)
    grouped = overview.groupby("condition_id", dropna=False, observed=True)
    result = grouped.size().rename("session_count").to_frame()
    for metric in selected:
        numeric = pd.to_numeric(overview[metric], errors="coerce")
        result[f"{metric}_mean"] = numeric.groupby(overview["condition_id"], dropna=False).mean()
        result[f"{metric}_median"] = numeric.groupby(overview["condition_id"], dropna=False).median()
    return result.reset_index()


def question_score_summary(dataset: ExperimentDataset) -> pd.DataFrame:
    responses = dataset.question_responses.copy()
    if responses.empty:
        return pd.DataFrame(columns=["question_id", "response_count", "answered_count", "mean_effective_score"])
    attempts = dataset.grading_attempts.groupby("response_id").size().rename("grading_attempt_count")
    overrides = dataset.score_overrides.groupby("response_id").size().rename("score_override_count")
    responses = responses.merge(attempts, on="response_id", how="left").merge(overrides, on="response_id", how="left")
    responses[["grading_attempt_count", "score_override_count"]] = responses[
        ["grading_attempt_count", "score_override_count"]
    ].fillna(0)
    group = responses.groupby("question_id", dropna=False, observed=True)
    result = group.size().rename("response_count").to_frame()
    if "is_answered" in responses:
        result["answered_count"] = responses["is_answered"].astype("boolean").groupby(responses["question_id"]).sum()
    if "effective_score" in responses:
        scores = pd.to_numeric(responses["effective_score"], errors="coerce")
        result["mean_effective_score"] = scores.groupby(responses["question_id"], dropna=False).mean()
        result["total_effective_score"] = scores.groupby(responses["question_id"], dropna=False).sum(min_count=1)
    result["grading_attempt_count"] = group["grading_attempt_count"].sum()
    result["score_override_count"] = group["score_override_count"].sum()
    return result.reset_index()


def survey_response_summary(dataset: ExperimentDataset) -> pd.DataFrame:
    responses = dataset.survey_responses.copy()
    selections = dataset.survey_response_choices.copy()
    choices = dataset.survey_choices.copy()
    base_columns = [
        "survey_question_id",
        "survey_choice_id",
        "choice_text",
        "selection_count",
        "answered_count",
        "mean_scale_answer",
    ]
    if responses.empty:
        return pd.DataFrame(columns=base_columns)

    answered = responses.groupby("survey_question_id", dropna=False).size().rename("answered_count")
    scale = None
    if "scale_answer" in responses:
        scale = (
            pd.to_numeric(responses["scale_answer"], errors="coerce")
            .groupby(responses["survey_question_id"], dropna=False)
            .mean()
            .rename("mean_scale_answer")
        )
    if selections.empty:
        result = answered.to_frame()
        if scale is not None:
            result = result.join(scale)
        result["survey_choice_id"] = pd.NA
        result["choice_text"] = pd.NA
        result["selection_count"] = 0
        return result.reset_index().reindex(columns=base_columns)

    counts = (
        selections.groupby(["survey_question_id", "survey_choice_id"], dropna=False)
        .size()
        .rename("selection_count")
        .reset_index()
    )
    question_rows = responses[["survey_question_id"]].drop_duplicates()
    counts = question_rows.merge(counts, on="survey_question_id", how="left")
    counts["selection_count"] = counts["selection_count"].fillna(0).astype("Int64")
    if not choices.empty and "text" in choices:
        counts = counts.merge(
            choices[["survey_choice_id", "text"]].rename(columns={"text": "choice_text"}),
            on="survey_choice_id",
            how="left",
        )
    else:
        counts["choice_text"] = pd.NA
    counts = counts.merge(answered, on="survey_question_id", how="left")
    if scale is not None:
        counts = counts.merge(scale, on="survey_question_id", how="left")
    else:
        counts["mean_scale_answer"] = pd.NA
    return counts.reindex(columns=base_columns)


def essay_summary(dataset: ExperimentDataset) -> pd.DataFrame:
    essays = dataset.essays.copy()
    if essays.empty:
        return essays
    if "content" in essays:
        calculated_words = essays["content"].map(lambda value: len(value.split()) if isinstance(value, str) else pd.NA)
        calculated_characters = essays["content"].map(lambda value: len(value) if isinstance(value, str) else pd.NA)
        essays["calculated_word_count"] = pd.array(calculated_words, dtype="Int64")
        essays["calculated_character_count"] = pd.array(calculated_characters, dtype="Int64")
    return essays.sort_values(["_session_order", "_record_order"], kind="stable").reset_index(drop=True)


def _usage_value(value: Any, keys: Iterable[str]) -> int | float:
    if not isinstance(value, dict):
        return 0
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return candidate
    return 0


def chat_usage_summary(dataset: ExperimentDataset) -> pd.DataFrame:
    messages = dataset.messages.copy()
    columns = ["session_id", "message_count", "user_messages", "assistant_messages", "input_tokens", "output_tokens"]
    if messages.empty:
        return pd.DataFrame(columns=columns)
    roles = messages.get("role", pd.Series(pd.NA, index=messages.index)).astype("string")
    messages["_user_message"] = roles.eq("user").astype("Int64")
    messages["_assistant_message"] = roles.eq("assistant").astype("Int64")
    usage = messages.get("usage", pd.Series(None, index=messages.index))
    messages["_input_tokens"] = usage.map(lambda value: _usage_value(value, ("input_tokens", "prompt_tokens")))
    messages["_output_tokens"] = usage.map(lambda value: _usage_value(value, ("output_tokens", "completion_tokens")))
    return (
        messages.groupby("session_id", dropna=False)
        .agg(
            message_count=("message_id", "size"),
            user_messages=("_user_message", "sum"),
            assistant_messages=("_assistant_message", "sum"),
            input_tokens=("_input_tokens", "sum"),
            output_tokens=("_output_tokens", "sum"),
        )
        .reset_index()
        .reindex(columns=columns)
    )


def telemetry_timeline(dataset: ExperimentDataset, session_id: str | None = None) -> pd.DataFrame:
    events = dataset.telemetry_events.copy()
    if session_id is not None:
        events = events.loc[events["session_id"] == session_id]
    order = _available(events, ["session_id", "event_time_dt", "_parent_order", "_record_order"])
    return events.sort_values(order, kind="stable").reset_index(drop=True) if order else events


def session_timeline(dataset: ExperimentDataset, session_id: str | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def append_events(frame: pd.DataFrame, kind: str, id_column: str, timestamp_column: str) -> None:
        if timestamp_column not in frame:
            return
        for record in frame.to_dict("records"):
            if session_id is not None and record.get("session_id") != session_id:
                continue
            occurred_at = record.get(timestamp_column)
            if occurred_at is None or occurred_at is pd.NA:
                continue
            dt_column = f"{timestamp_column}_dt"
            rows.append(
                {
                    "session_id": record.get("session_id"),
                    "event_kind": kind,
                    "source_id": record.get(id_column),
                    "occurred_at": occurred_at,
                    "occurred_at_dt": record.get(dt_column, pd.NaT),
                    "details": record,
                }
            )

    append_events(dataset.session_tasks, "task_started", "task_id", "started_at")
    append_events(dataset.session_tasks, "task_completed", "task_id", "completed_at")
    append_events(dataset.messages, "message", "message_id", "created_at")
    append_events(dataset.telemetry_events, "telemetry", "telemetry_event_id", "event_time")
    append_events(dataset.llm_requests, "llm_request", "llm_request_id", "request_at")
    append_events(dataset.warning_states, "warning_displayed", "warning_state_id", "displayed_at")
    result = pd.DataFrame(
        rows,
        columns=["session_id", "event_kind", "source_id", "occurred_at", "occurred_at_dt", "details"],
    )
    if result.empty:
        return result
    return result.sort_values(
        ["session_id", "occurred_at_dt", "event_kind"], kind="stable", na_position="last"
    ).reset_index(drop=True)
