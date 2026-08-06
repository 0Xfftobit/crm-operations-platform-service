"""LLM task agent approval API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import events
from app.agents.llm_task_agent import approve_llm_action, classify_free_text, pending_approvals
from app.metrics import record_llm_action
from app.rbac import Principal, require_scope

router = APIRouter(tags=["llm-agents"])
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]
ApproveAuth = Annotated[Principal, Depends(require_scope("llm.approve"))]


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1)
    lead_id: str


class ApproveRequest(BaseModel):
    approval_id: str


@router.post("/agents/llm/classify", summary="Classify free text under guardrails")
def classify(body: ClassifyRequest, _: ReadAuth) -> dict[str, Any]:
    return classify_free_text(text=body.text, lead_id=body.lead_id)


@router.get("/agents/llm/pending", summary="Pending LLM write approvals")
def pending(_: ReadAuth) -> dict[str, Any]:
    return {"pending": pending_approvals()}


@router.post("/agents/llm/approve", summary="Approve LLM-drafted write action")
def approve(body: ApproveRequest, p: ApproveAuth) -> dict[str, Any]:
    row = approve_llm_action(approval_id=body.approval_id, approver=p.subject)
    if not row:
        raise HTTPException(status_code=404, detail="approval not found")
    events.publish("automation.llm.feedback", {
        "approval_id": body.approval_id,
        "approved_by": p.subject,
        "confidence": row.get("confidence"),
        "action": "approved",
    })
    record_llm_action(overridden=False)
    return row
