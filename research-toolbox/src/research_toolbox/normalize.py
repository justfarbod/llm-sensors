from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from numbers import Number
from typing import Any

import pandas as pd

from .errors import ExportValidationError

TABLE_COLUMNS: dict[str, list[str]] = {
    "sessions": ["session_id"],
    "session_summaries": ["session_id"],
    "session_workflows": ["session_id", "workflow_id", "configuration_id"],
    "session_usage": ["session_id"],
    "task_usage": ["session_id", "task_id"],
    "participants": ["participant_id"],
    "groups": ["group_id"],
    "memberships": ["session_id", "group_id", "participant_id"],
    "topics": ["topic_id"],
    "topic_assignments": ["session_id", "topic_id"],
    "plans": ["plan_id"],
    "plan_items": ["plan_id", "plan_item_id"],
    "plan_item_topics": ["plan_id", "plan_item_id", "topic_id"],
    "conditions": ["plan_id", "condition_id"],
    "session_conditions": ["session_id", "plan_id", "condition_id", "assigned_to_session"],
    "condition_task_scopes": ["condition_id", "plan_item_id"],
    "prompt_injections": ["condition_id"],
    "warning_modals": ["condition_id"],
    "response_timings": ["condition_id"],
    "question_tasks": ["question_task_id"],
    "questions": ["question_task_id", "question_id"],
    "question_choices": ["question_id", "choice_id"],
    "question_blanks": ["question_id", "blank_id"],
    "accepted_blank_answers": ["blank_id", "accepted_answer_id"],
    "free_text_configs": ["question_id"],
    "survey_tasks": ["survey_task_id"],
    "survey_questions": ["survey_task_id", "survey_question_id"],
    "survey_choices": ["survey_question_id", "survey_choice_id"],
    "session_tasks": ["session_id", "task_id"],
    "essays": ["session_id", "task_id", "essay_id"],
    "question_submissions": ["session_id", "task_id", "submission_id"],
    "question_responses": ["session_id", "task_id", "submission_id", "response_id", "question_id"],
    "question_response_choices": ["response_id", "choice_id"],
    "question_blank_answers": ["response_id", "blank_id"],
    "grading_attempts": ["response_id", "grading_attempt_id"],
    "score_overrides": ["response_id", "score_override_id"],
    "survey_submissions": ["session_id", "task_id", "submission_id"],
    "survey_responses": ["session_id", "task_id", "submission_id", "response_id", "survey_question_id"],
    "survey_response_choices": ["response_id", "survey_question_id", "survey_choice_id"],
    "chats": ["session_id", "task_id", "chat_id"],
    "messages": ["session_id", "task_id", "chat_id", "message_id"],
    "chat_attachments": ["chat_id", "file_id", "attachment_id"],
    "files": ["file_id"],
    "chat_embedded_files": ["chat_id", "file_id"],
    "feedback": ["session_id", "chat_id", "feedback_id"],
    "telemetry_summaries": ["session_id"],
    "extension_presence": ["session_id"],
    "telemetry_events": ["session_id", "telemetry_event_id"],
    "llm_requests": ["session_id", "llm_request_id"],
    "warning_states": ["session_id", "warning_state_id"],
}


SECOND_TABLES = {
    "participants",
    "groups",
    "memberships",
    "chats",
    "messages",
    "chat_attachments",
    "files",
    "chat_embedded_files",
    "feedback",
}

