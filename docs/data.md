# CODEJOB Data Models and Data Flow

Audit date: 2026-05-17  
Branch: `snowball-md`

## 1 Core entities

### `RecruiterEmail`

Primary workflow record with:

- message metadata and source identifiers
- queue/send state fields
- routing evidence/candidates and confidence
- draft metadata + quality payload exposure
- labeling/send tracking fields

### `UserSettings`

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

## 6 Schema/runtime notes

- Schema response contracts are defined in `backend/app/schemas.py`.
- Routing evidence/candidates are stored as JSON text and parsed in schema validators.
- SQLite schema evolution is currently additive at startup via `ensure_sqlite_phase0_columns()`.
- Query bucket persistence is in `UserSettings.saved_gmail_queries_json` (not a standalone table).

Evidence basis: code inspection  
Verification limits: model/flow mapping reviewed from source; full backend suite currently blocked by stale `test_phone_attribution.py` import.
