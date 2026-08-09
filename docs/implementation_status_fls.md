# Feedback Loop System — Implementation Status

## 1. Overview

The `feedback_loop` agent is a self-contained FastAPI service that implements the **Feedback Loop System (FLS)** for the CRM AI Assistant platform. It captures predictions, outcomes, overrides, and explicit feedback from downstream model sub-agents; joins them into point-in-time labels; monitors drift and performance; and orchestrates reproducible model retraining with fairness-gated promotion.

This document tracks what has been implemented against the FLS ticket (`docs/ticket_description_fls.md`) and all CEAAPSS subtasks, using the following legend:

| Emoji | Meaning |
|-------|---------|
| ✅ | Implemented and covered by tests in P0–P3 |
| 🚧 | Implemented as a stub / in-memory approximation or missing production integration |
| 📝 | Planned for P4 (Promotion / GA Launch) or future work |
| ❌ | Not implemented and not currently planned |

## 2. Scope Delivered

| Phase / Subtask | Theme | Status | Key Deliverables |
|-----------------|-------|--------|------------------|
| **P0** | Capture | ✅ | Prediction, outcome, override, and explicit-feedback ingestion with idempotent capture and stable `prediction_id` attribution |
| **P1** | Labels | ✅ | Label store, immediate + windowed outcome labels, point-in-time feature references, training-dataset assembly |
| **P2** | Monitoring | ✅ | Per-engine precision/recall/F1/AUC/ECE, feature-level PSI drift, drift alerts, retrain triggers |
| **P3** | Retraining | ✅ | Seeded reproducible training, champion/challenger evaluation, accuracy + fairness gates, model registry + lineage, rollback, A/B test framework |
| **CEAAPSS-559** | Model Retraining | 🚧 | Manual/scheduled/performance/drift triggers, data validation, retraining history, event-log notifications; production scheduler and alerting integrations are stubs |
| **CEAAPSS-560** | A/B Testing Framework | 🚧 | Consistent hashing, power-analysis minimum sample size, p-value threshold, guardrail metric, early stopping, one-click promotion; UI dashboard not built |
| **CEAAPSS-641** | Vision | ✅ | Captured in `docs/ticket_description_fls.md` and implemented in P0–P3 scope |
| **CEAAPSS-642** | Project Requirement | ✅ | Requirements mapped to code, tests, and this status doc |
| **CEAAPSS-643** | Functional Requirement | ✅ | FR-001 through FR-014 and FR-018/FR-020 implemented; FR-015/016/017/019 reserved for P4 |
| **CEAAPSS-644** | System Architecture | ✅ | Component design implemented (Prediction Logger, Label Builder, Drift Monitor, Retrain Orchestrator, Eval & Gate Service, Registry/Lineage adapter stubs) |
| **CEAAPSS-645** | System Design | ✅ | Data models, schemas, API specification, MLOps pipeline, and sequence/flow designs implemented |
| **P4** | Promotion / GA Launch | 📝 | Shadow/canary rollout, MLflow registry, Kafka streaming, admin dashboards, model cards, right-to-erasure, propensity weighting |

## 3. Functional Requirements (FR-001..FR-020)

