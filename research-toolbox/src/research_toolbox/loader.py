from __future__ import annotations

import copy
import json
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

from .dataset import ExperimentDataset
from .errors import ExportValidationError, UnsupportedSchemaVersionError
from .normalize import normalize_export


def _read_source(source: str | Path | IO[str] | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return copy.deepcopy(dict(source))

    try:
        if hasattr(source, "read"):
            document = json.load(source)
        else:
            with Path(source).expanduser().open(encoding="utf-8") as handle:
                document = json.load(handle)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ExportValidationError(f"Could not read experiment export: {exc}") from exc

    if not isinstance(document, dict):
        raise ExportValidationError("The export root must be a JSON object.")
    return document


def _validate_envelope(document: dict[str, Any]) -> None:
    missing = [
        key
        for key in ("schema_version", "exported_at", "anonymized", "timestamp_units", "sessions")
        if key not in document
    ]
    if missing:
        raise ExportValidationError(f"The export envelope is missing required fields: {', '.join(missing)}.")
    version = document.get("schema_version")
    if not isinstance(version, (str, int, float)):
        raise ExportValidationError("The export must include schema_version.")
    major_text = str(version).split(".", 1)[0]
    if not major_text.isdigit() or int(major_text) != 1:
        raise UnsupportedSchemaVersionError(f"Unsupported export schema_version {version!r}; supported major: 1.")
    if not isinstance(document["exported_at"], str) or not document["exported_at"]:
        raise ExportValidationError("The exported_at field must be a non-empty string.")
    if not isinstance(document["timestamp_units"], Mapping):
        raise ExportValidationError("The timestamp_units field must be an object.")
    if not isinstance(document["sessions"], list):
        raise ExportValidationError("The export must include a sessions list.")
    if not isinstance(document["anonymized"], bool):
        raise ExportValidationError("The anonymized field must be a boolean.")


def load_export(
    source: str | Path | IO[str] | Mapping[str, Any],
    *,
    strict: bool = False,
) -> ExperimentDataset:
    """Load and normalize one full-session export.

    ``strict=False`` tolerates missing historical sections and records validation
    warnings. Structural envelope problems and unsupported major versions always
    raise an exception.
    """

    document = _read_source(source)
    _validate_envelope(document)
    tables, validation_warnings = normalize_export(document, strict=strict)

    if document.get("anonymized") is False:
        message = (
            "This export is identified. The toolbox does not anonymize free-form content "
            "or identifiers; handle results as sensitive data."
        )
        validation_warnings.append(message)
        warnings.warn(message, UserWarning, stacklevel=2)

    metadata = {key: copy.deepcopy(value) for key, value in document.items() if key != "sessions"}
    return ExperimentDataset(
        raw=document,
        metadata=metadata,
        tables=tables,
        validation_warnings=validation_warnings,
    )
