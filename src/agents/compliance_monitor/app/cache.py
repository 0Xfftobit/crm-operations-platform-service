"""Consent hot-cache for the pre-send hook (design §9 — 'consent cached with short TTL').

The pre-send path must be fast and safe: it reads consent from a short-TTL cache backed by the
repository. ``CACHE_GATEWAY=redis`` shares the cache across replicas with native TTL; otherwise an
in-process TTL dict is used. Redis is lazily imported and degrades to memory if unavailable — a
cache miss simply falls through to the durable repository, so correctness never needs the cache.
"""
import json
import time
from threading import RLock

from app.core.config import get_settings
from app.domain.models import Consent

_lock = RLock()
_mem: dict[str, tuple[float, str]] = {}   # key → (expires_at, json)
_redis = None
_redis_url: str | None = None


def _key(lead_id: str, channel: str) -> str:
    return f"consent:{lead_id}:{channel}"


def _client():
    """Return a Redis client or None (memory fallback)."""
    global _redis, _redis_url
    settings = get_settings()
    if not settings.uses_cache:
        return None

    with _lock:
        if _redis is not None and _redis_url == settings.redis_url:
            return _redis

    try:
        import redis as redis_lib
        client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
    except Exception:  # noqa: BLE001 — broker/lib unavailable → memory fallback
        client = None

    with _lock:
        if client is not None:
            _redis = client
            _redis_url = settings.redis_url
        else:
            _redis = None
        return _redis


def get(lead_id: str, channel: str) -> Consent | None:
    key = _key(lead_id, channel)
    client = _client()
    if client is not None:
        raw = client.get(key)
        return Consent(**json.loads(raw)) if raw else None
    with _lock:
        hit = _mem.get(key)
        if hit and hit[0] > time.time():
            return Consent(**json.loads(hit[1]))
        _mem.pop(key, None)
    return None


def set(consent: Consent) -> None:  # noqa: A001 — cache verb, not the builtin
    key = _key(consent.lead_id, consent.channel)
    raw = json.dumps(consent.model_dump(mode="json"))
    ttl = get_settings().consent_cache_ttl_s
    client = _client()
    if client is not None:
        client.set(key, raw, ex=ttl)
    with _lock:
        _mem[key] = (time.time() + ttl, raw)


def invalidate(lead_id: str, channel: str) -> None:
    key = _key(lead_id, channel)
    client = _client()
    if client is not None:
        client.delete(key)
    with _lock:
        _mem.pop(key, None)


def reset() -> None:
    global _redis, _redis_url
    with _lock:
        _mem.clear()
        _redis, _redis_url = None, None


def redis_health() -> dict[str, str]:
    """Best-effort Redis connectivity check for /ready probes."""
    settings = get_settings()
    if not settings.uses_cache:
        return {"status": "disabled"}
    client = _client()
    return {"status": "ok"} if client is not None else {"status": "unavailable"}
