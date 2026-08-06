"""ISO-8601 duration parsing for step delays + branch waits (System Design §2 step schema).

Sequence steps express delays as ISO-8601 durations (``PT0M``, ``PT48H``, ``PT72H``, ``P1D``). This
parses the day/hour/minute/second subset the cadence designer uses into ``timedelta``. Also parses
the duration embedded in timed branch conditions like ``no_open_after:PT48H``.
"""
from __future__ import annotations

import re
from datetime import timedelta

_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$")


def parse_duration(value: str) -> timedelta:
    """Parse an ISO-8601 duration (day/hour/min/sec subset). Invalid/empty ⇒ zero."""
    if not value:
        return timedelta(0)
    m = _PATTERN.match(value.strip())
    if not m or not any(m.groupdict().values()):
        return timedelta(0)
    parts = {k: int(v) for k, v in m.groupdict().items() if v}
    return timedelta(days=parts.get("days", 0), hours=parts.get("hours", 0),
                     minutes=parts.get("minutes", 0), seconds=parts.get("seconds", 0))


def condition_duration(condition: str) -> timedelta:
    """Extract the duration from a timed branch condition, e.g. ``no_open_after:PT48H`` → 48h."""
    if ":" in condition:
        return parse_duration(condition.split(":", 1)[1])
    return timedelta(0)
