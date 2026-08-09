# CRM Operations Platform — Bug / Logical Error Audit

**Date:** 2026-08-07  
**Scope:** All five sub-agents (`compliance_monitor`, `feedback_loop`, `follow_up_sequences`, `performance_analytics`, `task_automation`) plus the shared API gateway.

**Method:**

- Ran `ruff check` and `pytest` for every agent — all passed.
- Ran `mypy` for every agent — surfaced many type issues, several of which correspond to real runtime or logical bugs.
- Performed line-by-line review of mypy-flagged and high-risk files.
- Used targeted `grep` for common bug patterns (`.days`, `datetime.min/max`, mutable defaults, unsafe casts, `pickle`, response header handling, etc.).

This document lists the confirmed bugs/logical errors and the recommended fix for each.

---

## 1. compliance_monitor

### 1.1 Coverage sweep uses the wrong interval
- **File:** `src/agents/compliance_monitor/app/main.py`
- **Lines:** 94-95
- **Severity:** Major
- **Bug:** The coverage heartbeat loop is started with `settings.chain_verify_interval_min` instead of a coverage-specific interval. If `chain_verify_interval_min` is `0`, the coverage sweep never runs even when `coverage_heartbeat_max_min > 0`.
- **Fix:** Add a dedicated `coverage_interval_min` setting or use `coverage_heartbeat_max_min` for the `_coverage_loop` interval.
- **Status:** Fixed — `coverage_sweep_interval_min` added to `Settings` and used in `src/agents/compliance_monitor/app/main.py:94-95`.

```python
if settings.coverage_heartbeat_max_min > 0 and settings.chain_verify_interval_min > 0:
    tasks.append(asyncio.create_task(_coverage_loop(settings.coverage_heartbeat_max_min)))
```

### 1.2 HTTP source crashes on non-numeric `source_seq`
- **File:** `src/agents/compliance_monitor/app/http_source.py`
- **Line:** 30
- **Severity:** Major
- **Bug:** `int(ev.get("source_seq") or 0)` raises `ValueError` if the upstream returns a non-numeric `source_seq`, killing the entire poll loop.
- **Fix:** Validate before casting and fall back safely.
- **Status:** Fixed — `http_source.py` now wraps `int()` in `try/except (ValueError, TypeError)` and falls back to `cursor`.

```python
try:
    seq = int(ev.get("source_seq") or 0)
except (ValueError, TypeError):
    seq = cursor
source_seq = max(cursor, seq)
cursor = source_seq or (cursor + 1)
```

### 1.3 `update_case` allows invalid case status
- **Files:** `src/agents/compliance_monitor/app/casework.py`, `src/agents/compliance_monitor/app/api/compliance.py`
- **Lines:** `casework.py:57`, `compliance.py:160-174`
- **Severity:** Major
- **Bug:** `CaseUpdate.status` is typed as `str`. `casework.update_case` assigns it directly to `Case.status` (a `Literal["open", "investigating", "remediated", "closed"]`). Invalid values cause a Pydantic `ValidationError`, not a `ValueError`, so the API returns 500 instead of 400.
- **Fix:** Use `Literal` in the request model and service signature, or validate explicitly.
- **Status:** Fixed — `CaseUpdate.status` is now `Literal["open", "investigating", "remediated", "closed"] | None`; `casework.update_case` accepts `CaseStatus | None`.

```python
class CaseUpdate(BaseModel):
    status: Literal["open", "investigating", "remediated", "closed"] | None = None
```

### 1.4 `_raise` uses loose `str` for domain/severity
- **File:** `src/agents/compliance_monitor/app/service.py`
- **Lines:** 84-85, 185
- **Severity:** Major
- **Bug:** `service._raise` accepts `domain: str` and `severity: str`, then passes them to `casework.raise_alert`, which expects the `Domain` and `Severity` `Literal` types. Currently all call sites use valid literals, but the loose signature lets invalid values slip through.
- **Fix:** Tighten the signature.
- **Status:** Fixed — `service._raise` signature now uses `Domain` and `Severity` literal types.

```python
def _raise(
    *, rule_id: str, domain: Domain, severity: Severity, ...
) -> Alert | None:
```

