from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd

from . import analysis
from .materialize import materialize_tables


@dataclass(slots=True)
class ExperimentDataset:
    """A lossless export plus its normalized, analysis-ready tables."""

    raw: dict[str, Any]
    metadata: dict[str, Any]
    _tables: dict[str, pd.DataFrame]
    validation_warnings: list[str]

    def __init__(
        self,
        *,
        raw: dict[str, Any],
        metadata: dict[str, Any],
        tables: dict[str, pd.DataFrame],
        validation_warnings: list[str],
    ) -> None:
        self.raw = raw
        self.metadata = metadata
        self._tables = tables
        self.validation_warnings = validation_warnings

    @property
    def tables(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType(self._tables)

    def table(self, name: str) -> pd.DataFrame:
        try:
            return self._tables[name]
        except KeyError as exc:
            raise KeyError(f"Unknown table {name!r}. Available: {', '.join(self._tables)}") from exc

    def __getattr__(self, name: str) -> pd.DataFrame:
        tables = object.__getattribute__(self, "_tables")
        if name in tables:
            return tables[name]
        raise AttributeError(name)

    def session_overview(self) -> pd.DataFrame:
        return analysis.session_overview(self)

    def task_progress(self) -> pd.DataFrame:
        return analysis.task_progress(self)

    def condition_summary(self, metrics: Iterable[str] | None = None) -> pd.DataFrame:
        return analysis.condition_summary(self, metrics)

    def question_score_summary(self) -> pd.DataFrame:
        return analysis.question_score_summary(self)

    def survey_response_summary(self) -> pd.DataFrame:
        return analysis.survey_response_summary(self)

    def essay_summary(self) -> pd.DataFrame:
        return analysis.essay_summary(self)

    def chat_usage_summary(self) -> pd.DataFrame:
        return analysis.chat_usage_summary(self)

    def telemetry_timeline(self, session_id: str | None = None) -> pd.DataFrame:
        return analysis.telemetry_timeline(self, session_id)

    def session_timeline(self, session_id: str | None = None) -> pd.DataFrame:
        return analysis.session_timeline(self, session_id)

    def materialize(
        self,
        directory: str | Path,
        *,
        format: str = "parquet",
        tables: Iterable[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return materialize_tables(
            self._tables,
            self.metadata,
            self.validation_warnings,
            directory,
            format=format,
            selected_tables=tables,
            overwrite=overwrite,
        )
