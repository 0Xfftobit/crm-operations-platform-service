# Engine Testing Results

Generated: 2026-08-13T00:00:00Z

## Root (gateway + integration)

```bash
cd /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service
python -m pytest tests/ -q
```

```
............                                                             [100%]
16 passed, 1 warning in 1.03s
```

### Coverage

|| Test | Requirement / Area |
||------|-------------------|
|| `test_health_unauthenticated` | Gateway `/health` is public |
|| `test_agents_unauthenticated` | Gateway `/agents` discovery is public and lists all 5 agents |
|| `test_proxy_requires_api_key` | Proxy routes require `X-API-Key` |
|| `test_proxy_unknown_agent_returns_404` | Unknown agent slug → `404` |
|| `test_proxy_unknown_upstream_returns_502` | Unconfigured upstream → `502` |
|| `test_ready_without_upstreams_is_degraded` | `/ready` probes upstream agents and reports `degraded` |
|| `test_default_dev_key_rejected_in_production` | `change-me-dev-key` rejected in non-local `ENV` |
|| `test_gateway_generates_and_returns_request_id` | Gateway generates `X-Request-ID` on every proxy response |
|| `test_gateway_propagates_existing_request_id` | Gateway propagates inbound `X-Request-ID` to upstreams |
|| `test_proxy_forwards_api_key_and_request_id` | Proxy includes `X-API-Key` and `X-Request-ID` in upstream headers |
|| `test_proxy_generates_request_id_when_absent` | Proxy generates `X-Request-ID` when not supplied by caller |
|| `test_proxy_forwards_rls_headers` | Proxy forwards `X-Tenant` / `X-Team` / `X-Rep` RLS headers |
|| `test_ready_reports_ok_when_all_agents_up` | `/ready` returns `ok` when all upstream agents are healthy |
|| `test_ready_degraded_when_one_agent_unhealthy` | `/ready` reports `degraded` and the failing HTTP status |
|| `test_metrics_returns_prometheus_text` | Gateway `/metrics` returns Prometheus exposition text |
|| `test_metrics_increments_on_proxy` | Gateway `http_requests_total` increments after proxy calls |

## Compliance Monitor (CMS)

```bash
cd src/agents/compliance_monitor
uv run pytest tests/ -q
```

```
........................................................................ [ 40%]
........................................................................ [ 80%]
...................................                                      [100%]
179 passed, 1 warning in 8.16s
```

### Requirement coverage

|| Test module | FR / NFR / BR / Acceptance area | Count |
||-------------|----------------------------------|-------|
|| `test_activity_dashboard` | Activity append, explainability, Engine-8 signals, dashboard RAG | 6 |
|| `test_api` | Precheck, fairness, rule workflow, dashboard/verify over HTTP | 5 |
|| `test_audit` | Hash-chain append, tampering, search, export, RBAC | 5 |
|| `test_audit_findings` | Consent cache flush, dedupe, dead-letter, stage disclosures | 7 |
|| `test_disclosures` | ECOA 30-day clock, transitions, overdue alerts, satisfy | 4 |
|| `test_fairness` | 4/5ths selection-rate ratio, pass/fail, disparity cases | 4 |
|| `test_gaps` | Policy overrides, PII blocking, retention/erasure, regulation changes, integrity, PDF export | 11 |
|| `test_health` | Health, readiness, OpenAPI, CORS, settings | 5 |
|| `test_kafka_bus` | Kafka contract: canonical/short, partition key, producer/consumer, dead letter | 11 |
|| `test_kafka_spark_cache` | Kafka producer/consumer, CRM surveillance, batch redlining, Redis cache | 10 |
|| `test_lifecycle_latency` | Latency budget, cold-tier archive, purge, integrity | 7 |
|| `test_metrics` | Prometheus exposition, `http_requests_total`, `http_request_duration_seconds`, Kafka counters | 4 |
|| `test_new_tiers` | Sequence-gap, heartbeat, dead-letter, HTTP poll, tracing, stage requirements | 8 |
|| `test_persistence_sql` | Consent/audit persistence, tamper detection, SQL workflow | 7 |
|| `test_precheck` | Missing consent, opt-out/DNC, quiet hours, CAN-SPAM, marketing alerts | 6 |
|| `test_redis_consent_cache` | `CACHE_GATEWAY=redis` TTL, invalidation, broker-fallback | 5 |
|| `test_requirements` | Acceptance / requirements: precheck, consent, audit, fairness, disclosures, PII, erasure, cases, rules, Kafka, security, request-id, dashboard | 23 |
|| `test_resilience` | `ResilientClient` retry, `CircuitBreaker` open/close, `X-Request-ID` propagation | 4 |
|| `test_rules` | Two-step approval, version increments, emergency override, history | 7 |
|| `test_security_config` | Default API key / wildcard CORS rejection, local/test allowance | 11 |
|| `test_worm_coldstore_local` | Local WORM mirror, cold-store archive, Merkle anchor/purge | 6 |
|| `test_worm_merkle` | Write-once mirror, S3 object lock, Merkle root, inclusion proofs | 7 |
|| `test_coverage_concurrency_batch` | HTTP poll, coverage/sweep, 4/5ths floor, spark batch, concurrency | 10 |

