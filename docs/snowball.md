# Snowball Risk Register

Date: 2026-05-16  
Branch: `copilot/update-docs-files-except-agent-context`

## High-risk snowball items

### HR-1: Monolithic backend orchestration surface
- Concentrated logic in `backend/app/main.py` creates high cross-flow regression risk.

### HR-2: Routing/sendability decision drift
- Divergence between queue-time and approve-time routing interpretation can cause unsafe sends or overblocking.

### HR-3: Number-intelligence idempotency drift
- Multi-entity writes (lead/review/bucket/opportunity) can duplicate or orphan records if contracts drift.

### HR-4: Runtime schema evolution complexity
- `ensure_sqlite_phase0_columns()` keeps growing and can become fragile across existing DBs.

### HR-5: Labeling path reliability
- If Gmail labeling rules, AI fallback, and apply semantics drift, mailbox organization quality degrades quickly.

### HR-6: Manual safety control erosion
- Removing or weakening failed-mapping and number-classification controls undermines human-in-loop safety.

## Medium-risk items

- Frontend `App.tsx` monolith and refresh orchestration debt
- Frontend/backend policy default duplication
- Hardcoded defaults in config/runtime paths
- Partial observability for decision-level debugging
- Test coverage trust gap from stale tests

## Low-risk items

- Documentation synchronization overhead
- Placeholder UI controls in sidebar footer
- Ongoing heuristic tuning debt (routing/premium relevance/labeling rules)

## Priority order

1. routing/send safety + dedupe invariants
2. labeling and opportunity-write correctness
3. monolith decomposition and refresh stabilization
4. stale-test cleanup and validation hardening
