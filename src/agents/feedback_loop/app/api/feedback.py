"""Feedback Loop System REST API."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import service
from app.api.deps import require_api_key
from app.domain.models import Feedback, Outcome, Prediction

router = APIRouter(tags=["feedback-loop"])
Auth = Annotated[None, Depends(require_api_key)]


# ── Capture ──────────────────────────────────────────────────────────────────
@router.post("/predictions", summary="Log a prediction (FR-001)")
def post_prediction(prediction: Prediction, _: Auth) -> dict[str, Any]:
    return service.ingest_prediction(prediction)


@router.post("/outcomes", summary="Record an outcome event (FR-002/003)")
def post_outcome(outcome: Outcome, _: Auth) -> dict[str, Any]:
    return service.ingest_outcome(outcome)


@router.post("/feedback", summary="Record override or explicit feedback (FR-004/005)")
def post_feedback(feedback: Feedback, _: Auth) -> dict[str, Any]:
    return service.ingest_feedback(feedback)


# ── Labels ───────────────────────────────────────────────────────────────────
@router.get("/labels", summary="List labels")
def get_labels(
    engine_name: str | None = None,
    prediction_id: str | None = None,
) -> list[dict[str, Any]]:
    return service.get_labels(engine_name=engine_name, prediction_id=prediction_id)


@router.post("/labels/build", summary="Batch build labels")
def build_labels(_: Auth) -> list[dict[str, Any]]:
    return service.build_all_labels()


# ── Performance metrics ──────────────────────────────────────────────────────
@router.get("/metrics/{engine_name}", summary="Metrics for one engine (FR-009)")
def get_metrics(
    engine_name: str,
    window_days: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    return service.get_metrics(engine_name, window_days=window_days)


@router.get("/metrics", summary="Metrics for all engines")
def get_all_metrics() -> dict[str, dict[str, Any]]:
    return service.get_all_metrics()


# ── Drift ────────────────────────────────────────────────────────────────────
@router.post("/drift/evaluate/{engine_name}", summary="Evaluate drift and trigger retrain (FR-008/010)")
def evaluate_drift(engine_name: str, _: Auth) -> dict[str, Any]:
    return service.evaluate_drift(engine_name)


@router.get("/drift", summary="Drift records")
def get_drift(engine_name: str | None = None) -> list[dict[str, Any]]:
    return service.get_drift_records(engine_name=engine_name)


# ── Retraining / registry ────────────────────────────────────────────────────
@router.post("/retrain/{engine_name}", summary="Trigger a retraining run (FR-011/012)")
def retrain(
    engine_name: str,
    _: Auth,
    trigger: str = Query(default="manual"),
) -> dict[str, Any]:
    return service.trigger_retrain(engine_name, trigger=trigger)


@router.post("/retrain/{engine_name}/auto", summary="Auto-retrain on performance or scheduled trigger")
def auto_retrain(engine_name: str, _: Auth) -> dict[str, Any]:
    return service.auto_retrain(engine_name)


@router.get("/retrain/{engine_name}/triggers", summary="Check retrain trigger state")
def retrain_triggers(engine_name: str, _: Auth) -> dict[str, Any]:
    perf_needed, perf_info = service.performance_retrain_needed(engine_name)
    scheduled_due, hours = service.scheduled_retrain_due(engine_name)
    return {
        "performance_trigger_needed": perf_needed,
        "performance": perf_info,
        "scheduled_due": scheduled_due,
        "scheduled_hours_since": hours,
    }


@router.post("/models/{model_id}/promote", summary="Promote a gated challenger")
def promote_model(model_id: str, _: Auth) -> dict[str, Any]:
    try:
        return service.promote_model(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/rollback/{engine_name}", summary="Roll back to previous production model")
def rollback(engine_name: str, _: Auth) -> dict[str, Any]:
    return service.rollback_model(engine_name)


@router.get("/models", summary="List model versions")
def list_models(engine_name: str | None = None) -> list[dict[str, Any]]:
    return service.get_models(engine_name=engine_name)


@router.get("/models/{model_id}/lineage", summary="Model lineage report (FR-018)")
def model_lineage(model_id: str) -> dict[str, Any]:
    try:
        return service.get_model_lineage(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets", summary="List training datasets")
def list_datasets(engine_name: str | None = None) -> list[dict[str, Any]]:
    return service.get_datasets(engine_name=engine_name)


# ── A/B tests ────────────────────────────────────────────────────────────────
class ExperimentCreate(BaseModel):
    name: str
    hypothesis: str
    engine_name: str
    experiment_type: str | None = None
    variants: list[dict[str, Any]]
    traffic_split: dict[str, float] | None = None
    audience_segment: str | None = None
    success_metric: str = "conversion"
    guardrail_metric: str | None = None
    guardrail_threshold: float | None = None
    p_value_threshold: float = 0.05
    min_duration_hours: float = 24.0


@router.post("/experiments", summary="Create an A/B experiment")
def create_experiment(body: ExperimentCreate, _: Auth) -> dict[str, Any]:
    return service.create_experiment(body.model_dump())


@router.get("/experiments/{experiment_id}", summary="Get experiment")
def get_experiment(experiment_id: str) -> dict[str, Any]:
    exp = service.get_experiment(experiment_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="unknown experiment")
    return exp.model_dump(mode="json")


@router.post("/experiments/{experiment_id}/assign", summary="Assign a variant to an entity")
def assign_variant(experiment_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return service.assign_experiment_variant(experiment_id, body.get("entity_id", ""))


class RecordResult(BaseModel):
    variant: str
    entity_id: str
    metric_value: float
    guardrail_value: float | None = None


@router.post("/experiments/{experiment_id}/record", summary="Record an experiment result")
def record_result(experiment_id: str, body: RecordResult, _: Auth) -> dict[str, Any]:
    return service.record_experiment_result(
        experiment_id,
        body.variant,
        body.entity_id,
        body.metric_value,
        guardrail_value=body.guardrail_value,
    )


@router.post("/experiments/{experiment_id}/evaluate", summary="Evaluate experiment")
def evaluate_experiment(experiment_id: str, _: Auth) -> dict[str, Any]:
    return service.evaluate_experiment(experiment_id)


class PromoteRequest(BaseModel):
    force: bool = False


@router.post("/experiments/{experiment_id}/promote", summary="Promote the winning variant")
def promote_winner(experiment_id: str, body: PromoteRequest, _: Auth) -> dict[str, Any]:
    try:
        return service.promote_experiment_winner(experiment_id, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Stats / export ───────────────────────────────────────────────────────────
@router.get("/stats", summary="Capture statistics")
def get_stats(engine_name: str | None = None) -> dict[str, Any]:
    return service.get_capture_stats(engine_name=engine_name)


@router.get("/retraining-log", summary="Retraining history")
def get_retraining_log(engine_name: str | None = None) -> list[dict[str, Any]]:
    return service.get_retraining_log(engine_name=engine_name)


@router.get("/export/{engine_name}", summary="Export labeled dataset")
def export_dataset(engine_name: str) -> dict[str, Any]:
    return service.export_labeled_dataset(engine_name)
