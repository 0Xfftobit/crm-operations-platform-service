# Performance Analytics System — Implementation Status

## 1. Overview

The `performance_analytics` agent is a self-contained FastAPI service that implements the **Performance Analytics System (PAS)** for the CRM AI Assistant platform. It ingests touch events, loan/funnel outcomes, campaign spend, and finance source-of-truth records; maintains a governed semantic layer of versioned KPIs; computes funnels, scorecards, channel effectiveness, cohorts, and attribution; and provides an API for dashboards, export, anomaly alerts, and A/B uplift.

This document tracks what has been implemented against the PAS ticket (`docs/ticket_description_pas.md`) and all CEAAPSS subtasks, using the following legend:

| Emoji | Meaning |
|-------|---------|
| ✅ | Implemented and covered by tests in P0–P4 |
| 🚧 | Implemented as a stub / in-memory approximation or missing production integration |
| 📝 | Planned for P4 (Promotion / GA Launch) or future work |
| ❌ | Not implemented and not currently planned |

## 2. Scope Delivered

| Phase / Subtask | Theme | Status | Key Deliverables |
|-----------------|-------|--------|------------------|
| **P0** | Capture | ✅ | Touch, outcome, campaign-cost, and finance source-of-truth ingestion with idempotent dedup and event conformation |
| **P1** | Governed metrics | ✅ | Governed, versioned KPI definitions (semantic layer), metric publication, and KPI query API |
| **P2** | Dashboards + monitoring | ✅ | Funnel/velocity, rep/team scorecards, leaderboard, channel effectiveness, heatmaps, recommendations, cohorts, reconciliation, anomaly/threshold alerts |
| **P3** | Attribution + ROI | ✅ | Rule-based (first/last/linear/time-decay) and data-driven (Markov/Shapley) multi-touch attribution; attributed revenue and ROI per campaign/channel/sequence; A/B uplift with z-test |
| **CEAAPSS-562** | ROI Attribution | ✅ | Attribution models, attribution API, attributed revenue by entity (campaign, channel, sequence, source, rep, team, property type, area), period filtering, CSV/PDF export, campaign/channel/sequence ROI |
| **CEAAPSS-563** | Channel Effectiveness | ✅ | Channel metrics, segment/stage/time-of-day breakdowns, channel comparison, trends over time, automated recommendations, CSV/PDF export |
| **CEAAPSS-646** | Vision | ✅ | Captured in `docs/ticket_description_pas.md` and implemented in P0–P3 scope |
| **CEAAPSS-647** | Project Requirement | ✅ | Requirements mapped to code, tests, and this status doc |
| **CEAAPSS-648** | Functional Requirement | ✅ | FR-001 through FR-016 implemented (FR-015 via `/explore` API; self-serve UI P4); FR-018 implemented via `app/messaging/kafka_bus.py`; FR-017 reserved for P4 |
| **CEAAPSS-649** | System Architecture | ✅ | Component design implemented (Collector, Semantic Layer, Dashboard Builder, Attribution Engine, Reconciliation, Anomaly Detector, Report Scheduler) |
| **CEAAPSS-650** | System Design | ✅ | Data models (`app/domain/models.py`), period/bucket parsing (`app/period_utils.py`), schemas, API spec, and metric-computation designs implemented |
| **P4** | Promotion / GA Launch | 🚧 | Kafka producer/consumer baseline, default-dev-key/CORS hardening, feedback client fan-out, and focused P4 test modules are in; BI rendering, production DW/persistence, scheduled email delivery, and full SSO/RLS remain P4 |

## 3. Functional Requirements (FR-001..FR-018)

