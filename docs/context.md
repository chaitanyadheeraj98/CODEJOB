# CODEJOB Context

## 1. Project Overview

CODEJOB automates recruiter-email operations:
- ingest unread recruiter emails from Gmail
- parse/filter/score/routing per message
- queue for manual approval before sending
- capture phone intelligence and opportunity history
- apply Gmail labels for downstream inbox organization

## 2. Purpose

The project reduces repetitive inbox triage while preserving explicit safety controls and traceability.

Primary outcomes:
- faster opportunity triage
- controlled outbound responses
- cleaner recruiter/employer identity buckets
- historical activity visibility through analytics and recent runs

## 3. Target User

Primary user: a job seeker or recruiting-ops assistant managing inbound recruiter traffic.

Core goals:
- run inbox automation safely
- resolve ambiguous routing quickly
- classify contact numbers reliably
- maintain opportunity follow-up context

## 4. Key Features

- Gmail OAuth bootstrap + status polling
- Run-once orchestrator with policy controls
- Needs Review queue with approve/reject safety gate
- Failed Mapping recovery (`Save Mapping & Move to Review`)
- Premium number extraction + manual number review queue
- Recruiter/Employer bucket management with swap actions
- Recruiter opportunity tracking + cold-call script generation
- Gmail labeling preview and runtime label application
- Productivity analytics + recent run timeline
- Telegram command operations

## 5. Core Workflow

1. Configure settings/policy/query bucket.
2. Trigger OAuth or run automation.
3. Process candidate emails through parse/filter/score/routing.
4. Write candidate state (`needs_review`, `failed`, `processed_skipped`).
5. Execute premium-number intelligence and dedupe-aware bucket writes.
6. Apply Gmail label decision.
7. Resolve failed mappings or unknown numbers as needed.
8. Approve-send only after safety checks pass.

## 6. Non-negotiable Business Rules

- No send without manual approval path.
- Approve-send must enforce routing safety + To + CC + draft + resume presence.
- Recruiter and Employer number buckets remain separate entities.
- Unknown numbers must remain manually classifiable.
- Dedupe/index invariants must hold for recruiter/employer/opportunity/review rows.
- Opportunity rows must remain traceable to source email/message.

## 7. Required UI Controls

Sections expected in the dashboard:
- Run Queue
- Needs Review
- Failed Mapping
- Premium Numbers
- Sent Items
- Recent Runs

Controls/actions that must remain available:
- Connect Gmail / Sync trigger
- Save Filters
- Approve & Send
- Reject
- Save Mapping & Move to Review
- Mark as Recruiter
- Mark as Employer
- Swap-to-recruiter / swap-to-employer actions in number buckets

## 8. Important Files

- `backend/app/main.py`
- `backend/app/automation/run_orchestrator.py`
- `backend/app/phase0.py`
- `backend/app/premium_numbers/*`
- `backend/app/gmail_labeling/*`
- `backend/app/cold_call/*`
- `backend/app/models.py`
- `backend/app/db.py`
- `dashboard/src/App.tsx`

## 9. Current Limitations

- Backend and frontend still have monolithic concentration points (`main.py`, `App.tsx`).
- Some tests are stale relative to current code contracts (for example `test_phone_attribution.py`).
- Multi-tenant/auth boundaries are still minimal at UI level.
