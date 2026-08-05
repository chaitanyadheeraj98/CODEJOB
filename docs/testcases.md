# CODEJOB Test and Validation Matrix

Audit date: 2026-08-05
Branch: `semantic-embeddings`

## 1 Standard repo validation commands

### Backend (from `backend/`)

- `python -m pytest`
- `python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`

### Dashboard (from `dashboard/`)

- `npm run lint`
- `npm run build`
- `npm run test -- --run`

## 2 Command results in this audit session

- `cd dashboard; npm run test`
  - **Result:** passed
  - **Exact output:** `24 passed files`, `78 passed tests`
  - **Blocker class:** none

- `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_premium_numbers_api.py tests/test_analytics_view_events.py tests/test_telegram_interactive.py`
  - **Result:** failed
  - **Exact output:** `30 passed`, `1 failed`
  - **Exact failure:** `test_reextract_overrides_name_without_to_when_contact_snippet_is_strong` expected `Rabbanis` and received `Samshritha Gangula`.
  - **Blocker class:** stale test or runtime regression

- `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_approve_cc_regression.py tests/test_candidate_screening_service.py tests/test_phase0_routing.py`
  - **Result:** stopped before completion
  - **Exact failure:** command stalled after `test_approve_cc_regression.py` began; no pass/fail result was captured.
  - **Blocker class:** incompatible local runtime timeout

- `npx --no-install markdownlint-cli <touched docs files>`
  - **Result:** failed before linting
  - **Exact failure:** `npx canceled due to missing packages and no YES option: ["markdownlint-cli@0.49.1"]`.
  - **Blocker class:** missing dependency

## 3 HR-1 closeout gate mapping

| Behavior gate | Evidence | Outcome |
| --- | --- | --- |
| approve-send regression safety | `test_approve_cc_regression.py` | Pass |
| run-once orchestration behavior | `test_run_once_hotfix.py` | Pass |
| routing policy behavior | `test_routing_policy.py` | Pass |
| telegram interactive behavior | `test_telegram_interactive.py` | Pass |
| candidate date filtering behavior | `test_candidate_date_filtering.py` | Pass |

## 4 Known stale/mismatched tests

- `test_phone_attribution.py` imports `app.phone_attribution`, which is not present in current backend code.
- `test_run_orchestrator.py` is stale against the current `RunOrchestratorDependencies` contract.

## 5 Reviewer attention

- No full backend pass can be claimed in this session.
- Dashboard tests passed. Markdownlint could not run because the CLI dependency is absent; dashboard lint and build were not run in this audit.

- Audit date: 2026-08-05
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: focused validation only; one backend premium-number failure and missing markdownlint dependency.
