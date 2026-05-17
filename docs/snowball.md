# Snowball Risk Register (Verified Audit)

Audit date: 2026-05-17  
Branch context: `snowball-md`

## High-risk tickets

### HR-1: Over-coupled backend orchestration in `backend/app/main.py`
- **Status:** Done
- **Closeout evidence (D.1 targeted gate):** `test_approve_cc_regression.py`, `test_run_once_hotfix.py`, `test_routing_policy.py`, `test_telegram_interactive.py`, and `test_candidate_date_filtering.py` passed on `snowball-md`.
- **Tracked non-blocking debt:** `test_run_orchestrator.py` remains stale against current `RunOrchestratorDependencies` contract and is tracked as follow-up cleanup.

### HR-2: Routing safety remains a multi-step contract
- **Status:** Still Open
- **Remaining issue:** routing is evaluated in orchestration and re-checked at approve-send time, so drift risk remains.
- **Evidence:** `RunOrchestrator` decides queue state; `OrchestrationService.approve_send()` separately enforces `routing_is_sendable()`.
- **Recommended next action:** consolidate sendability decisions behind one canonical contract used by both queueing and approval.

### HR-3: Phone-intelligence write path is dense and side-effect heavy
- **Status:** Still Open
- **Remaining issue:** extraction, intelligence classification, review queue, recruiter/employer buckets, and opportunity creation still span multiple helpers with multiple writes.
- **Evidence:** `premium_numbers/service.py` + `premium_numbers/intelligence.py` + candidate runtime capture path.
- **Recommended next action:** introduce a single transaction-scoped phone-intelligence workflow boundary with explicit idempotency points.

### HR-4: Runtime schema mutation depends on one additive helper
- **Status:** Still Open
- **Remaining issue:** startup still performs many SQLite `ALTER TABLE`/`CREATE INDEX` migrations at runtime.
- **Evidence:** `StartupService.startup()` calls `ensure_sqlite_phase0_columns()` in `app/db.py`.
- **Recommended next action:** replace runtime additive migration behavior with explicit schema migration tooling ownership.

### HR-5: Persisted feature flags overstate implemented automation
- **Status:** Still Open
- **Remaining issue:** `feature_auto_send` and `feature_retry_queue` are persisted but do not activate dedicated execution flows.
- **Evidence:** flags exist in models/schemas/settings update path; no runtime auto-send or retry worker path uses them.
- **Recommended next action:** either implement execution semantics or remove/deprecate operator-facing exposure.

## Medium-risk tickets

### MR-1: Frontend monolith and refresh coordination
- **Status:** Still Open
- **Remaining issue:** `dashboard/src/App.tsx` still owns most workflows and post-mutation refresh orchestration.
- **Evidence:** central state/actions and `schedulePostMutationRefresh()` in App.
- **Recommended next action:** split candidate workflow, settings, premium numbers, and analytics into dedicated containers.

### MR-2: Policy profile duplication between frontend and backend
- **Status:** Still Open
- **Remaining issue:** profile definitions are duplicated in two places.
- **Evidence:** backend `policy_service.policy_profiles()` and frontend `App.tsx` local `policyProfiles`.
- **Recommended next action:** move profile definitions to one shared source.

### MR-3: Hardcoded deployment defaults remain in source
- **Status:** Still Open
- **Remaining issue:** permissive and project-specific defaults remain hardcoded.
- **Evidence:** `allow_origins=["*"]` in `main.py`; redirect URI and sheets defaults in `config.py`.
- **Recommended next action:** externalize sensitive defaults and tighten production defaults.

### MR-4: In-memory operational state is not durable
- **Status:** Still Open
- **Remaining issue:** runtime sessions/caches reset on process restart.
- **Evidence:** `runtime_state` stores telegram auth sessions, pending input state, and runtime process state in memory.
- **Recommended next action:** persist critical operational state where restart continuity is required.

### MR-5: Validation confidence is weaker than it appears
- **Status:** Needs Re-test
- **Remaining issue:** key suites were not executable in this environment; stale tests are present.
- **Evidence:** `python -m pytest` failed (`No module named pytest`) in prior docs branch context; `test_run_orchestrator.py` is stale vs current dependency contract; `test_phone_attribution.py` imports missing module `app.phone_attribution`.
- **Recommended next action:** provision dependencies and re-run targeted backend/frontend suites after stale tests are fixed.

## Low-risk tickets

### LR-1: Documentation drift pressure
- **Status:** Partially Closed
- **Remaining issue:** this audit improves consistency, but drift risk remains ongoing.
- **Evidence:** docs were aligned to current code in this branch, yet architecture remains fast-moving in `main.py`/`App.tsx`.
- **Recommended next action:** keep docs updates mandatory in behavioral PRs touching orchestration, routing, or queue logic.

### LR-2: Placeholder navigation affordances
- **Status:** Still Open
- **Remaining issue:** `New Campaign`, `Settings`, and `Help Center` buttons are presentational.
- **Evidence:** `Sidebar.tsx` renders buttons without routed feature actions.
- **Recommended next action:** either wire these actions or clearly mark them as disabled placeholders in UI.

### LR-3: Partial feature-module extraction
- **Status:** Still Open
- **Remaining issue:** query bucket is modularized; most dashboard behavior remains centralized.
- **Evidence:** `App.tsx` remains primary state container; `features/ai` is still a minimal stub.
- **Recommended next action:** continue module extraction by workflow domain.

## Priority reminder
1. protect routing/send safety and orchestration correctness,
2. reduce phone-intelligence write-path complexity,
3. replace or constrain runtime schema mutation,
4. reconcile feature-flag intent with actual runtime behavior,
5. then continue frontend decomposition.
