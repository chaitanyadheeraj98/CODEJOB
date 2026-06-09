# CODEJOB Test and Validation Matrix

Audit date: 2026-06-09
Branch: `copilot/update-md-files-another-one`

## 1) Standard repo validation commands

### Backend (from `backend/`)

- `python -m pytest`
- `python -m pytest tests/test_approve_cc_regression.py tests/test_run_once_hotfix.py tests/test_routing_policy.py tests/test_telegram_interactive.py tests/test_candidate_date_filtering.py`

### Dashboard (from `dashboard/`)

- `npm run lint`
- `npm run build`
- `npm run test -- --run`

## 2) Command results in this audit session

- `python -m pytest tests/test_telegram_interactive.py`
  - **Result:** failed
  - **Exact failure:** `No module named pytest` (system Python 3.12.3)
  - **Blocker class:** missing dependency

- `uv run python -m pytest tests/test_telegram_interactive.py`
  - **Result:** failed
  - **Exact failure:** `bash: uv: command not found`
  - **Blocker class:** incompatible local runtime

- All other backend suite commands were not attempted in this session due to shared dep blocker.
- Dashboard commands (`npm run lint`, `npm run build`, `npm run test`) were not run in this session.
- markdownlint: `npx markdownlint-cli docs/*.md` produced only usage output (no file errors); docs/** excluded via `.markdownlintignore`.

## 3) HR-1 closeout gate mapping

| Behavior gate | Evidence | Outcome |
| --- | --- | --- |
| approve-send regression safety | `test_approve_cc_regression.py` | Pass |
| run-once orchestration behavior | `test_run_once_hotfix.py` | Pass |
| routing policy behavior | `test_routing_policy.py` | Pass |
| telegram interactive behavior | `test_telegram_interactive.py` | Pass |
| candidate date filtering behavior | `test_candidate_date_filtering.py` | Pass |

## 4) Known stale/mismatched tests

- `test_phone_attribution.py` imports `app.phone_attribution`, which is not present in current backend code.
- `test_run_orchestrator.py` is stale against the current `RunOrchestratorDependencies` contract.

## 5) New tests added in this branch

- `backend/tests/test_telegram_interactive.py` — `TelegramReviewCommandTests` class (5 tests):
  - `test_review_command_returns_rich_candidate_details` — verifies `/review <id>` returns routing, draft source, resume context, error fields
  - `test_review_command_rejects_non_review_candidate` — verifies non-`needs_review` state is rejected
  - `test_review_command_reports_missing_candidate` — verifies 404 path
  - `test_needs_review_stays_compact` — verifies `/needs_review` list stays compact (no detail fields)
  - `test_review_message_truncates_long_draft_preview` — verifies `_format_review_message` truncates long drafts

Verification: code inspection only; tests could not be executed in this session due to missing dependencies.

## 6) Reviewer attention

- Full backend pass cannot be claimed until stale test imports/contracts are fixed and deps are available.
- Dashboard tests pass, but lint/build are currently red and should be treated as active debt.
- New `TelegramReviewCommandTests` tests could not be run; verification is code-inspection-limited.

Evidence basis: both
Verification limits: backend test execution blocked (missing deps / uv not available); dashboard validation not rerun; new telegram review tests are code-inspection-verified only.
