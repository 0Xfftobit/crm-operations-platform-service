"""Acceptance / requirements tests for the Feedback Loop System (FLS).

This suite exercises the ticket requirements, business rules, NFRs and
acceptance criteria found in:

- docs/ticket_description_fls.md
- docs/implementation_status_fls.md

The tests are self-contained, use the in-memory backend, and do not require
a live Kafka broker or MLflow registry.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import label_builder
from app.core.config import Settings, get_settings
from app.domain.models import Outcome
from app.messaging import kafka_bus
from app.repositories import (
    get_event_log_repository,
    get_model_repository,
    get_outcome_repository,
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
    engine: str = "lead_scoring",
) -> dict:
    return {
        "prediction_id": prediction_id,
        "entity_id": entity_id,
        "engine_name": engine,
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
    }


def _override_payload(prediction_id: str) -> dict:
    return {
        "prediction_id": prediction_id,
        "entity_id": "entity-1",
        "engine_name": "lead_scoring",
        "feedback_type": "override",
        "original_output": {"action": "call"},
        "chosen_action": "email",
        "reason": "borrower requested",
        "actor": "lo:amy",
    }


def _explicit_payload(prediction_id: str, value: float = 1.0) -> dict:
    return {
        "prediction_id": prediction_id,
        "entity_id": "entity-1",
        "engine_name": "lead_scoring",
        "feedback_type": "explicit",
        "label_value": value,
        "actor": "lo:mike",
    }


def _seed(client: TestClient, n: int, *, engine: str = "lead_scoring") -> None:
    """Create ``n`` deterministic, perfectly-labeled predictions for ``engine``."""
    for i in range(n):
        label = 1.0 if i % 2 == 0 else 0.0
        prob = 0.8 if label else 0.2
        features = {"f1": float(i % 2), "f2": prob}
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                engine=engine,
                entity_id=f"{engine}-{i}",
                prob=prob,
                features=features,
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"{engine}-{i}",
                event_type="deal.funded" if label else "deal.lost",
                engine=engine,
            ),
        )


# ── P0: Prediction, outcome, override and explicit-feedback capture ──────────


def test_prediction_outcome_and_explicit_feedback_capture(client: TestClient) -> None:
    """FR-001, FR-002/003, FR-005: predictions, outcomes and explicit feedback
    are captured, joined and returned as labels."""
    pred = client.post(f"{BASE}/predictions", json=_prediction_payload()).json()
    assert pred["status"] == "logged"
    prediction_id = pred["prediction_id"]

    res = client.post(
        f"{BASE}/outcomes",
        json=_outcome_payload(prediction_id=prediction_id),
    ).json()
    assert res["status"] == "recorded"
    assert res["labels"]

    labels = client.get(f"{BASE}/labels", params={"prediction_id": prediction_id}).json()
    assert len(labels) == 1
    assert labels[0]["label_value"] == 1.0

    client.post(f"{BASE}/feedback", json=_explicit_payload(prediction_id, value=1.0))
    labels = client.get(f"{BASE}/labels", params={"prediction_id": prediction_id}).json()
    assert any(label["label_source"] == "explicit" for label in labels)


def test_override_capture(client: TestClient) -> None:
    """FR-004: a human override is captured as a labeled feedback record."""
    pred = client.post(f"{BASE}/predictions", json=_prediction_payload()).json()
    prediction_id = pred["prediction_id"]

    res = client.post(f"{BASE}/feedback", json=_override_payload(prediction_id)).json()
    assert res["status"] == "recorded"

    labels = client.get(f"{BASE}/labels", params={"prediction_id": prediction_id}).json()
    assert any(label["label_source"] == "human_override" for label in labels)
    assert any(label["label_value"] == "email" for label in labels)


def test_orphan_outcomes_and_feedback_are_quarantined(client: TestClient) -> None:
    """BR-1: unmatched outcomes and feedback are quarantined, not guessed."""
    client.post(f"{BASE}/outcomes", json=_outcome_payload(prediction_id=None))
    client.post(
        f"{BASE}/feedback",
        json={
            "prediction_id": None,
            "entity_id": "orphan",
            "engine_name": "lead_scoring",
            "feedback_type": "explicit",
            "label_value": 1.0,
            "actor": "lo:amy",
        },
    )

    quarantine = get_event_log_repository().list(event_type="label.quarantined")
    assert len(quarantine) == 2
    reasons = {q["payload"]["reason"] for q in quarantine}
    assert {"orphan_outcome", "orphan_feedback"} <= reasons


# ── P1: Point-in-time label building ─────────────────────────────────────────


def test_point_in_time_label_builder(client: TestClient) -> None:
    """FR-006: outcomes are matched to the prediction that existed at the time
    of the outcome, not to a newer prediction."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    early = client.post(
        f"{BASE}/predictions",
        json=_prediction_payload(entity_id="pit-1", scored_at=now.isoformat()),
    ).json()["prediction_id"]

    # A later prediction should not steal the outcome.
    future = (now + timedelta(days=10)).isoformat()
    client.post(
        f"{BASE}/predictions",
        json=_prediction_payload(entity_id="pit-1", prob=0.2, scored_at=future),
    )

    outcome = Outcome(
        entity_id="pit-1",
        engine_name="lead_scoring",
        event_type="deal.funded",
        occurred_at=now + timedelta(days=2),
    )
    matched = label_builder.match_prediction_for_outcome(outcome)
    assert matched is not None
    assert matched.prediction_id == early


