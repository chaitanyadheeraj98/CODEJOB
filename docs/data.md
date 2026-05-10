# CODEJOB Data Structures and Data Flow

## 1) Main data models (backend ORM)

Defined in `backend/app/models.py`.

| Model | Key fields | Purpose | Storage |
|---|---|---|---|
| `RecruiterEmail` | sender, subject, body, role, score, state, routing fields, draft fields, send metadata | Primary candidate/work-item record through queue lifecycle | SQLite table `recruiter_emails` |
| `UserSettings` | query/date defaults, thresholds, feature toggles, signature/template, policy_json | Per-owner operational configuration | SQLite table `user_settings` |
| `ResumeAsset` | file_path, file_name, sha256, version, is_current | Resume file version tracking for attachments/context | SQLite table `resume_assets`; file on disk (`resume_storage_dir`) |
| `SyncRun` | sync_batch_id, imported/skipped/error counts, timestamps | Captures each sync run outcome | SQLite table `sync_runs` |
| `DraftEditFeedback` | original_draft, edited_draft, recruiter_email_id | Captures manual draft edits for learning/traceability | SQLite table `draft_edit_feedback` |
| `RecipientRoutingFeedback` | sender_domain, corrected_to, corrected_cc, evidence flags | Stores manual recipient corrections for routing improvement | SQLite table `recipient_routing_feedback` |
| `ProductivityEvent` | event_type, source, weight, metadata_json, occurred_at | Event log for dashboard analytics/trend | SQLite table `productivity_events` |

## 2) API schemas and types

### Backend request models (`backend/app/schemas.py`)
- `SettingsRequest`
- `IngestEmailRequest`
- `ApproveSendRequest`
- `RejectRequest`
- `BulkRejectRequest`
- `ResolveRecipientsRequest`
- `AutomationRunRequest`
- `ProductivityEventCreateRequest`

### Backend response models
- `SettingsResponse`
- `EmailResponse`
- `CandidateListResponse`
- `GmailStatusResponse`
- `AIStatusResponse`
- `GmailSyncResponse`
- `OAuthStartResponse`
- `AutomationRunResponse`
- `ResumeResponse`
- `ProductivityEventResponse`
- `ProductivityTrendResponse`

### Frontend TypeScript structures (`dashboard/src/App.tsx`)
- `GmailStatus`, `AiStatus`, `TelegramStatus`
- `SettingsPayload`, `DynamicPolicy`
- `Candidate`, `RoutingEvidence`, `CandidateListResponse`
- `AutomationRunResponse`, `OAuthStartResponse`
- `ProductivityEvent`, `ProductivityTrendResponse`, `ProductivityBarPoint`

## 3) Other important in-memory/state structures

- Frontend local state via React `useState` for settings, queues, logs, routing fixes, productivity data.
- Backend process-level state in `main.py`:
  - OAuth/AI status timestamps and errors
  - Telegram auth sessions
  - Auto-run thread controls

## 4) Data storage and management locations

| Data category | Where defined | Where stored/managed |
|---|---|---|
| Operational settings | `UserSettings`, `SettingsRequest` | SQLite `user_settings` + frontend state while editing |
| Candidate emails and workflow state | `RecruiterEmail`, `EmailResponse` | SQLite `recruiter_emails` |
| Resume binary/context | `ResumeAsset` + file upload endpoint | Disk (`./data/resumes`) + SQLite metadata |
| Sync execution history | `SyncRun` | SQLite `sync_runs` |
| Routing corrections | `RecipientRoutingFeedback` | SQLite `recipient_routing_feedback` |
| Productivity analytics | `ProductivityEvent` | SQLite `productivity_events` |
| Telegram authorization session | `telegram_auth_sessions` dict | In-memory backend runtime only |
| Frontend queue/review state | TS types in `App.tsx` | In-memory React state |
| Gmail inbox/send data | Gmail API payloads | External Gmail service |
| Optional tracking row data | Sheet row arrays | External Google Sheets |

## 5) API request/response structure patterns

Common patterns:
- JSON requests for settings/actions.
- Typed JSON responses for status/queues.
- Pagination-like fields in candidates response: `items`, `next_cursor`, `has_next`.
- Event/trend endpoints provide time-windowed analytics with bucketed bars.

## 6) End-to-end data flow

### A) Input to queue
1. User triggers run from dashboard.
2. Backend resolves effective query and policy.
3. Gmail client fetches candidate messages.
4. Parser + qualification + routing logic compute structured fields.
5. Record persisted into `RecruiterEmail` with resulting state.
6. Frontend refreshes queue lists and displays normalized candidate objects.

### B) Queue to send
1. User edits draft and approves in needs_review.
2. Backend validates required fields (routing safety, resume, body).
3. Gmail send API called with To/CC/thread + attachment.
4. Email record updated (`approved_sent`, timestamps, gmail_sent_id).
5. Productivity event logged; optional Google Sheets row appended.
6. Sent item appears in dashboard sent queue/history.

### C) Failure correction loop
1. Failed routing item appears in failed_mapping.
2. User supplies corrected To/CC.
3. Backend stores correction feedback and regenerates draft.
4. Item moves back to needs_review.

### D) Analytics flow
1. Backend emits `ProductivityEvent` on key actions/state changes.
2. Frontend calls `/analytics/events` and `/analytics/trend`.
3. UI renders trend bars, deltas, and activity log in live monitor.