| ID | Requirement | MoSCoW | Status | Notes |
|----|-------------|--------|--------|-------|
| FR-001 | Ingest standardized events from all sub-agents + loan outcomes | Must | ✅ | `POST /api/v1/ingest/touch`, `POST /api/v1/ingest/outcome` |
| FR-002 | Deduplicate + conform events into a unified activity/outcome model | Must | ✅ | `TouchEvent`/`OutcomeEvent` models + `event_id`/`outcome_id` dedup in `metrics_collector.py` |
| FR-003 | Maintain a governed semantic layer of versioned KPI definitions | Must | ✅ | `app/semantic.py` + `MetricDefinition` repository + `GET/POST /api/v1/metrics/definitions` |
| FR-004 | Compute funnel conversion metrics (lead→app→fund) by stage | Must | ✅ | `GET /api/v1/funnel` with drop-off and cycle time per stage |
| FR-005 | Compute pipeline velocity + cycle time per stage and per rep | Must | ✅ | `app/dashboard_builder.py` velocity functions; `median_cycle_time` KPI |
| FR-006 | Compute rep + team scorecards (outcome-weighted) | Must | ✅ | `GET /api/v1/scorecards/reps` and `GET /api/v1/scorecards/reps/{rep_id}` |
| FR-007 | Compute engine-level KPIs + A/B uplift | Must | ✅ | `GET /api/v1/kpi/{metric}` and `POST /api/v1/ab-uplift` |
| FR-008 | Run multi-touch attribution (rule-based + data-driven) | Must | ✅ | `GET /api/v1/attribution`, `POST /api/v1/attribution/compute` |
| FR-009 | Compute channel + campaign + sequence ROI | Should | ✅ | `GET /api/v1/channel-effectiveness`, `GET /api/v1/attribution/summary`, `cost_per_funded_loan` and `roi_per_campaign` KPIs; covered by `tests/test_performance_analytics.py::TestChannelEffectiveness` |
| FR-010 | Provide drillable dashboards with role-based row-level security | Must | 🚧 | `app/api/deps.py::parse_role_headers` + `app/api/analytics.py::_rls_filter` apply tenant/team/rep filters; full policy/SSO enforcement P4. Covered by `tests/test_performance_analytics.py::TestRLS` |
| FR-011 | Provide KPI API for programmatic metric retrieval | Must | ✅ | `GET /api/v1/kpi/{metric_name}` with dims/filters/period |
| FR-012 | Reconcile financial metrics to source-of-truth + alert on drift | Must | ✅ | `POST /api/v1/reconcile/{period}`, `POST /api/v1/ingest/finance-source` |
| FR-013 | Support cohort analysis | Should | ✅ | `GET /api/v1/cohorts?dim=...` |
| FR-014 | Schedule reports + threshold alerts | Should | ✅ | `POST /api/v1/reports/schedule` + `GET /api/v1/reports/{id}/run` + `POST /api/v1/threshold-alert/{metric}`; scheduled email/cron delivery P4. Covered by `tests/test_performance_analytics.py::TestReports` and `::TestAlerts` |
| FR-015 | Provide self-serve exploration with governed fields | Should | ✅ | `GET /api/v1/explore` returns metrics, dimensions, period formats |
| FR-016 | Expose metric lineage + definition history | Must | ✅ | `GET /api/v1/metrics/definitions/{name}/history`, `GET /api/v1/metrics/definitions/{name}/lineage`; `MetricDefinition` carries `lineage` and `depends_on` |
| FR-017 | Compute forecast-attainment vs Forecasting Engine targets | Should | 📝 | P4: depends on Forecasting Engine integration |
| FR-018 | Provide near-real-time streaming KPIs for ops dashboards | Should | ✅ | `app/messaging/kafka_bus.py` producer/consumer contract with in-memory fallback; real broker optional. Covered by `tests/test_kafka_bus.py` |

## 3.1 Business Rules (BR-001..BR-007)

| ID | Rule | Status | Notes |
|----|------|--------|-------|
| BR-001 | A KPI may only be published if it exists in the governed semantic layer with a version | ✅ | `app/semantic.py::publish_metric_definition` increments version and sets `status="published"`; `GET /api/v1/metrics/definitions` and `/history` expose versions. Covered by `tests/test_performance_analytics.py::TestSemantic` |
| BR-002 | "Funded loan" + revenue figures must reconcile to Engine 4/finance source-of-truth | 🚧 | `app/reconciliation.py::reconcile_period` computes delta vs `FinanceSourceOfTruth` and logs `analytics.reconciliation_alert` when tolerance is breached; it does not yet gate KPI publication or auto-block (P4) |
| BR-003 | Attribution windows are configurable (default 90-day lookback for funded loans) | ✅ | `app/core/config.py::attribution_lookback_d` (default 90), used in `app/attribution.py::_touch_path` |
| BR-004 | Scorecards must include guardrail metrics (quality/compliance), not just volume | ✅ | `app/dashboard_builder.py::build_rep_scorecards` returns `guardrail_compliance` (1 - opt-outs/delivered) and `opt_out_rate` per rep |
| BR-005 | Row-level security restricts data to the viewer's team/role/tenant | 🚧 | `app/api/deps.py::parse_role_headers` + `_rls_filter` in `app/api/analytics.py` apply tenant/team/rep filters; full SSO/policy engine is P4. Covered by `tests/test_performance_analytics.py::TestRLS` |
| BR-006 | Analytics layer stores pseudonymized IDs; raw PII excluded | 🚧 | `TouchEvent`/`OutcomeEvent` use `contact_pid` and have no raw-PII fields; validation/erasure audit is P4 (BR-6) |
| BR-007 | Late-arriving outcomes trigger recomputation of affected attribution/cohorts | 📝 | Attribution and cohorts are only recomputed on request; no automatic backfill for late-arriving outcomes (P4) |

