# Problem Fix Log (Current Branch Review)

Date: 2026-05-17  
Branch: `copilot/audit-and-sync-markdown-docs`

## 1. Audit outcome summary

This documentation audit did not change runtime code, but it refreshed the docs set to match the current branch's real implementation boundaries.

The largest corrections made in docs were:

- separating import-only Gmail sync from the full queue-building run pipeline,
- correcting the role of `SyncRun` versus `ProductivityEvent`,
- documenting the full candidate state set (`auto_rejected`, `processed_skipped`, `needs_review`, `failed`, `approved_sent`, `rejected`),
- documenting Telegram `/sync`, `/run`, and `/recent_runs` behavior more precisely,
- clarifying that `feature_auto_send`, `feature_retry_queue`, and Redis/RQ infrastructure are present in config but not implemented as end-to-end runtime systems.

## 2. Current tangled zones that remain true

### A. `backend/app/main.py` remains the main composition hotspot

Why it is still risky:

- route registration, status shaping, settings normalization, runtime wiring, and shared helpers still converge there,
- multiple flows depend on the same composition and helper contracts.

Risk if modified casually:

- queue logic, AI status reporting, Gmail labeling, Telegram integration, and analytics shaping can all drift together.

### B. Orchestration still splits across sync, run-once, and approval paths

The system has three related but distinct flows:

- import-only Gmail sync,
- full run-once queue building,
- manual approval and send.

Any documentation or code drift between them creates misleading operator expectations.

### C. Phone intelligence is still a multi-write workflow

The same email can touch:

- `PremiumNumberLead`,
- `NumberReviewQueue`,
- `RecruiterNumber`,
- `EmployerNumber`,
- `RecruiterOpportunity`.

That keeps dedupe and traceability critical.

### D. Frontend state remains centralized

`dashboard/src/App.tsx` still owns the majority of:

- API loading,
- mutation success handling,
- polling,
- queue refresh coordination,
- premium-number views,
- analytics rendering,
- settings persistence.

## 3. Unresolved inconsistencies discovered during the audit

These were documented, not fixed in code:

1. `feature_auto_send` exists in config/settings but does not produce automatic sends.
2. `feature_retry_queue` exists in config/settings but does not drive a dedicated retry loop.
3. Redis/RQ are configured in dependencies and `docker-compose.yml` but are not used by active runtime code.
4. Policy profile definitions are duplicated between backend and frontend.
5. Sidebar footer actions and `New Campaign` remain placeholders.
6. Some tests are stale relative to current orchestration/runtime contracts.
7. Validation commands are present, but the current shell environment lacks required Python/Node packages, so command runs stop before meaningful suite execution.

## 4. Documentation-grounded guidance for future fixes

If future work targets architecture cleanup, the highest-value extractions remain:

1. move routing/sendability decisions behind a single canonical service boundary,
2. isolate import-sync vs run-once behavior behind clearer service contracts,
3. isolate premium-number write logic into a more explicit transaction-aware service,
4. split `App.tsx` into workflow-specific feature containers,
5. centralize policy profile definitions,
6. reconcile stale tests before relying on full-suite confidence.

## 5. Merge-readiness note for this docs PR

After this audit, the `docs/` directory tracks the current branch more accurately, but the branch still contains known runtime debt. Reviewers should treat the docs as the source-of-truth description of current implementation, not as evidence that the underlying debt has been removed.
