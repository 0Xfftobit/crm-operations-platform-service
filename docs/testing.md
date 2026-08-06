# Testing Guide

This document describes how to run tests for the `feedback_loop` agent and the repository-wide integration suite.

## 1. Feedback Loop Agent Tests

The agent has its own isolated test suite under `src/agents/feedback_loop/tests/`. It uses `pytest` and the FastAPI `TestClient`.

### Run all agent tests

```bash
cd src/agents/feedback_loop
PYTHONPATH=. pytest tests
```

### Run a specific test class

```bash
PYTHONPATH=. pytest tests/test_feedback_loop.py::TestCapture -q
```

### Run with coverage

```bash
PYTHONPATH=. pytest tests --cov=app --cov-report=term-missing
```

### Use the Makefile

```bash
cd src/agents/feedback_loop
make test      # uv run pytest -q
make lint      # uv run ruff check --fix app tests
make fmt       # uv run ruff format app tests
```

## 2. What the Agent Tests Cover

`tests/test_feedback_loop.py` is organized into five classes that map to the FLS phases:

| Class | Scope | Count |
|-------|-------|-------|
| `TestCapture` | Prediction logging, duplicate detection, outcomes, overrides, explicit feedback, auth, quarantine | 6 |
| `TestLabels` | Windowed matching, out-of-window labels, batch label build | 3 |
| `TestMetrics` | Precision, recall, F1, accuracy computation | 1 |
| `TestDrift` | PSI computation and sustained drift trigger | 2 |
| `TestRetraining` | Dataset assembly, model training, promotion, lineage, fairness gate | 3 |
| `TestABTest` | Experiment creation, consistent allocation, power-analysis gating, guardrail early stop | 3 |
| `TestSubtasks` | Performance/scheduled retrain triggers, data validation, retraining event logs | 3 |

Total: **22 tests** covering P0–P3 of the FLS plus CEAAPSS-559 and CEAAPSS-560.

## 3. Root Integration Tests

The top-level suite validates the API gateway and agent wiring.

```bash
cd /path/to/crm-operations-platform-service
pytest tests
```

Current integration tests:

- `tests/integration/test_gateway.py` — health endpoint and `/agents` discovery listing.

## 4. Linting & Formatting

The agent uses `ruff` for both linting and formatting. The root `.venv` includes `ruff` so it can be invoked from the repository root.

### Lint

```bash
cd src/agents/feedback_loop
ruff check app tests
```

### Auto-fix safe issues

```bash
ruff check app tests --fix
```

### Format

```bash
ruff format app tests
```

## 5. Manual API Smoke Tests

Start the agent:

```bash
cd src/agents/feedback_loop
uv run uvicorn app.main:app --host 127.0.0.1 --port 8009 --reload
```

```bash
curl -H "X-API-Key: dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8009/api/v1/predictions \
  -d '{
    "engine_name": "lead_scoring",
    "model_version": "1.0.0",
    "entity_id": "lead-123",
    "features_ref": "fs://snap/2026-06-15T18:00Z/lead-123",
    "features": {"f1": 1.0, "f2": 0.5},
    "output": {"probability": 0.8}
  }'
```

OpenAPI docs are available at `http://127.0.0.1:8009/docs`.

## 6. Expected Results

| Command | Expected Result |
|---------|----------------|
| `pytest tests` in agent dir | `22 passed` |
| `pytest tests` in repo root | `2 passed` |
| `ruff check app tests` | no errors |
| `ruff format app tests` | formats in place |

## 7. Performance Analytics Agent Tests

The agent has its own isolated test suite under `src/agents/performance_analytics/tests/`. It uses `pytest` and the FastAPI `TestClient`.

### Run all agent tests

```bash
cd src/agents/performance_analytics
uv run pytest tests
```

### Run with verbose output

```bash
cd src/agents/performance_analytics
uv run pytest tests -v
```

Expected (latest run):

```
23 passed, 1 warning in ~3s
```

### Run a specific test class

