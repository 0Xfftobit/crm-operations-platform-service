"""Unit + functional tests for the Feedback Loop System (P0–P3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import drift
from app.repositories import (
    get_event_log_repository,
    get_prediction_repository,
)

API_KEY = "test-key"
BASE = "/api/v1"


def _prediction_payload(
    engine: str = "lead_scoring",
    entity_id: str = "entity-1",
    prob: float = 0.8,
    features: dict | None = None,
    scored_at: str | None = None,
) -> dict:
    return {
        "engine_name": engine,
        "model_version": "1.0.0",
        "entity_id": entity_id,
        "features_ref": "fs://snap/2026-06-15T18:00Z/entity-1",
        "features": features or {"f1": 1.0, "f2": 0.5},
        "output": {"probability": prob},
        "scored_at": scored_at or datetime.now(UTC).isoformat(),
    }


def _outcome_payload(
    prediction_id: str | None = None,
    entity_id: str = "entity-1",
    event_type: str = "deal.funded",
    occurred_at: str | None = None,
) -> dict:
    return {
        "prediction_id": prediction_id,
        "entity_id": entity_id,
        "engine_name": "lead_scoring",
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
    }


class TestCapture:
    def test_log_prediction(self, client: TestClient) -> None:
        res = client.post(f"{BASE}/predictions", json=_prediction_payload())
        assert res.status_code == 200
        assert res.json()["status"] == "logged"

    def test_duplicate_prediction(self, client: TestClient) -> None:
        payload = _prediction_payload()
        payload["prediction_id"] = "pred-duplicate-1"
        client.post(f"{BASE}/predictions", json=payload)
        res = client.post(f"{BASE}/predictions", json=payload)
        assert res.json()["status"] == "duplicate"

    def test_record_outcome_creates_label(self, client: TestClient) -> None:
        pred = client.post(f"{BASE}/predictions", json=_prediction_payload()).json()
        outcome = _outcome_payload(prediction_id=pred["prediction_id"])
        res = client.post(f"{BASE}/outcomes", json=outcome)
        assert res.status_code == 200
        assert res.json()["status"] == "recorded"
        assert len(res.json()["labels"]) == 1

    def test_record_explicit_feedback(self, client: TestClient) -> None:
        pred = client.post(f"{BASE}/predictions", json=_prediction_payload()).json()
        fb = {
            "prediction_id": pred["prediction_id"],
            "entity_id": "entity-1",
            "engine_name": "lead_scoring",
            "feedback_type": "explicit",
            "label_value": 1.0,
            "actor": "lo:mike",
        }
        res = client.post(f"{BASE}/feedback", json=fb)
        assert res.status_code == 200
        labels = client.get(f"{BASE}/labels", params={"prediction_id": pred["prediction_id"]}).json()
        assert len(labels) == 1

    def test_record_override(self, client: TestClient) -> None:
        pred = client.post(f"{BASE}/predictions", json=_prediction_payload()).json()
        fb = {
            "prediction_id": pred["prediction_id"],
            "entity_id": "entity-1",
            "engine_name": "lead_scoring",
            "feedback_type": "override",
            "original_output": {"action": "call"},
            "chosen_action": "email",
            "reason": "borrower requested",
            "actor": "lo:amy",
        }
        res = client.post(f"{BASE}/feedback", json=fb)
        assert res.status_code == 200
        labels = client.get(f"{BASE}/labels").json()
        assert any(label["label_source"] == "human_override" for label in labels)

    def test_outcome_without_prediction_is_quarantined(self, client: TestClient) -> None:
        res = client.post(f"{BASE}/outcomes", json=_outcome_payload(prediction_id=None))
        assert res.status_code == 200
        assert res.json()["labels"] == []
        quarantine = [e for e in get_event_log_repository().list() if e["event"] == "label.quarantined"]
        assert len(quarantine) == 1

    def test_requires_auth(self, client: TestClient) -> None:
        anon = TestClient(client.app)
        assert anon.post(f"{BASE}/predictions", json=_prediction_payload()).status_code == 401


class TestLabels:
    def test_windowed_matching(self, client: TestClient) -> None:
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        pred_payload = _prediction_payload(
            entity_id="deal-204",
            scored_at=now.isoformat(),
        )
        client.post(f"{BASE}/predictions", json=pred_payload)

        # Outcome 10 days later should match and be in window.
        later = (now + timedelta(days=10)).isoformat()
        outcome = _outcome_payload(
            prediction_id=None,
            entity_id="deal-204",
            event_type="deal.funded",
            occurred_at=later,
        )
        client.post(f"{BASE}/outcomes", json=outcome)
        labels = client.get(f"{BASE}/labels").json()
        assert len(labels) == 1
        assert labels[0]["label_value"] == 1.0
        assert labels[0]["in_window"] is True

    def test_outcome_out_of_window(self, client: TestClient) -> None:
        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        pred_payload = _prediction_payload(
            entity_id="deal-205",
            scored_at=now.isoformat(),
        )
        client.post(f"{BASE}/predictions", json=pred_payload)

        # 100 days later is past the 90-day conversion window.
        later = (now + timedelta(days=100)).isoformat()
        outcome = _outcome_payload(
            prediction_id=None,
            entity_id="deal-205",
            event_type="deal.funded",
            occurred_at=later,
        )
        client.post(f"{BASE}/outcomes", json=outcome)
        labels = client.get(f"{BASE}/labels").json()
        assert len(labels) == 1
        assert labels[0]["in_window"] is False

    def test_build_all_labels(self, client: TestClient) -> None:
        client.post(f"{BASE}/predictions", json=_prediction_payload(entity_id="e1"))
        client.post(f"{BASE}/outcomes", json=_outcome_payload(prediction_id=None, entity_id="e1"))
        assert len(client.get(f"{BASE}/labels").json()) == 1
        client.post(f"{BASE}/labels/build")
        labels = client.get(f"{BASE}/labels").json()
        assert len(labels) == 1


class TestMetrics:
    def test_metrics(self, client: TestClient) -> None:
        # Seed a few predictions with varying probabilities and outcomes.
        for i, prob in enumerate([0.9, 0.1, 0.8, 0.2, 0.85, 0.15]):
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"e-{i}", prob=prob),
            ).json()
            event = "deal.funded" if prob >= 0.5 else "deal.lost"
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(prediction_id=pred["prediction_id"], entity_id=f"e-{i}", event_type=event),
            )

        metrics = client.get(f"{BASE}/metrics/lead_scoring").json()
        assert metrics["support"] == 6
        assert metrics["accuracy"] == 1.0
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0


class TestDrift:
    def test_psi(self) -> None:
        ref = [1.0] * 80 + [2.0] * 20
        cur = [1.0] * 50 + [2.0] * 50
        assert drift.psi(ref, cur) > 0.1

    def test_drift_evaluate_triggers(self, client: TestClient) -> None:
        # Seed reference distribution.
        for i in range(100):
            client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"ref-{i}", features={"f1": float(i % 10)}),
            ).json()
        drift.set_reference_from_predictions("lead_scoring", get_prediction_repository().list("lead_scoring"))

        # New distribution is shifted.
        for i in range(100):
            client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(
                    entity_id=f"cur-{i}",
                    features={"f1": float(i % 10) + 50.0},
                ),
            )

        # Need two consecutive breaches to trigger.
        client.post(f"{BASE}/drift/evaluate/lead_scoring")
        client.post(f"{BASE}/drift/evaluate/lead_scoring")

        records = client.get(f"{BASE}/drift", params={"engine_name": "lead_scoring"}).json()
        assert any(r["psi"] > 0.0 for r in records)


class TestRetraining:
    def test_assemble_and_train(self, client: TestClient) -> None:
        # Build 60 labeled rows with informative features.
        for i in range(60):
            prob = 0.8 if i % 2 == 0 else 0.2
            features = {"f1": float(i % 2), "f2": prob}
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"train-{i}", prob=prob, features=features),
            ).json()
            event = "deal.funded" if prob >= 0.5 else "deal.lost"
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(prediction_id=pred["prediction_id"], entity_id=f"train-{i}", event_type=event),
            )

        res = client.post(f"{BASE}/retrain/lead_scoring").json()
        assert res["status"] == "promoted"

        models = client.get(f"{BASE}/models", params={"engine_name": "lead_scoring"}).json()
        assert len(models) == 1
        assert models[0]["status"] == "production"

    def test_lineage_report(self, client: TestClient) -> None:
        # Same training data as above.
        for i in range(60):
            prob = 0.8 if i % 2 == 0 else 0.2
            features = {"f1": float(i % 2), "f2": prob}
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"train-{i}", prob=prob, features=features),
            ).json()
            event = "deal.funded" if prob >= 0.5 else "deal.lost"
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(prediction_id=pred["prediction_id"], entity_id=f"train-{i}", event_type=event),
            )

        res = client.post(f"{BASE}/retrain/lead_scoring").json()
        model_id = res["model_id"]
        lineage = client.get(f"{BASE}/models/{model_id}/lineage").json()
        assert lineage["model_id"] == model_id
        assert lineage["training_dataset"]
        assert "eval" in lineage

    def test_fairness_gate_blocks_unfair_model(self, client: TestClient) -> None:
        # Build a dataset where protected group strongly correlates with the label.
        for i in range(60):
            group = "A" if i % 2 == 0 else "B"
            label = 1.0 if group == "A" else 0.0
            features = {"f1": label, "f2": 1 - label, "protected_group": group}
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"fair-{i}", prob=0.5, features=features),
            ).json()
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(
                    prediction_id=pred["prediction_id"],
                    entity_id=f"fair-{i}",
                    event_type="deal.funded" if label else "deal.lost",
                ),
            )

        res = client.post(f"{BASE}/retrain/lead_scoring").json()
        assert res["status"] in ("rejected", "rollback")


class TestABTest:
    def test_experiment_lifecycle(self, client: TestClient) -> None:
        exp = client.post(
            f"{BASE}/experiments",
            json={
                "name": "subject-line-test",
                "hypothesis": "short subject wins",
                "engine_name": "lead_scoring",
                "variants": [
                    {"name": "control", "traffic_pct": 50},
                    {"name": "challenger", "traffic_pct": 50},
                ],
            },
        ).json()
        exp_id = exp["experiment_id"]

        # Same lead always gets the same variant.
        v1 = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": "lead-1"}).json()
        v2 = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": "lead-1"}).json()
        assert v1["variant"] == v2["variant"]

        # Record results and evaluate.
        for i in range(20):
            variant = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()[
                "variant"
            ]
            client.post(
                f"{BASE}/experiments/{exp_id}/record",
                json={"variant": variant, "entity_id": f"lead-{i}", "metric_value": 1.0 if i % 2 == 0 else 0.0},
            )

        report = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
        assert report["status"] == "running"
        assert "variants" in report

    def test_experiment_power_analysis_and_significance(self, client: TestClient) -> None:
        exp = client.post(
            f"{BASE}/experiments",
            json={
                "name": "big-win-test",
                "hypothesis": "challenger is much better",
                "engine_name": "lead_scoring",
                "experiment_type": "message",
                "p_value_threshold": 0.05,
                "min_duration_hours": 0.0,
                "variants": [
                    {"name": "control", "traffic_pct": 50},
                    {"name": "challenger", "traffic_pct": 50},
                ],
            },
        ).json()
        exp_id = exp["experiment_id"]

        # Seed enough samples for a clear 50% vs 10% conversion difference.
        for i in range(200):
            resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
            variant = resp["variant"]
            if variant == "challenger":
                value = 1.0 if i % 2 == 0 else 0.0
            else:
                value = 1.0 if i % 10 == 0 else 0.0
            client.post(
                f"{BASE}/experiments/{exp_id}/record",
                json={"variant": variant, "entity_id": f"lead-{i}", "metric_value": value},
            )

        report = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
        assert report["min_sample_size_per_variant"] > 0
        assert report["min_duration_met"] is True
        assert report["variants"]["challenger"]["significant"] is True

    def test_experiment_guardrail_blocks_promotion(self, client: TestClient) -> None:
        exp = client.post(
            f"{BASE}/experiments",
            json={
                "name": "guardrail-test",
                "hypothesis": "challenger converts but harms guardrail",
                "engine_name": "lead_scoring",
                "experiment_type": "model",
                "guardrail_metric": "retention",
                "guardrail_threshold": 0.5,
                "min_duration_hours": 0.0,
                "variants": [
                    {"name": "control", "traffic_pct": 50},
                    {"name": "challenger", "traffic_pct": 50},
                ],
            },
        ).json()
        exp_id = exp["experiment_id"]

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

        # Promote without force should be blocked by guardrail.
        blocked = client.post(f"{BASE}/experiments/{exp_id}/promote", json={}).json()
        assert blocked["status"] == "guardrail_breached"

        # Force promotion should succeed.
        forced = client.post(f"{BASE}/experiments/{exp_id}/promote", json={"force": True}).json()
        assert forced["status"] == "promoted"

    def test_traffic_split_rounds_to_exactly_100(self, client: TestClient) -> None:
        # traffic_pct values are ignored when traffic_split is computed automatically;
        # the bug-audit item is that auto-computed splits must sum to exactly 100.
        variants = [{"name": f"v{i}", "traffic_pct": 100 / 7} for i in range(7)]
        exp = client.post(
            f"{BASE}/experiments",
            json={
                "name": "split-sum-test",
                "hypothesis": "splits must sum to 100",
                "engine_name": "lead_scoring",
                "variants": variants,
            },
        ).json()
        total = round(sum(exp["traffic_split"].values()), 2)
        assert total == 100.0

    def test_guardrail_fallback_not_used_when_metric_configured(self, client: TestClient) -> None:
        """Guardrail metric configured and no guardrail values recorded.

        The fallback (compare primary conversion rate to guardrail_threshold) must not run,
        otherwise a threshold of 1.5 above the 100% conversion rate would falsely breach.
        """
        exp = client.post(
            f"{BASE}/experiments",
            json={
                "name": "guardrail-fallback-test",
                "hypothesis": "fallback does not fire when guardrail metric is set",
                "engine_name": "lead_scoring",
                "experiment_type": "model",
                "guardrail_metric": "retention",
                "guardrail_threshold": 1.5,
                "min_duration_hours": 0.0,
                "variants": [
                    {"name": "control", "traffic_pct": 50},
                    {"name": "challenger", "traffic_pct": 50},
                ],
            },
        ).json()
        exp_id = exp["experiment_id"]
        for i in range(20):
            resp = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}).json()
            variant = resp["variant"]
            client.post(
                f"{BASE}/experiments/{exp_id}/record",
                json={"variant": variant, "entity_id": f"lead-{i}", "metric_value": 1.0},
            )
        report = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
        assert report["guardrail_breached"] is False

    def test_drift_window_excludes_30d1s_predictions(self, client: TestClient) -> None:
        from app import drift as drift_mod

        old = datetime.now(UTC) - timedelta(days=30, seconds=1)
        client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id="drift-old",
                scored_at=old.isoformat(),
                features={"f1": 1.0},
            ),
            headers={"X-API-Key": API_KEY},
        )
        client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id="drift-recent",
                scored_at=(datetime.now(UTC) - timedelta(days=29)).isoformat(),
                features={"f1": 1.0},
            ),
            headers={"X-API-Key": API_KEY},
        )
        assert len(drift_mod._recent_values("lead_scoring", "f1", max_age_days=30)) == 1


class TestSubtasks:
    def test_performance_trigger_auto_retrains(self, client: TestClient) -> None:
        # Seed 60 rows where the logged probabilities are the opposite of the true label.
        # This drives engine accuracy to ~0, which should fire the performance retrain trigger.
        for i in range(60):
            label = 1.0 if i % 2 == 0 else 0.0
            prob = 0.1 if label else 0.9
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"perf-{i}", prob=prob, features={"f1": label, "f2": label}),
            ).json()
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(
                    prediction_id=pred["prediction_id"],
                    entity_id=f"perf-{i}",
                    event_type="deal.funded" if label else "deal.lost",
                ),
            )

        triggers = client.get(f"{BASE}/retrain/lead_scoring/triggers").json()
        assert triggers["performance_trigger_needed"] is True
        assert triggers["performance"]["reasons"]

        # Auto-retrain should fire and, because the features perfectly encode the label,
        # the new model should pass the gates and be promoted.
        res = client.post(f"{BASE}/retrain/lead_scoring/auto").json()
        assert res["status"] == "promoted"

        # Run an explicit scheduled retrain and verify it is no longer due.
        client.post(f"{BASE}/retrain/lead_scoring?trigger=scheduled")
        triggers = client.get(f"{BASE}/retrain/lead_scoring/triggers").json()
        assert triggers["scheduled_due"] is False

    def test_data_validation_rejects_single_class(self, client: TestClient) -> None:
        for i in range(60):
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"single-{i}", prob=0.9, features={"f1": 0.0, "f2": 0.0}),
            ).json()
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(
                    prediction_id=pred["prediction_id"],
                    entity_id=f"single-{i}",
                    event_type="deal.lost",
                ),
            )

        res = client.post(f"{BASE}/retrain/lead_scoring").json()
        assert res["status"] == "rejected"
        assert "single_class" in res["reason"]

    def test_retraining_emits_lifecycle_events(self, client: TestClient) -> None:
        for i in range(60):
            label = 1.0 if i % 2 == 0 else 0.0
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(entity_id=f"event-{i}", prob=0.5, features={"f1": label, "f2": label}),
            ).json()
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(
                    prediction_id=pred["prediction_id"],
                    entity_id=f"event-{i}",
                    event_type="deal.funded" if label else "deal.lost",
                ),
            )

        client.post(f"{BASE}/retrain/lead_scoring")
        events = get_event_log_repository().list()
        event_types = {e["event"] for e in events}
        assert "retrain.started" in event_types
        assert "retrain.completed" in event_types
        assert "model.promoted" in event_types
