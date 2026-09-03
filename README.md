# CodeJob MailOps

**An AI-assisted job-search operations platform.** CodeJob connects to your Gmail account, reads the recruiter emails that arrive there, works out which ones are real job opportunities worth your time, and turns them into a reviewable pipeline — so you apply *selectively* instead of drowning in your inbox.

---

## Table of contents

- [The problem it solves](#the-problem-it-solves)
- [Who it is for](#who-it-is-for)
- [How it works — the core loop](#how-it-works--the-core-loop)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Access model: user, developer, administrator](#access-model-user-developer-administrator)
- [Features](#features)
  - [1. Gmail intake and the job-intent gate](#1-gmail-intake-and-the-job-intent-gate)
  - [2. Needs Review — the candidate workflow](#2-needs-review--the-candidate-workflow)
  - [3. Premium Contacts — recruiter identity](#3-premium-contacts--recruiter-identity)
  - [4. Application Tracking (AppTS)](#4-application-tracking-appts)
  - [5. Resume Tracking](#5-resume-tracking)
  - [6. Reply Inbox](#6-reply-inbox)
  - [7. CodeJob Assistant — the in-app AI](#7-codejob-assistant--the-in-app-ai)
  - [8. Relationship intelligence](#8-relationship-intelligence)
  - [9. Scheduled tasks](#9-scheduled-tasks)
  - [10. External feeds](#10-external-feeds)
  - [11. Telegram control](#11-telegram-control)
- [User guide: common workflows](#user-guide-common-workflows)
- [Developer setup](#developer-setup)
- [Environment variables](#environment-variables)
- [Database and migrations](#database-and-migrations)
- [Configuration and feature flags](#configuration-and-feature-flags)
- [Testing](#testing)
- [Deployment and operations](#deployment-and-operations)
- [Known limitations](#known-limitations)
- [Repository layout](#repository-layout)

---

## The problem it solves

If you are job-hunting in a contract-heavy market, your inbox fills with recruiter mail. Most of it is noise: duplicate postings from six vendors for the same role, mass blasts that do not match your skills, and follow-ups on things you never applied to. The genuinely good opportunities are in there, but finding them costs hours a day, and the ones you *do* pursue immediately become a tracking problem — who submitted you where, which resume you used, who owes you a reply.

CodeJob automates the triage and remembers the rest:

| Problem | What CodeJob does |
| --- | --- |
| Recruiter mail volume | Classifies every message; only real opportunities reach your queue |
| Duplicate postings | Reconciles recruiter identity and clusters related opportunities |
| "Who is this recruiter?" | Builds a versioned contact record from every email they have sent |
| Losing track of applications | Tracks submissions, RTRs, interviews, and follow-up deadlines |
| Nothing ever gets followed up | Scheduled reminders and digests that stop and ask before acting |
| "What was that role from Tuesday?" | An in-app AI assistant that can search your own pipeline |

**The design principle throughout: the machine prepares, the human decides.** Nothing is sent, applied to, or committed on your behalf without an explicit click.

---

## Who it is for

**A single job seeker running it for themselves.** This is a single-tenant, single-owner application designed to run locally or on a private host. It is not multi-user SaaS — there are no accounts, no tenant isolation, and no login screen. See [Access model](#access-model-user-developer-administrator) for exactly what that means and why it matters for how you deploy it.

Secondary audiences: developers extending the pipeline, and anyone operating the stack (they are usually the same person).

---

## How it works — the core loop

```text
   Gmail ──┐
           ├──►  Parse  ──►  Hard filter  ──►  Intent gate  ──►  Score  ──►  Route
 Job feed ─┘     (role,        (rules:          (LLM: is this      (fit vs      │
                  salary,       location,        actually a job     your          │
                  skills)       seniority)       opportunity?)      profile)      │
                                                                                  ▼
                                                                    ┌─────────────────────┐
                                                                    │    Needs Review     │
                                                                    │  (you decide here)  │
                                                                    └──────────┬──────────┘
                                                     ┌─────────────────────────┼──────────────┐
                                                     ▼                         ▼              ▼
                                                 Approve                    Reject      Failed Mapping
                                                     │                                  (couldn't parse)
                                                     ▼
                                            Send reply + attach resume
                                                     │
                                                     ▼
                                          Application Tracking ──► RTR ──► Interview
                                                     │
                                                     ▼
                                             Reply Inbox (their response)
```

Every stage writes an audit trail. When a candidate is rejected you can see which rule or score did it, and the parser's extraction is inspectable field by field.

---

## Quick start

**Prerequisites:** Docker Desktop, and (optionally) [Ollama](https://ollama.com) on the host if you want the AI assistant.

```bash
git clone https://github.com/chaitanyadheeraj98/CODEJOB.git
cd CODEJOB
```

**Step 1 — create `backend/.env.postgres`.** This file is required and is *not* in the repository (it holds credentials). Create it by hand:

```dotenv
POSTGRES_USER=codejob
POSTGRES_PASSWORD=choose-a-strong-password
POSTGRES_DB=codejob
DATABASE_URL=postgresql+psycopg2://codejob:choose-a-strong-password@postgres:5432/codejob
# Used by host-side scripts that connect from outside the Compose network
POSTGRES_HOST_DATABASE_URL=postgresql+psycopg2://codejob:choose-a-strong-password@localhost:5432/codejob
```

> ⚠️ The `postgres` service healthcheck runs `pg_isready -U codejob -d codejob`. If you change the user or database name, update the healthcheck in `docker-compose.yml` too, or the stack will never report healthy.

**Step 2 — create `backend/.env`.** See [Environment variables](#environment-variables) for the full list. A minimal working file:

```dotenv
APP_ENV=dev
REDIS_URL=redis://redis:6379/0
OWNER_ID=default-owner
GOOGLE_CLIENT_ID=your-google-oauth-client-id
GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8080/
```

**Step 3 — start the stack.**

```bash
docker compose up -d
```

Migrations run automatically on backend boot. Then open:

| Service | URL |
| --- | --- |
| Dashboard | <http://localhost:5173> |
| API + docs | <http://localhost:8000> · <http://localhost:8000/docs> |

**Step 4 — connect Gmail.** Go to **Settings → Gmail**, click **Connect**, and complete the Google OAuth consent flow. Then run your first sync from **Run Queue → Sync Gmail**.

---

## Architecture

```text
┌────────────────────────────────────────────────────────────────────┐
│  dashboard (React 19 + TypeScript + Vite)          :5173           │
│  Left rail navigation · Needs Review · Assistant · Settings        │
└───────────────────────────────┬────────────────────────────────────┘
                                │ REST (216 endpoints)
┌───────────────────────────────▼────────────────────────────────────┐
│  backend (FastAPI + SQLAlchemy 2.0, Python 3.12+)  :8000           │
│  ┌──────────────┬───────────────┬──────────────┬────────────────┐  │
│  │ Parsing &    │ Orchestration │ Premium       │ MCP server     │  │
│  │ screening    │ & send gate   │ numbers       │ mounted at /mcp│  │
│  └──────────────┴───────────────┴──────────────┴────────────────┘  │
└──┬──────────────┬───────────────┬──────────────┬───────────────────┘
   │              │               │              │
┌──▼───────┐ ┌────▼─────┐  ┌──────▼──────┐ ┌─────▼──────────────────┐
│ postgres │ │  redis   │  │   searxng   │ │ Ollama (host)          │
│   :16    │ │  queues  │  │  web search │ │ DeepSeek · Groq (API)  │
└──────────┘ └────┬─────┘  └─────────────┘ └────────────────────────┘
                  │
        ┌─────────▼──────────────────────────────────────┐
        │ worker (RQ) — 5 queues                         │
        │ gmail_sync · nvoids_sync · automation_run      │
        │ embedding_generation · scheduled_task          │
        └────────────────────────────────────────────────┘
```

### Components

| Layer | Technology | Notes |
| --- | --- | --- |
| **Frontend** | React 19, TypeScript, Vite, Vitest | State concentrated in `App.tsx`; features in `src/features/*` |
| **Backend** | FastAPI, SQLAlchemy 2.0, Pydantic Settings | Routes largely inline in `main.py` (~9.8k lines) |
| **Database** | PostgreSQL 16 | Migrated from SQLite; Alembic manages schema (60 revisions) |
| **Queue** | Redis + RQ, with `--with-scheduler` | Five named queues, one worker process |
| **Chat LLM** | Ollama (host) — `gemma4:31b-cloud` | Two fallback models configured |
| **Intent gate** | DeepSeek (default) → Groq → taxonomy | Provider is a config switch, not a deploy |
| **Embeddings** | `sentence-transformers/all-MiniLM-L6-v2` (local, CPU) | Google/OpenRouter available as alternates |
| **Web search** | Self-hosted SearXNG | Only registered when `SEARXNG_URL` is set |
| **Tool protocol** | MCP (Model Context Protocol) | In-process server mounted at `/mcp` |

### Why the AI layers are separate

Three distinct AI roles, deliberately not one model:

1. **The intent gate** decides what enters your queue at all. It runs on every inbound email, so it must be fast and cheap — and reversible. `INTENT_GATE_PROVIDER` flips between `deepseek`, `groq`, and `taxonomy` (rules only, no LLM) without a redeploy.
2. **Extraction** pulls structured fields (role, salary, skills, location) out of unstructured email bodies.
3. **The Assistant** is conversational and tool-driven, running against your own data through MCP.

---

## Access model: user, developer, administrator

**Read this before exposing the stack to any network.**

CodeJob has **no application-level authentication**. There is no login page, no session token, no password, and no user table. Every record is scoped to a single configured `OWNER_ID` (default `"default-owner"`), applied consistently across the data layer — but that is *data scoping*, not access control. The API additionally sets `allow_origins=["*"]` for CORS.

The three "roles" below are therefore **operational conventions, not enforced permissions**:

| Role | What it means in practice | Enforced by |
| --- | --- | --- |
| **User** | Uses the dashboard at `:5173`. Reviews candidates, approves sends, chats with the Assistant. | Nothing — anyone who can reach the port has full access |
| **Developer** | Has the repository, runs tests, writes migrations, extends the pipeline. | Filesystem / repo access |
| **Administrator** | Holds the `.env` files, database credentials, and Google OAuth secrets; decides which feature flags are on. | Host and file access |

### What this means for deployment

- **Run it locally, or behind something that does authenticate.** Bind to `127.0.0.1`, use a VPN, or put an authenticating reverse proxy in front. Do not publish port 8000 or 5173 to the internet.
- **`postgres` is deliberately not published to a host port** in `docker-compose.yml`. Keep it that way.
- **The one real credential gate is Telegram.** `TELEGRAM_ACTION_PIN` and `TELEGRAM_ALLOWED_CHAT_IDS` restrict who can drive the bot, because that surface *is* reachable from outside.

If you need real multi-user access, that is a feature to build, not a setting to configure.

---

## Features

### 1. Gmail intake and the job-intent gate

**How it works.** A sync job pulls messages from Gmail (optionally restricted by `GMAIL_LABEL_FILTER`). Each message goes through: parse → hard filter → intent gate → score → route.

- **Parse** extracts role, salary, skills, location, and contact details, keeping per-field provenance so you can see *where* each value came from.
- **Hard filter** applies deterministic rules (location, seniority, policy) before any model runs — cheap rejections happen first.
- **Intent gate** asks an LLM the one question rules struggle with: *is this actually a job opportunity?* It returns strict JSON. If the model fails or times out, the rules taxonomy answers instead.
- **Score** compares the parsed role against your profile and `QUALIFICATION_THRESHOLD`.
- **Route** assigns a queue state: `needs_review`, or `failed_mapping` if parsing could not produce a usable record.

**Use case.** You get 60 recruiter emails overnight. 41 are mass blasts or duplicates; 12 are out-of-state contract roles you have ruled out; 7 are real. You open the dashboard to 7 items in Needs Review and a Recent Runs entry explaining what happened to the other 53.

**Reversibility.** Set `INTENT_GATE_PROVIDER=taxonomy` to turn the LLM off entirely and run on rules alone. The pipeline keeps working.

---

### 2. Needs Review — the candidate workflow

**How it works.** Each candidate card shows the parsed fields, the extraction provenance, the score, and the routing reason. From there you can approve and send, reject, regenerate the draft, resolve recipients, or dismiss. Bulk actions cover multi-select, and bulk operations are idempotency-keyed so a double-click cannot double-send.

Sibling views:

| View | Purpose |
| --- | --- |
| **Needs Review** | Candidates awaiting your decision |
| **Failed Mapping** | Items the parser could not turn into a usable record — inspect and fix |
| **Sent Items** | What went out, with the attachment snapshot recorded |
| **Recent Runs** | Every automation run, including a skipped-item drill-down |

**Use case.** A card shows "Senior Java Developer — Austin, TX — $65/hr". The provenance hint reveals the rate came from the email body but the location came from the subject line. You approve, the reply goes out with your Java resume attached, and an application record is created automatically.

**Filtering.** Every list supports the shared filter/sort framework: autocomplete-backed filter options, per-dashboard visibility settings (hide filters you never use), and URL sync so a filtered view is a shareable, bookmarkable link.

---

### 3. Premium Contacts — recruiter identity

**How it works.** The same human contacts you from three addresses across two agencies over six months. This subsystem reconciles them into one versioned contact record, using phone numbers, email domains, and name matching as evidence. Every merge is recorded, and the contact carries a version history.

Capabilities:

- **Identity reconciliation** with an evidence trail for each link
- **Conflict resolution** when two records disagree
- **Multi-email contacts** and email-domain sync
- **Manual contact creation** for numbers you collect offline
- **Recycle Bin** with version pruning — soft delete, recoverable
- **Cold-call script generation** from the contact's opportunity history

**Use case.** You get a call from a number you do not recognise. You search it in Premium Contacts and find it belongs to a recruiter who emailed you twice in March about two different roles at the same end client — plus the script showing what you discussed.

---

### 4. Application Tracking (AppTS)

*Requires `feature_applications_enabled`.*

**How it works.** When you approve and send, an application record is created and moves through a lifecycle: submitted → RTR (right-to-represent) → interview → outcome. It tracks submission deduplication (so two vendors cannot submit you twice to one client), role-similarity scoring, risk fields, and next-action deadlines.

Optional AI-drafted outreach messages (`feature_application_outreach_drafts_enabled`) prepare follow-up text — drafted and held, never sent automatically.

**Use case.** Two agencies both pitch you the same role at the same client. Submission dedupe flags the collision before you sign the second RTR — which is the difference between a clean submission and being blacklisted for double submission.

---

### 5. Resume Tracking

*Requires `feature_resume_tracking_enabled`.*

**How it works.** Manages multiple resume versions and records which one went to which submission. Skill-gap snapshots compare a resume against a target role. Applications are auto-logged on approve-send, so the record of "which resume did I send them?" is created without you doing anything.

**Use case.** A recruiter calls about a role you applied to five weeks ago. You open the submission and see exactly which of your four resume variants they hold, so the conversation matches the document in front of them.

---

### 6. Reply Inbox

*Requires `feature_reply_inbox_enabled`.*

**How it works.** Groups recruiter responses into conversations, tracks direction (inbound/outbound), and surfaces replies worth acting on. Optional email open tracking uses `PUBLIC_BASE_URL` and `TRACKING_SECRET_KEY`.

**Use case.** Three recruiters replied while you were in an interview. The Inbox badge shows 3, grouped by conversation, with the original opportunity linked from each.

---

### 7. CodeJob Assistant — the in-app AI

*Requires `FEATURE_CHAT_ENABLED=true` and a reachable Ollama daemon.*

**How it works.** A full-page workspace (plus a floating widget) backed by an MCP tool server running in-process. The model cannot query the database freely — it can only call registered, owner-scoped tools. It renders structured output: candidate tables, charts (trend, distribution, funnel, pipeline), metric cards with a provenance contract, and ranked opportunity lists.

**Tool tiers:**

| Tier | Examples | Requires |
| --- | --- | --- |
| **Read** (default) | `search_candidates`, `get_metrics`, `get_chart`, `list_conversations`, `rank_opportunities`, `render_candidate_table` | `FEATURE_CHAT_ENABLED` |
| **Propose** | `propose_send_email`, `propose_candidate_action`, `propose_record_update`, `propose_add_note` | `FEATURE_CHAT_ACTIONS_ENABLED` |
| **Scheduling** | `list_scheduled_tasks`, `propose_scheduled_task` | `FEATURE_SCHEDULING_ENABLED` |
| **Relationships** | `get_relationships`, `recommend_recruiter` | `FEATURE_RELATIONSHIP_INTELLIGENCE_ENABLED` |

**`propose_*` tools never write.** They return a proposal card; a human click performs the action through the same endpoint the UI uses. There is no model-callable write path.

**File attachments.** Upload a job description or resume to a chat session and the Assistant can read it. Uploads are validated by extension allowlist *and* magic-byte signature, capped at 10 MB, stored content-addressed by SHA-256 (never by the supplied filename), and deduplicated with reference counting.

**Web search.** When `SEARXNG_URL` is set, the Assistant can search the web and cite sources.

**Example prompts:**

```text
Show me every candidate from last week scoring above 0.8
Which recruiters have I never replied to?
Chart my application funnel for the last 90 days
Compare record 412 and record 418
Draft a follow-up to the Austin Java role — don't send it
```

---

### 8. Relationship intelligence

*Requires `feature_relationship_intelligence_enabled` (off by default — see [Known limitations](#known-limitations)).*

**How it works.** Entity resolution plus decomposed pair scoring group opportunities that are really the same underlying role reaching you through different vendors. Runs in **shadow mode** by design: clustering computes and persists long before results may be shown. A second flag, `feature_relationship_surfacing_enabled`, controls display — the scorer has to earn the right to surface by being measured first.

Ships with a labelling tool and a calibration harness for building a labelled set.

**Use case.** Five postings across three agencies are one role at one end client. Clustering collapses them, so you pursue the strongest vendor relationship instead of applying five times.

---

### 9. Scheduled tasks

*Requires `FEATURE_SCHEDULING_ENABLED` (off by default).*

**How it works.** Timezone-aware recurring tasks in four kinds — **reminder**, **digest**, **monitor**, **workflow**. A sweep runs on a fixed interval and executes phases in a strict order: **expire → warn → enqueue → suspend**.

The authorization model is **draft-and-hold**: a task runs unattended right up to the point of consequence, then stops and waits for you. The dividing line is *consequence, not effort* — a task may do arbitrary work preparing a result, but anything that leaves the system waits for a click.

Safety properties:

- **Double refusal on approval** — a run is refused both when its outcome is no longer `pending` and on a fresh clock comparison against `expires_at`, so an expired run cannot be approved even if the button is still on screen
- **Half-window warning** before expiry
- **Auto-suspend** after 3 consecutive failures
- Full DST handling: a skipped hour shifts forward; a repeated hour takes the first occurrence

**Use case.** "Every Monday at 9am, show me applications with no reply for 5+ days." Monday arrives, the digest is prepared, and it waits in Scheduled Review. Nothing is sent until you approve.

---

### 10. External feeds

*Requires `feature_nvoids_enabled` (on by default; sync must still be triggered).*

Ingests job postings from an external feed alongside Gmail, running them through the same parse → filter → score pipeline. Optional auto-sync on an interval. Includes recovery of recruiter identity from masked postings.

---

### 11. Telegram control

*Requires `TELEGRAM_BOT_TOKEN`.*

A bot for reviewing and acting on the queue from your phone. Access is restricted by `TELEGRAM_ALLOWED_CHAT_IDS`, and actions require `TELEGRAM_ACTION_PIN` with a TTL. This is the one surface with a real credential gate, because it is reachable from outside your network.

---

## User guide: common workflows

### Connecting Gmail

1. Open **Settings → Gmail**
2. Click **Connect** → complete Google OAuth consent
3. *(Optional)* Set a **label filter** to restrict intake to one Gmail label
4. **Run Queue → Sync Gmail**
5. Watch progress in **Recent Runs**

> Restricting to a label is the recommended starting point. Create a Gmail filter that labels recruiter mail, point CodeJob at that label, and the pipeline never sees the rest of your inbox.

### Reviewing and sending

1. Open **Needs Review** — the badge shows the count
2. Click a card to expand parsed fields, provenance, and score
3. Choose your action:
   - **Approve & Send** — sends the reply with the selected resume attached
   - **Reject** — records the reason
   - **Regenerate** — redraft the reply
   - **Resolve recipients** — fix a wrong or missing To-address
4. For bulk work, multi-select and use the action bar

### Tuning what reaches you

If too much noise arrives:

- Raise **Qualification threshold** (Settings) — default `0.6`
- Enable **strict candidate screening**
- Tighten hard-filter policy (location, seniority)

If real opportunities are being dropped:

- Lower the threshold
- Check **Failed Mapping** — they may be parse failures, not rejections
- Check **Recent Runs → skipped items** for the specific reason

### Using the Assistant

1. Enable it: `FEATURE_CHAT_ENABLED=true`, ensure Ollama is running, recreate the backend
2. Open **CodeJob Assistant**
3. Ask in plain language; attach a file with the paperclip if relevant
4. If a reply includes a **proposal card**, review it and click to execute — the model cannot perform the action itself

### Investigating a rejection

1. **Recent Runs** → open the run
2. Expand **skipped items**
3. Read the reason code — hard filter, intent gate, or score
4. Adjust the corresponding setting

---

## Developer setup

### Prerequisites

| Tool | Version |
| --- | --- |
| Python | ≥ 3.12 |
| Node.js | ≥ 20 |
| [uv](https://github.com/astral-sh/uv) | latest |
| Docker Desktop | latest |

### Backend

```bash
cd backend
uv sync                                  # install from uv.lock
uv run alembic upgrade head              # apply migrations
uv run uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd dashboard
npm install
npm run dev                              # http://localhost:5173
```

Point the dashboard at your API with `dashboard/.env`:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

### Worker

```bash
cd backend
uv run rq worker gmail_sync nvoids_sync automation_run embedding_generation scheduled_task \
  --url redis://localhost:6379/0 --with-scheduler
```

> All five queue names must be present. A queue nothing consumes accepts jobs silently and runs none of them — and no test covers that line.

---

## Environment variables

Backend configuration lives in `backend/.env`; database credentials in `backend/.env.postgres`. Both are gitignored (`backend/.gitignore` ignores `.env*`).

### Required

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://user:pass@postgres:5432/codejob` |
| `REDIS_URL` | `redis://redis:6379/0` |
| `POSTGRES_USER` · `POSTGRES_PASSWORD` · `POSTGRES_DB` | Postgres service credentials (`.env.postgres`) |
| `OWNER_ID` | Scopes every record. Default `default-owner` |

### Gmail / Google

| Variable | Description |
| --- | --- |
| `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` | OAuth client credentials |
| `GOOGLE_REDIRECT_URI` | Default `http://localhost:8080/` |
| `GOOGLE_TOKEN_PATH` | Token store. Default `./data/google_token.json` |
| `GMAIL_LABEL_FILTER` | Restrict intake to one label |
| `GOOGLE_LOGIN_HINT` | Pre-fills the consent screen |

### AI providers

| Variable | Default | Description |
| --- | --- | --- |
| `INTENT_GATE_PROVIDER` | `deepseek` | `deepseek` · `groq` · `taxonomy` |
| `Deepseek_API_KEY` | — | DeepSeek credential (note the casing) |
| `DEEPSEEK_MODEL_PRO` | `deepseek-v4-pro` | Escalation model |
| `CodeJobGroq` · `CodeJobGroq_Model` | — | Groq credential and model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Assistant LLM host |
| `OLLAMA_CHAT_MODEL` | `gemma4:31b-cloud` | Primary chat model |
| `OLLAMA_CHAT_MODEL_FALLBACK` | `minimax-m3:cloud` | First fallback |
| `INTENT_GATE_TIMEOUT_SECONDS` | `12.0` | Total budget per email, not per attempt |

### Embeddings

| Variable | Default |
| --- | --- |
| `SEMANTIC_EMBEDDING_PROVIDER` | `sbert` |
| `SEMANTIC_EMBEDDING_SBERT_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `SEMANTIC_EMBEDDING_SBERT_DEVICE` | `cpu` |
| `SEMANTIC_KEYWORD_WEIGHT` · `SEMANTIC_SIMILARITY_WEIGHT` | `0.6` · `0.4` |
| `OPENROUTER_API_KEY` · `HF_TOKEN` | — |

### Integrations

| Variable | Description |
| --- | --- |
| `SEARXNG_URL` | Enables Assistant web search when set |
| `TELEGRAM_BOT_TOKEN` · `TELEGRAM_ALLOWED_CHAT_IDS` · `TELEGRAM_ACTION_PIN` | Telegram bot |
| `GITHUB_TOKEN` · `GITHUB_REPO` | In-app issue reporting |
| `PUBLIC_BASE_URL` · `TRACKING_SECRET_KEY` | Email open tracking |
| `GOOGLE_SHEETS_TRACKING_*` | Optional Sheets append on send |

---

## Database and migrations

PostgreSQL 16, managed by Alembic — **60 revisions** (`0001` → `0060`).

```bash
uv run alembic upgrade head        # apply all
uv run alembic current             # current revision
uv run alembic history             # full chain
uv run alembic downgrade -1        # step back one
```

### ⚠️ Migrations run automatically on backend boot

`docker-compose.yml` applies `alembic upgrade head` before starting Uvicorn. **A bad migration takes the backend down at startup**, not at deploy review time. This has happened: an index on an unbounded `Text` column exceeded the Postgres btree key limit and stopped the service.

**Rules for every new migration:**

1. **Never index an unbounded `Text` column.** Index only fixed-width columns (`String(n)`), FKs, booleans, or datetimes.
2. **Make it idempotent.** Guard with `inspector.has_table(...)` / column inspection so re-application is safe.
3. **Guard dialect-specific DDL.** SQLite has no `ALTER COLUMN`.
4. **Write a migration test** under `backend/tests/test_migration_*.py`.

### SQLite → PostgreSQL cutover

The project migrated from SQLite. Tooling is retained:

```bash
python scripts/copy_sqlite_to_postgres.py --source <sqlite-url> --target <postgres-url>
python scripts/verify_data_parity.py      --source <sqlite-url> --target <postgres-url>
python scripts/verify_schema_equivalence.py --database-url <postgres-url>
```

Never copy from a SQLite file while the application is writing to it. Recreate and migrate the target, copy from a stopped-app snapshot, verify parity, then start the backend.

---

## Configuration and feature flags

Two kinds of flags, and the distinction matters.

### Environment flags (require a container restart)

| Flag | Default | Effect |
| --- | --- | --- |
| `FEATURE_CHAT_ENABLED` | `false` | The Assistant. All `/chat/*` routes 404 when off |
| `FEATURE_CHAT_ACTIONS_ENABLED` | `false` | Registers `propose_*` tools |
| `FEATURE_SCHEDULING_ENABLED` | `false` | Scheduled tasks, its two nav rows, and two MCP tools |
| `FEATURE_RELATIONSHIP_INTELLIGENCE_ENABLED` | `false` | Clustering (shadow mode — computes, does not display) |
| `FEATURE_RELATIONSHIP_SURFACING_ENABLED` | `false` | Displays clustering results |
| `FEATURE_DEEPSEEK_ENABLED` | `false` | DeepSeek extraction paths |

### Persisted settings (changeable in the UI, effective immediately)

| Setting | Default | Effect |
| --- | --- | --- |
| `qualification_threshold` | `0.6` | Score cutoff for routing |
| `feature_auto_polling` | `false` | Automatic Gmail polling |
| `feature_auto_poll_interval_minutes` | `10` | Poll cadence |
| `feature_auto_send` | `false` | **Sends without review.** Leave off unless you are certain |
| `feature_nvoids_enabled` | `true` | External feed |
| `feature_ai_enabled` · `feature_ai_extractor_enabled` | `false` | AI drafting and extraction |
| `feature_semantic_enabled` | `false` | Embedding-based similarity |
| `feature_strict_candidate_screening_enabled` | `false` | Stricter pre-scoring screen |
| `feature_applications_enabled` | `false` | Application Tracking |
| `feature_resume_tracking_enabled` | `false` | Resume Tracking |
| `feature_reply_inbox_enabled` | `false` | Reply Inbox |
| `feature_email_tracking_enabled` | `false` | Open tracking |
| `feature_scheduling_sweep_interval_minutes` | `15` | Sweep cadence (5-minute floor) |

**Applying an environment flag change:**

```bash
docker compose up -d --no-deps --force-recreate backend
```

---

## Testing

### Backend tests

```bash
cd backend
uv run python -m pytest                      # full suite
uv run python -m pytest -n auto              # parallel (pytest-xdist)
uv run python -m pytest tests/test_chat_attachments.py -v
uv run python -m pytest --testmon            # only tests affected by your changes
```

A global 60-second timeout is configured in `pyproject.toml` to stop hangs from blocking a run.

### Frontend tests

```bash
cd dashboard
npm test                                     # vitest, 20s timeouts
npm run lint
npm run build                                # tsc -b && vite build
```

> The 20-second timeout is deliberate: mounting the full `App` under jsdom contends enough to exceed the default.

### Browser / QA sweep

`dashboard/e2e/qa-sweep.mjs` drives Playwright directly (there is no `playwright.config.ts`):

```bash
cd dashboard
npx playwright install chromium              # first run only
node e2e/qa-sweep.mjs
```

It captures screenshots and scans for layout defects across viewports. Output lands in `dashboard/e2e/qa-report/`.

### Migration tests

```bash
cd backend
uv run python -m pytest tests/test_migration_0056.py -v
```

### CI

One workflow: `.github/workflows/db-migrations.yml`. It runs `alembic upgrade head` plus `verify_schema_equivalence.py` against **both SQLite and PostgreSQL**, triggered on changes to `backend/alembic/**`, `backend/app/models.py`, and related paths.

> **CI does not run the application test suites.** It validates the migration chain and schema equivalence only. Backend and dashboard tests must be run locally. Adding them to CI is an open improvement.

---

## Deployment and operations

### Standard deploy

```bash
docker compose build
docker compose up -d
docker compose logs -f backend
```

### Verifying a deploy

```bash
docker compose ps                                   # all services healthy
docker exec codejob-postgres psql -U codejob -d codejob -t -c \
  "select version_num from alembic_version;"        # expected revision
docker inspect codejob-worker --format '{{join .Config.Cmd " "}}'   # all 5 queues
curl -s localhost:8000/health
```

### After changing the worker's queue list

`docker compose up -d` will **not** recreate a container whose command changed in a way Compose considers compatible. Force it:

```bash
docker compose up -d --force-recreate worker
```

### Build performance

The backend image installs from `uv.lock` via `uv export`, because `uv pip install .` is the pip-compatible interface and **does not read the lockfile** — it re-resolves all 212 packages on every build. The dependency layer is copied before application source, so an ordinary code edit does not invalidate it.

> **Torch is not pinned to a CPU-only wheel.** It remains the dominant contributor to image size and build time. Pinning a CPU wheel is the single biggest remaining build win.

### Backups

```bash
docker exec codejob-postgres pg_dump -U codejob codejob > backup-$(date +%F).sql
```

Also back up the `backend_data` volume — it holds resumes, attachments, chat attachments, and the Google token.

### Monitoring

- **Recent Runs** — per-run outcomes with skipped-item drill-down
- **Settings → AI status** — provider reachability
- `docker compose logs -f worker` — queue processing

---

## Known limitations

**Honest assessment of what is not finished.**

### Security

- **No application authentication.** Single-owner by design; `allow_origins=["*"]`. Never expose the API or dashboard to an untrusted network. See [Access model](#access-model-user-developer-administrator).

### Setup

- **`backend/.env.postgres` has no committed template.** It is required for the stack to start and gitignored with no `.env.postgres.example`. A fresh clone fails until it is created by hand. (The [Quick start](#quick-start) above documents the variable names — that is currently the only place they are written down.)

### Features held behind flags for measured reasons

- **Relationship intelligence signals are too sparse to cluster on.** Measured against production (1,114 opportunities): `end_client` 6.7%, `domain` 6.3%, `implementation_partner` 1.6%, `prime_vendor` **0%**. Clustering cannot key on these fields at these rates, which is why surfacing ships off and requires a measured precision bar first.
- **Scheduling condition defaults are refuted, not merely unmeasured.** A read-only replay of the shipped predicates against production showed two of three can never fire, and the third matches 74% of the corpus. Re-derive the defaults before enabling scheduling.

### Testing

- **CI validates schema only.** The application test suites — backend and dashboard — are not run by any workflow.
- Migration tests exist but are not in CI's explicit pytest list; migrations are exercised by `alembic upgrade head` instead.

### Architecture

- **`main.py` is a ~9,800-line monolith** holding most of the 216 endpoints. `App.tsx` is similarly central on the frontend. Both are known refactor targets.
- **Route ordering is load-bearing.** Routes must be registered *before* `app.mount("/", chat_mcp_app)` or they are silently shadowed when chat is enabled.
- Some AI and embedding status is process-memory state and does not survive a restart.

### Operational

- Migrations run on backend boot, so a bad migration is a startup outage.
- The external feed integration depends on a third-party source whose availability is outside this project's control.

---

## Repository layout

```text
CODEJOB/
├── backend/
│   ├── alembic/versions/       # 60 migrations
│   ├── app/
│   │   ├── main.py             # ~9.8k lines, most of 216 endpoints
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── config.py           # Settings — every env var
│   │   ├── ai/                 # intent gate, chat, prompting
│   │   ├── mcp_server/         # MCP tools for the Assistant
│   │   ├── services/           # domain services
│   │   │   └── scheduling/     # sweep, handlers, timezone
│   │   ├── premium_numbers/    # contact identity
│   │   ├── routers/chat.py     # the one extracted router
│   │   └── jobs/               # RQ queues and tasks
│   ├── scripts/                # backfills, cutover, verification
│   ├── tests/                  # pytest suite
│   └── Dockerfile
├── dashboard/
│   ├── src/
│   │   ├── App.tsx             # central workflow container
│   │   ├── components/         # shared UI
│   │   └── features/           # chat, scheduling, premium_numbers, …
│   ├── e2e/qa-sweep.mjs        # Playwright QA sweep
│   └── Dockerfile
├── docs/                       # architecture, features, data, taxonomy
├── searxng/settings.yml
└── docker-compose.yml
```

---

## Contributing

1. Branch from `main`
2. Follow the [migration rules](#database-and-migrations) if you touch the schema
3. Run backend and dashboard tests locally — CI will not run them for you
4. Open a PR against `main`

## License

No license file is currently present. The repository is private.
