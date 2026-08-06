"""Doc-request workflow activities — verify, escalate, compensate."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from temporalio import activity


def documents_complete(*, document_types: list[str], signals_received: list[dict[str, Any]]) -> bool:
    """True when every required doc type has at least one received signal."""
    if not signals_received:
        return False
    if not document_types:
        return True
    received_types = {str(s.get("doc_type")) for s in signals_received if s.get("doc_type")}
    required = {str(d) for d in document_types}
    return required.issubset(received_types)


def _verify_documents_impl(payload: dict[str, Any]) -> dict[str, Any]:
    document_types = list(payload.get("document_types") or [])
    signals = list(payload.get("signals_received") or [])
    received_types = {str(s.get("doc_type")) for s in signals if s.get("doc_type")}
    required = {str(d) for d in document_types}

    if not signals:
        verified = False
    elif not required:
        verified = True
    else:
        verified = required.issubset(received_types) or bool(received_types)

    return {
        "verified": verified,
        "deal_id": payload.get("deal_id"),
        "lead_id": payload.get("lead_id"),
        "document_types": document_types,
        "received_types": sorted(received_types),
        "signals_count": len(signals),
        "verified_at": datetime.now(UTC).isoformat(),
    }


def _escalate_impl(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "escalated": True,
        "deal_id": payload.get("deal_id"),
        "lead_id": payload.get("lead_id"),
        "rule_id": payload.get("rule_id"),
        "sla_hours": payload.get("sla_hours"),
        "escalation_id": f"esc_{uuid4().hex[:12]}",
        "action": "notify_processor_and_reassign",
        "escalated_at": datetime.now(UTC).isoformat(),
    }


def _compensate_impl(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "compensated": True,
        "workflow_id": payload.get("workflow_id"),
        "deal_id": payload.get("deal_id"),
        "reason": payload.get("reason", "manual_compensation"),
        "compensated_at": datetime.now(UTC).isoformat(),
    }


@activity.defn(name="verify_documents")
async def verify_documents_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = _verify_documents_impl(payload)
    return {**result, "activity": "verify_documents"}


@activity.defn(name="escalate_doc_request")
async def escalate_doc_request_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = _escalate_impl(payload)
    return {**result, "activity": "escalate_doc_request"}


@activity.defn(name="compensate_doc_request")
async def compensate_doc_request_activity(payload: dict[str, Any]) -> dict[str, Any]:
    result = _compensate_impl(payload)
    return {**result, "activity": "compensate_doc_request"}


DOC_WORKFLOW_ACTIVITIES = [
    verify_documents_activity,
    escalate_doc_request_activity,
    compensate_doc_request_activity,
]

# Public helpers for local workflow runner
verify_documents = _verify_documents_impl
escalate_doc_request = _escalate_impl
compensate_doc_request = _compensate_impl
