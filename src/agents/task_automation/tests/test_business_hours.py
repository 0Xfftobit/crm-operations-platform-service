"""Unit tests — business hours and rule engine."""
from __future__ import annotations

from datetime import UTC, datetime

from app.business_hours import calculate_due_at, is_overdue
from app.engine.rule_engine import conditions_match


def test_conditions_and_logic() -> None:
    payload = {"stage": "underwriting", "grade": "A"}
    assert conditions_match(payload, [{"field": "stage", "operator": "eq", "value": "underwriting"}])
    assert not conditions_match(payload, [{"field": "stage", "operator": "eq", "value": "processing"}])


def test_weekend_skipped_for_due_date() -> None:
    # Saturday noon
    sat = datetime(2026, 6, 20, 17, 0, tzinfo=UTC)
    due = calculate_due_at(base=sat, delay_hours=1, timezone="America/Chicago")
    local = due.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    assert local.weekday() < 5


def test_is_overdue() -> None:
    assert is_overdue("2020-01-01T12:00:00+00:00")
    assert not is_overdue("2099-01-01T12:00:00+00:00")
