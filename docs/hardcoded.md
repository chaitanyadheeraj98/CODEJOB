# Hardcoded Values and Constants Review

Audit date: 2026-05-18  
Branch: `snowball-md`

## 1) Deployment-sensitive defaults

| Location | Hardcoded default | Impact |
| --- | --- | --- |
| `backend/app/main.py` | `allow_origins=["*"]` | overly broad CORS default |
| `backend/app/config.py` | `google_redirect_uri = "http://localhost:8080/"` | local OAuth default |
| `backend/app/config.py` | spreadsheet id default value | project-specific target baked in |
| `backend/app/config.py` | `owner_id = "default-owner"` | single-owner default behavior |
| `backend/app/config.py` | `telegram_auth_ttl_minutes = 30` | in-memory auth session duration |

## 2) Workflow-affecting constants

| Location | Constant/rule | Effect |
| --- | --- | --- |
| `phase0.py` | fallback template + signature defaults | shapes rules-only draft content |
| `phase0.py` | heuristic keyword/hint sets | affects parse/scoring/routing |
| `main.py` / `policy_service.py` | policy defaults and profile values | controls run behavior |
| `query_bucket/service.py` | query limit `10` | query bucket cap |
| `premium_numbers/intelligence.py` | fixed opportunity status set | API validation contract |

## 3) Drift hotspots

1. **Policy profile duplication** in backend and frontend.
2. **Settings bootstrap defaults** persisted into DB on missing values.
3. **Heuristic-heavy parsing/routing defaults** in phase0.
4. **Execution-control operator clarity**:
   `feature_auto_send` and `feature_retry_queue` are no longer persisted-only;
   they now drive live post-orchestration behavior and should stay aligned with
   UI helper text and run-response counters.

## 4) Current conclusion

Hardcoded values in this branch are not only cosmetic; several directly influence runtime behavior, deployment posture, and operator expectations. Any behavior change to these constants should be paired with test updates and docs updates.

Evidence basis: code inspection  
Verification limits: behavior impact inferred from config/runtime usage; no dedicated constant-only regression suite.
