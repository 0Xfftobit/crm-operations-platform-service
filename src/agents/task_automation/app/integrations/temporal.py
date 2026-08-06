"""Temporal workflow orchestration — durable multi-step automations."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.activity_runner import build_retry_policy_dict

logger = logging.getLogger(__name__)

_workflows: dict[str, dict[str, Any]] = {}
_client: Any = None


def reset_temporal() -> None:
    global _client
    _workflows.clear()
    _client = None


async def _get_client():
    global _client
    from temporalio.client import Client

    settings = get_settings()
    if _client is None:
        _client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    return _client


def _local_start_workflow(
    *,
    template: str,
    lead_id: str,
    input_payload: dict[str, Any],
    rule_id: str | None = None,
    retry_policy: dict[str, Any] | None = None,
    timer_hours: float | None = None,
) -> dict[str, Any]:
    wf_id = f"wf_{uuid4().hex[:12]}"
    policy = retry_policy or build_retry_policy_dict()
    row = {
        "workflow_id": wf_id,
        "run_id": f"run_{uuid4().hex[:8]}",
        "template": template,
        "lead_id": lead_id,
        "rule_id": rule_id,
        "status": "started",
        "provider": "local",
        "started_at": datetime.now(UTC).isoformat(),
        "input": input_payload,
        "retry_policy": policy,
        "timer_due_at": (datetime.now(UTC) + timedelta(hours=timer_hours or 24)).isoformat() if timer_hours else None,
        "compensation_available": True,
        "durable": False,
    }
    _workflows[wf_id] = row
    return row


async def _start_workflow_async(
    *,
    template: str,
    lead_id: str,
    input_payload: dict[str, Any],
    rule_id: str | None = None,
    retry_policy: dict[str, Any] | None = None,
    timer_hours: float | None = None,
) -> dict[str, Any]:
    from app.workflows.generic_temporal import GenericStartWorkflow

    settings = get_settings()
    client = await _get_client()
    wf_id = f"wf_{template.replace('.', '_')}_{lead_id}_{uuid4().hex[:8]}"
    options = {
        "retry_policy": retry_policy or build_retry_policy_dict(),
        "activity_timeout_s": settings.activity_timeout_s,
    }
    handle = await client.start_workflow(
        GenericStartWorkflow.run,
        args=[{
            "template": template,
            "lead_id": lead_id,
            "rule_id": rule_id,
            "timer_hours": timer_hours,
            "payload": input_payload,
        }, options],
        id=wf_id,
        task_queue=settings.temporal_task_queue,
    )
    row = {
        "workflow_id": handle.id,
        "run_id": handle.result_run_id,
        "template": template,
        "lead_id": lead_id,
        "rule_id": rule_id,
        "status": "started",
        "provider": "temporal",
        "started_at": datetime.now(UTC).isoformat(),
        "input": input_payload,
        "retry_policy": options["retry_policy"],
        "timer_due_at": (datetime.now(UTC) + timedelta(hours=timer_hours or 24)).isoformat() if timer_hours else None,
        "compensation_available": True,
        "durable": True,
    }
    _workflows[handle.id] = row
    return row


def start_workflow(
    *,
    template: str,
    lead_id: str,
    input_payload: dict[str, Any],
    rule_id: str | None = None,
    retry_policy: dict[str, Any] | None = None,
    timer_hours: float | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.temporal_enabled:
        try:
            return asyncio.run(_start_workflow_async(
                template=template,
                lead_id=lead_id,
                input_payload=input_payload,
                rule_id=rule_id,
                retry_policy=retry_policy,
                timer_hours=timer_hours,
            ))
        except Exception as exc:
            logger.warning("temporal_start_workflow_fallback", extra={"error": str(exc)})
    return _local_start_workflow(
        template=template,
        lead_id=lead_id,
        input_payload=input_payload,
        rule_id=rule_id,
        retry_policy=retry_policy,
        timer_hours=timer_hours,
    )


def compensate_workflow(workflow_id: str, *, reason: str) -> dict[str, Any] | None:
    settings = get_settings()
    if settings.temporal_enabled:
        try:
            async def _signal() -> None:
                client = await _get_client()
                handle = client.get_workflow_handle(workflow_id)
                await handle.signal("compensate", reason)

            asyncio.run(_signal())
        except Exception as exc:
            logger.warning("temporal_compensate_failed", extra={"error": str(exc)})

    wf = _workflows.get(workflow_id)
    if not wf:
        return None
    wf["status"] = "compensated"
    wf["compensation_reason"] = reason
    wf["compensated_at"] = datetime.now(UTC).isoformat()
    return wf


def list_workflows(*, lead_id: str | None = None) -> list[dict[str, Any]]:
    rows = list(_workflows.values())
    if lead_id:
        rows = [r for r in rows if r.get("lead_id") == lead_id]
    return rows
