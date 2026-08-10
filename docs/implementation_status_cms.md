# Compliance Monitor System — Implementation Status

## 1. Overview

The `compliance_monitor` agent is a self-contained FastAPI service that implements the **Compliance Monitor System (CMS)** for the CRM AI Assistant platform. It acts as Engine 2's trust and defensibility layer — screening every outbound communication, AI decision, record change, and consent update for TCPA / CAN-SPAM / ECOA / fair-lending / GDPR-CCPA adherence; keeping an immutable, hash-chained, tamper-evident audit trail; raising tiered alerts and compliance cases; and publishing signals to the platform's Engine 8 (Compliance & Risk Monitor).

This document tracks what has been implemented against the CMS ticket (`docs/ticket_description_cms.md`) and all CEAAPSS subtasks, using the following legend:

| Emoji | Meaning |
|-------|---------|
| ✅ | Implemented and covered by tests in P0–P3 |
| 🚧 | Implemented as a stub / in-memory approximation or missing production integration |
| 📝 | Planned for P4 (Promotion / GA Launch) or future work |
| ❌ | Not implemented and not currently planned |

## 1.5. Latest P4 pass (2026-08-10)

The latest P4 hardening pass completed the following CMS pieces and brings the test suite to **152 passing tests** with `ruff` clean:

- **Kafka producer/consumer contract** (`app/kafka_bus.py`) with canonical/short topic names, **per-deal partition keys** (`deal_id` → `lead_id` → `contact_id` → `entity_id` → `id`), and **in-memory fallback** when the broker is unavailable or Kafka is disabled. New broker-free contract tests in `tests/test_kafka_bus.py` (11 tests) cover canonical topics, partition-key precedence, producer/consumer graceful degradation, CRM-surveillance dispatch, and bus stats/reset.
- **Kafka + Spark + cache integration** covered by `tests/test_kafka_spark_cache.py` (10 tests): activity ingestion, CRM stage-changed surveillance, batch redlining aggregation, and Redis-backed cache fallback.
- **Security config hardening** (`tests/test_security_config.py` — 11 tests): default dev API key rejection and wildcard CORS origin rejection in non-local environments (`app/core/config.py:130-144`, `app/main.py:109-115`), plus `CORS_ORIGINS` JSON/comma parsing.
- **Redis-backed consent cache** (`tests/test_redis_consent_cache.py` — 5 tests): `app/cache.py` works with `CACHE_GATEWAY=redis` and falls back to in-memory.
- **Local WORM / cold-store** (`tests/test_worm_coldstore_local.py` — 6 tests): `app/worm.py` and `app/coldstore.py` local backends, archive/restore, and lifecycle purge.
- **Coverage, concurrency, and batch fairness** (`tests/test_coverage_concurrency_batch.py` — 10 tests): coverage-gap sweeps, concurrent precheck, and Spark/Flink batch redlining aggregation.
- **Ruff linting** now passes on the CMS package (`uv run ruff check .`).

### P4 Hardening Checklist

| Item | Status | Notes |
|------|--------|-------|
| Kafka baseline (producer/consumer contract, per-deal partition keys, in-memory fallback) | ✅ | `app/kafka_bus.py`; 11 broker-free contract tests in `tests/test_kafka_bus.py`; 10 integration tests in `tests/test_kafka_spark_cache.py` |
| Default-dev-key rejection | ✅ | `app/core/config.py:130-136` / `tests/test_security_config.py` |
| CORS validation (wildcard rejection + origin allow list) | ✅ | `app/core/config.py:138-144` + `app/main.py:109-115` / `tests/test_security_config.py` |
| Request-id propagation | ✅ | FUS→CMS precheck client sends `X-Request-ID` (`src/agents/follow_up_sequences/app/clients/compliance_client.py:52`); gateway generates/propagates `X-Request-ID` on all proxy responses (`src/api/main.py:130-179`) |

Latest CMS test run: `uv run pytest tests/` — **152 passed**, no failures, `ruff` clean.

## 2. Scope Delivered

| Phase / Subtask | Theme | Status | Key Deliverables |
|-----------------|-------|--------|------------------|
| **P0** | Capture | ✅ | `ActivityEvent` schema, `POST /activity`, Kafka consumer (`app/kafka_bus.py`), HTTP-poll upstream (`app/http_source.py`), immutable `AuditEntry` ledger (`app/audit.py`) |
| **P1** | Rules | ✅ | Synchronous fail-closed pre-send hook (`POST /precheck`, `app/precheck.py`), consent management (`PUT /consent`, `app/cache.py`), disclosure clocks (`app/disclosures.py`) |
| **P2** | Fairness | ✅ | 4/5ths fair-lending disparity (`POST /fairness/evaluate`, `app/fairness.py`), explainability records (`POST /explainability`), PII exposure scan (`app/pii.py`), data retention + right-to-erasure (`app/privacy.py`) |
| **P3** | Cases | ✅ | Tiered alerts (`app/casework.py`), auto-case on critical, case lifecycle API (`GET/PATCH /cases`), RAG dashboard (`GET /dashboard`), Engine-8 signals (`app/events.py`) |
| **CEAAPSS-565** | Regulatory Updates | 🚧 | Rule versioning + two-step approval + staging→production + emergency override (`app/rules.py`, `app/api/rules.py`) fully implemented; automated external regulation feed monitoring is a stub |
| **CEAAPSS-566** | Audit Trail | ✅ | Append-only hash-chained ledger (`app/audit.py:70-96`), search/filter (`app/audit.py:106-132`), tamper verification (`app/audit.py:135-148`), CSV/JSON/PDF export (`app/audit.py:151-164` / `app/pdf.py`), RBAC export roles (`app/api/deps.py:21-31`), Merkle anchoring (`app/merkle.py:19-46` / `app/audit.py:174-192`), cold tier/purge (`app/coldstore.py` / `tests/test_lifecycle_latency.py:46-91`); WORM/cold-store default to `none`/in-memory; production S3 Object Lock/Glacier wiring is P4 |
| **CEAAPSS-651** | Vision | ✅ | Captured in `docs/ticket_description_cms.md` and implemented in P0–P3 scope |
| **CEAAPSS-652** | Project Requirement | ✅ | Objectives, constraints, acceptance, NFRs, and BRs mapped to code in §3.1 and this doc |
| **CEAAPSS-653** | Functional Requirement | ✅ | FR-001..FR-020 mapped to code in §3; FR-012, FR-015, and FR-020 have P4 gaps; 152 tests pass in the current CMS suite (broker-free, optional extras installed) |
| **CEAAPSS-654** | System Architecture | ✅ | Component design implemented (Pre-Send Hook, Ingestor, Rule Engine, Disparity Monitor, Audit Ledger, Alert/Case Engine, Engine-8 Adapter stubs) |
| **CEAAPSS-655** | System Design | ✅ | Data models, schemas, API specification, hash-chain/Merkle design, and lifecycle workflows implemented |
| **P4 / P5 / GA** | Integration / Hardening / Launch | 📝 | Engine-8 central correlation, scheduled batch analytics, production streaming, S3 WORM/Object Lock, cold-storage automation, BI dashboard, RBAC/SSO, SOC 2 runbooks, load/latency hardening |

