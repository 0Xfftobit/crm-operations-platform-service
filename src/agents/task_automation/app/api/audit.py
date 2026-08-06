"""Audit trail per lead."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app import store
from app.api.deps import require_api_key

router = APIRouter(tags=["audit"])
Auth = Annotated[str, Depends(require_api_key)]


@router.get("/audit/{lead_id}", summary="Full auditable activity trail for a lead")
def lead_audit(lead_id: str, _: Auth, limit: int = 100) -> dict[str, Any]:
    return {"lead_id": lead_id, "entries": store.audit_for_lead(lead_id, limit=limit)}


@router.get("/audit", summary="Recent audit entries (all leads)")
def recent_audit(_: Auth, limit: int = 100) -> dict[str, Any]:
    return {"entries": store.all_audit(limit=limit)}