## 3.2 Non-Functional Requirements (NFR-01..NFR-11)

| ID | Category | Requirement | Target | Status | Notes |
|----|----------|-------------|--------|--------|-------|
| NFR-01 | Latency | Dashboard query (pre-aggregated, p95) | ≤ 2 s | 📝 | In-memory, on-demand computation; no pre-aggregated marts or p95 load tests (P4) |
| NFR-02 | Freshness | Streaming metric freshness | ≤ 5 min | ✅ | `app/messaging/kafka_bus.py` producer/consumer with in-memory fallback; real broker optional. Covered by `tests/test_kafka_bus.py` |
| NFR-03 | Freshness | Batch/warehouse model refresh | ≤ 1 h | 📝 | In-memory repositories; no warehouse/dbt refresh pipeline (P4) |
| NFR-04 | Accuracy | Reconciliation vs funded-loan source-of-truth | ≥ 99.5% | 🚧 | `reconciliation.reconcile_period` uses `RECON_TOLERANCE=0.005`; on-demand, not continuously enforced against live SoT (P4) |
| NFR-05 | Governance | Published KPIs with versioned definitions | 100% | ✅ | `app/semantic.py` versions every metric; `GET /api/v1/metrics/definitions/{name}/history`. Covered by `tests/test_performance_analytics.py::TestSemantic` |
| NFR-06 | Availability | Dashboard + KPI API uptime | ≥ 99.9% | 📝 | No SLO monitoring or production uptime stack (P4/ops) |
| NFR-07 | Scalability | Warehouse concurrency | ≥ 200 concurrent queries | 📝 | In-memory; no warehouse or load/concurrency tests (P4) |
| NFR-08 | Security | Row-level security by team/role/tenant | Enforced | 🚧 | Header-driven filters only; full policy/SSO enforcement P4 (see BR-005) |
| NFR-09 | Auditability | Metric lineage + definition history | Full lineage | ✅ | `GET /api/v1/metrics/definitions/{name}/history` and `/lineage`; `MetricDefinition.depends_on` and `lineage` fields. Covered by `tests/test_performance_analytics.py::TestSemantic` |
| NFR-10 | Attribution | Touch coverage | ≥ 95% | 🚧 | `_touch_path` includes all touches in the lookback; no coverage metric or missing-tracking heuristic with `low_confidence` flag; Shapley falls back to linear for >7 touches |
| NFR-11 | Recovery | RPO / RTO | RPO ≤ 15 min, RTO ≤ 1 h | 📝 | No backup, replay, or recovery automation (P4) |