Test coverage: **152 tests** in `src/agents/compliance_monitor/tests/`; current run is **152 passed** with optional dev extras installed and no stale failures.

## 3. Functional Requirements (FR-001..FR-020)

| ID | Requirement | MoSCoW | Status | Notes |
|----|-------------|--------|--------|-------|
| FR-001 | Ingest activity events (comms, record changes, scores, recommendations, overrides) | Must | ✅ | `POST /activity` (`app/api/compliance.py:51-53`), `app/kafka_bus.py:315-355` consumer loop, `app/http_source.py:19-39` poll, `app/service.py:97-153` `record_activity`; covered by `tests/test_activity_dashboard.py:6-10`, `tests/test_new_tiers.py:41-63`, and `tests/test_kafka_spark_cache.py:50-57` |
| FR-002 | Provide a synchronous pre-send compliance hook for outbound comms | Must | ✅ | `POST /precheck` (`app/api/compliance.py:33-36`) + `app/precheck.py:30-49` `evaluate` + `app/service.py:156-188` `run_precheck`; covered by `tests/test_precheck.py:16-67` and `tests/test_lifecycle_latency.py:13-24` |
| FR-003 | Evaluate TCPA quiet-hours, opt-out, DNC on every communication | Must | ✅ | `app/precheck.py:19-43` quiet-hours with contact timezone; `Consent.opted_in` / `Consent.on_dnd_registry` (local flag; real TCPA DNC registry integration is P4); covered by `tests/test_precheck.py:24-49` |
| FR-004 | Evaluate CAN-SPAM requirements (unsubscribe, sender identity) on emails | Must | ✅ | `app/precheck.py:44-48` checks `has_unsubscribe` and `sender_id` for `channel=email` |
| FR-005 | Monitor fair-lending disparity (selection-rate ratio) per decisioning sub-agent | Must | ✅ | `POST /fairness/evaluate` (`app/api/compliance.py:64-67`) + `app/fairness.py:12-27` `disparity` with 4/5ths band and min-sample guard; covered by `tests/test_fairness.py:8-30` |
| FR-006 | Track required disclosures + adverse-action timelines (ECOA 30-day) | Must | ✅ | `app/disclosures.py:14-50` + `POST /disclosures/stage` (`app/api/compliance.py:98-100`) + `service.on_stage_change` (`app/service.py:237-244`) + `service.check_disclosures` (`app/service.py:261-277`); covered by `tests/test_disclosures.py:10-43` and `tests/test_new_tiers.py:89-117` |
| FR-007 | Detect PII exposure / improper handling in communications + logs | Should | ✅ | `app/pii.py` regex scan; high-risk categories block precheck; activity payloads raise `data_privacy` alerts |
| FR-008 | Store an immutable, hash-chained audit entry per monitored event | Must | ✅ | `app/audit.py:70-96` `append` writes `prev_hash`/`entry_hash`; no edit/delete path in API; covered by `tests/test_audit.py:12-29` and `tests/test_persistence_sql.py:37-59` |
| FR-009 | Store explainability records for AI decisions (reason codes, features) | Must | ✅ | `POST /explainability` (`app/api/compliance.py:136-138`) + `ExplainabilityRepository`; missing `explain_ref` on `decision` events raises a case (`app/service.py:128-135`); covered by `tests/test_activity_dashboard.py:13-26` and `tests/test_audit_findings.py:89-93` |
| FR-010 | Raise tiered alerts (info/warning/critical) with context | Must | ✅ | `app/casework.py::raise_alert` creates `Alert`; criticals (or `force_case`) open `Case` |
| FR-011 | Create + manage compliance cases with status + assignment | Must | ✅ | `GET /cases`, `PATCH /cases/{id}` + `app/casework.py::update_case` + `Case` lifecycle |
| FR-012 | Provide remediation workflow + SLA tracking on cases | Should | 🚧 | Case model carries `status`, `assignee`, `sla_due_at` (`app/domain/models.py:120-130` / `app/casework.py:50-68`); `PATCH /cases/{case_id}` updates status/assignee (`app/api/compliance.py:160-173` / `tests/test_api.py:45-57`); full remediation playbooks/SLA enforcement UI are P4 |
| FR-013 | Publish compliance signals to Engine 8 | Must | ✅ | `app/events.py:22-33` emits `compliance.signal` / `compliance.alert` / `compliance.case.opened`; `app/kafka_bus.py:176-205` mirrors to Kafka when configured; Engine-8 central correlation + signed policy verification are P4; covered by `tests/test_activity_dashboard.py:29-34` |
| FR-014 | Consume policy thresholds/rules from Engine 8 | Must | ✅ | `POST /policies` + `app/policies.py::effective_settings` overlays active policy onto runtime settings |
| FR-015 | Run batch pattern analysis (e.g., redlining-style geographic disparity) | Should | 🚧 | `app/spark_batch.py:11-35` pure aggregation; `jobs/disparity_batch.py:21-40` Spark runner; `POST /fairness/batch` (`app/service.py:365-381`); production scheduled Spark/Flink + warehouse integration are P4; covered by `tests/test_kafka_spark_cache.py:61-90` and `tests/test_gaps.py:129-133` |
| FR-016 | Export audit evidence packages for a given entity/timeframe | Must | ✅ | `GET /audit/export?fmt=json|csv|pdf` + `app/audit.py::export_package` + `app/pdf.py::render` |
| FR-017 | Block/hold non-compliant outbound comms at the pre-send hook | Must | ✅ | `service.run_precheck` returns `deny` for any hard-rule hit and records the block in the ledger |
| FR-018 | Verify audit-chain integrity periodically + on demand | Must | ✅ | `GET /audit/verify` (`app/api/audit.py:31-34`), `POST /audit/integrity-check` (`app/api/audit.py:36-38`), background `_chain_verify_loop` (`app/main.py:25-33`); covered by `tests/test_gaps.py:137-145` |
| FR-019 | Suppress/deduplicate repeat alerts to control noise | Should | ✅ | `app/casework.py:36-42` dedup window; non-critical `(rule, entity, severity)` alerts are suppressed; criticals never are; covered by `tests/test_gaps.py:115-125` |
| FR-020 | Provide dashboards for coverage, disparity, alert backlog | Should | 🚧 | `GET /dashboard` (`app/service.py:624-646`) returns JSON RAG status; `GET /alerts`, `GET /cases`, `GET /coverage`; rendered BI dashboard UI is P4; covered by `tests/test_activity_dashboard.py:37-51` and `tests/test_api.py:40-42` |

