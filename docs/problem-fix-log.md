<!-- markdownlint-configure-file {"MD013": false} -->

# Problem Fix Log (Current Branch Verification)

## Ticket Status Summary

| Ticket | Status |
| --- | --- |
| HR-1 | Partially Closed |
| HR-2 | Still Open |
| HR-3 | Still Open |
| HR-4 | Unknown |
| HR-5 | Partially Closed |
| MR-1 | Still Open |
| MR-2 | Still Open |
| MR-3 | Still Open |
| MR-4 | Still Open |
| MR-5 | Needs Re-test |
| LR-1 | Partially Closed |
| LR-2 | Still Open |
| LR-3 | Still Open |

## Evidence Notes

### HR-1

- Status: Partially Closed
- Remaining issue: `backend/app/main.py` remains central orchestration hub.
- Evidence: service extraction exists, but route wiring and runtime composition are still centralized in `main.py`.
- Recommended next action: continue endpoint-by-endpoint extraction from `main.py` into narrower service modules.

### HR-3

- Status: Still Open
- Remaining issue: premium-number path remains dense and multi-branch.
- Evidence: extraction, review, bucket swaps, and opportunity operations span `app/premium_numbers/*` and multiple backend routes.
- Recommended next action: add focused service-level transaction boundary tests around review-to-opportunity transitions.

### HR-4

- Status: Unknown
- Remaining issue: migration/runtime ownership was not re-verified by migration command execution in this session.
- Evidence: no `alembic` command output captured in this session.
- Recommended next action: run migration status commands before claiming closure.

### HR-5

- Status: Partially Closed
- Remaining issue: feature toggles are runtime-active, but full branch verification remains incomplete.
- Evidence: flag persistence and application in `/settings` and runtime orchestration pathways; no full backend suite pass in this session.
- Recommended next action: resolve stale backend suite blockers and re-run full validation.

## Validation Commands in This Session

- `cd backend; python -m pytest tests/test_premium_numbers_extraction.py`
  - Result: passed (`17 passed in 9.40s`)
  - Blocker class: none

- `cd backend; python -m pytest tests/test_premium_numbers_extraction.py` (initial attempt)
  - Result: timeout
  - Exact failure: command timed out before completion
  - Blocker class: incompatible local runtime timeout setting (rerun succeeded)

- `cd backend; python -m pytest`
  - Result: not executed in this session
  - Blocker class: unknown in this session

## Branch Conclusion

Targeted premium-number extraction verification is green on this branch snapshot, but full-suite confidence is still limited and should not be presented as complete regression closure.

- Audit date: 2026-05-30
- Branch: semantic-embeddings
- Commit: 5991f97
- Evidence basis: both
- Verification limits: only targeted extraction tests were executed in this session.