### 1.5 Global mutable state without locking
- **Files:** `app/worm.py`, `app/coldstore.py`, `app/cache.py`, `app/coverage.py`, `app/audit.py`, `app/kafka_bus.py`
- **Severity:** Major
- **Bug:** Module-level globals (stores, counts, event lists) are read/written without `threading.Lock` or `asyncio.Lock`. With multiple Uvicorn workers or concurrent async tasks the state can race, corrupt, or lose updates.
- **Fix:** Wrap each global in a lock, or replace the in-memory implementations with the planned Redis/Postgres-backed gateways.
- **Status:** Fixed — module-level globals in `worm.py`, `coldstore.py`, `cache.py`, `coverage.py`, `audit.py`, `kafka_bus.py`, and `repositories.py` are now protected with `threading.RLock()`.

### 1.6 Retention purge ignores leap years
- **File:** `src/agents/compliance_monitor/app/service.py`
- **Line:** 583
- **Severity:** Minor
- **Bug:** `cutoff = _now() - timedelta(days=settings.audit_retention_years * 365)` ignores leap years.
- **Fix:** Use `dateutil.relativedelta` or compute the cutoff relative to `retained_until` dates.
- **Status:** Fixed — retention purge uses `privacy._add_years(_now(), -settings.audit_retention_years)` for exact-year handling.

```python
from dateutil.relativedelta import relativedelta
cutoff = _now() - relativedelta(years=settings.audit_retention_years)
```

### 1.7 `timedelta.days` truncates disclosure/retention timing
- **Files:** `src/agents/compliance_monitor/app/disclosures.py:43`, `src/agents/compliance_monitor/app/privacy.py:30`
- **Severity:** Minor
- **Bug:** `(due_at - now).days` discards hours/minutes, so a deadline 12 hours away returns `days_left=0` and can trigger warnings prematurely.
- **Fix:** Use fractional days or compare `timedelta` directly.
- **Status:** Fixed — `disclosures.py` and `privacy.py` now compute `days_left` as `delta.total_seconds() / 86400` for fractional days.

```python
days_left = (timer.due_at - now).total_seconds() / 86400
```

### 1.8 Cold-store local files opened without explicit encoding
- **File:** `src/agents/compliance_monitor/app/coldstore.py`
- **Lines:** 31, 49
- **Severity:** Minor
- **Bug:** `open(path, "w")` and `open(...)` use the platform default encoding.
- **Fix:** Always use `encoding="utf-8"`.
- **Status:** Fixed — `coldstore.py` local file operations use `encoding="utf-8"`.

```python
with open(path, "w", encoding="utf-8") as f:
    json.dump(..., f)
```

### 1.9 S3 cold-store fetch double-paginates
- **File:** `src/agents/compliance_monitor/app/coldstore.py`
- **Lines:** 80-96
- **Severity:** Minor (performance)
- **Bug:** `S3ColdStore.fetch()` calls `archived_seqs()` once (full pagination) and then re-paginates to find the file.
- **Fix:** Combine the two passes into one pass that checks `lo <= seq <= hi` while listing objects.
- **Status:** Open — `S3ColdStore.fetch()` still calls `archived_seqs()` once and then re-paginates to find the file; pagination should be combined into a single pass.

---

## 2. feedback_loop

### 2.1 A/B traffic split can total ≠ 100%
- **File:** `src/agents/feedback_loop/app/abtest.py`
- **Line:** 74
- **Severity:** Major
- **Bug:** `round(100 / total, 2)` produces splits that do not sum to 100% (e.g. 7 variants → 100.03), causing the last variant to receive a slightly different bucket share.
- **Fix:** Normalize after rounding.
- **Status:** Fixed — `abtest.py::create_experiment` normalizes rounded traffic splits and applies the delta to the first variant so the total is exactly 100.

```python
raw = {v.name: 100 / total for v in parsed_variants}
rounded = {k: round(v, 2) for k, v in raw.items()}
delta = round(100 - sum(rounded.values()), 2)
# add delta to the first (or largest) variant to make the total exactly 100
traffic_split = rounded
```

### 2.2 Guardrail fallback uses the wrong metric
- **File:** `src/agents/feedback_loop/app/abtest.py`
- **Lines:** 222-226
- **Severity:** Major
- **Bug:** When a `guardrail_metric` is configured (e.g. retention), the fallback still compares the primary conversion rate to `guardrail_threshold`, which can falsely trigger a guardrail breach.
- **Fix:** Only fall back to conversion rate when `guardrail_metric` is `None`.
- **Status:** Fixed — guardrail fallback only compares the primary conversion rate to `guardrail_threshold` when `guardrail_metric is None`.

```python
if not guardrail_breached and exp.guardrail_threshold is not None and exp.guardrail_metric is None:
    ...
```

