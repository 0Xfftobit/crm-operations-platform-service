"""Activity ingest, explainability enforcement, Engine-8 signals, and the RAG dashboard."""
from app import events, service
from app.domain.models import ActivityEvent


def test_activity_is_appended_to_the_ledger():
    out = service.record_activity(ActivityEvent(
        event_type="comm_sent", engine="follow-up", entity_type="lead", entity_id="lead_1",
        action="send", outcome="delivered"))
    assert out["audit_seq"] == 1 and out["alerts"] == []


def test_ai_decision_without_explainability_alerts():
    out = service.record_activity(ActivityEvent(
        event_type="decision", actor_type="engine", engine="lead-scoring",
        entity_type="lead", entity_id="lead_2", action="score", outcome="D"))
    assert len(out["alerts"]) == 1
    assert out["alerts"][0]["rule_id"] == "explainability_required"


def test_decision_with_explainability_is_clean():
    out = service.record_activity(ActivityEvent(
        event_type="decision", actor_type="engine", engine="lead-scoring",
        entity_type="lead", entity_id="lead_3", action="score", outcome="A",
        explain_ref="explain_123"))
    assert out["alerts"] == []


def test_engine8_signal_is_published_on_alert():
    service.record_activity(ActivityEvent(
        event_type="decision", actor_type="engine", engine="lead-scoring",
        entity_id="lead_4", action="score"))
    signals = events.get_events(event_type=events.EVENT_SIGNAL)
    assert signals and signals[0]["payload"]["engine"] == "compliance-monitoring"


def test_dashboard_reflects_rag_and_chain_health():
    # A critical fairness disparity → red on ai_fairness.
    service.evaluate_fairness("lead_assignment", "cohort",
                              {"a": {"selected": 42, "total": 100},
                               "b": {"selected": 31, "total": 100}})
    dash = service.dashboard()
    assert dash["rag"]["ai_fairness"] == "red"
    assert dash["overall"] == "red"
    assert dash["audit_chain_verified"] is True
    assert dash["open_cases"] >= 1


def test_dashboard_all_green_when_quiet():
    dash = service.dashboard()
    assert set(dash["rag"].values()) == {"green"}
    assert dash["overall"] == "green"
