# CODEJOB Test and Validation Matrix

## Standard validation commands

### Backend (from `backend/`)

- `python -m pytest`

### Dashboard (from `dashboard/`)

- `npm run lint`
- `npm run build`
- `npm run test`

## Command results in this audit session

- `cd backend && python -m pytest`
  - **Result:** failed
  - **Exact output:** `/usr/bin/python: No module named pytest`
  - **Blocker class:** missing dependency

- `cd dashboard && npm run lint`
  - **Result:** failed
  - **Exact output:** `sh: 1: eslint: not found`
  - **Blocker class:** missing dependency

- `cd dashboard && npm run test`
  - **Result:** failed
  - **Exact output:** `sh: 1: vitest: not found`
  - **Blocker class:** missing dependency

- `cd dashboard && npm run build`
  - **Result:** failed
  - **Exact output:** `TS2688: Cannot find type definition file for 'vite/client'` and `TS2688: Cannot find type definition file for 'node'`
  - **Blocker class:** missing dependency

- `npx --yes markdownlint-cli -- docs/snowball.md docs/problem-fix-log.md docs/testcases.md docs/architecture.md docs/features.md`
  - **Result:** failed to execute linting pass
  - **Exact output:** CLI returned usage text only (`Usage: markdownlint [options] [files|directories|globs...]`)
  - **Blocker class:** incompatible local runtime/tooling invocation

## Stale/dead-flow findings from test inspection

- `backend/tests/test_phone_attribution.py` imports `app.phone_attribution`, but that module is not present in runtime code.
  - **Classification:** tests targeting removed or non-existent contract
- `backend/tests/test_run_orchestrator.py` still uses an outdated dependency signature (`analyze_email_routing` / `apply_routing_result`) compared with current `RunOrchestratorDependencies` (`evaluate_routing_policy` / `apply_routing_decision`).
  - **Classification:** stale test contract

## Reviewer attention

- No backend or dashboard quality gate can currently be marked green in this environment.
- Re-run full validation after installing backend/frontend dependencies.

- Audit date: 2026-05-18
- Branch: `copilot/update-docs-md-files`
- Evidence basis: both
- Verification limits: command execution was limited by missing local toolchain/dependency setup.
