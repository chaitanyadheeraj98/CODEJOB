# Hardcoded Values and Constants Review

## 1. Security- or deployment-sensitive defaults

| Location | Current hardcoded value | Why it matters |
|---|---|---|
| `backend/app/main.py` | `allow_origins=["*"]` | Permissive CORS is convenient for development but too broad for production exposure |
| `backend/app/config.py` | `google_redirect_uri = "http://localhost:8080/"` | Assumes local OAuth redirect unless overridden |
| `backend/app/config.py` | `google_sheets_tracking_spreadsheet_id = "1F73Iax75j2rGGb53GGqTmAkEK0o19nmg"` | Project-specific sheet target lives in source defaults |
| `backend/app/config.py` | `telegram_auth_ttl_minutes = 30` | Defines how long an in-memory Telegram auth session stays active |
| `backend/app/config.py` | `owner_id = "default-owner"` | Confirms the app is effectively single-owner by default |

## 2. Behavior-defining defaults in active workflow code

| Location | Current constant or rule | Branch impact |
|---|---|---|
| `backend/app/main.py` | auto-run interval clamped to `1..1440` minutes | Prevents invalid polling cadence |
| `backend/app/main.py` | default Gmail query `is:unread in:inbox recruiter` | Seeds first-run behavior |
| `backend/app/main.py` | default policy batch limit `20` and named profile presets | Controls run aggressiveness |
| `backend/app/phase0.py` | fallback signature name/phone/email and fallback draft template | Affects rules-only reply output |
| `backend/app/phase0.py` | employer-domain fallback heuristics and keyword lists | Influences routing and qualification heuristics |
| `backend/app/premium_numbers/intelligence.py` | fixed opportunity status set | Backend validation depends on exact strings |
| `backend/app/query_bucket/service.py` | saved query limit `10` | Defines query bucket UX capacity |
| `dashboard/src/App.tsx` | duplicate policy profile objects and UI timeout values | Risks frontend/backend drift |

## 3. Hardcoded values that are acceptable in the current branch

These are static values that are reasonable to keep local unless requirements change:

- OAuth scopes and API endpoint URLs for Gmail/Google/Telegram integrations
- test literals in backend and dashboard test files
- internal label names used by Gmail labeling (`assessment`, `Interview`, `screening`, etc.)
- opportunity status strings enforced by the application contract

## 4. Drift hotspots to watch

### Policy profile duplication

Policy definitions exist in both:

- `backend/app/main.py`
- `dashboard/src/App.tsx`

This is the most important behavior-drift hotspot because operators choose a profile in the UI while the backend enforces its own profile definitions.

### Runtime defaults bootstrapped into settings

At startup, `_ensure_default_settings()` writes fallback values into the `UserSettings` row when values are missing. This means hardcoded defaults are not only read at runtime; they are also persisted into the database.

### Prompt and heuristic tuning

Scoring keywords, recruiter/employer hints, and fallback messaging templates remain embedded in application code. Those choices directly affect qualification and routing outcomes.

## 5. Recommended handling priority

1. **High priority:** externalize security- and deployment-sensitive defaults (`CORS`, redirect URIs, sheet target IDs) where feasible.
2. **Medium priority:** establish a single source of truth for policy profile definitions.
3. **Medium priority:** document and periodically review heuristic keyword lists, default templates, and opportunity status values.
4. **Low priority:** leave stable integration constants local unless they need environment-specific overrides.

## 6. Current branch conclusion

Hardcoded values are not just cosmetic in CODEJOB. Several of them define live workflow behavior, operator experience, or deployment safety. Any change to these constants should be reflected in tests and documentation in the same branch update.
