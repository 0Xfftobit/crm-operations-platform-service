"""Performance Analytics System REST API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app import service
from app.api.deps import parse_role_headers, require_api_key
from app.domain.models import (
    CampaignCost,
    FinanceSourceOfTruth,
    MetricDefinition,
    OutcomeEvent,
    ReportSchedule,
    TouchEvent,
)

router = APIRouter(tags=["performance-analytics"])
AuthDep = Depends(require_api_key)
RoleCtxDep = Depends(parse_role_headers)


def _rls_filter(filters: dict[str, Any] | None, ctx: dict[str, Any]) -> dict[str, Any]:
    """Apply role-based row-level security (BR-5)."""
    filters = filters or {}
    if ctx.get("tenant_id"):
        filters["tenant_id"] = ctx["tenant_id"]
    if ctx.get("team_id"):
        filters["team_id"] = ctx["team_id"]
    if ctx.get("rep_id"):
        filters["rep_id"] = ctx["rep_id"]
    return filters


# ── Ingestion ────────────────────────────────────────────────────────────────


@router.post("/ingest/touch", summary="Ingest a touch event (FR-001)")
def post_touch(
    touch: TouchEvent,
    _: None = AuthDep,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    return service.ingest_touch(touch)


@router.post("/ingest/outcome", summary="Ingest a funnel/funded outcome (FR-001)")
def post_outcome(
    outcome: OutcomeEvent,
    _: None = AuthDep,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    return service.ingest_outcome(outcome)


@router.post("/ingest/campaign-cost", summary="Ingest campaign spend (FR-009)")
def post_campaign_cost(
    cost: CampaignCost,
    _: None = AuthDep,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    return service.ingest_campaign_cost(cost)


@router.post("/ingest/finance-source", summary="Record finance source-of-truth (FR-012)")
def post_finance_source(
    finance: FinanceSourceOfTruth,
    _: None = AuthDep,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    return service.ingest_finance_source(finance)


# ── Governed metrics ─────────────────────────────────────────────────────────


@router.get("/metrics/definitions", summary="List governed metric definitions")
def get_metric_definitions(status: str | None = None) -> list[dict[str, Any]]:
    return service.list_metrics(status=status)


@router.get("/metrics/definitions/{name}", summary="Get a metric definition")
def get_metric_definition(name: str, version: int | None = None) -> dict[str, Any]:
    m = service.get_metric(name, version=version)
    if m is None:
        raise HTTPException(status_code=404, detail="unknown metric")
    return m


@router.get("/metrics/definitions/{name}/history", summary="Metric definition history (FR-016)")
def get_metric_definition_history(name: str) -> list[dict[str, Any]]:
    return service.get_metric_history(name)


@router.get("/metrics/definitions/{name}/lineage", summary="Metric lineage (FR-016)")
def get_metric_definition_lineage(name: str) -> dict[str, Any]:
    lineage = service.get_metric_lineage(name)
    if lineage is None:
        raise HTTPException(status_code=404, detail="unknown metric")
    return lineage


@router.post("/metrics/definitions", summary="Publish a new metric version")
def post_metric_definition(
    definition: MetricDefinition,
    _: None = AuthDep,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    return service.publish_metric(definition)


@router.get("/explore", summary="Self-serve metric exploration (FR-015)")
def get_explore() -> dict[str, Any]:
    return {
        "metrics": service.list_metrics(),
        "dimensions": [
            "team",
            "rep",
            "channel",
            "campaign",
            "sequence",
            "source",
            "segment",
            "stage",
            "area",
            "property_type",
            "period",
        ],
        "period_formats": [
            "daily",
            "weekly",
            "monthly",
            "quarterly",
            "yearly",
            "YYYY-MM-DD",
            "YYYY-W##",
            "YYYY-MM",
            "YYYY-Q#",
            "YYYY",
            "start/end",
        ],
    }


@router.get("/kpi/{metric_name}", summary="Query a governed KPI (FR-011)")
def get_kpi(
    metric_name: str,
    period: str | None = None,
    dim: list[str] | None = Query(default=None),
    tenant_id: str | None = None,
    team_id: str | None = None,
    rep_id: str | None = None,
    channel: str | None = None,
    segment: str | None = None,
    stage: str | None = None,
    campaign: str | None = None,
    source: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    filters = _rls_filter(
        {
            k: v
            for k, v in {
                "tenant_id": tenant_id,
                "team_id": team_id,
                "rep_id": rep_id,
                "channel": channel,
                "segment": segment,
                "stage": stage,
                "campaign": campaign,
                "source": source,
            }.items()
            if v is not None
        },
        ctx,
    )
    return service.get_kpi(metric_name, period=period, dims=dim or [], filters=filters)


# ── Dashboards ───────────────────────────────────────────────────────────────


@router.get("/funnel", summary="Pipeline funnel with cycle times (FR-004)")
def get_funnel(
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    rep_id: str | None = None,
    segment: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    filters = _rls_filter(
        {
            k: v
            for k, v in {"tenant_id": tenant_id, "team_id": team_id, "rep_id": rep_id, "segment": segment}.items()
            if v
        },
        ctx,
    )
    return service.build_funnel(filters=filters, period=period)


@router.get("/scorecards/reps", summary="Rep scorecards (FR-006)")
def get_scorecards(
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> list[dict[str, Any]]:
    filters = _rls_filter({k: v for k, v in {"tenant_id": tenant_id, "team_id": team_id}.items() if v}, ctx)
    return service.build_rep_scorecards(filters=filters, period=period)


@router.get("/scorecards/reps/{rep_id}", summary="Single rep scorecard")
def get_scorecard(
    rep_id: str,
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    filters = _rls_filter({k: v for k, v in {"tenant_id": tenant_id, "team_id": team_id}.items() if v}, ctx)
    card = service.build_rep_scorecard(rep_id, filters=filters, period=period)
    if card is None:
        raise HTTPException(status_code=404, detail="rep not found")
    return card


@router.get("/leaderboard", summary="Agent leaderboard (FR-007)")
def get_leaderboard(
    metric: str = "funded_amount",
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> list[dict[str, Any]]:
    filters = _rls_filter({k: v for k, v in {"tenant_id": tenant_id, "team_id": team_id}.items() if v}, ctx)
    return service.build_leaderboard(metric=metric, filters=filters, period=period)


@router.get("/channel-effectiveness", summary="Channel effectiveness report (FR-008/009)")
def get_channel_effectiveness(
    period: str | None = None,
    by: list[str] | None = Query(default=None),
    tenant_id: str | None = None,
    team_id: str | None = None,
    rep_id: str | None = None,
    channel: str | None = None,
    segment: str | None = None,
    stage: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> list[dict[str, Any]]:
    filters = _rls_filter(
        {
            k: v
            for k, v in {
                "tenant_id": tenant_id,
                "team_id": team_id,
                "rep_id": rep_id,
                "channel": channel,
                "segment": segment,
                "stage": stage,
            }.items()
            if v
        },
        ctx,
    )
    return service.build_channel_effectiveness(filters=filters, period=period, breakdown=by or ["channel"])


@router.get("/channel-heatmaps", summary="Time-of-day heatmaps (FR-008)")
def get_channel_heatmaps(
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> dict[str, Any]:
    filters = _rls_filter({k: v for k, v in {"tenant_id": tenant_id, "team_id": team_id}.items() if v}, ctx)
    return service.build_channel_heatmaps(filters=filters, period=period)


@router.get("/channel-recommendations", summary="Automated channel recommendations")
def get_channel_recommendations(
    period: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> list[dict[str, Any]]:
    filters = _rls_filter({k: v for k, v in {"tenant_id": tenant_id, "team_id": team_id}.items() if v}, ctx)
    return service.build_channel_recommendations(filters=filters, period=period)


@router.get("/cohorts", summary="Cohort conversion analysis (FR-013)")
def get_cohorts(
    dim: str = "acquisition_month",
    period: str | None = None,
    ctx: dict[str, Any] = RoleCtxDep,
) -> list[dict[str, Any]]:
    return service.build_cohorts(dim=dim, period=period)


# ── Attribution ──────────────────────────────────────────────────────────────


@router.get("/attribution", summary="Touch-level attribution for a funded deal (FR-008)")
def get_attribution(
    deal_id: str,
    model: str | None = Query(default=None),
) -> dict[str, Any]:
    rec = service.compute_attribution(deal_id, model=model)
    if rec is None:
        raise HTTPException(status_code=404, detail="deal not found or not funded")
    return rec


@router.post("/attribution/compute", summary="Compute attribution for all funded deals")
def post_attribution_compute(
    _: None = AuthDep,
    model: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return service.compute_all_attribution(model=model, period=period)


@router.get("/attribution/summary", summary="Attribution summary ranked by revenue")
def get_attribution_summary(
    model: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> dict[str, Any]:
    return service.attribution_summary(model=model, period=period)


@router.get("/attribution/entities", summary="Attributed revenue by entity (CEAAPSS-562)")
def get_attribution_entities(
    entity: str = Query(
        ...,
        description="Entity attribute: channel, campaign_id, sequence_id, source, rep_id, team_id, property_type, area",
    ),
    model: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return service.attribution_by_entity(entity, model=model, period=period)


@router.get("/attribution/export", summary="Export attributed revenue by entity (CSV/PDF)")
def get_attribution_export(
    entity: str = Query(..., description="Entity attribute: channel, campaign_id, rep_id, etc."),
    report_format: str = Query(default="csv"),
    model: str | None = Query(default=None),
    period: str | None = Query(default=None),
) -> Response:
    content, filename = service.export_attribution(entity, report_format, model=model, period=period)
    media_type = "text/csv" if report_format == "csv" else "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── A/B uplift ───────────────────────────────────────────────────────────────


class ABUpliftRequest(BaseModel):
    treatment: list[str]
    control: list[str]
    stage: str = "funded"


@router.post("/ab-uplift", summary="Engine A/B uplift (FR-007)")
def post_ab_uplift(
    body: ABUpliftRequest,
    _: None = AuthDep,
) -> dict[str, Any]:
    return service.compute_ab_uplift(body.treatment, body.control, stage=body.stage)


# ── Reconciliation ───────────────────────────────────────────────────────────


@router.post("/reconcile/{period}", summary="Reconcile vs finance SoT (FR-012)")
def post_reconcile(
    period: str,
    _: None = AuthDep,
) -> dict[str, Any]:
    return service.reconcile(period)


@router.get("/reconciliations", summary="Reconciliation history")
def get_reconciliations(period: str | None = None) -> list[dict[str, Any]]:
    return service.get_reconciliations(period=period)


# ── Alerts / anomaly ────────────────────────────────────────────────────────


@router.post("/detect-anomalies/{metric_name}", summary="Detect KPI anomalies")
def post_detect_anomalies(
    metric_name: str,
    _: None = AuthDep,
    period: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    return service.detect_anomalies(metric_name=metric_name, period=period)


@router.post("/threshold-alert/{metric_name}", summary="Set a threshold alert")
def post_threshold_alert(
    metric_name: str,
    threshold: float,
    _: None = AuthDep,
    direction: str = Query(default="below"),
) -> dict[str, Any]:
    return service.threshold_alert(metric_name=metric_name, threshold=threshold, direction=direction)


@router.get("/alerts", summary="List anomaly alerts")
def get_alerts(metric: str | None = None, severity: str | None = None) -> list[dict[str, Any]]:
    return service.get_alerts(metric=metric, severity=severity)


# ── Reports / export ────────────────────────────────────────────────────────


class ReportScheduleRequest(BaseModel):
    name: str
    metric: str
    period: str
    report_format: str = "csv"
    recipients: list[str] | None = None
    frequency: str = "weekly"


@router.post("/reports/schedule", summary="Schedule a report (FR-014)")
def post_schedule_report(
    body: ReportScheduleRequest,
    _: None = AuthDep,
) -> dict[str, Any]:
    report = ReportSchedule(
        name=body.name,
        metric=body.metric,
        period=body.period,
        report_format=body.report_format,
        recipients=body.recipients or [],
        frequency=body.frequency,
    )
    return service.schedule_report(report)


@router.get("/reports", summary="List scheduled reports")
def get_reports() -> list[dict[str, Any]]:
    return service.list_reports()


@router.get("/reports/{report_id}/run", summary="Run a scheduled report")
def get_run_report(
    report_id: str,
    report_format: str | None = Query(default=None),
    ctx: dict[str, Any] = RoleCtxDep,
) -> Response:
    return service.run_report(report_id, report_format=report_format, filters=_rls_filter({}, ctx))


@router.get("/export/{metric_name}", summary="Export a metric as CSV or PDF")
def export_metric(
    metric_name: str,
    report_format: str = Query(default="csv"),
    period: str | None = Query(default=None),
    dim: list[str] | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    rep_id: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    segment: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    campaign: str | None = Query(default=None),
    ctx: dict[str, Any] = RoleCtxDep,
) -> Response:
    filters = _rls_filter(
        {
            k: v
            for k, v in {
                "tenant_id": tenant_id,
                "team_id": team_id,
                "rep_id": rep_id,
                "channel": channel,
                "segment": segment,
                "stage": stage,
                "campaign": campaign,
            }.items()
            if v
        },
        ctx,
    )
    return service.export_metric(
        metric_name,
        report_format=report_format,
        period=period,
        dims=dim or [],
        filters=filters,
    )


# ── Stats / health ───────────────────────────────────────────────────────────


@router.get("/stats", summary="Capture statistics")
def get_stats() -> dict[str, Any]:
    return service.get_stats()