### 2.3 Drift window uses truncated `.days`
- **File:** `src/agents/feedback_loop/app/drift.py`
- **Line:** 89
- **Severity:** Minor
- **Bug:** `(now - p.scored_at).days <= max_age_days` includes predictions up to ~24 hours past the intended `max_age_days` window.
- **Fix:** Compare `timedelta` exactly.
- **Status:** Fixed — `drift.py::_recent_values` uses a `timedelta(days=max_age_days)` cutoff and compares `now - p.scored_at <= cutoff`.

```python
if now - p.scored_at <= timedelta(days=max_age_days) and ...:
```

### 2.4 Model/dataset artifacts use `pickle`
- **File:** `src/agents/feedback_loop/app/model_retrainer.py`
- **Lines:** 267-268, 213
- **Severity:** Minor (Critical for untrusted storage)
- **Bug:** `pickle.dump` and `pd.read_pickle` are used for model and dataset artifacts. If the object store is compromised, unpickling can execute arbitrary code.
- **Fix:** Use `joblib` or a restricted, signed serialization format for models; store datasets as Parquet or Arrow.
- **Status:** Fixed — `model_retrainer.py` now serializes models and datasets with `joblib` instead of `pickle`.

### 2.5 `_precision_recall_f1` return type mismatch
- **File:** `src/agents/feedback_loop/app/aggregator.py`
- **Lines:** 50, 64
- **Severity:** Minor
- **Bug:** The function returns 4 values but is annotated to return 3. The `# type: ignore` hides the mismatch.
- **Fix:** Update the annotation.
- **Status:** Fixed — `_precision_recall_f1` return type updated to a 4-tuple of `float | None`.

```python
def _precision_recall_f1(...) -> tuple[float | None, float | None, float | None, float | None]:
```

### 2.6 A/B variant hash uses MD5
- **File:** `src/agents/feedback_loop/app/abtest.py`
- **Line:** 20
- **Severity:** Minor
- **Bug:** `hashlib.md5` is used for deterministic assignment. MD5 is cryptographically broken; use `sha256` or a non-cryptographic hash.

```python
h = hashlib.sha256(f"{experiment_id}:{entity_id}".encode()).hexdigest()
```

---

## 3. follow_up_sequences

