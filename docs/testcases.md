<!-- markdownlint-configure-file {"MD013": false} -->

# CODEJOB Test and Validation Matrix

Audit date: 2026-08-14
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

- `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_mcp_server.py tests/test_chat_service.py tests/test_chat_routes.py tests/test_migration_0015.py -q`
  - **Result:** passed
  - **Exact output:** `6 passed`
  - **Blocker class:** none

- Affected backend batch covering AI status, inbox, schemas, startup lifespan, chat, MCP, and migration behavior
  - **Result:** passed
  - **Exact output:** `19 passed`
  - **Blocker class:** none

- `cd dashboard; npm run test -- --run`
  - **Result:** passed
  - **Exact output:** `29 passed files`, `89 passed tests`
  - **Blocker class:** none

- Clean Docker backend/dashboard builds and live chat validation
  - **Result:** passed
  - **Exact behavior:** migration reached `20260814_0015`; `/mcp` listed eight tools; `search_candidates` fired through LangGraph; `gemma4:31b-cloud` streamed an SSE answer; user, tool, and assistant rows persisted.
  - **Blocker class:** none

- `npx --no-install markdownlint-cli2 docs/architecture.md docs/features.md docs/data.md docs/testcases.md docs/mermaids-features.md`
  - **Result:** passed
  - **Exact output:** `Summary: 0 error(s)`
  - **Blocker class:** none

- Full backend suite
  - **Result:** stopped during collection
  - **Exact failure:** `tests/test_phone_attribution.py` imports missing module `app.phone_attribution`.
  - **Blocker class:** known stale test

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
- The chat implementation passed focused backend tests, the full dashboard suite, container builds, migration checks, and a live tool-backed answer.
- Host dashboard build remains affected by the known `.tsbuildinfo` permission issue; the clean Docker build passed.

- Audit date: 2026-08-14
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: the unrelated stale backend import prevents an all-green broad backend-suite claim.
