# Snowball Risk Register (Verified Audit)

Audit date: 2026-05-22  
Branch context: `copilot/update-md-files-again` (commit `ae610f5`)

## High-risk tickets

### HR-1: Over-coupled backend orchestration in `backend/app/main.py`

- **Status:** Done
- **Severity:** High
- **Closeout evidence (D.1 targeted gate):**
  `test_approve_cc_regression.py`, `test_run_once_hotfix.py`,
  `test_routing_policy.py`, `test_telegram_interactive.py`, and
  `test_candidate_date_filtering.py` passed on `snowball-md`.
- **Tracked non-blocking debt:** `test_run_orchestrator.py` remains stale
  against current `RunOrchestratorDependencies` contract and is tracked as
  follow-up cleanup.

### HR-2: Routing safety remains a multi-step contract

- **Status:** Done
- **Severity:** High
- **Closeout evidence:** queue-time and approve-send now both consume
  canonical `RoutingDecision` via service-layer evaluation
  (`evaluate_routing_for_email`), removing the split boolean gate path.
- **Validation evidence:** targeted regression suite passed after change:
  - `tests/test_approve_cc_regression.py`
  - `tests/test_run_once_hotfix.py`
  - `tests/test_routing_policy.py`
  - `tests/test_candidate_date_filtering.py`
  - `tests/test_telegram_interactive.py`
  - Result: `29 passed`

### HR-3: Phone-intelligence write path is dense and side-effect heavy

- **Status:** Partially Closed
- **Severity:** High
- **Closeout evidence:** canonical transaction-scoped workflow boundary now
  exists in `app/services/phone_intelligence_workflow_service.py`, with
  centralized idempotency checkpoints for recruiter number, employer number,
  review-card, and opportunity paths.
- **Implementation evidence:** premium helper delegation
  (`premium_numbers/service.py`, `premium_numbers/intelligence.py`) and
  candidate runtime capture path now route through one workflow boundary;
  `/premium-numbers/reextract/{id}` is routed through the workflow path via
  candidate runtime.
- **Validation evidence:** targeted regression run passed (`39 passed`) across
  premium + HR safety set:
  - `backend/tests/test_premium_numbers_extraction.py`
  - `backend/tests/test_premium_numbers_api.py`
  - `backend/tests/test_approve_cc_regression.py`
  - `backend/tests/test_run_once_hotfix.py`
  - `backend/tests/test_routing_policy.py`
  - `backend/tests/test_candidate_date_filtering.py`
  - `backend/tests/test_telegram_interactive.py`
- **Residual risk:** full-suite confidence is still limited by stale import
  debt in `backend/tests/test_phone_attribution.py`
  (`ModuleNotFoundError: No module named 'app.phone_attribution'`).
- **Debt classification:** stale test/import contract, orphaned test-only
  reference, non-blocking for HR-3 closure.
- **Runtime isolation evidence:** no current backend route/service/runtime
  workflow imports `app.phone_attribution`.

### HR-4: Runtime schema mutation depends on one additive helper

- **Status:** Done
- **Severity:** High
- **Closeout evidence:** migration ownership is wired through Alembic
  (`backend/alembic.ini`, `backend/alembic/env.py`,
  `backend/alembic/versions/*`) and startup enforces migration-state checks.
- **Implementation evidence:** `StartupService.startup()` calls
  `MigrationRuntimeService.ensure_schema_ready()`; deprecated runtime fallback
  execution path was removed from migration runtime service.
- **Strict-mode evidence:** with strict mode enabled, behind DB check fails
  with actionable error (`Database migration is required before startup. Run
  'alembic upgrade head'...`).
- **Validation evidence:** migration commands and targeted regressions passed
  in this branch phase:
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic current`
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic
    upgrade head`
  - `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic
    current`
  - `docker compose up --build` now runs Alembic upgrades before Uvicorn and
    reaches healthy startup in strict mode (`ALLOW_RUNTIME_SCHEMA_PATCH=false`).
  - backend logs confirm migration-first prestart + clean serving path (`GET /settings`,
    `GET /gmail/status`, `POST /automation/run-once`, and
    `POST /analytics/events/view` all `200 OK` in latest run window).
  - `pytest backend/tests/test_run_once_hotfix.py`
    `backend/tests/test_approve_cc_regression.py`
    `backend/tests/test_routing_policy.py`
    `backend/tests/test_candidate_date_filtering.py`
    `backend/tests/test_telegram_interactive.py`
    `backend/tests/test_premium_numbers_api.py` -> `33 passed`

### HR-5: Persisted feature flags overstate implemented automation

- **Status:** Done
- **Severity:** High
- **Closeout evidence:** persisted flags now activate runtime semantics in run orchestration:
  - `feature_auto_send` sends only current-run queued candidates.
  - `feature_retry_queue` retries failed queue and promotes sendable candidates.
- **Implementation evidence:** backend orchestration now emits structured
  automation counters in `AutomationRunResponse` (`auto_sent_count`,
  `auto_send_failed_count`, `retry_promoted_count`, `retry_skipped_count`) and
  frontend `Recent Runs` renders these as explicit automation chips.
- **UX evidence:** `Execution Control` now exposes both toggles with operator
  helper text, and `Recent Runs` shows per-run automation metrics.
