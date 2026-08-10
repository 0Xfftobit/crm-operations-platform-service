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
| **P4** | Promotion / GA Launch | 🚧 | Kafka producer/consumer baseline ✅ (`app/messaging/kafka_bus.py`, in-memory fallback); MLflow registry, shadow/canary, admin dashboards, model cards, right-to-erasure, propensity weighting still P4 |

### Latest P4 Pass (2026-08-10)

The latest P4 hardening pass landed the following in `feedback_loop` while keeping the agent a destination for cross-agent clients:

- **Kafka producer/consumer contract** (`app/messaging/kafka_bus.py`):
  - Canonical/short topic helpers and per-deal/lead/contact partition keys (`partition_key`, lines 69–78).
  - Graceful degradation to an in-process log when `KAFKA_PRODUCE` / `KAFKA_CONSUMER_ENABLED` are off, `aiokafka`/broker is unavailable, or no event loop is running.
  - Producer API: `start_producer`, `produce`, `produce_sync`; consumer API: `start_consumer`/`consume_loop` with `crm.outcome.funded`, `crm.outcome.lost`, and `crm.outreach.converted` dispatch to `service.ingest_outcome`.
  - Emits `crm.model.retrained` and `crm.champion.promoted` after a successful retrain.
- **Default-dev-key rejection** (`app/core/config.py` lines 83–89): raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`.
- **Wildcard CORS rejection** (`app/core/config.py` lines 91–97): raises `ValueError` if `CORS_ORIGINS="*"` outside local-like environments.
- **Test suite expansion** (63 passed, 11 warnings):
  - `tests/test_kafka_bus.py` — 16 tests
  - `tests/test_security.py` — 4 tests
  - `tests/test_disk_persistence.py` — 1 test
  - `tests/test_retraining_rejections_and_triggers.py` — 8 tests
  - `tests/test_abtest_and_feedback.py` — 9 tests
  - `tests/test_feedback_loop.py` — 25 existing unit/functional tests

| P4 Item | Status | Notes |
|---------|--------|-------|
| Kafka producer/consumer contract | ✅ | `app/messaging/kafka_bus.py`; in-memory fallback; real broker and end-to-end fan-out still P4 |
| Default-dev-key rejection | ✅ | `app/core/config.py:83-89`; `tests/test_security.py` |
| CORS wildcard validation | ✅ | `app/core/config.py:91-97`; `tests/test_security.py` |
| X-Request-ID propagation | 📝 | Not implemented inside the FLS agent; tracked at the API gateway layer |

## 3. Functional Requirements (FR-001..FR-020)

| ID | Requirement | MoSCoW | Status | Notes |
|----|-------------|--------|--------|-------|
| FR-001 | Ingest prediction logs `{prediction_id, model, version, features_ref, output}` | Must | ✅ | `POST /api/v1/predictions` + `Prediction` model |
| FR-002 | Capture immediate outcomes (e.g., `email.replied`) | Must | ✅ | `POST /api/v1/outcomes` + `LabelBuilder` immediate join |
| FR-003 | Capture windowed/delayed outcomes (e.g., `deal.funded` at 60d) | Must | ✅ | `window_response_days`, `window_stage_days`, `window_conversion_days`, and `funded_label_window_days` in `app/label_builder.py:27-41,105-128`; out-of-window matches retained for audit |
| FR-004 | Capture human overrides with before/after + reason | Must | ✅ | `POST /api/v1/feedback` with `feedback_type=override` |
| FR-005 | Capture explicit user feedback (thumbs, ratings, corrections) | Should | ✅ | `POST /api/v1/feedback` with `feedback_type=explicit` |
| FR-006 | Join predictions↔outcomes with point-in-time correctness | Must | ✅ | `app/label_builder.py:105-128` matches by `prediction_id` or entity+window; `features_ref` and `scored_at` prevent leakage |
| FR-007 | Assemble versioned training datasets with lineage | Must | ✅ | `assemble_training_dataset` in `app/model_retrainer.py:95-198` writes `TrainingDataset` with `prediction_ids`, `label_ids`, `seed`, and `feature_versions` |
| FR-008 | Compute drift metrics (PSI) per feature on schedule | Must | ✅ | `app/drift.py:73-164` computes PSI; `/api/v1/drift/evaluate/{engine_name}` in `app/api/feedback.py:63-65` |
| FR-009 | Compute performance decay (AUC, calibration ECE) vs. labels | Must | ✅ | `app/aggregator.py:95-147` exposes accuracy, precision, recall, F1, AUC, ECE per engine |
| FR-010 | Trigger retrain candidates on drift/decay/volume thresholds | Must | ✅ | Drift persistence in `app/drift.py:166-181`; accuracy threshold and `min_training_samples` in `app/service.py:71-132` and `app/model_retrainer.py:422-435` |
| FR-011 | Orchestrate reproducible retraining pipelines (seeded) | Must | ✅ | `retrain_pipeline` in `app/model_retrainer.py:386-516` pins `seed` and lineage; `LogisticRegression` estimator is hard-coded in `app/model_retrainer.py:236-241` |
| FR-012 | Register models + artifacts + metrics in MLflow with lineage | Must | 🚧 | In-memory `ModelVersionRepository` in `app/repositories.py:183-237` mimics MLflow fields; `ModelVersion` written in `app/model_retrainer.py:278-296`; real MLflow adapter is P4 |
| FR-013 | Run challenger eval vs. champion on holdout | Must | ✅ | `train_model` in `app/model_retrainer.py:201-298` uses stratified `train_test_split` and compares `auc` to champion with `accuracy_epsilon` |
| FR-014 | Enforce fairness gate (selection-rate ratio 0.80–1.25) before promote | Must | ✅ | `fairness_gate` in `app/model_retrainer.py:318-344`; enforced in `train_model` line 248 and `promote_model` line 352 |
| FR-015 | Support shadow + canary deployment with auto-rollback | Must | 📝 | P4: `ModelVersion.status` in `app/domain/models.py:93` reserves `shadow`/`canary`, but only binary `promote`/`rollback` exists; no ramp logic |
| FR-016 | Apply propensity/exploration weighting to mitigate feedback bias | Should | 📝 | P4: no propensity/exploration code; feedback and outcomes are not reweighted |
| FR-017 | Propagate right-to-erasure deletions into training datasets | Must | 📝 | P4: no erasure endpoint or re-derivation pipeline; `consent.erasure_requested` events not consumed |
| FR-018 | Expose API to query model lineage + feedback stats | Must | ✅ | `GET /api/v1/models/{model_id}/lineage` (`app/api/feedback.py:118-124`, `app/model_retrainer.py:519-535`) and `GET /api/v1/stats` (`app/api/feedback.py:201-203`) |
| FR-019 | Generate model cards per promoted model | Should | 📝 | P4: no model-card generation; `ModelVersion.metrics`/`lineage` contain data that would populate a card |
| FR-020 | Alert on label match-rate drop or capture gaps | Should | 🚧 | `match_rate` in `app/aggregator.py:134` and `app/service.py:155-170`; `label.quarantined` events logged but no external alerting |

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
| A/B test framework | 🚧 | Core API + stats in `app/abtest.py:56-271` and `tests/test_feedback_loop.py:292-454`; admin UI / model-variant auto-experiments P4 |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Event schema `{prediction_id, engine_name, recommendation, context_snapshot, action_taken, outcome, label, timestamp}` | ✅ | `Prediction`, `Outcome`, `Feedback`, `Label` models in `app/domain/models.py` |
| Outcome observation windows (7d / 30d / 90d) | ✅ | `window_response_days`, `window_stage_days`, `window_conversion_days`, and `funded_label_window_days` in `app/label_builder.py:27-41,105-128` |
| MLOps pipeline (data validation → feature engineering → training → staging → production) | 🚧 | `validate_training_dataset` in `app/model_retrainer.py:68-93`; `assemble_training_dataset` in `app/model_retrainer.py:95-198`; `LogisticRegression` training in `app/model_retrainer.py:236-241`; feature engineering is identity; staging is `challenger` only |
| Feedback store separate from transactional DB, read-optimized for ML | 🚧 | In-memory `RLock`-protected repositories in `app/repositories.py`; `STORAGE_BACKEND=disk` writes `joblib` snapshots locally; production DW/Postgres + S3 P4 |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Agent explicit feedback captured for all AI recommendations | ✅ | Generic feedback endpoint accepts any engine |
| Implicit outcome labels auto-generated for all recommendations with measurable outcomes | ✅ | Immediate labels + windowed delayed labels via `label_builder.py` |
| Model performance metrics visible per engine in real time | ✅ | Metrics computed on demand from the label store |
| Retraining pipeline triggers automatically when accuracy drops below threshold | ✅ | `auto_retrain` checks accuracy/auc vs `ACCURACY_THRESHOLD` |
| Feedback data cleanly formatted and exportable for training | ✅ | `export_labeled_dataset` returns clean rows |
| Alert fires when any engine's accuracy drops significantly vs baseline | 🚧 | `performance_retrain_needed` in `app/service.py:95-104` and `evaluate_drift` in `app/service.py:71-84`; trigger state exposed via API and event logs; external alerting P4 |

## 5. CEAAPSS-559 — Model Retraining

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Retraining trigger: accuracy below configurable threshold | ✅ | `performance_retrain_needed()` in `app/service.py:95-104`; triggered via `auto_retrain` in `app/service.py:117-132` |
| Retraining trigger: scheduled monthly cycle | 🚧 | `scheduled_retrain_due()` in `app/service.py:107-115` and `/auto` endpoint; no production cron configured |
| Retraining trigger: manually triggered by ML team | ✅ | `POST /api/v1/retrain/{engine_name}` |
| Data pipeline: pull labeled feedback data → validate quality → feature engineering → feed into training | 🚧 | Pull in `app/model_retrainer.py:95-198`; validation in `app/model_retrainer.py:68-93`; `LogisticRegression` training in `app/model_retrainer.py:236-241`; feature engineering is identity (numeric coercion + `StandardScaler` in model pipeline) |
| Model evaluation before deployment (hold-out test set) | ✅ | `train_model` evaluates on stratified holdout |
| Staging environment before production promotion | 🚧 | `challenger` status in `app/model_retrainer.py:286` is the only pre-production gate; `ModelVersion.status` in `app/domain/models.py:93` reserves `shadow`/`canary` but no ramp logic |
| Rollback mechanism | ✅ | `POST /api/v1/models/rollback/{engine_name}` (`app/api/feedback.py:108-110`) calls `rollback()` in `app/model_retrainer.py:363-383` |
| Retraining log per cycle | ✅ | `RetrainingRun` repository + `GET /api/v1/retraining-log` |
| Notification to ML team on retraining start, completion, deployment outcome | 🚧 | `EventLogRepository` in `app/repositories.py:311-330` captures `retrain.started`, `retrain.completed`, `model.promoted`; external channels P4 |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| MLOps pipeline: data validation → feature store → training job → evaluation → staging → production | 🚧 | Validation in `app/model_retrainer.py:68-93`; training/eval/gates in `app/model_retrainer.py:201-298`; `features_ref` captured but no feature-store client; staging is `challenger` only |
| Model registry with version-controlled storage + metrics | 🚧 | `ModelVersionRepository` in `app/repositories.py:183-237` (in-memory, `RLock`-protected); `ModelVersion` in `app/model_retrainer.py:278-296`; real MLflow P4 |
| Evaluation metrics per engine (accuracy, precision, recall, F1, AUC) | ✅ | `app/aggregator.py:95-147` and `app/model_retrainer.py:300-315` compute accuracy, precision, recall, F1, AUC, ECE |
| Training data minimum threshold (N samples) | ✅ | `MIN_TRAINING_SAMPLES` in `app/core/config.py:48`; enforced in `app/model_retrainer.py:73,422-435` and `validate_training_dataset` in `app/model_retrainer.py:68-93` |
| Infrastructure: scheduled batch or event-triggered pipeline | 🚧 | Drift event path in `app/service.py:71-84`; performance path in `app/service.py:117-132`; no external cron/scheduler configured |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Retraining pipeline triggers automatically when accuracy drops below defined threshold | ✅ | `auto_retrain` in `app/service.py:117-132` fires `retrain_pipeline` when `performance_retrain_needed` is true |
| Monthly scheduled retraining cycle runs without manual intervention | 🚧 | `scheduled_retrain_due()` in `app/service.py:107-115` and `/auto` endpoint; external cron/scheduler P4 |
| New model is evaluated against current model before any deployment | ✅ | Champion/challenger comparison with `accuracy_epsilon` |
| Staging evaluation must pass before production promotion | 🚧 | `gate_passed` in `app/model_retrainer.py:261` is required for `challenger` status; separate staging environment and shadow/canary P4 |
| Rollback to previous model version works within 15 minutes if needed | ✅ | Rollback is a single API call and immediate in memory |
| Full retraining log stored per cycle (trigger, data size, metrics, outcome) | ✅ | `RetrainingRun` records all fields |
| ML team is notified at each stage of the pipeline | 🚧 | `EventLogRepository` in `app/repositories.py:311-330` records `retrain.started`, `retrain.completed`, `model.promoted`, `model.rolled_back`; Slack/email/PagerDuty P4 |

## 6. CEAAPSS-560 — A/B Testing Framework

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Experiment configuration (name, hypothesis, variants, traffic split %, audience segment, success metric, min duration) | ✅ | `POST /api/v1/experiments` accepts all fields |
| Traffic splitting to variants automatically | ✅ | `POST /api/v1/experiments/{id}/assign` |
| Supported experiment types: model / message / sequence / offer | 🚧 | `experiment_type` in `app/domain/models.py:125` and `app/abtest.py:56-98` is captured but behavior is generic for all types; type-specific routing P4 |
| Statistical significance calculation (configurable p-value threshold) | ✅ | Two-proportion z-test + `p_value_threshold` |
| Early stopping rules (guardrail metric breached) | ✅ | `guardrail_metric` + `guardrail_threshold` |
| Results dashboard (conversion rate, engagement rate, significance per variant) | 🚧 | `POST /api/v1/experiments/{id}/evaluate` in `app/abtest.py:146-244` returns metrics; UI dashboard P4 |
| Winner promotion to 100% traffic | ✅ | `POST /api/v1/experiments/{id}/promote` with optional `force` |
| Experiment history log | ✅ | All experiments stored in `ExperimentRepository` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Traffic allocation: consistent hashing on `entity_id` | ✅ | `app/abtest.py:18-21,101-114` uses SHA-256 hash of `experiment_id:entity_id` |
| Minimum sample size calculation (power analysis) | ✅ | `app/abtest.py:42-53,190-211` computes per-variant minimum sample size from observed effect size |
| Metrics tracked per variant: primary + guardrail | ✅ | `record_result` in `app/abtest.py:116-143` accepts `guardrail_value`; `evaluate_experiment` in `app/abtest.py:146-244` computes guardrail mean and breach |
| Results stored: `{experiment_id, variant, entity_id, metric_value, timestamp}` | ✅ | Stored in `Experiment.results` by `record_result` in `app/abtest.py:131-141` and persisted in `ExperimentRepository` (`app/repositories.py:271-290`) |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Experiments can be configured without code changes (admin UI or config) | 🚧 | Configurable via `POST /api/v1/experiments` in `app/api/feedback.py:147-149`; admin UI / config file P4 |
| Traffic split is consistent — same lead always sees the same variant | ✅ | Deterministic hash on `experiment_id:entity_id` |
| Statistical significance is calculated and displayed in real time | 🚧 | Calculated by `evaluate_experiment` in `app/abtest.py:146-244`; display layer P4 |
| Early stopping fires when a guardrail metric is breached | ✅ | `evaluate_experiment` returns `guardrail_breached` |
| Results dashboard shows per-variant metrics with significance indicator | 🚧 | `POST /api/v1/experiments/{id}/evaluate` in `app/abtest.py:146-244` returns data; dashboard P4 |
| Winner can be promoted to 100% traffic with one action | ✅ | Single `POST /experiments/{id}/promote` call |
| All experiment history and results are stored and auditable | ✅ | Repository keeps all records |

## 7. CEAAPSS-641 to CEAAPSS-645 — Documentation & Design Deliverables

| Subtask | Title | Status | Evidence |
|---------|-------|--------|----------|
| **CEAAPSS-641** | Vision | ✅ | `docs/ticket_description_fls.md` §1–7 (Purpose, Problem, Value, Scope, KPIs) and P0–P3 implementation |
| **CEAAPSS-642** | Project Requirement | ✅ | `docs/ticket_description_fls.md` §8–10 (Objectives, NFRs, Constraints, Acceptance) and this status doc §16–17 (BR-1..BR-6 and NFR status) |
| **CEAAPSS-643** | Functional Requirement | ✅ | `docs/ticket_description_fls.md` §11 (FR-001..FR-020) mapped to code in §3 above |
| **CEAAPSS-644** | System Architecture | ✅ | `docs/ticket_description_fls.md` §12 (Context, Components, Data Flow, Tech Stack) and `app/` component layout |
| **CEAAPSS-645** | System Design | ✅ | `docs/ticket_description_fls.md` §13 (Detailed Design, Schemas, API Spec, MLOps Pipeline, Sequence) and implemented API endpoints / models in `app/domain/models.py` |

## 8. P4 Implementation Update

This P4 pass added the following bug-audit fixes to the Feedback Loop agent while remaining a destination for cross-agent clients:

- **PAS → FLS outcome fan-out**: `src/agents/performance_analytics/app/clients/feedback_client.py` forwards `loan.funded` / `deal.lost` outcome events from Performance Analytics to `POST /api/v1/outcomes` when `OUTCOME_FANOUT_ENABLED=true` and `FEEDBACK_LOOP_URL` are configured in Performance Analytics.
- **Shared infrastructure**: `docker-compose.yml` now provisions Postgres, Redis, Redpanda (Kafka), Prometheus, Grafana, Schema Registry, and an OpenTelemetry Collector. FLS can switch from in-memory to disk persistence today; Postgres/S3 and Kafka integration remain P4.
- **Bug-audit fixes**: `app/abtest.py` auto-computes `traffic_split` so rounded variants sum to exactly 100% and absorbs the rounding delta in the first variant (`app/abtest.py:72-82`); the guardrail fallback only compares the primary conversion rate to `guardrail_threshold` when no separate `guardrail_metric` is configured (`app/abtest.py:230-239`); `app/abtest.py::_variant_hash` uses SHA-256 (`app/abtest.py:20-21`); `app/aggregator.py::_precision_recall_f1` returns a 4-tuple (`precision`, `recall`, `f1`, `accuracy`) matching its type annotation (`app/aggregator.py:50-66`); `app/drift.py` now imports and uses `timedelta` for exact time-window comparisons (`app/drift.py:6,86-88`); `app/model_retrainer.py` now uses `joblib` for dataset and model serialization and stores in-memory artifacts on the repository for tests when `STORAGE_BACKEND=memory` (`app/model_retrainer.py:9,167,218,271,274`).
- **Thread-safety hardening**: all in-memory repository methods now use `threading.RLock()` (`app/repositories.py`); `app/drift.py` protects the global `_reference_distributions` with a module-level `RLock`; `app/model_retrainer.py` no longer assigns directly to `repo._artifacts` and instead uses the public `ModelVersionRepository.add_artifact` method.
- **Default-dev-key rejection**: `app/core/config.py` now raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway.

Items still deferred:
- Real MLflow model registry, feature store, shadow/canary serving, production scheduler, real Kafka broker / end-to-end fan-out, Postgres/S3 persistence, and external alerting.

## 9. P4 — Promotion / GA Launch (Future)

P4 in the ticket is the Promotion milestone (weeks 18–22) followed by GA Launch. The following items are intentionally deferred to P4 and are **not** part of the current P0–P3 delivery:

| Item | P4 Phase | Status | Notes |
|------|----------|--------|-------|
| Shadow + canary deployment with auto-rollback | Promotion | 📝 | Binary `promote_model`/`rollback` in `app/model_retrainer.py:347-383` is implemented; `ModelVersion.status` in `app/domain/models.py:93` reserves `shadow`/`canary` but no ramp or shadow scoring logic |
| Real MLflow model registry integration | Promotion | 📝 | `ModelVersionRepository` in `app/repositories.py:183-237` is an in-memory stub mimicking MLflow fields; real MLflow client/tracking P4 |
| Feature store integration (point-in-time `as_of` reads) | Promotion | 📝 | `features_ref` captured in `app/domain/models.py:19` and `features` used from the prediction in `app/model_retrainer.py:124-127`; no feature-store client |
| Production scheduler for monthly retrains (Airflow/Temporal) | Promotion | 📝 | `scheduled_retrain_due` in `app/service.py:107-115` and `/retrain/{engine}/auto` endpoint ready; no Airflow/Temporal/cron integration |
| External alerting (Slack/email/PagerDuty) | Promotion | 📝 | `EventLogRepository` in `app/repositories.py:311-330` emits retraining/drift/rollback events; no external alerting adapters |
| Admin UI / MLOps dashboards | GA Launch | 📝 | REST endpoints for metrics (`app/api/feedback.py:49-59`), drift (`app/api/feedback.py:63-70`), eval (`app/api/feedback.py:183-186`) exist; no admin UI |
| Model cards per promoted model | GA Launch | 📝 | FR-019; no model-card generation; `ModelVersion.metrics` and `lineage` in `app/model_retrainer.py:278-296` contain data for future cards |
| Right-to-erasure propagation into training datasets | GA Launch | 📝 | FR-017; no erasure endpoint, deletion handler, or re-derivation pipeline; `consent.erasure_requested` not consumed |
| Propensity / explore-exploit weighting | GA Launch | 📝 | FR-016; no propensity scoring, inverse propensity weighting, or exploration sampling in `app/model_retrainer.py` or `app/collector.py` |
| Kafka producer/consumer contract with in-memory fallback | GA Launch | ✅ | `app/messaging/kafka_bus.py` adds canonical/short topic helpers, per-deal/lead/contact partition keys, producer/consumer APIs, and in-process fallback; real broker and end-to-end fan-out still P4 |
| Production persistence (Postgres + S3/DW) | GA Launch | 📝 | `app/repositories.py` is in-memory; `STORAGE_BACKEND=disk` writes `joblib` snapshots in `app/model_retrainer.py:162-168,269-271`; Postgres/S3/DW adapters P4 |
| SOC 2 / fair-lending compliance runbooks | GA Launch | 📝 | Lineage (`app/model_retrainer.py:519-535`) and fairness gate (`app/model_retrainer.py:318-344`) implemented; formal runbooks and model cards P4 |

## 9. Component Coverage (System Architecture View)

| Component | Status | Notes |
|-----------|--------|-------|
| Prediction Logger | ✅ | `app/collector.py::log_prediction` + `PredictionRepository` |
| Outcome / Feedback Collector | ✅ | `app/collector.py` |
| Label Builder | ✅ | `app/label_builder.py` |
| Training-Data Assembler | ✅ | `app/model_retrainer.py::assemble_training_dataset` |
| Drift Monitor | ✅ | `app/drift.py` (PSI per feature); `timedelta` used for exact 30-day window comparisons |
| Performance Monitor | ✅ | `app/aggregator.py:95-147` computes accuracy, precision, recall, F1, AUC, ECE and `match_rate` |
| Retrain Orchestrator | ✅ | `app/service.py:71-132` (`evaluate_drift`, `auto_retrain`, `trigger_retrain`); `app/model_retrainer.py:95-516` assembles datasets, trains seeded `LogisticRegression`, and gates promotion; `joblib` snapshots in `app/model_retrainer.py:162-168,269-271` |
| Eval & Gate Service | ✅ | Accuracy non-regression + fairness gate in `app/model_retrainer.py:248-261,300-315,318-344,352` |
| Deployment Manager | 📝 P4 | Direct `promote_model`/`rollback` in `app/model_retrainer.py:347-383`; `ModelVersion.status` reserves `shadow`/`canary` but no ramp logic |
| Lineage / Registry Adapter | 📝 P4 | `ModelVersionRepository` in `app/repositories.py:183-237` (in-memory); `model_lineage` in `app/model_retrainer.py:519-535`; real MLflow adapter P4 |

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
│   ├── messaging/
│   │   └── kafka_bus.py        # Kafka producer/consumer contract + in-process fallback
│   ├── service.py              # orchestration layer used by API routes
│   └── repositories.py         # in-memory repo implementations
├── tests/
│   ├── __init__.py
│   ├── conftest.py                                  # FastAPI TestClient fixture
│   ├── test_abtest_and_feedback.py                  # 9 tests — A/B test + feedback event emission
│   ├── test_disk_persistence.py                     # 1 test — disk backend snapshot/restore
│   ├── test_feedback_loop.py                        # 25 unit/functional tests covering P0-P3 + subtasks
│   ├── test_kafka_bus.py                            # 16 tests — Kafka producer/consumer contract + in-memory fallback
│   ├── test_retraining_rejections_and_triggers.py   # 8 tests — retrain data validation + trigger paths
│   └── test_security.py                             # 4 tests — default-dev-key + CORS wildcard rejection
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
| `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_PRODUCE` / `KAFKA_CONSUMER_ENABLED` | `localhost:9092` / `false` / `false` | Optional Kafka transport; falls back to in-process log when disabled or unavailable |

## 13. Known Limitations & Simplifications

- **Persistence**: The default implementation is in-memory. `STORAGE_BACKEND=disk` serializes dataset frames and model artifacts locally; swap `repositories.py` for Postgres/S3/DW for production (P4).
- **Statistical testing**: A/B tests use a pragmatic normal-approximation Z-test with a power-analysis minimum sample-size gate; production should use a proper power analysis and stratified t-test/Bootstrap where appropriate.
- **Model training**: `LogisticRegression` is used for all engines for reproducibility. In production each engine will plug in its own estimator.
- **MLflow**: The registry is an in-memory `ModelVersionRepository` that mimics MLflow fields (`run_id`, `params`, `metrics`, `model_uri`, `lineage`). Real MLflow integration is P4.
- **Kafka**: `app/messaging/kafka_bus.py` provides a producer/consumer contract with per-deal/lead/contact partition keys and an in-process fallback; capture still defaults to REST and a real Kafka cluster / end-to-end fan-out is P4.
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
- ✅ Unit + functional tests pass (63 passed, 11 warnings in the agent test suite; `2/2` root gateway tests).

## 15. P4 Next Steps

1. Integrate real MLflow model registry and swap in production persistence (Postgres + S3/DW).
2. Provision a real Kafka/Redpanda cluster and enable end-to-end producer/consumer fan-out (the contract adapter is in place).
3. Implement shadow / canary rollout with gradual traffic ramp and automatic rollback.
4. Add propensity / explore-exploit sampling to avoid feedback-loop bias.
5. Add right-to-erasure propagation for GDPR/CCPA compliance.
6. Build an admin UI / dashboard for A/B test results, drift alerts, and model cards.
7. Wire external notification channels (Slack/email) for retraining lifecycle events.
8. Add production scheduler (Airflow/Temporal) for monthly/scheduled retrain runs.
9. Auto-generate model cards per promoted model.
10. Add SOC 2 / fair-lending runbooks.

## 16. Business Rules (BR-1..BR-6)

| ID | Rule | Status | Notes |
|----|------|--------|-------|
| BR-1 | Every label joins to a prediction via stable `prediction_id`; orphans are quarantined | ✅ | `Prediction.prediction_id` in `app/domain/models.py:15`; `label_builder.py:181-225` quarantines orphans to event log; idempotent ingestion in `app/collector.py:50-60` |
| BR-2 | Training features use point-in-time snapshots; no future leakage | 🚧 | `features_ref` and `scored_at` used in `app/label_builder.py:105-128`; `assemble_training_dataset` uses prediction `features` in `app/model_retrainer.py:95-198`; actual feature-store `as_of` reads P4 |
| BR-3 | No protected-class attribute used as a direct training feature; proxies monitored | ✅ | `protected_group` excluded from `feature_cols` in `app/model_retrainer.py:127,221-222`; fairness gate monitors selection-rate ratio in `app/model_retrainer.py:318-344` |
| BR-4 | Promotion blocked if accuracy regresses or fairness ratio breaches 0.80–1.25 | ✅ | `accuracy_regress` and `fairness["passed"]` in `app/model_retrainer.py:248-261`; `promote_model` enforces `gate == "passed"` in `app/model_retrainer.py:352` |
| BR-5 | Right-to-erasure removes a subject and re-derives affected models | 📝 | No erasure endpoint, deletion handler, or re-derivation pipeline; P4 (FR-017) |
| BR-6 | Auto-retrain only for low-risk refreshes; high-impact models require human sign-off | ✅ | `auto` retrain rejected unless `settings.auto_retrain_risk_class == "low"` in `app/model_retrainer.py:398-399`; manual trigger always available via `POST /api/v1/retrain/{engine}` |

## 17. Non-Functional Requirements (NFR)

| NFR | Target / Constraint | Status | Notes |
|-----|---------------------|--------|-------|
| Prediction log ingestion | ≥ 10,000 predictions/s | 🚧 | In-memory `PredictionRepository` with `RLock` in `app/repositories.py:37-61`; no load/HA testing |
| Label join correctness | Point-in-time accurate (no leakage) | 🚧 | Point-in-time logic uses `features_ref` and `scored_at`; actual feature-store `as_of` reads P4 |
| Outcome match rate | ≥ 95% | 🚧 | `match_rate` computed in `app/aggregator.py:134` and `app/service.py:161-170`; synthetic tests pass but no production guarantee |
| Drift evaluation cadence | Hourly streaming + daily batch | 📝 | No scheduler; `POST /api/v1/drift/evaluate/{engine}` in `app/api/feedback.py:63-65` is on-demand; streaming P4 |
| Retraining reproducibility | Bit-for-bit dataset + seed pinning | 🚧 | Seed, stratified split, and `LogisticRegression` pinned in `app/model_retrainer.py`; `joblib` snapshots and lineage recorded; no artifact hash verification |
| Lineage completeness | 100% (data→features→model→eval→deploy) | 🚧 | `LineageReport` and `ModelVersion.lineage` in `app/model_retrainer.py:519-535` and `278-296`; in-memory only; MLflow P4 |
| Promotion gate | Accuracy non-regression + fairness within 4/5ths | ✅ | `app/model_retrainer.py:248-261,318-344,352` |
| Availability (capture path) | 99.9% (never drop feedback) | 🚧 | REST API with `RLock` in `app/repositories.py`; no HA/clustering or durability guarantees |
| Security | PII pseudonymized; encrypted at rest | 📝 | No PII handling, pseudonymization, or encryption in current code; P4 |
| Compliance | SOC 2, GDPR/CCPA, ECOA fairness | 🚧 | Fairness gate and lineage present; right-to-erasure P4; SOC 2 runbooks P4 |
| Recoverability | RPO ≤ 1 min; pipelines re-runnable | 🚧 | In-memory by default; `STORAGE_BACKEND=disk` writes local `joblib`; Postgres/S3/DW and re-runnable pipelines P4 |
