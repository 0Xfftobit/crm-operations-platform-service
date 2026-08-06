"""In-memory repositories with a narrow interface.

A real deployment swaps these for a data-warehouse/Postgres/S3-backed gateway.
The in-memory default keeps the engine self-contained and testable.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    DriftRecord,
    Experiment,
    Feedback,
    Label,
    ModelVersion,
    Outcome,
    Prediction,
    RetrainingRun,
    TrainingDataset,
)


class PredictionRepository:
    def __init__(self) -> None:
        self._items: dict[str, Prediction] = {}

    def put(self, p: Prediction) -> None:
        self._items[p.prediction_id] = p

    def get(self, prediction_id: str) -> Prediction | None:
        return self._items.get(prediction_id)

    def list(self, engine_name: str | None = None, entity_id: str | None = None) -> list[Prediction]:
        return [
            p
            for p in self._items.values()
            if (engine_name is None or p.engine_name == engine_name) and (entity_id is None or p.entity_id == entity_id)
        ]

    def clear(self) -> None:
        self._items.clear()


class OutcomeRepository:
    def __init__(self) -> None:
        self._items: list[Outcome] = []

    def add(self, o: Outcome) -> None:
        self._items.append(o)

    def list(
        self,
        engine_name: str | None = None,
        entity_id: str | None = None,
        unlabeled: bool = False,
        labels: LabelRepository | None = None,
    ) -> list[Outcome]:
        labeled_ids = set()
        if unlabeled and labels is not None:
            labeled_ids = {lab.prediction_id for lab in labels._items if lab.label_source == "outcome_event"}
        return [
            o
            for o in self._items
            if (engine_name is None or o.engine_name == engine_name)
            and (entity_id is None or o.entity_id == entity_id)
            and (not unlabeled or o.prediction_id not in labeled_ids)
        ]

    def clear(self) -> None:
        self._items.clear()


class FeedbackRepository:
    def __init__(self) -> None:
        self._items: list[Feedback] = []

    def add(self, f: Feedback) -> None:
        self._items.append(f)

    def list(
        self,
        engine_name: str | None = None,
        feedback_type: str | None = None,
    ) -> list[Feedback]:
        return [
            f
            for f in self._items
            if (engine_name is None or f.engine_name == engine_name)
            and (feedback_type is None or f.feedback_type == feedback_type)
        ]

    def clear(self) -> None:
        self._items.clear()


class LabelRepository:
    def __init__(self) -> None:
        self._items: list[Label] = []
        self._by_prediction: dict[str, list[Label]] = defaultdict(list)

    def add(self, label: Label) -> None:
        self._items.append(label)
        self._by_prediction[label.prediction_id].append(label)

    def get_for_prediction(self, prediction_id: str) -> list[Label]:
        return copy.copy(self._by_prediction.get(prediction_id, []))

    def list(
        self,
        engine_name: str | None = None,
        label_type: str | None = None,
    ) -> list[Label]:
        return [
            lab
            for lab in self._items
            if (engine_name is None or lab.engine_name == engine_name)
            and (label_type is None or lab.label_type == label_type)
        ]

    def clear(self) -> None:
        self._items.clear()
        self._by_prediction.clear()


class TrainingDatasetRepository:
    def __init__(self) -> None:
        self._meta: dict[str, TrainingDataset] = {}
        self._frames: dict[str, Any] = {}

    def put(self, meta: TrainingDataset, frame: object | None = None) -> None:
        self._meta[meta.dataset_id] = meta
        if frame is not None:
            self._frames[meta.dataset_id] = frame

    def get(self, dataset_id: str) -> tuple[TrainingDataset | None, object | None]:
        return self._meta.get(dataset_id), self._frames.get(dataset_id)

    def list(self, engine_name: str | None = None) -> list[TrainingDataset]:
        return [m for m in self._meta.values() if engine_name is None or m.engine_name == engine_name]

    def clear(self) -> None:
        self._meta.clear()
        self._frames.clear()


class ModelVersionRepository:
    def __init__(self) -> None:
        self._items: dict[str, ModelVersion] = {}
        self._production: dict[str, str] = {}  # engine -> model_id
        self._artifacts: dict[str, Any] = {}  # in-memory artifact store

    def put(self, m: ModelVersion) -> None:
        self._items[m.model_id] = m

    def get(self, model_id: str) -> ModelVersion | None:
        return self._items.get(model_id)

    def get_production(self, engine_name: str) -> ModelVersion | None:
        model_id = self._production.get(engine_name)
        if not model_id:
            return None
        return self._items.get(model_id)

    def set_production(self, engine_name: str, model_id: str) -> None:
        for m in self._items.values():
            if m.engine_name == engine_name and m.status == "production":
                m.status = "archived"
        self._production[engine_name] = model_id
        m = self._items.get(model_id)
        if m is not None:
            m.status = "production"
            m.promoted_at = datetime.now(UTC)

    def list(self, engine_name: str | None = None) -> list[ModelVersion]:
        return [m for m in self._items.values() if engine_name is None or m.engine_name == engine_name]

    def next_version(self, engine_name: str) -> str:
        versions = [
            int(m.version[1:])
            for m in self._items.values()
            if m.engine_name == engine_name and m.version.startswith("v")
        ]
        next_int = max(versions, default=0) + 1
        return f"v{next_int}"

    def clear(self) -> None:
        self._items.clear()
        self._production.clear()
        self._artifacts.clear()


class DriftRepository:
    def __init__(self) -> None:
        self._items: list[DriftRecord] = []

    def add(self, d: DriftRecord) -> None:
        self._items.append(d)

    def list(
        self,
        engine_name: str | None = None,
        feature: str | None = None,
    ) -> list[DriftRecord]:
        return [
            d
            for d in self._items
            if (engine_name is None or d.engine_name == engine_name) and (feature is None or d.feature == feature)
        ]

    def clear(self) -> None:
        self._items.clear()


class ExperimentRepository:
    def __init__(self) -> None:
        self._items: dict[str, Experiment] = {}

    def put(self, e: Experiment) -> None:
        self._items[e.experiment_id] = e

    def get(self, experiment_id: str) -> Experiment | None:
        return self._items.get(experiment_id)

    def list(self, engine_name: str | None = None) -> list[Experiment]:
        return [e for e in self._items.values() if engine_name is None or e.engine_name == engine_name]

    def clear(self) -> None:
        self._items.clear()


class RetrainingRunRepository:
    def __init__(self) -> None:
        self._items: list[RetrainingRun] = []

    def add(self, run: RetrainingRun) -> None:
        self._items.append(run)

    def list(self, engine_name: str | None = None) -> list[RetrainingRun]:
        return [r for r in self._items if engine_name is None or r.engine_name == engine_name]

    def clear(self) -> None:
        self._items.clear()


class EventLogRepository:
    """Captures produced events for observability and audit."""

    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, event_type: str, payload: dict) -> None:
        self._items.append({"event": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()})

    def list(self, event_type: str | None = None) -> list[dict]:
        if event_type is None:
            return copy.copy(self._items)
        return [e for e in self._items if e["event"] == event_type]

    def clear(self) -> None:
        self._items.clear()


# ── Singletons ───────────────────────────────────────────────────────────────
_predictions = PredictionRepository()
_outcomes = OutcomeRepository()
_feedback = FeedbackRepository()
_labels = LabelRepository()
_datasets = TrainingDatasetRepository()
_models = ModelVersionRepository()
_drift = DriftRepository()
_experiments = ExperimentRepository()
_retrain_runs = RetrainingRunRepository()
_events = EventLogRepository()


def get_prediction_repository() -> PredictionRepository:
    return _predictions


def get_outcome_repository() -> OutcomeRepository:
    return _outcomes


def get_feedback_repository() -> FeedbackRepository:
    return _feedback


def get_label_repository() -> LabelRepository:
    return _labels


def get_dataset_repository() -> TrainingDatasetRepository:
    return _datasets


def get_model_repository() -> ModelVersionRepository:
    return _models


def get_drift_repository() -> DriftRepository:
    return _drift


def get_experiment_repository() -> ExperimentRepository:
    return _experiments


def get_retrain_run_repository() -> RetrainingRunRepository:
    return _retrain_runs


def get_event_log_repository() -> EventLogRepository:
    return _events


def reset_repositories() -> None:
    _predictions.clear()
    _outcomes.clear()
    _feedback.clear()
    _labels.clear()
    _datasets.clear()
    _models.clear()
    _drift.clear()
    _experiments.clear()
    _retrain_runs.clear()
    _events.clear()