### 3.1 Non-Functional Requirements (NFR) and Business Rules (BR)

Ticket NFRs (§5) and business rules (§6) are tracked below using the same legend.

#### Non-Functional Requirements

| NFR | Status | Notes |
|-----|--------|-------|
| Detection latency — p95 ≤ 10 s (streaming), batch nightly for pattern analysis | 🚧 | `service.run_precheck` hard rules run in < 80 ms (`app/service.py:156-188`); alerts are raised synchronously in `service.record_activity` (`app/service.py:97-153`); batch scheduling and formal p95 load measurement are P4 |
| Throughput — ≥ 8,000 activity events/s | 📝 | In-memory path is fast, but the 8k/s target requires load testing, horizontal scaling, and HPA in P5/GA |
| Availability — 99.95% | 📝 | Default in-memory/single-replica; multi-AZ replication and HA are P5/GA |
| Audit immutability — append-only, tamper-evident (hash-chained), ≥7-year retention | ✅ | `app/audit.py:70-148` append-only + hash chain; `app/coldstore.py` / `app/worm.py` support durable long-term storage; default 7-year retention |
| Evidence retrieval — ≤ 5 min for any entity/timeframe | ✅ | `GET /audit/search` and `GET /audit/export` are sub-second in-memory / SQL with indexes (`app/db/orm.py:23-29`); production guarantees depend on scale |
| False-positive rate — ≤ 15% after tuning | 🚧 | `app/casework.py:36-42` dedup window + `app/fairness.py:21-23` min-sample guard reduce noise; ML-based alert tuning is P4 |
| Coverage — 100% of communications + decisioning events | 🚧 | `app/coverage.py:25-44` sequence gaps, `app/coverage.py:47-55` heartbeat, `app/coverage.py:58-62` dead-letters; full 100% coverage requires peer-engine integration and Kafka/HTTP ingest in P4 |
| Security — PII encrypted + access-logged; least-privilege RBAC | 🚧 | All state changes append to the audit log (`app/audit.py:70-96`); PII is not encrypted at rest in the default JSON/SQL stores; RBAC is limited to `X-API-Key` + `X-Role` (`app/api/deps.py`); OIDC/SSO and field-level encryption are P4/P5 |
| Compliance — ECOA/Reg B, TCPA, CAN-SPAM, GDPR/CCPA, SOC 2 | 🚧 | Rule detectors and audit trails are implemented; SOC 2 runbooks/evidence packaging and external certifications are P5/GA |
| Recoverability — RPO ≤ 30 s, RTO ≤ 10 min; no audit-event loss | 🚧 | Durable SQL backend (`app/db/ledger.py` / `app/repositories.py`) + carry-hash purge (`app/audit.py:206-226`) protect entries; multi-AZ replication and automated DR are P5/GA |

#### Business Rules

| BR | Status | Notes |
|----|--------|-------|
| BR-1 — A communication failing any hard rule is denied at pre-send | ✅ | `app/precheck.py:30-49` + `app/service.py:156-188`; `tests/test_precheck.py:16-67` |
| BR-2 — Protected-class attributes are used only for disparity monitoring, never as decision inputs | ✅ | `app/fairness.py:12-27` and `app/spark_batch.py:11-35` only consume cohort counts; protected attributes are not stored or used in decisioning; `tests/test_fairness.py:8-37` |
| BR-3 — Every monitored event yields an immutable, hash-chained audit entry; gaps are alerts | ✅ | `app/audit.py:70-96` append; `app/coverage.py:25-44` sequence gaps raise `coverage_gap` alert; `tests/test_new_tiers.py:16-24` and `tests/test_audit.py:12-20` |
| BR-4 — AI decisions without an explainability record are flagged non-compliant | ✅ | `app/service.py:128-135` raises `explainability_required` and opens a case; `tests/test_activity_dashboard.py:13-26` and `tests/test_audit_findings.py:89-93` |
| BR-5 — Disclosure clocks use regulatory calendars; adverse-action default = 30 days (ECOA) | ✅ | `app/disclosures.py:14-21` stage→disclosure map; `app/disclosures.py:39-50` status; `tests/test_disclosures.py:10-43` and `tests/test_new_tiers.py:89-117` |
| BR-6 — Critical alerts auto-create a case and notify compliance within SLA | ✅ | `app/casework.py:27-53` critical → auto-case with `sla_due_at`; `tests/test_fairness.py:24-30` and `tests/test_gaps.py:34-37` |