def test_windowed_labels_immediate_and_delayed(client: TestClient) -> None:
    """FR-002/003: an immediate outcome inside the window labels ``in_window``;
    a 100-day-later funded outcome is retained but out-of-window."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    client.post(
        f"{BASE}/predictions",
        json=_prediction_payload(entity_id="in-win", scored_at=now.isoformat()),
    )
    client.post(
        f"{BASE}/outcomes",
        json=_outcome_payload(
            prediction_id=None,
            entity_id="in-win",
            event_type="deal.funded",
            occurred_at=(now + timedelta(days=10)).isoformat(),
        ),
    )

    client.post(
        f"{BASE}/predictions",
        json=_prediction_payload(entity_id="out-win", scored_at=now.isoformat()),
    )
    client.post(
        f"{BASE}/outcomes",
        json=_outcome_payload(
            prediction_id=None,
            entity_id="out-win",
            event_type="deal.funded",
            occurred_at=(now + timedelta(days=100)).isoformat(),
        ),
    )

    labels = client.get(f"{BASE}/labels").json()
    in_win = next(lab for lab in labels if lab["entity_id"] == "in-win")
    out_win = next(lab for lab in labels if lab["entity_id"] == "out-win")
    assert in_win["in_window"] is True
    assert out_win["in_window"] is False


# ── P2: Per-engine metrics and drift ─────────────────────────────────────────


def test_per_engine_metrics(client: TestClient) -> None:
    """FR-009: per-engine precision, recall, F1, AUC, ECE and match rate."""
    for i, prob in enumerate([0.9, 0.1, 0.8, 0.2, 0.85, 0.15]):
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(entity_id=f"metrics-{i}", prob=prob),
        ).json()
        event = "deal.funded" if prob >= 0.5 else "deal.lost"
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"metrics-{i}",
                event_type=event,
            ),
        )

    metrics = client.get(f"{BASE}/metrics/lead_scoring").json()
    assert metrics["support"] == 6
    assert metrics["accuracy"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["auc"] is not None
    assert metrics["match_rate"] == 1.0


def test_psi_drift_and_sustained_retrain_trigger(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-008/010: a sustained PSI breach triggers an auto-retrain candidate."""
    monkeypatch.setenv("AUTO_RETRAIN_ENABLED", "true")
    get_settings.cache_clear()

    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    for i in range(60):
        client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id=f"drift-ref-{i}",
                features={"f1": float(i % 10), "f2": 0.5},
                scored_at=old,
            ),
        )
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=None,
                entity_id=f"drift-ref-{i}",
                event_type="deal.funded" if i % 2 == 0 else "deal.lost",
            ),
        )

    # Manual retrain establishes the reference distribution.
    res = client.post(f"{BASE}/retrain/lead_scoring?trigger=manual").json()
    assert res["status"] == "promoted"

    # New predictions shift the f1 distribution.
    for i in range(60):
        client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id=f"drift-cur-{i}",
                features={"f1": float(i % 10) + 50.0, "f2": 0.5},
            ),
        )

    first = client.post(f"{BASE}/drift/evaluate/lead_scoring").json()
    assert first["retrain_triggered"] is False

    second = client.post(f"{BASE}/drift/evaluate/lead_scoring").json()
    assert second["retrain_triggered"] is True
    assert any(record["alert"] for record in second["records"])
    assert second["retrain_result"]["status"] == "promoted"


