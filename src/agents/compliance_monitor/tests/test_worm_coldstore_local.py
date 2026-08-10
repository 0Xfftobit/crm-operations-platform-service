"""Local-path WORM and cold-store integration (no S3/broker) for CEAAPSS-566."""
import os
from datetime import UTC, datetime, timedelta

import pytest

from app import audit, coldstore, service, worm
from app.core.config import get_settings
from app.domain.models import ActivityEvent, AuditEntry


def _set_local_store(monkeypatch, tmp_path, *, worm_dir=None, cold_dir=None,
                     cold_age_days="0"):
    if worm_dir:
        monkeypatch.setenv("WORM_GATEWAY", "local")
        monkeypatch.setenv("WORM_LOCAL_DIR", str(worm_dir))
    else:
        monkeypatch.setenv("WORM_GATEWAY", "none")
    if cold_dir:
        monkeypatch.setenv("COLD_STORE_GATEWAY", "local")
        monkeypatch.setenv("COLD_STORE_DIR", str(cold_dir))
    else:
        monkeypatch.setenv("COLD_STORE_GATEWAY", "none")
    monkeypatch.setenv("AUDIT_COLD_AGE_DAYS", cold_age_days)
    get_settings.cache_clear()
    worm.reset()
    coldstore.reset()
    audit.reset_ledger()


# ── Local WORM store: write-once, read-only, audit integration ──────────────
def test_worm_local_mirror_written_on_audit_append(tmp_path, monkeypatch):
    worm_dir = tmp_path / "worm"
    _set_local_store(monkeypatch, tmp_path, worm_dir=worm_dir)

    out = service.record_activity(
        ActivityEvent(event_type="comm_sent", entity_id="l1", engine="crm"))
    mirrored = service.worm_entry(out["audit_seq"])
    assert mirrored is not None
    assert mirrored["seq"] == out["audit_seq"]

    filename = worm_dir / f"{out['audit_seq']:012d}.json"
    assert filename.exists()
    assert not (os.stat(filename).st_mode & 0o200)  # write bit removed


def test_worm_local_refuses_overwrite(tmp_path):
    store = worm.LocalWormStore(str(tmp_path / "worm"))
    entry = AuditEntry(
        seq=1, event_id="e1", occurred_at=datetime.now(UTC),
        actor_type="engine", event_type="decision", prev_hash="0", entry_hash="h")
    store.put(entry)
    with pytest.raises(FileExistsError, match="WORM violation"):
        store.put(entry)


def test_worm_disabled_does_not_mirror(tmp_path, monkeypatch):
    _set_local_store(monkeypatch, tmp_path)

    out = service.record_activity(
        ActivityEvent(event_type="comm_sent", entity_id="l2", engine="crm"))
    assert service.worm_entry(out["audit_seq"]) is None


# ── Local cold store: archive, fetch, and archived seqs ─────────────────────
def test_cold_store_local_archive_and_fetch(tmp_path, monkeypatch):
    cold_dir = tmp_path / "cold"
    _set_local_store(monkeypatch, tmp_path, cold_dir=cold_dir)

    old = datetime(2020, 1, 1, tzinfo=UTC)
    out = service.record_activity(
        ActivityEvent(event_type="comm_sent", entity_id="l3", engine="crm",
                      occurred_at=old))

    result = service.audit_cold_tier()
    assert result["archived"] == 1
    assert service.cold_entry(out["audit_seq"])["seq"] == out["audit_seq"]
    assert out["audit_seq"] in coldstore.archived_seqs()


def test_cold_store_disabled_returns_none_and_zero_archive(tmp_path, monkeypatch):
    _set_local_store(monkeypatch, tmp_path)

    old = datetime(2020, 1, 1, tzinfo=UTC)
    service.record_activity(
        ActivityEvent(event_type="comm_sent", entity_id="l4", engine="crm",
                      occurred_at=old))

    result = service.audit_cold_tier()
    assert result["archived"] == 0
    assert service.cold_entry(1) is None


# ── WORM + cold store together: every purgable entry is both mirrored
# and archived before the hot ledger is deleted. ───────────────────────────
def test_worm_and_cold_store_cooperate_on_purge(tmp_path, monkeypatch):
    worm_dir = tmp_path / "worm"
    cold_dir = tmp_path / "cold"
    _set_local_store(monkeypatch, tmp_path, worm_dir=worm_dir, cold_dir=cold_dir)
    # Make entries instantly past the 7-year retention boundary for purge.
    monkeypatch.setenv("AUDIT_RETENTION_YEARS", "0")
    get_settings.cache_clear()

    old = datetime(2020, 1, 1, tzinfo=UTC)
    for i in range(3):
        service.record_activity(
            ActivityEvent(event_type="comm_sent", entity_id=f"l{i}",
                          engine="crm",
                          occurred_at=old + timedelta(seconds=i)))

    # Archive and retain a Merkle anchor before purging.
    assert service.audit_cold_tier()["archived"] == 3
    service.anchor_ledger(external_ref="pre-purge")

    purged = service.audit_purge()
    assert purged["purged"] == 3
    assert len(audit.all_entries()) == 0

    for seq in range(1, 4):
        assert service.worm_entry(seq) is not None
        assert service.cold_entry(seq) is not None

    # Local cold store writes one compact range file per contiguous run.
    assert len(os.listdir(cold_dir)) == 1
