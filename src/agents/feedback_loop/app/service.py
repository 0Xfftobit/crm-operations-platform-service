"""Orchestration layer that wires P0–P3 subsystems together."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app import abtest, aggregator, collector, drift, label_builder, model_retrainer
from app.core.config import get_settings
from app.domain.models import Feedback, Outcome, Prediction
from app.repositories import (
    get_dataset_repository,
    get_drift_repository,
    get_experiment_repository,
    get_feedback_repository,
    get_label_repository,
    get_model_repository,
    get_outcome_repository,
    get_prediction_repository,
    get_retrain_run_repository,
    reset_repositories,
)


def _now() -> datetime:
    return datetime.now(UTC)


def reset_state() -> None:
    """Clear all in-memory state (used by tests)."""
    reset_repositories()
    drift.reset_drift_references()


def ingest_prediction(prediction: Prediction) -> dict[str, Any]:
    return collector.log_prediction(prediction)


def ingest_outcome(outcome: Outcome) -> dict[str, Any]:
    return collector.record_outcome(outcome)


def ingest_feedback(feedback: Feedback) -> dict[str, Any]:
    if feedback.feedback_type == "override":
        return collector.record_override(feedback)
    return collector.record_explicit_feedback(feedback)


def get_labels(
    engine_name: str | None = None,
    prediction_id: str | None = None,
) -> list[dict[str, Any]]:
    repo = get_label_repository()
    if prediction_id:
        return [lab.model_dump(mode="json") for lab in repo.get_for_prediction(prediction_id)]
    return [lab.model_dump(mode="json") for lab in repo.list(engine_name=engine_name)]


def build_all_labels() -> list[dict[str, Any]]:
    return [lab.model_dump(mode="json") for lab in label_builder.build_all_labels()]


def get_metrics(engine_name: str, *, window_days: int | None = None) -> dict[str, Any]:
    return aggregator.engine_metrics(engine_name, window_days=window_days)


def get_all_metrics() -> dict[str, dict[str, Any]]:
    return aggregator.all_engine_metrics()


def evaluate_drift(engine_name: str) -> dict[str, Any]:
    settings = get_settings()
    records = drift.drift_evaluate_all(engine_name)
    trigger, features = drift.should_trigger_retrain(engine_name)
    if trigger and settings.auto_retrain_enabled:
        result = model_retrainer.retrain_pipeline(engine_name, trigger="drift", auto=True)
    else:
        result = {"triggered": False, "features": features}
    return {
        "records": [r.model_dump(mode="json") for r in records],
        "retrain_triggered": trigger,
        "retrain_features": features,
        "retrain_result": result,
    }


def get_drift_records(engine_name: str | None = None) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in get_drift_repository().list(engine_name=engine_name)]


def trigger_retrain(engine_name: str, trigger: str = "manual") -> dict[str, Any]:
    return model_retrainer.retrain_pipeline(engine_name, trigger=trigger)


def performance_retrain_needed(engine_name: str) -> tuple[bool, dict[str, Any]]:
    """Return True if current engine metrics have dropped below the configured threshold."""
    settings = get_settings()
    metrics = aggregator.engine_metrics(engine_name)
    reasons: list[str] = []
    for key in ("accuracy", "auc"):
        value = metrics.get(key)
        if value is not None and value < settings.accuracy_threshold:
            reasons.append(f"{key}_below_threshold")
    return bool(reasons), {"metrics": metrics, "reasons": reasons}


def scheduled_retrain_due(engine_name: str, *, cadence_days: int = 30) -> tuple[bool, float]:
    """Check whether the last scheduled retrain for an engine is older than cadence_days."""
    runs = [r for r in get_retrain_run_repository().list(engine_name=engine_name) if r.trigger == "scheduled"]
    if not runs:
        return True, float("inf")
    last = max(runs, key=lambda r: r.created_at)
    hours_since = (datetime.now(UTC) - last.created_at) / timedelta(hours=1)
    return hours_since >= cadence_days * 24, hours_since


def auto_retrain(engine_name: str) -> dict[str, Any]:
    """Evaluate performance and scheduled triggers and run a retraining pipeline if needed."""
    perf_needed, perf_info = performance_retrain_needed(engine_name)
    if perf_needed:
        return model_retrainer.retrain_pipeline(engine_name, trigger="performance", auto=True)

    scheduled_due, hours = scheduled_retrain_due(engine_name)
    if scheduled_due:
        return model_retrainer.retrain_pipeline(engine_name, trigger="scheduled", auto=True)

    return {
        "status": "skipped",
        "reason": "no_trigger",
        "performance": perf_info,
        "scheduled_hours_since": hours if not scheduled_due else 0.0,
    }


def promote_model(model_id: str) -> dict[str, Any]:
    return model_retrainer.promote_model(model_id)


def rollback_model(engine_name: str) -> dict[str, Any]:
    return model_retrainer.rollback(engine_name)


def get_models(engine_name: str | None = None) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in get_model_repository().list(engine_name=engine_name)]


def get_model_lineage(model_id: str) -> dict[str, Any]:
    return model_retrainer.model_lineage(model_id).model_dump(mode="json")


def get_datasets(engine_name: str | None = None) -> list[dict[str, Any]]:
    return [d.model_dump(mode="json") for d in get_dataset_repository().list(engine_name=engine_name)]


def get_capture_stats(engine_name: str | None = None) -> dict[str, Any]:
    return {
        "predictions": len(get_prediction_repository().list(engine_name=engine_name)),
        "outcomes": len(get_outcome_repository().list(engine_name=engine_name)),
        "feedback": len(get_feedback_repository().list(engine_name=engine_name)),
        "labels": len(get_label_repository().list(engine_name=engine_name)),
        "match_rate": _match_rate(engine_name),
    }


def _match_rate(engine_name: str | None) -> float:
    preds = get_prediction_repository().list(engine_name=engine_name)
    if not preds:
        return 0.0
    labeled = sum(1 for p in preds if get_label_repository().get_for_prediction(p.prediction_id))
    return round(labeled / len(preds), 4)


def get_experiment(experiment_id: str) -> Any:
    return get_experiment_repository().get(experiment_id)


def create_experiment(config: dict[str, Any]) -> dict[str, Any]:
    exp = abtest.create_experiment(**config)
    return exp.model_dump(mode="json")


def assign_experiment_variant(experiment_id: str, entity_id: str) -> dict[str, Any]:
    return {"variant": abtest.assign_variant(experiment_id, entity_id)}


def record_experiment_result(
    experiment_id: str,
    variant: str,
    entity_id: str,
    metric_value: float,
    guardrail_value: float | None = None,
) -> dict[str, Any]:
    return abtest.record_result(experiment_id, variant, entity_id, metric_value, guardrail_value=guardrail_value)


def evaluate_experiment(experiment_id: str) -> dict[str, Any]:
    return abtest.evaluate_experiment(experiment_id)


def promote_experiment_winner(experiment_id: str, *, force: bool = False) -> dict[str, Any]:
    return abtest.promote_winner(experiment_id, force=force)


def export_labeled_dataset(engine_name: str) -> dict[str, Any]:
    """Export a clean, structured labeled dataset for training or audit."""
    labels = get_label_repository().list(engine_name=engine_name)
    rows: list[dict[str, Any]] = []
    for lab in labels:
        pred = get_prediction_repository().get(lab.prediction_id)
        if pred is None:
            continue
        rows.append(
            {
                "prediction_id": lab.prediction_id,
                "entity_id": lab.entity_id,
                "engine_name": lab.engine_name,
                "model_version": pred.model_version,
                "features_ref": lab.features_ref,
                "features": pred.features,
                "output": pred.output,
                "label_type": lab.label_type,
                "label_value": lab.label_value,
                "label_source": lab.label_source,
                "observed_at": lab.observed_at.isoformat(),
                "in_window": lab.in_window,
            }
        )
    return {"engine_name": engine_name, "rows": rows, "count": len(rows)}


def get_retraining_log(engine_name: str | None = None) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in get_retrain_run_repository().list(engine_name=engine_name)]
