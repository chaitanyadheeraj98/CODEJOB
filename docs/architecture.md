# CODEJOB Architecture (Current Branch)

Audit date: 2026-05-17  
Branch snapshot: `snowball-md`

## 1. System shape

CODEJOB currently runs as:
- **Backend:** FastAPI app, still centered in `backend/app/main.py` with service extraction helpers.
- **Frontend:** React + TypeScript SPA, still centered in `dashboard/src/App.tsx`.
- **Persistence:** SQLite via SQLAlchemy (`backend/app/models.py`, runtime schema patching in `backend/app/db.py`).
- **Integrations:** Gmail API, optional Google Sheets append, DeepSeek/OpenAI-compatible chat + embedding providers, Telegram Bot API polling.

## 2. Backend module map

| Area | Current implementation | Notes |
| --- | --- | --- |
| API surface + integration glue | `backend/app/main.py` | Route definitions and service wiring remain centralized |
| Startup lifecycle | `backend/app/services/startup_service.py` | Creates tables, patches schema, boots labeling/telegram/auto-runner |
| Run orchestration | `backend/app/services/orchestration_service.py` + `backend/app/automation/run_orchestrator.py` | Run-once, approve/reject/send-to-failed, resolve-recipients |
| Routing policy | `backend/app/routing/policy.py` + `backend/app/services/routing_runtime_service.py` | Heuristic routing is active; learned adapter currently fallback-only |
| Candidate runtime helpers | `backend/app/services/candidate_runtime_service.py` | draft repair, routing refresh, premium-number capture bridge |
| Scoring runtime | `backend/app/services/scoring_runtime_service.py` | semantic blending and embedding-cache helpers |
| Phone intelligence | `backend/app/premium_numbers/*` | extraction, review queue, recruiter/employer buckets, opportunities |
| Gmail labeling | `backend/app/gmail_labeling/*` + runtime service | rules-first label decision + AI fallback |
| Cold-call scripts | `backend/app/cold_call/*` | recruiter-opportunity script generation with sanitization |
| Query bucket | `backend/app/query_bucket/service.py` | saved query sanitize/dedupe (limit 10) |
| Telegram runtime | `backend/app/services/telegram_runtime_service.py` + `backend/app/telegram_bot.py` | command/callback handling + polling transport |

## 3. Frontend module map

| Area | Current implementation | Notes |
| --- | --- | --- |
| Main shell and workflow UI | `dashboard/src/App.tsx` | Holds most state/actions/effects |
| Navigation | `dashboard/src/components/Sidebar.tsx` | `New Campaign`, `Settings`, `Help Center` remain placeholder actions |
| Candidate bucket logic | `dashboard/src/candidateBuckets.ts` | bucket paging/refresh for needs_review, failed, approved_sent |
| Query bucket UI | `dashboard/src/features/query_bucket/*` | inline saved-query suggestions and persistence interactions |
| AI UI module | `dashboard/src/features/ai/*` | utility/stub-level module only |

## 4. Runtime execution model

### Startup order
1. `Base.metadata.create_all()`
2. `ensure_sqlite_phase0_columns()` runtime patching
3. default settings bootstrap
4. Gmail labeling service bootstrap + label ensure (when Gmail configured)
5. Telegram bot start (if token + allowed chats configured)
6. background auto-runner thread start

### Main mail-processing path
1. resolve effective query/date/policy/threshold
2. list unread Gmail candidates
3. orchestrator parse + hard filter + optional semantic blend
4. routing policy evaluation
5. state assignment (`needs_review`, `failed`, or `processed_skipped`)
6. side effects: premium-number intelligence + Gmail labeling + Gmail unread mark processing

### Approval path
`POST /candidates/{id}/approve-send` enforces:
- candidate in `needs_review`
- routing sendability (`safe/confirmed + confidence>=0.8` or manual confirmation)
- `To` + `CC` present
- non-empty draft
- active resume

On success: Gmail send, state => `approved_sent`, productivity event, optional Sheets append.

## 5. Candidate states currently seen in code

Primary queue-facing states:
- `needs_review`
- `failed`
- `processed_skipped`
- `approved_sent`

Also present for manual/legacy transitions:
- `rejected`
- `auto_rejected`

## 6. API capability map

| Capability | Endpoints |
| --- | --- |
| Health/status | `GET /health`, `GET /gmail/status`, `GET /ai/status`, `GET /telegram/status` |
| Settings/resume | `GET/PUT /settings`, `POST /settings/resume`, `GET /settings/resumes` |
| Gmail auth/ops | `POST /gmail/oauth/start`, `GET /gmail/oauth/url`, `POST /gmail/sync`, `POST /gmail/labeling/preview` |
| Automation | `POST /automation/run-once`, `POST /phase0/emails/ingest` |
| Candidate workflow | `GET /candidates`, `GET /candidates/{id}`, `POST /candidates/{id}/approve-send`, `POST /candidates/{id}/reject`, `POST /candidates/{id}/send-to-failed-mapping`, `POST /candidates/{id}/resolve-recipients`, `POST /candidates/reject-bulk` |
| Premium numbers + review | `GET /premium-numbers`, `GET /premium-numbers/{id}`, `POST /premium-numbers/reextract/{id}`, `GET /number-review`, classification/swap/delete endpoints |
| Opportunities | `GET /recruiter-opportunities`, `PATCH /recruiter-opportunities/{id}`, `POST /recruiter-opportunities/{id}/generate-cold-call-script` |
| Analytics | `POST /analytics/events/view`, `GET /analytics/events`, `GET /analytics/trend` |

## 7. Known architecture constraints

- `main.py` is still the primary integration hub.
- `App.tsx` is still the primary frontend state container.
- policy profile definitions are duplicated backend/frontend.
- learned routing adapter is still a parity placeholder.
- runtime process memory stores telegram auth sessions and runtime health signals (non-durable).
- `feature_auto_send` and `feature_retry_queue` are persisted settings without full runtime executors.

## 8. Intended vs Current Runtime

Intended architecture:
- thinner composition root with smaller route/domain modules and less centralized orchestration coupling.

Current runtime behavior:
- `backend/app/main.py` remains the central integration and route hub even after service extraction.
- `dashboard/src/App.tsx` remains the primary UI state container.

Evidence basis: code inspection  
Verification limits: runtime ownership validated from source paths; full-suite validation still limited by stale tests and local lint/build blockers.
