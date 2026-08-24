"""Durable Postgres-backed repositories (STORAGE_GATEWAY=postgres) exercised against a local sqlite DB
— proves the SQL layer round-trips domain objects. Runs when the `postgres` extra (sqlalchemy) is
installed; skipped otherwise so the lean in-memory build's suite is unaffected.
"""
import pytest

pytest.importorskip("sqlalchemy")


def _use_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_GATEWAY", "postgres")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fus.db")
    monkeypatch.setenv("DB_AUTO_CREATE", "true")
    monkeypatch.setenv("DB_SCHEMA", "")
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.db import engine as eng
    eng.reset_engine()
    import app.repositories as repos
    # older builds cached SQL repos in _sql_repos; newer ones construct fresh
    getattr(repos, "_sql_repos", {}).clear()


def test_sql_repositories_round_trip(monkeypatch, tmp_path):
    _use_sqlite(monkeypatch, tmp_path)
    from app.core.config import get_settings
    assert get_settings().uses_sql is True

    import app.repositories as repos
    from app.domain.models import Enrollment, OutreachRecord, SequenceDef
    repos.reset_repositories()

    repos.get_sequence_repository().put(SequenceDef(
        sequence_id="s1", tenant_id="t", name="Welcome", version=1))
    repos.get_enrollment_repository().put(Enrollment(
        enrollment_id="e1", contact_id="c1", sequence_id="s1", tenant_id="t", state="active"))
    repos.get_ledger_repository().add(OutreachRecord(
        id="r1", enrollment_id="e1", contact_id="c1", tenant_id="t", step_id="step0",
        occurrence=1, status="sent", channel="email"))

    # drop any cached repo instances → forces a fresh read straight from the DB (durability check)
    getattr(repos, "_sql_repos", {}).clear()
    assert repos.get_sequence_repository().get("s1", "t").name == "Welcome"
    assert repos.get_enrollment_repository().get("e1").state == "active"
    assert repos.get_ledger_repository().sent_exists("e1", "step0", 1) is True
    assert len(repos.get_enrollment_repository().for_contact("c1", "t")) == 1


def test_memory_path_untouched_by_default(monkeypatch):
    # with the default memory gateway, the getters return the in-memory singletons (no SQLAlchemy use)
    monkeypatch.setenv("STORAGE_GATEWAY", "memory")
    monkeypatch.setenv("DATABASE_URL", "")
    from app.core.config import get_settings
    get_settings.cache_clear()
    import app.repositories as repos
    assert repos.get_sequence_repository() is repos._sequences
