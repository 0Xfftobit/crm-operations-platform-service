"""Tests for STORAGE_BACKEND=disk persistence of datasets and model artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import joblib
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


def _seed_labeled_rows(client: TestClient, n: int = 60) -> None:
    for i in range(n):
        prob = 0.8 if i % 2 == 0 else 0.2
        features = {"f1": float(i % 2), "f2": prob}
        pred = client.post(
            f"{BASE}/predictions",
            json=_prediction_payload(entity_id=f"disk-{i}", prob=prob, features=features),
        ).json()
        event = "deal.funded" if prob >= 0.5 else "deal.lost"
        client.post(
            f"{BASE}/outcomes",
            json=_outcome_payload(
                prediction_id=pred["prediction_id"],
                entity_id=f"disk-{i}",
                event_type=event,
            ),
        )


def test_disk_backend_writes_dataset_and_model_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When STORAGE_BACKEND=disk, retraining should persist the dataset and model to disk."""
    data_dir = tmp_path / "data"
    model_dir = tmp_path / "models"
    monkeypatch.setenv("STORAGE_BACKEND", "disk")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("MODEL_SNAPSHOT_DIR", str(model_dir))
    get_settings.cache_clear()
    reset_state()

    with TestClient(create_app(), headers={"X-API-Key": API_KEY}) as client:
        _seed_labeled_rows(client)

        res = client.post(f"{BASE}/retrain/lead_scoring").json()
        assert res["status"] == "promoted"
        dataset_id = res["dataset_id"]
        model_id = res["model_id"]

        datasets = client.get(f"{BASE}/datasets", params={"engine_name": "lead_scoring"}).json()
        dataset = next(d for d in datasets if d["dataset_id"] == dataset_id)
        assert Path(dataset["snapshot_uri"]).exists()
        bundle = joblib.load(dataset["snapshot_uri"])
        assert "df" in bundle
        assert len(bundle["df"]) >= 50

        models = client.get(f"{BASE}/models", params={"engine_name": "lead_scoring"}).json()
        model = next(m for m in models if m["model_id"] == model_id)
        assert Path(model["artifact_path"]).exists()
        artifact = joblib.load(model["artifact_path"])
        assert "model" in artifact
        assert "feature_names" in artifact
        assert artifact["feature_names"] == ["f1", "f2"]