# ── P3: Reproducible retraining, gates and promotion ─────────────────────────


def test_reproducible_retraining_and_fairness_gate(client: TestClient) -> None:
    """FR-011/013/014: a seeded retrain is reproducible; the fairness gate
    blocks an unfair challenger."""
    _seed(client, 60, engine="repro_engine")

    r1 = client.post(f"{BASE}/retrain/repro_engine").json()
    assert r1["status"] == "promoted"

    r2 = client.post(f"{BASE}/retrain/repro_engine").json()
    assert r2["status"] == "promoted"

    # Same data + seed should produce identical eval metrics.
    assert r1["metrics"] == r2["metrics"]

    lineage = client.get(f"{BASE}/models/{r1['model_id']}/lineage").json()
    assert lineage["training_dataset"]
    assert lineage["eval"]
    assert lineage["feature_versions"]

    # Build an unfair dataset where protected group correlates perfectly with label.
    for i in range(60):
        group = "A" if i % 2 == 0 else "B"
        label = 1.0 if group == "A" else 0.0
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                engine="unfair_engine",
                entity_id=f"unfair-{i}",
                prob=0.5,
                features={
                    "f1": float(label),
                    "f2": 1.0 - float(label),
                    "protected_group": group,
                },
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"unfair-{i}",
                event_type="deal.funded" if label else "deal.lost",
                engine="unfair_engine",
            ),
        )

    res = client.post(f"{BASE}/retrain/unfair_engine").json()
    assert res["status"] in ("rejected", "rollback")
    assert res["metrics"]["fairness_passed"] is False


def test_auto_retrain_triggers_on_performance_decay(client: TestClient) -> None:
    """FR-010: auto-retrain runs when accuracy/AUC drops below threshold."""
    for i in range(60):
        label = 1.0 if i % 2 == 0 else 0.0
        prob = 0.1 if label else 0.9
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id=f"auto-{i}",
                prob=prob,
                features={"f1": float(label), "f2": float(label)},
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"auto-{i}",
                event_type="deal.funded" if label else "deal.lost",
            ),
        )

    triggers = client.get(f"{BASE}/retrain/lead_scoring/triggers").json()
    assert triggers["performance_trigger_needed"] is True

    res = client.post(f"{BASE}/retrain/lead_scoring/auto").json()
    assert res["status"] == "promoted"
    assert res["model_id"]
    assert res["metrics"]["accuracy"] >= get_settings().accuracy_threshold


