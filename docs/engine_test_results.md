# Engine Testing Results — All CRM AI Agents

**Run date:** 2026-08-07  
**Tested by:** Devin (per Charles' request)  
**Scope:** Run the documented test suites for all five engines (FLS, PAS, CMS, FUS, TAS) and realistic synthetic-data smoke tests against the implemented Feedback Loop System (FLS) and Performance Analytics System (PAS) engines.

> **Note on data:** No production/real data file was present in the repository or workspace, so the smoke tests were executed with a realistic synthetic CRM dataset (100 contacts, 4 channels, 2 teams, 5 reps, funded/lost outcomes, campaign costs, and finance SoT records). The unit/functional test suites use their own fixture data.

---

## 1. Unit / Functional Test Suite Results

### 1.1 Feedback Loop System (`src/agents/feedback_loop`)

```bash
cd src/agents/feedback_loop
uv run pytest tests -v
```

**Result:** `25 passed, 1 warning in 7.64s`

| Class | Scope | Count |
|-------|-------|-------|
| `TestCapture` | Prediction logging, duplicate detection, outcomes, explicit feedback, overrides, quarantine, auth | 6 |
| `TestLabels` | Windowed matching, out-of-window labels, batch label build | 3 |
| `TestMetrics` | Precision, recall, F1, accuracy computation | 1 |
| `TestDrift` | PSI computation and drift evaluation | 2 |
| `TestRetraining` | Dataset assembly, model training, promotion, lineage, fairness gate | 3 |
| `TestABTest` | Experiment creation, consistent allocation, power analysis, split rounding, guardrail fallback, exact drift window, guardrail early stop | 6 |
| `TestSubtasks` | Performance retrain triggers, data validation, retraining event logs | 3 |

### 1.2 Performance Analytics System (`src/agents/performance_analytics`)

```bash
cd src/agents/performance_analytics
uv run pytest tests -v
```

**Result:** `42 passed, 1 warning in 4.78s`

| Class | Scope | Count |
|-------|-------|-------|
| `TestIngestion` | Touch/outcome ingestion, duplicate detection, auth | 4 |
| `TestRLS` | Team/rep/tenant row-level security filters | 3 |
| `TestSemantic` | Governed metric definitions, versioning, history, lineage, explore | 4 |
| `TestKpi` | KPI queries, conversion rates, channel/rep dimensions, period filters | 4 |
| `TestFunnel` | Pipeline funnel with stage counts and drop-off | 1 |
| `TestCohorts` | Cohort conversion by acquisition month | 1 |
| `TestScorecards` | Rep scorecards and leaderboard | 2 |
| `TestAttribution` | Multi-touch attribution (6 models), ranked summary, entity attribution, export | 5 |
| `TestChannelEffectiveness` | Channel metrics and recommendations | 2 |
| `TestReconciliation` | Finance source-of-truth pass/fail | 2 |
| `TestAlerts` | Threshold alert | 1 |
| `TestReports` | Scheduled reports, CSV/PDF export | 4 |
| `TestABUplift` | Treatment vs control conversion lift with p-value | 1 |
| `test_bug_audit` | ISO reconciliation, `_in_period` ISO, time-decay reference, attribution keys, concurrent repository safety | 7 |

### 1.3 Root Integration Tests

```bash
cd /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service
PYTHONPATH=. uv run pytest tests -v
```

**Result:** `2 passed, 2 warnings in 0.84s`

The `PYTHONPATH=.` export was required because `tests/integration/test_gateway.py` imports `from src.api.main import app`.

---

## 2. Real-Data Smoke Test Results

Two standalone scripts exercised the engines end-to-end with realistic CRM data:

- `/tmp/fls_real_data_test.py` — Feedback Loop System
- `/tmp/pas_real_data_test.py` — Performance Analytics System

Raw JSON outputs:

- `/tmp/fls_real_data_results.json`
- `/tmp/pas_real_data_results.json`

### 2.1 Feedback Loop System

**Dataset:** 80 reference predictions + outcomes for `lead_scoring`, 40 intentionally drifted predictions, 200 A/B-test samples.

| Capability | Result |
|------------|--------|
| Prediction capture | 120 predictions ingested (80 reference + 40 shifted) |
| Outcome → label matching | 80 labels created, match rate 66.67% of all predictions (100% of the reference set) |
| Engine metrics | `accuracy=0.9375`, `precision=0.8857`, `recall=0.9688`, `f1=0.9254`, `auc=0.9818`, `support=80` |
| Drift monitoring | Feature `f1` PSI = **4.2997** (large distribution shift detected); `f2` and `f3` PSI minimal. After a small logic fix in `app/drift.py`, the second consecutive evaluation triggers a sustained retrain signal for `f1`. |
| Retraining pipeline | Manual retrain **promoted** a new model `lead_scoring@v1`; eval metrics `accuracy=0.875`, `auc=0.9667`, `fairness_passed=true` |
| Model lineage | Full lineage report returned: dataset id, feature versions (`f1`, `f2`, `f3`), eval metrics, promoted_at, gate status |
| A/B testing | 200-sample experiment; challenger (31.6% conversion) significantly outperformed control (3.3%); **challenger promoted** |
| Labeled dataset export | 80 labeled rows available for training/audit |

#### Key FLS Observations

- Capture, labeling, metrics, retraining, and A/B promotion all functioned as expected.
- Drift PSI calculation correctly identifies the shifted feature (`f1` PSI 4.3).

**Post-run fix applied:** The sustained retrain trigger in `app/drift.py` was not escalating because `DriftRecord.alert` was only set to `true` when a prior `alert=true` record already existed, which was impossible. I changed the logic so `alert` now reflects a threshold breach on the current evaluation, while `should_trigger_retrain` still uses `drift_persistence_evals` consecutive alerts to declare sustained drift. After the fix:

- First drift evaluation: `f1` PSI 4.3 → `alert=true`, `retrain_triggered=false`.
- Second drift evaluation: `f1` PSI 4.3 → `alert=true`, `retrain_triggered=true`, `retrain_features=["f1"]`.

The FLS unit suite still passes (`25 passed`).

### 2.2 Performance Analytics System

**Dataset:** 100 contacts, 300 touches across 4 channels, 100 lead + 97 app + 39 funded + 61 lost outcomes, 4 campaign costs, 1 finance SoT record.

| Capability | Result |
|------------|--------|
| Ingestion | 300 touches, 300 outcomes, 4 campaign costs, 1 finance record ingested |
| **KPIs** | `deal_count=39`, `app_to_fund_conversion=0.4021` (97 apps → 39 funded), `funded_amount` by channel, `conversion_rate` by rep |
| **Funnel** | 100 lead → 100 app → 0 approved → 39 funded; median lead→app cycle = 2.96 days |
| **Rep scorecards** | 5 reps scored by funded amount, conversion, response rate, guardrail compliance; median cycle ~12–14 days |
| **Leaderboard** | Top rep: `rep-3` ($5.74M funded, 52% conversion) |
| **Channel effectiveness** | All 4 channels reported: email leads by volume, call and WhatsApp higher conversion contribution, SMS best reply rate |
| **Channel recommendations** | `whatsapp` at `app` stage surfaced as +48% above average |
| **Cohorts** | July 2026 cohort: 100 leads, 39 funded, 39% conversion |
| **Attribution** | Time-decay model credits summed to 1.0; ranked summary: email $8.07M, SMS $3.22M, WhatsApp $2.70M, call $2.17M |
| **Reconciliation** | **PASSED** — mart funded amount $16,150,000 equals finance SoT $16,150,000 (delta = 0, tolerance 0.005) |
| **Threshold alert** | `app_to_fund_conversion` correctly flagged as below the 0.99 threshold for the July 2026 bucket (value 0.4021) |
| **A/B uplift** | Treatment group 30 vs control 30; treatment funded rate 53.3% vs control 30%, lift +23.3 pp, p=0.0668 (not significant at α=0.05 with this split) |
| **Reports / export** | Funnel CSV and `deal_count` CSV exported successfully (`%PDF` not tested in this run) |

#### Key PAS Observations

- All core P0–P3 capabilities (ingestion, governed KPIs, funnel, scorecards, attribution, reconciliation, alerts, A/B uplift, CSV export) produced coherent, non-empty results with realistic data.
- The `approved` funnel stage is 0 because the synthetic scenario used only `lead` → `app` → `funded`/`lost`. This is expected behavior, not a defect.
- Attribution credits summed to ~1.0 and channel revenue attribution is internally consistent.
- Reconciliation passed with zero delta, demonstrating the finance SoT integration works.

---

## 3. Commands Reference

```bash
# Feedback Loop agent tests
cd src/agents/feedback_loop
uv run pytest tests -v

# Performance Analytics agent tests
cd src/agents/performance_analytics
uv run pytest tests -v

# Root integration tests
PYTHONPATH=. uv run pytest tests -v

# Real-data smoke tests (scripts live in /tmp and are not committed)
cd src/agents/feedback_loop && uv run python /tmp/fls_real_data_test.py
cd src/agents/performance_analytics && uv run python /tmp/pas_real_data_test.py
```

---

## 4. Summary

| Engine | Unit Tests | Real-Data Smoke Test | Status |
|--------|------------|----------------------|--------|
| Feedback Loop System | 25/25 passed | Capture, metrics, drift, retrain, lineage, A/B promotion all operational | ✅ Ready for feedback loop |
| Performance Analytics System | 42/42 passed | KPIs, funnel, scorecards, attribution, reconciliation, alerts, CSV export all operational, bug-audit ISO/time-decay/locking | ✅ Ready for feedback loop |

Both engines satisfy the ticket requirements implemented in P0–P3. The FLS drift-to-retrain escalation bug was fixed in `src/agents/feedback_loop/app/drift.py` and verified with the unit suite and the real-data smoke test.

---

## 5. New Agents — Unit / Functional Test Results

### 5.1 Compliance Monitor System (`src/agents/compliance_monitor`)

```bash
cd src/agents/compliance_monitor
uv run pytest tests -v
```

**Result:** `106 passed, 1 warning in 6.30s`

| Class / Module | Scope | Count |
|----------------|-------|-------|
| `test_activity_dashboard` | Activity append, explainability, Engine-8 signals, dashboard RAG | 6 |
| `test_api` | Precheck, fairness, rule workflow, dashboard/verify over HTTP | 5 |
| `test_audit` | Hash-chain append, tampering, search, export, RBAC | 5 |
| `test_audit_findings` | Consent cache flush, dedupe, dead-letter, stage disclosures | 7 |
| `test_disclosures` | ECOA 30-day clock, transitions, overdue alerts, satisfy | 4 |
| `test_fairness` | 4/5ths selection-rate ratio, pass/fail, disparity cases | 4 |
| `test_gaps` | Policy overrides, PII blocking, retention/erasure, regulation changes, integrity, PDF export | 11 |
| `test_health` | Health, readiness, OpenAPI, CORS, settings | 5 |
| `test_kafka_spark_cache` | Kafka producer/consumer, poison messages, batch disparity, cache | 8 |
| `test_lifecycle_latency` | Latency budget, cold-tier archive, purge, integrity | 7 |
| `test_new_tiers` | Sequence-gap, heartbeat, dead-letter, HTTP poll, tracing, stage requirements | 8 |
| `test_persistence_sql` | Consent/audit persistence, tamper detection, SQL workflow | 7 |
| `test_precheck` | Missing consent, opt-out/DNC, quiet hours, CAN-SPAM, marketing alerts | 6 |
| `test_rules` | Two-step approval, version increments, emergency override, history | 7 |
| `test_worm_merkle` | Write-once mirror, S3 object lock, Merkle root, inclusion proofs | 7 |

**Fix applied:** `test_new_tiers.py::test_tracing_enabled_instruments_app` failed because the OTel packages were not in the default test environment. Added `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, and `opentelemetry-instrumentation-fastapi` to the `dev` dependency group in `src/agents/compliance_monitor/pyproject.toml`, then re-ran the suite. Result: `106 passed`.

### 5.2 Follow-up Sequences Engine (`src/agents/follow_up_sequences`)

```bash
cd src/agents/follow_up_sequences
uv run pytest tests -v
```

**Result:** `50 passed, 1 warning in 0.81s`

| Class / Module | Scope | Count |
|----------------|-------|-------|
| `test_api` | API key auth, enroll/tick, sequence lookup, engagement reply, opt-out, stage triggers, analytics | 7 |
| `test_guardrails` | Quiet hours, timezone fallback, consent/DNC/frequency-cap, branching, duration/branch primitives | 14 |
| `test_health` | Health, readiness, OpenAPI, CORS, settings | 5 |
|| `test_cross_agent` | CMS precheck client sends `X-API-Key` and `X-Request-ID` | 1 |
|| `test_bug_audit` | UTC frequency keys, thread-safe `FrequencyStore`/`events`/`notifications`/`providers` | 5 |
| `test_pipeline` | Personalization, quiet hours, consent, reply/convert/opt-out, duplicate enrollment, frequency cap, branching, lead-scored trigger, bounce/complaint suppression, owner notification | 18 |

**Fix applied:** `test_pipeline.py::test_branch_no_open_takes_path_b_after_window` failed because `_ledger` in `src/agents/follow_up_sequences/app/service.py` stamped `sent_at` with `_now()` (wall-clock time) instead of the deterministic `now` passed through the pipeline. This broke elapsed-time branch evaluation when the test used a frozen test clock. Updated `_ledger` to accept and use an optional `now` parameter and wired it through all `_process_send` call sites. Result: `50 passed`.

### 5.3 Task Automation Engine (`src/agents/task_automation`)

```bash
cd src/agents/task_automation
uv run pytest tests -v
```

**Result:** `78 passed, 1 skipped, 1 warning in 5.93s`

| Class / Module | Scope | Count |
|----------------|-------|-------|
| `test_acceptance` | Auto task creation, business-hours due dates, queue sorting, overdue alerts, manager view, completion notes, conflict detection, calendar stub | 8 |
| `test_api` | Health, readiness, API-key auth | 3 |
| `test_architecture` | Architecture overview, components, observability, dry-run, Kafka fan-out | 5 |
| `test_business_hours` | Conditions, weekend skipping, overdue | 3 |
| `test_design` | System design, rule design, run API, event dedupe, doc workflow | 5 |
| `test_doc_request_workflow` | Temporal doc-request happy path, SLA escalation, local chain | 3 |
| `test_functional` | Pre-approval doc request, blast-radius dry-run, LLM approval, run query, requirements matrix, conflict resolution, protected-class proxy block, task completed event | 8 |
| `test_hardening` | Event dedupe (Redis), CEL conditions, round-robin, SQL rules, doc SLA, ledger export, contract events, burst load | 9 |
| `test_persistence` | Ledger unique constraint, in-memory + DB dedupe, Redis, Postgres constraint (skipped) | 4 |
| `test_requirements` | Pre-approval acceptance, idempotency, dry-run + canary, ledger hash, LLM write block, JSONLogic, Kafka stub, DLQ replay, requirements matrix | 10 |
| `test_temporal_activities` | Retry policy, activity retries, connector via rule engine | 4 |
| `test_vision` | Pre-approval compound automation, idempotency, dry-run, rule publish, ledger provenance, event latency | 6 |

**No fixes required.** One persistence test is skipped because it requires a live Postgres database (`test_postgres_ledger_unique_constraint`); all other tests pass.

### 5.4 Root Integration / Gateway Tests

```bash
cd /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service
PYTHONPATH=. uv run pytest tests -v
```

**Result:** `2 passed, 2 warnings in 1.07s`

The gateway `/health` and `/agents` discovery endpoints return all five agents, confirming the inter-agent HTTP routing layer is wired.

---

### 5.5 Realistic API Smoke Tests

In addition to the unit/functional suites, each new agent was exercised end-to-end through its public REST API with realistic synthetic data. The scripts live in `/tmp` and are not committed; they replicate real LO/operations usage and exercise personalization, branching, guardrails, audit, and workflow rule execution.

| Engine | Script | Key operations verified | Result |
|--------|--------|------------------------|--------|
| Compliance Monitor | `/tmp/cms_smoke_test.py` | Consent, precheck (allow/block), activity ingest, explainability, fairness/disparity, ECOA disclosure clock, rule change workflow, audit search/verify, dashboard, alerts, cases | ✅ All endpoints returned expected status/cases/audit records |
| Follow-up Sequences | `/tmp/fus_smoke_test.py` | Contact/sequence creation, enrollment, scheduler ticks, email send, open engagement → branch, personalized rendering, ledger, analytics, opt-out stop | ✅ Sequence sent first email, branched to follow-up email on open, rendered personalization, stopped on opt-out |
| Task Automation | `/tmp/tas_smoke_test.py` | Rule creation, dry-run simulate, event ingest, task auto-creation, agent queue, task completion, blast-radius dry-run | ✅ Rule fired for `deal.stage_changed` → `underwriting`, created a `call` task assigned to `lo:amy`, completed with notes, blast-radius showed 1 would-fire event |

---

## 6. Summary

| Engine | Unit Tests | Smoke / Real-Data Test | Status |
|--------|------------|------------------------|--------|
| Feedback Loop System | 25/25 passed | Capture, metrics, drift, retrain, lineage, A/B promotion operational | ✅ Ready |
| Performance Analytics System | 42/42 passed | KPIs, funnel, scorecards, attribution, reconciliation, alerts, CSV export operational, bug-audit ISO/time-decay/locking | ✅ Ready |
| Compliance Monitor System | 106/106 passed | Unit suite + API smoke: precheck, audit, fairness, disclosures, PII, retention, rule workflow | ✅ Ready |
| Follow-up Sequences Engine | 50/50 passed | Unit suite + API smoke: enrollment, guardrails, branching, reply/convert/opt-out, frequency cap, bug-audit timezone/locking, analytics | ✅ Ready |
| Task Automation Engine | 78 passed, 1 skipped | Unit suite + API smoke: rule engine, stage-driven task, business hours, dashboard, blast radius, bug-audit trigger-id/locking | ✅ Ready |

All five engines now have passing unit/functional test suites and realistic API smoke tests. Two small bugs were identified and fixed during testing: FLS drift escalation (previous thread) and the FUS `sent_at` branch-timing bug, plus the CMS missing OTel test dependency. No external LLM or data-provider API keys are required for the current in-memory/stub implementations.