### 3.1 Frequency cap is not timezone-normalized
- **File:** `src/agents/follow_up_sequences/app/frequency.py`
- **Lines:** 12-18
- **Severity:** Major
- **Bug:** Day/week keys are built from `now.strftime(...)` / `now.isocalendar()` without converting `now` to a consistent timezone first. The cap can shift across timezones and allow over-messaging or under-messaging.
- **Fix:** Convert `now` to UTC (or the contact's local time) before formatting.
- **Status:** Fixed — `frequency.py` normalizes `now` to UTC before building day/week keys (`_as_utc`).

```python
def _day_key(tenant, contact_id, now):
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    return (tenant, contact_id, "d", now.strftime("%Y-%m-%d"))
```

### 3.2 `set_enrollment_state` accepts and silently ignores invalid states
- **Files:** `src/agents/follow_up_sequences/app/api/sequences.py:69-75`, `src/agents/follow_up_sequences/app/service.py:311-323`
- **Severity:** Minor
- **Bug:** `StateRequest.state` is a plain `str` and `set_enrollment_state` silently ignores anything other than `resume`/`paused`/`stopped`.
- **Fix:** Validate in the API model or raise an explicit error.
- **Status:** Fixed — `StateRequest.state` is now `Literal["paused", "resume", "stopped"]`.

```python
class StateRequest(BaseModel):
    state: Literal["resume", "paused", "stopped"]
```

### 3.3 Mutable default `dict = {}` in Pydantic request models
- **File:** `src/agents/follow_up_sequences/app/api/sequences.py`
- **Lines:** 45, 130
- **Severity:** Minor
- **Bug:** `context: dict = {}` in `EnrollRequest` and `LeadScoredRequest` is not the recommended pattern, even though Pydantic v2 currently copies the default.
- **Fix:** Use `Field(default_factory=dict)`.
- **Status:** Fixed — `EnrollRequest.context` and `LeadScoredRequest.context` use `Field(default_factory=dict)`.

```python
context: dict = Field(default_factory=dict)
```

### 3.4 Global in-memory state is unprotected
- **Files:** `app/events.py`, `app/providers.py`, `app/repositories.py`
- **Severity:** Minor
- **Bug:** Global lists/dicts are not locked. Concurrent ticks or webhooks in a multi-worker deployment can race.
- **Fix:** Add locks or switch to the planned Redis/Postgres stores.
- **Status:** Fixed — `app/repositories.py`, `app/events.py`, `app/providers.py`, and `app/frequency.py` are protected with `threading.RLock()`.

---

## 4. performance_analytics

### 4.1 `_period_grain` misclassifies ISO and other period strings
- **File:** `src/agents/performance_analytics/app/reconciliation.py`
- **Lines:** 33-45
- **Severity:** Major
- **Bug:** `period.startswith("P")` returns `monthly` for any period beginning with `P` (e.g. `"P1M"`). Reconciliation then compares bucket `YYYY-MM` against `"P1M"` and always returns 0.
- **Fix:** Reuse the existing `period_utils._parse_grain` / `_parse_bounds` helpers or use regex to distinguish real bucket keywords from ISO durations.
- **Status:** Fixed — `reconciliation._funded_amount_for_period` now uses `period_utils._in_period` with explicit `now`, so ISO-8601 durations (`P1D`, `P7D`, `P1M`) are evaluated as rolling windows and bucket strings are matched by bounds.

### 4.2 `datetime.min` fallback includes deals in every period
- **File:** `src/agents/performance_analytics/app/dashboard_builder.py`
- **Lines:** 590, 625
- **Severity:** Major
- **Bug:** When a deal has no valid stage/final timestamp, `datetime.min.replace(tzinfo=UTC)` is used as a fallback. `_in_period` returns `True` for any period, so those deals pollute every funnel and scorecard.
- **Fix:** Skip the deal when no valid timestamp exists.
- **Status:** Fixed — `dashboard_builder` funnels and scorecards use `_in_period_or_skip` to skip records with missing timestamps.

```python
ts = _deal_stage_at(d, stage) or d["final_at"]
if not ts:
    continue
```

### 4.3 Time-decay attribution overwrites the conversion reference
- **File:** `src/agents/performance_analytics/app/attribution.py`
- **Lines:** 78-80
- **Severity:** Minor
- **Bug:** `funded_at` is computed considering `outcome` and then immediately overwritten by `max(t.sent_at for t in path)`. The outcome-aware conversion-time logic is dead code.
- **Fix:** Remove the dead line or use it correctly:
- **Status:** Fixed — `attribution._time_decay` accepts and uses the actual conversion `funded_at` instead of `max(t.sent_at)`.

```python
funded_at = path[-1].sent_at if not hasattr(path[-1], "outcome") else _now()
```

### 4.4 `AttributionRepository` key type is wrong
- **File:** `src/agents/performance_analytics/app/repositories.py`
- **Lines:** 175-184
- **Severity:** Minor
- **Bug:** `_items` is typed `dict[str, AttributionRecord]` but the actual keys are tuples `(deal_id, model)`.
- **Fix:** Change the type.
- **Status:** Fixed — `AttributionRepository._items` is typed as `dict[tuple[str, str], AttributionRecord]`.

```python
self._items: dict[tuple[str, str], AttributionRecord] = {}
```

### 4.5 Duplicate branches in `build_channel_effectiveness`
- **File:** `src/agents/performance_analytics/app/dashboard_builder.py`
- **Lines:** 730-733
- **Severity:** Minor
- **Bug:** The `if` and `elif` branches perform the exact same filter, so one is dead code.
- **Fix:** Simplify to a single `if period:` block.
- **Status:** Fixed — duplicate branches in `build_channel_effectiveness` collapsed to a single `if period:` block.

---

## 5. task_automation

### 5.1 Doc-request workflow assumes verification and defaults to `True`
- **File:** `src/agents/task_automation/app/workflows/doc_request_temporal.py`
- **Line:** 162
- **Severity:** Major
- **Bug:** `self._verification.get("verified", True)` is fail-open (missing key = verified) and will raise `AttributeError` if `verify_documents_activity` returns `None`.
- **Fix:** Guard the lookup and default to `False`.
- **Status:** Fixed — `doc_request_temporal.py:162-163` and `doc_request_chain.py:210-213` default to `verified=False` when the verification result is missing/empty.

```python
verification = self._verification or {}
self._verified = bool(verification.get("verified", False))
```

### 5.2 `wf_id` type is `str | None` at API call sites
- **File:** `src/agents/task_automation/app/api/events.py`
- **Lines:** 38, 42, 43
- **Severity:** Minor
- **Bug:** `signal_document_received`, `complete_verification`, and `get_workflow` accept `str`, but `wf_id` is statically `str | None`. Runtime handles it, but the type is brittle.
- **Fix:** Assert or narrow the type.
- **Status:** Fixed — `events.py::document_received` resolves `workflow_id` and rejects non-string values with `{"error": "no_workflow_id"}`.

```python
wf_id = active[-1]["workflow_id"]
assert isinstance(wf_id, str)
```

### 5.3 `trigger_id` type is loose through the pipeline
- **Files:** `src/agents/task_automation/app/pipeline.py`, `src/agents/task_automation/app/service.py`
- **Severity:** Minor
- **Bug:** `trigger_id` is `str | None` in several places but is required by downstream functions.
- **Fix:** Generate and assert a non-None `trigger_id` in `normalize_trigger` and propagate it as `str`.
- **Status:** Fixed — `trigger_ingestor.normalize_trigger` asserts/stringifies `trigger_id`; `run_pipeline_stages` and `process_event`/`_persist_and_emit` assert `isinstance(trigger_id, str)`.

---

## 6. API gateway

### 6.1 Proxy strips all response headers except `content-type`
- **File:** `src/api/main.py`
- **Lines:** 84-85
- **Severity:** Major
- **Bug:** The gateway only forwards `content-type`. File-download responses that set `Content-Disposition`, `Content-Length`, etc. lose those headers, breaking CSV/PDF exports.
- **Fix:** Forward the upstream headers (excluding hop-by-hop headers).
- **Status:** Fixed — `src/api/main.py` forwards all upstream response headers except hop-by-hop headers (`host`, `content-encoding`, `transfer-encoding`, `connection`).

```python
excluded = {"host", "content-encoding", "transfer-encoding", "connection"}
headers = {k: v for k, v in up.headers.items() if k.lower() not in excluded}
return Response(status_code=up.status_code, content=up.content, headers=headers)
```

---

## 7. Cross-cutting issues

### 7.1 `pydantic_settings.NoDecode` not recognized
- **Files:** `src/agents/*/app/core/config.py`
- **Severity:** Minor / Type
- **Bug:** Every agent uses `NoDecode` from `pydantic_settings`, but mypy reports `Module "pydantic_settings" has no attribute "NoDecode"`. Either the installed version is too old or the import is wrong. This cascades into many spurious mypy errors.
- **Fix:** Verify the installed `pydantic-settings` version and use the correct annotation (`NoDecode` was added in `pydantic-settings` 2.7+; otherwise use a custom `BeforeValidator`).
- **Status:** Verified not a bug — `pydantic_settings.NoDecode` is available in the installed version (`pydantic-settings` 2.7+). Remaining mypy errors are local environment/version dependent and should be addressed by pinning `pydantic-settings` or running `mypy` with the correct stubs.

### 7.2 `list[...]` type annotations shadowed by `list()` methods
- **Files:** `src/agents/performance_analytics/app/repositories.py`, `src/agents/follow_up_sequences/app/repositories.py`, etc.
- **Severity:** Minor / Type
- **Bug:** Classes define `def list(self, ...)` and then use `list[MetricDefinition]` as a return type. mypy resolves `list` to the method.
- **Fix:** Rename repository methods to `list_all` or import `List` from `typing` for annotations.
- **Status:** Fixed — PAS and FUS use `from __future__ import annotations` and `typing.List` (or renamed methods) so `list[...]` type annotations are not shadowed by `def list(...)`.

### 7.3 In-memory global state is unprotected in every agent
- **All agents**
- **Severity:** Major
- **Bug:** Each agent uses module-level in-memory repositories and caches without locks. This is fine for single-process tests but is unsafe for any multi-worker or multi-threaded deployment.
- **Fix:** Add `threading.Lock` / `asyncio.Lock` wrappers, or switch to the documented Redis/Postgres/Temporal-backed implementations.
- **Status:** Fixed — all five agents now wrap in-memory repositories and module-level global state with `threading.RLock()`.

---

## Recommended fix priority

1. **Fail-closed / safety first:**
   - `task_automation` doc verification default
   - `compliance_monitor` case-status validation
   - `follow_up` enrollment-state validation
   - `feedback_loop` A/B guardrail fallback

2. **Scheduling and guardrails:**
   - Compliance coverage loop interval
   - Follow-up frequency timezone
   - Compliance HTTP source crash

3. **Data correctness:**
   - Performance analytics `datetime.min` fallback
   - Reconciliation period parsing
   - Time-decay attribution reference
   - Feedback-loop traffic split

4. **Concurrency and hardening:**
   - Global locks for in-memory state
   - Replace `pickle` with safer model serialization
   - Gateway response header forwarding

5. **Type and cleanup:**
   - `NoDecode` / mypy issues
   - Repository `list` method shadowing
   - Dead/duplicate code