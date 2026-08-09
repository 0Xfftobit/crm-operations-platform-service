"""Redis-backed idempotency store with in-memory fallback (§7)."""
from __future__ import annotations

from threading import RLock
from typing import Any

from app.core.config import get_settings
from app.persistence.memory_store import MemoryStore
from app.persistence.redis_client import RedisClient, StoreBackend, connect_redis

_backend: StoreBackend | None = None
_fallback: MemoryStore = MemoryStore()
_using_fallback: bool = False

_lock = RLock()


def reset_redis_store() -> None:
    global _backend, _using_fallback
    with _lock:
        _fallback.reset()
        backend = _backend
        _backend = None
        _using_fallback = False
    if isinstance(backend, RedisClient):
        try:
            backend.flush_db()
        except Exception:
            pass


def _loop_ttl_seconds() -> int:
    return int(get_settings().loop_budget_ttl_hours * 3600)


def _ttl_seconds() -> int:
    return get_settings().idempotency_ttl_days * 86400


def _resolve_backend() -> StoreBackend:
    global _backend, _using_fallback
    with _lock:
        if _backend is not None:
            return _backend
        settings = get_settings()

    client = None
    if settings.idempotency_backend == "redis":
        client = connect_redis(settings.redis_url)

    with _lock:
        if _backend is not None:
            return _backend
        if client is not None:
            _backend = client
            _using_fallback = False
        else:
            _backend = _fallback
            _using_fallback = True
        return _backend


def backend_info() -> dict[str, Any]:
    settings = get_settings()
    backend = _resolve_backend()
    with _lock:
        label = "memory"
        if isinstance(backend, RedisClient):
            label = "redis"
        elif _using_fallback and settings.idempotency_backend == "redis":
            label = "memory_fallback"
    return backend.stats(backend_label=label, ttl_days=settings.idempotency_ttl_days)


def get_key(key: str) -> dict[str, Any] | None:
    return _resolve_backend().get_key(key)


def set_key(key: str, value: dict[str, Any]) -> None:
    _resolve_backend().set_key(key, value, ttl_seconds=_ttl_seconds())


def idempotency_key(*, trigger_id: str, rule_id: str, action_key: str) -> str:
    return f"idem:{trigger_id}:{rule_id}:{action_key}"


def get_loop_budget(deal_id: str) -> int:
    return _resolve_backend().get_loop_budget(deal_id)


def incr_loop_budget(deal_id: str, *, increment: int = 1) -> int:
    return _resolve_backend().incr_loop_budget(
        deal_id, increment=increment, ttl_seconds=_loop_ttl_seconds(),
    )


def invalidate_rule_cache(rule_id: str) -> None:
    _resolve_backend().invalidate_rule_cache(rule_id)


def cache_rule_version(rule_id: str, version: str, rule: dict[str, Any]) -> None:
    _resolve_backend().cache_rule_version(rule_id, version, rule)


def get_cached_rule_version(rule_id: str, version: str) -> dict[str, Any] | None:
    return _resolve_backend().get_cached_rule_version(rule_id, version)


def record_violation() -> None:
    _resolve_backend().record_violation()


def violation_count() -> int:
    return _resolve_backend().violation_count()


def incr_round_robin(pool_key: str) -> int:
    return _resolve_backend().incr_round_robin(pool_key)


def cache_rule(rule_id: str, rule: dict[str, Any]) -> None:
    _resolve_backend().cache_rule(rule_id, rule)


def get_cached_rule(rule_id: str) -> dict[str, Any] | None:
    return _resolve_backend().get_cached_rule(rule_id)


def store_stats() -> dict[str, Any]:
    return backend_info()


def mark_event_seen(event_id: str) -> bool:
    """Durable event-id dedupe — True if first time seen."""
    return _resolve_backend().mark_event_seen(event_id, ttl_seconds=_ttl_seconds())
