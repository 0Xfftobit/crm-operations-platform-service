"""Tests for retraining rejections, drift/accuracy triggers, and auto_retrain logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.service import reset_state

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


def _seed(client: TestClient, n: int, *, engine: str = "lead_scoring", features: dict | None = None) -> None:
    for i in range(n):
        label = 1.0 if i % 2 == 0 else 0.0
        prob = 0.8 if label else 0.2
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                engine=engine,
                entity_id=f"{engine}-{i}",
                prob=prob,
                features=features or {"f1": float(i % 2), "f2": prob},
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


def _custom_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_state()
    return TestClient(create_app(), headers={"X-API-Key": API_KEY})


def test_reject_insufficient_samples(client: TestClient) -> None:
    _seed(client, 10)
    res = client.post(f"{BASE}/retrain/lead_scoring").json()
    assert res["status"] == "rejected"
    assert ("too_few_rows" in res["reason"] or "insufficient" in res["reason"])


def test_reject_class_imbalance(client: TestClient) -> None:
    # 58 funded / 2 lost is >95% majority class, so the validation gate should fire.
    for i in range(60):
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(entity_id=f"imbal-{i}", features={"f1": 0.0, "f2": 1.0}),
        ).json()
        event = "deal.lost" if i < 2 else "deal.funded"
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"imbal-{i}",
                event_type=event,
            ),
        )
    res = client.post(f"{BASE}/retrain/lead_scoring").json()
    assert res["status"] == "rejected"
    assert "class_imbalance" in res["reason"]


def test_reject_too_many_missing_values(client: TestClient) -> None:
    for i in range(60):
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id=f"miss-{i}",
                features={"f1": "nan"},
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"miss-{i}",
                event_type="deal.funded" if i % 2 == 0 else "deal.lost",
            ),
        )
    res = client.post(f"{BASE}/retrain/lead_scoring").json()
    assert res["status"] == "rejected"
    assert "too_many_missing_values" in res["reason"]


def test_reject_no_usable_rows(client: TestClient) -> None:
    for i in range(60):
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(
                entity_id=f"nouse-{i}",
                features={"f1": "not-a-number"},
            ),
        ).json()
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"nouse-{i}",
                event_type="deal.funded" if i % 2 == 0 else "deal.lost",
            ),
        )
    res = client.post(f"{BASE}/retrain/lead_scoring").json()
    assert res["status"] == "rejected"
    assert "no usable training rows" in res["reason"]


def test_auto_retrain_rejected_for_high_risk_class(monkeypatch: pytest.MonkeyPatch) -> None:
    with _custom_client(monkeypatch, AUTO_RETRAIN_RISK_CLASS="high") as client:
        res = client.post(f"{BASE}/retrain/lead_scoring/auto").json()
        assert res["status"] == "rejected"
        assert "auto_retrain_not_allowed_for_risk_class" in res["reason"]


def test_auto_retrain_skips_when_no_trigger(client: TestClient) -> None:
    _seed(client, 60)
    res = client.post(f"{BASE}/retrain/lead_scoring?trigger=scheduled").json()
    assert res["status"] == "promoted"

    triggers = client.get(f"{BASE}/retrain/lead_scoring/triggers").json()
    assert triggers["scheduled_due"] is False
    assert triggers["performance_trigger_needed"] is False

    res = client.post(f"{BASE}/retrain/lead_scoring/auto").json()
    assert res["status"] == "skipped"
    assert res["reason"] == "no_trigger"


def test_performance_retrain_needed_trigger(client: TestClient) -> None:
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
    assert any("below_threshold" in reason for reason in triggers["performance"]["reasons"])


def test_drift_sustained_trigger_auto_retrains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two consecutive drift breaches with auto_retrain enabled should trigger a retrain."""
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with _custom_client(monkeypatch, AUTO_RETRAIN_ENABLED="true", AUTO_RETRAIN_RISK_CLASS="low") as client:
        # Seed a reference distribution from 31-day-old predictions.
        for i in range(60):
            pred = client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(
                    entity_id=f"drift-ref-{i}",
                    features={"f1": float(i % 2), "f2": 0.5},
                    scored_at=old,
                ),
            ).json()
            client.post(
                f"{BASE}/outcomes",
                json=_outcome_payload(
                    prediction_id=pred["prediction_id"],
                    entity_id=f"drift-ref-{i}",
                    event_type="deal.funded" if i % 2 == 0 else "deal.lost",
                ),
            )

        # Manual retrain establishes the reference distribution.
        res = client.post(f"{BASE}/retrain/lead_scoring?trigger=manual").json()
        assert res["status"] == "promoted"

        # New predictions are a shifted distribution.
        for i in range(60):
            client.post(
                f"{BASE}/predictions",
                json=_prediction_payload(
                    entity_id=f"drift-cur-{i}",
                    features={"f1": float(i % 2) + 50.0, "f2": 0.5},
                ),
            )

        # One breach is not enough (DRIFT_PERSISTENCE_EVALS=2).
        first = client.post(f"{BASE}/drift/evaluate/lead_scoring").json()
        assert first["retrain_triggered"] is False

        # Second consecutive breach should trigger auto-retrain.
        second = client.post(f"{BASE}/drift/evaluate/lead_scoring").json()
        assert second["retrain_triggered"] is True
        assert second["retrain_result"]["status"] == "promoted"
        assert second["retrain_result"]["model_id"]
