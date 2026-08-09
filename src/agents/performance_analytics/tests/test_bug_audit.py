"""Bug-audit regression tests for the Performance Analytics System."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app import reconciliation
from app.attribution import _time_decay
from app.domain.models import AttributionRecord, FinanceSourceOfTruth, OutcomeEvent, TouchEvent
from app.period_utils import _in_period, _parse_bounds
from app.repositories import (
    AttributionRepository,
    TouchRepository,
    get_finance_repository,
    get_outcome_repository,
)


def test_in_period_accepts_iso_duration():
    now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    assert _in_period(now - timedelta(hours=12), "P1D", now=now) is True
    assert _in_period(now - timedelta(days=2), "P1D", now=now) is False


def test_parse_bounds_returns_none_for_bucket_keyword():
    assert _parse_bounds("monthly") is None


def test_parse_bounds_bucket_and_iso():
    now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    bucket = _parse_bounds("2026-08-01", now=now)
    assert bucket is not None
    assert bucket[0] == datetime(2026, 8, 1, tzinfo=UTC)

    iso = _parse_bounds("P7D", now=now)
    assert iso is not None
    assert iso[0] == now - timedelta(days=7)
    assert iso[1] == now


def test_reconcile_iso_duration_excludes_outside_window(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(reconciliation, "_now", lambda: now)

    outcomes = get_outcome_repository()
    outcomes.add(
        OutcomeEvent(
            outcome_id="outcome-in",
            contact_pid="c-in",
            deal_id="d-in",
            stage="funded",
            amount=1000.0,
            occurred_at=now - timedelta(hours=12),
            event_type="loan.funded",
        )
    )
    outcomes.add(
        OutcomeEvent(
            outcome_id="outcome-out",
            contact_pid="c-out",
            deal_id="d-out",
            stage="funded",
            amount=500.0,
            occurred_at=now - timedelta(days=2),
            event_type="loan.funded",
        )
    )

    get_finance_repository().add(
        FinanceSourceOfTruth(period="P1D", funded_amount=1000.0)
    )

    result = reconciliation.reconcile_period("P1D")
    assert result["passed"] is True
    assert result["mart_amount"] == 1000.0
    assert result["finance_amount"] == 1000.0
    assert result["delta"] == 0.0


def test_time_decay_uses_supplied_conversion_time():
    now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
    t1 = TouchEvent(
        touch_id="t1",
        contact_pid="c1",
        deal_id="d1",
        channel="email",
        sent_at=now - timedelta(days=7),
    )
    t2 = TouchEvent(
        touch_id="t2",
        contact_pid="c1",
        deal_id="d1",
        channel="email",
        sent_at=now - timedelta(days=1),
    )

    # With conversion = now and a 7-day half life, the first touch should get
    # exactly half the raw weight of a touch at conversion time.
    shares = _time_decay([t1, t2], funded_at=now)
    assert len(shares) == 2
    assert round(sum(c for _, c in shares), 4) == 1.0
    assert shares[0][0].touch_id == "t1"
    assert shares[1][0].touch_id == "t2"
    # The first touch (7 days before conversion with half-life 7) is weighted 0.5,
    # the second (1 day before) is weighted 0.5^(1/7). The first should be smaller.
    assert shares[0][1] < shares[1][1]


def test_attribution_repository_tuple_key():
    repo = AttributionRepository()
    record1 = AttributionRecord(deal_id="d1", funded_amount=100.0, model="time_decay")
    record2 = AttributionRecord(deal_id="d1", funded_amount=100.0, model="linear")
    repo.put(record1)
    repo.put(record2)
    assert repo.get("d1", "time_decay") is record1
    assert repo.get("d1", "linear") is record2
    assert len(repo.list()) == 2


def test_touch_repository_is_thread_safe():
    repo = TouchRepository()

    def worker(n: int) -> None:
        for i in range(100):
            repo.add(
                TouchEvent(
                    touch_id=f"t-{n}-{i}",
                    contact_pid="c1",
                    channel="email",
                    sent_at=datetime(2026, 8, 9, tzinfo=UTC),
                )
            )

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(repo.list()) == 1000