## Follow-up Sequences (FUS)

```bash
cd src/agents/follow_up_sequences
uv run pytest tests/ -q
```

```
........................................................................ [ 62%]
...........................................                              [100%]
121 passed, 1 warning in 2.36s
```

### Requirement coverage

|| Test module | FR / NFR / BR / Acceptance area | Count |
||-------------|----------------------------------|-------|
|| `test_api` | API key auth, enroll/tick, sequence lookup, engagement reply, opt-out, stage triggers, analytics | 7 |
|| `test_bug_audit` | UTC frequency keys, thread-safe `FrequencyStore`/`events`/`notifications`/`providers` | 5 |
|| `test_config_hardening` | Default API key / wildcard CORS rejection in non-local envs | 4 |
|| `test_cross_agent` | CMS precheck client sends `X-API-Key` and `X-Request-ID` | 1 |
|| `test_guardrails` | Quiet hours, timezone fallback, consent/DNC/frequency-cap, branching, duration/branch primitives | 14 |
|| `test_health` | Health, readiness, OpenAPI, CORS, settings | 5 |
|| `test_kafka_bus` | Kafka contract: canonical/short, partition key, producer/consumer, dead letter | 13 |
|| `test_metrics` | Prometheus exposition, request/Kafka metric counters | 4 |
|| `test_p4_gaps` | Suppression webhook, owner reply, A/B SHA-256, CMS precheck client | 8 |
|| `test_pipeline` | Personalization, quiet hours, consent, reply/convert/opt-out, duplicate enrollment, frequency cap, branching, lead-scored trigger, bounce/complaint suppression, owner notification | 18 |
|| `test_provider_resilience` | Provider `deliver()` retry, exponential backoff, rate-limit | 6 |
|| `test_redis_frequency_store` | `RedisFrequencyStore` per-sequence caps with fake Redis | 5 |
|| `test_requirements` | Acceptance / requirements: sequence builder, enrollment, quiet hours, guardrails, Redis frequency, branching, stop conditions, CMS precheck, providers, Kafka, suppression, owner notification, security | 19 |
|| `test_resilience` | `ResilientClient` retry, circuit-breaker fallback, `compliance_client` headers | 3 |

## Performance Analytics (PAS)

```bash
cd src/agents/performance_analytics
uv run pytest tests/ -q
```

```
........................................................................ [ 60%]
................................................                         [100%]
120 passed, 1 warning in 12.43s
```

### Requirement coverage

