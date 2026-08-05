# Target Architecture (v1): Email Automation System

## Architecture Diagram

SVG could not be converted in this environment.

## Architecture Components

1. **Email Ingestion Service**: Monitors inboxes, retrieves unread recruiter emails, and stores message metadata.
2. **Parser and Normalizer**: Extracts structured fields such as role, location, salary, and required skills.
3. **Qualification Engine**: Applies rule-based scoring with optional LLM-assisted scoring.
4. **Reply Orchestrator**: Drafts and sends responses with configurable approval gates.
5. **Audit and Dashboard**: Logs all decisions and actions, and provides a review interface.

## Recommended Rollout

1. **Phase 0: Manual Assist**: Label and draft only; all sends require human approval.
2. **Phase 1: Semi-Automation**: Auto-send high-confidence replies; route the rest for manual review.
3. **Phase 2: Full Automation**: Enable guardrailed automation with ongoing QA sampling.

## Key Considerations

1. **Safety**: Block low-confidence auto-sends, prevent duplicate responses, and enforce rate limits.
2. **Legal and Compliance**: Follow Gmail/Outlook platform policies, minimize stored data, and maintain compliance controls.
3. **Quality**: Keep rules transparent, use LLMs as assistants, and version decision logic.
4. **Reliability**: Ensure idempotency, retries, and monitoring/alerting.

## Proposed Tech Stack

1. **Backend**: Node.js or Python (FastAPI).
2. **Email Integration**: Gmail API or Microsoft Graph.
3. **Database**: SQLite (local file-based storage).
4. **Queue**: Redis with BullMQ or Celery.
5. **LLM Layer**: OpenAI API.
6. **UI**: React-based dashboard.

## Qualification Model

1. **Hard Filters**: Location, visa status, salary expectations.
2. **Skill Match Score**: Match required vs. candidate skills.
3. **Seniority Fit**: Align role level with candidate profile.
4. **Confidence Thresholds**:
   - `>= 85`: Qualified
   - `60-84`: Maybe
   - `< 60`: Reject

## Next Steps

1. Define qualification criteria in detail.
2. Choose the inbox provider and API integration path.
3. Build an MVP for read, classify, and draft.
4. Validate on historical recruiter email data.
5. Enable controlled auto-send with monitoring.
