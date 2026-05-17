# CODEJOB Architecture (Current Branch)

Branch snapshot: `copilot/audit-and-update-docs`

## 1. System shape

CODEJOB is a single-owner mail-operations system built from:

- **Backend:** FastAPI app centered in `backend/app/main.py`
- **Frontend:** React + TypeScript dashboard centered in `dashboard/src/App.tsx`
- **Persistence:** SQLite via SQLAlchemy models in `backend/app/models.py`
- **External services:** Gmail API, optional Google Sheets tracking, DeepSeek/OpenAI-compatible APIs, optional OpenRouter/OpenAI embeddings, Telegram Bot API

The codebase is intentionally concentrated in a few large modules. `backend/app/main.py` owns the HTTP surface, startup lifecycle, Telegram command plumbing, policy normalization, and several workflow helpers. `dashboard/src/App.tsx` owns most screen state, API calls, refresh coordination, and feature toggles.

## 2. Backend module map

| Area | Current implementation | Notes |
|---|---|---|
| API + lifecycle | `backend/app/main.py` | Starts schema sync, Gmail labeling bootstrap, Telegram bot, and background auto-runner thread |
| Run orchestration | `backend/app/automation/run_orchestrator.py` | Executes parse → hard filter → AI/semantic score → routing → queue transition → side effects |
| Parsing and fallback drafting | `backend/app/phase0.py` | Email parsing, routing evidence extraction, fallback signature/template logic |
| Routing policy | `backend/app/routing/policy.py` | Heuristic routing is the live adapter; learned adapter is still a parity placeholder |
| AI draft path | `backend/app/ai/*` | Draft generation, prompt building, resume context attribution, draft quality scoring |
| Semantic scoring | `backend/app/semantic/*` | Embedding generation, caching, similarity, and blended scoring |
| Phone intelligence | `backend/app/premium_numbers/*` | Premium lead extraction, manual review queue, recruiter/employer buckets, opportunity creation |
| Gmail labeling | `backend/app/gmail_labeling/*` | Rules-first labeling with AI fallback and Gmail label application |
| Cold call generation | `backend/app/cold_call/*` | Recruiter-opportunity cold call script generation with truthfulness guardrails |
| Query bucket | `backend/app/query_bucket/service.py` | Sanitizes and deduplicates saved Gmail queries |
| Telegram control plane | `backend/app/telegram_bot.py` + handlers in `main.py` | Polling bot with action auth and inline callbacks |
| DB bootstrapping | `backend/app/db.py` | Runtime table/index creation and additive SQLite patching |

## 3. Frontend module map

| Area | Current implementation | Notes |
|---|---|---|
| Dashboard shell | `dashboard/src/App.tsx` | Single-screen app with six main sections and settings panel |
| Navigation | `dashboard/src/components/Sidebar.tsx` | Sidebar counts and section switches; footer buttons are placeholders |
| Query bucket UI | `dashboard/src/features/query_bucket/*` | Saved Gmail query suggestions, add/remove actions, 10-item limit |
| Candidate pagination | `dashboard/src/candidateBuckets.ts` | Bucket-specific list refresh behavior |
| Employer domain helpers | `dashboard/src/employerDomains.ts` | Normalization and validation helpers |
| AI UI stub | `dashboard/src/features/ai/*` | Minimal placeholder README/state types, not a full feature surface yet |

## 4. Runtime execution model

### Startup

Application startup performs all of the following in order:

1. Creates ORM tables.
2. Runs `ensure_sqlite_phase0_columns()` additive schema patching.
3. Creates or repairs the default `UserSettings` row.
4. Boots `GmailLabelingService` and pre-syncs allowed Gmail labels when Gmail is configured.
5. Starts `TelegramBotService` if bot token and allowed chat IDs are configured.
6. Starts the background auto-runner thread.

### Main mail-processing path

```mermaid
flowchart TD
    A[Dashboard or Telegram run trigger] --> B[Resolve effective query, date mode, policy, threshold]
    B --> C[List unread Gmail candidates]
    C --> D[RunOrchestrator.execute]
    D --> E[Parse subject/body into recruiter metadata]
    E --> F[Hard filters + F2F policy block]
    F --> G[AI score and optional semantic blend]
    G --> H{Qualified?}
    H -->|No| I[processed_skipped]
    H -->|Yes| J[RoutingPolicyService]
    J --> K{To + CC resolved?}
    K -->|No| L[failed]
    K -->|Yes| M[Build fallback or AI draft]
    M --> N[needs_review]
    I --> O[Phone extraction + Gmail labeling]
    L --> O
    N --> O
    O --> P[Mark Gmail message processed]
```

### Approval path

```mermaid
flowchart TD
    A[needs_review candidate] --> B[Operator edits draft or confirms routing]
    B --> C{Approve gate}
    C -->|safe routing + To + CC + draft + resume| D[Send Gmail reply with resume]
    C -->|missing condition| E[Block send]
    D --> F[approved_sent]
    F --> G[Record productivity event]
    G --> H[Attempt optional Google Sheets append]
```

