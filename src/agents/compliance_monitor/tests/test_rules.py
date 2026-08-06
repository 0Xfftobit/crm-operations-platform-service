"""Regulatory Updates (CEAAPSS-565 / US-5 / BR-6): versioning + two-step approval + staging."""
import pytest

from app import service
from app.repositories import get_rule_change_repository, get_rule_repository


def _propose():
    return service.propose_rule_change(
        rule_id="tcpa_quiet_hours", domain="marketing", definition={"start": "20:00"},
        change_reason="tighten window", changed_by="alice")


def test_two_step_approval_and_deploy():
    change = _propose()
    assert change.status == "proposed" and change.new_version == 1
    # Staged, not yet in production.
    assert get_rule_repository().get("tcpa_quiet_hours", "staging") is not None
    assert get_rule_repository().get("tcpa_quiet_hours", "production") is None

    service.approve_rule_change(change_id=change.change_id, approved_by="bob")
    _, prod = service.deploy_rule_change(change_id=change.change_id)
    assert prod.stage == "production" and prod.version == 1
    assert prod.definition == {"start": "20:00"}


def test_proposer_cannot_approve_own_change():
    change = _propose()
    with pytest.raises(ValueError, match="approver_must_differ"):
        service.approve_rule_change(change_id=change.change_id, approved_by="alice")


def test_deploy_requires_approval_first():
    change = _propose()
    with pytest.raises(ValueError, match="not_approved"):
        service.deploy_rule_change(change_id=change.change_id)


def test_reject_blocks_deploy():
    change = _propose()
    service.reject_rule_change(change_id=change.change_id, approved_by="bob", reason="too strict")
    assert get_rule_change_repository().get(change.change_id).status == "rejected"
    with pytest.raises(ValueError, match="not_approved"):
        service.deploy_rule_change(change_id=change.change_id)


def test_version_increments_across_deploys():
    c1 = _propose()
    service.approve_rule_change(change_id=c1.change_id, approved_by="bob")
    service.deploy_rule_change(change_id=c1.change_id)
    c2 = _propose()
    assert c2.previous_version == 1 and c2.new_version == 2


def test_emergency_override_is_single_actor_and_flagged():
    change, prod = service.emergency_rule_override(
        rule_id="dnc_scrub", domain="marketing", definition={"enabled": True},
        change_reason="incident", changed_by="carol")
    assert change.emergency is True and change.status == "deployed"
    assert prod.stage == "production"


def test_history_is_recorded(client):
    c = _propose()
    service.approve_rule_change(change_id=c.change_id, approved_by="bob")
    resp = client.get("/api/v1/rules/tcpa_quiet_hours/history")
    assert resp.status_code == 200
    assert len(resp.json()["changes"]) == 1
