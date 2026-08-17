from __future__ import annotations

import json

import pandas as pd
import pytest

from research_toolbox import load_export


@pytest.mark.parametrize("format", ["csv", "parquet"])
def test_materializes_selected_tables_with_manifest(fixture_path, tmp_path, format):
    data = load_export(fixture_path)
    output = tmp_path / format
    manifest = data.materialize(output, format=format, tables=["sessions", "telemetry_events"])

    assert (output / f"sessions.{format}").exists()
    assert (output / f"telemetry_events.{format}").exists()
    persisted = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["source"]["schema_version"] == "1.0"
    assert persisted["tables"]["telemetry_events"]["rows"] == 2
    assert "payload_json" in persisted["tables"]["telemetry_events"]["json_encoded_columns"]
    assert manifest == persisted

    if format == "csv":
        events = pd.read_csv(output / "telemetry_events.csv")
        assert json.loads(events.loc[0, "payload_json"])["count"] == 12
    else:
        events = pd.read_parquet(output / "telemetry_events.parquet")
        assert json.loads(events.loc[0, "payload_json"])["count"] == 12


def test_materialization_refuses_overwrite_and_unknown_tables(fixture_path, tmp_path):
    data = load_export(fixture_path)
    data.materialize(tmp_path, format="csv", tables=["sessions"])
    with pytest.raises(FileExistsError):
        data.materialize(tmp_path, format="csv", tables=["sessions"])
    data.materialize(tmp_path, format="csv", tables=["sessions"], overwrite=True)
    with pytest.raises(KeyError, match="Unknown tables"):
        data.materialize(tmp_path / "other", tables=["not_a_table"])
    with pytest.raises(ValueError, match="format"):
        data.materialize(tmp_path / "other", format="xlsx")
