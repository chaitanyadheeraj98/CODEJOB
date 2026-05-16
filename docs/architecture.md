# CODEJOB Architecture (Current Branch)

Branch snapshot: `copilot/update-docs-files-except-agent-context`  
Recent milestone tags:
- `milestone-querybucket-remap-hooks-2026-05-15`
- `milestone-recruiter-opportunity-phone-2026-05-15`

## 1) System shape

CODEJOB is a **FastAPI backend + React (Vite) dashboard** system with SQLite persistence.

- Backend: `backend/app/main.py` and domain packages (`automation`, `routing`, `premium_numbers`, `gmail_labeling`, `cold_call`, `query_bucket`)
- Frontend: `dashboard/src/App.tsx` with feature helpers
- Storage: SQLite via SQLAlchemy models in `backend/app/models.py`
- Integrations: Gmail API, Gmail OAuth, optional Google Sheets tracking, Telegram bot operations, DeepSeek/OpenAI-compatible APIs

## 2) Runtime architecture

### Backend runtime modules

- `backend/app/main.py`
  - App lifecycle, endpoint surface, orchestration helpers, analytics, Telegram handlers
- `backend/app/automation/run_orchestrator.py`
  - Queue-state pipeline for `POST /automation/run-once`
- `backend/app/routing/*`
  - Routing policy and routing decision adapters
- `backend/app/premium_numbers/*`
  - Extraction, normalization, intelligence classification, dedupe-aware writes
- `backend/app/gmail_labeling/*`
  - Label rule engine + AI fallback + Gmail label apply/sync
- `backend/app/cold_call/*`
  - Recruiter-opportunity cold-call script generation + truthfulness sanitization
- `backend/app/query_bucket/*`
  - Saved-query sanitization and dedupe

### Frontend runtime modules

- `dashboard/src/App.tsx`
  - Primary UI state machine and API wiring
- `dashboard/src/components/Sidebar.tsx`
  - Navigation and counters
- `dashboard/src/candidateBuckets.ts`
  - Candidate paging/refresh behavior
- `dashboard/src/employerDomains.ts`
  - Employer-domain list normalization helpers
- `dashboard/src/features/query_bucket/*`
  - Saved-query bucket UI/API integration
- `dashboard/src/features/ai/*`
  - AI state/UI helpers

## 3) Primary execution paths

1. User updates filters/settings (`PUT /settings`) and optionally saved queries.
2. User starts Gmail OAuth (`POST /gmail/oauth/start`) or triggers run (`POST /automation/run-once`).
3. Backend fetches unread Gmail candidates using effective query + policy.
4. Run orchestrator parses/filter/scores/routes each candidate.
5. Candidate transitions to `needs_review`, `failed`, or `processed_skipped`.
6. Premium-number intelligence runs for processed candidates.
7. Gmail labeling rules/AI decide and apply labels to processed messages.
8. User resolves failed mappings, classifies unknown numbers, approves/rejects queued items.
9. Approved items send via Gmail and optionally append a Sheets tracking row.
10. Recruiter opportunities can generate/update cold-call scripts.

## 4) Safety and control architecture

- Approve-send gate validates routing sendability, To/CC, non-empty draft, and active resume
- Routing decision safety is centralized through routing policy decision helpers
- Duplicate prevention is enforced by both write-path checks and DB unique indexes
- Manual controls are preserved for:
  - failed mapping correction
  - unknown-number classification (`mark-recruiter`, `mark-employer`)

## 5) Persistence architecture

Main entity groups:

- Workflow: `RecruiterEmail`, `SyncRun`, `UserSettings`, `ResumeAsset`
- Feedback: `DraftEditFeedback`, `RecipientRoutingFeedback`
- Analytics: `ProductivityEvent`
- Number intelligence: `PremiumNumberLead`, `NumberReviewQueue`, `RecruiterNumber`, `EmployerNumber`, `RecruiterOpportunity`

SQLite schema initialization/migration guardrails are in `backend/app/db.py`.

## 6) Architectural constraints

- Backend orchestration is concentrated in `main.py` and shared helper functions
- Frontend state is concentrated in `App.tsx`
- Several workflows are cross-coupled (run queue, routing, premium numbers, Gmail labeling, analytics)
- Single-owner scoped operation is still the default behavior (`owner_id`-scoped data)
