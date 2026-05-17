# CODEJOB Context

## 1. What the project is today

CODEJOB is an inbox triage and controlled-response system for recruiter emails. The current branch processes unread Gmail messages, evaluates opportunity fit, resolves recipients, drafts replies, tracks phone intelligence, and requires a human approval step before sending.

This branch is not a generic CRM. It is a single-owner workflow tool with owner-scoped settings and records.

## 2. Active product outcomes

The current implementation is optimized for:

- reducing manual recruiter-email triage,
- keeping outbound replies reviewable and auditable,
- preserving source traceability from every opportunity or phone record back to a Gmail message,
- separating recruiter and employer phone identities,
- exposing operational status through the dashboard and Telegram.

## 3. Core runtime workflows

### Inbox automation

1. Operator connects Gmail or reuses existing auth.
2. Operator triggers `Sync + Queue` or Telegram `/run`, or enables background auto polling.
3. Backend resolves effective query, date mode, policy, and threshold.
4. Each Gmail candidate is parsed, filtered, scored, routed, and drafted.
5. Candidate is stored as `needs_review`, `failed`, or `processed_skipped`.
6. Side effects run: premium-number extraction, recruiter/employer intelligence, Gmail labeling, analytics.

### Review and send

1. Operator opens a `needs_review` candidate.
2. Operator inspects routing evidence, edits draft if needed, and confirms recipient mapping when required.
3. `Approve & Send` validates Gmail metadata, `To`, `CC`, routing safety, non-empty draft, and active resume.
4. Backend sends the threaded Gmail reply and records approval telemetry.
5. Google Sheets append is attempted as a non-blocking export step.

### Manual recovery workflows

- `failed` candidates can be repaired through `resolve-recipients` and moved back to `needs_review`.
- Unknown phone numbers can be manually classified to recruiter or employer buckets.
- Recruiter/employer records can be swapped when the earlier classification was wrong.
- Recruiter opportunities support notes, status updates, and cold call script generation.

## 4. Current feature inventory

| Feature area | Current branch state |
|---|---|
| Gmail OAuth + sync | Live |
| Manual review / approval queue | Live |
| Failed mapping recovery | Live |
| AI draft generation with fallback | Live |
| Semantic score blending | Live, optional via feature flag |
| Query bucket saved searches | Live |
| Gmail labeling rules + AI fallback | Live |
| Premium number extraction | Live |
| Recruiter/employer bucket management | Live |
| Recruiter opportunity tracking | Live |
| Cold call script generation | Live |
| Telegram remote operations | Live |
| Background auto polling | Live |
| Automatic send without approval | **Not implemented** despite persisted flag |
| Dedicated retry queue processor | **Not implemented** despite persisted flag |

## 5. Dashboard behavior

The dashboard is a single-page React app with these primary sections:

- Run Queue
- Needs Review
- Failed Mapping
- Premium Numbers
- Sent Items
- Recent Runs

Supporting behavior that is part of the current implementation:

- status cards for Gmail, AI, and Telegram connection state,
- settings form for thresholds, signatures, saved queries, policy controls, employer domains, feature flags, and resume upload,
- query bucket inline suggestions with keyboard navigation,
- premium numbers subviews for all leads, review queue, recruiter numbers, employer numbers, and recruiter opportunities,
- recent runs and analytics trend views.

## 6. Backend operational model

The backend runs several parallel concerns inside one process:

- FastAPI request handling,
- a background auto-runner thread for periodic automation,
- a Telegram polling thread when configured,
- AI/embedding status tracking in process memory,
- Gmail labeling label-cache state in process memory.

This means process restarts reset in-memory telemetry such as Telegram auth sessions and AI runtime timestamps.

## 7. Important business rules that are enforced in code

- Manual approval is mandatory for sending replies.
- Send safety requires resolved routing plus `To`, `CC`, resume, and non-empty draft.
- Recruiter and employer buckets must remain separate.
- Unknown phone numbers become review-queue cards when they cannot be matched.
- Duplicate writes are blocked by both application logic and DB indexes.
- Low-confidence routing stays reviewable and explainable through evidence payloads.
- Auto polling can trigger ingestion, but it still produces review queues instead of auto-sending.

## 8. Current branch limitations and TODO-level realities

These are real limitations observed in the codebase, not future guesses:

- `dashboard/src/App.tsx` is still monolithic and tightly coupled.
- `backend/app/main.py` still mixes API, orchestration, policy, Telegram, and helper logic.
- Policy profile definitions exist in both frontend and backend.
- `feature_auto_send` and `feature_retry_queue` are persisted settings without corresponding end-to-end automation behavior.
- Sidebar footer actions and `New Campaign` do not wire to a separate route or feature flow.
- Validation tooling is present in the repo, but this shell session did not have Python/Node dependencies installed, so commands could not run to completion.

## 9. Developer guidance for this branch

If you are changing behavior on this branch, verify your assumptions against code in:

- `backend/app/main.py`
- `backend/app/automation/run_orchestrator.py`
- `backend/app/routing/policy.py`
- `backend/app/premium_numbers/*`
- `dashboard/src/App.tsx`

The highest-risk areas remain routing safety, approval gating, duplicate prevention, and phone classification write paths.