## 4. Main CMS Ticket Requirement Coverage

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Communication compliance (unsubscribe link, sender identity, Do Not Call) | ✅ | `POST /precheck` runs TCPA quiet-hours, opt-out, DND/DNC, and CAN-SPAM checks |
| Consent management (opt-in/opt-out per lead per channel) | ✅ | `PUT /consent` (`app/api/compliance.py:39-41`) + `ConsentRepository` / `app/cache.py:52-74`; `service.run_precheck` blocks against cached/durable consent (`app/service.py:156-188`); external consent service integration is P4 |
| Data retention policy engine (auto-anonymize/delete, pre-deletion alert) | ✅ | `app/privacy.py:30-72` + `POST /privacy/retention/check` (`app/api/compliance.py:205-207`) + `POST /privacy/erase` (`app/api/compliance.py:215-217`); `service.check_retention` (`app/service.py:403-426`) warns then auto-anonymizes; covered by `tests/test_gaps.py:61-80` and `tests/test_audit_findings.py:24-35` |
| AI decision audit log (immutable, append-only, full reasoning) | ✅ | Every decision/override/activity appends a hash-chained `AuditEntry`; missing `explain_ref` is flagged |
| Fairness monitor (disparate impact across demographic groups) | ✅ | 4/5ths selection-rate ratio in `app/fairness.py:12-27`; batch redlining in `app/spark_batch.py:11-35`; covered by `tests/test_fairness.py:8-30` and `tests/test_kafka_spark_cache.py:61-90`; chi-square statistical test is not applied (P4) |
| RESPA/TILA regulatory disclosure tracker | ✅ | `app/disclosures.py` maps required disclosures to pipeline stages; adverse-action 30-day clock with T-7d/T-2d warnings |
| Compliance dashboard (real-time RAG status per domain) | 🚧 | `GET /dashboard` (`app/service.py:624-646`) returns red/amber/green per domain; visual BI dashboard is P4; covered by `tests/test_activity_dashboard.py:37-51` and `tests/test_api.py:40-42` |
| Violation alert and block (immediate alert + action block) | ✅ | Precheck `deny` + `casework.raise_alert` + critical auto-case + Kafka signal |
| Audit export (complete activity log for regulatory inspection) | ✅ | `GET /audit/export?fmt=json|csv|pdf` (`app/api/audit.py:41-56`) supports JSON, CSV, and PDF with RBAC `X-Role` gating (`app/api/deps.py:21-31`); covered by `tests/test_gaps.py:148-161` and `tests/test_audit.py:39-57` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Middleware architecture — comms and decisions pass through compliance before execution | ✅ | `service.run_precheck` is the sync gate; `service.record_activity` is the async surveillance path |
| Immutable audit log — append-only, no modification/deletion | ✅ | `app/audit.py`; purge only for aged, anchored, cold-archived ranges with carry-hash continuity |
| Fairness analysis — statistical tests (Chi-square / disparate impact ratio) | 🚧 | 4/5ths selection-rate ratio implemented in `app/fairness.py:12-27` and `app/spark_batch.py:11-35`; chi-square test is not currently applied (P4 enhancement); covered by `tests/test_fairness.py:8-37` |
| CCPA/Privacy right-to-delete — anonymizes lead data and removes from training datasets | ✅ | `service.erase_lead` (`app/service.py:429-438`) / `app/privacy.py:53-72` anonymizes consent, flags record `erased`, and clears the consent cache; actual removal from external training datasets is a P4 integration |
| Tamper detection — hash-chaining and Merkle proofs | ✅ | `audit.verify_chain`, `merkle.build_root`, `GET /audit/{seq}/proof` |
| Coverage-gap / dropped-event monitoring | ✅ | `app/coverage.py` tracks per-source `source_seq` gaps, heartbeat staleness, and dead-letter counts |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| All outbound messages compliance-checked before sending | ✅ | `POST /precheck` is the fail-closed hard-rule gate |
| Messages to opted-out leads blocked and the block is logged | ✅ | Opt-out/DND + no-consent → `deny` + `AuditEntry` + `Alert` |
| Consent status tracked per channel per lead and respected system-wide | ✅ | `Consent` keyed by `(lead_id, channel)`; cache invalidated on any change |
| Immutable AI decision audit log complete and exportable | ✅ | Every `decision` event appends a hash-chained record; export as CSV/JSON/PDF |
| Fairness monitor flags statistically biased outputs with confidence threshold | ✅ | Min-sample guard + 4/5ths band; failing ratio raises a critical alert and case |
| Data retention policies automatically enforced with pre-deletion alerts | ✅ | `service.check_retention` warns then auto-anonymizes past the deadline |
| Compliance dashboard shows real-time status across all monitored domains | 🚧 | `GET /dashboard` RAG API returns real-time status; visual dashboard P4 |
| Full audit trail exportable for regulatory inspection at any time | ✅ | `GET /audit/export` with `X-Role` authorization |

## 5. CEAAPSS-565 — Regulatory Updates

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Regulatory change monitoring (track RESPA, TILA, FTC DNC, CCPA, state privacy laws) | 🚧 | `app/regwatch.py:15-25` `report_change` + `POST /regulations/{id}/change` (`app/api/rules.py:118-123`) support manual reporting; staleness sweep at `app/regwatch.py:40-49`; automated federal-register/FTC/CFPB/HUD feed scraping/API subscription is a P4 stub; covered by `tests/test_gaps.py:99-112` |
| Alert system when a tracked regulation changes | ✅ | `service.report_regulatory_change` flips status to `review_needed` and raises a `regulatory` alert |
| Rule update workflow (propose → approve → deploy) | ✅ | `app/rules.py:24-88` + `app/api/rules.py:43-75`: propose (`app/rules.py:24-38`), approve (`app/rules.py:41-54`), reject (`app/rules.py:57-69`), deploy (`app/rules.py:72-88`), history (`app/api/rules.py:94-97`); covered by `tests/test_rules.py:14-68` |
| Rule version history | ✅ | `RuleChange` model + `GET /rules/{id}/history` |
| Rule staging | ✅ | `ComplianceRule` with `stage=staging`/`production`; deploy promotes staged definition |
| Regulation tracker dashboard | ✅ | `PUT /regulations`, `GET /regulations?status=...`, `POST /regulations/sweep` |
| Manual rule override for emergencies | ✅ | `POST /rules/emergency` single-actor override, flagged `emergency=True` and audited |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Regulation monitoring (federal register, FTC/CFPB/HUD, legal update API) | 🚧 | No active scraper (`app/regwatch.py:15-49` only reacts to manual/API reports); changes are reported through `POST /regulations/{id}/change` or can be wired to a P4 feed adapter |
| Rule change record `{rule_id, previous_version, new_version, changed_by, approved_by, change_reason, effective_date}` | ✅ | `RuleChange` model (`app/domain/models.py:142-156`) persisted in `RuleChangeRepository` / `app/db/sql_repositories.py:151-175`; covered by `tests/test_rules.py:14-68` |
| Approval workflow — two-step, proposer ≠ approver | ✅ | `rules.approve_change` rejects self-approval; `app/rules.py:49-50` |
| Staging → production deployment requires explicit approval | ✅ | `rules.deploy_change` only allows `status=approved`; `app/rules.py:78-79` |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Tracked regulations are monitored and changes detected within 48 hours | 🚧 | Detection is manual/API-driven (`app/regwatch.py:15-25` / `app/api/rules.py:118-123`); automated feed polling not yet implemented; P4 |
| Compliance officer/legal team alerted when a tracked regulation changes | ✅ | `service.report_regulatory_change` raises a `regulatory` domain alert |
| Rule update workflow requires proposer and separate approver | ✅ | `rules.approve_change` enforces different users |
| All rule changes are version-controlled with full change history | ✅ | `RuleChange` + `GET /rules/{id}/history` |
| Proposed changes go through staging before production | ✅ | `propose_change` creates a `staging` rule; `deploy_change` promotes to `production` |
| Regulation tracker dashboard shows status of all tracked regulations | ✅ | `GET /regulations` with `current`/`review_needed`/`updated` filter |
| Emergency manual rule updates are possible for authorized users | ✅ | `POST /rules/emergency` with full audit trail |