```bash
cd src/agents/performance_analytics
uv run pytest tests/test_performance_analytics.py::TestIngestion -q
```

### Use the Makefile

```bash
cd src/agents/performance_analytics
make test      # uv run pytest -q
make lint      # uv run ruff check --fix app tests
make fmt       # uv run ruff format app tests
```

### What the Agent Tests Cover

`tests/test_performance_analytics.py` is organized into classes that map to the PAS phases:

| Class | Scope | Count |
|-------|-------|-------|
| `TestIngestion` | Touch/outcome/cost/finance ingestion, duplicate detection, API-key auth | 4 |
| `TestRLS` | Row-level security tenant/team/rep filters | 3 |
| `TestSemantic` | Governed metric definitions, versioned metric publication, superseding, history, lineage, explore, lifecycle | 4 |
| `TestKpi` | Governed metric query, conversion rates, channel/rep dimensions, period filters, numerator/denominator | 4 |
| `TestFunnel` | Pipeline funnel with stage counts and drop-off | 1 |
| `TestCohorts` | Cohort conversion analysis by acquisition month | 1 |
| `TestScorecards` | Rep scorecards with `as_of_date` and leaderboard ranking | 2 |
| `TestAttribution` | Multi-touch attribution credits (sum to one), ranked summary, period filtering, attribution by entity, CSV/PDF export | 5 |
| `TestChannelEffectiveness` | Channel metrics, conversion contribution, recommendations | 2 |
| `TestReconciliation` | Finance source-of-truth reconciliation pass/fail | 2 |
| `TestAlerts` | Threshold-based conversion alert | 1 |
| `TestReports` | Scheduled reports, metric/funnel/channel-effectiveness export, CSV and PDF | 4 |
| `TestABUplift` | Treatment vs control conversion lift with p-value | 1 |

Total: **34 tests** covering P0–P3 of the PAS plus CEAAPSS-562 (ROI Attribution) and CEAAPSS-563 (Channel Effectiveness).

### Manual API Smoke Tests

Start the agent:

```bash
cd src/agents/performance_analytics
uv run uvicorn app.main:app --host 127.0.0.1 --port 8007 --reload
```

Ingest a touch and an outcome:

```bash
curl -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8007/api/v1/ingest/touch \
  -d '{
    "touch_id": "t-1",
    "contact_pid": "c-1",
    "deal_id": "d-1",
    "rep_id": "r-1",
    "team_id": "team-a",
    "channel": "email",
    "sent_at": "2026-06-01T12:00:00+00:00"
  }'

curl -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8007/api/v1/ingest/outcome \
  -d '{
    "outcome_id": "o-1",
    "contact_pid": "c-1",
    "deal_id": "d-1",
    "stage": "funded",
    "amount": 300000,
    "event_type": "loan.funded",
    "occurred_at": "2026-06-15T12:00:00+00:00"
  }'
```

Query a KPI:

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/kpi/deal_count"
```

OpenAPI docs are available at `http://127.0.0.1:8007/docs`.

### Expected Results

| Command | Expected Result |
|---------|----------------|
| `uv run pytest tests` in agent dir | `34 passed, 1 warning` |
| `uv run pytest tests -v` in agent dir | `34 passed` with per-test names |
| `PYTHONPATH=. pytest tests` from repo root | `2 passed` |
| `uv run ruff check app tests` | no errors |
| `uv run ruff format app tests` | formats in place |

## 8. CI Notes

A minimal CI job should:

1. Install dependencies (`uv sync` or `pip install -r requirements.txt`).
2. Run `ruff check app tests` in `src/agents/feedback_loop`.
3. Run `pytest tests` in `src/agents/feedback_loop`.
4. Run `ruff check app tests` in `src/agents/performance_analytics`.
5. Run `pytest tests` in `src/agents/performance_analytics`.
6. Run `pytest tests` from the repo root.

Because the `feedback_loop` engine uses `scikit-learn`, the first import is relatively slow; allow extra time for cold-start test runs.
