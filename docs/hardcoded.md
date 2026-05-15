# Hardcoded Values and Constants Review

## 1) High-risk hardcoded values (should be moved/configured)

| Location | Value pattern | Risk |
|---|---|---|
| `backend/app/config.py` | default Google Sheets spreadsheet id | project-specific constant in source |
| `backend/app/config.py`, `phase0.py` | default signature name/phone/email fallbacks | profile identity values in code path |
| `backend/app/main.py` | permissive CORS `allow_origins=["*"]` | unsafe default for production |
| `backend/app/phase0.py` | default employer domains fallback | affects routing behavior if settings empty |

## 2) Medium-risk constants (centralize to reduce drift)

| Location | Constant family | Why centralize |
|---|---|---|
| `backend/app/main.py` + `dashboard/src/App.tsx` | policy profile defaults | frontend/backend parity risk |
| `backend/app/main.py` | event weights and ranges | analytics behavior coupling |
| `dashboard/src/App.tsx` | timeout values, bucket limits | UX behavior consistency and tuning |
| `phase0.py` | recruiter hints/skill keyword lists | business signal tuning should be explicit |

## 3) Expected hardcoded values (acceptable)

- Gmail/Sheets OAuth scopes and endpoint URLs in integration modules
- Default local dev URLs (`http://localhost:*`) in config/docker/dev frontend fallback
- Unit-test fixture literals in `backend/tests`

## 4) Hardcoded-value hotspots that affect behavior correctness

1. `backend/app/main.py::_default_policy`, `_policy_profiles`, `_normalize_policy`
2. `dashboard/src/App.tsx` policy profile objects and defaults
3. `backend/app/phase0.py` recruiter/employer heuristics
4. `backend/app/premium_numbers/extraction.py` relevance scoring thresholds and term lists

These should stay synchronized with tests and docs whenever changed.
