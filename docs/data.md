<!-- markdownlint-configure-file {"MD013": false} -->

# CODEJOB Data Models and Data Flow

Audit date: 2026-08-14
Branch: `semantic-embeddings`

## 1 Core entities

### `RecruiterEmail`

Primary workflow record with:

- message metadata and source identifiers
- queue/send state fields
- routing evidence/candidates and confidence
- draft metadata + quality payload exposure
- labeling/send tracking fields

### Settings record

Owner-scoped settings row including:

- query defaults and saved query JSON
- date defaults and qualification filters
- feature flags
- fallback draft/signature fields
- serialized policy JSON

### `ResumeAsset`

Versioned resume file metadata + optional semantic embedding cache.

### Other core tables

- `SyncRun`
- `DraftEditFeedback`
- `RecipientRoutingFeedback`
- `ProductivityEvent`

### Chat history

- `ChatSession`: owner-scoped conversation title plus created and updated timestamps.
- `ChatMessage`: ordered user, assistant, and tool audit rows linked to a session. Tool rows retain the tool name and compact call metadata.

## 2 Phone-intelligence entities

- `PremiumNumberLead`
- `NumberReviewQueue`
- `RecruiterNumber`
- `EmployerNumber`
- `RecruiterOpportunity`

## 3 State and enum contracts

Candidate states used in code:

- `needs_review`
- `failed`
- `processed_skipped`
- `approved_sent`
- `rejected`
- `auto_rejected`

Resume context statuses:

- `injected`, `limited`, `missing_resume`, `extract_failed`, `rules_only`

Draft quality labels:

- `Excellent`, `Strong`, `Good`, `Review`, `Risky`

Opportunity statuses:

- `New`, `Called`, `Applied`, `Follow Up`, `Closed`, `Not Interested`

## 4 Duplicate-prevention and uniqueness

Active uniqueness protections include:

- `recruiter_emails.external_message_id` unique
- `ux_recruiter_numbers_owner_phone`
- `ux_employer_numbers_owner_phone`
- `ux_recruiter_opportunities_owner_recruiter_msg`
- `ux_number_review_queue_owner_phone_email`

## 5 Data flow summary

1. Gmail candidate enters orchestration.
2. `RecruiterEmail` inserted/updated with scoring/routing/draft details.
3. Side effects run:
   - premium lead extraction/upsert
   - phone intelligence (review queue / recruiter/employer matching / opportunity create)
   - Gmail label decision/application metadata
4. Manual approval path updates send state and emits analytics.
5. Optional Sheets append runs as best-effort side effect.

Chat uses a separate read-only flow:

1. A user message is validated and persisted under an owner-scoped `ChatSession`.
2. LangGraph may call an owner-scoped MCP tool for candidates, runs, inbox, AI health, or settings.
3. Tool and assistant audit rows are persisted, while assistant text streams to the dashboard over SSE.

## 6 Schema/runtime notes

- Schema response contracts are defined in `backend/app/schemas.py`.
- Routing evidence/candidates are stored as JSON text and parsed in schema validators.
- Alembic is the only production schema writer; startup verifies the database is at head and never patches it.
- The Compose production database is PostgreSQL 16 on the internal `postgres:5432` service; the preserved SQLite volume and final stopped-app snapshot are rollback-only after the 2026-08-18 cutover.
- Query bucket persistence is in `UserSettings.saved_gmail_queries_json` (not a standalone table).
- Alembic revision `20260814_0015` creates `chat_sessions` and `chat_messages`; upgrade and downgrade are covered by a focused migration test.
- Alembic revision `20260818_0020` widens nine fields whose real SQLite values exceeded their declared `VARCHAR` capacities; SQLite keeps its equivalent unbounded representation without a table rebuild.

- Audit date: 2026-08-18
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: fresh SQLite/PostgreSQL migration chains, complete live-data content parity, production Docker startup, and representative API/worker reads were tested; the unfiltered backend suite remains blocked during collection by the stale `app.phone_attribution` import and retains its documented unrelated failure baseline.
