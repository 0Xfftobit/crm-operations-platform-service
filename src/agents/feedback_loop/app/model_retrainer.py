"""P3 — Retraining: reproducible pipelines, model registry, and promotion gates."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app import aggregator, drift
from app.core.config import get_settings
from app.domain.models import LineageReport, ModelVersion, RetrainingRun, TrainingDataset
from app.messaging import kafka_bus
from app.repositories import (
    get_dataset_repository,
    get_event_log_repository,
    get_label_repository,
    get_model_repository,
    get_prediction_repository,
    get_retrain_run_repository,
)


def _ensure_dirs(settings: Any) -> None:
    if settings.storage_backend != "memory":
        settings.data_path.mkdir(parents=True, exist_ok=True)
        settings.model_path.mkdir(parents=True, exist_ok=True)


def _snapshot_path(dataset_id: str, settings: Any) -> Path:
    return settings.data_path / "datasets" / f"{dataset_id}.joblib"


def _artifact_path(model_id: str, settings: Any) -> Path:
    return settings.model_path / f"{model_id}.joblib"


def _binary_label(label_value: Any) -> float | None:
    if label_value is None:
        return None
    if isinstance(label_value, bool):
        return 1.0 if label_value else 0.0
    if isinstance(label_value, (int, float)):
        return float(label_value)
    if isinstance(label_value, str):
        if label_value.lower() in ("positive", "up", "yes", "true", "1", "success"):
            return 1.0
        if label_value.lower() in ("negative", "down", "no", "false", "0", "failure"):
            return 0.0
    return None


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def validate_training_dataset(df: Any) -> dict[str, Any]:
    """Check dataset quality before feeding a model (CEAAPSS-559 data validation)."""
    settings = get_settings()
    issues: list[str] = []
    n = len(df)
    if n < settings.min_training_samples:
        issues.append(f"too_few_rows: {n} < {settings.min_training_samples}")

    targets = df["__target__"].astype(float).values
    if len(set(targets)) < 2:
        issues.append("single_class_labels")
    else:
        counts = pd.Series(targets).value_counts(normalize=True)
        if counts.max() > 0.95:
            issues.append("class_imbalance")

    feature_cols = [
        c for c in df.columns if c not in {"__target__", "__split__", "prediction_id", "label_id", "protected_group"}
    ]
    if feature_cols:
        missing_ratio = float(df[feature_cols].isna().mean().mean())
        if missing_ratio > 0.5:
            issues.append(f"too_many_missing_values: {missing_ratio:.2f}")

    return {"valid": not issues, "issues": issues, "row_count": n}


def assemble_training_dataset(
    engine_name: str,
    now: datetime | None = None,
    seed: int = 42,
) -> TrainingDataset:
    """Assemble a versioned, point-in-time training dataset (FR-007)."""
    settings = get_settings()
    _ensure_dirs(settings)
    now = now or datetime.now(UTC)

    labels = [
        lab
        for lab in get_label_repository().list(engine_name=engine_name)
        if lab.in_window and _binary_label(lab.label_value) is not None
    ]

    rows: list[dict[str, Any]] = []
    feature_names: set[str] = set()
    label_ids: list[str] = []
    prediction_ids: list[str] = []

    for lab in labels:
        pred = get_prediction_repository().get(lab.prediction_id)
        if pred is None:
            continue
        y = _binary_label(lab.label_value)
        if y is None:
            continue

        # Use numeric features only; protected group is excluded from direct training
        # but kept for the fairness gate.
        features: dict[str, Any] = {
            k: float(v) for k, v in pred.features.items() if k != "protected_group" and _is_number(v)
        }
        if not features:
            continue

        row = {"__target__": y, "prediction_id": pred.prediction_id, "label_id": lab.label_id}
        row.update(features)
        if "protected_group" in pred.features:
            row["protected_group"] = pred.features["protected_group"]
        rows.append(row)
        feature_names.update(features.keys())
        label_ids.append(lab.label_id)
        prediction_ids.append(pred.prediction_id)

    if not rows:
        raise ValueError(f"no usable training rows for engine '{engine_name}'")

    df = pd.DataFrame(rows)

    # Data-quality gates must run before any imputation or split so that
    # class imbalance and excessive missing values are detected honestly.
    validation = validate_training_dataset(df)
    if not validation["valid"]:
        raise ValueError(f"training_data_validation_failed: {validation['issues']}")

    for col in sorted(feature_names):
        if col not in df.columns:
            df[col] = 0.0
    df[sorted(feature_names)] = df[sorted(feature_names)].fillna(0.0)

    # Stratified hold-out split.
    y = df["__target__"].astype(float).values
    train_idx, test_idx = train_test_split(
        np.arange(len(df)),
        test_size=settings.holdout_ratio,
        random_state=seed,
        stratify=y if len(set(y)) > 1 else None,
    )
    df["__split__"] = "train"
    df.loc[test_idx, "__split__"] = "test"

    dataset_id = f"ds-{engine_name}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{len(rows)}"
    if settings.storage_backend == "memory":
        snapshot_uri = f"memory://{dataset_id}"
    else:
        path = _snapshot_path(dataset_id, settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"df": df}, path)
        snapshot_uri = str(path)

    feature_names_list = sorted(feature_names)
    meta = TrainingDataset(
        dataset_id=dataset_id,
        engine_name=engine_name,
        snapshot_uri=snapshot_uri,
        row_count=len(rows),
        feature_names=feature_names_list,
        created_at=now,
        lineage={
            "engine_name": engine_name,
            "prediction_ids": prediction_ids,
            "label_ids": label_ids,
            "seed": seed,
            "holdout_ratio": settings.holdout_ratio,
            "feature_versions": list(feature_names_list),
        },
    )
    get_dataset_repository().put(meta, df)

    # Reference distribution for drift monitoring.
    train_df = df[df["__split__"] == "train"]
    for feature in feature_names_list:
        drift.set_reference(engine_name, feature, train_df[feature].tolist())

    return meta


def train_model(
    engine_name: str,
    dataset_id: str,
    seed: int = 42,
) -> ModelVersion:
    """Train a challenger, evaluate it, and run accuracy/fairness gates."""
    settings = get_settings()
    _ensure_dirs(settings)

    meta, frame = get_dataset_repository().get(dataset_id)
    if meta is None:
        raise ValueError(f"unknown dataset {dataset_id}")
    if frame is not None:
        df = frame
    else:
        bundle = joblib.load(meta.snapshot_uri)
        df = bundle.get("df") if isinstance(bundle, dict) else bundle

    train_df = df[df["__split__"] == "train"]
    test_df = df[df["__split__"] == "test"]
    feature_cols = [
        c for c in df.columns if c not in {"__target__", "__split__", "prediction_id", "label_id", "protected_group"}
    ]

    X_train = train_df[feature_cols].values
    y_train = train_df["__target__"].astype(float).values
    X_test = test_df[feature_cols].values
    y_test = test_df["__target__"].astype(float).values

    # Protected group for fairness gate is taken from the original prediction features,
    # not used as a direct training feature.
    protected = None
    if "protected_group" in test_df.columns:
        protected = test_df["protected_group"].values

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=seed)),
        ]
    )
    model.fit(X_train, y_train)

    y_prob = model.predict_proba(X_test)[:, 1]
    y_pred = (y_prob >= 0.5).astype(float)

    metrics = _evaluate(y_test, y_prob, y_pred)
    fairness = fairness_gate(y_test, y_pred, protected)
    metrics["fairness_ratio"] = fairness["ratio"]
    metrics["fairness_passed"] = fairness["passed"]

    # Accuracy + fairness promotion gate (FR-013, FR-014).
    champion = get_model_repository().get_production(engine_name)
    accuracy_regress = False
    if champion is not None:
        champ_auc = champion.metrics.get("auc")
        if champ_auc is not None and metrics["auc"] is not None:
            if metrics["auc"] < champ_auc - settings.accuracy_epsilon:
                accuracy_regress = True

    gate_passed = (not accuracy_regress) and fairness["passed"]

    version = get_model_repository().next_version(engine_name)
    model_id = f"{engine_name}@{version}"
    artifact_path = (
        _artifact_path(model_id, settings) if settings.storage_backend != "memory" else f"memory://{model_id}"
    )

    if settings.storage_backend != "memory":
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "feature_names": feature_cols, "seed": seed}, artifact_path)
    else:
        # In-memory artifacts are stored on the repository object for tests.
        get_model_repository().add_artifact(
            model_id, {"model": model, "feature_names": feature_cols, "seed": seed}
        )

    mv = ModelVersion(
        model_id=model_id,
        engine_name=engine_name,
        version=version,
        dataset_id=dataset_id,
        artifact_path=str(artifact_path),
        metrics=metrics,
        gate="passed" if gate_passed else "failed",
        status="challenger" if gate_passed else "rejected",
        lineage={
            "dataset_id": dataset_id,
            "feature_names": feature_cols,
            "seed": seed,
            "champion": champion.model_id if champion else None,
            "eval": metrics,
            "fairness": fairness,
        },
    )
    get_model_repository().put(mv)
    return mv


def _evaluate(y_test: np.ndarray, y_prob: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    try:
        auc = float(roc_auc_score(y_test, y_prob)) if len(set(y_test)) > 1 else None
    except Exception:
        auc = None

    ece = aggregator._calibration_ece(y_test.tolist(), y_prob.tolist())
    return {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "auc": round(auc, 4) if auc is not None else None,
        "ece": round(ece, 4) if ece is not None else None,
        "support": int(len(y_test)),
    }


def fairness_gate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    protected_attr: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute selection-rate ratio across protected-class cohorts."""
    settings = get_settings()
    if protected_attr is None:
        return {"passed": True, "ratio": 1.0, "selection_rates": {}}

    rates: dict[str, float] = {}
    for group in set(protected_attr):
        mask = protected_attr == group
        rates[str(group)] = round(float(y_pred[mask].mean()), 4)

    if not rates:
        return {"passed": True, "ratio": 1.0, "selection_rates": rates}

    min_rate = min(rates.values())
    max_rate = max(rates.values())
    ratio = min_rate / max_rate if max_rate else 1.0
    passed = settings.fairness_ratio_min <= ratio <= settings.fairness_ratio_max
    return {
        "passed": bool(passed),
        "ratio": round(ratio, 4),
        "selection_rates": rates,
    }


