# Problem Fix Log (Current Branch Review)

Date: 2026-05-17  
Branch: `copilot/audit-and-update-docs`

## 1. Audit outcome summary

This documentation audit did not change runtime code, but it surfaced the current implementation boundaries that matter most for future work.

The largest corrections made in docs were:

- documenting Gmail labeling as a real runtime subsystem,
- documenting cold call script generation on recruiter opportunities,
- documenting query bucket persistence and UI behavior,
- clarifying that `feature_auto_send` and `feature_retry_queue` are stored flags without fully implemented execution flows,
- updating architecture docs to include startup threads and in-memory Telegram auth sessions,
- updating data docs to include routing evidence, draft quality, resume context attribution, and opportunity statuses.

## 2. Current tangled zones that remain true

### A. `backend/app/main.py` remains the main coupling hotspot

Why it is tangled:

- API routes, startup lifecycle, routing helpers, policy helpers, Telegram handlers, analytics hooks, and side effects all live together.
- Multiple workflows reuse the same helper functions and shared process-global state.

Risk if modified casually:

- queue logic, send gates, Gmail labeling, and Telegram actions can all regress together.

### B. Orchestration and routing still depend on shared contracts

The run pipeline and approve-send path both depend on the same routing concepts but at different times:

- orchestration decides `needs_review` vs `failed`,
- approval re-checks whether the routing is safe enough to send.

Any drift here can create false-safe or false-blocked behavior.

### C. Phone intelligence is still a multi-write workflow

The same email can touch:

- `PremiumNumberLead`,
- `NumberReviewQueue`,
- `RecruiterNumber`,
- `EmployerNumber`,
- `RecruiterOpportunity`.

That makes dedupe and traceability critical.

### D. Frontend state remains centralized

`dashboard/src/App.tsx` still owns the majority of:

- API loading,
- mutation success handling,
- polling,
- queue refresh coordination,
- premium-number views,
- settings persistence.

## 3. Unresolved inconsistencies discovered during the audit

These were documented, not fixed in code:

1. `feature_auto_send` exists in config/settings but does not produce automatic sends.
2. `feature_retry_queue` exists in config/settings but does not drive a dedicated retry loop.
3. Policy profile definitions are duplicated between backend and frontend.
4. Sidebar footer actions and `New Campaign` remain placeholders.
5. Some tests are stale relative to live runtime contracts.
6. Tooling dependencies were missing in this shell session, so repo validation commands could not complete successfully.

## 4. Documentation-grounded guidance for future fixes

If future work targets architecture cleanup, the highest-value extractions remain:

1. move routing/sendability decisions behind a single canonical service boundary,
2. isolate premium-number write logic into a more explicit transaction-aware service,
3. split `App.tsx` into workflow-specific feature containers,
4. centralize policy profile definitions,
5. reconcile stale tests before relying on full-suite confidence.

## 5. Merge-readiness note for this docs PR

After this audit, the `docs/` directory reflects the current branch more accurately than before, but the branch still contains known runtime debt. Reviewers should treat the docs as the source-of-truth description of the current implementation, not as evidence that the underlying debt has been removed.

## 6. HR-1 closeout (snowball-md)

HR-1 was closed on `snowball-md` after targeted D.1 validation passed for orchestration/routing/telegram compatibility, with a compatibility shim commit to preserve legacy `app.main` test hooks; `test_run_orchestrator.py` remains tracked stale test-contract debt.
