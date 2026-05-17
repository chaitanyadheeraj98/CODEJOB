# Problem Fix Log (Current Branch Verification)

Audit date: 2026-05-17  
Branch: `snowball-md`

## 1) Ticket verification summary

| Ticket | Status |
| --- | --- |
| HR-1 | Done |
| HR-2 | Done |
| HR-3 | Still Open |
| HR-4 | Still Open |
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
- **Remaining issue:** phone-intelligence path remains multi-write and side-effect dense.
- **Evidence:** extraction + intelligence + queue + bucket/opportunity writes span multiple modules.
- **Recommended next action:** consolidate into a transaction-aware service boundary.

### HR-4
- **Remaining issue:** runtime schema patching remains startup migration mechanism.
- **Evidence:** `ensure_sqlite_phase0_columns()` called at startup.
- **Recommended next action:** shift schema evolution ownership to explicit migrations.

### HR-5
- **Remaining issue:** feature flags imply behavior that is not implemented end-to-end.
- **Evidence:** persisted `feature_auto_send` and `feature_retry_queue` without runtime workers.
- **Recommended next action:** implement semantics or deprecate flags.

### MR-5
- **Remaining issue:** full validation confidence is still incomplete.
- **Evidence:** full backend suite collection fails due stale import; dashboard lint/build fail with current rule/type constraints.
- **Recommended next action:** fix stale tests and lint/build blockers, then re-run full suites.

## 4) Validation command results from this audit session

- `cd backend; python -m pytest`
  - **Result:** failed during collection
  - **Exact failure:** `ModuleNotFoundError: No module named 'app.phone_attribution'`
  - **Blocker class:** stale test

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
