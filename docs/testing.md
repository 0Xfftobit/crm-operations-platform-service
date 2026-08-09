# Testing Guide

This document describes how to run tests for all five agent engines, the shared API gateway, and the repository-wide integration suite.

## 1. Quick Start

### All engines (from each agent directory)

```bash
for d in src/agents/*/; do
  (cd "$d" && uv run pytest -q)
done
```

### Root gateway / integration suite

```bash
cd /path/to/crm-operations-platform-service
python -m pytest tests -q
```

### Lint and format

```bash
# Root gateway + shared config
ruff check src config tests

# Each engine
for d in src/agents/*/; do
  (cd "$d" && ruff check app tests)
done
```

## 2. Per-Agent Test Commands

### Compliance Monitor

```bash
cd src/agents/compliance_monitor
uv run pytest -q
```

Expected: `109 passed`

### Feedback Loop

```bash
cd src/agents/feedback_loop
uv run pytest -q
```

Expected: `25 passed`

### Follow-up Sequences

```bash
cd src/agents/follow_up_sequences
uv run pytest -q
```

Expected: `50 passed`

### Performance Analytics

```bash
cd src/agents/performance_analytics
uv run pytest -q
```

Expected: `42 passed`

### Task Automation

```bash
cd src/agents/task_automation
uv run pytest -q
```

Expected: `78 passed, 1 skipped`

## 3. Gateway / Integration Tests

Location: `tests/integration/test_gateway.py`

```bash
cd /path/to/crm-operations-platform-service
python -m pytest tests/integration/test_gateway.py -q
```

Coverage:

- `GET /health` (unauthenticated)
- `GET /agents` discovery (unauthenticated)
- `GET /ready` with no upstreams configured returns `degraded`
- Proxy path `/{slug}/{path}` requires `X-API-Key`
- Unknown agent slug returns `404`
- Unconfigured upstream returns `502`
- Default dev key rejected in non-local `ENV`

## 4. P4 Feature Tests

### Gateway

The new P4 tests live in `tests/integration/test_gateway.py`. They validate auth, header forwarding, per-agent timeouts, and unknown/upstream error handling.

### Follow-up Sequences

- Redis-backed frequency counters: `app/frequency.py` selects Redis when `CACHE_GATEWAY=redis`; falls back to in-memory.
- CMS precheck client: `app/clients/compliance_client.py`. Add `FUS_PRECHECK_ENABLED=true` and `COMPLIANCE_MONITOR_URL` to exercise.

### Performance Analytics

- FLS outcome fan-out: `app/clients/feedback_client.py`. Add `OUTCOME_FANOUT_ENABLED=true` and `FEEDBACK_LOOP_URL` to exercise.

### Task Automation

- FUS fan-out: `app/clients/followup_client.py`. Add `FUS_FANOUT_ENABLED=true` and `FOLLOW_UP_SEQUENCES_URL` to exercise.

## 5. Manual Smoke Tests

### Start the local stack

```bash
docker compose --profile infra up --build
```

This starts Postgres, Redis, Redpanda (Kafka), Prometheus, Grafana, the OpenTelemetry Collector, the five agents, and the gateway.

### Gateway health / ready

```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready -H "X-API-Key: change-me-dev-key"
```

### Proxy to an engine

```bash
curl http://localhost:8080/compliance-monitor/api/v1/health \
  -H "X-API-Key: change-me-dev-key"

# Create a sequence through the gateway
curl http://localhost:8080/follow-up-sequences/api/v1/sequences \
  -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{
    "sequence_id": "sq-1",
    "name": "Welcome",
    "tenant_id": "default",
    "version": 1,
    "steps": [
      {"id": "s1", "channel": "email", "delay": "PT0M", "template": "Hi {{name}}"}
    ]
  }'
```

## 6. CI Notes

A CI job should:

1. Install dependencies (`uv sync` or `pip install -r requirements.txt` in each engine and the root).
2. Run `ruff check` in the root (`ruff check src config tests`) and in each engine.
3. Run `pytest` in the root (`python -m pytest tests`).
4. Run `uv run pytest -q` in `src/agents/{compliance_monitor,feedback_loop,follow_up_sequences,performance_analytics,task_automation}`.

The `feedback_loop` engine uses `scikit-learn`; the first import is relatively slow, so allow extra time for cold-start test runs.