def test_champion_challenger_promotion_and_rollback(client: TestClient) -> None:
    """Champion/challenger promotion and rollback via the public model registry."""
    _seed(client, 60, engine="champ_engine")

    r1 = client.post(f"{BASE}/retrain/champ_engine").json()
    assert r1["status"] == "promoted"

    r2 = client.post(f"{BASE}/retrain/champ_engine").json()
    assert r2["status"] == "promoted"
    assert r2["model_id"] != r1["model_id"]

    # Promote the first model back to production.
    promoted = client.post(f"{BASE}/models/{r1['model_id']}/promote").json()
    assert promoted["status"] == "promoted"

    models = client.get(f"{BASE}/models", params={"engine_name": "champ_engine"}).json()
    prod = next(m for m in models if m["status"] == "production")
    assert prod["model_id"] == r1["model_id"]

    # Rollback should restore the most recent previous production model.
    rb = client.post(f"{BASE}/models/rollback/champ_engine").json()
    assert rb["status"] == "rolled_back"

    models = client.get(f"{BASE}/models", params={"engine_name": "champ_engine"}).json()
    prod = next(m for m in models if m["status"] == "production")
    assert prod["model_id"] == r2["model_id"]


# ── CEAAPSS-560: A/B test framework ──────────────────────────────────────────


def test_ab_test_lifecycle_significance_and_promotion(client: TestClient) -> None:
    """A/B test consistent hashing, power analysis, significance and promotion."""
    exp = client.post(
        f"{BASE}/experiments",
        json={
            "name": "requirements-ab",
            "hypothesis": "challenger converts better",
            "engine_name": "lead_scoring",
            "experiment_type": "model",
            "min_duration_hours": 0.0,
            "p_value_threshold": 0.05,
            "variants": [
                {"name": "control", "traffic_pct": 50},
                {"name": "challenger", "traffic_pct": 50},
            ],
        },
    ).json()
    exp_id = exp["experiment_id"]

    # Consistent hashing: same entity, same variant.
    v1 = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": "lead-1"}).json()
    v2 = client.post(f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": "lead-1"}).json()
    assert v1["variant"] == v2["variant"]

    # Seed a strong challenger effect.
    for i in range(200):
        variant = client.post(
            f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}
        ).json()["variant"]
        if variant == "challenger":
            value = 1.0 if i % 2 == 0 else 0.0
        else:
            value = 1.0 if i % 10 == 0 else 0.0
        client.post(
            f"{BASE}/experiments/{exp_id}/record",
            json={
                "variant": variant,
                "entity_id": f"lead-{i}",
                "metric_value": value,
            },
        )

    report = client.post(f"{BASE}/experiments/{exp_id}/evaluate").json()
    assert report["min_sample_size_per_variant"] > 0
    assert report["min_duration_met"] is True
    assert report["variants"]["challenger"]["significant"] is True

    promoted = client.post(f"{BASE}/experiments/{exp_id}/promote", json={}).json()
    assert promoted["status"] == "promoted"
    assert promoted["winner"] == "challenger"


