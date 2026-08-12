"""ORM tables for PAS.

Operational rows use a hybrid of key columns + a JSON ``data`` blob holding the full pydantic model,
so the DB schema stays stable as models evolve while key columns keep queries indexable.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class _KV(Base):
    """Abstract single-key hybrid row: string key + JSON model blob."""

    __abstract__ = True
    k: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class TouchRow(Base):
    __tablename__ = "touch"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    touch_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class OutcomeRow(Base):
    __tablename__ = "outcome"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class CampaignCostRow(Base):
    __tablename__ = "campaign_cost"
    cost_id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    period: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class FinanceSourceRow(Base):
    __tablename__ = "finance_source"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class MetricDefinitionRow(Base):
    __tablename__ = "metric_definition"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class AttributionRow(Base):
    __tablename__ = "attribution"
    deal_id: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReportScheduleRow(_KV):
    __tablename__ = "report_schedule"


class ReconciliationRow(Base):
    __tablename__ = "reconciliation"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    period: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class AlertRow(Base):
    __tablename__ = "alert"
    alert_id: Mapped[str] = mapped_column(String, primary_key=True)
    metric: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class KpiSnapshotRow(Base):
    __tablename__ = "kpi_snapshot"
    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    metric_name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class ExperimentResultRow(Base):
    __tablename__ = "experiment_result"
    experiment_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class EventLogRow(Base):
    __tablename__ = "event_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
