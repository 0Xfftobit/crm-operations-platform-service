"""Event ingest — Stage Tracking, Lead Assignment, NBA triggers."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.schemas.tasks import EventIngestRequest
from app.service import process_event
from app.workflows.doc_request_chain import (
    complete_verification,
    get_workflow,
    list_workflows,
    signal_document_received,
)

router = APIRouter(tags=["events"])
Auth = Annotated[str, Depends(require_api_key)]


class DocumentReceivedRequest(BaseModel):
    deal_id: str
    workflow_id: str | None = None
    document_id: str
    doc_type: str


@router.post("/events/document-received", summary="Signal doc-request workflow (§4)")
def document_received(body: DocumentReceivedRequest, _: Auth) -> dict[str, Any]:
    wf_id = body.workflow_id
    if not wf_id:
        active = list_workflows(deal_id=body.deal_id)
        if not active:
            return {"error": "no_active_workflow"}
        wf_id = active[-1]["workflow_id"]
    wf = signal_document_received(wf_id, document_id=body.document_id, doc_type=body.doc_type)
    if not wf:
        return {"error": "signal_failed"}
    if wf.get("provider") != "temporal" and wf.get("state") == "Verifying":
        complete_verification(wf_id, verified=True)
    result = get_workflow(wf_id) or wf
    return result


@router.post("/events/ingest", summary="Ingest trigger event and auto-execute governed actions")
def ingest_event(
    body: EventIngestRequest,
    _: Auth,
    x_idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    return process_event(topic=body.topic, payload=body.payload, idempotency_key=x_idempotency_key)


@router.post("/events/simulate", summary="Dry-run all matching rules for an event")
def simulate_event(body: EventIngestRequest, _: Auth) -> dict[str, Any]:
    return process_event(topic=body.topic, payload=body.payload, dry_run=True)
