"""Follow-up Sequences API — sequence/contact definition, enrollment, scheduler tick, engagement
webhooks, consent/opt-out, terminal triggers, ledger, and analytics.

Write/ops endpoints require ``X-API-Key``; reads are open in this build. The ``/scheduler/tick``
endpoint is the in-process stand-in for Temporal durable timers — advances every due enrollment.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import analytics, events, service
from app.api.deps import require_api_key
from app.domain.models import Contact, EngagementEvent, SequenceDef, SuppressionEvent

router = APIRouter(tags=["sequences"])
Auth = Depends(require_api_key)


# ── Definitions ────────────────────────────────────────────────────────────
@router.post("/sequences", summary="Create/version a sequence definition (FR: sequence builder)")
def upsert_sequence(seq: SequenceDef, _: Annotated[None, Auth]) -> dict:
    return service.upsert_sequence(seq).model_dump(mode="json")


@router.get("/sequences/{sequence_id}", summary="Get a sequence definition")
def get_sequence(sequence_id: str, tenant: str = "default", version: int | None = None) -> dict:
    from app.repositories import get_sequence_repository
    seq = get_sequence_repository().get(sequence_id, tenant, version)
    if seq is None:
        raise HTTPException(status_code=404, detail="unknown sequence")
    return seq.model_dump(mode="json")


@router.post("/contacts", summary="Upsert a contact (consent/timezone/personalization data)")
def upsert_contact(contact: Contact, _: Annotated[None, Auth]) -> dict:
    return service.upsert_contact(contact).model_dump(mode="json")


# ── Enrollment ─────────────────────────────────────────────────────────────
class EnrollRequest(BaseModel):
    contact_id: str
    sequence_id: str
    trigger: str = "manual"
    context: dict = Field(default_factory=dict)
    version: int | None = None
    tenant_id: str = "default"


@router.post("/enrollments", summary="Enroll a contact into a sequence (FR: enrollment)")
def enroll(req: EnrollRequest, _: Annotated[None, Auth]) -> dict:
    return service.enroll(req.contact_id, req.sequence_id, trigger=req.trigger,
                          context=req.context, version=req.version, tenant=req.tenant_id)


@router.get("/enrollments/{enrollment_id}", summary="Enrollment state + outreach history")
def get_enrollment(enrollment_id: str) -> dict:
    result = service.get_enrollment(enrollment_id)
    if result is None:
        raise HTTPException(status_code=404, detail="unknown enrollment")
    return result


@router.get("/enrollments", summary="List enrollments (optionally by contact)")
def list_enrollments(contact_id: str | None = None, tenant: str = "default") -> dict:
    return {"enrollments": service.list_enrollments(contact_id=contact_id, tenant=tenant)}


class StateRequest(BaseModel):
    state: Literal["paused", "resume", "stopped"]  # FR-013: pause/resume/stop


@router.post("/enrollments/{enrollment_id}/state", summary="LO override: pause/resume/stop")
def set_state(enrollment_id: str, req: StateRequest, _: Annotated[None, Auth]) -> dict:
    return service.set_enrollment_state(enrollment_id, state=req.state)


# ── Scheduler tick (Temporal-timer stand-in) ───────────────────────────────
@router.post("/scheduler/tick", summary="Advance all due enrollments (guardrail→send→branch)")
def tick(_: Annotated[None, Auth], tenant: str | None = None) -> dict:
    return service.tick(tenant=tenant)


# ── Engagement webhooks + terminal signals ─────────────────────────────────
@router.post("/webhooks/engagement", summary="Delivery/open/click/reply webhook (drives branching)")
def engagement(event: EngagementEvent, _: Annotated[None, Auth]) -> dict:
    return service.ingest_engagement(event)


class StageRequest(BaseModel):
    contact_id: str
    stage: str
    tenant_id: str = "default"


@router.post("/stage-changed", summary="Deal stage change (apply/convert → exit sequence)")
def stage_changed(req: StageRequest, _: Annotated[None, Auth]) -> dict:
    return service.record_stage_change(req.contact_id, req.stage, tenant=req.tenant_id)


@router.post("/contacts/{contact_id}/opt-out", summary="Opt-out/unsubscribe — permanent (BR)")
def opt_out(contact_id: str, _: Annotated[None, Auth], tenant: str = "default") -> dict:
    return service.opt_out(contact_id, tenant=tenant)


class ConsentRequest(BaseModel):
    consent_basis: str | None = None
    dnc: bool | None = None
    opted_out: bool | None = None
    tenant_id: str = "default"


@router.post("/contacts/{contact_id}/consent", summary="Update consent/DNC (propagates)")
def consent(contact_id: str, req: ConsentRequest, _: Annotated[None, Auth]) -> dict:
    return service.update_consent(contact_id, tenant=req.tenant_id, consent_basis=req.consent_basis,
                                  dnc=req.dnc, opted_out=req.opted_out)


@router.post("/contacts/{contact_id}/manual-outreach", summary="Record LO manual touch (cooldown)")
def manual_outreach(contact_id: str, _: Annotated[None, Auth], tenant: str = "default") -> dict:
    return service.mark_manual_outreach(contact_id, tenant=tenant)


@router.post("/webhooks/suppression", summary="Provider bounce/complaint/spam — suppress contact")
def suppression(event: SuppressionEvent, _: Annotated[None, Auth]) -> dict:
    return service.apply_suppression(event)


# ── Auto-enroll triggers ───────────────────────────────────────────────────
class LeadScoredRequest(BaseModel):
    contact_id: str
    score: float
    sequence_id: str
    tenant_id: str = "default"
    context: dict = Field(default_factory=dict)


@router.post("/triggers/lead-scored", summary="Auto-enroll when score ≥ threshold (FR: trigger)")
def lead_scored(req: LeadScoredRequest, _: Annotated[None, Auth]) -> dict:
    return service.handle_lead_scored(req.contact_id, req.score, req.sequence_id,
                                      tenant=req.tenant_id, context=req.context)


class TagRequest(BaseModel):
    contact_id: str
    tag: str
    sequence_id: str
    tenant_id: str = "default"


@router.post("/triggers/tag-applied", summary="Auto-enroll when a tag is applied (FR: trigger)")
def tag_applied(req: TagRequest, _: Annotated[None, Auth]) -> dict:
    return service.handle_tag_applied(req.contact_id, req.tag, req.sequence_id,
                                      tenant=req.tenant_id)


# ── Ledger + analytics + events ────────────────────────────────────────────
@router.get("/analytics/sequences/{sequence_id}", summary="Per-step delivery/open/reply rates")
def sequence_analytics(sequence_id: str, tenant: str = "default") -> dict:
    return analytics.sequence_analytics(sequence_id, tenant=tenant)


@router.get("/ledger", summary="Outreach ledger (consent basis + outcome per send)")
def ledger(tenant: str = "default", limit: int = 200) -> dict:
    return {"ledger": service.outreach_ledger(tenant=tenant, limit=limit)}


@router.get("/events", summary="Produced events (enrolled/sent/stopped/completed/failed)")
def list_events(event_type: str | None = None, limit: int = 200) -> dict:
    return {"events": events.get_events(event_type=event_type, limit=limit)}


@router.get("/events/dlq", summary="Dead-letter events (schema-invalid / failed sends)")
def list_dlq(limit: int = 200) -> dict:
    return {"dlq": events.get_dlq(limit=limit)}