## 6. CEAAPSS-566 — Audit Trail

### Functional Requirements

| Requirement | Status | Notes |
|-------------|--------|-------|
| Events logged: AI decisions, user actions, compliance checks, consent changes, rule updates | ✅ | Every state change calls `audit.append`; `record_activity` starts with a ledger write |
| Immutability: append-only log, no edit or delete by any user | ✅ | No write/update/delete endpoints for audit rows; hash chain detects tampering |
| Search and filter by lead ID, agent ID, event type, date range, engine | ✅ | `GET /audit/search` (`app/api/audit.py:16-28`) filters by `entity_id`, `actor_id`, `event_type`, `engine`, `frm`/`to`; SQL indexes on `entity_id`/`event_type`/`engine` (`app/db/orm.py:23-29`); full-text search across `reasoning`/`payload` is P4 |
| Audit record structure: who, what, when, why, outcome | ✅ | `AuditEntry` carries `actor_*`, `event_type`, `entity_*`, `action`, `reasoning`, `outcome`, `engine`, `explain_ref`, `ip_address`, `session_id` |
| Data retention (configurable, default 7 years) | ✅ | `AUDIT_RETENTION_YEARS` drives purge; `audit_cold_age_days` tiers older entries |
| Export: full or filtered audit log as CSV or PDF | ✅ | `GET /audit/export?fmt=csv|pdf|json` (`app/api/audit.py:41-56`) + `app/audit.py:151-164` + `app/pdf.py`; covered by `tests/test_gaps.py:148-161` and `tests/test_audit.py:39-57` |
| Access control: read-only for all users; only compliance roles can export | ✅ | `GET /audit/export` requires `X-Role` in `EXPORT_ROLES` (`app/api/deps.py:21-31`); search/verify are unauthenticated reads; full RBAC/OIDC/SSO is P4 |
| Tamper detection: hash-chaining or equivalent | ✅ | `audit.verify_chain` recomputes hashes; Merkle inclusion proofs at `GET /audit/{seq}/proof` |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Storage: append-only event store separate from main transactional DB | ✅ | `app/audit.py` ledger with in-memory default and SQL backend (`app/db/ledger.py`) |
| Record schema `{event_id, timestamp, actor_type, actor_id, event_type, entity_type, entity_id, action, reasoning, outcome, ip_address, session_id}` | ✅ | `AuditEntry` model; `occurred_at` stored as ISO-8601 for deterministic hashing |
| Hash chaining: each record includes hash of previous record | ✅ | `prev_hash` + SHA-256 of canonical core → `entry_hash`; `app/audit.py:47-48` |
| Search index: full-text + structured index on key fields | 🚧 | SQL indexes on `entity_id`, `engine`, `event_type`; full-text search is P4 |
| Long-term storage: cold storage for records older than 1 year | ✅ | `app/coldstore.py` (`LocalColdStore:38-70` / `S3ColdStore:73-117`) + `POST /audit/cold-tier` / `/audit/purge` (`app/api/audit.py:86-93`); S3/local backends; default `COLD_STORE_GATEWAY=none`; production S3 Glacier automation is P4; covered by `tests/test_lifecycle_latency.py:46-91` |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| All defined event types are captured in the audit log automatically | ✅ | `record_activity` + all service workflows append a ledger entry |
| No user (including admin) can edit or delete any audit record | ✅ | Append-only by design; API has no mutation path |
| Audit log is searchable by lead ID, agent ID, event type, and date range | ✅ | `GET /audit/search` |
| Full and filtered audit log is exportable as CSV and PDF | ✅ | `GET /audit/export` with format selector |
| Tamper detection mechanism is in place and verifiable | ✅ | `GET /audit/verify`, `POST /audit/integrity-check`, `GET /audit/{seq}/proof` |
| Records are retained for the configured retention period (default 7 years) | ✅ | `service.audit_purge` (`app/service.py:578-605`) only removes anchored + cold-archived entries older than `AUDIT_RETENTION_YEARS`; exact-year `privacy._add_years` (`app/privacy.py:15-27`) |
| Only authorized compliance roles can access and export the audit log | ✅ | `require_export_role` gating |
| Response time for audit log search under 5 seconds for any query | ✅ | In-memory and indexed-SQL searches are sub-second; production guarantees depend on scale |

## 7. CEAAPSS-651 to CEAAPSS-655 — Documentation & Design Deliverables

| Subtask | Title | Status | Evidence |
|---------|-------|--------|----------|
| **CEAAPSS-651** | Vision | ✅ | `docs/ticket_description_cms.md` §1–7 (Purpose, Problem, Value, Scope, KPIs) and P0–P3 implementation |
| **CEAAPSS-652** | Project Requirement | ✅ | `docs/ticket_description_cms.md` §8–10 (Objectives, NFRs, Constraints, Acceptance) and §3.1 below |
| **CEAAPSS-653** | Functional Requirement | ✅ | `docs/ticket_description_cms.md` §11 (FR-001..FR-020) mapped to code in §3 above |
| **CEAAPSS-654** | System Architecture | ✅ | `docs/ticket_description_cms.md` §12 (Context, Components, Data Flow, Tech Stack) and `app/` component layout |
| **CEAAPSS-655** | System Design | ✅ | `docs/ticket_description_cms.md` §13 (Detailed Design, Schemas, API Spec, Workflows, Sequence) and implemented API endpoints |

## 8. P4 Implementation Update

This P4 pass added the following bug-audit and hardening fixes to the Compliance Monitor agent while remaining a target for cross-agent clients:

