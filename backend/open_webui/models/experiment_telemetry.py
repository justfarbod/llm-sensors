import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Float, Index, Integer, Text, UniqueConstraint

from open_webui.internal.db import Base, JSONField


class ExperimentTelemetryEvent(Base):
    __tablename__ = 'experiment_telemetry_event'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    experiment_session_id = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    event_time = Column(BigInteger, nullable=False)
    field_context = Column(Text, nullable=False)
    payload_json = Column(JSONField, nullable=False)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_experiment_telemetry_event_session_time', 'experiment_session_id', 'event_time'),
        Index('ix_experiment_telemetry_event_user_session', 'user_id', 'experiment_session_id'),
    )


class ExperimentTelemetrySummary(Base):
    __tablename__ = 'experiment_telemetry_summary'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    experiment_session_id = Column(Text, nullable=False)
    total_keystrokes = Column(Integer, nullable=False, default=0)
    avg_inter_key_interval_ms = Column(Float, nullable=True)
    avg_key_hold_duration_ms = Column(Float, nullable=True)
    pause_count = Column(Integer, nullable=False, default=0)
    longest_pause_ms = Column(Integer, nullable=False, default=0)
    copy_count = Column(Integer, nullable=False, default=0)
    cut_count = Column(Integer, nullable=False, default=0)
    paste_count = Column(Integer, nullable=False, default=0)
    total_pasted_chars = Column(Integer, nullable=False, default=0)
    tab_switch_count = Column(Integer, nullable=False, default=0)
    total_time_away_ms = Column(BigInteger, nullable=False, default=0)
    inter_key_interval_total_ms = Column(BigInteger, nullable=False, default=0)
    inter_key_interval_sample_count = Column(Integer, nullable=False, default=0)
    key_hold_duration_total_ms = Column(BigInteger, nullable=False, default=0)
    key_hold_duration_sample_count = Column(Integer, nullable=False, default=0)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint('experiment_session_id', name='uq_experiment_telemetry_summary_session'),
        Index('ix_experiment_telemetry_summary_user_session', 'user_id', 'experiment_session_id'),
    )


class ExperimentTelemetrySummaryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_keystrokes: int = 0
    avg_inter_key_interval_ms: Optional[float] = None
    avg_key_hold_duration_ms: Optional[float] = None
    pause_count: int = 0
    longest_pause_ms: int = 0
    copy_count: int = 0
    cut_count: int = 0
    paste_count: int = 0
    total_pasted_chars: int = 0
    tab_switch_count: int = 0
    total_time_away_ms: int = 0


def empty_summary(session_id: str, user_id: str) -> ExperimentTelemetrySummary:
    now = int(time.time_ns())
    return ExperimentTelemetrySummary(
        id=session_id,
        user_id=user_id,
        experiment_session_id=session_id,
        total_keystrokes=0,
        pause_count=0,
        longest_pause_ms=0,
        copy_count=0,
        cut_count=0,
        paste_count=0,
        total_pasted_chars=0,
        tab_switch_count=0,
        total_time_away_ms=0,
        inter_key_interval_total_ms=0,
        inter_key_interval_sample_count=0,
        key_hold_duration_total_ms=0,
        key_hold_duration_sample_count=0,
        created_at=now,
        updated_at=now,
    )
