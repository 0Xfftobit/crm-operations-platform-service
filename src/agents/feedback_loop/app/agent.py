"""Feedback Loop Agent — public orchestration facade.

The ``FeedbackLoopAgent`` wires the P0–P3 subsystems and exposes the engine's
public API. It is deliberately thin; most logic lives in ``collector``,
``label_builder``, ``aggregator``, ``drift``, ``model_retrainer``, and ``abtest``.
"""

from __future__ import annotations

from app import service


class FeedbackLoopAgent:
    """Engine-level agent for the Feedback Loop System."""

    ingest_prediction = staticmethod(service.ingest_prediction)
    ingest_outcome = staticmethod(service.ingest_outcome)
    ingest_feedback = staticmethod(service.ingest_feedback)
    get_labels = staticmethod(service.get_labels)
    build_all_labels = staticmethod(service.build_all_labels)
    get_metrics = staticmethod(service.get_metrics)
    get_all_metrics = staticmethod(service.get_all_metrics)
    evaluate_drift = staticmethod(service.evaluate_drift)
    get_drift_records = staticmethod(service.get_drift_records)
    trigger_retrain = staticmethod(service.trigger_retrain)
    auto_retrain = staticmethod(service.auto_retrain)
    performance_retrain_needed = staticmethod(service.performance_retrain_needed)
    scheduled_retrain_due = staticmethod(service.scheduled_retrain_due)
    promote_model = staticmethod(service.promote_model)
    rollback_model = staticmethod(service.rollback_model)
    get_models = staticmethod(service.get_models)
    get_model_lineage = staticmethod(service.get_model_lineage)
    get_datasets = staticmethod(service.get_datasets)
    get_capture_stats = staticmethod(service.get_capture_stats)
    create_experiment = staticmethod(service.create_experiment)
    assign_experiment_variant = staticmethod(service.assign_experiment_variant)
    record_experiment_result = staticmethod(service.record_experiment_result)
    evaluate_experiment = staticmethod(service.evaluate_experiment)
    promote_experiment_winner = staticmethod(service.promote_experiment_winner)
    export_labeled_dataset = staticmethod(service.export_labeled_dataset)
    get_retraining_log = staticmethod(service.get_retraining_log)
    reset_state = staticmethod(service.reset_state)