- **FUS → CMS precheck**: `src/agents/follow_up_sequences/app/clients/compliance_client.py` calls `POST /api/v1/precheck` to centralise compliance screening before any outbound send. Configure `COMPLIANCE_MONITOR_URL` and `COMPLIANCE_API_KEY` in `src/agents/follow_up_sequences/.env`.
- **Shared infrastructure**: `docker-compose.yml` now provisions Postgres, Redis, Redpanda (Kafka), Prometheus, Grafana, Schema Registry, and an OpenTelemetry Collector for CMS to use when `STORAGE_GATEWAY=postgres`, `CACHE_GATEWAY=redis`, `KAFKA_PRODUCE=true`, and `KAFKA_INGEST=true`.
- **Bug-audit fixes**: `app/main.py` `_coverage_loop` now uses `coverage_sweep_interval_min` (`app/main.py:94-95`); `app/core/config.py` adds `coverage_sweep_interval_min` and `data_retention_years` (`app/core/config.py:86,119`); `app/http_source.poll_once` safely handles non-numeric `source_seq` and `cursor` (`app/http_source.py:31-38`); `app/api/compliance.py::CaseUpdate.status` is `Literal["open", "investigating", "remediated", "closed"]` (`app/api/compliance.py:161`), `app/casework.py::update_case` uses the same `CaseStatus` `Literal`, and `app/service.py::_raise` is typed with `Domain`/`Severity` (`app/service.py:79-83`); `app/privacy.py` retention uses exact-year `data_retention_years` with leap-year handling, falling back to `data_retention_days` if > 0 (`app/privacy.py:15-27`); `app/service.py::audit_purge` now uses `privacy._add_years` for an exact-year retention cutoff (`app/service.py:585`); `app/privacy.py` and `app/disclosures.py` `days_left` are now floats from total seconds (`app/privacy.py:45`, `app/disclosures.py:44`); `app/coldstore.py` lists S3 ranges once (`S3ColdStore._range_keys` and `fetch`) and uses `encoding="utf-8"` for local/S3 files (`LocalColdStore.archive` and `S3ColdStore.archive`).
- **Thread-safety hardening**: every global in-memory store and all in-memory repository methods now use `threading.RLock()` — `app/audit.py`, `app/cache.py`, `app/coverage.py`, `app/events.py`, `app/kafka_bus.py`, `app/worm.py`, `app/coldstore.py`, `app/casework.py` dedup, and `app/repositories.py`.
- **Default-dev-key rejection**: `app/core/config.py` now raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway.

Items still deferred:
- Engine-8 central correlation + signed policy verification.
- Scheduled batch fair-lending / redlining analytics (Spark/Flink).
- External consent / DNC service integration.
- S3 WORM / Object Lock automation and BI dashboard rendering.

## 9. P4 — Promotion / GA Launch (Future)

P4 in the ticket is the Integration milestone (weeks 16–19), followed by P5 Hardening and GA Launch. The following items are intentionally deferred and are **not** part of the current P0–P3 delivery:

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| Engine-8 central correlation + signed policy verification | P4 Integration | 📝 | `compliance.signal` emitted and policies consumed; cryptographic signature validation and central correlation UI are P4 |
| Scheduled batch fair-lending / redlining analytics (Spark/Flink) | P4 Integration | 📝 | `jobs/disparity_batch.py` + `app/spark_batch.py` ready; nightly scheduler and warehouse integration P4 |
| Production Kafka streaming at scale (consumer groups, partition keyed by `entity_id`) | P4 Integration | 📝 | `aiokafka` producer/consumer present; production multi-replica consumer group tuning P4 |
| External consent / DNC service integration | P4 Integration | 📝 | Consent and DNC checks use local repository; real TCPA DNC registry / consent service API P4 |
| S3 Object Lock WORM + external notary Merkle anchoring | P4 Integration | 📝 | `app/worm.py` and `app/merkle.py` support S3 and local; production Object Lock + notary wiring P4 |
| Cold-storage automation and lifecycle purge | P4 Integration | 📝 | `app/coldstore.py` + `audit/cold-tier` + `audit/purge`; production S3 Glacier + cron automation P4 |
| BI compliance dashboard (visual RAG, case queue, disparity charts) | P5 Hardening | 📝 | `GET /dashboard` returns JSON; Looker/Superset/Metabase/Custom UI P4 |
| Full RBAC / SSO with role policies beyond `X-API-Key` and `X-Role` | P5 Hardening | 📝 | `app/api/deps.py` header auth; OIDC/SSO policy engine P4 |
| Load + latency hardening (8,000 events/s, precheck p99 < 100 ms) | P5 Hardening | 📝 | Domain logic is fast; horizontal scaling, HPA, and load testing are P4 |
| SOC 2 / fair-lending compliance runbooks and evidence automation | P5 Hardening | 📝 | Audit trail + fairness gates present; runbooks and continuous evidence packaging P4 |
| Multi-AZ durability, RPO ≤ 30 s, RTO ≤ 10 min | P5 Hardening | 📝 | In-memory default; replicated Postgres + multi-AZ object storage P4 |
| Admin UI / MLOps-style compliance ops (case triage, alert suppression, rule staging) | GA Launch | 📝 | Only REST endpoints exist; admin UI P4 |

## 9. Component Coverage (System Architecture View)

| Component | Status | Notes |
|-----------|--------|-------|
| Pre-Send Hook | ✅ | `app/precheck.py` fail-closed TCPA/CAN-SPAM/DNC/PII gate (FR-002/003/004/017) |
| Activity Ingestor | ✅ | `app/kafka_bus.py`, `app/http_source.py`, `service.record_activity` (FR-001) |
| Rule Engine | ✅ | `app/precheck.py`, `app/rules.py`, `app/disclosures.py` (TCPA, CAN-SPAM, disclosure rules) |
| Disparity Monitor | ✅ | `app/fairness.py` 4/5ths + `app/spark_batch.py` redlining (FR-005/015) |
| Explainability Recorder | ✅ | `service.record_activity` enforces `explain_ref`, `POST /explainability` (FR-009) |
| Audit Ledger | ✅ | `app/audit.py` append-only hash-chained ledger + SQL backend (FR-008/018) |
| Alert Manager | ✅ | `app/casework.py` tiered + dedup (FR-010/019) |
| Case Engine | ✅ | `app/casework.py` critical auto-case + `PATCH /cases` (FR-011) |
| Engine 8 Adapter | 🚧 | `app/events.py:22-33` + `app/kafka_bus.py:176-205` emit `compliance.signal` / alert / case events; central correlation and crypto-signed policies P4; covered by `tests/test_activity_dashboard.py:29-34` |
| Evidence Exporter | ✅ | `app/audit.py::export_csv`, `app/pdf.py::render` (FR-016) |
| Consent Cache | ✅ | `app/cache.py` memory/Redis with TTL and invalidation |
| WORM Mirror | 🚧 | `app/worm.py` (`LocalWormStore:31-58` / `S3WormStore:61-83`) local/S3 Object Lock; production S3 COMPLIANCE wiring P4; covered by `tests/test_worm_merkle.py:17-50` (S3 test skipped when `moto` absent) |
| Coverage Monitor | ✅ | `app/coverage.py` sequence gaps, heartbeat, dead-letters (BR-3) |
| Data Privacy Engine | ✅ | `app/privacy.py` retention, erasure, anonymization (FR-007 data privacy) |
| Regulatory Tracker | 🚧 | `app/regwatch.py:15-49` manual change reporting (`report_change`) + staleness sweep (`stale_sweep`); `POST /regulations/{id}/change` (`app/api/rules.py:118-123`); external feed P4; covered by `tests/test_gaps.py:99-112` |

