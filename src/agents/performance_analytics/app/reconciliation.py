"""P2 — Reconciliation of financial metrics against finance source-of-truth."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.domain.models import ReconciliationRecord
from app.period_utils import _in_period
from app.repositories import (
    get_event_log_repository,
    get_finance_repository,
    get_outcome_repository,
    get_reconciliation_repository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _funded_amount_for_period(period: str, now: datetime | None = None) -> float:
    """Sum funded amounts from outcomes whose occurrence falls within `period`."""
    total = 0.0
    for o in get_outcome_repository().list(stage="funded"):
        if _in_period(o.occurred_at, period, now=now):
            total += o.amount or 0.0
    return total


def reconcile_period(period: str) -> dict[str, Any]:
    """Compare PAS funded amount to finance SoT and alert if tolerance breached."""
    settings = get_settings()
    now = _now()
    mart_amount = _funded_amount_for_period(period, now=now)
    finance = get_finance_repository().get(period)
    finance_amount = finance.funded_amount if finance else 0.0
    delta = mart_amount - finance_amount
    passed = abs(delta) / max(finance_amount, 1) <= settings.recon_tolerance

    rec = ReconciliationRecord(
        period=period,
        mart_amount=round(mart_amount, 2),
        finance_amount=round(finance_amount, 2),
        delta=round(delta, 2),
        passed=passed,
    )
    get_reconciliation_repository().add(rec)
    if not passed and finance:
        get_event_log_repository().add(
            "analytics.reconciliation_alert",
            {"period": period, "delta": round(delta, 2), "tolerance": settings.recon_tolerance},
        )
    return {
        "period": period,
        "mart_amount": round(mart_amount, 2),
        "finance_amount": round(finance_amount, 2),
        "delta": round(delta, 2),
        "tolerance": settings.recon_tolerance,
        "passed": passed,
    }


def reconcile_all() -> list[dict[str, Any]]:
    """Reconcile every period with a finance SoT record."""
    periods = {f.period for f in get_finance_repository().list()}
    return [reconcile_period(p) for p in sorted(periods)]
