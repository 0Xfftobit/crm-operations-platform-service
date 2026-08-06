"""Global frequency cap (NFR / §7) — prevents over-messaging a contact across ALL cadences.

The cap is per-contact and GLOBAL (spans every active sequence), enforced day and week. Counters are
keyed by (tenant, contact_id, period). A real deployment uses Redis atomic INCRs; this in-memory
store has the same surface. ``remaining`` gates the guardrail; ``increment`` runs on a real send.
"""
from __future__ import annotations

from datetime import datetime


def _day_key(tenant: str, contact_id: str, now: datetime) -> tuple:
    return (tenant, contact_id, "d", now.strftime("%Y-%m-%d"))


def _week_key(tenant: str, contact_id: str, now: datetime) -> tuple:
    iso = now.isocalendar()
    return (tenant, contact_id, "w", iso.year, iso.week)


class FrequencyStore:
    def __init__(self) -> None:
        self._counts: dict[tuple, int] = {}

    def count_day(self, tenant: str, contact_id: str, now: datetime) -> int:
        return self._counts.get(_day_key(tenant, contact_id, now), 0)

    def count_week(self, tenant: str, contact_id: str, now: datetime) -> int:
        return self._counts.get(_week_key(tenant, contact_id, now), 0)

    def remaining(self, tenant: str, contact_id: str, now: datetime, *, cap_day: int,
                  cap_week: int) -> int:
        return min(cap_day - self.count_day(tenant, contact_id, now),
                   cap_week - self.count_week(tenant, contact_id, now))

    def increment(self, tenant: str, contact_id: str, now: datetime) -> None:
        for key in (_day_key(tenant, contact_id, now), _week_key(tenant, contact_id, now)):
            self._counts[key] = self._counts.get(key, 0) + 1

    def clear(self) -> None:
        self._counts.clear()


_store = FrequencyStore()


def get_store() -> FrequencyStore:
    return _store


def reset_store() -> None:
    _store.clear()
