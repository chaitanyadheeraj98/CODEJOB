# Problem Fix Log (Current Branch Verification)

Audit date: 2026-05-17  
Branch: `snowball-md`

## 1) Ticket verification summary

| Ticket | Status |
| --- | --- |
| HR-1 | Done |
| HR-2 | Done |
| HR-3 | Partially Closed |
| HR-4 | Done |
| HR-5 | Still Open |
| MR-1 | Still Open |
| MR-2 | Still Open |
| MR-3 | Still Open |
| MR-4 | Still Open |
| MR-5 | Needs Re-test |
| LR-1 | Partially Closed |
| LR-2 | Still Open |
| LR-3 | Still Open |

## 2) HR-1 verification details

**Status:** Done

**Remaining issue:** `backend/app/main.py` remains a large integration hub, but HR-1 closeout criteria were met via targeted regression coverage on extracted orchestration/telegram/routing paths.

**Evidence:**
- Service extraction is present (`startup_service.py`, `orchestration_service.py`, `routing_runtime_service.py`, `telegram_runtime_service.py`).
- `main.py` delegates sync/run/approve/reject/resolve flows into service facades.
- Targeted closeout suite passed on this branch:
  - `tests/test_approve_cc_regression.py`
  - `tests/test_run_once_hotfix.py`
  - `tests/test_routing_policy.py`
  - `tests/test_telegram_interactive.py`
  - `tests/test_candidate_date_filtering.py`
- Command evidence:
  - `cd backend; python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`
  - Result: `29 passed`

**Reviewer attention:**
- A full backend run is still blocked by stale test import (`tests/test_phone_attribution.py` imports missing `app.phone_attribution`).
- `test_run_orchestrator.py` still requires contract refresh against current dependency shape.

## 3) Other unresolved verification outcomes

### HR-2
- **Status:** Done
- **Closeout summary:** routing safety evaluation is now unified through canonical `RoutingDecision` usage in both queue-time and approve-send paths.
- **Evidence:** `RoutingRuntimeService.evaluate_routing_for_email(...)` now drives approve-send decisioning; orchestration no longer depends on a separate boolean-only routing gate.
- **Validation evidence:** `cd backend; python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_candidate_date_filtering.py tests/test_telegram_interactive.py` -> `29 passed`.
- **Residual note:** full backend suite remains blocked by stale `test_phone_attribution.py` import, but HR-2 targeted behavior gate is green.

### HR-3
- **Status:** Partially Closed
- **What changed:** phone-intelligence writes are now centralized behind a canonical transaction-scoped service boundary in `app/services/phone_intelligence_workflow_service.py`.
- **Implementation evidence:** premium extraction/intelligence helper paths now delegate into the workflow boundary; candidate runtime capture path and `/premium-numbers/reextract/{id}` route through the same workflow path.
- **Idempotency evidence:** workflow now uses explicit checkpoints for recruiter number, employer number, review-card existence, and opportunity existence before writes.
- **Validation command:**
  - `cd backend; python -m pytest tests/test_premium_numbers_extraction.py tests/test_premium_numbers_api.py tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_candidate_date_filtering.py tests/test_telegram_interactive.py`
  - **Result:** `39 passed`
- **Residual/non-blocking blocker:** full-suite confidence remains limited by separate stale import debt:
  - **Exact failure:** `ModuleNotFoundError: No module named 'app.phone_attribution'`
  - **Blocker class:** stale test/import contract (orphaned test-only reference)
  - **Runtime isolation evidence:** no current backend route/service/runtime workflow imports `app.phone_attribution`
  - **Gate impact:** non-blocking for HR-3 closure while targeted premium + safety suite remains green

### HR-4
- **Status:** Done
- **What changed:** Alembic migration ownership was introduced (`backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/20260518_0001_schema_baseline.py`, `backend/alembic/versions/20260518_0002_phone_bucket_merge.py`).
- **Implementation evidence:** startup now gates on migration state via `MigrationRuntimeService.ensure_schema_ready()` and no longer directly invokes `ensure_sqlite_phase0_columns()` in `StartupService`.
- **Strict-mode enforcement evidence:** deprecated runtime fallback execution branch was removed from `MigrationRuntimeService`; behind DB startup path now raises explicit migration-required error.
- **Validation evidence:**
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic current`
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic upgrade head`
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic current` (head reached: `20260518_0002`)
  - strict check (behind DB + fallback false) returns expected failure with actionable message.
  - `docker compose up --build` starts backend successfully in strict mode after
    prestart Alembic upgrades (`-> 20260518_0001`, `20260518_0001 -> 20260518_0002`)
    and then `Application startup complete`.

### HR-5
- **Remaining issue:** feature flags imply behavior that is not implemented end-to-end.
- **Evidence:** persisted `feature_auto_send` and `feature_retry_queue` without runtime workers.
- **Recommended next action:** implement semantics or deprecate flags.

### MR-5
- **Remaining issue:** full validation confidence is still incomplete.
- **Evidence:** full backend suite collection fails due stale import; dashboard lint/build fail with current rule/type constraints.
- **Recommended next action:** keep `phone_attribution` as non-blocking stale-contract debt for current ticket closure gates, and track a separate follow-up decision (remove stale test vs restore compatibility shim) before full-suite re-baseline.

## 4) Validation command results from this audit session

- `cd backend; python -m pytest`
  - **Result:** failed during collection
  - **Exact failure:** `ModuleNotFoundError: No module named 'app.phone_attribution'`
  - **Blocker class:** stale test/import contract (orphaned test-only reference)

- `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic current`
  - **Result:** passed
  - **Exact output:** revision reported (pre-upgrade `20260518_0001`, post-upgrade `20260518_0002`)
  - **Blocker class:** none

- `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic upgrade head`
  - **Result:** passed
  - **Exact output:** upgrade `20260518_0001 -> 20260518_0002`
  - **Blocker class:** none

- `docker compose up --build`
  - **Result:** passed for backend startup path in strict mode
  - **Exact output:** Alembic upgrades run first, then Uvicorn starts successfully
  - **Blocker class:** none

- `docker compose logs backend --tail=100`
  - **Result:** clean in latest run window
  - **Exact output:** serving traffic with `200 OK` for `/settings`, `/gmail/status`,
    `/automation/run-once`, `/analytics/events/view`, and number-review/opportunity
    endpoints
  - **Blocker class:** none

- `cd backend; python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`
  - **Result:** passed (`29 passed`)
  - **Blocker class:** none

- `cd dashboard; npm run lint`
  - **Result:** failed
  - **Exact failure:** eslint rule violations (`react-refresh/only-export-components`, missing rule `react/no-array-index-key`, and hook/set-state issues)
  - **Blocker class:** incompatible local runtime/tooling rules + stale lint contract

- `cd dashboard; npm run test -- --run`
  - **Result:** passed (`7 files, 27 tests`)
  - **Blocker class:** none

- `cd dashboard; npm run build`
  - **Result:** failed
  - **Exact failure:** TypeScript write permission (`EPERM` on `.tsbuildinfo`) plus TS6133 unused variable errors
  - **Blocker class:** incompatible local runtime + stale type/lint debt

## 5) Current branch conclusion

HR-1 and HR-2 closure are supported by targeted behavior tests on this branch. Broader validation debt remains open (MR-5) and should not be interpreted as product-wide green status.

Evidence basis: both  
Verification limits: full backend suite blocked by stale test import; dashboard lint/build blocked by rule/type/runtime constraints.