## 5. Queue and orchestration rules

The live state machine is small but strict:

- `needs_review`: qualified candidate with resolved recipients; still requires manual approval.
- `failed`: routing could not produce a valid recruiter `To` and employer `CC` pair.
- `processed_skipped`: candidate was filtered out by hard rules, score threshold, or F2F policy block.
- `approved_sent`: approval path completed successfully.

Important details from the implementation:

- Routing safety is determined by `_evaluate_routing_policy()` and `_routing_is_sendable()`.
- A routing pair is considered sendable only when the routing status is safe/confirmed with confidence `>= 0.8` or when routing has been manually confirmed.
- `feature_auto_send` exists in settings but does **not** bypass the manual approval gate in the current branch.
- `feature_retry_queue` is persisted but is not wired to a separate retry processor in the current branch.

## 6. Service interactions

### Gmail

- OAuth bootstrap endpoints drive Gmail authorization.
- Runs fetch unread candidates by effective query.
- Approvals send threaded replies with resume attachment.
- Processed messages can be labeled through `GmailLabelingService`.

### Google Sheets

- After a successful approval, the backend attempts a best-effort tracking row append.
- Failures here do not roll back the send; they are stored as `last_error` warnings.

### AI and semantic systems

- Draft generation uses DeepSeek/OpenAI-compatible chat APIs when `feature_ai_enabled` is on.
- Semantic ranking uses a provider selected by configuration (`hash`, OpenAI-compatible, or OpenRouter) when `feature_semantic_enabled` is on.
- Resume context attribution records whether draft context was injected, limited, missing, extract-failed, or rules-only.
- Draft quality is computed for every `RecruiterEmail` response payload from draft text, routing confidence, resume context, and AI fallback state.

### Telegram

- Telegram is a polling bot, not a webhook service.
- Allowed chats are enforced from configuration.
- Sensitive actions require `/auth <PIN>` or `pin=<PIN>` and create an in-memory session with TTL.
- Sessions are not persisted across process restarts.

## 7. API surface by capability

| Capability | Current endpoints |
|---|---|
| Health and status | `GET /health`, `GET /gmail/status`, `GET /ai/status`, `GET /telegram/status` |
| Settings and resumes | `GET/PUT /settings`, `POST /settings/resume`, `GET /settings/resumes` |
| Gmail auth and inbox operations | `POST /gmail/oauth/start`, `GET /gmail/oauth/url`, `POST /gmail/sync`, `POST /gmail/labeling/preview` |
| Automation | `POST /automation/run-once`, `POST /phase0/emails/ingest` |
| Candidate workflow | `GET /candidates`, `GET /candidates/{id}`, `POST /candidates/{id}/approve-send`, `POST /candidates/{id}/reject`, `POST /candidates/{id}/send-to-failed-mapping`, `POST /candidates/{id}/resolve-recipients`, `POST /candidates/reject-bulk` |
| Premium numbers | `GET /premium-numbers`, `GET /premium-numbers/{lead_id}`, `POST /premium-numbers/reextract/{recruiter_email_id}` |
| Number review queue | `GET /number-review`, `POST /number-review/{id}/mark-recruiter`, `POST /number-review/{id}/mark-employer`, `DELETE /number-review/{id}` |
| Recruiter and employer buckets | `GET /recruiter-numbers`, `POST /recruiter-numbers/{id}/swap-to-employer`, `GET /employer-numbers`, `POST /employer-numbers/{id}/swap-to-recruiter` |
| Opportunities | `GET /recruiter-opportunities`, `PATCH /recruiter-opportunities/{id}`, `POST /recruiter-opportunities/{id}/generate-cold-call-script` |
| Analytics | `POST /analytics/events/view`, `GET /analytics/events`, `GET /analytics/trend` |

## 8. Architecture constraints and partial systems

Current branch constraints that matter for future work:

- `main.py` remains the central orchestration hub; many cross-cutting rules are shared there.
- `App.tsx` remains the main UI state machine; post-mutation refreshes are coordinated manually through `schedulePostMutationRefresh()` and effects.
- Policy profiles are duplicated in backend and frontend code. They currently match in intent but can drift.
- `LearnedRoutingAdapter` is still a placeholder that delegates to heuristic routing.
- Query bucket support is implemented for storing and recalling Gmail queries, but it is still embedded inside the main dashboard rather than a fully isolated module.
- Sidebar footer buttons (`Settings`, `Help Center`) and `New Campaign` are presentational only in the current branch.

## 9. Source-of-truth summary

For this branch, the source of truth is the code, not historical docs. The most important implementation-grounded invariants are:

- manual approval before any outbound send,
- separate recruiter vs employer identity stores,
- duplicate prevention through both logic checks and DB uniqueness,
- owner-scoped settings and records,
- routing evidence preserved on workflow records,
- Gmail labeling and premium-number extraction running as side effects of ingestion/orchestration.