def promote_model(model_id: str) -> dict[str, Any]:
    """Promote a challenger to production (FR-015 partial)."""
    model = get_model_repository().get(model_id)
    if model is None:
        raise ValueError(f"unknown model {model_id}")
    if model.gate != "passed":
        raise ValueError("model did not pass promotion gate")

    get_model_repository().set_production(model.engine_name, model_id)
    get_event_log_repository().add(
        "model.promoted",
        {"model_id": model_id, "engine_name": model.engine_name, "metrics": model.metrics},
    )
    return {"status": "promoted", "model_id": model_id}


def rollback(engine_name: str) -> dict[str, Any]:
    """Roll back to the most recent archived production model."""
    repo = get_model_repository()
    current = repo.get_production(engine_name)
    current_id = current.model_id if current else None
    candidates = [
        m
        for m in repo.list(engine_name=engine_name)
        if m.status in ("archived", "challenger") and m.gate == "passed" and m.model_id != current_id
    ]
    if not candidates:
        return {"status": "no_rollback_candidate", "engine_name": engine_name}

    # Choose the model that was most recently in production.
    previous = max(candidates, key=lambda m: m.promoted_at or datetime.min.replace(tzinfo=UTC))
    repo.set_production(engine_name, previous.model_id)
    previous.status = "production"
    if current is not None:
        current.status = "rollback"
    get_event_log_repository().add("model.rolled_back", {"engine_name": engine_name, "rolled_to": previous.model_id})
    return {"status": "rolled_back", "model_id": previous.model_id, "engine_name": engine_name}


