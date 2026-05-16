# CODEJOB Test and Validation Matrix (Current Branch)

## 1) Backend test modules present

`backend/tests/` currently includes coverage for:

- routing/parsing: `test_phase0_routing.py`, `test_routing_policy.py`, `test_candidate_date_filtering.py`
- run orchestration: `test_run_orchestrator.py`, `test_run_once_hotfix.py`
- premium-number intelligence: `test_premium_numbers_extraction.py`, `test_premium_numbers_api.py`
- gmail labeling: `test_gmail_labeling_rules.py`, `test_gmail_labeling_service.py`, `test_gmail_labeling_api.py`
- cold-call workflows: `test_cold_call_service.py`, `test_cold_call_api.py`
- query bucket behavior: `test_query_bucket_service.py`
- draft/prompt quality: `test_prompting.py`, `test_draft_formatting.py`, `test_draft_quality.py`
- resume/semantic: `test_resume_context_attribution.py`, `test_semantic_ranking.py`
- analytics/telegram: `test_productivity_trend.py`, `test_telegram_interactive.py`
- API regressions/schemas: `test_approve_cc_regression.py`, `test_sheets_tracking.py`, `test_schemas.py`

## 2) Frontend checks

From `dashboard/package.json`:
- `npm run lint`
- `npm run build`
- `npm run test`

## 3) Backend command

From repo setup and tests:
- `python -m pytest` (run from `backend` directory)

## 4) High-priority regression scenarios

1. run-once queue accounting (`matched/queued/skipped/failed`)
2. approve-send safety gate (routing + recipients + draft + resume)
3. failed mapping resolution path (`resolve-recipients`)
4. number-review manual classification and dedupe behavior
5. bucket swap endpoints
6. recruiter opportunity updates and cold-call script generation
7. Gmail labeling preview and apply behavior

## 5) Known branch mismatch

- `backend/tests/test_phone_attribution.py` imports `app.phone_attribution`, which is not present under `backend/app` in this branch.
