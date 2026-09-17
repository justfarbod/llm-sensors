from __future__ import annotations

import json

import pytest


def test_notebook_is_clean_and_executable(project_root):
    nbformat = __import__("nbformat")
    notebook_path = project_root / "notebooks" / "full_session_analysis.ipynb"
    notebook = nbformat.read(notebook_path, as_version=4)
    assert all(cell.get("execution_count") is None for cell in notebook.cells if cell.cell_type == "code")
    assert all(not cell.get("outputs") for cell in notebook.cells if cell.cell_type == "code")

    nbclient = __import__("nbclient")
    client = nbclient.NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(project_root)}},
    )
    client.execute()


def test_synthetic_fixture_contains_no_explicit_account_fields(fixture_path):
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert document["anonymized"] is True
    serialized = fixture_path.read_text(encoding="utf-8")
    assert '"email"' not in serialized
    assert '"username"' not in serialized
    assert '"path"' not in serialized


@pytest.mark.parametrize("variant", ["current", "sparse", "empty", "historical"])
def test_notebook_runs_with_participant_exports(project_root, fixture_path, tmp_path, monkeypatch, variant):
    import nbclient
    import nbformat

    document = json.loads(fixture_path.read_text())
    if variant == "empty":
        document["sessions"] = []
    elif variant == "sparse":
        document["sessions"] = [document["sessions"][1]]
        document["sessions"][0]["tasks"] = []
    elif variant == "historical":
        document["sessions"] = [{"session": {"id": "historical-run"}}]
    else:
        # Ensure the tutorial uses the selected export, not the bundled session ID.
        serialized = json.dumps(document).replace("session-complete", "participant-run-42")
        document = json.loads(serialized)
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps(document))
    monkeypatch.setenv("RESEARCH_EXPORT_PATH", str(export_path))
    notebook = nbformat.read(project_root / "notebooks" / "full_session_analysis.ipynb", as_version=4)
    assertions = "assert len(overview) == len(data.raw['sessions'])\n"
    if variant == "current":
        assertions += (
            "assert SESSION_ID == 'participant-run-42'\n"
            "assert response_details['title'].notna().all()\n"
            "assert not timeline.empty\n"
            "assert overview.loc[0, 'workflow_id'] == 'workflow-transport'\n"
        )
    notebook.cells.append(nbformat.v4.new_code_cell(assertions))
    nbclient.NotebookClient(
        notebook, timeout=120, kernel_name="python3",
        resources={"metadata": {"path": str(project_root.parent)}},
    ).execute()
