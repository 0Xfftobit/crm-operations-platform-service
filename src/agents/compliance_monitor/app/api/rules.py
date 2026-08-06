"""Regulatory Updates API (CEAAPSS-565) — rule versioning, the two-step approval workflow,
staging→production deployment, emergency override, and the regulation tracker.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import service
from app.api.deps import require_api_key
from app.domain.models import Domain, Regulation
from app.repositories import get_rule_change_repository, get_rule_repository

router = APIRouter(tags=["rules"])
Auth = Depends(require_api_key)


class ProposeRequest(BaseModel):
    rule_id: str
    domain: Domain = "regulatory"
    definition: dict = Field(default_factory=dict)
    change_reason: str = ""
    changed_by: str


class ApproveRequest(BaseModel):
    approved_by: str


class RejectRequest(BaseModel):
    approved_by: str
    reason: str = ""


class EmergencyRequest(BaseModel):
    rule_id: str
    domain: Domain = "regulatory"
    definition: dict = Field(default_factory=dict)
    change_reason: str
    changed_by: str


@router.post("/rules/changes", summary="Propose a rule change (step 1)")
def propose(req: ProposeRequest, _: Annotated[None, Auth]) -> dict:
    change = service.propose_rule_change(
        rule_id=req.rule_id, domain=req.domain, definition=req.definition,
        change_reason=req.change_reason, changed_by=req.changed_by)
    return change.model_dump(mode="json")


@router.post("/rules/changes/{change_id}/approve", summary="Approve a change (step 2, other user)")
def approve(change_id: str, req: ApproveRequest, _: Annotated[None, Auth]) -> dict:
    try:
        return service.approve_rule_change(change_id=change_id,
                                           approved_by=req.approved_by).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rules/changes/{change_id}/reject", summary="Reject a proposed change")
def reject(change_id: str, req: RejectRequest, _: Annotated[None, Auth]) -> dict:
    try:
        return service.reject_rule_change(change_id=change_id, approved_by=req.approved_by,
                                          reason=req.reason).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/rules/changes/{change_id}/deploy", summary="Deploy staging→production (step 3)")
def deploy(change_id: str, _: Annotated[None, Auth]) -> dict:
    try:
        change, prod = service.deploy_rule_change(change_id=change_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"change": change.model_dump(mode="json"), "production": prod.model_dump(mode="json")}


@router.post("/rules/emergency", summary="Emergency override (single-actor, audited)")
def emergency(req: EmergencyRequest, _: Annotated[None, Auth]) -> dict:
    change, prod = service.emergency_rule_override(
        rule_id=req.rule_id, domain=req.domain, definition=req.definition,
        change_reason=req.change_reason, changed_by=req.changed_by)
    return {"change": change.model_dump(mode="json"), "production": prod.model_dump(mode="json")}


@router.get("/rules", summary="List rules (optionally by stage)")
def list_rules(stage: str | None = None) -> dict:
    rules = get_rule_repository().list()
    if stage:
        rules = [r for r in rules if r.stage == stage]
    return {"rules": [r.model_dump(mode="json") for r in rules]}


@router.get("/rules/{rule_id}/history", summary="Change history for a rule")
def rule_history(rule_id: str) -> dict:
    changes = get_rule_change_repository().for_rule(rule_id)
    return {"rule_id": rule_id, "changes": [c.model_dump(mode="json") for c in changes]}


@router.put("/regulations", summary="Upsert a tracked regulation")
def put_regulation(reg: Regulation, _: Annotated[None, Auth]) -> dict:
    return service.upsert_regulation(reg).model_dump(mode="json")


@router.get("/regulations", summary="List tracked regulations")
def list_regulations(status: str | None = None) -> dict:
    from app.repositories import get_regulation_repository
    regs = get_regulation_repository().list()
    if status:
        regs = [r for r in regs if r.status == status]
    return {"regulations": [r.model_dump(mode="json") for r in regs]}


class RegChangeRequest(BaseModel):
    summary: str = ""


@router.post("/regulations/{reg_id}/change", summary="Record a detected regulatory change → alert")
def report_reg_change(reg_id: str, req: RegChangeRequest, _: Annotated[None, Auth]) -> dict:
    reg = service.report_regulatory_change(reg_id, summary=req.summary)
    if reg is None:
        raise HTTPException(status_code=404, detail="untracked regulation")
    return reg.model_dump(mode="json")


@router.post("/regulations/{reg_id}/reviewed", summary="Mark a regulation reviewed (clears flag)")
def mark_reviewed(reg_id: str, _: Annotated[None, Auth]) -> dict:
    reg = service.mark_regulation_reviewed(reg_id)
    if reg is None:
        raise HTTPException(status_code=404, detail="untracked regulation")
    return reg.model_dump(mode="json")


@router.post("/regulations/sweep", summary="Flag regulations overdue for review")
def sweep_regulations(_: Annotated[None, Auth], max_age_days: int = 180) -> dict:
    return {"flagged": service.sweep_regulations(max_age_days=max_age_days)}
