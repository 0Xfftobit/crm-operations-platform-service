"""Global + per-sequence frequency cap (NFR / §7) — prevents over-messaging a contact.

The cap is per-contact and can be global (spans every active sequence) and/or per-sequence.
Counters are keyed by (tenant, contact_id, sequence, period). A real deployment uses Redis
atomic INCRs; this in-memory store has the same surface. ``remaining`` gates the guardrail;
``increment`` runs on a real send.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _as_utc(now: datetime) -> datetime:
    """Normalize to UTC before building period keys."""
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _day_key(tenant: str, contact_id: str, sequence_id: str, now: datetime) -> str:
    now = _as_utc(now)
    return f"freq:{tenant}:{contact_id}:{sequence_id}:d:{now.strftime('%Y-%m-%d')}"


def _week_key(tenant: str, contact_id: str, sequence_id: str, now: datetime) -> str:
    now = _as_utc(now)
    iso = now.isocalendar()
    return f"freq:{tenant}:{contact_id}:{sequence_id}:w:{iso.year}:{iso.week}"


class FrequencyStore:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = RLock()

    def count_day(self, tenant: str, contact_id: str, sequence_id: str, now: datetime) -> int:
        with self._lock:
            return self._counts.get(_day_key(tenant, contact_id, sequence_id, now), 0)

    def count_week(self, tenant: str, contact_id: str, sequence_id: str, now: datetime) -> int:
        with self._lock:
            return self._counts.get(_week_key(tenant, contact_id, sequence_id, now), 0)

    def remaining(
        self,
        tenant: str,
        contact_id: str,
        sequence_id: str,
        now: datetime,
        *,
        cap_day: int,
        cap_week: int,
        per_seq_day: int | None = None,
        per_seq_week: int | None = None,
    ) -> int:
        with self._lock:
            # Global cap first.
            global_remaining = min(
                cap_day - self._counts.get(_day_key(tenant, contact_id, "__global__", now), 0),
                cap_week - self._counts.get(_week_key(tenant, contact_id, "__global__", now), 0),
            )
            if per_seq_day is not None and per_seq_week is not None:
                day_key = _day_key(tenant, contact_id, sequence_id, now)
                week_key = _week_key(tenant, contact_id, sequence_id, now)
                seq_remaining = min(
                    per_seq_day - self._counts.get(day_key, 0),
                    per_seq_week - self._counts.get(week_key, 0),
                )
                return min(global_remaining, seq_remaining)
            return global_remaining

    def increment(
        self, tenant: str, contact_id: str, sequence_id: str, now: datetime
    ) -> None:
        with self._lock:
            sequences = ["__global__", sequence_id]
            for seq in sequences:
                for key in (
                    _day_key(tenant, contact_id, seq, now),
                    _week_key(tenant, contact_id, seq, now),
                ):
                    self._counts[key] = self._counts.get(key, 0) + 1

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()


class RedisFrequencyStore(FrequencyStore):
    """Redis-backed frequency store with atomic INCR and TTL-based expiry."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def _keys(
        self, tenant: str, contact_id: str, sequence_id: str, now: datetime
    ) -> tuple[str, str, int, int]:
        day_key = _day_key(tenant, contact_id, sequence_id, now)
        week_key = _week_key(tenant, contact_id, sequence_id, now)
        # TTL: day counters expire in 48h; week counters in 8 days.
        return day_key, week_key, 172800, 691200

    def count_day(self, tenant: str, contact_id: str, sequence_id: str, now: datetime) -> int:
        day_key, _, _, _ = self._keys(tenant, contact_id, sequence_id, now)
        raw = self._client.get(day_key)
        return int(raw) if raw else 0

    def count_week(self, tenant: str, contact_id: str, sequence_id: str, now: datetime) -> int:
        _, week_key, _, _ = self._keys(tenant, contact_id, sequence_id, now)
        raw = self._client.get(week_key)
        return int(raw) if raw else 0

    def remaining(
        self,
        tenant: str,
        contact_id: str,
        sequence_id: str,
        now: datetime,
        *,
        cap_day: int,
        cap_week: int,
        per_seq_day: int | None = None,
        per_seq_week: int | None = None,
    ) -> int:
        global_remaining = min(
            cap_day - self.count_day(tenant, contact_id, "__global__", now),
            cap_week - self.count_week(tenant, contact_id, "__global__", now),
        )
        if per_seq_day is not None and per_seq_week is not None:
            seq_remaining = min(
                per_seq_day - self.count_day(tenant, contact_id, sequence_id, now),
                per_seq_week - self.count_week(tenant, contact_id, sequence_id, now),
            )
            return min(global_remaining, seq_remaining)
        return global_remaining

    def increment(
        self, tenant: str, contact_id: str, sequence_id: str, now: datetime
    ) -> None:
        sequences = ["__global__", sequence_id]
        pipe = self._client.pipeline()
        for seq in sequences:
            day_key, week_key, day_ttl, week_ttl = self._keys(tenant, contact_id, seq, now)
            pipe.incr(day_key)
            pipe.expire(day_key, day_ttl)
            pipe.incr(week_key)
            pipe.expire(week_key, week_ttl)
        results = pipe.execute()
        logger.debug(
            "redis_frequency_increment",
            sequence=sequence_id,
            day_count=results[0] if results else None,
            week_count=results[4] if len(results) > 4 else None,
        )

    def clear(self) -> None:
        # Only clear local state; Redis TTLs make remote keys expire naturally.
        pass


def _redis_client() -> Any | None:
    settings = get_settings()
    if not settings.uses_cache:
        return None
    try:
        import redis as redis_lib
        client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_frequency_unavailable", error=str(exc))
        return None


def _create_store() -> FrequencyStore:
    client = _redis_client()
    if client is not None:
        return RedisFrequencyStore(client)
    return FrequencyStore()


_store: FrequencyStore | None = None
_store_lock = RLock()


def get_store() -> FrequencyStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = _create_store()
        return _store


def reset_store() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.clear()
        _store = None