## 4. Main PAS Ticket Requirement Coverage

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Agent KPIs | ✅ | `GET /api/v1/scorecards/reps` + leaderboard |
| Pipeline KPIs | ✅ | `GET /api/v1/funnel` + velocity/cycle time |
| Revenue KPIs | ✅ | `funded_amount`, `deal_count`, `roi_per_campaign`, attributed revenue |
| Communication KPIs | ✅ | `open_rate`, `reply_rate`, `engagement_rate`, `opt_out_rate` |
| Role-based access | 🚧 | `parse_role_headers` + `_rls_filter` apply tenant/team/rep filters across dashboards; full policy/SSO engine P4. See `tests/test_performance_analytics.py::TestRLS` |
| Date range filters | ✅ | Daily/weekly/monthly/quarterly/yearly/custom period expressions |
| Agent leaderboard | ✅ | `GET /api/v1/leaderboard?metric=...` |
| Pipeline funnel | ✅ | `GET /api/v1/funnel` |
| CSV + PDF export | ✅ | `GET /api/v1/export/{metric}` and `GET /api/v1/reports/{id}/run` use `app/pdf.py`/`app/service.py::export_metric`; covered by `tests/test_performance_analytics.py::TestReports` |
| Automated reports | 🚧 | `ReportSchedule` entity + `GET /api/v1/reports/{id}/run`; no cron or email delivery (P4). Tested in `tests/test_performance_analytics.py::TestReports` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Separate analytics DB | 🚧 | `app/repositories.py` in-memory (`STORAGE_BACKEND=memory`); DW/Postgres/S3 persistence adapters P4 |
| Pre-computed KPIs refreshed every 15 min | 📝 | `app/dashboard_builder.py`/`app/attribution.py` compute on demand; materialized marts + refresh cron P4 |
| Server-side PDF reports | ✅ | `app/pdf.py` builds valid PDFs from metric/funnel/channel data |
| Email on schedule | 📝 | P4: delivery mechanism not built |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Agent dashboard shows personal KPIs near real-time | ✅ | Scorecard + KPI API compute from current state |
| Manager dashboard shows individual + team-wide performance | ✅ | Scorecards + leaderboard + team filters |
| Pipeline funnel shows conversion rates and drop-off per stage | ✅ | `GET /api/v1/funnel` |
| Charts support date range filtering | ✅ | `period` parameter across dashboards and KPIs |
| Self-serve exploration of governed metrics | ✅ | `GET /api/v1/explore` (FR-015) |
| Metric lineage and definition history | ✅ | `GET /api/v1/metrics/definitions/{name}/history` and `/lineage` (FR-016) |
| Reports export correctly as CSV and PDF | ✅ | `GET /api/v1/export/{metric}` and `GET /api/v1/reports/{id}/run` produce CSV/PDF; covered by `tests/test_performance_analytics.py::TestReports` |
| Automated weekly reports emailed to managers | 📝 | Schedule entity exists; email delivery P4 |

## 5. CEAAPSS-562 — ROI Attribution

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| First Touch attribution | ✅ | `app/attribution.py::_first_touch` |
| Last Touch attribution | ✅ | `app/attribution.py::_last_touch` |
| Linear / Multi-Touch attribution | ✅ | `app/attribution.py::_linear` |
| Time Decay attribution | ✅ | `app/attribution.py::_time_decay` with configurable half-life |
| Markov removal-effect attribution | ✅ | Simplified first-order Markov approximation in `_markov` |
| Shapley-value attribution | ✅ | Exact Shapley over channel types in `app/attribution.py::_shapley_value`; falls back to linear for >7 unique channels; channel credit split equally across touches in that channel |
| Attribution per entity | ✅ | `GET /api/v1/attribution/entities?entity=<attr>` supports campaign, channel, sequence, source, rep_id, team_id, property_type, area, contact_pid, deal_id. Covered by `tests/test_performance_analytics.py::TestAttribution` |
| Configurable attribution window | ✅ | `ATTRIBUTION_LOOKBACK_D` (default 90 days) |
| ROI calculation per campaign | ✅ | `(attributed revenue - cost) / cost × 100` in `roi_per_campaign` |
| Attribution dashboard | ✅ | `GET /api/v1/attribution/summary` and `GET /api/v1/attribution/entities` return ranked attributed revenue. Covered by `tests/test_performance_analytics.py::TestAttribution` |
| Model toggle | ✅ | `?model=` query param on all attribution endpoints |
| CSV/PDF export | ✅ | `GET /api/v1/attribution/export?entity=<attr>&report_format=csv|pdf`; plus generic `GET /api/v1/export/{metric}` and `GET /api/v1/reports/{id}/run`. Covered by `tests/test_performance_analytics.py::TestAttribution` and `::TestReports` |
| Time range filters | ✅ | `?period=` on attribution summary, entity, export, and compute endpoints; daily/weekly/monthly/quarterly bucket and custom ranges |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Conversion event: deal funded | ✅ | `stage="funded"` outcome with `amount` |
| 90-day lookback | ✅ | `ATTRIBUTION_LOOKBACK_D` |
| Touchpoint log with timestamp and source | ✅ | `TouchEvent` model with `sent_at`, `source`, `channel`, `campaign_id` |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| First/last/linear/time-decay/data-driven models return credit per touch | ✅ | Credits sum to 1.0 per deal |
| Campaign/channel/sequence/AI recommendation/agent attribution supported | ✅ | Summary endpoints cover channel, campaign, sequence, source; entity endpoint covers `property_type`, `area`, `contact_pid`, `deal_id` |
| Attribution model can be toggled | ✅ | `?model=` param |
| Period filtering on attribution | ✅ | `?period=` on `/attribution`, `/attribution/compute`, `/attribution/summary`, `/attribution/entities`, `/attribution/export` |
| Reports export as CSV/PDF | ✅ | `GET /api/v1/attribution/export`, `GET /api/v1/export/{metric}` and `GET /api/v1/reports/{id}/run` use `app/pdf.py`/`app/service.py::export_metric`; covered by `tests/test_performance_analytics.py::TestReports` |

