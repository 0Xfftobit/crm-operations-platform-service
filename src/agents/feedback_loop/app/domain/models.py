"""Pydantic domain models for the Feedback Loop System."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Prediction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prediction_id: str = Field(default_factory=lambda: uuid4().hex)
    engine_name: str
    model_version: str = "0.1.0"
    entity_id: str
    features_ref: str
    features: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any]
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tenant_id: str = "default"


class Outcome(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outcome_id: str = Field(default_factory=lambda: uuid4().hex)
    prediction_id: str | None = None
    entity_id: str
    engine_name: str | None = None
    event_type: str
    metric_value: float | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Feedback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    feedback_id: str = Field(default_factory=lambda: uuid4().hex)
    prediction_id: str | None = None
    entity_id: str
    engine_name: str
    feedback_type: str = "explicit"  # explicit | override
    original_output: dict[str, Any] | None = None
    chosen_action: str | None = None
    reason: str | None = None
    label_value: float | str | None = None  # 1.0 / 0.0 or "positive" / "negative"
    actor: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Label(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label_id: str = Field(default_factory=lambda: uuid4().hex)
    prediction_id: str
    entity_id: str
    engine_name: str
    label_type: str  # funded | lost | replied | override | explicit
    label_value: Any
    label_source: str  # outcome_event | human_override | explicit
    observed_at: datetime
    in_window: bool = True
    features_ref: str
    outcome_id: str | None = None
    feedback_id: str | None = None


class TrainingDataset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dataset_id: str = Field(default_factory=lambda: uuid4().hex)
    engine_name: str
    snapshot_uri: str
    row_count: int
    feature_names: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lineage: dict[str, Any] = Field(default_factory=dict)


class ModelVersion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model_id: str
    engine_name: str
    version: str
    dataset_id: str
    artifact_path: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    gate: str = "unknown"  # passed | failed | unknown
    status: str = "challenger"  # challenger | shadow | canary | production | archived | rejected | rollback
    promoted_at: datetime | None = None
    lineage: dict[str, Any] = Field(default_factory=dict)


class DriftRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str = Field(default_factory=lambda: uuid4().hex)
    engine_name: str
    feature: str
    psi: float
    reference_window: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    alert: bool = False


class ExperimentVariant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    model_version: str | None = None
    traffic_pct: float


class Experiment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    hypothesis: str
    engine_name: str
    experiment_type: str | None = None  # model | message | sequence | offer
    variants: list[ExperimentVariant] = Field(default_factory=list)
    traffic_split: dict[str, float] = Field(default_factory=dict)
    audience_segment: str | None = None
    success_metric: str = "conversion"
    guardrail_metric: str | None = None
    guardrail_threshold: float | None = None
    p_value_threshold: float = 0.05
    min_duration_hours: float = 24.0
    status: str = "running"  # running | stopped | completed
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    results: dict[str, Any] = Field(default_factory=dict)


class RetrainingRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    engine_name: str
    trigger: str
    dataset_id: str | None = None
    model_id: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    gate: str = "unknown"
    outcome: str = "unknown"  # promoted | rejected | rollback
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LineageReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model_id: str
    engine_name: str
    version: str
    training_dataset: str
    snapshot_uri: str
    feature_versions: list[str]
    eval: dict[str, Any]
    promoted_at: datetime | None
    gate: str