def test_ab_test_guardrail_blocks_promotion(client: TestClient) -> None:
    """Guardrail metric breach blocks promotion unless force is used."""
    exp = client.post(
        f"{BASE}/experiments",
        json={
            "name": "guardrail-ab",
            "hypothesis": "challenger harms retention",
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
        variant = client.post(
            f"{BASE}/experiments/{exp_id}/assign", json={"entity_id": f"lead-{i}"}
        ).json()["variant"]
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

    blocked = client.post(f"{BASE}/experiments/{exp_id}/promote", json={}).json()
    assert blocked["status"] == "guardrail_breached"

    forced = client.post(f"{BASE}/experiments/{exp_id}/promote", json={"force": True}).json()
    assert forced["status"] == "promoted"


# ── Kafka consumer baseline and in-memory fallback ───────────────────────────


async def _wait_until(predicate: callable, timeout: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"predicate did not become true within {timeout}s")
        await asyncio.sleep(0.01)


def test_kafka_consumer_in_memory_fallback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The consumer falls back to an in-process log when no broker is available
    and still dispatches crm.outcome.* / crm.outreach.converted events."""
    # Force the consumer to start, but make aiokafka unavailable.
    fake = types.ModuleType("aiokafka")
    monkeypatch.setitem(sys.modules, "aiokafka", fake)
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()
    kafka_bus.reset_kafka_bus()

    async def _run() -> None:
        info = await kafka_bus.start_consumer()
        assert info["status"] == "started"

        await kafka_bus.produce(
            "outcome.funded",
            {"entity_id": "kafka-deal", "engine_name": "lead_scoring"},
        )
        await _wait_until(lambda: kafka_bus.bus_stats()["consumer_lag"] == 0)

        await kafka_bus.produce(
            "outreach.converted",
            {"entity_id": "kafka-lead"},
        )
        await _wait_until(lambda: kafka_bus.bus_stats()["consumer_lag"] == 0)

        await kafka_bus.stop_consumer()

    asyncio.run(_run())

    outcomes = get_outcome_repository().list()
    assert any(o.entity_id == "kafka-deal" and o.event_type == "funded" for o in outcomes)
    assert any(o.entity_id == "kafka-lead" and o.event_type == "converted" for o in outcomes)


# ── Security / NFR: default keys and CORS ────────────────────────────────────


def test_default_dev_key_rejected_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default dev API key is rejected in non-local environments."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()


def test_wildcard_cors_rejected_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wildcard CORS origin is rejected in non-local environments."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "prod-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()


# ── NFR: X-Request-ID propagation ────────────────────────────────────────────


def test_x_request_id_propagation(client: TestClient) -> None:
    """The API echoes a caller-supplied X-Request-ID and generates one when absent."""
    req_id = "fls-req-123"
    res = client.get(f"{BASE}/stats", headers={"X-Request-ID": req_id})
    assert res.headers.get("x-request-id") == req_id or res.headers.get("X-Request-ID") == req_id

    res = client.get(f"{BASE}/stats")
    assert res.headers.get("x-request-id") or res.headers.get("X-Request-ID")


# ── NFR: default storage backend and joblib serialization ────────────────────


def test_default_storage_backend_is_memory(client: TestClient) -> None:
    """Default STORAGE_BACKEND is memory for self-contained operation."""
    assert get_settings().storage_backend == "memory"


def test_memory_backend_model_artifact_available(client: TestClient) -> None:
    """STORAGE_BACKEND=memory keeps the model artifact in the repository."""
    _seed(client, 60, engine="mem_engine")
    res = client.post(f"{BASE}/retrain/mem_engine").json()
    assert res["status"] == "promoted"

    artifact = get_model_repository().get_artifact(res["model_id"])
    assert artifact is not None
    assert "model" in artifact
    assert "feature_names" in artifact


# ── Acceptance: export and lineage completeness ──────────────────────────────


def test_labeled_dataset_export(client: TestClient) -> None:
    """Feedback data is cleanly formatted and exportable for training/audit."""
    pred = client.post(
        f"{BASE}/predictions",
        json=_prediction_payload(entity_id="export-1"),
    ).json()
    client.post(
        f"{BASE}/outcomes",
        json=_outcome_payload(
            prediction_id=pred["prediction_id"],
            entity_id="export-1",
        ),
    )

    export = client.get(f"{BASE}/export/lead_scoring").json()
    assert export["count"] >= 1
    row = export["rows"][0]
    assert row["prediction_id"]
    assert "features" in row
    assert "label_value" in row
    assert "in_window" in row


def test_model_lineage_report(client: TestClient) -> None:
    """A promoted model can produce a complete lineage report."""
    _seed(client, 60, engine="lineage_engine")
    res = client.post(f"{BASE}/retrain/lineage_engine").json()
    model_id = res["model_id"]

    lineage = client.get(f"{BASE}/models/{model_id}/lineage").json()
    assert lineage["model_id"] == model_id
    assert lineage["training_dataset"]
    assert lineage["feature_versions"]
    assert lineage["eval"]
    assert lineage["gate"]