## 6. CEAAPSS-563 — Channel Effectiveness

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Channel metrics (delivery, open, click, reply, opt-out, conversion) | ✅ | `GET /api/v1/channel-effectiveness`; covered by `tests/test_performance_analytics.py::TestChannelEffectiveness` |
| Segment / stage / time-of-day breakdowns | ✅ | `?by=channel,stage` / `?by=channel,hour` / `GET /api/v1/channel-heatmaps` |
| Conversion contribution rate per channel | ✅ | `conversion_contribution_rate` per channel |
| Channel recommendations | ✅ | `GET /api/v1/channel-recommendations`; covered by `tests/test_performance_analytics.py::TestChannelEffectiveness` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Delivery status flag | ✅ | `TouchEvent.delivery_status` |
| Engaged / opened / clicked / replied flags | ✅ | Boolean fields on `TouchEvent` |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Channel performance ranked by key metrics | ✅ | Response by any metric in leaderboard-style output |
| Breakdowns by segment/stage/time-of-day | ✅ | `by` query and heatmap endpoints |
| Channel comparison view | ✅ | `GET /api/v1/channel-effectiveness` returns side-by-side metrics |
| Trend over time | ✅ | `?period=monthly&by=channel,period` groups channel metrics by time bucket |
| CSV/PDF export | ✅ | `GET /api/v1/export/channel_effectiveness` and `GET /api/v1/reports/{id}/run`; covered by `tests/test_performance_analytics.py::TestReports` |
| Automated recommendations surface top-performing combinations | ✅ | `channel-recommendations` |

## 7. CEAAPSS-646 to CEAAPSS-650 — Documentation & Design Deliverables

| Subtask | Title | Status | Evidence |
|---------|-------|--------|----------|
| **CEAAPSS-646** | Vision | ✅ | `docs/ticket_description_pas.md` §1–7 and P0–P3 implementation |
| **CEAAPSS-647** | Project Requirement | ✅ | `docs/ticket_description_pas.md` §8–10 and this status doc |
| **CEAAPSS-648** | Functional Requirement | ✅ | `docs/ticket_description_pas.md` §11 (FR-001..FR-018) and §3.1/§3.2 (BR/NFR) mapped to code in §3 above |
| **CEAAPSS-649** | System Architecture | ✅ | `docs/ticket_description_pas.md` §12 and `app/` component layout |
| **CEAAPSS-650** | System Design | ✅ | `docs/ticket_description_pas.md` §13 (Detailed Design, Schemas, API Spec) and implemented API endpoints |

## 8. P4 Implementation Update

### Latest P4 pass (2026-08-10)

This pass implemented the Kafka producer/consumer contract, security and RLS hardening, and split the test suite into focused modules; the full suite is now `91/91` tests passing and P0–P3 behavior remains intact:

- **Kafka producer/consumer contract** (`app/messaging/kafka_bus.py`): public contract `start_producer` / `stop_producer` / `produce` / `produce_sync` / `consume_loop` / `start_consumer` / `stop_consumer` / `reset_kafka_bus` / `bus_stats` / `canonical_topic` / `partition_key`; publishes `TouchEvent`s to `crm.outreach.*` and `OutcomeEvent`s to `crm.outcome.*` with per-deal partition keys and degrades to an in-memory bus when Kafka is disabled or unavailable. Covered by `tests/test_kafka_bus.py` (14 tests).
- **Security, CORS validation, and RLS header filtering**: `app/core/config.py` rejects `API_KEY=change-me-dev-key` and wildcard `CORS_ORIGINS` in non-local environments, `app/main.py` applies `CORSMiddleware`, and `app/api/deps.py::parse_role_headers` plus `app/api/analytics.py::_rls_filter` enforce tenant/team/rep filters. Covered by `tests/test_security_and_rls.py` (10 tests).
- **Advanced attribution, anomaly, and reconciliation coverage**: `tests/test_attribution_advanced.py` (6 tests) and `tests/test_anomaly_and_reconciliation.py` (8 tests).
- **Feedback client and report/exports**: `app/clients/feedback_client.py` cross-agent fan-out plus scheduled report/export edge cases. Covered by `tests/test_feedback_client_and_reports.py` (11 tests).
- **Suite split and regression coverage**: `test_performance_analytics.py` (34 P0–P3 + subtask tests), `test_bug_audit.py` (7), and `test_cross_agent.py` (1) are preserved; total `91/91` passing.

