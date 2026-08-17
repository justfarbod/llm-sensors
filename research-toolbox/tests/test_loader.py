from __future__ import annotations

import copy
import io
import json

import pandas as pd
import pytest

from research_toolbox import ExportValidationError, UnsupportedSchemaVersionError, load_export
from research_toolbox.normalize import TABLE_COLUMNS


def test_loads_path_file_and_mapping(fixture_path):
    from_path = load_export(fixture_path)
    with fixture_path.open(encoding="utf-8") as handle:
        from_file = load_export(handle)
    mapping = json.loads(fixture_path.read_text(encoding="utf-8"))
    from_mapping = load_export(mapping)

    assert len(from_path.sessions) == len(from_file.sessions) == len(from_mapping.sessions) == 2
    assert from_mapping.raw is not mapping
    assert from_path.metadata["schema_version"] == "1.0"


def test_rejects_malformed_json_and_envelopes():
    with pytest.raises(ExportValidationError, match="Could not read"):
        load_export(io.StringIO("{"))
    with pytest.raises(ExportValidationError, match="root"):
        load_export(io.StringIO("[]"))
    with pytest.raises(ExportValidationError, match="missing required fields"):
        load_export({"schema_version": "1.0"})
    with pytest.raises(UnsupportedSchemaVersionError):
        load_export(
            {
                "schema_version": "2.0",
                "exported_at": "2026-01-01T00:00:00+00:00",
                "anonymized": True,
                "timestamp_units": {},
                "sessions": [],
            }
        )


def test_all_tables_exist_and_expanded_records_are_normalized(fixture_path):
    data = load_export(fixture_path)

    assert set(data.tables) == set(TABLE_COLUMNS)
    assert len(data.session_tasks) == 4
    assert len(data.plan_items) == 3
    assert len(data.questions) == 2
    assert len(data.question_choices) == 2
    assert len(data.question_blanks) == 1
    assert len(data.accepted_blank_answers) == 1
    assert len(data.question_responses) == 2
    assert len(data.grading_attempts) == 1
    assert len(data.score_overrides) == 1
    assert len(data.survey_questions) == 2
    assert len(data.survey_choices) == 2
    assert len(data.survey_responses) == 2
    assert len(data.survey_response_choices) == 1
    assert len(data.messages) == 2
    assert len(data.files) == 2
    assert len(data.telemetry_events) == 2
    assert len(data.llm_requests) == 1
    assert len(data.warning_states) == 1
    assert data.question_responses.loc[0, "session_id"] == "session-complete"
    assert data.question_responses.loc[0, "task_id"] == "task-question"


def test_nested_values_remain_python_objects_and_timestamps_are_lossless(fixture_path):
    data = load_export(fixture_path)
    event = data.telemetry_events.iloc[0]
    session = data.sessions.iloc[0]
    message = data.messages.loc[data.messages["role"] == "assistant"].iloc[0]

    assert isinstance(event["payload_json"], dict)
    assert isinstance(message["usage"], dict)
    assert session["created_at"] == 1_700_000_000_000_000_000
    assert session["created_at_dt"] == pd.Timestamp("2023-11-14 22:13:20+00:00")
    assert message["created_at_dt"] == pd.Timestamp("2023-11-14 22:13:37+00:00")
    assert event["event_time_dt"] == pd.Timestamp("2023-11-14 22:13:32+00:00")
    assert str(data.session_tasks["position"].dtype) == "Int64"


def test_tolerant_and_strict_historical_sections():
    document = {
        "schema_version": "1.0",
        "exported_at": "2026-01-01T00:00:00+00:00",
        "anonymized": True,
        "timestamp_units": {},
        "sessions": [{"session": {"id": "historical", "created_at": 1, "updated_at": 1}}],
    }
    tolerant = load_export(document)
    assert any("historical sections" in warning for warning in tolerant.validation_warnings)
    assert tolerant.chats.empty
    assert list(tolerant.chats.columns[:3]) == ["session_id", "task_id", "chat_id"]
    with pytest.raises(ExportValidationError, match="historical sections"):
        load_export(document, strict=True)


def test_conflicting_definitions_warn_or_raise(fixture_path):
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(document["sessions"][0])
    duplicate["session"]["id"] = "another-session"
    duplicate["group"]["name"] = "Conflicting name"
    document["sessions"].append(duplicate)

    tolerant = load_export(document)
    assert len(tolerant.groups) == 1
    assert any("Conflicting groups definition" in warning for warning in tolerant.validation_warnings)
    with pytest.raises(ExportValidationError, match="Conflicting groups definition"):
        load_export(document, strict=True)


def test_identified_export_emits_privacy_warning(fixture_path):
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    document["anonymized"] = False
    document["sessions"][0]["participant"]["email"] = "person@example.test"
    with pytest.warns(UserWarning, match="identified"):
        data = load_export(document)
    assert any("identified" in warning for warning in data.validation_warnings)
    assert data.participants.loc[0, "email"] == "person@example.test"
