<!-- markdownlint-configure-file {"MD013": false} -->

# CODEJOB Architecture (Current Branch)

## System Shape

- Backend: FastAPI route composition remains centered in `backend/app/main.py`.
- Frontend: React + TypeScript workflow state and API calls remain concentrated in `dashboard/src/App.tsx`.
- Persistence: SQLAlchemy models are declared in `backend/app/models.py`; route handlers query and mutate those records directly or through services.
- Integrations: Gmail, optional AI and semantic providers, Telegram, Redis Queue, Nvoids, and optional Google Sheets behavior are configuration-dependent.

## Current Runtime Ownership

| Area | Current runtime owner | Evidence |
| --- | --- | --- |
| API composition | `backend/app/main.py` | FastAPI decorators declare candidate, settings, Gmail, queue, analytics, premium-number, and external-feed routes. |
| Candidate review actions | `app/services/orchestration_service.py` | `approve_and_send()`, `reject_candidate()`, and `resolve_recipients()` delegate through `_get_orchestration_service()`. |
| Intake and routing | `backend/app/main.py:ingest_email` | `POST /phase0/emails/ingest` parses, filters, screens, scores, and routes an email. |
| Automation | `backend/app/main.py:_run_automation` | `POST /automation/run-once` calls `_run_automation()`; queue routes enqueue related background work. |
| Premium-number operations | `backend/app/main.py` and `app/premium_numbers/*` | Re-extraction, review classification, number buckets, and opportunity endpoints use `PremiumNumberLead`, `NumberReviewQueue`, `RecruiterNumber`, `EmployerNumber`, and `RecruiterOpportunity`. |
| Frontend workflow container | `dashboard/src/App.tsx` | Dashboard tests cover settings bootstrap, execution control, queue, analytics, recent-runs, and review views. |

## Intended and Current Runtime

- Intended architecture: domain-specific route and UI modules own individual workflows.
- Current runtime: `main.py` and `App.tsx` remain central hubs. `RecruiterEmail` is a broad workflow record, and some AI or embedding status is process-memory state.

## Active Coupling Hotspots

- Candidate routing and approval are separate gates: intake assigns a queue state, while `POST /candidates/{email_id}/approve-send` invokes the send gate.
- Settings flags influence automation, AI, semantic scoring, Nvoids, auto-send, and retry behavior through centralized runtime wiring.
- Integration outcomes depend on credentials, configured feature flags, and external services; source inspection alone cannot establish their operational availability.

- Audit date: 2026-08-05
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: focused backend verification has one premium-number regression; Gmail, Nvoids, Telegram, queue, and Sheets integrations were not exercised against live services.
