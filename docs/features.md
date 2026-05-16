# CODEJOB Features (Current Implementation)

## Core automation

| Feature | What it does | Key files |
|---|---|---|
| Gmail OAuth bootstrap | Starts OAuth flow and checks auth readiness | `backend/app/gmail_client.py`, `backend/app/main.py` |
| Run-once orchestration | Fetches unread Gmail candidates and runs parse/filter/score/routing/draft pipeline | `backend/app/main.py`, `backend/app/automation/run_orchestrator.py` |
| Policy + query controls | Applies policy JSON and saved-query bucket behavior | `backend/app/main.py`, `backend/app/query_bucket/*`, `dashboard/src/features/query_bucket/*` |
| AI + fallback draft generation | Uses AI when enabled; falls back to rules-only draft path | `backend/app/ai/*`, `backend/app/phase0.py` |
| Optional semantic score blending | Combines rules and embeddings for qualification score | `backend/app/semantic/*` |

## Manual safety workflow

| Feature | What it does | Key files |
|---|---|---|
| Needs Review queue | Holds qualified items pending human decision | `dashboard/src/App.tsx`, `backend/app/main.py` |
| Approve & Send gate | Enforces safety checks before Gmail send | `backend/app/main.py` |
| Failed Mapping recovery | Allows To/CC correction and requeue to review | `dashboard/src/App.tsx`, `backend/app/main.py` |
| Reject actions | Single and bulk reject paths | `backend/app/main.py` |

## Number intelligence and opportunities

| Feature | What it does | Key files |
|---|---|---|
| Premium number extraction | Extracts and stores phone leads with relevance metadata | `backend/app/premium_numbers/extraction.py` |
| Unknown number review | Manual queue to classify unknown numbers | `/number-review*` endpoints, `dashboard/src/App.tsx` |
| Recruiter/Employer buckets | Maintains separate deduped identity stores | `RecruiterNumber`, `EmployerNumber`, `backend/app/main.py` |
| Bucket swap actions | Swaps recruiter/employer assignment while preserving consistency | `/recruiter-numbers/*/swap-to-employer`, `/employer-numbers/*/swap-to-recruiter` |
| Recruiter opportunities | Tracks recruiter-message opportunity cards and status updates | `RecruiterOpportunity`, `/recruiter-opportunities*` |
| Cold-call script generation | Generates sanitized script for a recruiter opportunity | `backend/app/cold_call/*`, `/recruiter-opportunities/{id}/generate-cold-call-script` |

## Inbox labeling and operations

| Feature | What it does | Key files |
|---|---|---|
| Gmail labeling preview | Runs rule + AI fallback labeling preview | `backend/app/gmail_labeling/*`, `/gmail/labeling/preview` |
| Runtime Gmail label apply | Applies resolved label after processing candidate | `backend/app/gmail_labeling/service.py`, orchestrator integration |
| Productivity analytics | Tracks activity events and trend buckets | `ProductivityEvent`, `/analytics/*` |
| Telegram operations | Remote run/status/approve/reject helpers | `backend/app/telegram_bot.py`, `backend/app/main.py` |
| Optional Sheets tracking | Appends approved send rows to Google Sheet | `backend/app/gmail_client.py`, `backend/app/main.py` |
