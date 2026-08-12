# Master Test Matrix

This matrix lists every test module in the monorepo, grouped by agent / gateway.
Test counts come from `pytest --collect-only -q` against the current code.

## Unit and functional test modules

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

## Per-agent totals

| Agent | Modules | Tests | Status |
|-------|---------|-------|--------|
| Gateway | 3 | 16 | `16 passed, 1 warning` |
| Compliance Monitor (CMS) | 23 | 179 | `179 passed, 1 warning` |
| Feedback Loop (FLS) | 10 | 92 | `92 passed, 11 warnings` |
| Follow-up Sequences (FUS) | 14 | 121 | `121 passed, 1 warning` |
| Performance Analytics (PAS) | 11 | 120 | `120 passed, 1 warning` |
| Task Automation (TAS) | 21 | 135 | `135 passed, 1 skipped, 1 warning` |
| **Grand total** | **82** | **663** | |

## SQL-mode smoke tests

The same test modules can run through the SQL repository path using SQLite:

| Agent | Command | Expected |
|-------|---------|----------|
| FUS | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///:memory: DB_AUTO_CREATE=true uv run pytest -q tests/test_api.py tests/test_pipeline.py` | `25 passed` |
| PAS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `120 passed, 1 deselected` |
| FLS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `91 passed, 1 deselected` |
| Alembic | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///./.test_alembic.db uv run alembic upgrade head` | `head` succeeds |

> `test_readiness_default` is deselected in SQL mode because it asserts Postgres is `not_configured`, which is no longer true when `STORAGE_GATEWAY=postgres`.
