"""Static-body Zoho CRM intake — ``POST /zoho/event``.

Zoho webhooks post a FULLY STATIC body: ``{"journey": "LEAD"}`` (plus an
optional static ``tenant_id``). No record ids, amounts, stages, emails or
any other dynamic value ever appear in the request body. The triggering
record's id travels in the ``X-CRM-Record-Id`` header; everything dynamic
is resolved *inside this endpoint* — the record is fetched from Zoho CRM
(``zoho_client.get_record``) and each agent payload is computed here.

Journeys served by this gateway (operations-platform):

  LEAD  Lead created  -> follow-up-sequences default nurture trigger
  DEAL  Deal edited   -> follow-up-sequences stage-changed + compliance
                         disclosures; Closed Won/Lost adds a lead-scoring
                         outcome into the feedback loop
  TASK  Task edited   -> task-automation event ingest + performance-analytics
                         task_event metric

Steps never abort the chain: each agent call is reported with its HTTP
status (0 = transport error) and the journey continues.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from src.api import zoho_client

router = APIRouter()

MODULE_BY_JOURNEY = {
    "LEAD": "Leads",
    "DEAL": "Deals",
    "TASK": "Tasks",
}

CLOSED_LOST_STAGES = {"Closed Lost", "Closed Lost to Competition"}


class ZohoEvent(BaseModel):
    """Static webhook body — a journey constant, nothing dynamic."""

    journey: str
    tenant_id: str = "default"


def _upstream(agent: str) -> str:
    return os.environ.get(f"{agent.upper()}_URL", "")


def _s(record: dict, field: str, default: str = "") -> str:
    value = record.get(field)
    return default if value is None else str(value)


def _num(record: dict, field: str, default: float) -> float:
    try:
        return float(record.get(field))
    except (TypeError, ValueError):
        return default


def _lookup_id(record: dict, field: str) -> str:
    value = record.get(field)
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return ""


async def _post_agent(
    client: httpx.AsyncClient,
    agent: str,
    path: str,
    payload: Any,
    headers: dict[str, str],
) -> tuple[int, Any]:
    """POST one agent step; (0, error) on transport failure — never raises."""
    base = _upstream(agent)
    if not base:
        return 0, "upstream not configured"
    try:
        resp = await client.post(
            base.rstrip("/") + path, json=payload, headers=headers, timeout=30.0
        )
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}"
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = resp.text[:200]
    return resp.status_code, body


@router.post("/zoho/event", tags=["zoho"])
async def zoho_event(
    evt: ZohoEvent,
    request: Request,
    x_crm_record_id: str = Header(default=""),
):
    journey = evt.journey.strip().upper()
    module = MODULE_BY_JOURNEY.get(journey)
    if not module:
        raise HTTPException(
            status_code=400,
            detail=f"unknown journey '{evt.journey}' — expected one of "
            f"{sorted(MODULE_BY_JOURNEY)}",
        )
    if not x_crm_record_id:
        raise HTTPException(status_code=400, detail="X-CRM-Record-Id header required")

    record = await zoho_client.get_record(module, x_crm_record_id)
    live_fetch = len(record) > 1
    fwd_headers = {"Content-Type": "application/json"}
    api_key = request.headers.get("X-API-Key")
    if api_key:
        fwd_headers["X-API-Key"] = api_key

    steps: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:

        async def run(agent: str, path: str, payload: Any) -> Any:
            status, body = await _post_agent(client, agent, path, payload, fwd_headers)
            steps.append({"agent": agent, "path": path, "status": status})
            return body

        if journey == "LEAD":
            lead_id = _s(record, "id", x_crm_record_id)
            await run(
                "follow_up_sequences",
                "/api/v1/triggers/lead-scored",
                {
                    "sequence_id": "default-lead-nurture",
                    "contact_id": lead_id,
                    "score": 50.0,
                    "tenant_id": evt.tenant_id,
                    "context": {
                        "email": _s(record, "Email"),
                        "source": "zoho-lead-create",
                    },
                },
            )

        elif journey == "DEAL":
            deal_id = _s(record, "id", x_crm_record_id)
            stage = _s(record, "Stage")
            amount = _num(record, "Amount", 0.0)
            contact_id = _lookup_id(record, "Contact_Name")
            won = stage == "Closed Won"
            lost = stage in CLOSED_LOST_STAGES
            await run(
                "follow_up_sequences",
                "/api/v1/stage-changed",
                {
                    "stage": stage,
                    "contact_id": contact_id,
                    "tenant_id": evt.tenant_id,
                },
            )
            await run(
                "compliance_monitor",
                "/api/v1/disclosures/stage",
                {"deal_id": deal_id, "stage": stage},
            )
            if won or lost:
                prediction = min(max(_num(record, "Probability", 50.0) / 100.0, 0.0), 1.0)
                await run(
                    "feedback_loop",
                    "/api/v1/outcomes",
                    {
                        "engine": "lead_scoring",
                        "entity_id": deal_id,
                        "prediction": prediction,
                        "outcome": 1.0 if won else 0.0,
                        "tenant_id": evt.tenant_id,
                        "meta": {"stage": stage, "amount": amount},
                    },
                )

        elif journey == "TASK":
            task_id = _s(record, "id", x_crm_record_id)
            status = _s(record, "Status")
            await run(
                "task_automation",
                "/api/v1/events/ingest",
                {
                    "topic": "task.completed" if status == "Completed" else "manual",
                    "payload": {
                        "task_id": task_id,
                        "subject": _s(record, "Subject"),
                        "status": status,
                        "due_date": _s(record, "Due_Date"),
                        "owner": _lookup_id(record, "Owner"),
                        "related_to": _lookup_id(record, "Who_Id")
                        or _lookup_id(record, "What_Id")
                        or "",
                        "source": "zoho-task",
                    },
                },
            )
            await run(
                "performance_analytics",
                "/api/v1/metrics",
                {
                    "metric": "task_event",
                    "value": 1.0,
                    "dimensions": {"agent": "task_automation", "status": status},
                    "tenant_id": evt.tenant_id,
                    "meta": {"task_id": task_id},
                },
            )

    return {
        "journey": journey,
        "module": module,
        "record_id": x_crm_record_id,
        "zoho_fetch": "live" if live_fetch else "fallback",
        "tenant_id": evt.tenant_id,
        "steps": steps,
    }
