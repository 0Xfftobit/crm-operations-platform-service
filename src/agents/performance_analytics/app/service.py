"""Orchestration layer for the Performance Analytics System."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.responses import PlainTextResponse, Response

from app import anomaly_detector, attribution, dashboard_builder, metrics_collector, reconciliation, semantic
from app.core.config import get_settings
from app.domain.models import (
    CampaignCost,
    FinanceSourceOfTruth,
    MetricDefinition,
    OutcomeEvent,
    ReportSchedule,
    TouchEvent,
)
from app.messaging import kafka_bus
from app.pdf import build_csv, build_pdf
from app.repositories import (
    get_alert_repository,
    get_attribution_repository,
    get_campaign_cost_repository,
    get_finance_repository,
    get_metric_repository,
    get_outcome_repository,
    get_reconciliation_repository,
    get_report_repository,
    get_touch_repository,
    reset_repositories,
)


def _now() -> datetime:
    return datetime.now(UTC)


def reset_state() -> None:
    """Clear all in-memory state (used by tests)."""
    reset_repositories()
    semantic.seed_builtin_metrics()


def seed() -> None:
    """Seed the governed semantic layer on startup."""
    semantic.seed_builtin_metrics()


# ── Ingestion ────────────────────────────────────────────────────────────────


def ingest_touch(touch: TouchEvent) -> dict[str, Any]:
    result = metrics_collector.ingest_touch(touch)
    kafka_bus.produce_touch(touch)
    return result


def ingest_outcome(outcome: OutcomeEvent) -> dict[str, Any]:
    result = metrics_collector.ingest_outcome(outcome)
    kafka_bus.produce_outcome(outcome)
    return result


def ingest_campaign_cost(cost: CampaignCost) -> dict[str, Any]:
    return metrics_collector.ingest_campaign_cost(cost)


def ingest_finance_source(finance: FinanceSourceOfTruth) -> dict[str, Any]:
    return metrics_collector.ingest_finance_source(finance)


# ── Governed metrics ────────────────────────────────────────────────────────


def publish_metric(definition: MetricDefinition) -> dict[str, Any]:
    m = semantic.publish_metric_definition(definition)
    return m.model_dump(mode="json")


def list_metrics(status: str | None = None) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in semantic.list_metric_definitions(status=status)]


def get_metric(name: str, version: int | None = None) -> dict[str, Any] | None:
    m = semantic.get_metric_definition(name, version=version)
    return m.model_dump(mode="json") if m else None


def get_metric_history(name: str) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in semantic.get_metric_history(name)]


def get_metric_lineage(name: str) -> dict[str, Any] | None:
    return semantic.get_metric_lineage(name)


def get_kpi(
    metric_name: str,
    *,
    period: str | None = None,
    dims: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = semantic.get_metric_definition(metric_name)
    if definition is None:
        return {"error": f"unknown metric {metric_name}"}
    return dashboard_builder.compute_kpi(definition, period=period, dims=dims, filters=filters)


# ── Dashboards ───────────────────────────────────────────────────────────────


def build_funnel(filters: dict[str, Any] | None = None, period: str | None = None) -> dict[str, Any]:
    return dashboard_builder.build_funnel(filters=filters, period=period)


def build_rep_scorecards(filters: dict[str, Any] | None = None, period: str | None = None) -> list[dict[str, Any]]:
    return dashboard_builder.build_rep_scorecards(filters=filters, period=period)


def build_rep_scorecard(
    rep_id: str, filters: dict[str, Any] | None = None, period: str | None = None
) -> dict[str, Any] | None:
    return dashboard_builder.build_rep_scorecard(rep_id, filters=filters, period=period)


def build_leaderboard(
    metric: str = "funded_amount", filters: dict[str, Any] | None = None, period: str | None = None
) -> list[dict[str, Any]]:
    return dashboard_builder.build_leaderboard(metric=metric, filters=filters, period=period)


def build_channel_effectiveness(
    filters: dict[str, Any] | None = None,
    period: str | None = None,
    breakdown: list[str] | None = None,
) -> list[dict[str, Any]]:
    return dashboard_builder.build_channel_effectiveness(filters=filters, period=period, breakdown=breakdown)


def build_channel_heatmaps(filters: dict[str, Any] | None = None, period: str | None = None) -> dict[str, Any]:
    return dashboard_builder.build_channel_heatmaps(filters=filters, period=period)


def build_channel_recommendations(
    filters: dict[str, Any] | None = None, period: str | None = None
) -> list[dict[str, Any]]:
    return dashboard_builder.build_channel_recommendations(filters=filters, period=period)


def build_cohorts(dim: str = "acquisition_month", period: str | None = None) -> list[dict[str, Any]]:
    return dashboard_builder.build_cohort_analysis(dim=dim, period=period)


def compute_ab_uplift(treatment: list[str], control: list[str], stage: str = "funded") -> dict[str, Any]:
    return dashboard_builder.compute_ab_uplift(treatment, control, stage=stage)


# ── Attribution ──────────────────────────────────────────────────────────────


def compute_attribution(deal_id: str, model: str | None = None) -> dict[str, Any] | None:
    rec = attribution.compute_attribution_for_deal(deal_id, model=model)
    return rec.model_dump(mode="json") if rec else None


def compute_all_attribution(model: str | None = None, period: str | None = None) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in attribution.compute_all_attribution(model=model, period=period)]


def attribution_summary(model: str | None = None, period: str | None = None) -> dict[str, Any]:
    return attribution.attribution_summary(model=model, period=period)


def attribution_by_entity(entity: str, *, model: str | None = None, period: str | None = None) -> list[dict[str, Any]]:
    return attribution.attribution_by_entity(entity, model=model, period=period)


def export_attribution(
    entity: str,
    report_format: str,
    *,
    model: str | None = None,
    period: str | None = None,
) -> tuple[bytes, str]:
    rows = attribution.attribution_by_entity(entity, model=model, period=period)
    filename = f"attribution_{entity}_{model or get_settings().attribution_default_model}"
    if period:
        filename += f"_{period}"

    headers = ["entity", "entity_id", "attributed_revenue"]
    table = [[row["entity"], row["entity_id"], str(row["attributed_revenue"])] for row in rows]

    if report_format == "csv":
        content = build_csv(headers, table)
        return content, f"{filename}.csv"

    pdf = build_pdf(f"Attribution by {entity}", headers, table)
    return pdf, f"{filename}.pdf"


# ── Reconciliation ───────────────────────────────────────────────────────────


def reconcile(period: str) -> dict[str, Any]:
    return reconciliation.reconcile_period(period)


def reconcile_all() -> list[dict[str, Any]]:
    return reconciliation.reconcile_all()


def get_reconciliations(period: str | None = None) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in get_reconciliation_repository().list(period=period)]


# ── Alerts / anomaly ────────────────────────────────────────────────────────


def detect_anomalies(metric_name: str = "app_to_fund_conversion", period: str | None = None) -> list[dict[str, Any]]:
    return [
        a.model_dump(mode="json") for a in anomaly_detector.detect_anomalies(metric_name=metric_name, period=period)
    ]


def threshold_alert(metric_name: str, threshold: float, direction: str = "below") -> dict[str, Any]:
    return anomaly_detector.threshold_alert(metric_name=metric_name, threshold=threshold, direction=direction)


def get_alerts(metric: str | None = None, severity: str | None = None) -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in get_alert_repository().list(metric=metric, severity=severity)]


# ── Reports / export ────────────────────────────────────────────────────────


def schedule_report(report: ReportSchedule) -> dict[str, Any]:
    get_report_repository().put(report)
    return report.model_dump(mode="json")


def list_reports() -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in get_report_repository().list()]


def _report_data(
    report: ReportSchedule, filters: dict[str, Any] | None
) -> tuple[list[str], list[list[Any]], dict[str, Any]]:
    return _export_data(report.metric, period=report.period, filters=filters)


def run_report(report_id: str, *, report_format: str | None = None, filters: dict[str, Any] | None = None) -> Response:
    report = get_report_repository().get(report_id)
    if report is None:
        return PlainTextResponse("unknown report", status_code=404)
    fmt = report_format or report.report_format
    headers, rows, _ = _report_data(report, filters)
    if not headers:
        return PlainTextResponse("no data for report", status_code=400)
    if fmt == "csv":
        body = build_csv(headers, rows)
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={report.name}.csv"},
        )
    if fmt == "pdf":
        body = build_pdf(report.name, headers, rows)
        return Response(
            content=body,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={report.name}.pdf"},
        )
    return PlainTextResponse("unsupported format", status_code=400)


def _export_data(
    metric_name: str,
    *,
    period: str | None = None,
    dims: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> tuple[list[str], list[list[Any]], dict[str, Any]]:
    """Resolve headers, rows, and raw data for export/report endpoints."""
    if metric_name == "funnel":
        data = dashboard_builder.build_funnel(filters=filters, period=period)
        headers = ["stage", "count", "median_cycle_days", "conversion_from_previous", "drop_off_rate"]
        rows = [[r[h] for h in headers] for r in data["stages"]]
        return headers, rows, data
    if metric_name == "channel_effectiveness":
        data = dashboard_builder.build_channel_effectiveness(filters=filters, period=period)
        headers = [
            "channel",
            "touches",
            "delivered",
            "delivery_rate",
            "open_rate",
            "reply_rate",
            "conversion_contribution_rate",
        ]
        rows = [[r.get(h) for h in headers] for r in data]
        return headers, rows, {"rows": data}

    definition = semantic.get_metric_definition(metric_name)
    if definition is None:
        return [], [], {"error": f"unknown metric {metric_name}"}
    data = dashboard_builder.compute_kpi(definition, period=period, dims=dims, filters=filters)
    if not data["rows"]:
        return [], [], data
    headers = list(data["rows"][0].keys())
    rows = [[r.get(h) for h in headers] for r in data["rows"]]
    return headers, rows, data


def export_metric(
    metric_name: str,
    *,
    report_format: str = "csv",
    period: str | None = None,
    dims: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> Response:
    headers, rows, _ = _export_data(metric_name, period=period, dims=dims, filters=filters)
    if not headers:
        return PlainTextResponse("unknown metric or no data", status_code=404)
    if report_format == "csv":
        body = build_csv(headers, rows)
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={metric_name}.csv"},
        )
    if report_format == "pdf":
        body = build_pdf(metric_name, headers, rows)
        return Response(
            content=body,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={metric_name}.pdf"},
        )
    return PlainTextResponse("unsupported format", status_code=400)


# ── Stats ────────────────────────────────────────────────────────────────────


def get_stats() -> dict[str, Any]:
    return {
        "touches": len(get_touch_repository().list()),
        "outcomes": len(get_outcome_repository().list()),
        "campaign_costs": len(get_campaign_cost_repository().list()),
        "finance_records": len(get_finance_repository().list()),
        "metric_definitions": len(get_metric_repository().list()),
        "attribution_records": len(get_attribution_repository().list()),
        "reconciliations": len(get_reconciliation_repository().list()),
        "alerts": len(get_alert_repository().list()),
    }
