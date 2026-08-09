"""Doc-request workflow facade — Temporal durable + local fallback (§4)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.activities.doc_workflow import (
    compensate_doc_request,
    documents_complete,
    escalate_doc_request,
    verify_documents,
)
from app.persistence.db import persist_workflow_run

logger = logging.getLogger(__name__)

_local_workflows: dict[str, dict[str, Any]] = {}
_index_by_deal: dict[str, list[str]] = {}

_lock = RLock()


def reset_workflows() -> None:
    with _lock:
        _local_workflows.clear()
        _index_by_deal.clear()
    from app.workflows.workflow_client import reset_temporal_client

    reset_temporal_client()


def _persist_workflow(row: dict[str, Any], *, status: str | None = None) -> None:
    persist_workflow_run({
        "run_id": row.get("run_id") or row["workflow_id"],
        "lead_id": row.get("lead_id"),
        "deal_id": row.get("deal_id") or row.get("lead_id"),
        "workflow_type": row.get("type", "doc_request_chain"),
        "status": status or row.get("state") or row.get("status", "started"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
    })


def _index_workflow(row: dict[str, Any]) -> None:
    deal_id = row.get("deal_id") or row.get("lead_id")
    if not deal_id:
        return
    wf_id = row["workflow_id"]
    ids = _index_by_deal.setdefault(deal_id, [])
    if wf_id not in ids:
        ids.append(wf_id)


def _use_temporal() -> bool:
    return get_settings().temporal_enabled


def start_doc_request_workflow(
    *,
    deal_id: str,
    lead_id: str,
    document_types: list[str],
    rule_id: str | None = None,
    sla_hours: float = 48,
) -> dict[str, Any]:
    input_payload = {
        "deal_id": deal_id,
        "lead_id": lead_id,
        "document_types": document_types,
        "rule_id": rule_id,
        "sla_hours": sla_hours,
    }

    if _use_temporal():
        try:
            from app.workflows.workflow_client import start_doc_request_temporal

            row = start_doc_request_temporal(input_payload=input_payload)
            row["sla_due_at"] = (datetime.now(UTC) + timedelta(hours=sla_hours)).isoformat()
            row["signals_received"] = []
            row["compensation_available"] = True
            with _lock:
                _local_workflows[row["workflow_id"]] = row
                _index_workflow(row)
                snapshot = dict(row)
            _persist_workflow(snapshot, status="AwaitingDocs")
            return row
        except Exception as exc:
            logger.warning("temporal_doc_workflow_fallback", extra={"error": str(exc)})

    wf_id = f"wf_doc_{uuid4().hex[:12]}"
    row = {
        "workflow_id": wf_id,
        "type": "doc_request_chain",
        "provider": "local",
        "deal_id": deal_id,
        "lead_id": lead_id,
        "document_types": document_types,
        "rule_id": rule_id,
        "state": "AwaitingDocs",
        "sla_hours": sla_hours,
        "sla_due_at": (datetime.now(UTC) + timedelta(hours=sla_hours)).isoformat(),
        "started_at": datetime.now(UTC).isoformat(),
        "signals_received": [],
        "compensation_available": True,
    }
    with _lock:
        _local_workflows[wf_id] = row
        _index_workflow(row)
        snapshot = dict(row)
    _persist_workflow(snapshot, status="AwaitingDocs")
    return row


def signal_document_received(workflow_id: str, *, document_id: str, doc_type: str) -> dict[str, Any] | None:
    with _lock:
        meta = _local_workflows.get(workflow_id)
    if meta and meta.get("provider") == "temporal" and _use_temporal():
        try:
            from app.workflows.workflow_client import signal_document_received_temporal

            status = signal_document_received_temporal(
                workflow_id, document_id=document_id, doc_type=doc_type,
            )
            if status:
                with _lock:
                    current = _local_workflows.get(workflow_id) or meta
                    _local_workflows[workflow_id] = {**current, **status}
                return status
        except Exception as exc:
            logger.warning("temporal_signal_fallback", extra={"workflow_id": workflow_id, "error": str(exc)})

    with _lock:
        wf = _local_workflows.get(workflow_id)
        if not wf or wf["state"] not in ("AwaitingDocs", "Escalated"):
            return None
        wf["signals_received"].append({
            "document_id": document_id,
            "doc_type": doc_type,
            "at": datetime.now(UTC).isoformat(),
        })
        if documents_complete(
            document_types=list(wf.get("document_types") or []),
            signals_received=wf["signals_received"],
        ):
            wf["state"] = "Verifying"
        wf["updated_at"] = datetime.now(UTC).isoformat()
        snapshot = dict(wf)
    _persist_workflow(snapshot, status=snapshot["state"])
    return wf


def check_sla_timers(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Local-mode SLA escalation — Temporal workflows use durable timers."""
    now = now or datetime.now(UTC)
    with _lock:
        escalated: list[dict[str, Any]] = []
        for wf in list(_local_workflows.values()):
            if wf.get("provider") == "temporal":
                continue
            if wf["state"] != "AwaitingDocs":
                continue
            due = datetime.fromisoformat(wf["sla_due_at"].replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if now >= due:
                wf["state"] = "Escalated"
                wf["escalated_at"] = now.isoformat()
                wf["escalation"] = escalate_doc_request({
                    "deal_id": wf["deal_id"],
                    "lead_id": wf["lead_id"],
                    "rule_id": wf.get("rule_id"),
                    "sla_hours": wf.get("sla_hours"),
                })
                escalated.append(wf)
        snapshots = [dict(wf) for wf in escalated]
    for wf in escalated:
        _persist_workflow(wf, status="Escalated")
    return snapshots


def complete_verification(workflow_id: str, *, verified: bool | None = None) -> dict[str, Any] | None:
    with _lock:
        wf = _local_workflows.get(workflow_id)
    if not wf:
        if _use_temporal():
            from app.workflows.workflow_client import query_workflow_temporal

            return query_workflow_temporal(workflow_id)
        return None

    if wf.get("provider") == "temporal":
        from app.workflows.workflow_client import query_workflow_temporal

        return query_workflow_temporal(workflow_id)

    if wf["state"] != "Verifying":
        return wf

    verification = verify_documents({
        "deal_id": wf["deal_id"],
        "lead_id": wf["lead_id"],
        "document_types": wf.get("document_types") or [],
        "signals_received": wf.get("signals_received") or [],
    })
    verification = verification or {}
    with _lock:
        wf["verification"] = verification
        wf["verified"] = verified if verified is not None else bool(verification.get("verified", False))
        wf["state"] = "Completed"
        wf["completed_at"] = datetime.now(UTC).isoformat()
        snapshot = dict(wf)
    _persist_workflow(snapshot, status="Completed")
    return wf


def compensate(workflow_id: str, *, reason: str) -> dict[str, Any] | None:
    with _lock:
        meta = _local_workflows.get(workflow_id)
    if meta and meta.get("provider") == "temporal" and _use_temporal():
        try:
            from app.workflows.workflow_client import compensate_workflow_temporal

            return compensate_workflow_temporal(workflow_id, reason=reason)
        except Exception as exc:
            logger.warning("temporal_compensate_failed", extra={"error": str(exc)})

    with _lock:
        wf = _local_workflows.get(workflow_id)
        if not wf:
            return None
        wf["state"] = "Compensated"
        wf["compensation"] = compensate_doc_request({
            "workflow_id": workflow_id,
            "deal_id": wf.get("deal_id"),
            "reason": reason,
        })
        wf["compensated_at"] = datetime.now(UTC).isoformat()
        return wf


def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    with _lock:
        meta = _local_workflows.get(workflow_id)
    if meta and meta.get("provider") == "temporal" and _use_temporal():
        try:
            from app.workflows.workflow_client import query_workflow_temporal

            status = query_workflow_temporal(workflow_id)
            if status:
                with _lock:
                    current = _local_workflows.get(workflow_id) or meta
                    _local_workflows[workflow_id] = {**current, **status}
                return status
        except Exception as exc:
            logger.warning("temporal_query_fallback", extra={"workflow_id": workflow_id, "error": str(exc)})
    with _lock:
        return _local_workflows.get(workflow_id)


def list_workflows(*, deal_id: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        if deal_id:
            ids = list(_index_by_deal.get(deal_id, []))
        else:
            ids = list(_local_workflows.keys())
    return [row for wf_id in ids if (row := get_workflow(wf_id))]