| ID | Requirement | MoSCoW | Status | Notes |
|----|-------------|--------|--------|-------|
| FR-001 | Ingest prediction logs `{prediction_id, model, version, features_ref, output}` | Must | ✅ | `POST /api/v1/predictions` + `Prediction` model |
| FR-002 | Capture immediate outcomes (e.g., `email.replied`) | Must | ✅ | `POST /api/v1/outcomes` + `LabelBuilder` immediate join |
| FR-003 | Capture windowed/delayed outcomes (e.g., `deal.funded` at 60d) | Must | ✅ | `WINDOW_FUNDED_DAYS` and windowed match logic |
| FR-004 | Capture human overrides with before/after + reason | Must | ✅ | `POST /api/v1/feedback` with `feedback_type=override` |
| FR-005 | Capture explicit user feedback (thumbs, ratings, corrections) | Should | ✅ | `POST /api/v1/feedback` with `feedback_type=explicit` |
| FR-006 | Join predictions↔outcomes with point-in-time correctness | Must | ✅ | `app/label_builder.py` uses `features_ref` and `scored_at` |
| FR-007 | Assemble versioned training datasets with lineage | Must | ✅ | `assemble_training_dataset` writes `TrainingDataset` |
| FR-008 | Compute drift metrics (PSI) per feature on schedule | Must | ✅ | `app/drift.py` + `/api/v1/drift` |
| FR-009 | Compute performance decay (AUC, calibration ECE) vs. labels | Must | ✅ | `app/aggregator.py` |
| FR-010 | Trigger retrain candidates on drift/decay/volume thresholds | Must | ✅ | Drift persistence, accuracy threshold, `min_training_samples` |
| FR-011 | Orchestrate reproducible retraining pipelines (seeded) | Must | ✅ | `retrain_pipeline` pins seed, data, eval |
| FR-012 | Register models + artifacts + metrics in MLflow with lineage | Must | 🚧 | In-memory `ModelVersionRepository` mimics MLflow; real MLflow integration is P4 |
| FR-013 | Run challenger eval vs. champion on holdout | Must | ✅ | `train_model` holdout split + champion comparison |
| FR-014 | Enforce fairness gate (selection-rate ratio 0.80–1.25) before promote | Must | ✅ | `fairness_gate` in `model_retrainer.py` |
| FR-015 | Support shadow + canary deployment with auto-rollback | Must | 📝 | P4: only binary promote/rollback exists; no shadow/canary ramp |
| FR-016 | Apply propensity/exploration weighting to mitigate feedback bias | Should | 📝 | P4: not implemented |
| FR-017 | Propagate right-to-erasure deletions into training datasets | Must | 📝 | P4: not implemented |
| FR-018 | Expose API to query model lineage + feedback stats | Must | ✅ | `GET /api/v1/models/{model_id}/lineage`, `GET /api/v1/stats` |
| FR-019 | Generate model cards per promoted model | Should | 📝 | P4: not implemented |
| FR-020 | Alert on label match-rate drop or capture gaps | Should | 🚧 | Match rate exposed in stats; external alerting not wired |

## 4. Main FLS Ticket Requirement Coverage

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Explicit feedback (thumbs up/down) | ✅ | `POST /api/v1/feedback` with `feedback_type=explicit` |
| Implicit feedback / automatic outcome tracking | ✅ | `POST /api/v1/outcomes` matches outcomes to predictions |
| Outcome labeling (successful vs unsuccessful) | ✅ | `app/label_builder.py` produces binary labels from outcomes and overrides |
| Model performance dashboard (precision, recall, F1, AUC) | ✅ | `GET /api/v1/metrics/{engine_name}` |
| Retraining pipeline (labeled data → training → eval → deploy) | ✅ | `app/model_retrainer.py::retrain_pipeline` |
| Accuracy alert when below threshold | ✅ | `GET /api/v1/retrain/{engine}/triggers` + `POST /api/v1/retrain/{engine}/auto` |
| Labeled dataset export | ✅ | `GET /api/v1/export/{engine_name}` |
| A/B test framework | 🚧 | Core API + stats implemented; admin UI P4 |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Event schema `{prediction_id, engine_name, recommendation, context_snapshot, action_taken, outcome, label, timestamp}` | ✅ | `Prediction`, `Outcome`, `Feedback`, `Label` models in `app/domain/models.py` |
| Outcome observation windows (7d / 30d / 90d) | ✅ | `WINDOW_RESPONSE_DAYS`, `WINDOW_STAGE_DAYS`, `WINDOW_CONVERSION_DAYS` |
| MLOps pipeline (data validation → feature engineering → training → staging → production) | 🚧 | Validation + training + eval in place; feature engineering is identity; staging modeled as `challenger` only |
| Feedback store separate from transactional DB, read-optimized for ML | 🚧 | In-memory repositories; production DW/Postgres + S3 P4 |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Agent explicit feedback captured for all AI recommendations | ✅ | Generic feedback endpoint accepts any engine |
| Implicit outcome labels auto-generated for all recommendations with measurable outcomes | ✅ | Immediate labels + windowed delayed labels via `label_builder.py` |
| Model performance metrics visible per engine in real time | ✅ | Metrics computed on demand from the label store |
| Retraining pipeline triggers automatically when accuracy drops below threshold | ✅ | `auto_retrain` checks accuracy/auc vs `ACCURACY_THRESHOLD` |
| Feedback data cleanly formatted and exportable for training | ✅ | `export_labeled_dataset` returns clean rows |
| Alert fires when any engine's accuracy drops significantly vs baseline | 🚧 | Trigger state exposed via API and event logs; external alerting P4 |

