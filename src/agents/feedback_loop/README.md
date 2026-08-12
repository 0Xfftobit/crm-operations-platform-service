# Feedback Loop System (FLS)

FastAPI service for the **MLOps backbone** of the CRM AI Assistant (Engine 2). It closes the learning loop for every model-driven sub-agent by capturing predictions, outcomes, overrides, and explicit feedback; building point-in-time labels; monitoring drift and performance; and orchestrating reproducible retraining with fairness-gated promotion.

## Table of Contents

1. [Overview](#overview)
2. [Implemented Scope](#implemented-scope)
3. [Architecture](#architecture)
4. [Data Flow](#data-flow)
5. [API Reference](#api-reference)
6. [Configuration](#configuration)
7. [Running Locally](#running-locally)
8. [Testing](#testing)
9. [Docker](#docker)
10. [Subtask Coverage](#subtask-coverage)
11. [Key Modules](#key-modules)
12. [Roadmap](#roadmap)

## Overview

The Feedback Loop System answers: *"Were our AI predictions and recommendations actually right, where were they wrong, and how do we feed that truth back to make every Engine 2 model better — safely and reproducibly?"*

It is a self-contained FastAPI agent that can be deployed independently or alongside the API gateway. Downstream model services (Lead Scoring, Next-Best-Action, Churn Prediction, etc.) send prediction and outcome events to the FLS, which joins them into training labels, watches for drift, and triggers retraining when models degrade.

## Implemented Scope

P0–P3 of the FLS ticket, plus the CEAAPSS-559 (Model Retraining) and CEAAPSS-560 (A/B Testing Framework) subtasks:

- **P0 Capture**: prediction logging, outcome/override/explicit-feedback ingestion, idempotent capture, event schemas.
- **P1 Labels**: label store, immediate + windowed outcome joins, point-in-time feature references, training-data assembly.
- **P2 Monitoring**: precision/recall/F1/AUC/ECE per engine, population stability index (PSI) per feature, drift alerts, retrain triggers.
- **P3 Retraining**: reproducible training pipelines (seeded), champion/challenger evaluation, accuracy + fairness promotion gates, model registry + lineage, rollback, A/B test framework.
- **CEAAPSS-559**: manual/scheduled/performance/drift retrain triggers, dataset validation (row count, class balance, missing values), retraining history, event-log lifecycle notifications.
- **CEAAPSS-560**: consistent-hash variant allocation, power-analysis minimum sample size, configurable p-value threshold, guardrail metric + early stopping, one-click winner promotion.

## Latest P4 Hardening

- Bug-audit fixes: A/B split sums to 100%, guardrail fallback only fires when no guardrail metric is configured, SHA-256 variant hashing, `timedelta` drift window, `joblib` serialization, `_precision_recall_f1` 4-tuple return type.
- All in-memory repositories plus the drift reference store now use `threading.RLock()`.
- Resilience: `ResilientClient` (exponential backoff, `X-Request-ID` propagation, `X-API-Key` forwarding) and `CircuitBreaker` (open after 3 consecutive failures, 30s timeout, half-open probe) on cross-agent calls.
- Observability: Prometheus-compatible `/api/v1/metrics` endpoint, request-ID middleware, and in-memory metrics fallback.
- PostgreSQL persistence: `STORAGE_GATEWAY=postgres` + `DATABASE_URL` with SQLAlchemy ORM, SQL repositories, and Alembic migrations; default remains `memory`; SQLite works for local SQL-mode smoke tests.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Model Agents   │────▶│   FLS API       │────▶│  Label Store    │
│ (lead_scoring,  │     │  (FastAPI)      │     │  (in-memory or │
│  nba, churn…)   │     │                 │     │   disk/Postgres)│
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
   ┌─────────────┐        ┌─────────────┐        ┌─────────────────┐
   │  Collector  │        │ Label Builder│        │  Drift Monitor  │
   └─────────────┘        └─────────────┘        └─────────────────┘
          │                        │                        │
          │                 ┌──────┴──────┐                │
          │                 ▼             ▼                 ▼
          │        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
          │        │   Metrics    │ │  Training    │ │ Retrain      │
          │        │  (precision, │ │   Dataset    │ │ Orchestrator │
          │        │  recall, AUC) │ │   Assembler  │ │              │
          │        └──────────────┘ └──────┬───────┘ └──────┬───────┘
          │                                │                │
          │                                ▼                ▼
          │                       ┌─────────────────┐ ┌─────────────┐
          │                       │ Model Retrainer │ │ A/B Test      │
          │                       │ (train / eval)  │ │ Framework     │
          │                       └────────┬────────┘ └──────┬──────┘
          │                                │                │
          └────────────────────────────────┴────────────────┘
                                           │
                                           ▼
                                    ┌─────────────┐
                                    │ Model       │
                                    │ Registry +  │
                                    │ Lineage     │
                                    └─────────────┘
```

## Data Flow

1. A model sub-agent emits a prediction via `POST /api/v1/predictions`.
2. The prediction is stored with a stable `prediction_id` and a `features_ref` pointing to the point-in-time feature snapshot.
3. Outcome events (`deal.funded`, `deal.lost`, `email.replied`) or explicit/override feedback arrive via `POST /api/v1/outcomes` or `POST /api/v1/feedback`.
4. The `LabelBuilder` joins outcomes to predictions either immediately (by `prediction_id`) or within configurable observation windows (7d / 30d / 60d / 90d).
5. `Aggregator` continuously computes per-engine metrics and `Drift` computes PSI per feature.
6. When accuracy drops or drift persists, `auto_retrain` triggers a reproducible pipeline: assemble dataset → train challenger → evaluate on holdout → run fairness gate → promote or rollback.
7. `abtest.py` runs controlled experiments with consistent variant allocation and statistical significance testing.

## API Reference

All routes are under `/api/v1`. The API gateway proxies `/<agent-slug>/*` to this service.

### Capture

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/predictions` | Log a prediction event |
| `POST` | `/outcomes` | Record a downstream outcome |
| `POST` | `/feedback` | Record an override or explicit feedback |

### Labels & Export

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/labels` | List labels (optionally filter by `engine_name` or `prediction_id`) |
| `POST` | `/labels/build` | Batch (re)build all labels |
| `GET` | `/export/{engine_name}` | Export the labeled dataset for training/audit |

### Monitoring

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/metrics/{engine_name}` | Per-engine precision, recall, F1, accuracy, AUC, ECE |
| `GET` | `/metrics` | All-engine metrics |
| `GET` | `/drift` | Drift records per engine |

### Retraining & Registry

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/retrain/{engine_name}` | Trigger a retraining run (`?trigger=manual|scheduled|performance|drift`) |
| `POST` | `/retrain/{engine_name}/auto` | Auto-trigger on performance or scheduled triggers |
| `GET` | `/retrain/{engine_name}/triggers` | Check whether performance/scheduled triggers are active |
| `POST` | `/models/{model_id}/promote` | Promote a gated challenger to production |
| `POST` | `/models/rollback/{engine_name}` | Roll back to the previous production model |
| `GET` | `/models` | List model versions |
| `GET` | `/models/{model_id}/lineage` | Full lineage report |
| `GET` | `/datasets` | List training datasets |
| `GET` | `/retraining-log` | Retraining run history |

### A/B Testing

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/experiments` | Create an experiment |
| `GET` | `/experiments/{experiment_id}` | Get experiment configuration/history |
| `POST` | `/experiments/{experiment_id}/assign` | Assign a variant to an entity |
| `POST` | `/experiments/{experiment_id}/record` | Record a result (supports `guardrail_value`) |
| `POST` | `/experiments/{experiment_id}/evaluate` | Evaluate per-variant metrics and significance |
| `POST` | `/experiments/{experiment_id}/promote` | Promote the winning variant |

### Health & Stats

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/health` | Liveness |
| `GET` | `/ready` | Readiness (storage, postgres, redis, kafka) |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/stats` | Capture counts and label match rate |

Write/ops endpoints require the `X-API-Key` header configured by `API_KEY`.

## Configuration

Copy `.env.example` to `.env` and adjust as needed. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_NAME` | `crequity-feedback-loop` | Service name |
| `ENV` | `local` | Environment label |
| `LOG_LEVEL` | `INFO` | structlog level |
| `API_KEY` | `change-me-dev-key` | Key for `X-API-Key` header on write endpoints |
| `API_PREFIX` | `/api/v1` | Route prefix |
| `STORAGE_GATEWAY` | `memory` | `memory` (default) or `postgres` (requires `DATABASE_URL`) |
| `STORAGE_BACKEND` | `memory` | `memory` (default) or `disk` for dataset/model snapshots |
| `DATABASE_URL` | (empty) | PostgreSQL+psycopg URL when `STORAGE_GATEWAY=postgres` |
| `DB_AUTO_CREATE` | `false` | Create SQL tables on startup (dev only; prod uses `alembic upgrade head`) |
| `DATA_DIR` | `./data/feedback_loop` | Local path for `disk` snapshots |
| `MODEL_SNAPSHOT_DIR` | `./data/feedback_loop/models` | Local path for model artifacts |
| `WINDOW_RESPONSE_DAYS` | `7` | `email.replied`-style window |
| `WINDOW_STAGE_DAYS` | `30` | Stage progression window |
| `WINDOW_CONVERSION_DAYS` | `90` | Conversion window |
| `FUNDED_LABEL_WINDOW_DAYS` | `60` | `deal.funded` window |
| `PSI_THRESHOLD` | `0.2` | Feature drift alert threshold |
| `DRIFT_PERSISTENCE_EVALS` | `2` | Consecutive breaches before retrain trigger |
| `MIN_TRAINING_SAMPLES` | `50` | Minimum rows before training |
| `HOLDOUT_RATIO` | `0.2` | Stratified holdout fraction |
| `ACCURACY_THRESHOLD` | `0.70` | Performance retrain trigger floor |
| `ACCURACY_EPSILON` | `0.01` | AUC regression tolerance vs champion |
| `FAIRNESS_RATIO_MIN` / `MAX` | `0.80` / `1.25` | 4/5ths selection-rate gate |
| `AUTO_RETRAIN_ENABLED` | `true` | Allow drift-triggered auto retrain |
| `AUTO_RETRAIN_RISK_CLASS` | `low` | Risk class eligible for auto retrain |

## Running Locally

```bash
# Using uv (recommended)
cd src/agents/feedback_loop
uv sync
uv run pytest -q
make run                 # API on http://127.0.0.1:8009 (reload)
```

```bash
# Using the existing root .venv
PYTHONPATH=. python -m pytest tests -q
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8009 --reload
```

Open **http://127.0.0.1:8009/docs** for the interactive OpenAPI UI.

Health: `/api/v1/health`, readiness: `/api/v1/ready`, Prometheus metrics: `/api/v1/metrics`.

## Testing

The agent has an isolated pytest suite under `tests/`:

```bash
cd src/agents/feedback_loop
uv run pytest -q                       # 92 passed, 11 warnings
PYTHONPATH=. pytest tests -q           # 92 passed, 11 warnings
```

It also supports linting and formatting via `ruff`:

```bash
make lint      # ruff check app tests
make fmt       # ruff format app tests
make test      # uv run pytest -q
```

The root monorepo suite validates gateway wiring and cross-agent header propagation:

```bash
cd ../..
uv run --extra dev pytest -q tests         # 16 passed, 1 warning
```

See `docs/test_matrix.md` for the full 82-module test matrix.

## Docker

```bash
docker build -t crequity-feedback-loop src/agents/feedback_loop
docker run -p 8009:8000 --env-file src/agents/feedback_loop/.env crequity-feedback-loop
```

The Dockerfile uses `uv` for deterministic dependency resolution and runs `uvicorn` on port `8000`.

## Subtask Coverage

### CEAAPSS-559 — Model Retraining

- ✅ Manual, performance, drift, and scheduled retrain triggers (`/retrain/{engine}` and `/retrain/{engine}/auto`)
- ✅ Training dataset assembly with row-level lineage
- ✅ Dataset validation: minimum rows, single-class check, class imbalance, missing-value ratio
- ✅ Seeded `LogisticRegression` challenger training
- ✅ Hold-out evaluation vs champion
- ✅ Accuracy + fairness (4/5ths selection-rate) promotion gate
- ✅ Rollback to previous production model
- ✅ Retraining run log (`/retraining-log`)
- 🚧 Event-log lifecycle notifications (`retrain.started`, `retrain.completed`, etc.); external alerting adapter not wired
- 🚧 Production scheduler not configured (Airflow/Temporal cron should call `/retrain/{engine}/auto`)
- 🚧 Feature engineering is identity; production will use the platform feature store
- 🚧 Model registry is in-memory; MLflow adapter is future work

### CEAAPSS-560 — A/B Testing Framework

- ✅ Experiment creation with name, hypothesis, variants, traffic split, audience segment, success metric, and minimum duration
- ✅ Consistent hashing variant allocation (`POST /experiments/{id}/assign`)
- ✅ Generic `experiment_type` field for model/message/sequence/offer tests
- ✅ Two-proportion z-test significance with configurable `p_value_threshold`
- ✅ Power-analysis minimum sample-size gating
- ✅ Guardrail metric + threshold with early stopping
- ✅ Winner promotion with optional `force` flag
- ✅ Experiment history log
- 🚧 No admin UI dashboard (metrics are returned via `/experiments/{id}/evaluate`)

## Key Modules

- `app/collector.py` — ingestion for predictions, outcomes, overrides, explicit feedback
- `app/label_builder.py` — point-in-time label joins and windowed attribution
- `app/aggregator.py` — precision, recall, F1, AUC, calibration ECE, match rate
- `app/drift.py` — PSI computation and sustained drift alerts
- `app/model_retrainer.py` — dataset assembly, retraining, evaluation, fairness gate, registry
- `app/abtest.py` — consistent-hash variant allocation, power analysis, significance testing
- `app/service.py` — orchestration layer used by API routes
- `app/api/feedback.py` — REST routes
- `app/repositories.py` — in-memory repository implementations with SQLAlchemy/SQLite/Postgres dispatch

## Roadmap

1. Validate live PostgreSQL integration in staging/production (Postgres SQL persistence is code-complete; SQLite smoke tests pass).
2. Move model/dataset artifacts to S3/DW-backed storage for multi-node deployments.
3. Add Kafka consumer adapter for event-driven capture (producer/consumer contract tests pass; brokered fan-out is P4).
4. Integrate real MLflow model registry.
5. Add shadow / canary rollout with gradual traffic ramp and automatic rollback.
6. Add propensity / explore-exploit sampling to avoid feedback-loop bias.
7. Add right-to-erasure propagation for GDPR/CCPA compliance.
8. Build an admin UI / dashboard for A/B test results, drift alerts, and model cards.
9. Wire external notification channels (Slack/email) for retraining lifecycle events.
