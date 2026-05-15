# CODEJOB Data Models and Data Flow (Current Branch)

## 1) Core data entities

### RecruiterEmail
- Purpose: canonical candidate/workflow record
- Key fields: sender, subject, body, parsed role/location/skills, score, state, routing fields, draft fields, send metadata, external Gmail IDs
- Used by: run pipeline, review queues, approve/reject, analytics hooks, phone intelligence capture

### UserSettings
- Purpose: persisted runtime behavior and defaults
- Key fields: gmail query/date defaults, threshold, employer domains, AI/semantic toggles, auto-run, signatures, fallback template, policy JSON
- Used by: run orchestration, routing domain behavior, UI settings

### ResumeAsset
- Purpose: versioned resume metadata + disk file reference
- Key fields: file_path, file_name, version, is_current, sha256, semantic embedding cache
- Used by: draft generation, approve-send attachment gate

### SyncRun
- Purpose: historical sync run record for import/skipped/error counts
- Used by: run reporting, telegram recent run summaries

### DraftEditFeedback
- Purpose: stores operator-edited draft deltas
- Used by: draft quality/learning trace

### RecipientRoutingFeedback
- Purpose: stores manual recipient corrections by sender domain
- Used by: learned routing pair hints

### ProductivityEvent
- Purpose: event log for trend and activity monitor
- Event types include: `approved_sent`, `needs_review_marked`, `failed_mapping_marked`, view events, `recent_run_recorded`

## 2) Phone intelligence entities

### PremiumNumberLead
- Extracted per recruiter email and normalized phone number
- Stores owner/company/designation/purpose/confidence/relevance details and source evidence

### NumberReviewQueue
- Unknown numbers needing manual classification cards
- State starts at `pending`, transitions to classified states

### RecruiterNumber
- Recruiter bucket keyed by owner + normalized phone number
- Includes recruiter identity metadata and first source email id

### EmployerNumber
- Employer bucket keyed by owner + normalized phone number
- Separate from recruiter bucket by design

### RecruiterOpportunity
- Opportunity card linked to recruiter number and source email/message
- Unique by owner + recruiter_number_id + gmail_message_id

## 3) Important storage/uniqueness rules

- `recruiter_emails.external_message_id` unique index
- `ux_recruiter_numbers_owner_phone`
- `ux_employer_numbers_owner_phone`
- `ux_recruiter_opportunities_owner_recruiter_msg`
- `ux_number_review_queue_owner_phone_email`

These constraints are fundamental to duplicate prevention.

## 4) End-to-end data flow

1. Run endpoint fetches Gmail candidates
2. Candidate parsed/scored/routed and saved/updated as `RecruiterEmail`
3. Number extraction paths run:
   - premium leads write/update
   - recruiter/employer/unknown intelligence decisions
4. Unknown numbers go to review queue (`NumberReviewQueue`)
5. Manual classification moves numbers into recruiter/employer buckets
6. Recruiter classification can create a linked `RecruiterOpportunity`
7. Needs-review candidates can be approved and sent (Gmail + optional Sheets)
8. Productivity events are recorded for trend views

## 5) API schema anchors

Primary schema module: `backend/app/schemas.py`

Key request schemas:
- `SettingsRequest`, `AutomationRunRequest`, `ApproveSendRequest`, `RejectRequest`, `ResolveRecipientsRequest`

Key response schemas:
- `EmailResponse`, `CandidateListResponse`, `AutomationRunResponse`, `PremiumNumber*`, `RecruiterNumberResponse`, `EmployerNumberResponse`, `RecruiterOpportunityResponse`, `Productivity*`
