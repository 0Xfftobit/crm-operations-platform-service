"""LLM task agent — drafting/classification under guardrails with approval gate."""
from __future__ import annotations

from threading import RLock
from typing import Any

from app.core.config import get_settings

FORBIDDEN_TOPICS = {"credit_decision", "rate_quote", "adverse_action", "underwriting_approval"}
_pending_approvals: dict[str, dict[str, Any]] = {}

_lock = RLock()


def reset_llm_agent() -> None:
    with _lock:
        _pending_approvals.clear()


def draft_task_notes(
    *,
    task_type: str,
    lead_id: str,
    event_payload: dict[str, Any],
    requires_approval: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    stage = event_payload.get("stage") or event_payload.get("to_stage") or "pipeline"
    product = event_payload.get("product") or "loan"
    confidence = 0.92 if task_type in ("call", "send_email") else 0.78
    draft = (
        f"Follow up on {lead_id}: {task_type.replace('_', ' ')} for {product} at stage '{stage}'. "
        "Confirm borrower has submitted required documentation."
    )
    blocked = confidence < settings.llm_confidence_threshold
    approval_id = f"llm_{lead_id}_{task_type}"
    result = {
        "draft_notes": draft,
        "agent": "llm_task_agent_stub",
        "confidence": confidence,
        "confidence_threshold": settings.llm_confidence_threshold,
        "write_blocked": blocked,
        "requires_human_approval": requires_approval or blocked,
        "guardrails": {
            "forbidden_topics_blocked": list(FORBIDDEN_TOPICS),
            "compliance_safe": True,
        },
        "approval_id": approval_id,
        "approved": False,
    }
    if result["requires_human_approval"]:
        with _lock:
            _pending_approvals[approval_id] = result
    return result


def classify_free_text(*, text: str, lead_id: str) -> dict[str, Any]:
    settings = get_settings()
    lowered = text.lower()
    if "urgent" in lowered or "asap" in lowered:
        label, confidence = "urgent_follow_up", 0.88
    elif "document" in lowered or "upload" in lowered:
        label, confidence = "document_request", 0.85
    else:
        label, confidence = "general_follow_up", 0.72
    return {
        "label": label,
        "confidence": confidence,
        "confidence_threshold": settings.llm_confidence_threshold,
        "lead_id": lead_id,
        "write_allowed": confidence >= settings.llm_confidence_threshold,
    }


def approve_llm_action(*, approval_id: str, approver: str) -> dict[str, Any] | None:
    with _lock:
        row = _pending_approvals.get(approval_id)
        if not row:
            return None
        row["approved"] = True
        row["approved_by"] = approver
        row["write_blocked"] = False
        return row


def llm_write_permitted(*, llm_result: dict[str, Any] | None) -> bool:
    if not llm_result:
        return True
    if llm_result.get("write_blocked"):
        return False
    if llm_result.get("requires_human_approval") and not llm_result.get("approved"):
        return False
    confidence = llm_result.get("confidence", 0)
    return confidence >= get_settings().llm_confidence_threshold


def pending_approvals() -> list[dict[str, Any]]:
    with _lock:
        return [v for v in _pending_approvals.values() if not v.get("approved")]
