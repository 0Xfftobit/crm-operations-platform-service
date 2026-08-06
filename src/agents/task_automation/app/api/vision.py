"""Vision API — mission, KPIs, action ledger, DLQ replay."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_api_key
from app.dlq import list_dlq
from app.events import consume as consume_events
from app.exports.ledger_export import export_ledger
from app.ledger import list_ledger
from app.rbac import Principal, require_scope
from app.service import build_kpis, replay_dlq_entry, vision_overview

router = APIRouter(tags=["vision"])
Auth = Annotated[str, Depends(require_api_key)]
ReplayAuth = Annotated[Principal, Depends(require_scope("dlq.replay"))]


@router.get("/vision", summary="Engine vision — purpose, scope, alignment")
def vision(_: Auth) -> dict[str, Any]:
    return vision_overview()


@router.get("/vision/kpis", summary="Success metrics vs vision targets (§5)")
def kpis(_: Auth) -> dict[str, Any]:
    return build_kpis()


@router.get("/vision/ledger", summary="Immutable action ledger — full provenance")
def ledger(
    _: Auth,
    lead_id: str | None = Query(None),
    limit: int = Query(100, le=500),
) -> dict[str, Any]:
    return {"entries": list_ledger(lead_id=lead_id, limit=limit)}


@router.get("/vision/events", summary="Emitted platform events")
def platform_events(_: Auth, topic: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"events": consume_events(topic=topic, limit=limit)}


@router.get("/vision/dlq", summary="Dead-letter queue — failed actions")
def dead_letter_queue(_: Auth, limit: int = 50, status: str | None = None) -> dict[str, Any]:
    return {"entries": list_dlq(limit=limit, status=status)}


@router.get("/vision/ledger/export", summary="Export ledger to local file / optional S3")
def ledger_export(
    _: Auth,
    lead_id: str | None = Query(None),
    limit: int = Query(1000, le=5000),
) -> dict[str, Any]:
    return export_ledger(lead_id=lead_id, limit=limit)


@router.post("/vision/dlq/{dlq_id}/replay", summary="Replay DLQ entry (one-click recover)")
def replay_dlq(dlq_id: str, _: ReplayAuth) -> dict[str, Any]:
    result = replay_dlq_entry(dlq_id)
    if result.get("error") == "dlq_not_found":
        raise HTTPException(status_code=404, detail="dlq entry not found")
    if result.get("error") == "already_replayed":
        raise HTTPException(status_code=409, detail="already replayed")
    return result