- **Validation evidence:** runtime smoke matrix remained green
  (`POST /automation/run-once` = `200 OK` across all four flag combinations)
  with no duplicate-constraint or 500 regressions in latest logs.
- **Residual note:** embedding latency spikes remain intermittent and
  non-blocking; functional run outcomes stay successful.

## Medium-risk tickets

### MR-1: Frontend monolith and refresh coordination

- **Status:** Still Open
- **Severity:** Medium
- **Remaining issue:** `dashboard/src/App.tsx` still owns most workflows and
  post-mutation refresh orchestration.
- **Evidence:** central state/actions and `schedulePostMutationRefresh()` in App.
- **Recommended next action:** split candidate workflow, settings, premium
  numbers, and analytics into dedicated containers.

### MR-2: Policy profile duplication between frontend and backend

- **Status:** Still Open
- **Severity:** Medium
- **Remaining issue:** profile definitions are duplicated in two places.
- **Evidence:** backend `policy_service.policy_profiles()` and frontend
  `App.tsx` local `policyProfiles`.
- **Recommended next action:** move profile definitions to one shared source.

### MR-3: Hardcoded deployment defaults remain in source

- **Status:** Still Open
- **Severity:** Medium
- **Remaining issue:** permissive and project-specific defaults remain hardcoded.
- **Evidence:** `allow_origins=["*"]` in `main.py`; redirect URI and sheets
  defaults in `config.py`.
- **Recommended next action:** externalize sensitive defaults and tighten
  production defaults.

### MR-4: In-memory operational state is not durable

- **Status:** Still Open
- **Severity:** Medium
- **Remaining issue:** runtime sessions/caches reset on process restart.
- **Evidence:** `runtime_state` stores telegram auth sessions, pending input
  state, and runtime process state in memory.
- **Recommended next action:** persist critical operational state where restart
  continuity is required.

### MR-5: Validation confidence is weaker than it appears

- **Status:** Needs Re-test
- **Severity:** Medium
- **Remaining issue:** full-suite confidence remains incomplete in this session
  because validation tooling is not runnable in the current shell.
- **Evidence:** `cd backend && python -m pytest` failed with
  `/usr/bin/python: No module named pytest`; `cd dashboard && npm run lint`
  failed with `eslint: not found`; `cd dashboard && npm run build` failed with
  missing TS type definitions (`vite/client`, `node`) before app-level checks
  could run.
- **Recommended next action:** install backend/dashboard dependencies in the
  local environment, then re-run full backend and dashboard validation to
  re-baseline product confidence.

### MR-6: Local validation environment bootstrap is not enforced

- **Status:** Still Open
- **Severity:** Medium
- **Remaining issue:** repository validation commands can fail immediately when
  local dependency bootstrap is skipped, masking code-level signal.
- **Evidence:** backend pytest command requires `pytest` install; dashboard lint
  requires `eslint`; dashboard build requires TypeScript/Vite type dependencies
  available in `node_modules`.
- **Recommended next action:** add or harden setup/bootstrap guidance
  (or automation) so required backend and dashboard toolchains are installed
  before standard validation commands are executed.

## Low-risk tickets

### LR-1: Documentation drift pressure

- **Status:** Partially Closed
- **Severity:** Low
- **Remaining issue:** this audit improves consistency, but drift risk remains ongoing.
- **Evidence:** docs were aligned to current code in this branch, yet
  architecture remains fast-moving in `main.py`/`App.tsx`.
- **Recommended next action:** keep docs updates mandatory in behavioral PRs
  touching orchestration, routing, or queue logic.

### LR-2: Placeholder navigation affordances

- **Status:** Still Open
- **Severity:** Low
- **Remaining issue:** `New Campaign`, `Settings`, and `Help Center` buttons
  are presentational.
- **Evidence:** `Sidebar.tsx` renders buttons without routed feature actions.
- **Recommended next action:** either wire these actions or clearly mark them
  as disabled placeholders in UI.

### LR-3: Partial feature-module extraction

- **Status:** Still Open
- **Severity:** Low
- **Remaining issue:** query bucket is modularized; most dashboard behavior
  remains centralized.
- **Evidence:** `App.tsx` remains primary state container; `features/ai` is
  still a minimal stub.
- **Recommended next action:** continue module extraction by workflow domain.

## Priority reminder

1. protect routing/send safety and orchestration correctness,
2. reduce phone-intelligence write-path complexity,
3. replace or constrain runtime schema mutation,
4. reconcile feature-flag intent with actual runtime behavior,
5. then continue frontend decomposition.

```mermaid
stateDiagram-v2
    [*] --> Open
    Open --> PartiallyClosed: extraction/work completed\nbut verification incomplete
    Open --> Done: targeted behavior gate passes
    Open --> NeedsRetest: environment or stale-test blockers
    NeedsRetest --> Done: blockers fixed + suites pass
    PartiallyClosed --> Done: remaining criteria verified
```

Audit date: 2026-05-22  
Branch: copilot/update-md-files-again  
Evidence basis: both  
Verification limits: backend/dashboard validation is currently blocked by
missing local dependencies in this shell (`pytest`, `eslint`, and dashboard
type packages).
