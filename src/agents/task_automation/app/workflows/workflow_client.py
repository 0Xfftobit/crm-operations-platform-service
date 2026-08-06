"""Temporal workflow client — start, signal, query doc-request workflows."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.activity_runner import build_retry_policy_dict

logger = logging.getLogger(__name__)

_client: Any = None


async def get_temporal_client():
    global _client
    from temporalio.client import Client

    settings = get_settings()
    if _client is None:
        _client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    return _client


def reset_temporal_client() -> None:
    global _client
    _client = None


def _workflow_options() -> dict[str, Any]:
    settings = get_settings()
    return {
        "retry_policy": build_retry_policy_dict(),
        "activity_timeout_s": settings.activity_timeout_s,
    }


async def start_doc_request_async(*, input_payload: dict[str, Any], workflow_id: str | None = None) -> dict[str, Any]:
    from app.workflows.doc_request_temporal import DocRequestChainWorkflow

    settings = get_settings()
    client = await get_temporal_client()
    wf_id = workflow_id or f"doc-request-{input_payload['deal_id']}-{uuid4().hex[:10]}"
    handle = await client.start_workflow(
        DocRequestChainWorkflow.run,
        args=[input_payload, _workflow_options()],
        id=wf_id,
        task_queue=settings.temporal_task_queue,
    )
    return {
        "workflow_id": handle.id,
        "type": "doc_request_chain",
        "provider": "temporal",
        "deal_id": input_payload["deal_id"],
        "lead_id": input_payload["lead_id"],
        "document_types": input_payload.get("document_types") or [],
        "rule_id": input_payload.get("rule_id"),
        "state": "AwaitingDocs",
        "sla_hours": input_payload.get("sla_hours"),
        "run_id": handle.result_run_id,
    }


async def signal_document_received_async(
    workflow_id: str,
    *,
    document_id: str,
    doc_type: str,
) -> dict[str, Any] | None:
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal(
        "document_received",
        {"document_id": document_id, "doc_type": doc_type},
    )
    return await query_workflow_async(workflow_id)


async def query_workflow_async(workflow_id: str) -> dict[str, Any] | None:
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    try:
        return await handle.query("get_status")
    except Exception as exc:
        logger.warning("workflow_query_failed", extra={"workflow_id": workflow_id, "error": str(exc)})
        return None


async def compensate_workflow_async(workflow_id: str, *, reason: str) -> dict[str, Any] | None:
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.signal("compensate", reason)
    return await query_workflow_async(workflow_id)


def start_doc_request_temporal(*, input_payload: dict[str, Any], workflow_id: str | None = None) -> dict[str, Any]:
    return asyncio.run(start_doc_request_async(input_payload=input_payload, workflow_id=workflow_id))


def signal_document_received_temporal(
    workflow_id: str,
    *,
    document_id: str,
    doc_type: str,
) -> dict[str, Any] | None:
    return asyncio.run(signal_document_received_async(
        workflow_id, document_id=document_id, doc_type=doc_type,
    ))


def query_workflow_temporal(workflow_id: str) -> dict[str, Any] | None:
    return asyncio.run(query_workflow_async(workflow_id))


def compensate_workflow_temporal(workflow_id: str, *, reason: str) -> dict[str, Any] | None:
    return asyncio.run(compensate_workflow_async(workflow_id, reason=reason))