## 10. File Map

```
src/agents/compliance_monitor/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI factory + lifespan background loops
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # pydantic-settings + environment defaults
│   │   ├── logging.py          # structlog setup
│   │   └── tracing.py          # OpenTelemetry auto-instrumentation (opt-in)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # X-API-Key + X-Role dependencies
│   │   ├── health.py           # /health, /ready
│   │   ├── compliance.py       # precheck, activity, fairness, disclosures, privacy, dashboard
│   │   ├── audit.py            # search, verify, export, anchor, lifecycle, cold/worm
│   │   └── rules.py            # rule changes, regulations, emergency override
│   ├── domain/
│   │   ├── __init__.py
│   │   └── models.py           # ActivityEvent, AuditEntry, Consent, DisparityResult, Case, Rule, Regulation, etc.
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py           # SQLAlchemy engine + session factory
│   │   ├── orm.py              # ORM tables (audit_ledger, cases, consent, rules, retention, anchors)
│   │   ├── ledger.py           # SQL audit ledger + Merkle anchor backend
│   │   └── sql_repositories.py # SQL-backed operational repositories
│   ├── audit.py                # immutable hash-chained ledger (FR-008/018)
│   ├── precheck.py             # sync fail-closed pre-send gate (FR-002/003/004/017)
│   ├── fairness.py             # 4/5ths disparity monitor (FR-005)
│   ├── spark_batch.py          # pure batch redlining aggregation (FR-015)
│   ├── disclosures.py          # ECOA / TILA-RESPA disclosure clocks (FR-006)
│   ├── rules.py                # rule versioning + two-step approval (CEAAPSS-565)
│   ├── policies.py             # Engine-8 policy overlay (FR-014)
│   ├── pii.py                  # PII pattern scan (FR-007)
│   ├── privacy.py              # retention + right-to-erasure (Data Privacy)
│   ├── regwatch.py             # regulatory change + staleness sweep (CEAAPSS-565)
│   ├── casework.py             # tiered alerts, case lifecycle, SLA, dedup (FR-010/011/012/019)
│   ├── worm.py                 # local/S3 Object Lock WORM mirror
│   ├── coldstore.py            # local/S3 cold storage for aged audit records
│   ├── merkle.py               # Merkle tree, root, inclusion proofs
│   ├── cache.py                # memory/Redis consent hot cache
│   ├── kafka_bus.py            # aiokafka producer + consumer loop
│   ├── http_source.py          # HTTP-poll upstream event source
│   ├── coverage.py             # sequence-gap, heartbeat, dead-letter monitoring (BR-3)
│   ├── events.py               # produced compliance.alert/case/signal feed
│   ├── pdf.py                  # dependency-free PDF audit export
│   ├── service.py              # orchestration layer used by API routes
│   └── repositories.py         # in-memory repo implementations + SQL selector
├── jobs/
│   └── disparity_batch.py      # spark-submit batch redlining job
├── tests/
│   ├── conftest.py             # FastAPI TestClient + hermetic env reset
│   ├── test_api.py
│   ├── test_audit.py
│   ├── test_audit_findings.py
│   ├── test_rules.py
│   ├── test_precheck.py
│   ├── test_fairness.py
│   ├── test_disclosures.py
│   ├── test_gaps.py
│   ├── test_new_tiers.py
│   ├── test_lifecycle_latency.py
│   ├── test_worm_merkle.py
│   ├── test_worm_coldstore_local.py
│   ├── test_kafka_spark_cache.py
│   ├── test_kafka_bus.py
│   ├── test_security_config.py
│   ├── test_redis_consent_cache.py
│   ├── test_coverage_concurrency_batch.py
│   ├── test_persistence_sql.py
│   ├── test_activity_dashboard.py
│   └── test_health.py          # 152 tests total
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/0001_initial.py
├── deploy/
│   ├── README.md
│   └── compliance-monitoring-task-definition.json
├── pyproject.toml              # deps + ruff + pytest config
├── uv.lock                     # frozen dependency set
├── alembic.ini
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── .env.example
├── .gitignore
├── .dockerignore
├── MAPPING.md
└── README.md
```

## 11. Integration into the Monorepo

| Layer | Change | Location |
|-------|--------|----------|
| API gateway routing | Added `compliance_monitor` to `AGENTS` map with slug `compliance-monitor` | `src/api/main.py:13` |
| Gateway route notes | Notes that the gateway proxies to the compliance engine | `src/api/routes/compliance_monitor.py` |
| Compose orchestration | Added `compliance_monitor` service + `COMPLIANCE_MONITOR_URL` | `docker-compose.yml:17-18, 33-37` |
| Main README | Listed `compliance_monitor` as the `Compliance-Monitoring` engine | `README.md:12` |
| Agent tests | Standalone pytest suite (152 tests) | `src/agents/compliance_monitor/tests/` |

The gateway proxies `/compliance-monitor/*` to the agent once `COMPLIANCE_MONITOR_URL` is configured.

## 12. Configuration Highlights