NANOSECOND_TABLES = set(TABLE_COLUMNS) - SECOND_TABLES - {"session_summaries", "session_conditions"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _record(value: Any) -> dict[str, Any] | None:
    item = _mapping(value)
    if item is None:
        return None
    nested = item.get("record")
    return _mapping(nested) if "record" in item else item


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class _Collector:
    def __init__(self, strict: bool):
        self.strict = strict
        self.rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.warnings: list[str] = []
        self._seen: dict[str, dict[Any, str]] = defaultdict(dict)

    def problem(self, message: str) -> None:
        if self.strict:
            raise ExportValidationError(message)
        if message not in self.warnings:
            self.warnings.append(message)

    def add(
        self,
        table: str,
        record: Any,
        *,
        context: Mapping[str, Any] | None = None,
        session_order: int | None = None,
        dedupe_key: Any = None,
        fingerprint: Any = None,
    ) -> dict[str, Any] | None:
        payload = _record(record)
        if payload is None:
            return None
        if dedupe_key is not None:
            marker = _canonical(payload if fingerprint is None else fingerprint)
            existing = self._seen[table].get(dedupe_key)
            if existing is not None:
                if existing != marker:
                    self.problem(f"Conflicting {table} definition for key {dedupe_key!r}; first value retained.")
                return None
            self._seen[table][dedupe_key] = marker

        row = dict(payload)
        if context:
            row.update(context)
        if session_order is not None:
            row["_session_order"] = session_order
        row["_record_order"] = len(self.rows[table])
        self.rows[table].append(row)
        return row


def _definition_id(value: Any, fallback: str | None = None) -> str | None:
    record = _record(value) or {}
    result = record.get("id", fallback)
    return str(result) if result is not None else None


def _collect_topic(collector: _Collector, value: Any, session_order: int) -> str | None:
    topic_id = _definition_id(value)
    if value is not None:
        collector.add(
            "topics",
            value,
            context={"topic_id": topic_id},
            session_order=session_order,
            dedupe_key=topic_id,
        )
    return topic_id


def _collect_file(collector: _Collector, value: Any, session_order: int) -> str | None:
    file_id = _definition_id(value)
    if value is not None:
        collector.add(
            "files",
            value,
            context={"file_id": file_id},
            session_order=session_order,
            dedupe_key=file_id,
        )
    return file_id


def _collect_question_definition(
    collector: _Collector,
    question: Any,
    task_id: str | None,
    session_order: int,
    question_order: int = 0,
) -> str | None:
    question_wrapper = _mapping(question) or {}
    question_record = _record(question_wrapper) or {}
    task_id = question_record.get("task_id", task_id)
    question_id = _definition_id(question_wrapper)
    collector.add(
        "questions",
        question_wrapper,
        context={"question_task_id": task_id, "question_id": question_id, "_parent_order": question_order},
        session_order=session_order,
        dedupe_key=question_id,
    )
    for choice_order, choice in enumerate(_items(question_wrapper.get("choices"))):
        choice_id = _definition_id(choice)
        collector.add(
            "question_choices",
            choice,
            context={"question_id": question_id, "choice_id": choice_id, "_parent_order": choice_order},
            session_order=session_order,
            dedupe_key=choice_id,
        )
    for blank_order, blank in enumerate(_items(question_wrapper.get("blanks"))):
        blank_wrapper = _mapping(blank) or {}
        blank_id = _definition_id(blank_wrapper)
        collector.add(
            "question_blanks",
            blank_wrapper,
            context={"question_id": question_id, "blank_id": blank_id, "_parent_order": blank_order},
            session_order=session_order,
            dedupe_key=blank_id,
        )
        for answer_order, answer in enumerate(_items(blank_wrapper.get("accepted_answers"))):
            answer_id = _definition_id(answer)
            key = answer_id or (blank_id, answer_order, _canonical(answer))
            collector.add(
                "accepted_blank_answers",
                answer,
                context={"blank_id": blank_id, "accepted_answer_id": answer_id, "_parent_order": answer_order},
                session_order=session_order,
                dedupe_key=key,
            )
    config = question_wrapper.get("free_text_config")
    if config is not None:
        collector.add(
            "free_text_configs",
            config,
            context={"question_id": question_id},
            session_order=session_order,
            dedupe_key=question_id,
        )
    _collect_file(collector, question_wrapper.get("image"), session_order)
    return question_id


def _collect_question_task(collector: _Collector, value: Any, session_order: int) -> str | None:
    wrapper = _mapping(value)
    if wrapper is None:
        return None
    task_id = _definition_id(wrapper)
    collector.add(
        "question_tasks",
        wrapper,
        context={"question_task_id": task_id},
        session_order=session_order,
        dedupe_key=task_id,
    )
    for question_order, question in enumerate(_items(wrapper.get("questions"))):
        _collect_question_definition(collector, question, task_id, session_order, question_order)
    return task_id


def _collect_survey_task(collector: _Collector, value: Any, session_order: int) -> str | None:
    wrapper = _mapping(value)
    if wrapper is None:
        return None
    task_id = _definition_id(wrapper)
    collector.add(
        "survey_tasks",
        wrapper,
        context={"survey_task_id": task_id},
        session_order=session_order,
        dedupe_key=task_id,
    )
    for question_order, question in enumerate(_items(wrapper.get("questions"))):
        question_wrapper = _mapping(question) or {}
        question_id = _definition_id(question_wrapper)
        collector.add(
            "survey_questions",
            question_wrapper,
            context={"survey_task_id": task_id, "survey_question_id": question_id, "_parent_order": question_order},
            session_order=session_order,
            dedupe_key=question_id,
        )
        for choice_order, choice in enumerate(_items(question_wrapper.get("choices"))):
            choice_id = _definition_id(choice)
            collector.add(
                "survey_choices",
                choice,
                context={
                    "survey_question_id": question_id,
                    "survey_choice_id": choice_id,
                    "_parent_order": choice_order,
                },
                session_order=session_order,
                dedupe_key=choice_id,
            )
    return task_id


def _collect_plan_item(collector: _Collector, value: Any, plan_id: str | None, session_order: int) -> str | None:
    wrapper = _mapping(value)
    if wrapper is None:
        return None
    item_id = _definition_id(wrapper)
    collector.add(
        "plan_items",
        wrapper,
        context={"plan_id": plan_id, "plan_item_id": item_id},
        session_order=session_order,
        dedupe_key=item_id,
    )
    _collect_topic(collector, wrapper.get("topic"), session_order)
    for pool_order, pool in enumerate(_items(wrapper.get("topic_pool"))):
        pool_wrapper = _mapping(pool) or {}
        topic_id = _collect_topic(collector, pool_wrapper.get("topic"), session_order)
        pool_record = _record(pool_wrapper) or {}
        topic_id = pool_record.get("topic_id", topic_id)
        collector.add(
            "plan_item_topics",
            pool_wrapper,
            context={"plan_id": plan_id, "plan_item_id": item_id, "topic_id": topic_id, "_parent_order": pool_order},
            session_order=session_order,
            dedupe_key=(item_id, topic_id),
        )
    _collect_question_task(collector, wrapper.get("question_task"), session_order)
    _collect_survey_task(collector, wrapper.get("survey_task"), session_order)
    return item_id


def _collect_plan(collector: _Collector, value: Any, session_id: str | None, session_order: int) -> str | None:
    wrapper = _mapping(value)
    if wrapper is None:
        return None
    plan_id = _definition_id(wrapper)
    collector.add(
        "plans",
        wrapper,
        context={"plan_id": plan_id},
        session_order=session_order,
        dedupe_key=plan_id,
    )
    for item in _items(wrapper.get("items")):
        _collect_plan_item(collector, item, plan_id, session_order)
    assigned_id = wrapper.get("assigned_condition_id")
    for condition_order, condition in enumerate(_items(wrapper.get("conditions"))):
        condition_wrapper = _mapping(condition) or {}
        condition_id = _definition_id(condition_wrapper)
        collector.add(
            "conditions",
            condition_wrapper,
            context={"plan_id": plan_id, "condition_id": condition_id},
            session_order=session_order,
            dedupe_key=condition_id,
        )
        assigned = bool(condition_wrapper.get("assigned_to_session")) or condition_id == assigned_id
        collector.add(
            "session_conditions",
            {"assigned_to_session": assigned},
            context={"session_id": session_id, "plan_id": plan_id, "condition_id": condition_id},
            session_order=session_order,
            dedupe_key=(session_id, condition_id),
        )
        for table, key in (
            ("prompt_injections", "prompt_injection"),
            ("warning_modals", "warning_modal"),
            ("response_timings", "response_timing"),
        ):
            if condition_wrapper.get(key) is not None:
                collector.add(
                    table,
                    condition_wrapper[key],
                    context={"condition_id": condition_id},
                    session_order=session_order,
                    dedupe_key=condition_id,
                )
        for scope_order, scope in enumerate(_items(condition_wrapper.get("task_scopes"))):
            scope_wrapper = _mapping(scope) or {}
            scope_record = _record(scope_wrapper) or {}
            plan_item_id = scope_record.get("plan_item_id") or _definition_id(scope_wrapper.get("plan_item"))
            _collect_plan_item(collector, scope_wrapper.get("plan_item"), plan_id, session_order)
            scope_id = scope_record.get("id")
            scope_key = scope_id or (
                condition_id,
                plan_item_id,
                scope_record.get("perturbation_type"),
            )
            collector.add(
                "condition_task_scopes",
                scope_wrapper,
                context={"condition_id": condition_id, "plan_item_id": plan_item_id, "_parent_order": scope_order},
                session_order=session_order,
                dedupe_key=scope_key,
            )
    return plan_id


def _collect_question_submission(
    collector: _Collector,
    value: Any,
    context: dict[str, Any],
    session_order: int,
) -> None:
    wrapper = _mapping(value)
    if wrapper is None:
        return
    submission_id = _definition_id(wrapper)
    submission_context = {**context, "submission_id": submission_id}
    collector.add("question_submissions", wrapper, context=submission_context, session_order=session_order)
    for response_order, response in enumerate(_items(wrapper.get("responses"))):
        response_wrapper = _mapping(response) or {}
        response_id = _definition_id(response_wrapper)
        question = response_wrapper.get("question")
        question_id = _definition_id(question) or (_record(response_wrapper) or {}).get("question_id")
        if question is not None:
            question_record = _record(question) or {}
            _collect_question_definition(collector, question, question_record.get("task_id"), session_order)
        response_context = {
            **submission_context,
            "response_id": response_id,
            "question_id": question_id,
            "_parent_order": response_order,
        }
        collector.add("question_responses", response_wrapper, context=response_context, session_order=session_order)
        for choice_order, selected in enumerate(_items(response_wrapper.get("selected_choices"))):
            selected_wrapper = _mapping(selected) or {}
            choice = selected_wrapper.get("choice")
            choice_id = _definition_id(choice) or (_record(selected_wrapper) or {}).get("choice_id")
            if choice is not None:
                collector.add(
                    "question_choices",
                    choice,
                    context={"question_id": question_id, "choice_id": choice_id},
                    session_order=session_order,
                    dedupe_key=choice_id,
                )
            collector.add(
                "question_response_choices",
                selected_wrapper,
                context={"response_id": response_id, "choice_id": choice_id, "_parent_order": choice_order},
                session_order=session_order,
            )
        for blank_order, answer in enumerate(_items(response_wrapper.get("blank_answers"))):
            answer_wrapper = _mapping(answer) or {}
            blank = answer_wrapper.get("blank")
            blank_id = _definition_id(blank) or (_record(answer_wrapper) or {}).get("blank_id")
            if blank is not None:
                collector.add(
                    "question_blanks",
                    blank,
                    context={"question_id": question_id, "blank_id": blank_id},
                    session_order=session_order,
                    dedupe_key=blank_id,
                )
            collector.add(
                "question_blank_answers",
                answer_wrapper,
                context={"response_id": response_id, "blank_id": blank_id, "_parent_order": blank_order},
                session_order=session_order,
            )
        for attempt_order, attempt in enumerate(_items(response_wrapper.get("grading_attempts"))):
            attempt_id = _definition_id(attempt)
            collector.add(
                "grading_attempts",
                attempt,
                context={"response_id": response_id, "grading_attempt_id": attempt_id, "_parent_order": attempt_order},
                session_order=session_order,
            )
        for override_order, override in enumerate(_items(response_wrapper.get("score_overrides"))):
            override_id = _definition_id(override)
            collector.add(
                "score_overrides",
                override,
                context={"response_id": response_id, "score_override_id": override_id, "_parent_order": override_order},
                session_order=session_order,
            )


def _collect_survey_submission(
    collector: _Collector,
    value: Any,
    context: dict[str, Any],
    session_order: int,
) -> None:
    wrapper = _mapping(value)
    if wrapper is None:
        return
    submission_id = _definition_id(wrapper)
    submission_context = {**context, "submission_id": submission_id}
    collector.add("survey_submissions", wrapper, context=submission_context, session_order=session_order)
    for response_order, response in enumerate(_items(wrapper.get("responses"))):
        response_wrapper = _mapping(response) or {}
        response_id = _definition_id(response_wrapper)
        question = response_wrapper.get("question")
        question_id = _definition_id(question) or (_record(response_wrapper) or {}).get("question_id")
        if question is not None:
            collector.add(
                "survey_questions",
                question,
                context={"survey_task_id": None, "survey_question_id": question_id},
                session_order=session_order,
                dedupe_key=question_id,
            )
        response_context = {
            **submission_context,
            "response_id": response_id,
            "survey_question_id": question_id,
            "_parent_order": response_order,
        }
        collector.add("survey_responses", response_wrapper, context=response_context, session_order=session_order)
        for choice_order, selected in enumerate(_items(response_wrapper.get("selected_choices"))):
            selected_wrapper = _mapping(selected) or {}
            choice = selected_wrapper.get("choice")
            choice_id = _definition_id(choice) or (_record(selected_wrapper) or {}).get("choice_id")
            if choice is not None:
                collector.add(
                    "survey_choices",
                    choice,
                    context={"survey_question_id": question_id, "survey_choice_id": choice_id},
                    session_order=session_order,
                    dedupe_key=choice_id,
                )
            collector.add(
                "survey_response_choices",
                selected_wrapper,
                context={
                    "response_id": response_id,
                    "survey_question_id": question_id,
                    "survey_choice_id": choice_id,
                    "_parent_order": choice_order,
                },
                session_order=session_order,
            )


def _collect_session_task(
    collector: _Collector,
    value: Any,
    session_id: str | None,
    plan_id: str | None,
    session_order: int,
    task_order: int,
) -> None:
    wrapper = _mapping(value)
    if wrapper is None:
        return
    task_id = _definition_id(wrapper)
    context = {"session_id": session_id, "task_id": task_id, "_parent_order": task_order}
    collector.add("session_tasks", wrapper, context=context, session_order=session_order)
    _collect_plan_item(collector, wrapper.get("plan_item"), plan_id, session_order)
    _collect_topic(collector, wrapper.get("topic"), session_order)
    _collect_question_task(collector, wrapper.get("question_task"), session_order)
    _collect_survey_task(collector, wrapper.get("survey_task"), session_order)
    essay = wrapper.get("essay")
    if essay is not None:
        essay_id = _definition_id(essay)
        collector.add(
            "essays",
            essay,
            context={"session_id": session_id, "task_id": task_id, "essay_id": essay_id},
            session_order=session_order,
            dedupe_key=essay_id,
        )
    _collect_question_submission(collector, wrapper.get("question_submission"), context, session_order)
    _collect_survey_submission(collector, wrapper.get("survey_submission"), context, session_order)


def _collect_chats(collector: _Collector, chats: Any, session_id: str | None, session_order: int) -> None:
    for chat_order, chat in enumerate(_items(chats)):
        wrapper = _mapping(chat) or {}
        chat_record = _record(wrapper) or {}
        chat_id = chat_record.get("id")
        task_id = chat_record.get("experiment_session_task_id")
        context = {"session_id": session_id, "task_id": task_id, "chat_id": chat_id}
        collector.add(
            "chats",
            wrapper,
            context={**context, "_parent_order": chat_order},
            session_order=session_order,
        )
        for message_order, message in enumerate(_items(wrapper.get("messages"))):
            message_id = _definition_id(message)
            message_task_id = (_record(message) or {}).get("experiment_session_task_id") or task_id
            collector.add(
                "messages",
                message,
                context={
                    **context, "task_id": message_task_id, "message_id": message_id, "_parent_order": message_order
                },
                session_order=session_order,
            )
        for attachment_order, attachment in enumerate(_items(wrapper.get("attachments"))):
            attachment_wrapper = _mapping(attachment) or {}
            attachment_record = _record(attachment_wrapper) or {}
            file_id = attachment_record.get("file_id") or _definition_id(attachment_wrapper.get("file"))
            attachment_id = attachment_record.get("id")
            collector.add(
                "chat_attachments",
                attachment_wrapper,
                context={
                    "chat_id": chat_id,
                    "file_id": file_id,
                    "attachment_id": attachment_id,
                    "_parent_order": attachment_order,
                },
                session_order=session_order,
            )
            _collect_file(collector, attachment_wrapper.get("file"), session_order)
        for file_order, file_metadata in enumerate(_items(wrapper.get("embedded_file_metadata"))):
            file_id = _collect_file(collector, file_metadata, session_order)
            collector.add(
                "chat_embedded_files",
                {"chat_id": chat_id, "file_id": file_id},
                context={"chat_id": chat_id, "file_id": file_id, "_parent_order": file_order},
                session_order=session_order,
                dedupe_key=(chat_id, file_id),
            )
        for feedback_order, feedback in enumerate(_items(wrapper.get("feedback"))):
            feedback_id = _definition_id(feedback)
            collector.add(
                "feedback",
                feedback,
                context={
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "feedback_id": feedback_id,
                    "_parent_order": feedback_order,
                },
                session_order=session_order,
            )


def _collect_session(collector: _Collector, value: Any, session_order: int) -> None:
    wrapper = _mapping(value)
    if wrapper is None:
        collector.problem(f"sessions[{session_order}] is not an object and was skipped.")
        return
    expected_sections = {
        "session",
        "derived_summary",
        "participant",
        "group",
        "membership",
        "topic",
        "topic_assignment",
        "legacy_essay",
        "plan",
        "tasks",
        "chats",
        "telemetry",
        "perturbations",
    }
    missing_sections = sorted(expected_sections - wrapper.keys())
    if missing_sections:
        collector.problem(
            f"sessions[{session_order}] omits historical sections: {', '.join(missing_sections)}; empty values used."
        )
    session_record = _mapping(wrapper.get("session"))
    if session_record is None:
        collector.problem(f"sessions[{session_order}] has no session record.")
        session_record = {}
    session_id = session_record.get("id")
    collector.add(
        "sessions",
        session_record,
        context={"session_id": session_id, **({"is_demo": wrapper["is_demo"]} if "is_demo" in wrapper else {})},
        session_order=session_order,
    )
    # These sections were added without changing the schema major version.
    # Keep them optional so older exports continue to load in strict mode.
    for table, key in (("session_workflows", "workflow"), ("session_usage", "usage")):
        if wrapper.get(key) is not None:
            collector.add(table, wrapper[key], context={"session_id": session_id}, session_order=session_order)
    usage = _mapping(wrapper.get("usage")) or {}
    for task_id, task_usage in (_mapping(usage.get("by_task")) or {}).items():
        collector.add(
            "task_usage", task_usage, context={"session_id": session_id, "task_id": task_id},
            session_order=session_order,
        )
    if wrapper.get("derived_summary") is not None:
        collector.add(
            "session_summaries",
            wrapper["derived_summary"],
            context={"session_id": session_id},
            session_order=session_order,
        )
    participant = _mapping(wrapper.get("participant"))
    participant_id = None
    if participant is not None:
        participant_id = participant.get("participant_id") or participant.get("id")
        collector.add(
            "participants",
            participant,
            context={"participant_id": participant_id},
            session_order=session_order,
            dedupe_key=participant_id,
        )
    group = _mapping(wrapper.get("group"))
    group_id = session_record.get("group_id")
    if group is not None:
        group_id = group.get("id", group_id)
        collector.add(
            "groups",
            group,
            context={"group_id": group_id},
            session_order=session_order,
            dedupe_key=group_id,
        )
    if wrapper.get("membership") is not None:
        collector.add(
            "memberships",
            wrapper["membership"],
            context={"session_id": session_id, "group_id": group_id, "participant_id": participant_id},
            session_order=session_order,
            dedupe_key=(group_id, participant_id),
        )
    _collect_topic(collector, wrapper.get("topic"), session_order)
    assignment = _mapping(wrapper.get("topic_assignment"))
    if assignment is not None:
        assignment_record = _record(assignment) or {}
        topic_id = assignment_record.get("topic_id") or _collect_topic(
            collector, assignment.get("topic"), session_order
        )
        collector.add(
            "topic_assignments",
            assignment,
            context={"session_id": session_id, "topic_id": topic_id},
            session_order=session_order,
        )
    plan_id = _collect_plan(collector, wrapper.get("plan"), session_id, session_order)
    legacy_essay = wrapper.get("legacy_essay")
    if legacy_essay is not None:
        essay_id = _definition_id(legacy_essay)
        collector.add(
            "essays",
            legacy_essay,
            context={"session_id": session_id, "task_id": None, "essay_id": essay_id},
            session_order=session_order,
            dedupe_key=essay_id,
        )
    for task_order, task in enumerate(_items(wrapper.get("tasks"))):
        _collect_session_task(collector, task, session_id, plan_id, session_order, task_order)
    _collect_chats(collector, wrapper.get("chats"), session_id, session_order)

    telemetry = _mapping(wrapper.get("telemetry")) or {}
    for table, key in (("telemetry_summaries", "summary"), ("extension_presence", "extension_presence")):
        if telemetry.get(key) is not None:
            collector.add(table, telemetry[key], context={"session_id": session_id}, session_order=session_order)
    for event_order, event in enumerate(_items(telemetry.get("events"))):
        event_id = _definition_id(event)
        collector.add(
            "telemetry_events",
            event,
            context={"session_id": session_id, "telemetry_event_id": event_id, "_parent_order": event_order},
            session_order=session_order,
        )

    perturbations = _mapping(wrapper.get("perturbations")) or {}
    for request_order, request in enumerate(_items(perturbations.get("llm_requests"))):
        request_id = _definition_id(request)
        collector.add(
            "llm_requests",
            request,
            context={"session_id": session_id, "llm_request_id": request_id, "_parent_order": request_order},
            session_order=session_order,
        )
    for state_order, state in enumerate(_items(perturbations.get("warning_states"))):
        state_id = _definition_id(state)
        collector.add(
            "warning_states",
            state,
            context={"session_id": session_id, "warning_state_id": state_id, "_parent_order": state_order},
            session_order=session_order,
        )


def _timestamp_columns(table: str, frame: pd.DataFrame) -> dict[str, str]:
    if table == "session_summaries":
        return {
            name: "s"
            for name in ("session_start_time", "session_completion_time")
            if name in frame.columns
        }
    unit = "s" if table in SECOND_TABLES else "ns" if table in NANOSECOND_TABLES else None
    if unit is None:
        return {}
    names = [name for name in frame.columns if name.endswith("_at")]
    if table == "telemetry_events" and "event_time" in frame.columns:
        names.append("event_time")
    return {name: unit for name in names}


def _as_utc(value: Any, unit: str) -> pd.Timestamp | pd.NaT:
    if value is None or value is pd.NA or isinstance(value, bool):
        return pd.NaT
    if not isinstance(value, Number):
        return pd.NaT
    return pd.to_datetime(value, unit=unit, utc=True, errors="coerce")


def _frame(table: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    required = [*TABLE_COLUMNS[table], "_session_order", "_record_order"]
    frame = pd.DataFrame(rows)
    for column in required:
        if column not in frame.columns:
            frame[column] = pd.Series(dtype="Int64" if column.startswith("_") else "object")
    ordered = required + [column for column in frame.columns if column not in required]
    frame = frame.loc[:, ordered].convert_dtypes()
    for column, unit in _timestamp_columns(table, frame).items():
        frame[f"{column}_dt"] = pd.Series(
            [_as_utc(value, unit) for value in frame[column]],
            index=frame.index,
            dtype="datetime64[ns, UTC]",
        )
    return frame


def normalize_export(document: dict[str, Any], *, strict: bool = False) -> tuple[dict[str, pd.DataFrame], list[str]]:
    collector = _Collector(strict)
    sessions = document["sessions"]
    if not sessions:
        collector.problem("The export contains no sessions.")
    for session_order, session in enumerate(sessions):
        _collect_session(collector, session, session_order)
    tables = {name: _frame(name, collector.rows[name]) for name in TABLE_COLUMNS}
    return tables, collector.warnings
