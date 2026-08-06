"""Audit Trail API (CEAAPSS-566) — search, single-record lookup, tamper verification, and
RBAC-gated export (CSV / JSON). The ledger itself is append-only; there is no write path here.
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response

from app import audit, pdf, service
from app.api.deps import require_api_key, require_export_role

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/search", summary="Search the audit trail")
def search(
    entity_id: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
    engine: str | None = None,
    frm: datetime | None = Query(None, description="occurred_at >= (ISO-8601)"),
    to: datetime | None = Query(None, description="occurred_at <= (ISO-8601)"),
    limit: int = 500,
) -> dict:
    entries = audit.search(entity_id=entity_id, actor_id=actor_id, event_type=event_type,
                           engine=engine, frm=frm, to=to, limit=limit)
    return {"count": len(entries), "entries": [e.model_dump(mode="json") for e in entries]}


@router.get("/verify", summary="Verify the hash-chain (tamper detection)")
def verify() -> dict:
    return audit.verify_chain()


@router.post("/integrity-check", summary="Verify chain + alert on a break (FR-018)")
def integrity_check(_: Annotated[None, Depends(require_api_key)]) -> dict:
    return service.integrity_check()


@router.get("/export", summary="Export the audit trail — CSV / JSON / PDF (RBAC-gated)")
def export(
    role: Annotated[str, Depends(require_export_role)],
    fmt: str = Query("json", pattern="^(json|csv|pdf)$"),
    entity_id: str | None = None,
    actor_id: str | None = None,
    event_type: str | None = None,
):
    entries = audit.search(entity_id=entity_id, actor_id=actor_id, event_type=event_type,
                           limit=100_000)
    if fmt == "csv":
        return PlainTextResponse(audit.export_csv(entries), media_type="text/csv")
    if fmt == "pdf":
        return Response(pdf.render(entries), media_type="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=audit_trail.pdf"})
    return audit.export_package(entries)


@router.post("/anchor", summary="Anchor a Merkle root over the ledger (tamper-evidence)")
def anchor(_: Annotated[None, Depends(require_api_key)], external_ref: str = "") -> dict:
    result = service.anchor_ledger(external_ref=external_ref)
    if result is None:
        raise HTTPException(status_code=409, detail="empty ledger — nothing to anchor")
    return result


@router.get("/anchors", summary="List recorded Merkle anchors")
def anchors() -> dict:
    return {"anchors": service.get_anchors()}


@router.get("/worm/{seq}", summary="Read one entry back from the WORM mirror")
def worm_entry(seq: int) -> dict:
    record = service.worm_entry(seq)
    if record is None:
        raise HTTPException(status_code=404, detail="not in WORM store (disabled or unknown seq)")
    return {"seq": seq, "worm_record": record}


# ── Lifecycle: cold-storage tiering + retention purge (CEAAPSS-566) ─────────
@router.get("/lifecycle", summary="Audit lifecycle status (cold + purge watermark)")
def lifecycle() -> dict:
    return service.audit_lifecycle()


@router.post("/cold-tier", summary="Archive hot entries older than the cold-age threshold")
def cold_tier(_: Annotated[None, Depends(require_api_key)]) -> dict:
    return service.audit_cold_tier()


@router.post("/purge", summary="Purge aged, archived, anchored entries from the hot ledger")
def purge(_: Annotated[None, Depends(require_api_key)]) -> dict:
    return service.audit_purge()


@router.get("/cold/{seq}", summary="Read a purged/archived entry back from cold storage")
def cold_entry(seq: int) -> dict:
    record = service.cold_entry(seq)
    if record is None:
        raise HTTPException(status_code=404, detail="not in cold storage")
    return {"seq": seq, "cold_record": record}


@router.get("/{seq}", summary="Fetch one audit record by sequence")
def get_entry(seq: int) -> dict:
    entry = audit.get(seq)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown audit seq")
    return entry.model_dump(mode="json")


@router.get("/{seq}/proof", summary="Merkle inclusion proof for an anchored entry")
def proof(seq: int) -> dict:
    result = service.inclusion_proof(seq)
    if result is None:
        raise HTTPException(status_code=404, detail="entry not covered by any anchor")
    return result
