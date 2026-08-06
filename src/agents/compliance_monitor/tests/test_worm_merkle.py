"""WORM object-lock mirror (local + S3/moto) and Merkle-root anchoring + inclusion proofs."""
import os

import pytest

from app import merkle, service, worm
from app.core.config import get_settings
from app.domain.models import ActivityEvent, AuditEntry


def _entry(seq: int) -> AuditEntry:
    return AuditEntry(seq=seq, event_id=f"e{seq}", occurred_at="2026-08-01T00:00:00+00:00",
                      actor_type="engine", event_type="decision", prev_hash="0", entry_hash="h")


# ── Local WORM store (object-lock semantics) ───────────────────────────────
def test_local_worm_is_write_once_and_readonly(tmp_path):
    store = worm.LocalWormStore(str(tmp_path))
    store.put(_entry(1))
    assert store.get(1)["seq"] == 1
    # Second write to the same seq is refused (object-lock).
    with pytest.raises(FileExistsError):
        store.put(_entry(1))
    # File is read-only.
    mode = os.stat(tmp_path / f"{1:012d}.json").st_mode
    assert not (mode & 0o200)


def test_worm_mirror_wired_into_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORM_GATEWAY", "local")
    monkeypatch.setenv("WORM_LOCAL_DIR", str(tmp_path / "worm"))
    get_settings.cache_clear()
    worm.reset()
    out = service.record_activity(
        ActivityEvent(event_type="comm_sent", entity_id="l", engine="crm"))
    mirrored = service.worm_entry(out["audit_seq"])
    assert mirrored is not None and mirrored["seq"] == out["audit_seq"]


def test_s3_worm_object_lock(monkeypatch):
    moto = pytest.importorskip("moto")
    from moto import mock_aws
    with mock_aws():
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="cmp-audit", ObjectLockEnabledForBucket=True)
        store = worm.S3WormStore("cmp-audit", "audit/", retention_days=7)
        store.put(_entry(5))
        assert store.get(5)["seq"] == 5
    assert moto  # importorskip guard used


# ── Merkle tree primitives ─────────────────────────────────────────────────
def test_merkle_root_and_inclusion_proofs():
    leaves = [f"{i:064x}" for i in range(5)]
    root = merkle.build_root(leaves)
    for i, leaf in enumerate(leaves):
        proof = merkle.inclusion_proof(leaves, i)
        assert merkle.verify_proof(leaf, proof, root) is True
    # A wrong leaf must not verify.
    assert merkle.verify_proof("deadbeef", merkle.inclusion_proof(leaves, 0), root) is False


def test_merkle_edge_cases():
    assert merkle.build_root([]) == "0" * 64
    single = ["ab" * 32]
    assert merkle.verify_proof(single[0], merkle.inclusion_proof(single, 0),
                               merkle.build_root(single)) is True


# ── Anchoring wired through the service + ledger ───────────────────────────
def test_anchor_and_prove_inclusion():
    for _ in range(4):
        service.record_activity(ActivityEvent(event_type="decision", entity_id="l", engine="s"))
    anchor = service.anchor_ledger(external_ref="test")
    assert anchor is not None and anchor["seq_to"] == 4
    proof = service.inclusion_proof(2)
    assert proof is not None and proof["verified"] is True
    assert proof["merkle_root"] == anchor["merkle_root"]


def test_inclusion_proof_none_when_unanchored():
    service.record_activity(ActivityEvent(event_type="decision", entity_id="l", engine="s"))
    assert service.inclusion_proof(1) is None      # not anchored yet


def test_anchor_endpoints(client, api_headers):
    service.record_activity(ActivityEvent(event_type="decision", entity_id="l", engine="s"))
    assert client.post("/api/v1/audit/anchor", headers=api_headers).status_code == 200
    anchors = client.get("/api/v1/audit/anchors").json()["anchors"]
    assert len(anchors) == 1
    proof = client.get("/api/v1/audit/1/proof")
    assert proof.status_code == 200 and proof.json()["verified"] is True