|| Test module | FR / NFR / BR / Acceptance area | Count |
||-------------|----------------------------------|-------|
|| `test_anomaly_and_reconciliation` | Anomaly detection, threshold alerts, ISO-8601 `P30D`, drift tolerance | 8 |
|| `test_attribution_advanced` | Time-decay, 90-day lookback, Shapley >7 channels, Markov removal effect | 6 |
|| `test_bug_audit` | ISO reconciliation, `_in_period` ISO, time-decay reference, attribution keys, concurrent repository safety | 7 |
|| `test_cross_agent` | FLS fan-out client sends `X-API-Key` and `X-Request-ID` | 1 |
|| `test_feedback_client_and_reports` | Fan-out client headers, scheduled report format override, PDF/CSV export | 11 |
|| `test_kafka_bus` | Kafka contract: canonical/short, partition key, producer/consumer, dead letter | 14 |
|| `test_metrics` | Prometheus exposition, `http_requests_total`, Kafka counters | 4 |
|| `test_performance_analytics` | Core analytics: ingestion, RLS, semantic layer, KPIs, funnel, cohorts, scorecards, attribution, channel effectiveness, reports, A/B uplift | ~26 |
|| `test_requirements` | Acceptance / requirements: ingestion, governed metrics, dashboards, attribution, channels, reconciliation, alerts/reports, export, FLS fan-out, Kafka, security, RLS | 22 |
|| `test_resilience` | `ResilientClient` retry, `CircuitBreaker`, `feedback_client` header propagation | 4 |
|| `test_security_and_rls` | CORS / default key hardening, `parse_role_headers`, `_rls_filter` | 10 |

## Task Automation (TAS)

```bash
cd src/agents/task_automation
uv run pytest tests/ -q
```

```
........................................................................ [ 52%]
.........................................s......................         [100%]
135 passed, 1 skipped, 1 warning in 9.53s
```

### Requirement coverage

|| Test module | FR / NFR / BR / Acceptance area | Count |
||-------------|----------------------------------|-------|
|| `test_acceptance` | Auto task creation, business-hours due dates, queue sorting, overdue alerts, manager view, completion notes, conflict detection, calendar stub | 8 |
|| `test_api` | Health, readiness, API-key auth | 3 |
|| `test_architecture` | Architecture overview, components, observability, dry-run, Kafka fan-out | 5 |
|| `test_business_hours` | Conditions, weekend skipping, overdue | 3 |
|| `test_config_security` | Default API key / wildcard CORS rejection, local/test allowance | 4 |
|| `test_design` | System design, rule design, run API, event dedupe, doc workflow | 5 |
|| `test_doc_request_workflow` | Temporal doc-request happy path, SLA escalation, local chain | 3 |
|| `test_functional` | Pre-approval doc request, blast-radius dry-run, LLM approval, run query, requirements matrix, conflict resolution, protected-class proxy block, task completed event | 8 |
|| `test_hardening` | Event dedupe (Redis), CEL conditions, round-robin, SQL rules, doc SLA, ledger export, contract events, burst load | 9 |
|| `test_integration_guards` | CRM/calendar/feature-store fallbacks, FUS fan-out, DLQ replay, loop/canary guards | 24 |
|| `test_kafka_contract` | Kafka canonical/short topics, partition key, real producer/consumer, degrade | 7 |
|| `test_llm_client` | OpenAI-compatible completion, JSON/classify fallback, fenced JSON, HTTP errors | 16 |
|| `test_metrics` | Prometheus exposition, `http_requests_total`, Kafka counters | 4 |
|| `test_persistence` | Ledger unique constraint, in-memory + DB dedupe, Redis, Postgres constraint (skipped) | 4 |
|| `test_requirements` | Pre-approval acceptance, idempotency, dry-run + canary, ledger hash, LLM write block, JSONLogic, Kafka stub, DLQ replay, requirements matrix | 10 |
|| `test_resilience` | `ResilientClient` / `AsyncResilientClient` retry, circuit-breaker, `followup_client` fallback | 4 |
|| `test_temporal_activities` | Retry policy, activity retries, connector via rule engine | 4 |
|| `test_vision` | Pre-approval compound automation, idempotency, dry-run, rule publish, ledger provenance, event latency | 6 |

## Feedback Loop (FLS)

```bash
cd src/agents/feedback_loop
uv run pytest tests/ -q
```

```
........................................................................ [ 78%]
....................                                                     [100%]
92 passed, 11 warnings in 22.63s
```

### Requirement coverage

