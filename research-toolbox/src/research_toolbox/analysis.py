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

    workflows = dataset.session_workflows
    if not workflows.empty:
        columns = [column for column in workflows if not column.startswith("_")]
        sessions = sessions.merge(workflows[columns], on="session_id", how="left", suffixes=("", "_workflow"))

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
    result = result.reset_index()
    conditions = dataset.conditions
    if not conditions.empty:
        labels = conditions[_available(conditions, ["condition_id", "name", "source_condition_key", "is_control"])]
        labels = labels.rename(columns={"name": "condition_name", "source_condition_key": "condition_key"})
        result = result.merge(labels, on="condition_id", how="left")
        leading = _available(result, ["condition_id", "condition_name", "condition_key", "is_control", "session_count"])
        result = result[leading + [column for column in result if column not in leading]]
    return result


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
    if "grading_status" in responses:
        pending = responses["grading_status"].astype("string").isin(["AWAITING_REVIEW", "PENDING", "RUNNING"])
        result["awaiting_review_count"] = pending.groupby(responses["question_id"]).sum()
    result = result.reset_index()
    questions = dataset.questions
    if not questions.empty:
        labels = questions[_available(questions, ["question_id", "title", "question_type", "position", "max_score"])]
        result = result.merge(labels.rename(columns={"title": "question_title", "position": "question_position"}),
                              on="question_id", how="left")
        if {"mean_effective_score", "max_score"}.issubset(result.columns):
            maximum = pd.to_numeric(result["max_score"], errors="coerce")
            result["mean_score_fraction"] = (pd.to_numeric(result["mean_effective_score"], errors="coerce") / maximum).where(maximum > 0)
    return result


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
    questions = dataset.survey_questions
    extra = []
    if not questions.empty and "prompt" in questions:
        counts = counts.merge(
            questions[_available(questions, ["survey_question_id", "prompt", "question_type"])],
            on="survey_question_id",
            how="left",
        )
        extra = _available(counts, ["prompt", "question_type"])
    return counts.reindex(columns=base_columns + extra)


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


