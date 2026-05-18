# CODEJOB Test and Validation Matrix

Audit date: 2026-05-17  
Branch: `snowball-md`

## 1) Standard repo validation commands

### Backend (from `backend/`)
- `python -m pytest`
- `python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`
- `alembic upgrade head`
- `alembic current`

### Docker runtime (from repo root)
- `docker compose up --build`
- `docker compose logs backend --tail=100`

### Dashboard (from `dashboard/`)
- `npm run lint`
- `npm run build`
- `npm run test -- --run`

## 2) Command results in this audit session

- `cd backend; python -m pytest`
  - **Result:** failed during collection
  - **Exact failure:** `ModuleNotFoundError: No module named 'app.phone_attribution'` from `tests/test_phone_attribution.py`
  - **Blocker class:** stale test/import contract (orphaned test-only reference)
  - **Gate impact:** non-blocking for HR-3 functional closure when targeted premium + safety suite is green

- `cd backend; python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`
  - **Result:** passed
  - **Exact output:** `29 passed`
  - **Blocker class:** none

- `cd backend; DEBUG=false python -m pytest tests/test_hr5_feature_flags.py tests/test_hr5_duplicate_recovery.py tests/test_run_once_hotfix.py -q`
  - **Result:** passed
  - **Exact output:** `6 passed`
  - **Blocker class:** none

- `cd dashboard; npm run lint`
  - **Result:** failed
  - **Exact failure:** eslint rule failures in `App.tsx`, `QueryBucket.tsx`, and test files (including missing rule definition and hook/effect violations)
  - **Blocker class:** stale lint contract / incompatible local rule configuration

- `cd dashboard; npm run test -- --run`
  - **Result:** passed
  - **Exact output:** `7 passed files`, `27 passed tests`
  - **Blocker class:** none

- `cd dashboard; npm run build`
  - **Result:** failed
  - **Exact failure:** `EPERM` writing `.tsbuildinfo` under `node_modules/.tmp` plus TS6133 unused-variable errors
  - **Blocker class:** incompatible local runtime + type/lint debt

- `npx markdownlint-cli docs/architecture.md docs/context.md docs/data.md docs/design.md docs/features.md docs/hardcoded.md docs/problem-fix-log.md docs/snowball.md docs/testcases.md`
  - **Result:** failed to produce lint report
  - **Exact failure:** npm cache permission errors (`EPERM` on `npm-cache/_cacache/tmp/*`) and repeated CLI usage-only output in this shell
  - **Blocker class:** incompatible local runtime/tooling invocation

- `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic current`
  - **Result:** passed
  - **Exact output:** revision reported (pre-upgrade `20260518_0001`, post-upgrade `20260518_0002`)
  - **Blocker class:** none

- `cd backend; DEBUG=false DATABASE_URL=sqlite:///./data/codejob.db alembic upgrade head`
  - **Result:** passed
  - **Exact output:** `Running upgrade 20260518_0001 -> 20260518_0002`
  - **Blocker class:** none

- strict migration check (`ALLOW_RUNTIME_SCHEMA_PATCH=false` on behind DB)
  - **Result:** expected fail
  - **Exact output:** `RuntimeError: Database migration is required before startup. Run 'alembic upgrade head' in backend/ and restart.`
  - **Blocker class:** none (expected strict-mode behavior)

- `docker compose up --build`
  - **Result:** passed for strict-mode startup
  - **Exact output:** backend prestart runs Alembic upgrades to head, then Uvicorn starts
  - **Blocker class:** none

- `docker compose logs backend --tail=100`
  - **Result:** clean in latest run window
  - **Exact output:** post-start traffic shows `200 OK` on settings/status/analytics/run-once/number-review paths
  - **Blocker class:** none

## 3) HR-1 closeout gate mapping

| Behavior gate | Evidence | Outcome |
| --- | --- | --- |
| approve-send regression safety | `test_approve_cc_regression.py` | Pass |
| run-once orchestration behavior | `test_run_once_hotfix.py` | Pass |
| routing policy behavior | `test_routing_policy.py` | Pass |
| telegram interactive behavior | `test_telegram_interactive.py` | Pass |
| candidate date filtering behavior | `test_candidate_date_filtering.py` | Pass |

## 4) HR-5 runtime and API evidence

- `POST /automation/run-once` response now includes additive automation fields:
  - `auto_sent_count`
  - `auto_send_failed_count`
  - `retry_promoted_count`
  - `retry_skipped_count`
- Dashboard `Recent Runs` renders these fields as explicit automation chips (no detail-string parsing dependency).
- Runtime smoke matrix evidence (Docker + dashboard):
  - `auto_send=false, retry_queue=false` -> `200 OK`, no automation chips expected.
  - `auto_send=true, retry_queue=false` -> `200 OK`, auto-send chips present.
  - `auto_send=false, retry_queue=true` -> `200 OK`, retry chips present.
  - `auto_send=true, retry_queue=true` -> `200 OK`, both chip groups present.
- Regression safety evidence:
  - No `UNIQUE constraint failed: recruiter_emails.external_message_id` in latest validation windows.
  - No `500` on run-once endpoints during matrix execution.

## 5) Known stale/mismatched tests

- `test_phone_attribution.py` imports `app.phone_attribution`, which is not present in current backend code.
- Runtime isolation evidence: no current backend route/service/runtime workflow imports `app.phone_attribution`; this is test-only orphaned logic.
- `test_run_orchestrator.py` is stale against the current `RunOrchestratorDependencies` contract.

## 6) Reviewer attention

- Full backend pass cannot be claimed until stale test imports/contracts are fixed.
- Dashboard tests pass, but lint/build are currently red and should be treated as active debt.
- HR-4 strict-mode check is complete in this branch: migration-first startup contract is enforced and runtime patch fallback execution path is removed.

Evidence basis: both  
Verification limits: full backend and full frontend quality gates are not fully green due stale tests and lint/build blockers.
