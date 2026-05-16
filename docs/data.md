# CODEJOB Data Models and Data Flow (Current Branch)

## 1) Core entities

### RecruiterEmail
- Canonical workflow row for each processed message.
- Stores parse outputs, routing state, draft metadata, and send metadata.

### UserSettings
- Stores query defaults, saved query bucket, thresholds, toggles, signatures, and policy JSON.

### ResumeAsset
- Versioned resume metadata and active attachment reference.

### SyncRun
- Records per-run imported/skipped/error counts.

### DraftEditFeedback / RecipientRoutingFeedback
- Captures human corrections for drafts and recipient mapping.

### ProductivityEvent
- Stores weighted view/action events for trend charts.

## 2) Number intelligence entities

### PremiumNumberLead
- Extracted phone lead catalog from recruiter email content.

### NumberReviewQueue
- Unknown-number cards awaiting manual classification.

### RecruiterNumber
- Recruiter identity bucket keyed by owner + normalized phone.

### EmployerNumber
- Employer identity bucket keyed by owner + normalized phone.

### RecruiterOpportunity
- Opportunity card keyed by owner + recruiter_number_id + gmail_message_id.
- Includes status, notes, and optional cold-call script fields.

## 3) Storage uniqueness anchors

- `recruiter_emails.external_message_id` unique index
- `ux_recruiter_numbers_owner_phone`
- `ux_employer_numbers_owner_phone`
- `ux_recruiter_opportunities_owner_recruiter_msg`
- `ux_number_review_queue_owner_phone_email`

These constraints enforce idempotency and duplicate suppression.

## 4) Data flow summary

1. `POST /automation/run-once` loads Gmail candidates.
2. Candidate pipeline writes/updates `RecruiterEmail` with state decision.
3. Premium number extraction/classification updates lead + bucket entities.
4. Unknown numbers enter `NumberReviewQueue`; manual actions classify them.
5. Recruiter classification may create/update `RecruiterOpportunity`.
6. Gmail label decision is applied for processed candidate messages.
7. Approve-send updates sent fields and records productivity events.

## 5) API schema anchor

Schema source of truth: `backend/app/schemas.py`.

Notable request/response groups:
- settings + policy (`SettingsRequest`, `SettingsResponse`)
- run + queue (`AutomationRunRequest`, `AutomationRunResponse`, `CandidateListResponse`)
- review actions (`ApproveSendRequest`, `ResolveRecipientsRequest`, `RejectRequest`)
- premium-number domain (`PremiumNumber*`, `UnknownNumberReviewCardResponse`, `RecruiterNumberResponse`, `EmployerNumberResponse`, `RecruiterOpportunityResponse`)
- analytics (`Productivity*`)
