# Problem Fix Log (Current Branch Review)

Date: 2026-05-16  
Branch: `copilot/update-docs-files-except-agent-context`

## 1) Current tangled/high-risk zones

### A) `backend/app/main.py` orchestration concentration

**Why tangled**
- API endpoints, policy parsing, run orchestration glue, analytics events, and Telegram handlers are co-located.

**Break risk**
- Helper changes can affect multiple flows at once (`run`, `approve`, `reject`, Telegram actions).

### B) Routing decision + sendability coupling

**Why tangled**
- Queue-time routing interpretation and approve-send gate depend on shared decision semantics.

**Break risk**
- False-safe or false-block outcomes in outbound send logic.

### C) Premium-number write chain

**Why tangled**
- Extraction, review queue transitions, bucket writes, and opportunity writes are interdependent.

**Break risk**
- Duplicate records or broken traceability if idempotency assumptions drift.

### D) Gmail labeling integration path

**Why tangled**
- Rule-based decision, AI fallback, label syncing, and apply-idempotency run across service + orchestration paths.

**Break risk**
- Incorrect labeling, redundant apply calls, or unlabeled processed messages.

### E) Frontend `App.tsx` monolith + refresh sequencing

**Why tangled**
- Section rendering, API calls, and mutation refresh effects remain tightly coupled.

**Break risk**
- Stale counters/cards and race conditions after queue mutations.

## 2) Do-not-touch-without-regression list

1. Routing sendability helpers and approve-send gate path in `backend/app/main.py`
2. Run-orchestrator state transitions in `backend/app/automation/run_orchestrator.py`
3. Number-review manual classification endpoints
4. Dedupe uniqueness indexes in `backend/app/db.py`
5. Recruiter/employer bucket swap endpoints
6. Recruiter opportunity update + cold-call script generation path
7. Dashboard manual recovery controls (failed mapping + number classification)

## 3) Test alignment notes

- `backend/tests/test_phone_attribution.py` still imports `app.phone_attribution` (module not present in this branch).
- Prefer using current-domain tests (`gmail_labeling`, `cold_call`, `query_bucket`, `premium_numbers`, `run_orchestrator`) for branch-accurate confidence.
