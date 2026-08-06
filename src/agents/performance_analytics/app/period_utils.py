"""Shared period/bucket parsing helpers used by attribution and dashboards."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta


def _iso_bucket(dt: datetime, grain: str) -> str:
    if grain == "daily":
        return dt.strftime("%Y-%m-%d")
    if grain == "weekly":
        return dt.strftime("%Y-W%W")
    if grain == "monthly":
        return dt.strftime("%Y-%m")
    if grain == "quarterly":
        q = (dt.month - 1) // 3 + 1
        return dt.strftime(f"%Y-Q{q}")
    if grain == "yearly":
        return dt.strftime("%Y")
    return dt.isoformat()


def _parse_grain(period: str | None) -> str | None:
    if period in ("daily", "weekly", "monthly", "quarterly", "yearly"):
        return period
    return None


def _parse_datetime(s: str) -> datetime | None:
    s = s.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _parse_bounds(period: str | None) -> tuple[datetime, datetime] | None:
    """Return inclusive start/end for a period string, or None if it is a bucket keyword."""
    if not period:
        return None
    if _parse_grain(period):
        return None

    if "/" in period:
        left, right = period.split("/", 1)
        start = _parse_datetime(left) or datetime.min.replace(tzinfo=UTC)
        end = _parse_datetime(right) or datetime.max.replace(tzinfo=UTC)
        return start, end

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        start = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=UTC)
        return start, start + timedelta(days=1) - timedelta(microseconds=1)
    if re.fullmatch(r"\d{4}-W\d{2}", period):
        y, w = period.split("-W")
        start = datetime.strptime(f"{y}-W{w}-1", "%Y-W%W-%w").replace(tzinfo=UTC)
        return start, start + timedelta(weeks=1) - timedelta(microseconds=1)
    if re.fullmatch(r"\d{4}-\d{2}", period):
        start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1)
        else:
            end = start.replace(month=start.month + 1, day=1)
        return start, end - timedelta(microseconds=1)
    if re.fullmatch(r"\d{4}-Q[1-4]", period):
        y, q = period.split("-Q")
        q = int(q)
        start = datetime(int(y), q * 3 - 2, 1, tzinfo=UTC)
        end_month = q * 3 + 1 if q < 4 else 1
        end_year = int(y) + (1 if q == 4 else 0)
        end = datetime(end_year, end_month, 1, tzinfo=UTC)
        return start, end - timedelta(microseconds=1)
    if re.fullmatch(r"\d{4}", period):
        start = datetime(int(period), 1, 1, tzinfo=UTC)
        end = datetime(int(period) + 1, 1, 1, tzinfo=UTC)
        return start, end - timedelta(microseconds=1)

    return None


def _period_bucket(dt: datetime, period: str | None) -> str:
    grain = _parse_grain(period)
    if grain:
        return _iso_bucket(dt, grain)
    return ""


def _in_period(dt: datetime, period: str | None) -> bool:
    if not period:
        return True
    if _parse_grain(period):
        # Bucket keyword means "all rows" at this grain; grouping happens elsewhere.
        return True
    bounds = _parse_bounds(period)
    if bounds:
        return bounds[0] <= dt <= bounds[1]
    # Unknown period expression — include all data to avoid misleading empty results.
    return True
