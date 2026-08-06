"""Persistence tests — Postgres/SQLite ledger constraint + Redis backend."""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.ledger import append_ledger_entry, reset_ledger
from app.persistence import db, redis_store
from app.persistence.db import persist_ledger_entry, reset_db


@pytest.fixture
def sqlite_backend(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db_path = tmp_path / "test_tasks.db"
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("TASK_AUTOMATION_DB_PATH", str(db_path))
    get_settings.cache_clear()
    reset_db()
    reset_ledger()
    yield
    reset_db()
    reset_ledger()
    get_settings.cache_clear()


def test_ledger_unique_constraint_sqlite(sqlite_backend: None) -> None:
    """UNIQUE(trigger_id, rule_id, action_key) rejects duplicate ledger rows."""
    entry = {
        "ledger_id": "ldg_test_001",
        "action_type": "create_task",
        "lead_id": "L-001",
        "deal_id": "L-001",
        "rule_id": "rule_abc",
        "rule_version": "1",
        "inputs": {"task_type": "call"},
        "inputs_hash": "abc123",
        "outputs": {"task_id": "crm_1"},
        "actor": "system",
        "outcome": "executed",
        "trigger_id": "trg_001",
        "action_key": "create_task",
        "recorded_at": "2026-01-01T00:00:00+00:00",
        "immutable": True,
    }
    first = persist_ledger_entry(entry)
    assert first["inserted"] is True
    assert first["duplicate"] is False

    duplicate = persist_ledger_entry({**entry, "ledger_id": "ldg_test_002", "outcome": "executed"})
    assert duplicate["inserted"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["ledger_id"] == "ldg_test_001"

    stats = db.store_stats()
    assert stats["backend"] == "sqlite"
    assert stats["tables"]["action_ledger"] == 1


def test_append_ledger_entry_dedupes_in_memory_and_db(sqlite_backend: None) -> None:
    row1 = append_ledger_entry(
        action_type="request_document",
        lead_id="L-dedup",
        rule_id="rule_x",
        trigger_id="trg_x",
        action_key="request_document",
        inputs={"doc": "w2"},
        outputs={"document_types": ["w2"]},
    )
    row2 = append_ledger_entry(
        action_type="request_document",
        lead_id="L-dedup",
        rule_id="rule_x",
        trigger_id="trg_x",
        action_key="request_document",
        inputs={"doc": "w2"},
        outputs={"document_types": ["w2"]},
    )
    assert row1["ledger_id"] == row2["ledger_id"]
    assert row2.get("duplicate") is True
    assert row2["outcome"] == "deduped"
    assert db.store_stats()["tables"]["action_ledger"] == 1


def test_redis_backend_with_fakeredis(monkeypatch: pytest.MonkeyPatch) -> None:
    import fakeredis
    import redis as redis_lib

    fake = fakeredis.FakeRedis(decode_responses=True)

    def _fake_from_url(url: str, **kwargs: object):
        return fake

    monkeypatch.setenv("IDEMPOTENCY_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    get_settings.cache_clear()
    redis_store.reset_redis_store()
    monkeypatch.setattr(redis_lib, "from_url", _fake_from_url)

    redis_store.set_key("test:key", {"status": "ok"})
    assert redis_store.get_key("test:key") == {"status": "ok"}

    redis_store.incr_loop_budget("deal-1", increment=3)
    assert redis_store.get_loop_budget("deal-1") == 3

    redis_store.cache_rule_version("rule_1", "2", {"rule_id": "rule_1", "version": "2"})
    cached = redis_store.get_cached_rule_version("rule_1", "2")
    assert cached["rule_id"] == "rule_1"

    info = redis_store.backend_info()
    assert info["backend"] == "redis"

    redis_store.reset_redis_store()
    get_settings.cache_clear()


@pytest.mark.integration
def test_postgres_ledger_unique_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration test — requires Postgres at DATABASE_URL."""
    url = get_settings().database_url
    if not url:
        pytest.skip("DATABASE_URL not set")

    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    reset_db()
    reset_ledger()

    try:
        init = db.init_schema()
        if not init.get("initialized"):
            pytest.skip(init.get("error", "postgres unavailable"))

        entry = {
            "ledger_id": "ldg_pg_001",
            "action_type": "create_task",
            "lead_id": "L-pg",
            "deal_id": "L-pg",
            "rule_id": "rule_pg",
            "inputs": {},
            "inputs_hash": "hash1",
            "outputs": {},
            "outcome": "executed",
            "trigger_id": "trg_pg",
            "action_key": "create_task",
            "recorded_at": "2026-01-01T00:00:00+00:00",
        }
        first = persist_ledger_entry(entry)
        assert first["inserted"] is True

        dup = persist_ledger_entry({**entry, "ledger_id": "ldg_pg_002"})
        assert dup["duplicate"] is True
        assert dup["ledger_id"] == "ldg_pg_001"
        assert db.store_stats()["tables"]["action_ledger"] >= 1
    finally:
        reset_db()
        reset_ledger()
        get_settings.cache_clear()
