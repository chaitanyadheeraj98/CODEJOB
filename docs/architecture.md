# CODEJOB Architecture

## 1) Project structure overview

```text
CODEJOB/
├── backend/                  # FastAPI service, routing logic, Gmail/AI integrations, DB models
│   ├── app/
│   │   ├── main.py           # API entrypoint + orchestration flows
│   │   ├── config.py         # Environment/settings
│   │   ├── db.py             # SQLAlchemy engine/session + SQLite migration helper
│   │   ├── models.py         # ORM models
│   │   ├── schemas.py        # Pydantic request/response schemas
│   │   ├── phase0.py         # Parsing, scoring, routing, fallback draft generation
│   │   ├── gmail_client.py   # Gmail OAuth, inbox fetch, send, tracking sheet integration
│   │   ├── telegram_bot.py   # Telegram polling and command transport
│   │   └── ai/               # AI reply provider, prompts, formatting, resume extraction
│   └── tests/                # Backend unit/integration-style tests
├── dashboard/                # React + TypeScript + Vite frontend
│   ├── src/
│   │   ├── App.tsx           # Main dashboard UI and API integration
│   │   ├── App.css           # Main app styles
│   │   ├── index.css         # Global styles + typography
│   │   ├── components/       # Shared components (Sidebar)
│   │   └── features/ai/      # AI UI helper types/state/labels
├── docs/                     # Project docs
├── scripts/                  # PowerShell automation/graph context scripts
└── docker-compose.yml        # Local multi-service orchestration
```

## 2) Main folders and responsibilities

- **backend/**: Handles automation decisions, persistence, Gmail sync/send, AI drafting, and APIs.
- **dashboard/**: Provides operator UI to configure automation, run sync/queue, review drafts, fix routing, and approve/reject.
- **docs/**: Documentation and project context artifacts.
- **scripts/**: Quality/bootstrap and graph-report helper scripts.

## 3) Backend architecture

### Framework and core stack
- **FastAPI** app lifecycle and endpoints (`backend/app/main.py`)
- **SQLAlchemy ORM** models and session management (`models.py`, `db.py`)
- **Pydantic** schema validation (`schemas.py`)
- **Google APIs** (Gmail + Sheets) (`gmail_client.py`)
- **OpenAI SDK against DeepSeek base URL** (`ai/deepseek_client.py`)
- **Thread-based background workers** for auto-run and Telegram polling (`main.py`, `telegram_bot.py`)

### Major backend modules
- **main.py**: Request handling and orchestration for settings, run pipeline, queue states, analytics, and review actions.
- **phase0.py**: Business rules for recruiter detection, role/skills/location extraction, hard filters, AI-assist scoring, routing heuristics, and fallback draft templating.
- **gmail_client.py**: OAuth bootstrap, unread candidate fetch by query, message parsing, label updates, reply send with attachment, and sheet-row append.
- **ai/**: Builds prompts, calls DeepSeek, sanitizes/enforces draft shape, and extracts resume text for context.
- **telegram_bot.py**: Transport service to poll Telegram updates and route commands through backend command handler.

## 4) Frontend architecture

### Framework and core stack
- **React 19 + TypeScript** with **Vite**
- Single-page dashboard with stateful data loading in `App.tsx`
- CSS-driven component styling and responsive behavior (`App.css`, `index.css`)

### Frontend module structure
- **App.tsx**: API wiring, page-state management, forms, run actions, queue panels, analytics monitor.
- **components/Sidebar.tsx**: Left navigation with counters and active section behavior.
- **features/ai/**: Small AI-specific typing and display helpers.

## 5) Application flow

1. User configures settings/profile/policy in dashboard.
2. Dashboard sends `PUT /settings`.
3. User runs **Sync + Queue** (`POST /automation/run-once`) or starts OAuth (`POST /gmail/oauth/start`).
4. Backend loads unread Gmail messages based on effective query + policy.
5. For each message, backend performs:
   - parsing and heuristics
   - hard filter checks
   - score + threshold evaluation
   - recipient routing resolution
   - draft generation (AI-enabled or rules fallback)
6. Qualified items are stored as `needs_review`; blocked/missing become failed/skipped states.
7. User reviews queue in UI, optionally edits draft, fixes routing, then approves/rejects.
8. On approve, backend sends Gmail reply with resume attachment, updates DB state, records productivity events, and optionally appends Sheets tracking row.

## 6) Communication between parts

- **Frontend ↔ Backend**: REST/JSON over HTTP (fetch calls in `App.tsx`).
- **Backend ↔ Gmail API**: OAuth-authenticated API calls for inbox retrieval, label changes, and send.
- **Backend ↔ Google Sheets API**: Optional append-only tracking rows.
- **Backend ↔ DeepSeek (OpenAI-compatible)**: Chat completion request for enriched draft generation.
- **Backend ↔ Telegram API**: Polling + outbound notifications/command replies.
- **Backend ↔ SQLite DB**: Persistent state for settings, queue items, run history, and analytics events.

## 7) APIs/services/models/utilities overview

- **Controllers (FastAPI endpoints)**: in `backend/app/main.py`
- **Services**: Gmail service, Telegram service, AI reply service, policy and queue orchestration in main module helpers
- **Models**: `RecruiterEmail`, `UserSettings`, `ResumeAsset`, `SyncRun`, `DraftEditFeedback`, `RecipientRoutingFeedback`, `ProductivityEvent`
- **Schemas**: request/response models in `schemas.py`
- **Utilities**: parsing/routing/scoring/draft rendering and prompt/draft formatting helpers

## 8) Architectural patterns and design decisions visible in code

- **Monolithic backend service** with modular helper files rather than microservices.
- **Rule-first qualification** with optional AI enhancement for draft generation.
- **Manual approval gate** before sending production recruiter replies.
- **State-based queue lifecycle** (`needs_review`, `failed`, `approved_sent`, etc.) for clear operator actions.
- **Policy-driven run behavior** (date mode, dry-run, batch limit, threshold overrides).
- **Fallback-safe strategy** across integrations (OAuth status handling, AI fallback drafts, graceful errors).
- **Operator observability** through event logging and productivity trend endpoints.
