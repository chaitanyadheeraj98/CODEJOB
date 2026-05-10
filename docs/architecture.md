# CODEJOB Architecture

## 1. Project Structure Overview

```text
CODEJOB/
├── backend/                 # FastAPI service, business rules, Gmail/AI integrations
│   ├── app/
│   │   ├── main.py          # API routes + orchestration
│   │   ├── config.py        # environment-driven settings
│   │   ├── db.py            # SQLAlchemy engine/session + SQLite migration patches
│   │   ├── models.py        # SQLAlchemy models
│   │   ├── schemas.py       # Pydantic request/response schemas
│   │   ├── phase0.py        # parsing, scoring, routing, fallback draft logic
│   │   ├── gmail_client.py  # Gmail + Google Sheets integration
│   │   ├── telegram_bot.py  # Telegram bot polling and command dispatch
│   │   └── ai/              # AI prompting, client, reply orchestration
│   └── tests/               # backend unit tests
├── dashboard/               # React + TypeScript frontend (single-page dashboard)
│   └── src/
│       ├── App.tsx          # main UI and API calls
│       ├── components/      # reusable UI components (Sidebar)
│       └── features/ai/     # small AI-related UI helpers/types
├── docs/                    # documentation
├── scripts/                 # PowerShell helper scripts
└── docker-compose.yml       # local multi-service orchestration
```

## 2. Backend Architecture

- **Framework:** FastAPI (`backend/app/main.py`)
- **Persistence:** SQLAlchemy models + SQLite by default (`config.py`, `db.py`, `models.py`)
- **Validation/serialization:** Pydantic schemas (`schemas.py`)
- **Core domain logic:**
  - Email parsing, hard filters, scoring, routing, fallback drafting in `phase0.py`
  - AI draft generation in `app/ai/*`
  - Gmail OAuth/read/send/sheet tracking in `gmail_client.py`
  - Telegram control plane in `telegram_bot.py`

### Important backend modules

- **`main.py`**: central orchestrator for all API endpoints and automation flow.
- **`phase0.py`**: rules engine (recruiter detection, skills, routing candidates, threshold checks, fallback templates).
- **`gmail_client.py`**: Google API integration (OAuth bootstrap, message fetch, message send, unread labeling, Sheets append).
- **`telegram_bot.py`**: long-polling Telegram bot with allowlist authorization and command handling.
- **`models.py`**: entities for recruiter emails, user settings, resumes, sync runs, routing feedback, draft edit feedback.

## 3. Frontend Architecture

- **Framework/tooling:** React 19 + TypeScript + Vite (`dashboard/package.json`)
- **Pattern:** Single-page dashboard in `App.tsx` with local component state.
- **API communication:** `fetch()` calls directly to backend REST endpoints using `VITE_API_BASE_URL`.
- **UI composition:**
  - `App.tsx` for page sections/workflows
  - `components/Sidebar.tsx` for navigation and counters
  - CSS-based styling (`App.css`, `index.css`)

## 4. Application Flow

1. **Startup**
   - FastAPI app starts, creates DB schema, applies SQLite compatibility column patches, ensures default user settings.
   - Telegram service and auto-runner thread are initialized if enabled.
2. **Data ingest**
   - Gmail sync/run fetches unread emails from Gmail API.
   - Parser extracts role/location/salary/skills and routing candidates.
3. **Qualification + routing**
   - Hard filter + AI score + policy checks decide queue/skip/fail.
   - Routing determines `to/cc` safety or marks failed mapping.
4. **Drafting**
   - Rules-based fallback draft always available.
   - Optional DeepSeek generation augments/fallbacks to rules output.
5. **Manual approval**
   - Dashboard shows queue; user edits draft and approves/rejects.
   - Approve sends Gmail reply with resume attachment.
6. **Post-send tracking**
   - Email state updated in DB.
   - Optional Google Sheets row appended.

## 5. API/Service Communication

- **Frontend → Backend:** HTTP REST (`/settings`, `/gmail/*`, `/automation/run-once`, `/candidates/*`).
- **Backend → Gmail API:** OAuth + message list/get/modify/send.
- **Backend → DeepSeek API:** chat completion via OpenAI-compatible client.
- **Backend → Google Sheets API:** append approved-send tracking row.
- **Backend → Telegram API:** polling commands + push digests.

## 6. Architectural Patterns and Design Decisions

- **Single backend orchestrator file (`main.py`)**: centralizes workflows and route handlers for fast iteration.
- **Rules-first, AI-optional drafting**: stable fallback behavior even when AI is unavailable.
- **Manual approval gate before sending**: run flow queues candidates instead of auto-sending.
- **Policy object normalization**: defensive parsing of dynamic policy payloads to keep runtime safe.
- **SQLite compatibility migration helper**: startup column patching in `db.py` to keep local schema usable without full migration workflow.
- **Thread-based background services**: auto-runner + Telegram polling run in-process during app lifetime.
