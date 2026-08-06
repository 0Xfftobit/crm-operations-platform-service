"""Pydantic domain models for the Performance Analytics System."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TouchEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    touch_id: str
    contact_pid: str
    deal_id: str | None = None
    rep_id: str | None = None
    team_id: str | None = None
    tenant_id: str = "default"
    channel: str
    sequence_id: str | None = None
    campaign_id: str | None = None
    tracking_id: str | None = None
    source: str | None = None
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    engaged: bool = False
    opened: bool = False
    clicked: bool = False
    replied: bool = False
    opted_out: bool = False
    delivery_status: str = "delivered"  # delivered | failed | bounced
    cost: float | None = None
    segment: str | None = None
    stage: str | None = None
    property_type: str | None = None
    area: str | None = None


class OutcomeEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    outcome_id: str
    deal_id: str | None = None
    contact_pid: str
    rep_id: str | None = None
    team_id: str | None = None
    tenant_id: str = "default"
    stage: str
    amount: float | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: str  # loan.application.created | loan.funded | deal.lost | stage.progressed
    source: str | None = None


class CampaignCost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cost_id: str = Field(default_factory=lambda: uuid4().hex)
    campaign_id: str
    period: str | None = None
    cost: float
    currency: str = "USD"
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FinanceSourceOfTruth(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    period: str
    funded_amount: float
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    version: int = 1
    label: str
    metric_type: str = "ratio"  # ratio | count | rate | average
    numerator: str | None = None
    denominator: str | None = None
    description: str = ""
    owner: str = "analytics-eng"
    dimensions: list[str] = Field(default_factory=list)
    formula: str | None = None
    lineage: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "published"  # published | draft | deprecated


class ReportSchedule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    report_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    metric: str
    report_format: str = "csv"  # csv | pdf
    period: str
    recipients: list[str] = Field(default_factory=list)
    frequency: str = "weekly"  # daily | weekly | monthly
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReconciliationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    period: str
    mart_amount: float
    finance_amount: float
    delta: float
    passed: bool
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AttributionCredit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    touch_id: str
    channel: str
    source: str | None = None
    credit: float
    touch_at: datetime
    days_before_conversion: float | None = None


class AttributionRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    deal_id: str
    funded_amount: float
    model: str
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    credits: list[AttributionCredit] = Field(default_factory=list)


class AlertRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alert_id: str = Field(default_factory=lambda: uuid4().hex)
    metric: str
    dimension: str | None = None
    value: float
    expected: float | None = None
    zscore: float | None = None
    severity: str = "warning"  # warning | critical
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class KpiSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    metric_name: str
    version: int
    period: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExperimentGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    entity_ids: list[str] = Field(default_factory=list)


class ExperimentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    treatment_rate: float
    control_rate: float
    lift: float
    relative_lift: float
    p_value: float | None = None
    significant: bool = False
    treatment_n: int
    control_n: int
    treatment_funded: int
    control_funded: int
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
