"""P0 — Event ingestion: touches, outcomes, campaign costs, finance SoT."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    CampaignCost,
    FinanceSourceOfTruth,
    OutcomeEvent,
    TouchEvent,
)
from app.repositories import (
    get_campaign_cost_repository,
    get_event_log_repository,
    get_finance_repository,
    get_outcome_repository,
    get_touch_repository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _emit(event_type: str, payload: dict[str, Any]) -> None:
    get_event_log_repository().add(event_type, payload)


def ingest_touch(touch: TouchEvent) -> dict[str, Any]:
    """Ingest a touch event with idempotent dedup by event_id (FR-001)."""
    repo = get_touch_repository()
    existing = repo.get_by_event(touch.event_id)
    if existing is not None:
        return {"status": "duplicate", "event_id": touch.event_id}
    repo.add(touch)
    _emit("analytics.touch", {"event_id": touch.event_id, "touch_id": touch.touch_id, "channel": touch.channel})
    return {"status": "ingested", "event_id": touch.event_id, "touch_id": touch.touch_id}


def ingest_outcome(outcome: OutcomeEvent) -> dict[str, Any]:
    """Ingest a funnel/funded outcome (FR-001)."""
    repo = get_outcome_repository()
    existing = repo.get_by_event(outcome.event_id)
    if existing is not None:
        return {"status": "duplicate", "event_id": outcome.event_id}
    repo.add(outcome)
    _emit(
        "analytics.outcome",
        {"event_id": outcome.event_id, "deal_id": outcome.deal_id, "stage": outcome.stage},
    )
    return {"status": "ingested", "event_id": outcome.event_id, "outcome_id": outcome.outcome_id}


def ingest_campaign_cost(cost: CampaignCost) -> dict[str, Any]:
    """Ingest campaign spend for ROI attribution (FR-009)."""
    get_campaign_cost_repository().add(cost)
    _emit("analytics.cost", {"campaign_id": cost.campaign_id, "cost": cost.cost})
    return {"status": "ingested", "cost_id": cost.cost_id}


def ingest_finance_source(finance: FinanceSourceOfTruth) -> dict[str, Any]:
    """Record finance source-of-truth for reconciliation (FR-012)."""
    get_finance_repository().add(finance)
    _emit("analytics.finance", {"period": finance.period, "funded_amount": finance.funded_amount})
    return {"status": "ingested", "record_id": finance.record_id}