## 5. CEAAPSS-559 — Model Retraining

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Retraining trigger: accuracy below configurable threshold | ✅ | `performance_retrain_needed()` in `app/service.py` |
| Retraining trigger: scheduled monthly cycle | 🚧 | `/api/v1/retrain/{engine}?trigger=scheduled` and `/auto` exist; production cron P4 |
| Retraining trigger: manually triggered by ML team | ✅ | `POST /api/v1/retrain/{engine_name}` |
| Data pipeline: pull labeled feedback data → validate quality → feature engineering → feed into training | 🚧 | Pull + validate + train implemented; feature engineering is identity only |
| Model evaluation before deployment (hold-out test set) | ✅ | `train_model` evaluates on stratified holdout |
| Staging environment before production promotion | 🚧 | Challenger status serves as staging; shadow/canary P4 |
| Rollback mechanism | ✅ | `POST /api/v1/models/rollback/{engine_name}` reverts to previous production model |
| Retraining log per cycle | ✅ | `RetrainingRun` repository + `GET /api/v1/retraining-log` |
| Notification to ML team on retraining start, completion, deployment outcome | 🚧 | Event-log entries written; external notifications P4 |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| MLOps pipeline: data validation → feature store → training job → evaluation → staging → production | 🚧 | Pipeline stages in code; feature store and staging P4 |
| Model registry with version-controlled storage + metrics | 🚧 | In-memory `ModelVersionRepository` mimics MLflow; real MLflow P4 |
| Evaluation metrics per engine (accuracy, precision, recall, F1, AUC) | ✅ | `app/aggregator.py` and `app/model_retrainer.py::_evaluate` |
| Training data minimum threshold (N samples) | ✅ | `MIN_TRAINING_SAMPLES` + `validate_training_dataset` |
| Infrastructure: scheduled batch or event-triggered pipeline | 🚧 | Event/drift triggers work; scheduled batch via external cron P4 |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Retraining pipeline triggers automatically when accuracy drops below defined threshold | ✅ | `auto_retrain` fires on performance breach |
| Monthly scheduled retraining cycle runs without manual intervention | 🚧 | API ready; production scheduler P4 |
| New model is evaluated against current model before any deployment | ✅ | Champion/challenger comparison with `accuracy_epsilon` |
| Staging evaluation must pass before production promotion | 🚧 | Challenger pass required; separate staging env P4 |
| Rollback to previous model version works within 15 minutes if needed | ✅ | Rollback is a single API call and immediate in memory |
| Full retraining log stored per cycle (trigger, data size, metrics, outcome) | ✅ | `RetrainingRun` records all fields |
| ML team is notified at each stage of the pipeline | 🚧 | Event-log notifications only; external channels P4 |

## 6. CEAAPSS-560 — A/B Testing Framework

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Experiment configuration (name, hypothesis, variants, traffic split %, audience segment, success metric, min duration) | ✅ | `POST /api/v1/experiments` accepts all fields |
| Traffic splitting to variants automatically | ✅ | `POST /api/v1/experiments/{id}/assign` |
| Supported experiment types: model / message / sequence / offer | 🚧 | `experiment_type` field captured; behavior is generic for all types |
| Statistical significance calculation (configurable p-value threshold) | ✅ | Two-proportion z-test + `p_value_threshold` |
| Early stopping rules (guardrail metric breached) | ✅ | `guardrail_metric` + `guardrail_threshold` |
| Results dashboard (conversion rate, engagement rate, significance per variant) | 🚧 | `POST /api/v1/experiments/{id}/evaluate` returns metrics; UI dashboard P4 |
| Winner promotion to 100% traffic | ✅ | `POST /api/v1/experiments/{id}/promote` with optional `force` |
| Experiment history log | ✅ | All experiments stored in `ExperimentRepository` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Traffic allocation: consistent hashing on `entity_id` | ✅ | `app/abtest.py::_variant_hash` |
| Minimum sample size calculation (power analysis) | ✅ | Per-variant sample size computed from observed effect size |
| Metrics tracked per variant: primary + guardrail | ✅ | `record_result` accepts `guardrail_value` |
| Results stored: `{experiment_id, variant, entity_id, metric_value, timestamp}` | ✅ | Stored in `Experiment.results` |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Experiments can be configured without code changes (admin UI or config) | 🚧 | Configurable via REST API; admin UI P4 |
| Traffic split is consistent — same lead always sees the same variant | ✅ | Deterministic hash on `experiment_id:entity_id` |
| Statistical significance is calculated and displayed in real time | 🚧 | Calculated via API; display layer P4 |
| Early stopping fires when a guardrail metric is breached | ✅ | `evaluate_experiment` returns `guardrail_breached` |
| Results dashboard shows per-variant metrics with significance indicator | 🚧 | API returns data; dashboard P4 |
| Winner can be promoted to 100% traffic with one action | ✅ | Single `POST /experiments/{id}/promote` call |
| All experiment history and results are stored and auditable | ✅ | Repository keeps all records |

