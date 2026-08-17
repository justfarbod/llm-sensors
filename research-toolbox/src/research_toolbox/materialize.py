from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd


def _is_nested(value: Any) -> bool:
    return isinstance(value, (dict, list, tuple))


def _json_value(value: Any) -> str | Any:
    if _is_nested(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return value


def _serializable_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    result = frame.copy()
    nested_columns = [
        column
        for column in result.columns
        if result[column].dtype == "object" and result[column].map(_is_nested, na_action="ignore").any()
    ]
    for column in nested_columns:
        result[column] = result[column].map(_json_value, na_action="ignore").astype("string")
    return result, nested_columns


def materialize_tables(
    tables: Mapping[str, pd.DataFrame],
    metadata: Mapping[str, Any],
    validation_warnings: list[str],
    directory: str | Path,
    *,
    format: str = "parquet",
    selected_tables: Iterable[str] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if format not in {"parquet", "csv"}:
        raise ValueError("format must be 'parquet' or 'csv'.")
    names = list(selected_tables) if selected_tables is not None else list(tables)
    unknown = [name for name in names if name not in tables]
    if unknown:
        raise KeyError(f"Unknown tables: {', '.join(unknown)}")

    target = Path(directory).expanduser()
    expected = [target / f"{name}.{format}" for name in names]
    expected.append(target / "manifest.json")
    existing = [path for path in expected if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing materialized output: {existing[0]}")
    target.mkdir(parents=True, exist_ok=True)

    table_manifest: dict[str, Any] = {}
    for name in names:
        frame, nested_columns = _serializable_frame(tables[name])
        destination = target / f"{name}.{format}"
        if format == "parquet":
            frame.to_parquet(destination, index=False)
        else:
            frame.to_csv(destination, index=False)
        table_manifest[name] = {
            "file": destination.name,
            "rows": len(frame),
            "columns": list(frame.columns),
            "json_encoded_columns": nested_columns,
        }

    manifest = {
        "toolbox_schema_version": "1.0",
        "source": dict(metadata),
        "format": format,
        "validation_warnings": list(validation_warnings),
        "tables": table_manifest,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest
