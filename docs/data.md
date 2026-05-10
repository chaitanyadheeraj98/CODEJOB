# Data Structure and Flow

## 1. Core Backend Data Models (SQLAlchemy)

Defined in `backend/app/models.py`.

| Model | Key Fields | Purpose | Storage |
|---|---|---|---|
| `RecruiterEmail` | sender, subject, body, role, score, decision/state, routing fields, draft fields, send metadata | main candidate/email processing record | SQLite table `recruiter_emails` |
| `UserSettings` | query/date defaults, filter settings, feature flags, template/signature, policy JSON | per-owner runtime configuration | SQLite table `user_settings` |
| `ResumeAsset` | file_path, file_name, mime, sha256, version, is_current | uploaded resume versions and active file tracking | SQLite table `resume_assets` + local file storage |
| `SyncRun` | sync_batch_id, counts, start/end timestamps | audit trail of sync/import runs | SQLite table `sync_runs` |
| `DraftEditFeedback` | original_draft, edited_draft, recruiter_email_id | captures user draft edits | SQLite table `draft_edit_feedback` |
| `RecipientRoutingFeedback` | sender_domain, corrected_to, corrected_cc, evidence flags | stores corrected routing patterns for learning | SQLite table `recipient_routing_feedback` |

## 2. API Schemas / DTOs (Pydantic)

Defined in `backend/app/schemas.py`.

### Request schemas
- `IngestEmailRequest`
- `ApproveSendRequest`
- `RejectRequest`
- `BulkRejectRequest`
- `ResolveRecipientsRequest`
- `SettingsRequest`
- `AutomationRunRequest`

### Response schemas
- `SettingsResponse`
- `ResumeResponse`
- `EmailResponse`
- `CandidateListResponse`
- `GmailStatusResponse`
- `AIStatusResponse`
- `GmailSyncResponse`
- `OAuthStartResponse`
- `AutomationRunResponse`
- `TelegramStatusResponse`

## 3. Domain Structures (Rules/AI/Routing)

### `phase0.py`
- `RoutingEvidence` dataclass
- `RoutingResult` dataclass
- Parsed-email object (`dict[str, str | int | bool]`) containing role/location/salary/skills/F2F/contact flags
- Rule constant collections: skill keywords, recruiter hints, employer domains

### `app/ai/*`
- Prompt tuple: `(system_prompt, user_prompt)`
- `ReplyGenerationResult` dataclass (`draft_text`, `source`, `ai_model`, `ai_error`)

### `gmail_client.py`
- `GmailMessageCandidate` typed dict for normalized Gmail API message payload

## 4. Frontend State and Types

Defined mainly in `dashboard/src/App.tsx` and `dashboard/src/features/ai/*`.

### Main frontend types
- `GmailStatus`, `AiStatus`, `TelegramStatus`
- `SettingsPayload`
- `DynamicPolicy` and `PolicyProfileName`
- `Candidate`, `RoutingEvidence`, `CandidateListResponse`
- `AutomationRunResponse`, `OAuthStartResponse`, `ResumeAsset`

### Main React state slices
- Status/state: `status`, `aiStatus`, `telegramStatus`, `running`, `saving`, `error`
- Configuration: `settings`, `dynamicPolicyBeta`, profile selections
- Queue data: `queue`, `failedQueue`, `sentQueue`, `logs`
- Editing state: `draftEdits`, `routingFixes`, selected page and input drafts

## 5. Where Data is Stored and Managed

| Data Category | Storage Location |
|---|---|
| Operational entities (emails, settings, resumes metadata, run logs, feedback) | SQLite DB (`backend/app/db.py` + models) |
| Resume file bytes | Local filesystem path under `settings.resume_storage_dir` |
| OAuth token payload | Local token JSON file (`settings.google_token_path`) |
| Frontend transient UI state | In-memory React state (`useState`) |
| AI prompts/outputs | In-memory during request lifecycle; persisted result in `RecruiterEmail` draft fields |
| Gmail message source data | External Gmail API |
| Telegram updates | External Telegram Bot API |
| Optional tracking history | External Google Sheets row append |

## 6. API Request/Response Data Flow

1. **User input in dashboard** updates local React state.
2. **Frontend sends JSON or multipart requests** to FastAPI endpoints.
3. **Pydantic validates payloads** and transforms responses.
4. **Business logic in `main.py` + `phase0.py`** computes parsing, scoring, routing, drafting, and state transitions.
5. **SQLAlchemy writes/reads records** from SQLite.
6. **External integrations** (Gmail/AI/Telegram/Sheets) enrich or execute side effects.
7. **Backend returns typed responses** consumed by frontend and rendered in cards/lists/forms.

## 7. End-to-End Candidate Lifecycle

1. Gmail message fetched and normalized (`GmailMessageCandidate`).
2. Message parsed into structured fields (`parse_email` output).
3. Qualification and routing decisions computed.
4. Candidate record created/updated in `RecruiterEmail`.
5. Candidate appears in queue/failed/sent views via `/candidates` API.
6. User actions (approve/reject/resolve recipients) update DB state and may trigger Gmail send + Sheets logging.
7. Final state and metadata are displayed in frontend and available for Telegram summaries.
