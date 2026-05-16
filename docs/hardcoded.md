# Hardcoded Values and Constants Review

## 1) High-risk hardcoded values

| Location | Value pattern | Risk |
|---|---|---|
| `backend/app/config.py` | default Sheets spreadsheet id and integration defaults | deployment-specific constants in source |
| `backend/app/main.py` | `allow_origins=["*"]` CORS default | unsafe for production without env restriction |
| `backend/app/phase0.py` | default employer-domain/signature fallbacks | behavior drift when settings are incomplete |
| `backend/app/cold_call/service.py` | fallback self-profile claims in script text | identity/truthfulness drift if profile changes |

## 2) Medium-risk constant clusters

| Location | Constant family | Why centralize |
|---|---|---|
| `backend/app/main.py` + `dashboard/src/App.tsx` | policy profile defaults | frontend/backend parity risk |
| `backend/app/main.py` | event weights/range buckets | analytics interpretation coupling |
| `dashboard/src/App.tsx` | UI polling timeouts and refresh intervals | UX consistency/tuning drift |
| `backend/app/premium_numbers/*` | relevance thresholds and heuristics | signal-quality tuning drift |
| `backend/app/gmail_labeling/rules.py` | label rule mappings | inbox categorization consistency risk |

## 3) Expected hardcoded values (acceptable)

- OAuth scopes and provider endpoints
- localhost dev URLs
- test fixture literals in `backend/tests`

## 4) Hardcoded hotspots requiring docs/tests sync

1. policy defaults and normalization behavior
2. routing and qualification thresholds
3. premium-number scoring heuristics
4. Gmail labeling decision rules
5. cold-call fallback script constraints