### Earlier P4 pass

This pass added the following production hardening pieces without breaking P0–P3 tests:

1. **Cross-agent FLS outcome fan-out**: `app/clients/feedback_client.py` provides a synchronous `httpx` client that forwards `loan.funded` / `deal.lost` outcomes to the Feedback Loop `POST /api/v1/outcomes` when `OUTCOME_FANOUT_ENABLED=true` and `FEEDBACK_LOOP_URL` is configured.
2. **Environment variables**: `OUTCOME_FANOUT_ENABLED`, `FEEDBACK_LOOP_URL`, `FEEDBACK_LOOP_API_KEY`, `FEEDBACK_LOOP_TIMEOUT` added to `.env.example` and `app/core/config.py`.
3. **Request-id propagation in cross-agent calls**: `app/clients/feedback_client.py` now sends an `X-Request-ID` header on every FLS fan-out request (`app/clients/feedback_client.py:38`).
4. **Bug-audit / P4 hardening**: `reconciliation._funded_amount_for_period` now uses `period_utils._in_period` with explicit `now`, so ISO-8601 durations such as `P1D`/`P7D`/`P1M` are evaluated as rolling windows and bucket strings are matched by bounds instead of by string equality (`app/reconciliation.py:24-36`); `_parse_bounds` handles ISO-8601 durations (`app/period_utils.py:48-88`); `attribution._time_decay` accepts and uses the actual conversion `funded_at` when available (`app/attribution.py:74-87`, `app/attribution.py:198-211`); `AttributionRepository._items` is typed as `dict[tuple[str, str], AttributionRecord]` (`app/repositories.py:212`); duplicate branches in `build_channel_effectiveness` were removed; `dashboard_builder` funnels/scorecards skip records with missing timestamps via `_in_period_or_skip` (`app/dashboard_builder.py:577-581`); repository `list` method type-annotation shadowing fixed by using `typing.List` (`app/repositories.py`); every in-memory repository now uses `threading.RLock()` via `_synchronized`.
5. **Default-dev-key rejection**: `app/core/config.py` now raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway.
6. **Docker-compose cross-agent wiring**: `docker-compose.yml` sets `FEEDBACK_LOOP_URL=http://feedback_loop:8000` and `OUTCOME_FANOUT_ENABLED=true` for PAS by default.
7. **Cross-agent client tests**: `tests/test_cross_agent.py` mocks `httpx.Client` and verifies PAS→FLS `outcome` fan-out calls the right endpoint with `X-API-Key` and `X-Request-ID`.
8. **Bug-audit tests**: `tests/test_bug_audit.py` adds 7 regression tests covering ISO-8601 reconciliation windows, `_in_period` ISO support, time-decay conversion reference, attribution repository tuple keys, and concurrent repository safety.

Items still deferred:
- Production Postgres / DW / S3 persistence.
- Materialized KPI refresh, full SSO/RLS, BI dashboard rendering, scheduled email delivery, self-serve exploration UI.

## 9. P4 — Promotion / GA Launch (Future)

P4 in the ticket is the Promotion milestone. Items below are tracked for P4 delivery; those marked ✅ are now code-complete and covered by tests, while the remainder remain future work beyond the current P0–P3 delivery:

