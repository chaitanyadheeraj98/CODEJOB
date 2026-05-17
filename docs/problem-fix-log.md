# Problem Fix Log (Current Branch Verification)

Audit date: 2026-05-17  
Branch: `copilot/update-markdown-docs-audit`

## 1) Ticket verification summary

| Ticket | Status |
|---|---|
| HR-1 | Partially Closed |
| HR-2 | Still Open |
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

**Status:** Partially Closed

**Remaining issue:** `backend/app/main.py` is still the central integration module and coupling hotspot despite meaningful extraction to services.

**Evidence:**
- Service extraction is real (`startup_service.py`, `orchestration_service.py`, `routing_runtime_service.py`, `telegram_runtime_service.py`).
- `main.py` still hosts all route declarations and service wiring glue for orchestration, telegram runtime entry points, analytics wrappers, and startup/lifecycle integration.
- Prior closeout test files still exist: `test_approve_cc_regression.py`, `test_run_once_hotfix.py`, `test_routing_policy.py`, `test_telegram_interactive.py`, `test_candidate_date_filtering.py`.
- This environment could not execute backend tests (`python -m pytest` failed: `No module named pytest`), so pass-state claims could not be revalidated here.
- `test_run_orchestrator.py` remains stale vs current `RunOrchestratorDependencies` shape and is not non-impactful debt only; it weakens confidence in orchestrator regression evidence.

**Recommended next action:**
1. Further split route/lifecycle glue from orchestration assembly in `main.py`.
2. Update stale orchestrator tests to current dependency contract.
3. Re-run targeted regression suite in a provisioned test environment before marking HR-1 verified closed.

## 3) Other unresolved verification outcomes

### HR-2
- **Remaining issue:** routing correctness still depends on multi-step contracts.
- **Evidence:** queue-time routing + approve-time recheck (`routing_is_sendable`).
- **Recommended next action:** enforce one canonical sendability contract.

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
- **Remaining issue:** validation confidence gap remains.
- **Evidence:** missing env tooling and stale tests (`test_run_orchestrator.py`, missing `app.phone_attribution`).
- **Recommended next action:** fix stale tests and re-run full suites in provisioned CI/runtime.

## 4) Validation command results from this audit session

- Backend: `cd backend && python -m pytest` → failed (`No module named pytest`).
- Dashboard lint: `cd dashboard && npm run lint` → failed (`eslint: not found`).
- Dashboard test: `cd dashboard && npm run test -- --run` → failed (`vitest: not found`).
- Dashboard build: `cd dashboard && npm run build` → failed (`Cannot find type definition file for 'vite/client'` and `'node'`).

## 5) Current branch conclusion

Documentation now reflects current code behavior more accurately, but high-risk orchestration/routing/validation debt is still active. Treat this branch as **documented current state**, not as proof that all architectural debt is resolved.
