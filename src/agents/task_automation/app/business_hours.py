"""Business-hours-aware due date calculation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def _is_business_day(dt: datetime) -> bool:
    return dt.weekday() < 5


def _in_business_hours(dt: datetime, *, start_hour: int, end_hour: int) -> bool:
    return start_hour <= dt.hour < end_hour


def calculate_due_at(
    *,
    base: datetime | None = None,
    delay_hours: float = 0,
    timezone: str = "America/Chicago",
    business_start_hour: int = 9,
    business_end_hour: int = 17,
) -> datetime:
    """Compute due datetime respecting business hours (Mon–Fri, no odd hours)."""
    tz = ZoneInfo(timezone)
    current = (base or datetime.now(UTC)).astimezone(tz)

    remaining = timedelta(hours=delay_hours)
    while remaining > timedelta(0):
        if not _is_business_day(current):
            current = current.replace(hour=business_start_hour, minute=0, second=0, microsecond=0)
            current += timedelta(days=1)
            continue
        if current.hour < business_start_hour:
            current = current.replace(hour=business_start_hour, minute=0, second=0, microsecond=0)
        elif current.hour >= business_end_hour:
            current = (current + timedelta(days=1)).replace(
                hour=business_start_hour, minute=0, second=0, microsecond=0
            )
            continue

        end_of_day = current.replace(hour=business_end_hour, minute=0, second=0, microsecond=0)
        available = end_of_day - current
        if remaining <= available:
            current += remaining
            remaining = timedelta(0)
        else:
            remaining -= available
            current = (current + timedelta(days=1)).replace(
                hour=business_start_hour, minute=0, second=0, microsecond=0
            )

    if not _in_business_hours(current, start_hour=business_start_hour, end_hour=business_end_hour):
        if current.hour >= business_end_hour:
            current = (current + timedelta(days=1)).replace(
                hour=business_start_hour, minute=0, second=0, microsecond=0
            )
        elif current.hour < business_start_hour:
            current = current.replace(hour=business_start_hour, minute=0, second=0, microsecond=0)
        while not _is_business_day(current):
            current += timedelta(days=1)

    return current.astimezone(UTC)


def is_overdue(due_at: str, *, now: datetime | None = None) -> bool:
    ref = now or datetime.now(UTC)
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return ref > due