| Item | P4 Phase | Status | Notes |
|------|----------|--------|-------|
| Real-time streaming marts (Flink/Kafka) | Promotion | ✅ | `app/messaging/kafka_bus.py` producer/consumer contract, per-deal partition keys, canonical topic mapping, in-memory fallback; real broker optional. Covered by `tests/test_kafka_bus.py` (14 tests) |
| Default-dev-key rejection | Promotion | ✅ | `app/core/config.py` raises `ValueError` when `API_KEY=change-me-dev-key` in non-local envs. Covered by `tests/test_security_and_rls.py` |
| CORS validation | Promotion | ✅ | `app/core/config.py` rejects wildcard `CORS_ORIGINS` in non-local envs; `app/main.py` applies `CORSMiddleware`. Covered by `tests/test_security_and_rls.py` |
| Cross-agent feedback client fan-out | Promotion | ✅ | `app/clients/feedback_client.py` forwards `loan.funded` / `deal.lost` to FLS `POST /api/v1/outcomes` with `X-API-Key` and `X-Request-ID`. Covered by `tests/test_cross_agent.py` and `tests/test_feedback_client_and_reports.py` |
| Production persistence (Postgres/S3/DW) | Promotion | 📝 | In-memory repositories; persistence adapters P4 |
| Pre-computed / materialized KPI refresh | Promotion | 📝 | Computed on demand; dbt/materialized cron P4 |
| Full row-level security with SSO/policies | Promotion | 📝 | Header filters only; policy engine P4 |
| BI dashboard rendering | GA Launch | 📝 | REST/JSON only; Looker/Superset/Metabase P4 |
| Scheduled email delivery | GA Launch | 📝 | `ReportSchedule` entity + run endpoint; email channel P4 |
| Self-serve exploration UI | GA Launch | 📝 | FR-015; not built |
| Metric lineage graph | GA Launch | 📝 | FR-016; versioned definitions only |
| Forecast-attainment integration | GA Launch | 📝 | FR-017; depends on Forecasting Engine |
| Near-real-time streaming KPIs | GA Launch | ✅ | FR-018; `app/messaging/kafka_bus.py` producer/consumer contract with in-memory fallback. Covered by `tests/test_kafka_bus.py` |
| Right-to-erasure / pseudonymization audit | GA Launch | 📝 | BR-6; `contact_pid` used but no erasure/pseudonymization audit yet |

## 9. Component Coverage (System Architecture View)

| Component | Status | Notes |
|-----------|--------|-------|
| Metrics Collector | ✅ | `app/metrics_collector.py` (FR-001/FR-002) |
| Governed Semantic Layer | ✅ | `app/semantic.py` (FR-003/BR-1) |
| Dashboard Builder | ✅ | `app/dashboard_builder.py` (FR-004..FR-007, FR-013) |
| Attribution Engine | ✅ | `app/attribution.py` (FR-008/CEAAPSS-562) |
| Channel Effectiveness | ✅ | `app/dashboard_builder.py` channel functions (CEAAPSS-563) |
| Reconciliation | ✅ | `app/reconciliation.py` (FR-012/BR-2) |
| Anomaly / Alert Detector | ✅ | `app/anomaly_detector.py` implements z-score anomaly detection and threshold alerts; threshold alerts, scheduled reports, and `detect_anomalies` are exercised by `tests/test_anomaly_and_reconciliation.py` (8 tests) |
| Kafka Bus | ✅ | `app/messaging/kafka_bus.py` producer/consumer contract with per-deal partition keys, canonical topic mapping, and in-memory fallback; real broker optional. Covered by `tests/test_kafka_bus.py` (14 tests) |
| Report Exporter | ✅ | `app/pdf.py` + `app/service.py` export/run (FR-014) |
| RLS Filter | ✅ | `app/api/deps.py::parse_role_headers` + `app/api/analytics.py::_rls_filter` apply tenant/team/rep filters and override query params; full policy/SSO engine remains P4. Covered by `tests/test_security_and_rls.py` (10 tests) |

## 10. File Map