## 7. CEAAPSS-641 to CEAAPSS-645 — Documentation & Design Deliverables

| Subtask | Title | Status | Evidence |
|---------|-------|--------|----------|
| **CEAAPSS-641** | Vision | ✅ | `docs/ticket_description_fls.md` §1–7 (Purpose, Problem, Value, Scope, KPIs) and P0–P3 implementation |
| **CEAAPSS-642** | Project Requirement | ✅ | `docs/ticket_description_fls.md` §8–10 (Objectives, NFRs, Constraints, Acceptance) and this status doc |
| **CEAAPSS-643** | Functional Requirement | ✅ | `docs/ticket_description_fls.md` §11 (FR-001..FR-020) mapped to code in §3 above |
| **CEAAPSS-644** | System Architecture | ✅ | `docs/ticket_description_fls.md` §12 (Context, Components, Data Flow, Tech Stack) and `app/` component layout |
| **CEAAPSS-645** | System Design | ✅ | `docs/ticket_description_fls.md` §13 (Detailed Design, Schemas, API Spec, MLOps Pipeline, Sequence) and implemented API endpoints |

## 8. P4 Implementation Update

This P4 pass added the following bug-audit fixes to the Feedback Loop agent while remaining a destination for cross-agent clients:

- **PAS → FLS outcome fan-out**: `src/agents/performance_analytics/app/clients/feedback_client.py` forwards `loan.funded` / `deal.lost` outcome events from Performance Analytics to `POST /api/v1/outcomes` when `OUTCOME_FANOUT_ENABLED=true` and `FEEDBACK_LOOP_URL` are configured in Performance Analytics.
- **Shared infrastructure**: `docker-compose.yml` now provisions Postgres, Redis, Redpanda (Kafka), Prometheus, Grafana, Schema Registry, and an OpenTelemetry Collector. FLS can switch from in-memory to disk persistence today; Postgres/S3 and Kafka integration remain P4.
- **Bug-audit fixes**: `app/abtest.py` auto-computes `traffic_split` so rounded variants sum to exactly 100% and absorbs the rounding delta in the first variant (`app/abtest.py:72-82`); the guardrail fallback only compares the primary conversion rate to `guardrail_threshold` when no separate `guardrail_metric` is configured (`app/abtest.py:230-239`); `app/abtest.py::_variant_hash` uses SHA-256 (`app/abtest.py:20-21`); `app/aggregator.py::_precision_recall_f1` returns a 4-tuple (`precision`, `recall`, `f1`, `accuracy`) matching its type annotation (`app/aggregator.py:50-66`); `app/drift.py` now imports and uses `timedelta` for exact time-window comparisons (`app/drift.py:6,86-88`); `app/model_retrainer.py` now uses `joblib` for dataset and model serialization and stores in-memory artifacts on the repository for tests when `STORAGE_BACKEND=memory` (`app/model_retrainer.py:9,167,218,271,274`).
- **Thread-safety hardening**: all in-memory repository methods now use `threading.RLock()` (`app/repositories.py`); `app/drift.py` protects the global `_reference_distributions` with a module-level `RLock`; `app/model_retrainer.py` no longer assigns directly to `repo._artifacts` and instead uses the public `ModelVersionRepository.add_artifact` method.
- **Default-dev-key rejection**: `app/core/config.py` now raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway.

Items still deferred:
- Real MLflow model registry, feature store, shadow/canary serving, production scheduler, Kafka consumer, Postgres/S3 persistence, and external alerting.

## 9. P4 — Promotion / GA Launch (Future)

P4 in the ticket is the Promotion milestone (weeks 18–22) followed by GA Launch. The following items are intentionally deferred to P4 and are **not** part of the current P0–P3 delivery:

