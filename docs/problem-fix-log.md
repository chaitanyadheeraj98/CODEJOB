# Problem Fix Log (Current Branch Review)

Date: 2026-05-15  
Branch: `copilot/update-docs-except-agent-context`

## 1) Summary of critical tangled zones

This branch introduces strong coupling across run orchestration, routing safety, premium-number classification, and a monolithic frontend state layer.

The following areas are **high-risk to modify without end-to-end verification**.

---

## 2) Tangled backend code zones

### A) `backend/app/main.py` orchestration layer (highly coupled)

**Why tangled**
- Hosts API endpoints, business rules, policy parsing, routing gates, Telegram handlers, analytics updates, and helper utilities in one file.
- Shared helpers are reused by both UI endpoints and Telegram command paths.

**Break risk**
- Small helper changes can silently affect multiple flows (`/run`, `/approve`, `/reject`, UI endpoints).

**Untangle direction**
- Incrementally extract domain services (settings/policy, routing gate, approval/send, phone-intelligence classification) behind tested interfaces.

### B) Run pipeline + routing gate coupling (`RunOrchestrator.execute` + `_evaluate_routing_policy` + `_routing_is_sendable`)

**Why tangled**
- Queue transitions and send safety depend on combined status/confidence/manual-confirm flags.
- Similar routing data is interpreted in multiple places.

**Break risk**
- Incorrect refactor can allow unsafe sends or incorrectly block valid sends.

**Untangle direction**
- Centralize one canonical routing decision contract and use it consistently for queueing + approve-send.

### C) Premium numbers + classification + opportunity creation chain

**Why tangled**
- `extract_and_store_premium_numbers`, `process_email_number_intelligence`, and review-classification endpoints interact with overlapping entities.
- Duplicate prevention depends on both logic checks and DB uniqueness indexes.

**Break risk**
- Duplicate recruiter/employer/opportunity records, or lost manual review cards.

**Untangle direction**
- Move write-path rules into one transaction-aware service with explicit idempotency tests.

### D) SQLite additive migration helper (`ensure_sqlite_phase0_columns`)

**Why tangled**
- Large imperative migration logic creates/patches many tables/indexes at startup.

**Break risk**
- Schema drift or accidental index/column changes can corrupt compatibility with existing DBs.

**Untangle direction**
- Shift to versioned migrations (Alembic) while keeping backward compatibility checks.

---

## 3) Tangled frontend code zones

### A) `dashboard/src/App.tsx` monolith

**Why tangled**
- Contains all page rendering, API calls, business-state transitions, analytics polling, OAuth polling, and premium-number actions.

**Break risk**
- UI state regressions across unrelated sections when modifying shared hooks/effects.

**Untangle direction**
- Gradual extraction into feature modules (run queue, review queue, failed mapping, premium numbers, settings).

### B) Multi-source refresh sequencing (`schedulePostMutationRefresh`, effect chains)

**Why tangled**
- Multiple async refreshes trigger in timed and effect-driven paths.

**Break risk**
- stale UI state, duplicate requests, race conditions after approve/reject/resolve/classify actions.

**Untangle direction**
- Consolidate mutation success handlers and adopt a unified query/cache layer.

---

## 4) Do-not-touch-without-full-regression list

These areas should not be modified casually; they enforce safety and identity invariants.

1. `backend/app/main.py::_routing_is_sendable` and routing policy evaluation path
2. `backend/app/main.py::approve_and_send` validation checks (To/CC/routing/draft/resume requirements)
3. Number review manual actions:
   - `POST /number-review/{review_id}/mark-recruiter`
   - `POST /number-review/{review_id}/mark-employer`
4. DB uniqueness indexes created in `backend/app/db.py` for recruiter/employer/opportunity/review dedupe
5. `RunOrchestrator.execute` state transitions (`needs_review`, `failed`, `processed_skipped`)
6. UI manual classification buttons in Premium Numbers “All” view (`Mark as Recruiter`, `Mark as Employer`)
7. UI failed-mapping correction action (`Save Mapping & Move to Review`)

If any of these are changed, run full backend + frontend regression and manually validate end-to-end behavior.

---

## 5) Additional branch inconsistencies to track

- `backend/tests/test_phone_attribution.py` references a missing module (`app.phone_attribution`), indicating stale test coverage.
- Some tests appear to target older orchestrator dependency names and may not match current implementation.

Recommendation: align tests with current service contracts before relying on suite-level confidence.