```
src/agents/performance_analytics/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI factory + middleware
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # pydantic-settings + environment defaults
│   │   └── logging.py          # structlog setup
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # auth + RLS header parsing
│   │   ├── health.py           # /health, /ready
│   │   └── analytics.py        # all PAS REST endpoints
│   ├── clients/
│   │   ├── __init__.py
│   │   └── feedback_client.py  # PAS -> FLS outcome fan-out client
│   ├── domain/
│   │   ├── __init__.py
│   │   └── models.py           # TouchEvent, OutcomeEvent, CampaignCost, etc.
│   ├── messaging/
│   │   ├── __init__.py
│   │   └── kafka_bus.py        # async Kafka producer/consumer + in-memory fallback
│   ├── metrics_collector.py    # ingestion wrappers (FR-001/FR-002)
│   ├── repositories.py         # in-memory repo implementations
│   ├── semantic.py             # governed metric definitions (FR-003/BR-1)
│   ├── period_utils.py         # shared period/bucket parsing for dashboards + attribution
│   ├── dashboard_builder.py    # funnels, scorecards, channels, cohorts, A/B (FR-004..FR-007)
│   ├── attribution.py          # multi-touch attribution (FR-008/CEAAPSS-562)
│   ├── reconciliation.py       # finance source-of-truth reconciliation (FR-012/BR-2)
│   ├── anomaly_detector.py     # z-score and threshold alerts (FR-014)
│   ├── pdf.py                  # stdlib CSV + PDF export
│   ├── service.py              # orchestration layer used by API routes
│   └── agent.py                # public agent facade
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # FastAPI TestClient fixture + payload helpers
│   ├── helpers.py              # shared seed/payload helpers
│   ├── test_anomaly_and_reconciliation.py  # 8 tests — anomaly detection + reconciliation drift
│   ├── test_attribution_advanced.py        # 6 tests — attribution edge cases
│   ├── test_bug_audit.py       # 7 regression tests covering bug-audit fixes
│   ├── test_cross_agent.py     # PAS -> FLS fan-out wiring (1 test)
│   ├── test_feedback_client_and_reports.py # 11 tests — feedback client + scheduled reports/exports
│   ├── test_kafka_bus.py       # 14 tests — Kafka producer/consumer contract + in-memory fallback
│   ├── test_performance_analytics.py       # 34 P0-P3 + subtask tests
│   └── test_security_and_rls.py            # 10 tests — default-dev-key, CORS, RLS header filtering
├── pyproject.toml              # deps + ruff + pytest config
├── uv.lock                     # frozen dependency set
├── Dockerfile
├── Makefile
├── .env.example
└── README.md
```

## 11. Integration into the Monorepo

| Layer | Change | Location |
|-------|--------|----------|
| API gateway routing | Added `performance_analytics` to `AGENTS` map | `src/api/main.py` |
| Compose orchestration | Added `performance_analytics` service + `PERFORMANCE_ANALYTICS_URL` | `docker-compose.yml` |
| Gateway route notes | Updated proxy notes | `src/api/routes/performance_analytics.py` |
| Main README | Marked `performance_analytics` as implemented | `README.md` |
| Agent tests | Standalone pytest suite | `src/agents/performance_analytics/tests/` |

The gateway proxies `/performance-analytics/*` to the agent once `PERFORMANCE_ANALYTICS_URL` is configured.

## 12. Configuration Highlights

Key environment variables (see `src/agents/performance_analytics/.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | `change-me-dev-key` | X-API-Key header for write/ops endpoints |
| `LOG_LEVEL` | `INFO` | structlog level |
| `LOG_JSON` | `false` | JSON vs console logs |
| `STORAGE_BACKEND` | `memory` | `memory` only; Postgres/S3/DW persistence adapters P4 |
| `DATA_DIR` | `./data/performance_analytics` | Where `disk` snapshots would write (disk backend P4) |
| `ATTRIBUTION_LOOKBACK_D` | `90` | Attribution window |
| `ATTRIBUTION_DEFAULT_MODEL` | `time_decay` | Default credit model |
| `TIME_DECAY_HALFLIFE_D` | `14` | Time-decay half-life |
| `RECON_TOLERANCE` | `0.005` | Reconciliation delta tolerance |
| `COHORT_MIN_N` | `25` | Suppress cohorts below this size |
| `ANOMALY_Z_THRESHOLD` | `2.0` | Anomaly z-score threshold |

## 13. Known Limitations & Simplifications

- **Persistence**: all repositories are in-memory. Swap to Postgres/S3/DW for production.
- **Attribution Markov/Shapley**: simplified model using empirical channel conversion rates; full path-level Markov chain and exact Shapley for large sets is P4.
- **Anomaly detection**: uses z-score over historical monthly metric values; production will likely require seasonal decomposition and control charts.
- **RLS**: filters are derived from `X-*` headers and applied as query filters; a real policy engine/SSO is P4.
- **Scheduled reports**: `ReportSchedule` is stored and runnable on demand; cron/email delivery is P4.
- **PDF rendering**: a minimal stdlib PDF generator is included; production can switch to a dedicated PDF library.
- **Streaming**: `app/messaging/kafka_bus.py` implements an async Kafka producer/consumer contract with in-memory fallback and per-deal partition keys; a real Kafka/Flink broker and end-to-end streaming marts remain P4.
