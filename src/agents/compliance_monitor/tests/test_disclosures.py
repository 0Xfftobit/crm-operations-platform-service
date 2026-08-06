"""Disclosure / adverse-action clocks (FR-006 / US-3 / BR-5): ECOA 30-day window + warnings."""
from datetime import UTC, datetime, timedelta

from app import disclosures, service
from app.core.config import get_settings
from app.domain.models import DisclosureTimer
from app.repositories import get_alert_repository, get_disclosure_repository


def test_start_sets_a_30_day_due_date():
    timer = service.start_disclosure("deal_1")
    assert (timer.due_at - timer.started_at).days == 30
    assert get_disclosure_repository().get("deal_1") is not None


def test_status_transitions():
    settings = get_settings()
    now = datetime(2026, 6, 1, tzinfo=UTC)
    ok = DisclosureTimer(deal_id="d", started_at=now, due_at=now + timedelta(days=20))
    assert disclosures.status(ok, settings=settings, now=now)["state"] == "ok"
    warn = DisclosureTimer(deal_id="d", started_at=now, due_at=now + timedelta(days=5))
    assert disclosures.status(warn, settings=settings, now=now)["state"] == "warning"
    late = DisclosureTimer(deal_id="d", started_at=now, due_at=now - timedelta(days=1))
    assert disclosures.status(late, settings=settings, now=now)["state"] == "overdue"
    done = DisclosureTimer(deal_id="d", started_at=now, due_at=now, satisfied=True)
    assert disclosures.status(done, settings=settings, now=now)["state"] == "satisfied"


def test_overdue_sweep_raises_a_critical_alert():
    now = datetime.now(UTC)
    get_disclosure_repository().put(
        DisclosureTimer(deal_id="late_deal", started_at=now - timedelta(days=31),
                        due_at=now - timedelta(days=1)))
    statuses = service.check_disclosures()
    assert any(s["state"] == "overdue" for s in statuses)
    assert any(a.severity == "critical" and a.domain == "regulatory"
               for a in get_alert_repository().list())


def test_satisfy_stops_the_clock():
    service.start_disclosure("deal_2")
    timer = service.satisfy_disclosure("deal_2")
    assert timer.satisfied is True
