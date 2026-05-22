# Hardcoded Values and Constants Review

## Deployment-Sensitive Defaults

| Location | Value | Impact |
| --- | --- | --- |
| `backend/app/main.py` | `allow_origins=["*"]` | permissive CORS by default |
| `backend/app/config.py` | `google_redirect_uri` default localhost value | local-only default may be incorrect in deployment |
| `backend/app/config.py` | owner/project fallback defaults | single-owner assumptions unless overridden |

## Workflow Constants with Behavioral Impact

| Location | Constant class | Runtime effect |
| --- | --- | --- |
| `backend/app/main.py` | analytics event-weight map and range options | impacts trend scoring and bucketed reports |
| `backend/app/phase0.py` | parsing/filter/routing heuristics | affects queue qualification and routing confidence |
| `backend/app/query_bucket/service.py` | saved-query constraints | enforces dedupe and query limit behavior |
| `backend/app/premium_numbers/intelligence.py` | opportunity status values | endpoint validation and UI contract |

## Active Drift Risks

- Frontend/backend policy defaults can diverge if maintained separately.
- Hardcoded permissive values can leak from local defaults into non-local environments.
- Heuristic constants change behavior without obvious external contract changes.

Mermaid not needed: this update is a constant inventory and risk categorization.

- Audit date: 2026-05-22
- Branch: external-recruiter-feed-ingestion
- Evidence basis: code inspection
- Verification limits: no dedicated constant-only regression suite was run in this session.

