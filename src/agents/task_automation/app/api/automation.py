"""Gateway-style automation API — /automation/v1/* aliases."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_api_key
from app.service import (
    get_automation_run,
    process_event,
    simulate_blast_radius,
    simulate_rule,
    simulate_rule_design,
)

router = APIRouter(prefix="/automation/v1", tags=["automation-gateway"])
Auth = Annotated[str, Depends(require_api_key)]


class TriggerRequest(BaseModel):
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None


class BlastRadiusRequest(BaseModel):
    topic: str | None = None
    days: int = Field(7, ge=1, le=30)


class RuleSimulateRequest(BaseModel):
    lookback_days: int = Field(7, ge=1, le=90)
    sample_size: int = Field(50, ge=1, le=500)
    topic: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/triggers", summary="Fire ad-hoc trigger (FR-018)")
def fire_trigger(body: TriggerRequest, _: Auth) -> dict[str, Any]:
    flags = {body.payload["feature_flag"]} if body.payload.get("feature_flag") else None
    return process_event(
        topic=body.topic,
        payload=body.payload,
        tenant_id=body.tenant_id,
        feature_flags=flags,
    )


@router.post("/rules/{rule_id}/simulate", summary="Dry-run single rule (system design §3)")
def simulate(rule_id: str, body: RuleSimulateRequest, _: Auth) -> dict[str, Any]:
    if body.topic:
        result = simulate_rule(rule_id=rule_id, topic=body.topic, payload=body.payload)
        if result.get("error"):
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    result = simulate_rule_design(
        rule_id=rule_id,
        lookback_days=body.lookback_days,
        sample_size=body.sample_size,
    )
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/rules/{rule_id}/blast-radius", summary="US-2: dry-run against historical events")
def blast_radius(rule_id: str, body: BlastRadiusRequest, _: Auth) -> dict[str, Any]:
    result = simulate_blast_radius(rule_id=rule_id, topic=body.topic, days=body.days)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/runs/{run_id}", summary="Run status + action ledger (FR-018)")
def get_run(run_id: str, _: Auth) -> dict[str, Any]:
    run = get_automation_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    return run