Key environment variables (see `src/agents/compliance_monitor/.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | `change-me-dev-key` | X-API-Key for write/ops endpoints |
| `STORAGE_GATEWAY` | `memory` | `memory` or `postgres` (needs `DATABASE_URL`) |
| `DATABASE_URL` | (empty) | Postgres URL for durable SQL backend |
| `CACHE_GATEWAY` | `memory` | `memory` or `redis` (short-TTL consent cache) |
| `EVENT_SOURCE_GATEWAY` | `local` | `local`, `http` (poll), or `kafka` (stream) |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka / Redpanda bus |
| `KAFKA_PRODUCE` / `KAFKA_INGEST` | `false` | Emit/consume on the bus |
| `WORM_GATEWAY` | `none` | `none`, `local`, or `s3` (Object Lock mirror) |
| `COLD_STORE_GATEWAY` | `none` | `none`, `local`, or `s3` (cold archive) |
| `PRECHECK_LATENCY_BUDGET_MS` | `80` | Sync hard-rule latency budget |
| `QUIET_HOURS_START` / `END` | `21:00` / `08:00` | TCPA quiet-hours window |
| `FAIRNESS_RATIO_MIN` / `MAX` | `0.80` / `1.25` | 4/5ths disparity band |
| `DISPARITY_MIN_SAMPLE` | `100` | Min cohort size before disparity alerts |
| `ADVERSE_ACTION_DAYS` | `30` | ECOA adverse-action disclosure window |
| `DISCLOSURE_WARN_OFFSETS` | `7,2` | Days before deadline to warn |
| `CASE_SLA_HOURS` | `72` | SLA on auto-opened critical cases |
| `AUDIT_RETENTION_YEARS` | `7` | Audit retention horizon |
| `DATA_RETENTION_YEARS` | `7` | Exact-year lead-data retention before anonymization (leap-year safe) |
| `DATA_RETENTION_DAYS` | `0` | Optional day-level override for retention (non-zero takes precedence) |
| `EXPORT_ROLES` | `compliance_officer,auditor,admin` | Roles allowed to export the audit trail |
| `CHAIN_VERIFY_INTERVAL_MIN` | `30` | Periodic audit-chain integrity sweep (0 disables) |
| `COVERAGE_SWEEP_INTERVAL_MIN` | `10` | Periodically sweep for silent sources / coverage gaps (0 disables) |
| `MERKLE_ANCHOR_INTERVAL_MIN` | `0` | Periodic Merkle-root anchoring (0 disables) |
| `PII_SCAN_ENABLED` | `true` | Scan comm content + activity payloads for PII |

## 13. Known Limitations & Simplifications

- **Persistence / scale**: all repositories default to in-memory. Swap to Postgres + Redis + Kafka + S3 for production. The SQL layer is fully implemented but not enabled by default.
- **Regulation feed monitoring**: `app/regwatch.py` and the regulation tracker support manual change reporting and staleness sweeps; an automated scraper or legal-update API subscription is a P4 stub.
- **Batch pattern analytics**: `app/spark_batch.py` and `jobs/disparity_batch.py` implement the 4/5ths redlining aggregation, but a scheduled Spark/Flink job and warehouse integration are P4.
- **Dashboard**: `GET /dashboard` returns a JSON RAG status; a rendered BI dashboard / admin UI is P4.
- **WORM / cold storage**: `app/worm.py` and `app/coldstore.py` support local and S3 backends, but production S3 Object Lock COMPLIANCE mode and Glacier/S3 Glacier Deep Archive tiering are P4 opt-in wiring.
- **Full-text audit search**: SQL indexes support exact/filtered queries; full-text search across `reasoning` / `payload` is P4.
- **Consent / DNC service integration**: the consent repository and DNC flag are local; real-time integration with a TCPA DNC registry or enterprise consent service is P4.
- **PII detection**: regex-based and conservative; deep NLP / entity-classification models are P4.
- **Engine-8 signal contracts**: signals and policies are published/consumed over in-process event log + optional Kafka; cryptographic signing validation and central correlation UI are P4.
- **Load / latency**: domain logic is designed for sub-100 ms prechecks, but the 8,000 events/s and p99 < 100 ms targets require load testing, horizontal scaling, and HPA in P4/P5.
- **Protected-class data**: the disparity monitor expects cohort counts from upstream; it does not itself store or access protected-class attributes (BR-2), and a governed access-controlled store is P4.

## 14. Acceptance Criteria Checklist

- ✅ Synchronous pre-send compliance hook evaluates TCPA quiet-hours, opt-out, DNC, and CAN-SPAM before any outbound send.
- ✅ Non-compliant outbound comms are blocked (`deny`) and the block is written to the immutable audit ledger.
- ✅ Consent is tracked per lead per channel, cached with TTL, and respected across precheck and activity ingest.
- ✅ Every AI decision and monitored activity produces an immutable, hash-chained audit entry with actor, entity, reasoning, and outcome.
- ✅ The audit chain is verifiable on demand (`GET /audit/verify`) and through periodic background integrity checks.
- ✅ Fair-lending disparity outside the 0.80–1.25 band raises a critical alert and opens a case when the minimum sample size is met.
- ✅ ECOA adverse-action and TILA-RESPA disclosure clocks start at the correct pipeline stages and warn/escalate before due dates.
- ✅ Data retention policies warn before deletion and auto-anonymize lead data past the retention deadline.
- ✅ Right-to-erasure requests anonymize the lead, opt out all channels, and flag the record as erased.
- ✅ Rule changes follow a two-step approval workflow with separation of duties, staging, and version history.
- 🚧 Regulation tracker supports manual change reporting and staleness alerts; automated external feed monitoring is P4.
- 🚧 Batch redlining / geographic disparity logic is unit-tested and Spark-runnable; production scheduling and warehouse integration are P4.
- 🚧 RAG dashboard and case/alert lists are available via REST; a rendered BI/ops dashboard is P4.
- ✅ Audit trail can be exported as JSON, CSV, or PDF by privileged roles (`X-Role`).
- ✅ 152/152 tests pass in the current environment with optional extras installed; the suite is broker-free and runs cleanly.

## 15. P4 Next Steps

1. Wire an external regulation feed monitor (FTC/CFPB/HUD gazette / legal-update API) to `app/regwatch.py`.
2. Schedule and operationalize the Spark/Flink batch redlining job against the data warehouse.
3. Enable production Kafka streaming at scale (consumer groups, partition-by-`entity_id`, exactly-once semantics).
4. Integrate a real consent / TCPA DNC service and keep the local cache as a fallback.
5. Enable S3 Object Lock WORM and external notary / Merkle anchoring in production.
6. Automate cold-storage tiering and retention purge against S3 Glacier.
7. Build a BI/ops dashboard for compliance officers (RAG, case triage, alert backlog, disparity trends).
8. Add full RBAC/SSO (OIDC) and cryptographically signed Engine-8 policy ingestion.
9. Load-test and horizontally scale to 8,000 events/s with precheck p99 < 100 ms.
10. Author SOC 2 / fair-lending runbooks and continuous evidence packaging workflows.
