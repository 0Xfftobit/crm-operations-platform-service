"""Compliance operations API — pre-send hook, consent, activity ingest, fairness, disclosures,
explainability, alerts/cases, and the RAG dashboard.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import events, service
from app.api.deps import require_api_key
from app.domain.models import (
    ActivityEvent,
    CompliancePolicy,
    Consent,
    DisparityResult,
    PrecheckRequest,
    PrecheckResult,
)
from app.repositories import (
    get_alert_repository,
    get_case_repository,
    get_consent_repository,
    get_disclosure_repository,
    get_explainability_repository,
    get_retention_repository,
)

router = APIRouter(tags=["compliance"])
Auth = Depends(require_api_key)


# ── Pre-send compliance hook ───────────────────────────────────────────────
@router.post("/precheck", response_model=PrecheckResult, summary="Pre-send compliance check")
def precheck(req: PrecheckRequest, _: Annotated[None, Auth]) -> PrecheckResult:
    """Fail-closed hard-rule gate: TCPA quiet-hours, opt-out, DND/DNC, CAN-SPAM."""
    return service.run_precheck(req)


@router.put("/consent", response_model=Consent, summary="Upsert consent")
def put_consent(consent: Consent, _: Annotated[None, Auth]) -> Consent:
    return service.upsert_consent(consent)


@router.get("/consent/{lead_id}", summary="List a lead's consent records")
def get_consent(lead_id: str) -> dict:
    items = [c for c in get_consent_repository().list() if c.lead_id == lead_id]
    return {"lead_id": lead_id, "consents": [c.model_dump(mode="json") for c in items]}


# ── Activity ingest ────────────────────────────────────────────────────────
@router.post("/activity", summary="Ingest a monitored activity event")
def ingest_activity(event: ActivityEvent, _: Annotated[None, Auth]) -> dict:
    return service.record_activity(event)


# ── Fair-lending disparity ─────────────────────────────────────────────────
class FairnessRequest(BaseModel):
    sub_agent: str
    cohort_dimension: str
    cohorts: dict[str, dict] = Field(..., description="{cohort: {selected, total}}")
    window: str = ""


@router.post("/fairness/evaluate", response_model=DisparityResult, summary="4/5ths disparity check")
def evaluate_fairness(req: FairnessRequest, _: Annotated[None, Auth]) -> DisparityResult:
    return service.evaluate_fairness(req.sub_agent, req.cohort_dimension, req.cohorts,
                                     window=req.window)


class FairnessBatchRequest(BaseModel):
    runs: list[dict] = Field(..., description="[{sub_agent, cohort_dimension, cohorts, window?}]")


@router.post("/fairness/batch", summary="Batch disparity + redlining (geographic) detection")
def evaluate_fairness_batch(req: FairnessBatchRequest, _: Annotated[None, Auth]) -> dict:
    return service.evaluate_fairness_batch(req.runs)


# ── Disclosure clocks: ECOA adverse-action + RERA/TILA-RESPA at pipeline stage ─
class DisclosureRequest(BaseModel):
    deal_id: str
    kind: str = "adverse_action"
    due_days: int | None = None
    stage: str = ""


@router.post("/disclosures", summary="Start a disclosure clock (kind defaults to adverse_action)")
def start_disclosure(req: DisclosureRequest, _: Annotated[None, Auth]) -> dict:
    return service.start_disclosure(req.deal_id, kind=req.kind, due_days=req.due_days,
                                    stage=req.stage).model_dump(mode="json")


class StageChangeRequest(BaseModel):
    deal_id: str
    stage: str


@router.post("/disclosures/stage", summary="Record a pipeline stage → start required disclosures")
def stage_change(req: StageChangeRequest, _: Annotated[None, Auth]) -> dict:
    return {"started": service.on_stage_change(req.deal_id, req.stage)}


@router.get("/disclosures/requirements", summary="Required disclosures by pipeline stage")
def disclosure_requirements() -> dict:
    from app.disclosures import DISCLOSURE_REQUIREMENTS
    return {"requirements": {stage: [{"kind": k, "due_days": d} for k, d in reqs]
                             for stage, reqs in DISCLOSURE_REQUIREMENTS.items()}}


@router.post("/disclosures/{deal_id}/satisfy", summary="Mark a disclosure satisfied")
def satisfy_disclosure(deal_id: str, _: Annotated[None, Auth],
                       kind: str = "adverse_action") -> dict:
    try:
        return service.satisfy_disclosure(deal_id, kind=kind).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/disclosures", summary="List disclosure clocks")
def list_disclosures() -> dict:
    items = get_disclosure_repository().list()
    return {"disclosures": [t.model_dump(mode="json") for t in items]}


@router.get("/disclosures/check", summary="Sweep clocks (warn/overdue → alerts)")
def check_disclosures(_: Annotated[None, Auth]) -> dict:
    return {"statuses": service.check_disclosures()}


# ── Explainability records ─────────────────────────────────────────────────
class ExplainRequest(BaseModel):
    ref: str
    record: dict


@router.post("/explainability", summary="Record an AI-decision explanation")
def record_explainability(req: ExplainRequest, _: Annotated[None, Auth]) -> dict:
    return {"ref": service.record_explainability(req.ref, req.record)}


@router.get("/explainability/{ref}", summary="Fetch an explanation record")
def get_explainability(ref: str) -> dict:
    record = get_explainability_repository().get(ref)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown explanation ref")
    return {"ref": ref, "record": record}


# ── Alerts / cases ─────────────────────────────────────────────────────────
@router.get("/alerts", summary="List raised alerts")
def list_alerts() -> dict:
    return {"alerts": [a.model_dump(mode="json") for a in get_alert_repository().list()]}


@router.get("/cases", summary="List compliance cases")
def list_cases() -> dict:
    return {"cases": [c.model_dump(mode="json") for c in get_case_repository().list()]}


class CaseUpdate(BaseModel):
    status: str | None = None
    assignee: str | None = None


@router.patch("/cases/{case_id}", summary="Update a case (status / assignee)")
def update_case(case_id: str, upd: CaseUpdate, _: Annotated[None, Auth]) -> dict:
    from app import casework
    try:
        case = casework.update_case(get_case_repository(), case_id=case_id,
                                    status=upd.status, assignee=upd.assignee)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    events.publish(events.EVENT_CASE_UPDATED, case.model_dump(mode="json"))
    return case.model_dump(mode="json")


# ── Engine-8 policy ingestion (FR-014) ─────────────────────────────────────
@router.post("/policies", summary="Ingest a signed Engine-8 policy (highest version wins)")
def ingest_policy(policy: CompliancePolicy, _: Annotated[None, Auth]) -> dict:
    return service.ingest_policy(policy)


@router.get("/policies/active", summary="The currently active policy")
def active_policy() -> dict:
    p = service.active_policy()
    return {"policy": p.model_dump(mode="json") if p else None}


# ── Data privacy: retention + right-to-erasure (Data Privacy domain) ────────
class RetentionRequest(BaseModel):
    lead_id: str
    category: str = "lead_data"


@router.post("/privacy/retention", summary="Register a lead's data retention clock")
def register_retention(req: RetentionRequest, _: Annotated[None, Auth]) -> dict:
    return service.register_retention(req.lead_id, category=req.category).model_dump(mode="json")


@router.get("/privacy/retention", summary="List retention records")
def list_retention() -> dict:
    return {"records": [r.model_dump(mode="json") for r in get_retention_repository().list()]}


@router.post("/privacy/retention/check", summary="Sweep (pre-deletion warn + auto-anonymize)")
def check_retention(_: Annotated[None, Auth]) -> dict:
    return {"states": service.check_retention()}


class ErasureRequest(BaseModel):
    lead_id: str
    reason: str = "gdpr_erasure_request"


@router.post("/privacy/erase", summary="Right-to-erasure (anonymize + drop from training)")
def erase_lead(req: ErasureRequest, _: Annotated[None, Auth]) -> dict:
    return service.erase_lead(req.lead_id, reason=req.reason).model_dump(mode="json")


# ── Coverage-gap / dropped-event monitoring (BR-3) ─────────────────────────
@router.get("/coverage", summary="Coverage report (per-source seq gaps + dead-letters)")
def coverage_report() -> dict:
    return service.coverage_report()


@router.post("/coverage/sweep", summary="Flag silent sources (coverage blind spots)")
def coverage_sweep(_: Annotated[None, Auth]) -> dict:
    return service.sweep_coverage()


# ── Dashboard + outbound events ────────────────────────────────────────────
@router.get("/dashboard", summary="Compliance RAG dashboard")
def dashboard() -> dict:
    return service.dashboard()


@router.get("/events", summary="Produced compliance events (Engine-8 feed)")
def list_events(event_type: str | None = None, limit: int = 200) -> dict:
    return {"events": events.get_events(event_type=event_type, limit=limit)}
