"""TCPA quiet-hours enforcement in the contact's local timezone (NFR guardrail, §7/§8).

The allowed sending window is ``[quiet_end, quiet_start)`` local time (default 08:00–21:00). A send
outside that window is a per-message TCPA violation, so this is a **fail-closed** check: if the
contact's timezone is unknown we fall back to a conservative default tz rather than assume it ok.
``next_allowed`` clamps a proposed time forward to the next legal window (the scheduler uses it).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(tz: str, default_tz: str) -> ZoneInfo:
    for candidate in (tz, default_tz, "UTC"):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def is_allowed(now: datetime, *, tz: str, default_tz: str = "UTC",
               quiet_end: int = 8, quiet_start: int = 21) -> bool:
    """True if ``now`` falls inside the contact-local allowed window [quiet_end, quiet_start)."""
    local = now.astimezone(_zone(tz, default_tz))
    return quiet_end <= local.hour < quiet_start


def next_allowed(now: datetime, *, tz: str, default_tz: str = "UTC",
                 quiet_end: int = 8, quiet_start: int = 21) -> datetime:
    """Clamp ``now`` forward to the next legal window; returns a UTC datetime."""
    zone = _zone(tz, default_tz)
    local = now.astimezone(zone)
    if quiet_end <= local.hour < quiet_start:
        return now.astimezone(UTC)
    target = local.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    if local.hour >= quiet_start:                 # past today's window → tomorrow's open
        target = target + timedelta(days=1)
    return target.astimezone(UTC)
