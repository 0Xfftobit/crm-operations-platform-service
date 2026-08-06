"""Workflow rule builder API — versioning, simulation, rollout."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app import store
from app.api.deps import require_api_key
from app.schemas.enums import TRIGGER_EVENTS
from app.schemas.rules import SimulateRuleRequest, WorkflowRuleCreate, WorkflowRuleUpdate
from app.service import publish_rule_version, rollback_rule, simulate_blast_radius, simulate_rule

router = APIRouter(tags=["rules"])
Auth = Annotated[str, Depends(require_api_key)]


@router.get("/rules/trigger-events", summary="Supported trigger events")
def trigger_events(_: Auth) -> dict[str, Any]:
    return {"trigger_events": TRIGGER_EVENTS}


@router.get("/rules", summary="List workflow rules")
def list_rules(_: Auth, active_only: bool = False) -> dict[str, Any]:
    return {"rules": store.list_rules(active_only=active_only)}


@router.post("/rules", summary="Create workflow rule", status_code=201)
def create_rule(body: WorkflowRuleCreate, _: Auth) -> dict[str, Any]:
    data = body.model_dump()
    if body.record_updates:
        data["record_updates"] = body.record_updates.model_dump(exclude_none=True)
    return store.create_rule(data)


@router.get("/rules/{rule_id}", summary="Get workflow rule")
def get_rule(rule_id: str, _: Auth) -> dict[str, Any]:
    row = store.get_rule(rule_id)
    if not row:
        raise HTTPException(status_code=404, detail="rule not found")
    return row


@router.patch("/rules/{rule_id}", summary="Update workflow rule")
def update_rule(rule_id: str, body: WorkflowRuleUpdate, _: Auth) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if body.record_updates:
        patch["record_updates"] = body.record_updates.model_dump(exclude_none=True)
    row = store.update_rule(rule_id, patch)
    if not row:
        raise HTTPException(status_code=404, detail="rule not found")
    return row


@router.post("/rules/{rule_id}/rollback", summary="Instant rollback — archive rule")
def rollback(rule_id: str, _: Auth) -> dict[str, Any]:
    row = rollback_rule(rule_id)
    if row.get("error"):
        raise HTTPException(status_code=404, detail="rule not found")
    return row


@router.post("/rules/{rule_id}/blast-radius", summary="Dry-run against last N days of events")
def blast_radius(rule_id: str, _: Auth, days: int = 7, topic: str | None = None) -> dict[str, Any]:
    result = simulate_blast_radius(rule_id=rule_id, topic=topic, days=days)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/rules/{rule_id}/publish", summary="Promote rule: draft→canary→active (mandatory gates)")
def publish_rule(rule_id: str, _: Auth) -> dict[str, Any]:
    row = publish_rule_version(rule_id)
    if row.get("error") == "rule_not_found":
        raise HTTPException(status_code=404, detail="rule not found")
    if row.get("error") == "dry_run_required":
        raise HTTPException(status_code=409, detail=row.get("message", "dry run required"))
    return row


@router.post("/rules/{rule_id}/simulate", summary="Dry-run single rule against sample event")
def simulate(rule_id: str, body: SimulateRuleRequest, _: Auth) -> dict[str, Any]:
    result = simulate_rule(rule_id=rule_id, topic=body.topic, payload=body.payload)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.delete("/rules/{rule_id}", summary="Delete workflow rule", status_code=204)
def delete_rule(rule_id: str, _: Auth) -> None:
    if not store.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