def retrain_pipeline(
    engine_name: str,
    trigger: str = "manual",
    auto: bool = False,
    now: datetime | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Run a full reproducible retraining pipeline (FR-011/012/013/014)."""
    settings = get_settings()
    now = now or datetime.now(UTC)

    # Auto-retrain only allowed for low-risk models.
    if auto and settings.auto_retrain_risk_class != "low":
        return {"status": "rejected", "reason": "auto_retrain_not_allowed_for_risk_class"}

    get_event_log_repository().add(
        "retrain.started",
        {"engine_name": engine_name, "trigger": trigger, "auto": auto},
    )

    try:
        dataset = assemble_training_dataset(engine_name, now=now, seed=seed)
    except ValueError as exc:
        run = RetrainingRun(
            engine_name=engine_name,
            trigger=trigger,
            outcome="rejected",
            gate="failed",
        )
        get_retrain_run_repository().add(run)
        get_event_log_repository().add(
            "model.rejected",
            {"engine_name": engine_name, "trigger": trigger, "reason": str(exc)},
        )
        return {"status": "rejected", "reason": str(exc), "run_id": run.run_id}

    if dataset.row_count < settings.min_training_samples:
        run = RetrainingRun(
            engine_name=engine_name,
            trigger=trigger,
            dataset_id=dataset.dataset_id,
            outcome="rejected",
            gate="failed",
        )
        get_retrain_run_repository().add(run)
        get_event_log_repository().add(
            "retrain.completed",
            {"engine_name": engine_name, "trigger": trigger, "outcome": "insufficient_samples"},
        )
        return {"status": "rejected", "reason": "insufficient_training_samples", "run_id": run.run_id}

    challenger = train_model(engine_name, dataset.dataset_id, seed=seed)
    run = RetrainingRun(
        engine_name=engine_name,
        trigger=trigger,
        dataset_id=dataset.dataset_id,
        model_id=challenger.model_id,
        metrics=challenger.metrics,
        gate=challenger.gate,
    )

    if challenger.gate == "passed":
        promote_model(challenger.model_id)
        kafka_bus.produce_model_retrained(
            engine_name=engine_name,
            model_id=challenger.model_id,
            metrics=challenger.metrics,
            dataset_id=dataset.dataset_id,
        )
        run.outcome = "promoted"
        get_retrain_run_repository().add(run)
        get_event_log_repository().add(
            "retrain.completed",
            {
                "engine_name": engine_name,
                "trigger": trigger,
                "outcome": "promoted",
                "model_id": challenger.model_id,
            },
        )
        return {
            "status": "promoted",
            "model_id": challenger.model_id,
            "dataset_id": dataset.dataset_id,
            "metrics": challenger.metrics,
            "run_id": run.run_id,
        }

    # Accuracy regression or fairness gate failure.
    champion = get_model_repository().get_production(engine_name)
    if champion is None:
        # No safe model to roll back to; leave challenger as rejected.
        run.outcome = "rejected"
        get_retrain_run_repository().add(run)
        get_event_log_repository().add(
            "model.rejected",
            {
                "engine_name": engine_name,
                "trigger": trigger,
                "model_id": challenger.model_id,
                "reason": "gate_failed",
                "metrics": challenger.metrics,
            },
        )
        get_event_log_repository().add(
            "retrain.completed",
            {"engine_name": engine_name, "trigger": trigger, "outcome": "rejected"},
        )
        return {
            "status": "rejected",
            "reason": "gate_failed",
            "model_id": challenger.model_id,
            "metrics": challenger.metrics,
            "run_id": run.run_id,
        }

    # Roll back to previous champion if the new model regresses in production shadow.
    rollback(engine_name)
    run.outcome = "rollback"
    get_retrain_run_repository().add(run)
    get_event_log_repository().add(
        "retrain.completed",
        {
            "engine_name": engine_name,
            "trigger": trigger,
            "outcome": "rollback",
            "model_id": challenger.model_id,
            "champion": champion.model_id,
        },
    )
    return {
        "status": "rollback",
        "model_id": challenger.model_id,
        "champion": champion.model_id,
        "metrics": challenger.metrics,
        "run_id": run.run_id,
    }


def model_lineage(model_id: str) -> LineageReport:
    """Return a complete lineage report for a model version (FR-018)."""
    model = get_model_repository().get(model_id)
    if model is None:
        raise ValueError(f"unknown model {model_id}")
    dataset, _ = get_dataset_repository().get(model.dataset_id)
    return LineageReport(
        model_id=model.model_id,
        engine_name=model.engine_name,
        version=model.version,
        training_dataset=model.dataset_id,
        snapshot_uri=dataset.snapshot_uri if dataset else "",
        feature_versions=model.lineage.get("feature_names", []),
        eval=model.metrics,
        promoted_at=model.promoted_at,
        gate=model.gate,
    )
