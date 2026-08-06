"""System architecture API — components, pipeline, observability (§2–§10)."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.architecture import build_architecture_overview, build_observability_snapshot
from app.messaging.kafka_bus import consume_kafka
from app.pipeline import init_pipeline, run_pipeline_stages
from app.rbac import Principal, require_scope

router = APIRouter(tags=["architecture"])
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]
WriteAuth = Annotated[Principal, Depends(require_scope("events.ingest"))]


@router.get("/architecture", summary="Architecture overview + component status (§1–§9)")
def architecture_overview(_: ReadAuth) -> dict[str, Any]:
    return build_architecture_overview()


@router.get("/architecture/components", summary="Detailed component + store status")
def architecture_components(_: ReadAuth) -> dict[str, Any]:
    from app.components.registry import component_details
    return component_details()


@router.get("/architecture/observability", summary="Golden signals, traces, Kafka (§10)")
def observability(_: ReadAuth) -> dict[str, Any]:
    snap = build_observability_snapshot()
    snap["kafka_messages"] = consume_kafka(limit=20)
    return snap


@router.post("/architecture/pipeline/init", summary="Initialize persistence schema")
def pipeline_init(_: WriteAuth) -> dict[str, Any]:
    return init_pipeline()


@router.post("/architecture/pipeline/dry-run", summary="Run pipeline stages without side effects")
def pipeline_dry_run(
    _: WriteAuth,
    topic: str = "deal.stage_changed",
    lead_id: str = "L-arch",
    stage: str = "pre_approval",
) -> dict[str, Any]:
    return run_pipeline_stages(
        topic=topic,
        payload={"lead_id": lead_id, "stage": stage, "agent_id": "lo:amy"},
        dry_run=True,
    )