| Item | P4 Phase | Status | Notes |
|------|----------|--------|-------|
| Shadow + canary deployment with auto-rollback | Promotion | 📝 | Binary promote/rollback in place; gradual ramp and shadow scoring not built |
| Real MLflow model registry integration | Promotion | 📝 | In-memory registry mimics MLflow fields; adapter swap P4 |
| Feature store integration (point-in-time `as_of` reads) | Promotion | 📝 | `features_ref` captured; actual feature-store client P4 |
| Production scheduler for monthly retrains (Airflow/Temporal) | Promotion | 📝 | `/auto` and `/retrain?trigger=scheduled` endpoints ready; cron not configured |
| External alerting (Slack/email/PagerDuty) | Promotion | 📝 | Event-log notifications emitted; alerting adapters P4 |
| Admin UI / MLOps dashboards | GA Launch | 📝 | Only REST metrics/drift/eval endpoints exist |
| Model cards per promoted model | GA Launch | 📝 | FR-019; auto-generated cards P4 |
| Right-to-erasure propagation into training datasets | GA Launch | 📝 | FR-017; deletion and re-derivation P4 |
| Propensity / explore-exploit weighting | GA Launch | 📝 | FR-016; bias mitigation P4 |
| Kafka consumer adapter for event-driven capture | GA Launch | 📝 | REST capture in place; streaming consumer P4 |
| Production persistence (Postgres + S3/DW) | GA Launch | 📝 | In-memory repositories; persistence adapters P4 |
| SOC 2 / fair-lending compliance runbooks | GA Launch | 📝 | Lineage and fairness gates implemented; runbooks P4 |

## 9. Component Coverage (System Architecture View)

| Component | Status | Notes |
|-----------|--------|-------|
| Prediction Logger | ✅ | `app/collector.py::log_prediction` + `PredictionRepository` |
| Outcome / Feedback Collector | ✅ | `app/collector.py` |
| Label Builder | ✅ | `app/label_builder.py` |
| Training-Data Assembler | ✅ | `app/model_retrainer.py::assemble_training_dataset` |
| Drift Monitor | ✅ | `app/drift.py` (PSI per feature); `timedelta` used for exact 30-day window comparisons |
| Performance Monitor | ✅ | `app/aggregator.py` |
| Retrain Orchestrator | ✅ | `app/service.py::auto_retrain`, `trigger_retrain`, drift-triggered retrain; `app/model_retrainer.py` uses `joblib` for dataset/model snapshots and in-memory artifact storage for tests |
| Eval & Gate Service | ✅ | Accuracy + fairness gates in `train_model` and `promote_model` |
| Deployment Manager | 📝 P4 | Direct promotion/rollback; shadow/canary ramp P4 |
| Lineage / Registry Adapter | 📝 P4 | In-memory registry + lineage reports; MLflow adapter P4 |

## 10. File Map

```
src/agents/feedback_loop/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI factory + middleware
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # pydantic-settings + environment defaults
│   │   └── logging.py          # structlog setup
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # auth + repo dependency providers
│   │   ├── health.py           # /health, /ready
│   │   └── feedback.py         # all FLS REST endpoints
│   ├── domain/
│   │   └── models.py           # Prediction, Outcome, Feedback, Label, ModelVersion, etc.
│   ├── collector.py            # ingestion wrappers (FR-001..FR-005)
│   ├── label_builder.py        # point-in-time label joins and windows (FR-006)
│   ├── aggregator.py           # per-engine performance metrics (FR-009)
│   ├── drift.py                # PSI reference/current drift (FR-008)
│   ├── model_retrainer.py      # dataset assembly, training, eval, gates, lineage (FR-007..FR-014)
│   ├── abtest.py               # experiment allocation + significance testing
│   ├── service.py              # orchestration layer used by API routes
│   └── repositories.py         # in-memory repo implementations
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # FastAPI TestClient fixture
│   └── test_feedback_loop.py   # 25 unit/functional tests covering P0-P3 + subtasks
├── pyproject.toml              # deps + ruff + pytest config
├── uv.lock                     # frozen dependency set
├── Dockerfile
├── Makefile
└── README.md
```

## 11. Integration into the Monorepo

| Layer | Change | Location |
|-------|--------|----------|
| API gateway routing | Added `feedback_loop` to `AGENTS` map | `src/api/main.py` |
| Compose orchestration | Added `feedback_loop` service + `FEEDBACK_LOOP_URL` | `docker-compose.yml` |
| Agent tests | Standalone pytest suite | `src/agents/feedback_loop/tests/` |

