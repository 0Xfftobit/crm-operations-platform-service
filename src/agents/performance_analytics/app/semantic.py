"""P1 — Governed semantic/metric layer: versioned KPI definitions."""

from __future__ import annotations

from typing import Any

from app.domain.models import MetricDefinition
from app.repositories import get_metric_repository

BUILTIN_METRICS: list[dict[str, Any]] = [
    {
        "name": "lead_to_app_conversion",
        "label": "Lead → App Conversion",
        "metric_type": "ratio",
        "numerator": "app_count",
        "denominator": "lead_count",
        "description": "Applications / leads within the attribution window",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "segment", "period"],
    },
    {
        "name": "app_to_fund_conversion",
        "label": "App → Fund Conversion",
        "metric_type": "ratio",
        "numerator": "funded_count",
        "denominator": "app_count",
        "description": "Funded loans / applications within 90-day window",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "segment", "period", "channel"],
    },
    {
        "name": "funded_amount",
        "label": "Funded Amount",
        "metric_type": "count",
        "description": "Sum of funded loan amounts",
        "owner": "finance",
        "dimensions": ["team", "rep", "channel", "campaign", "period"],
    },
    {
        "name": "deal_count",
        "label": "Funded Deal Count",
        "metric_type": "count",
        "description": "Count of funded deals",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "campaign", "period"],
    },
    {
        "name": "conversion_rate",
        "label": "Overall Conversion Rate",
        "metric_type": "ratio",
        "numerator": "funded_count",
        "denominator": "lead_count",
        "description": "Funded deals / leads",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "segment", "channel", "period"],
    },
    {
        "name": "response_rate",
        "label": "Response Rate",
        "metric_type": "rate",
        "description": "Replied touches / delivered touches",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "engagement_rate",
        "label": "Engagement Rate",
        "metric_type": "rate",
        "description": "Engaged touches / delivered touches",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "open_rate",
        "label": "Open Rate",
        "metric_type": "rate",
        "description": "Opened touches / delivered touches",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "click_rate",
        "label": "Click Rate",
        "metric_type": "rate",
        "description": "Clicked touches / delivered touches",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "reply_rate",
        "label": "Reply Rate",
        "metric_type": "rate",
        "description": "Replied touches / delivered touches",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "opt_out_rate",
        "label": "Opt-Out Rate",
        "metric_type": "rate",
        "description": "Opted-out touches / delivered touches",
        "owner": "compliance",
        "dimensions": ["team", "rep", "channel", "segment", "period"],
    },
    {
        "name": "cost_per_funded_loan",
        "label": "Cost Per Funded Loan",
        "metric_type": "average",
        "description": "Campaign spend / funded deals attributed to the campaign",
        "owner": "finance",
        "dimensions": ["campaign", "channel", "period"],
    },
    {
        "name": "roi_per_campaign",
        "label": "Campaign ROI",
        "metric_type": "ratio",
        "description": "(Attributed revenue - campaign cost) / campaign cost × 100",
        "owner": "finance",
        "dimensions": ["campaign", "channel", "period"],
    },
    {
        "name": "median_cycle_time",
        "label": "Median Cycle Time",
        "metric_type": "average",
        "description": "Median days from lead to funded",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "segment", "period"],
    },
    {
        "name": "pipeline_velocity",
        "label": "Pipeline Velocity",
        "metric_type": "average",
        "description": "Average days per funnel stage",
        "owner": "analytics-eng",
        "dimensions": ["team", "rep", "segment", "period"],
    },
]


def seed_builtin_metrics() -> None:
    """Load the governed metric definitions into the repository."""
    repo = get_metric_repository()
    existing_names = set()
    for m in repo.list():
        existing_names.add(m.name)
    for spec in BUILTIN_METRICS:
        if spec["name"] in existing_names:
            continue
        repo.add(MetricDefinition(**spec))


def publish_metric_definition(definition: MetricDefinition) -> MetricDefinition:
    """Publish a new version of a metric (BR-1), superseding the previous version."""
    repo = get_metric_repository()
    latest = repo.get(definition.name)
    if latest is not None:
        latest.status = "superseded"
        definition.version = latest.version + 1
    else:
        definition.version = 1
    definition.status = "published"
    repo.add(definition)
    return definition


def get_metric_definition(name: str, version: int | None = None) -> MetricDefinition | None:
    return get_metric_repository().get(name, version=version)


def list_metric_definitions(status: str | None = None) -> list[MetricDefinition]:
    return get_metric_repository().list(status=status)


def get_metric_history(name: str) -> list[MetricDefinition]:
    """Return all versions of a metric, ordered by version."""
    return get_metric_repository().history(name)


def get_metric_lineage(name: str) -> dict[str, Any] | None:
    """Return upstream metric lineage for the latest version of a metric."""
    m = get_metric_repository().get(name)
    if m is None:
        return None
    return {
        "metric": m.name,
        "version": m.version,
        "depends_on": m.depends_on,
        "lineage": m.lineage,
    }
