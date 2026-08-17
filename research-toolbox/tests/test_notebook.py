from __future__ import annotations

import json


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
