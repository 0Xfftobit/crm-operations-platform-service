"""Audit Trail (CEAAPSS-566): append-only immutability, hash-chain tamper detection, RBAC export."""
from app import audit


def _seed(n: int = 3) -> None:
    for i in range(n):
        audit.append(event_id=f"e{i}", event_type="decision", actor_type="engine",
                     actor_id="lead_scoring", entity_type="lead", entity_id=f"lead_{i}",
                     action="score", outcome="A", engine="lead-scoring")


def test_append_builds_a_verified_hash_chain():
    _seed(3)
    entries = audit.all_entries()
    assert [e.seq for e in entries] == [1, 2, 3]
    assert entries[0].prev_hash == "0" * 64            # genesis
    assert entries[1].prev_hash == entries[0].entry_hash
    v = audit.verify_chain()
    assert v["verified"] is True and v["entries"] == 3 and v["broken_at"] is None


def test_tampering_breaks_the_chain():
    _seed(3)
    # Mutating a record's content (bypassing append) must be detectable.
    audit.all_entries()[1].outcome = "D"
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["broken_at"] == 2


def test_search_filters_by_entity_and_event_type():
    _seed(3)
    audit.append(event_id="x", event_type="consent_update", actor_type="user",
                 entity_type="lead", entity_id="lead_9")
    assert len(audit.search(entity_id="lead_1")) == 1
    assert len(audit.search(event_type="consent_update")) == 1


def test_export_requires_a_privileged_role(client):
    _seed(2)
    # No role header → forbidden.
    assert client.get("/api/v1/audit/export").status_code == 403
    # Wrong role → forbidden.
    assert client.get("/api/v1/audit/export", headers={"X-Role": "agent"}).status_code == 403
    # Authorised role → JSON package with the verified chain.
    ok = client.get("/api/v1/audit/export", headers={"X-Role": "auditor"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["count"] == 2 and body["chain_verified"] is True


def test_export_csv(client):
    _seed(2)
    resp = client.get("/api/v1/audit/export?fmt=csv", headers={"X-Role": "compliance_officer"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "entry_hash" in resp.text