|| Test module | FR / NFR / BR / Acceptance area | Count |
||-------------|----------------------------------|-------|
|| `test_abtest_and_feedback` | A/B guardrail, promotion, orphan feedback, match rate, feedback events | 9 |
|| `test_disk_persistence` | `STORAGE_BACKEND=disk` writes/loads dataset and model artifacts | 1 |
|| `test_feedback_loop` | Core capture, metrics, drift, retraining, promotion, rollback, A/B | 25 |
|| `test_health` | `/ready` dependency checks: storage, postgres, redis, kafka | 1 |
|| `test_kafka_bus` | Kafka contract: canonical/short, partition key, producer/consumer, dead letter | 16 |
|| `test_metrics` | Prometheus exposition, `http_requests_total`, Kafka counters | 4 |
|| `test_requirements` | Acceptance / requirements: predictions/outcomes/feedback, label builder, metrics, drift, retraining, promotion, A/B, Kafka, security, request-id, lineage | 20 |
|| `test_retraining_rejections_and_triggers` | Insufficient samples, class imbalance, missing values, auto-retrain, drift | 8 |
|| `test_resilience` | `CircuitBreaker`, `ResilientClient` retry and open-circuit behavior | 4 |
|| `test_security` | CORS wildcard / default API key rejection in non-local envs | 4 |

---

## Cross-Agent and Integration Coverage

|| Flow | Test file | Verifies |
||------|-----------|----------|
|| FUS → CMS precheck | `src/agents/follow_up_sequences/tests/test_cross_agent.py` | `compliance_client` sends `X-API-Key` and `X-Request-ID`, falls back to local guardrails |
|| PAS → FLS outcome fan-out | `src/agents/performance_analytics/tests/test_cross_agent.py` | `feedback_client` forwards `deal.funded`/`deal.lost` with `X-API-Key` and `X-Request-ID` |
|| TAS → FUS stage/lead fan-out | `src/agents/task_automation/tests/test_cross_agent.py` | `followup_client` sends `deal.stage_changed`/`lead.assigned` with `X-API-Key` and `X-Request-ID` |
|| Gateway proxy / auth / request-id | `tests/integration/test_gateway.py` | Health, agents, auth, 404/502, `/ready`, default-key rejection, request-id generation/propagation |
|| Gateway cross-agent header / readiness | `tests/integration/test_gateway_cross_agent.py` | Request-id + API-key + RLS header propagation through proxy, upstream ready ok/degraded |
|| Gateway metrics | `tests/integration/test_metrics.py` | `GET /metrics` returns Prometheus exposition and `http_requests_total` increments after proxy calls |

## Master Test Matrix

This matrix lists every test module in the monorepo, grouped by agent / gateway. A standalone copy also lives in `docs/test_matrix.md`.

