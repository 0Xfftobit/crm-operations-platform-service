"""Durable SQL persistence (STORAGE_GATEWAY=postgres) exercised via SQLite — the same
dialect-agnostic ORM path. Verifies repos + the audit ledger survive a simulated restart and
stay tamper-evident.
"""
import pytest

from app import audit, service
from app.core.config import get_settings
from app.domain.models import ActivityEvent, Consent


@pytest.fixture
def sql_db(tmp_path, monkeypatch):
    """Point the app at a file-backed SQLite DB and create the schema (durable across sessions)."""
    db_file = tmp_path / "compliance.db"
    monkeypatch.setenv("STORAGE_GATEWAY", "postgres")     # gateway selector; dialect comes from URL
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    get_settings.cache_clear()
    from app.db import engine
    engine.reset_engine()
    engine.create_all()
    assert get_settings().uses_sql is True
    yield db_file
    engine.reset_engine()
    get_settings.cache_clear()


def test_consent_persists_across_restart(sql_db):
    service.upsert_consent(Consent(lead_id="lead_1", channel="sms", opted_in=True))
    # Simulate a process restart: drop the engine so the next access reconnects to the same file.
    from app.db import engine
    engine.reset_engine()
    got = service.get_consent_repository().get("lead_1", "sms")
    assert got is not None and got.opted_in is True


def test_audit_ledger_persists_and_chains_in_sql(sql_db):
    service.record_activity(ActivityEvent(event_type="comm_sent", entity_id="lead_1", engine="crm"))
    service.record_activity(ActivityEvent(event_type="comm_sent", entity_id="lead_2", engine="crm"))
    v = audit.verify_chain()
    assert v["verified"] is True and v["entries"] == 2 and v["broken_at"] is None
    from app.db import engine
    engine.reset_engine()                                  # restart
    assert len(audit.all_entries()) == 2                   # survived
    assert audit.verify_chain()["verified"] is True


def test_sql_ledger_tamper_is_detected(sql_db):
    service.record_activity(ActivityEvent(event_type="decision", entity_id="l", engine="scoring"))
    service.record_activity(ActivityEvent(event_type="decision", entity_id="l", engine="scoring"))
    # Tamper directly in the database (bypassing append), then re-verify.
    from app.db import engine
    from app.db.orm import AuditRow
    with engine.session() as s:
        row = s.get(AuditRow, 1)
        row.outcome = "TAMPERED"
        s.commit()
    result = audit.verify_chain()
    assert result["verified"] is False and result["broken_at"] == 1


def test_full_workflow_runs_on_sql(sql_db):
    # A representative slice: fairness disparity → alert + case, all persisted to SQL.
    service.evaluate_fairness("lead_assignment", "cohort",
                              {"a": {"selected": 42, "total": 100},
                               "b": {"selected": 31, "total": 100}})
    dash = service.dashboard()
    assert dash["rag"]["ai_fairness"] == "red"
    assert dash["open_cases"] >= 1
    assert dash["audit_chain_verified"] is True


def test_rule_workflow_persists_across_reads_on_sql(sql_db):
    # In SQL mode each repo.get() returns a fresh copy, so mutations must be written back.
    change = service.propose_rule_change(rule_id="r1", domain="marketing", definition={"x": 1},
                                         change_reason="tighten", changed_by="alice")
    service.approve_rule_change(change_id=change.change_id, approved_by="bob")
    # A fresh read must see 'approved' (the bug the live sweep caught was the mutation being lost).
    from app.repositories import get_rule_change_repository
    assert get_rule_change_repository().get(change.change_id).status == "approved"
    _, prod = service.deploy_rule_change(change_id=change.change_id)
    assert prod.stage == "production"
    assert get_rule_change_repository().get(change.change_id).status == "deployed"


def test_emergency_override_persists_on_sql(sql_db):
    change, prod = service.emergency_rule_override(
        rule_id="dnc", domain="marketing", definition={"e": 1}, change_reason="incident",
        changed_by="carol")
    assert change.status == "deployed" and prod.stage == "production"


def test_case_update_persists_on_sql(sql_db):
    service.evaluate_fairness("lead_assignment", "cohort",
                              {"a": {"selected": 42, "total": 100},
                               "b": {"selected": 31, "total": 100}})
    from app import casework
    from app.repositories import get_case_repository
    case = get_case_repository().list()[0]
    casework.update_case(get_case_repository(), case_id=case.case_id,
                         status="investigating", assignee="officer1")
    reread = get_case_repository().get(case.case_id)
    assert reread.status == "investigating" and reread.assignee == "officer1"


def test_retention_purge_on_sql_keeps_chain_verifiable(sql_db, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app import audit, coldstore
    from app.domain.models import ActivityEvent
    monkeypatch.setenv("COLD_STORE_GATEWAY", "local")
    monkeypatch.setenv("COLD_STORE_DIR", str(tmp_path / "cold"))
    monkeypatch.setenv("AUDIT_COLD_AGE_DAYS", "0")
    monkeypatch.setenv("AUDIT_RETENTION_YEARS", "0")
    get_settings.cache_clear()
    coldstore.reset()
    old = datetime(2020, 1, 1, tzinfo=UTC)
    for i in range(3):
        service.record_activity(ActivityEvent(event_type="comm_sent", entity_id=f"l{i}",
                                              occurred_at=old + timedelta(seconds=i)))
    service.anchor_ledger()
    assert service.audit_purge()["purged"] == 3
    assert audit.all_entries() == []                    # purged from the SQL hot table
    assert audit.verify_chain()["verified"] is True     # carry-hash watermark persisted in SQL
    # A new append continues the seq past the purged range.
    assert service.record_activity(ActivityEvent(event_type="comm_sent"))["audit_seq"] == 4
