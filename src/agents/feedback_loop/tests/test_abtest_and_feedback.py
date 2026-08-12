"""A/B test edge cases, label quarantine, match rate, and feedback-loop events."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.repositories import get_event_log_repository

API_KEY = "test-key"
BASE = "/api/v1"


def _prediction_payload(
    engine: str = "lead_scoring",
    entity_id: str = "entity-1",
    prob: float = 0.8,
    features: dict | None = None,
) -> dict:
    return {
        "engine_name": engine,
        "model_version": "1.0.0",
        "entity_id": entity_id,
        "features_ref": "fs://snap/2026-06-15T18:00Z/entity-1",
        "features": features or {"f1": 1.0, "f2": 0.5},
        "output": {"probability": prob},
        "scored_at": datetime.now(UTC).isoformat(),
    }


def _outcome_payload(
    prediction_id: str | None = None,
    entity_id: str = "entity-1",
    event_type: str = "deal.funded",
) -> dict:
    return {
        "prediction_id": prediction_id,
        "entity_id": entity_id,
        "engine_name": "lead_scoring",
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
    }


def _seed(client: TestClient, n: int, *, engine: str = "lead_scoring") -> None:
    for i in range(n):
        label = 1.0 if i % 2 == 0 else 0.0
        prob = 0.8 if label else 0.2
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                engine=engine,
                entity_id=f"{engine}-{i}",
                prob=prob,
                features={"f1": float(i % 2), "f2": prob},
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"{engine}-{i}",
                event_type="deal.funded" if label else "deal.lost",
            ),
        )


def _create_experiment(client: TestClient, **overrides: object) -> str:
    payload = {
        "name": "edge-test",
        "hypothesis": "edge",
        "engine_name": "lead_scoring",
        "min_duration_hours": 0.0,
        "variants": [
            {"name": "control", "traffic_pct": 50},
            {"name": "challenger", "traffic_pct": 50},
        ],
    }
    payload.update(overrides)
    return client.post(f"{BASE}/experiments", json=payload).json()["experiment_id"]


def test_experiment_guardrail_early_stopping_flag(client: TestClient) -> None:
    exp_id = _create_experiment(
        client,
        guardrail_metric="retention",
        guardrail_threshold=0.5,
    )
    for i in range(20):
        resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
        variant = resp["variant"]
        client.post(
            f"{BASE}/experiments/{exp_id}/record",
            json={
                "variant": variant,
                "entity_id": f"lead-{i}",
                "metric_value": 1.0,
                "guardrail_value": 0.1 if variant == "challenger" else 0.8,
            },
        )
    report = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
    assert report["guardrail_breached"] is True
    assert report["status"] == "running"


def test_experiment_promote_blocked_when_not_significant(client: TestClient) -> None:
    exp_id = _create_experiment(client, p_value_threshold=0.0)
    for i in range(40):
        resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
        variant = resp["variant"]
        # Equal conversion rates -> no significance.
        value = 1.0 if i % 2 == 0 else 0.0
        client.post(
            f"{BASE}/experiments/{exp_id}/record",
            json={"variant": variant, "entity_id": f"lead-{i}", "metric_value": value},
        )
    evaluate = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
    assert evaluate["variants"]["challenger"].get("significant") is False

    blocked = client.post(f"{BASE}/experiments/{exp_id}/promote", json={}).json()
    assert blocked["status"] == "not_significant"


def test_experiment_record_result_after_completion_is_rejected(client: TestClient) -> None:
    exp_id = _create_experiment(client)
    for i in range(10):
        resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
        client.post(
            f"{BASE}/experiments/{exp_id}/record",
            json={"variant": resp["variant"], "entity_id": f"lead-{i}", "metric_value": 1.0},
        )
    promoted = client.post(f"{BASE}/experiments/{exp_id}/promote", json={"force": True}).json()
    assert promoted["status"] == "promoted"

    rejected = client.post(
        f"{BASE}/experiments/{exp_id}/record",
        json={"variant": "control", "entity_id": "new-lead", "metric_value": 1.0},
    ).json()
    assert rejected["status"] == "experiment_not_running"


def test_experiment_winner_promotion_tie_and_force(client: TestClient) -> None:
    exp_id = _create_experiment(client, p_value_threshold=0.0)
    for i in range(40):
        resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
        variant = resp["variant"]
        value = 1.0 if i % 2 == 0 else 0.0
        client.post(
            f"{BASE}/experiments/{exp_id}/record",
            json={"variant": variant, "entity_id": f"lead-{i}", "metric_value": value},
        )

    blocked = client.post(f"{BASE}/experiments/{exp_id}/promote", json={}).json()
    assert blocked["status"] == "not_significant"

    forced = client.post(f"{BASE}/experiments/{exp_id}/promote", json={"force": True}).json()
    assert forced["status"] == "promoted"
    assert forced["winner"] in ("control", "challenger")


def test_orphan_explicit_feedback_is_quarantined(client: TestClient) -> None:
    fb = {
        "prediction_id": None,
        "entity_id": "orphan-feedback",
        "engine_name": "lead_scoring",
        "feedback_type": "explicit",
        "label_value": 1.0,
        "actor": "lo:mike",
    }
    res = client.post(f"{BASE}/feedback", json=fb)
    assert res.status_code == 200
    labels = client.get(f"{BASE}/labels").json()
    assert len(labels) == 0
    quarantine = get_event_log_repository().list(event_type="label.quarantined")
    assert len(quarantine) == 1
    assert quarantine[0]["payload"]["reason"] == "orphan_feedback"


def test_capture_stats_match_rate(client: TestClient) -> None:
    pred_ids = []
    for i in range(5):
        pred = client.post(f"{BASE}/predictions", json=_prediction_payload(entity_id=f"mr-{i}")).json()
        pred_ids.append(pred["prediction_id"])
    for i, pred_id in enumerate(pred_ids):
        if i < 3:
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(prediction_id=pred_id, entity_id=f"mr-{i}"),
            )
    stats = client.get(f"{BASE}/stats", params={"engine_name": "lead_scoring"}).json()
    assert stats["predictions"] == 5
    assert stats["labels"] == 3
    assert stats["match_rate"] == 0.6


def test_capture_stats_match_rate_zero_with_no_predictions(client: TestClient) -> None:
    stats = client.get(f"{BASE}/stats", params={"engine_name": "lead_scoring"}).json()
    assert stats["predictions"] == 0
    assert stats["labels"] == 0
    assert stats["match_rate"] == 0.0


def test_feedback_captured_events_emitted(client: TestClient) -> None:
    pred = client.post(f"{BASE}/predictions", json=_prediction_payload(entity_id="event-ent")).json()
    client.post(f"{BASE}/outcomes", json=_outcome_payload(prediction_id=pred["prediction_id"], entity_id="event-ent"))
    client.post(
        f"{BASE}/feedback",
        json={
            "prediction_id": pred["prediction_id"],
            "entity_id": "event-ent",
            "engine_name": "lead_scoring",
            "feedback_type": "explicit",
            "label_value": 1.0,
            "actor": "lo:amy",
        },
    )
    events = get_event_log_repository().list(event_type="feedback.captured")
    types = {e["payload"]["type"] for e in events}
    assert {"prediction", "outcome", "explicit"} <= types


def test_rollback_emits_rolled_back_event(client: TestClient) -> None:
    _seed(client, 60, engine="rollback_engine")
    res1 = client.post(f"{BASE}/retrain/rollback_engine").json()
    assert res1["status"] == "promoted"

    res2 = client.post(f"{BASE}/retrain/rollback_engine").json()
    assert res2["status"] == "promoted"
    assert res2["model_id"] != res1["model_id"]

    rb = client.post(f"{BASE}/models/rollback/rollback_engine").json()
    assert rb["status"] == "rolled_back"
    assert rb["model_id"] == res1["model_id"]

    rollback_events = get_event_log_repository().list(event_type="model.rolled_back")
    assert len(rollback_events) == 1
    assert rollback_events[0]["payload"]["engine_name"] == "rollback_engine"

    models = client.get(f"{BASE}/models", params={"engine_name": "rollback_engine"}).json()
    prod = next(m for m in models if m["status"] == "production")
    assert prod["model_id"] == res1["model_id"]