| Agent | Test Module | Tests | Focus |
|-------|-------------|-------|-------|
| **Gateway** | `tests/integration/test_gateway` | 9 | Proxy, auth, request ID, dev-key rejection, ready degradation |
| **Gateway** | `tests/integration/test_gateway_cross_agent` | 5 | Cross-agent header propagation, RLS headers, upstream health |
| **Gateway** | `tests/integration/test_metrics` | 2 | Prometheus `/metrics` exposition |
| **CMS** | `tests/test_activity_dashboard` | 6 | Activity ledger, dashboard alerts |
| **CMS** | `tests/test_api` | 6 | REST API round-trips, precheck, fairness, rule workflow |
| **CMS** | `tests/test_audit` | 5 | Audit hash chain, tamper detection |
| **CMS** | `tests/test_audit_findings` | 7 | Consent cache, disclosure clock, missing explainability |
| **CMS** | `tests/test_coverage_concurrency_batch` | 10 | HTTP polling, coverage sweep, dead-letter alerts, concurrency |
| **CMS** | `tests/test_disclosures` | 4 | ECOA/TCPA disclosure lifecycle |
| **CMS** | `tests/test_fairness` | 4 | Fairness metrics and four-fifths rule |
| **CMS** | `tests/test_gaps` | 15 | Gap detection, coverage alerts, sequence tracking |
| **CMS** | `tests/test_health` | 5 | `/health` and `/ready` probes |
| **CMS** | `tests/test_kafka_bus` | 11 | Kafka producer/consumer contract |
| **CMS** | `tests/test_kafka_spark_cache` | 10 | Spark/batch redlining detection, Redis cache fallback |
| **CMS** | `tests/test_lifecycle_latency` | 8 | Retention, purge, cold tier archive, latency budget |
| **CMS** | `tests/test_metrics` | 2 | Prometheus metrics endpoint |
| **CMS** | `tests/test_new_tiers` | 10 | Stage disclosures, deal tracking, tracing, sequence gaps |
| **CMS** | `tests/test_persistence_sql` | 8 | Postgres/SQLite persistence, ledger tamper, full workflow |
| **CMS** | `tests/test_precheck` | 6 | Consent, opt-out, DNC, quiet hours, CAN-SPAM |
| **CMS** | `tests/test_redis_consent_cache` | 5 | Redis consent cache with in-memory fallback |
| **CMS** | `tests/test_requirements` | 23 | Functional and security requirements |
| **CMS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **CMS** | `tests/test_rules` | 7 | Rule versioning, two-step approval, deploy history |
| **CMS** | `tests/test_security_config` | 11 | API key and CORS hardening |
| **CMS** | `tests/test_worm_coldstore_local` | 6 | Local WORM mirror and cold store |
| **CMS** | `tests/test_worm_merkle` | 8 | Merkle tree, inclusion proofs, anchor, S3 WORM |
| **FLS** | `tests/test_abtest_and_feedback` | 9 | A/B test allocation, significance, feedback events |
| **FLS** | `tests/test_disk_persistence` | 1 | Disk snapshot/restore of datasets and models |
| **FLS** | `tests/test_feedback_loop` | 25 | Core prediction, label, drift, retrain, promotion workflows |
| **FLS** | `tests/test_health` | 3 | `/health` and `/ready` probes |
| **FLS** | `tests/test_kafka_bus` | 16 | Kafka producer/consumer contract, canonical topics |
| **FLS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **FLS** | `tests/test_requirements` | 20 | Functional, security, and cross-agent requirements |
| **FLS** | `tests/test_resilience` | 3 | Retry, circuit breaker, mock network failures |
| **FLS** | `tests/test_retraining_rejections_and_triggers` | 8 | Retrain validation, drift/accuracy triggers |
| **FLS** | `tests/test_security` | 4 | Default key rejection, CORS, request ID |
| **FUS** | `tests/test_api` | 7 | REST API: sequence, enrollment, engagement, branching |
| **FUS** | `tests/test_bug_audit` | 5 | Regression tests from bug audit |
| **FUS** | `tests/test_config_hardening` | 4 | Default key, CORS, configuration validation |
| **FUS** | `tests/test_cross_agent` | 1 | CMS precheck fan-out with resilience headers |
| **FUS** | `tests/test_guardrails` | 14 | Consent, DNC, quiet hours, frequency cap, branching |
| **FUS** | `tests/test_health` | 7 | `/health` and `/ready` probes, Postgres check |
| **FUS** | `tests/test_kafka_bus` | 13 | Kafka producer/consumer, cross-agent events |
| **FUS** | `tests/test_metrics` | 2 | Prometheus metrics endpoint |
| **FUS** | `tests/test_p4_gaps` | 19 | P4 feature and gap validation |
| **FUS** | `tests/test_pipeline` | 18 | End-to-end sequence pipeline, scheduler, analytics |
| **FUS** | `tests/test_provider_resilience` | 5 | Provider retry, bounce/complaint, multi-channel |
| **FUS** | `tests/test_redis_frequency_store` | 5 | Redis frequency cap with in-memory fallback |
| **FUS** | `tests/test_requirements` | 19 | Functional and security requirements |
| **FUS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **PAS** | `tests/test_anomaly_and_reconciliation` | 8 | Anomaly detection, reconciliation windows |
| **PAS** | `tests/test_attribution_advanced` | 6 | Multi-touch attribution, Markov/Shapley paths |
| **PAS** | `tests/test_bug_audit` | 7 | Regression tests from bug audit |
| **PAS** | `tests/test_cross_agent` | 2 | FLS outcome fan-out with resilience headers |
| **PAS** | `tests/test_feedback_client_and_reports` | 11 | Feedback client, CSV/PDF report generation |
| **PAS** | `tests/test_health` | 3 | `/health` and `/ready` probes, Postgres check |
| **PAS** | `tests/test_kafka_bus` | 14 | Kafka producer/consumer contract |
| **PAS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **PAS** | `tests/test_performance_analytics` | 34 | KPIs, funnel, scorecards, semantic layer |
| **PAS** | `tests/test_requirements` | 22 | Functional, NFR, and security requirements |
| **PAS** | `tests/test_security_and_rls` | 10 | API key, CORS, RLS headers |
| **TAS** | `tests/test_acceptance` | 8 | Acceptance criteria, end-to-end rule firing |
| **TAS** | `tests/test_api` | 3 | REST API endpoints |
| **TAS** | `tests/test_architecture` | 5 | Component and system architecture requirements |
| **TAS** | `tests/test_bug_audit` | 6 | Regression tests from bug audit |
| **TAS** | `tests/test_business_hours` | 3 | Business hours and holiday rules |
| **TAS** | `tests/test_config_security` | 4 | Default key, CORS, env hardening |
| **TAS** | `tests/test_cross_agent` | 3 | FUS follow-up fan-out with resilience headers |
| **TAS** | `tests/test_design` | 6 | System design, schema, workflow design |
| **TAS** | `tests/test_doc_request_workflow` | 3 | Document request workflow and verification |
| **TAS** | `tests/test_functional` | 8 | Functional requirements |
| **TAS** | `tests/test_hardening` | 10 | Security, idempotency, RLS, blast radius |
| **TAS** | `tests/test_health` | 1 | `/health` and `/ready` probes |
| **TAS** | `tests/test_integration_guards` | 24 | Integration guards, trigger handling, event loop |
| **TAS** | `tests/test_kafka_contract` | 7 | Kafka producer/consumer contract |
| **TAS** | `tests/test_llm_client` | 16 | OpenAI-compatible LLM client and fallback |
| **TAS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **TAS** | `tests/test_persistence` | 4 | Postgres/SQLite persistence, schema init |
| **TAS** | `tests/test_requirements` | 9 | Requirements traceability |
| **TAS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **TAS** | `tests/test_temporal_activities` | 4 | Temporal activity and worker interfaces |
| **TAS** | `tests/test_vision` | 7 | Vision and scope requirements |