def chat_usage_summary(dataset: ExperimentDataset, by: Iterable[str] = ("session_id",)) -> pd.DataFrame:
    """Message counts and token usage computed from exported message rows.

    Pass ``by=("session_id", "task_id")`` for per-task usage based on the toolbox's resolved
    message attribution (see ``messages.task_id_source``). Unlike the export's ``usage.by_task``,
    this also attributes assistant replies that the platform stored without a task ID.
    """
    messages = dataset.messages.copy()
    group_columns = list(by)
    columns = [*group_columns, "message_count", "user_messages", "assistant_messages", "input_tokens", "output_tokens"]
    if messages.empty:
        return pd.DataFrame(columns=columns)
    roles = messages.get("role", pd.Series(pd.NA, index=messages.index)).astype("string")
    messages["_user_message"] = roles.eq("user").astype("Int64")
    messages["_assistant_message"] = roles.eq("assistant").astype("Int64")
    usage = messages.get("usage", pd.Series(None, index=messages.index))
    messages["_input_tokens"] = usage.map(lambda value: _usage_value(value, ("input_tokens", "prompt_tokens")))
    messages["_output_tokens"] = usage.map(lambda value: _usage_value(value, ("output_tokens", "completion_tokens")))
    return (
        messages.groupby(group_columns, dropna=False)
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


# ---------------------------------------------------------------------------
# Retrieval helpers: tidy, joined views of the export's records.
# ---------------------------------------------------------------------------

TAB_EVENT_TYPES = (
    "tab_snapshot",
    "tab_created",
    "tab_updated",
    "tab_activated",
    "tab_highlighted",
    "tab_moved",
    "tab_attached",
    "tab_detached",
    "tab_replaced",
    "tab_removed",
    "window_focus_changed",
    "telemetry_loss",
)
CLIPBOARD_EVENT_TYPES = ("copy", "cut", "paste")
CONTEXT_COLUMNS = [
    "session_id",
    "participant_id",
    "condition_id",
    "condition_name",
    "condition_key",
    "is_control",
    "state",
    "is_demo",
]


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _utc(values: pd.Series, unit: str) -> pd.Series:
    from .normalize import _as_utc

    return _as_utc(values, unit)


def _ms_between(frame: pd.DataFrame, start: str, end: str) -> pd.Series:
    if start not in frame or end not in frame:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    begin = pd.to_numeric(frame[start], errors="coerce").astype("Float64")
    finish = pd.to_numeric(frame[end], errors="coerce").astype("Float64")
    return ((finish - begin) / 1_000_000).round(3)


def session_context(dataset: ExperimentDataset) -> pd.DataFrame:
    """One row per session with participant, condition labels, state and demo flag."""
    sessions = dataset.sessions
    context = sessions[_available(sessions, ["session_id", "_session_order", "state", "condition_id", "is_demo"])].copy()
    summaries = dataset.session_summaries
    if "participant_id" in summaries and not summaries.empty:
        context = context.merge(summaries[["session_id", "participant_id"]], on="session_id", how="left")
    conditions = dataset.conditions
    if not conditions.empty and "condition_id" in context:
        labels = conditions[_available(conditions, ["condition_id", "name", "source_condition_key", "is_control"])]
        labels = labels.rename(columns={"name": "condition_name", "source_condition_key": "condition_key"})
        context = context.merge(labels, on="condition_id", how="left")
    context = context.reindex(columns=["_session_order", *CONTEXT_COLUMNS])
    return context.sort_values("_session_order", kind="stable").reset_index(drop=True)


def _with_context(frame: pd.DataFrame, dataset: ExperimentDataset) -> pd.DataFrame:
    context = session_context(dataset).drop(columns="_session_order")
    duplicate = [column for column in context if column in frame and column != "session_id"]
    merged = frame.drop(columns=duplicate).merge(context, on="session_id", how="left")
    leading = [column for column in CONTEXT_COLUMNS if column in merged]
    return merged[leading + [column for column in merged if column not in leading]]


def _task_labels(dataset: ExperimentDataset) -> pd.DataFrame:
    tasks = dataset.session_tasks
    labels = tasks[_available(tasks, ["task_id", "title", "position", "task_type", "status"])]
    return labels.rename(
        columns={"title": "task_title", "position": "task_position", "task_type": "task_type", "status": "task_status"}
    )


def question_answers(dataset: ExperimentDataset) -> pd.DataFrame:
    """One row per question response with readable answers, scores, review notes and context.

    ``answer_text`` renders free text, the selected choice texts, or ``blank=value`` pairs.
    Selected choices and blank answers are also kept as lists/dicts. Unanswered draft responses
    (abandoned tasks) are included with ``submission_status == 'DRAFT'``.
    """
    responses = dataset.question_responses
    columns = [*CONTEXT_COLUMNS, "task_id", "question_id", "question_title", "answer_text", "effective_score"]
    if responses.empty:
        return pd.DataFrame(columns=columns)
    result = responses.drop(columns=[c for c in ("_parent_order",) if c in responses]).copy()
    questions = dataset.questions[
        _available(dataset.questions, ["question_id", "title", "description", "question_type", "position", "max_score", "grading_mode"])
    ].rename(columns={"title": "question_title", "description": "question_text", "position": "question_position"})
    result = result.merge(questions, on="question_id", how="left")
    result = result.merge(_task_labels(dataset), on="task_id", how="left")
    submissions = dataset.question_submissions
    if not submissions.empty:
        result = result.merge(
            submissions[_available(submissions, ["submission_id", "status", "grading_status", "submitted_at"])].rename(
                columns={
                    "status": "submission_status",
                    "grading_status": "submission_grading_status",
                    "submitted_at": "submitted_at",
                }
            ),
            on="submission_id",
            how="left",
        )
    choices = dataset.question_choices
    choice_text = dict(zip(choices["choice_id"], choices.get("text", pd.Series(dtype="object")))) if not choices.empty else {}
    choice_position = dict(zip(choices["choice_id"], choices.get("position", pd.Series(dtype="object")))) if not choices.empty else {}
    choice_correct = dict(zip(choices["choice_id"], choices.get("is_correct", pd.Series(dtype="object")))) if not choices.empty else {}
    selected: dict[Any, list[Any]] = {}
    for row in dataset.question_response_choices.to_dict("records"):
        selected.setdefault(row["response_id"], []).append(row["choice_id"])
    blanks = dataset.question_blanks
    blank_key = dict(zip(blanks["blank_id"], blanks.get("blank_key", pd.Series(dtype="object")))) if not blanks.empty else {}
    blank_position = dict(zip(blanks["blank_id"], blanks.get("position", pd.Series(dtype="object")))) if not blanks.empty else {}
    blank_values: dict[Any, list[tuple[Any, Any, Any]]] = {}
    for row in dataset.question_blank_answers.to_dict("records"):
        blank_values.setdefault(row["response_id"], []).append(
            (blank_position.get(row["blank_id"], 0), blank_key.get(row["blank_id"], row["blank_id"]), row.get("answer"))
        )

    def ordered_choices(response_id: Any) -> list[Any]:
        return sorted(selected.get(response_id, []), key=lambda choice: (choice_position.get(choice) or 0, str(choice)))

    result["selected_choice_ids"] = [ordered_choices(response_id) for response_id in result["response_id"]]
    result["selected_choices"] = [[choice_text.get(choice) for choice in ids] for ids in result["selected_choice_ids"]]
    result["selected_correct"] = [sum(bool(choice_correct.get(c)) for c in ids) for ids in result["selected_choice_ids"]]
    result["selected_incorrect"] = [
        sum(not bool(choice_correct.get(c)) for c in ids) for ids in result["selected_choice_ids"]
    ]
    result["blank_answers"] = [
        {key: value for _, key, value in sorted(blank_values.get(response_id, []), key=lambda item: (item[0] or 0))}
        for response_id in result["response_id"]
    ]
    texts = []
    for row in result.to_dict("records"):
        if not _missing(row.get("free_text_answer")):
            texts.append(row["free_text_answer"])
        elif row["selected_choices"]:
            texts.append(" | ".join(str(text) for text in row["selected_choices"]))
        elif row["blank_answers"]:
            texts.append("; ".join(f"{key}={value}" for key, value in row["blank_answers"].items()))
        else:
            texts.append(pd.NA)
    result["answer_text"] = pd.array(texts, dtype="string")
    score = pd.to_numeric(result.get("effective_score"), errors="coerce")
    maximum = pd.to_numeric(result.get("max_score"), errors="coerce")
    result["score_fraction"] = (score / maximum).where(maximum > 0)
    overrides = dataset.score_overrides
    if not overrides.empty:
        latest = overrides.sort_values(["response_id", "created_at"], kind="stable").groupby("response_id").tail(1)
        review = latest[_available(latest, ["response_id", "new_score", "note", "created_at", "admin_id"])].rename(
            columns={"new_score": "review_score", "note": "review_note", "created_at": "reviewed_at", "admin_id": "reviewer_id"}
        )
        result = result.merge(review, on="response_id", how="left")
        result["reviewed_at_dt"] = _utc(result["reviewed_at"], "ns")
    if "submitted_at" in result:
        result["submitted_at_dt"] = _utc(result["submitted_at"], "ns")
    result = _with_context(result, dataset)
    order = _available(result, ["_session_order", "task_position", "question_position"])
    return result.sort_values(order, kind="stable").reset_index(drop=True)


def survey_answers(dataset: ExperimentDataset) -> pd.DataFrame:
    """One row per survey answer: prompt, type, readable value, submission status and context.

    ``value`` is the selected choice texts (list), the scale integer or the text answer.
    Legacy essay-study ``pre_survey``/``post_survey`` JSON is included with ``source`` set to
    ``legacy_pre_survey``/``legacy_post_survey`` and the form field name as ``question_key``.
    """
    rows: list[dict[str, Any]] = []
    responses = dataset.survey_responses
    if not responses.empty:
        questions = dataset.survey_questions
        question_map = {row["survey_question_id"]: row for row in questions.to_dict("records")}
        survey_titles = dict(zip(dataset.survey_tasks.get("survey_task_id", []), dataset.survey_tasks.get("title", [])))
        choices = dataset.survey_choices
        choice_text = dict(zip(choices.get("survey_choice_id", []), choices.get("text", [])))
        choice_position = dict(zip(choices.get("survey_choice_id", []), choices.get("position", [])))
        selected: dict[Any, list[Any]] = {}
        for row in dataset.survey_response_choices.to_dict("records"):
            selected.setdefault(row["response_id"], []).append(row["survey_choice_id"])
        submissions = {row["submission_id"]: row for row in dataset.survey_submissions.to_dict("records")}
        tasks = {row["task_id"]: row for row in dataset.session_tasks.to_dict("records")}
        for response in responses.to_dict("records"):
            question = question_map.get(response["survey_question_id"], {})
            submission = submissions.get(response.get("submission_id"), {})
            task = tasks.get(response.get("task_id"), {})
            choice_ids = sorted(selected.get(response["response_id"], []), key=lambda c: (choice_position.get(c) or 0))
            question_type = question.get("question_type")
            if question_type in ("SINGLE_CHOICE", "MULTIPLE_SELECT"):
                value: Any = [choice_text.get(choice) for choice in choice_ids]
            elif question_type == "SCALE":
                value = response.get("scale_answer")
            else:
                value = response.get("text_answer")
            rows.append(
                {
                    "session_id": response["session_id"],
                    "source": "task",
                    "task_id": response.get("task_id"),
                    "task_title": task.get("title"),
                    "task_position": task.get("position"),
                    "survey_task_id": question.get("survey_task_id"),
                    "survey_title": survey_titles.get(question.get("survey_task_id")),
                    "submission_id": response.get("submission_id"),
                    "submission_status": submission.get("status"),
                    "submitted_at": submission.get("submitted_at"),
                    "response_id": response["response_id"],
                    "survey_question_id": response["survey_question_id"],
                    "question_key": None,
                    "question_position": question.get("position"),
                    "prompt": question.get("prompt"),
                    "question_type": question_type,
                    "required": question.get("required"),
                    "scale_low_label": question.get("scale_low_label"),
                    "scale_high_label": question.get("scale_high_label"),
                    "is_answered": response.get("is_answered"),
                    "value": value,
                    "scale_answer": response.get("scale_answer"),
                    "text_answer": response.get("text_answer"),
                    "selected_choice_ids": choice_ids,
                    "_session_order": response.get("_session_order"),
                }
            )
    for session in dataset.sessions.to_dict("records"):
        for source in ("pre_survey", "post_survey"):
            answers = session.get(source)
            if isinstance(answers, dict):
                for position, (key, value) in enumerate(answers.items()):
                    rows.append(
                        {
                            "session_id": session["session_id"],
                            "source": f"legacy_{source}",
                            "submitted_at": session.get(f"{source}_submitted_at"),
                            "question_key": key,
                            "question_position": position,
                            "prompt": key,
                            "is_answered": not _missing(value) and value != "",
                            "value": value,
                            "_session_order": session.get("_session_order"),
                        }
                    )
    columns = [
        "session_id", "source", "task_id", "task_title", "task_position", "survey_task_id", "survey_title",
        "submission_id", "submission_status", "submitted_at", "response_id", "survey_question_id", "question_key",
        "question_position", "prompt", "question_type", "required", "scale_low_label", "scale_high_label",
        "is_answered", "value", "scale_answer", "text_answer", "selected_choice_ids", "_session_order",
    ]
    result = pd.DataFrame(rows, columns=columns)
    if result.empty:
        return _with_context(result, dataset)
    result["submitted_at_dt"] = _utc(result["submitted_at"], "ns")
    result = _with_context(result, dataset)
    return result.sort_values(
        ["_session_order", "task_position", "question_position"], kind="stable", na_position="last"
    ).reset_index(drop=True)


def essay_texts(dataset: ExperimentDataset, include_drafts: bool = True) -> pd.DataFrame:
    """Submitted essays plus (optionally) unsubmitted drafts left in essay tasks.

    ``status`` is ``submitted`` for essay records and ``draft`` for an essay task's saved
    ``essay_draft`` without a submitted essay (an abandoned or unfinished task).
    ``word_count``/``character_count`` are the platform's values for submitted essays;
    ``calculated_word_count`` is a whitespace split available for both.
    """
    columns = [*CONTEXT_COLUMNS, "task_id", "essay_id", "status", "text", "word_count", "calculated_word_count"]
    rows: list[dict[str, Any]] = []
    tasks = {row["task_id"]: row for row in dataset.session_tasks.to_dict("records")}
    for essay in dataset.essays.to_dict("records"):
        task = tasks.get(essay.get("task_id"), {})
        rows.append(
            {
                "session_id": essay["session_id"],
                "task_id": essay.get("task_id"),
                "task_title": task.get("title"),
                "essay_id": essay.get("essay_id"),
                "status": "submitted",
                "topic_title": essay.get("topic_title"),
                "text": essay.get("content"),
                "word_count": essay.get("word_count"),
                "character_count": essay.get("character_count"),
                "saved_at": essay.get("created_at"),
                "_session_order": essay.get("_session_order"),
            }
        )
    if include_drafts:
        for task in dataset.session_tasks.to_dict("records"):
            draft = task.get("essay_draft")
            if task.get("task_type") != "ESSAY" or not _missing(task.get("essay_id")):
                continue
            if _missing(draft) or not str(draft).strip():
                continue
            rows.append(
                {
                    "session_id": task["session_id"],
                    "task_id": task["task_id"],
                    "task_title": task.get("title"),
                    "essay_id": None,
                    "status": "draft",
                    "topic_title": task.get("essay_topic_title"),
                    "text": draft,
                    "word_count": None,
                    "character_count": None,
                    "saved_at": task.get("updated_at"),
                    "_session_order": task.get("_session_order"),
                }
            )
    result = pd.DataFrame(
        rows,
        columns=["session_id", "task_id", "task_title", "essay_id", "status", "topic_title", "text", "word_count",
                 "character_count", "saved_at", "_session_order"],
    )
    for column in ("word_count", "character_count"):
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("Int64")
    result["calculated_word_count"] = pd.array(
        [len(text.split()) if isinstance(text, str) else pd.NA for text in result["text"]], dtype="Int64"
    )
    result["calculated_character_count"] = pd.array(
        [len(text) if isinstance(text, str) else pd.NA for text in result["text"]], dtype="Int64"
    )
    result["saved_at_dt"] = _utc(result["saved_at"], "ns")
    result = _with_context(result, dataset)
    if result.empty:
        return result.reindex(columns=list(dict.fromkeys([*columns, *result.columns])))
    return result.sort_values(["_session_order", "status"], kind="stable").reset_index(drop=True)


def _message_text(value: Any) -> Any:
    if isinstance(value, str) or _missing(value):
        return value
    if isinstance(value, list):
        parts = [part.get("text") if isinstance(part, dict) else part for part in value]
        return "\n".join(str(part) for part in parts if part)
    if isinstance(value, dict):
        return value.get("text") or value.get("content") or str(value)
    return str(value)


def chat_transcript(dataset: ExperimentDataset, session_id: str | None = None) -> pd.DataFrame:
    """Messages in conversational order with text, task, tokens and the linked LLM request."""
    messages = dataset.messages
    if session_id is not None:
        messages = messages.loc[messages["session_id"] == session_id]
    columns = [*CONTEXT_COLUMNS, "chat_id", "task_id", "role", "text", "created_at_dt"]
    if messages.empty:
        return pd.DataFrame(columns=columns)
    result = messages.copy()
    result["text"] = result["content"].map(_message_text) if "content" in result else pd.NA
    usage = result.get("usage", pd.Series(None, index=result.index))
    result["input_tokens"] = usage.map(lambda value: _usage_value(value, ("input_tokens", "prompt_tokens")))
    result["output_tokens"] = usage.map(lambda value: _usage_value(value, ("output_tokens", "completion_tokens")))
    if "error" in result:
        result["error_text"] = result["error"].map(
            lambda value: value.get("content") if isinstance(value, dict) else (None if _missing(value) else value)
        )
    parents = dict(zip(zip(result["chat_id"], result["history_message_id"]), result.get("parent_id", [])))

    def depth(chat_id: Any, message_id: Any) -> int:
        level, seen = 0, set()
        parent = parents.get((chat_id, message_id))
        while not _missing(parent) and (chat_id, parent) in parents and parent not in seen:
            seen.add(parent)
            level += 1
            parent = parents.get((chat_id, parent))
        return level

    result["turn_depth"] = [depth(c, m) for c, m in zip(result["chat_id"], result["history_message_id"])]
    requests = dataset.llm_requests
    if not requests.empty:
        linked = requests[
            _available(requests, ["session_id", "assistant_message_id", "llm_request_id", "status", "timing_mode",
                                  "prompt_active", "prompt_number", "request_sequence", "error_type"])
        ].rename(
            columns={
                "assistant_message_id": "history_message_id",
                "status": "request_status",
                "error_type": "request_error_type",
            }
        )
        result = result.merge(linked, on=["session_id", "history_message_id"], how="left")
    result = result.merge(_task_labels(dataset), on="task_id", how="left")
    result = _with_context(result, dataset)
    keep = [
        *CONTEXT_COLUMNS, "chat_id", "task_id", "task_title", "task_position", "task_id_source", "message_id",
        "history_message_id", "parent_id", "turn_depth", "role", "text", "model_id", "done", "error_text",
        "input_tokens", "output_tokens", "created_at", "created_at_dt", "llm_request_id", "request_sequence",
        "prompt_number", "request_status", "request_error_type", "timing_mode", "prompt_active", "_session_order",
    ]
    result = result.reindex(columns=[column for column in keep if column in result])
    return result.sort_values(
        ["_session_order", "chat_id", "turn_depth", "created_at"], kind="stable"
    ).reset_index(drop=True)


def llm_request_timing(dataset: ExperimentDataset) -> pd.DataFrame:
    """LLM requests with latency components in milliseconds, measured from ``request_at``.

    ``client_*`` latencies use the participant's browser clock and can be negative or inflated
    by clock skew; the ``provider_*``/``server_*`` values share the server clock.
    """
    requests = dataset.llm_requests
    if requests.empty:
        return _with_context(requests.copy(), dataset)
    result = requests.copy()
    result["provider_queue_ms"] = _ms_between(result, "request_at", "provider_started_at")
    result["time_to_first_token_ms"] = _ms_between(result, "request_at", "provider_first_token_at")
    result["provider_generation_ms"] = _ms_between(result, "provider_started_at", "provider_completed_at")
    result["artificial_delay_ms"] = _ms_between(result, "artificial_delay_started_at", "artificial_delay_ended_at")
    result["server_first_emit_ms"] = _ms_between(result, "request_at", "server_first_emit_at")
    result["server_completed_ms"] = _ms_between(result, "request_at", "server_completed_emit_at")
    result["client_first_visible_ms"] = _ms_between(result, "request_at", "client_first_visible_at")
    result["client_completed_visible_ms"] = _ms_between(result, "request_at", "client_completed_visible_at")
    result = result.merge(_task_labels(dataset).rename(columns={"task_id": "session_task_id"}), on="session_task_id", how="left")
    result = _with_context(result, dataset)
    return result.sort_values(["_session_order", "request_sequence"], kind="stable").reset_index(drop=True)


def telemetry_events_flat(dataset: ExperimentDataset, event_types: Iterable[str] | None = None) -> pd.DataFrame:
    """Telemetry events with ``payload_json`` fields promoted to columns.

    Top-level payload keys become columns (for example ``key_class``, ``key_value``,
    ``inter_key_interval_ms``, ``text_value``, ``away_duration_ms``, ``browser_session_id``).
    ``modifiers`` becomes ``modifier_ctrl``/``modifier_shift``/…, and a tab snapshot becomes
    ``tab_*`` columns as in the platform's tab-activity export. ``payload_json`` is retained.
    """
    events = dataset.telemetry_events
    if event_types is not None:
        wanted = set(event_types)
        events = events.loc[events["event_type"].isin(wanted)] if "event_type" in events else events.iloc[0:0]
    if events.empty:
        return _with_context(events.copy().rename(columns={"session_task_id": "task_id"}), dataset)
    base = events.drop(columns=[column for column in ("experiment_session_id",) if column in events]).copy()
    for column in ("session_task_id", "question_id", "field_context"):
        if column not in base:
            base[column] = pd.NA
    flattened: list[dict[str, Any]] = []
    for payload in base["payload_json"]:
        row: dict[str, Any] = {}
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == "tab" and isinstance(value, dict):
                    row.update({f"tab_{name}": item for name, item in value.items()})
                elif key == "modifiers" and isinstance(value, dict):
                    row.update({f"modifier_{name}": item for name, item in value.items()})
                else:
                    row[key] = value
        flattened.append(row)
    extra = pd.DataFrame(flattened, index=base.index)
    extra = extra[[column for column in extra.columns if column not in base.columns]]
    result = pd.concat([base, extra.convert_dtypes()], axis=1)
    result = result.rename(columns={"session_task_id": "task_id"})
    result = result.merge(_task_labels(dataset), on="task_id", how="left")
    questions = dataset.questions
    if "question_id" in result and not questions.empty:
        result = result.merge(
            questions[["question_id", "title"]].rename(columns={"title": "question_title"}), on="question_id", how="left"
        )
    result = _with_context(result, dataset)
    return result.sort_values(["_session_order", "event_time", "_record_order"], kind="stable").reset_index(drop=True)


def keystrokes(dataset: ExperimentDataset) -> pd.DataFrame:
    """Keystroke events with key class, literal key value, interval, hold time and modifiers."""
    return telemetry_events_flat(dataset, ["keystroke"])


def clipboard_events(dataset: ExperimentDataset) -> pd.DataFrame:
    """Copy, cut and paste events with field context, length, line count and captured text."""
    return telemetry_events_flat(dataset, CLIPBOARD_EVENT_TYPES)


def tab_activity(dataset: ExperimentDataset) -> pd.DataFrame:
    """Browser-extension tab, window-focus and telemetry-loss events (schema 2+)."""
    return telemetry_events_flat(dataset, TAB_EVENT_TYPES)


def _task_at(tasks: list[dict[str, Any]], moment: Any) -> dict[str, Any]:
    for task in tasks:
        start = task.get("started_at")
        end = task.get("finalized_at") if not _missing(task.get("finalized_at")) else task.get("completed_at")
        if _missing(start) or _missing(moment):
            continue
        if start <= moment and (_missing(end) or moment <= end):
            return task
    return {}


def focus_periods(dataset: ExperimentDataset) -> pd.DataFrame:
    """Periods when the experiment page lost focus, from ``focus_away`` to ``focus_return``.

    ``away_ms`` uses the client's measured ``away_duration_ms`` when available. ``returned`` is
    false when the session ends without a return. The task is inferred from task start/finish
    times, and ``destination_title``/``destination_url`` come from the first tab the extension saw
    activated or created during the absence (empty when focus moved to another application).
    """
    events = telemetry_events_flat(dataset, ["focus_away", "focus_return", "tab_activated", "tab_created", "tab_updated"])
    columns = [*CONTEXT_COLUMNS, "task_id", "task_title", "away_at_dt", "returned_at_dt", "away_ms", "returned"]
    if events.empty:
        return pd.DataFrame(columns=columns)
    tasks_by_session: dict[Any, list[dict[str, Any]]] = {}
    for task in dataset.session_tasks.sort_values("position").to_dict("records"):
        tasks_by_session.setdefault(task["session_id"], []).append(task)
    rows: list[dict[str, Any]] = []
    for session_id, group in events.groupby("session_id", sort=False):
        group = group.sort_values(["event_time", "_record_order"], kind="stable")
        open_period: dict[str, Any] | None = None
        for event in group.to_dict("records"):
            kind = event["event_type"]
            if kind == "focus_away":
                if open_period is not None:
                    rows.append(open_period)
                task = _task_at(tasks_by_session.get(session_id, []), event["event_time"])
                open_period = {
                    "session_id": session_id,
                    "task_id": task.get("task_id"),
                    "task_title": task.get("title"),
                    "away_at": event["event_time"],
                    "returned_at": None,
                    "away_ms": None,
                    "returned": False,
                    "destination_title": None,
                    "destination_url": None,
                    "_destination_tab": None,
                    "_session_order": event.get("_session_order"),
                }
            elif open_period is not None and kind in ("tab_activated", "tab_created"):
                if open_period["_destination_tab"] is None:
                    open_period["_destination_tab"] = event.get("tab_tab_id")
                    open_period["destination_title"] = event.get("tab_title")
                    open_period["destination_url"] = event.get("tab_url")
            elif open_period is not None and kind == "tab_updated":
                # A new tab navigates after it is opened; keep the page it settled on.
                if event.get("tab_tab_id") == open_period["_destination_tab"] and not _missing(event.get("tab_url")):
                    open_period["destination_title"] = event.get("tab_title")
                    open_period["destination_url"] = event.get("tab_url")
            elif kind == "focus_return" and open_period is not None:
                open_period["returned_at"] = event["event_time"]
                measured = event.get("away_duration_ms")
                open_period["away_ms"] = (
                    measured if not _missing(measured) else (event["event_time"] - open_period["away_at"]) / 1_000_000
                )
                open_period["returned"] = True
                rows.append(open_period)
                open_period = None
        if open_period is not None:
            rows.append(open_period)
    result = pd.DataFrame(
        rows,
        columns=["session_id", "task_id", "task_title", "away_at", "returned_at", "away_ms", "returned",
                 "destination_title", "destination_url", "_session_order"],
    )
    result["away_at_dt"] = _utc(result["away_at"], "ns")
    result["returned_at_dt"] = _utc(result["returned_at"], "ns")
    result = _with_context(result, dataset)
    return result.sort_values(["_session_order", "away_at"], kind="stable").reset_index(drop=True)


def keystroke_summary(
    dataset: ExperimentDataset,
    by: Iterable[str] = ("session_id", "task_id", "field_context"),
    pause_threshold_ms: int = 2000,
) -> pd.DataFrame:
    """Typing statistics per group of keystroke events (default: session × task × field).

    Counts are computed from the exported events, so they match the server-side
    ``telemetry_summaries`` only when the export contains every event.
    """
    keys = keystrokes(dataset)
    group_columns = list(by)
    columns = [*group_columns, "keystrokes", "printable", "backspaces", "deletion_ratio", "mean_interval_ms"]
    if keys.empty:
        return pd.DataFrame(columns=columns)
    keys = keys.copy()
    key_class = keys.get("key_class", pd.Series(pd.NA, index=keys.index)).astype("string")
    keys["_printable"] = key_class.isin(["printable", "whitespace", "enter"]).astype(int)
    keys["_deletion"] = key_class.isin(["backspace", "delete"]).astype(int)
    keys["_interval"] = pd.to_numeric(keys.get("inter_key_interval_ms"), errors="coerce")
    keys["_hold"] = pd.to_numeric(keys.get("hold_duration_ms"), errors="coerce")
    keys["_pause"] = (keys["_interval"] > pause_threshold_ms).fillna(False).astype(int)
    grouped = keys.groupby(group_columns, dropna=False, sort=False)
    result = grouped.agg(
        keystrokes=("event_type", "size"),
        printable=("_printable", "sum"),
        backspaces=("_deletion", "sum"),
        mean_interval_ms=("_interval", "mean"),
        median_interval_ms=("_interval", "median"),
        pauses=("_pause", "sum"),
        longest_pause_ms=("_interval", "max"),
        mean_hold_ms=("_hold", "mean"),
        first_key_at=("event_time", "min"),
        last_key_at=("event_time", "max"),
    ).reset_index()
    result["deletion_ratio"] = (result["backspaces"] / result["keystrokes"]).round(4)
    result["typing_span_s"] = ((result["last_key_at"] - result["first_key_at"]) / 1_000_000_000).round(3)
    result["first_key_at_dt"] = _utc(result["first_key_at"], "ns")
    result["last_key_at_dt"] = _utc(result["last_key_at"], "ns")
    if "task_id" in result:
        result = result.merge(_task_labels(dataset), on="task_id", how="left")
    if "session_id" in result:
        result = _with_context(result, dataset)
    return result


def session_activity(dataset: ExperimentDataset) -> pd.DataFrame:
    """One row per session describing completion, timing, absence and data coverage.

    ``last_activity_at`` is the latest timestamp found in the session's tasks, submissions,
    messages, LLM requests and telemetry. ``wall_seconds`` runs from session creation to completion
    (or to the last activity for unfinished runs); ``away_seconds`` sums focus absences;
    ``active_seconds`` is their difference. These are descriptive values to support exclusion
    rules, not decisions.
    """
    context = session_context(dataset)
    sessions = dataset.sessions.set_index("session_id")
    latest: dict[Any, int] = {}

    def observe(session_id: Any, value: Any, unit: str = "ns") -> None:
        if _missing(value) or isinstance(value, bool):
            return
        try:
            stamp = int(value) * (1_000_000_000 if unit == "s" else 1)
        except (TypeError, ValueError):
            return
        latest[session_id] = max(latest.get(session_id, stamp), stamp)

    for session in dataset.sessions.to_dict("records"):
        for column in ("consented_at", "topic_shown_at", "essay_submitted_at", "task_submitted_at", "completed_at",
                       "updated_at"):
            observe(session["session_id"], session.get(column))
    for task in dataset.session_tasks.to_dict("records"):
        for column in ("started_at", "completed_at", "finalized_at", "updated_at"):
            observe(task["session_id"], task.get(column))
    for row in dataset.extension_presence.to_dict("records"):
        # Heartbeats arrive every ~20 s while the page is open, even without interaction.
        observe(row["session_id"], row.get("last_seen_at"))
    for table in ("question_submissions", "survey_submissions"):
        for row in dataset.table(table).to_dict("records"):
            observe(row["session_id"], row.get("updated_at"))
    for row in dataset.messages.to_dict("records"):
        observe(row["session_id"], row.get("created_at"), "s")
    for row in dataset.llm_requests.to_dict("records"):
        observe(row["session_id"], row.get("updated_at"))
    events = dataset.telemetry_events
    if not events.empty:
        for session_id, value in events.groupby("session_id")["event_time"].max().items():
            observe(session_id, value)
    periods = focus_periods(dataset)
    away = (
        pd.to_numeric(periods["away_ms"], errors="coerce").groupby(periods["session_id"]).sum() / 1000
        if not periods.empty else pd.Series(dtype="float64")
    )
    tasks = dataset.session_tasks
    status = tasks.get("status", pd.Series(dtype="string")).astype("string")
    finished = status.isin(["FINALIZED", "SKIPPED", "COMPLETED"]).groupby(tasks["session_id"]).sum() if not tasks.empty else {}
    total = tasks.groupby("session_id").size() if not tasks.empty else {}
    summaries = dataset.telemetry_summaries.set_index("session_id") if not dataset.telemetry_summaries.empty else None
    presence = dataset.extension_presence.set_index("session_id") if not dataset.extension_presence.empty else None
    event_counts = events.groupby("session_id").size() if not events.empty else {}
    rows = []
    for row in context.to_dict("records"):
        session_id = row["session_id"]
        record = sessions.loc[session_id] if session_id in sessions.index else {}
        created = record.get("created_at") if len(record) else None
        completed = record.get("completed_at") if len(record) else None
        last = latest.get(session_id)
        end = completed if not _missing(completed) else last
        wall = (end - created) / 1_000_000_000 if not _missing(end) and not _missing(created) else None
        away_seconds = float(away.get(session_id, 0.0)) if session_id in getattr(away, "index", []) else 0.0
        summary = summaries.loc[session_id] if summaries is not None and session_id in summaries.index else None
        rows.append(
            {
                **row,
                "created_at": created,
                "started_at": record.get("topic_shown_at") if len(record) else None,
                "completed_at": completed,
                "last_activity_at": last,
                "is_completed": row.get("state") == "COMPLETED",
                "tasks_total": int(total.get(session_id, 0)) if len(total) else 0,
                "tasks_finished": int(finished.get(session_id, 0)) if len(finished) else 0,
                "wall_seconds": None if wall is None else round(wall, 3),
                "away_seconds": round(away_seconds, 3),
                "active_seconds": None if wall is None else round(max(wall - away_seconds, 0.0), 3),
                "telemetry_events_exported": int(event_counts.get(session_id, 0)) if len(event_counts) else 0,
                "summary_keystrokes": None if summary is None else summary.get("total_keystrokes"),
                "summary_pasted_chars": None if summary is None else summary.get("total_pasted_chars"),
                "has_telemetry_summary": summary is not None,
                "extension_connected": presence is not None and session_id in presence.index,
                "extension_last_seen_at": (
                    presence.loc[session_id].get("last_seen_at")
                    if presence is not None and session_id in presence.index else None
                ),
            }
        )
    result = pd.DataFrame(rows)
    for column in ("created_at", "started_at", "completed_at", "last_activity_at", "extension_last_seen_at"):
        if column in result:
            result[f"{column}_dt"] = _utc(result[column], "ns")
    return result.drop(columns="_session_order", errors="ignore")