The gateway proxies `/feedback-loop/*` to the agent once `FEEDBACK_LOOP_URL` is configured.

## 12. Configuration Highlights

Key environment variables (see `src/agents/feedback_loop/.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | `change-me-dev-key` | Bearer/API-key auth |
| `LOG_LEVEL` | `INFO` | structlog level |
| `STORAGE_BACKEND` | `memory` | `memory` or `disk` |
| `DATA_DIR` | `./data/feedback_loop` | Where `disk` snapshots write |
| `MIN_TRAINING_SAMPLES` | `50` | Minimum labeled rows before retrain |
| `ACCURACY_THRESHOLD` | `0.70` | Retrain trigger floor |
| `ACCURACY_EPSILON` | `0.01` | Champion/challenger AUC regression tolerance |
| `FAIRNESS_RATIO_MIN` / `MAX` | `0.80` / `1.25` | 4/5ths selection-rate gate |

## 13. Known Limitations & Simplifications

- **Persistence**: The default implementation is in-memory. `STORAGE_BACKEND=disk` serializes dataset frames and model artifacts locally; swap `repositories.py` for Postgres/S3/DW for production (P4).
- **Statistical testing**: A/B tests use a pragmatic normal-approximation Z-test with a power-analysis minimum sample-size gate; production should use a proper power analysis and stratified t-test/Bootstrap where appropriate.
- **Model training**: `LogisticRegression` is used for all engines for reproducibility. In production each engine will plug in its own estimator.
- **MLflow**: The registry is an in-memory `ModelVersionRepository` that mimics MLflow fields (`run_id`, `params`, `metrics`, `model_uri`, `lineage`). Real MLflow integration is P4.
- **Kafka**: Event capture is currently REST-based; a Kafka consumer adapter can be added around `collector.py` without changing the domain logic (P4).
- **Notifications**: Event-log events are emitted for retraining lifecycle; an external notification adapter (Slack/email) is P4.
- **Shadow / canary / staged rollout**: Promotion is binary (challenger → production); gradual ramp and shadow scoring are P4.
- **Right-to-erasure / GDPR**: Deletion propagation into training datasets and re-derivation of affected models is P4.
- **Propensity / explore-exploit**: Feedback-loop bias mitigation through propensity weighting or exploration is P4.
- **Model cards**: Auto-generated model cards (FR-019) are P4.

## 14. Acceptance Criteria Checklist

- ✅ Prediction, outcome, override, and explicit-feedback ingestion endpoints return stable ids and are idempotent.
- ✅ Immediate and windowed outcome labels are generated and attributed to the originating `prediction_id`.
- ✅ Per-engine metrics (precision, recall, F1, AUC, ECE) are available via `GET /api/v1/metrics/{engine_name}`.
- ✅ PSI drift is computed per feature and sustained breaches trigger retrain candidates.
- ✅ Retraining pipeline assembles a versioned dataset, trains a seeded model, and evaluates on a held-out test set.
- ✅ Promotion requires non-regressing accuracy **and** a fairness selection-rate ratio within 0.80–1.25.
- ✅ Model lineage report (`GET /api/v1/models/{model_id}/lineage`) is reproducible and complete.
- 🚧 A/B test framework supports consistent variant allocation, power-analysis minimum sample sizes, p-value thresholds, guardrail metrics, and early stopping (dashboard/UI P4).
- 🚧 Model retraining supports manual, scheduled, performance, and drift triggers with data validation and event-log notifications (external alerting and production scheduler P4).
- ✅ Unit + functional tests pass (`25/25` in the agent test suite; `2/2` root gateway tests).

## 15. P4 Next Steps

1. Integrate real MLflow model registry and swap in production persistence (Postgres + S3/DW).
2. Add Kafka consumer adapter for event-driven capture.
3. Implement shadow / canary rollout with gradual traffic ramp and automatic rollback.
4. Add propensity / explore-exploit sampling to avoid feedback-loop bias.
5. Add right-to-erasure propagation for GDPR/CCPA compliance.
6. Build an admin UI / dashboard for A/B test results, drift alerts, and model cards.
7. Wire external notification channels (Slack/email) for retraining lifecycle events.
8. Add production scheduler (Airflow/Temporal) for monthly/scheduled retrain runs.
9. Auto-generate model cards per promoted model.
10. Add SOC 2 / fair-lending runbooks.