### Per-agent totals

| Agent | Modules | Tests | Status |
|-------|---------|-------|--------|
| Gateway | 3 | 16 | `16 passed, 1 warning` |
| Compliance Monitor (CMS) | 23 | 179 | `179 passed, 1 warning` |
| Feedback Loop (FLS) | 10 | 92 | `92 passed, 11 warnings` |
| Follow-up Sequences (FUS) | 14 | 121 | `121 passed, 1 warning` |
| Performance Analytics (PAS) | 11 | 120 | `120 passed, 1 warning` |
| Task Automation (TAS) | 21 | 135 | `135 passed, 1 skipped, 1 warning` |
| **Grand total** | **82** | **663** | |

### SQL-mode smoke tests

| Agent | Command | Expected |
|-------|---------|----------|
| FUS | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///:memory: DB_AUTO_CREATE=true uv run pytest -q tests/test_api.py tests/test_pipeline.py` | `25 passed` |
| PAS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `120 passed, 1 deselected` |
| FLS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `91 passed, 1 deselected` |
| Alembic | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///./.test_alembic.db uv run alembic upgrade head` | `head` succeeds |

---

## Remaining test gaps (P4)

The current suites cover all P0–P3 requirements and the code-level P4 hardening. The following still need real infrastructure to exercise:

- **Live Kafka/Redpanda broker** end-to-end fan-out (TAS → FUS, PAS → FLS, FUS → CMS) across real topics.
- **Live Postgres integration** for all agents: SQL repositories/ORM/migrations are in place and validated with SQLite smoke tests; a real Postgres cluster and load tests remain P4.
- **Live Redis** shared caches for all agents (`CACHE_GATEWAY=redis` and `IDEMPOTENCY_BACKEND=redis`).
- **Production Prometheus scraping / Grafana dashboards** — `/metrics` endpoints are now implemented; live scrape targets and dashboards remain P4.
- **OpenTelemetry** trace export to the OTel collector.
- **15k events/s (TAS)** and **2,000 msg/s (FUS)** load tests.
- **Temporal cluster** durable workflows.

## Lint

All suites are `ruff` clean:

```bash
# root
uv run --extra dev ruff check src config tests
# each agent
uv run ruff check app tests
```

## Type checking

The gateway is mypy-clean under the strict configuration:

```bash
uv run --extra dev mypy src/api config
```

A full-monorepo `mypy` pass is still P4 because each agent has its own import roots and optional stubs.
