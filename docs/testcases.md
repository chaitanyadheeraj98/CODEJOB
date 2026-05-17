# CODEJOB Test and Validation Matrix

Audit date: 2026-05-17  
Branch: `copilot/update-markdown-docs-audit`

## 1) Standard repo validation commands

### Backend (from `backend/`)
- `python -m pytest`

### Dashboard (from `dashboard/`)
- `npm run lint`
- `npm run build`
- `npm run test`

## 2) Command results in this audit session

- `cd backend && python -m pytest`
  - **Result:** failed
  - **Error:** `/usr/bin/python: No module named pytest`

- `cd dashboard && npm run lint`
  - **Result:** failed
  - **Error:** `eslint: not found`

- `cd dashboard && npm run test -- --run`
  - **Result:** failed
  - **Error:** `vitest: not found`

- `cd dashboard && npm run build`
  - **Result:** failed
  - **Error:** missing TS type definitions (`vite/client`, `node`)

These failures were environment/dependency issues in this shell, not docs changes.

## 3) Key backend test modules present

- Routing/parsing: `test_phase0_routing.py`, `test_routing_policy.py`, `test_candidate_date_filtering.py`
- Orchestration: `test_run_once_hotfix.py`, `test_run_orchestrator.py`
- Approval/regression: `test_approve_cc_regression.py`
- Telegram: `test_telegram_interactive.py`
- Labeling: `test_gmail_labeling_rules.py`, `test_gmail_labeling_service.py`, `test_gmail_labeling_api.py`
- Premium/phone: `test_premium_numbers_extraction.py`, `test_premium_numbers_api.py`, `test_phone_attribution.py`
- Cold-call: `test_cold_call_service.py`, `test_cold_call_api.py`
- Query bucket: `test_query_bucket_service.py`
- Schemas/other: `test_schemas.py`, `test_sheets_tracking.py`, `test_productivity_trend.py`, others

## 4) Known stale/mismatched tests

- `test_run_orchestrator.py` is stale against the current `RunOrchestratorDependencies` contract.
- `test_phone_attribution.py` imports `app.phone_attribution`, which is not present in current backend code.

## 5) Practical validation guidance

Before relying on "green" confidence for high-risk changes:
1. provision backend/frontend dependencies,
2. repair stale tests,
3. re-run targeted routing/orchestration/approval/telegram/premium regression suites.
