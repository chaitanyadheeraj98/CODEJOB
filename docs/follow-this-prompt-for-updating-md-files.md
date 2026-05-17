# Follow This Prompt For Updating Docs Markdown Files

Use this prompt whenever you update any file in `docs/` **except** `docs/agent-context.md`.

---

## Prompt To Follow

You are auditing CODEJOB documentation against the **current checked-out branch codebase**.

### Mandatory Audit Order

1. Inspect runtime/backend/frontend code first.
2. Inspect tests second.
3. Inspect existing docs third.
4. Update docs only after code/test grounding.
5. Never derive truth from old documentation before checking code evidence.

### Scope

- Update only files inside `docs/` (exclude `docs/agent-context.md`).
- Do not claim anything as fixed/closed unless code evidence supports it in this branch.
- If tests are not executable, explicitly say so and downgrade certainty.

### Required Inputs Before Editing

1. Current branch name and commit SHA.
2. `git status --short` snapshot.
3. Target doc file list in scope.
4. Code evidence from source files (paths/functions/endpoints) for each changed claim.

### Non-Negotiable Rules

1. **No assumption-based claims**: every major statement must map to code evidence.
2. **Cross-doc consistency required**:
   - `snowball.md`, `problem-fix-log.md`, `architecture.md`, `features.md`, `testcases.md` must not contradict.
3. **Status vocabulary must be strict**:
   - Allowed: `Done`, `Partially Closed`, `Still Open`, `Needs Re-test`, `Unknown`.
   - `Unknown` requires an explicit reason for insufficient runtime/test evidence.
4. **Test truthfulness**:
   - Only call a test "passed" if run evidence exists in this branch/session.
   - If tests could not run (missing tools/deps), record exact failure and mark verification limited.
5. **No silent scope expansion**:
   - Update only what the current ticket/ask requires.

### Evidence Freshness Rules

1. Prefer active runtime paths over comments or TODO notes.
2. Prefer imported/routed/invoked modules over orphaned files.
3. Do not treat commented-out code, archived notes, or stale TODOs as implemented behavior.

### Visual Clarity Rule (Mermaid-First)

1. Mermaid is required when updates explain:
   - flow or lifecycle behavior
   - decision gates or branching outcomes
   - state transitions
   - multi-step backend/frontend interactions
2. At least one Mermaid block must be added or updated in those cases.
3. Approved diagram patterns:
   - `flowchart` for operational/data workflows
   - `stateDiagram` for ticket/status/state transitions
   - `sequenceDiagram` for cross-service/API interactions
4. Diagram placement and readability rules:
   - place diagram near the section it explains (not only at the bottom)
   - keep node labels business-readable
   - ensure diagram logic matches text/tables in the same doc
5. Smart exception (narrow):
   - for tiny factual edits only (typo/date/wording), Mermaid may be skipped
   - editor must add one line: `Mermaid not needed: <reason>`

### Diagram Consistency Rule

1. When workflow text changes, verify nearby Mermaid diagrams still match runtime behavior.
2. Update or remove stale diagrams immediately.
3. Mermaid, prose, and table claims must agree.

### File-by-File Update Contract

#### `docs/snowball.md`

- Keep ticket-level risk register.
- For each ticket include:
  - `Status`
  - `Severity` (`Critical`, `High`, `Medium`, `Low`)
  - `Remaining issue`
  - `Evidence`
  - `Recommended next action`
- If a ticket is marked `Done`, include closure evidence and any non-blocking debt.
- If status is `Unknown`, include explicit uncertainty reason and missing evidence path.

#### `docs/problem-fix-log.md`

- Must reflect the **same ticket statuses** as `snowball.md` unless explicitly noted as a different audit date/context.
- Include verification constraints (for example missing pytest/eslint/vitest) with exact command outcomes.
- For each failed validation command, include blocker class:
  - missing dependency
  - missing environment variable
  - missing external service
  - incompatible local runtime
  - stale test

#### `docs/architecture.md`

- Describe actual current runtime ownership and coupling hotspots.
- If `main.py` or `App.tsx` are still central hubs, state that clearly.
- If design goals differ from implementation reality, explicitly separate:
  - Intended architecture
  - Current runtime behavior

#### `docs/features.md`

- Separate:
  - Live behavior
  - Optional behavior
  - Persisted-but-not-implemented flags
- Avoid feature claims that are only settings/UI placeholders.

#### `docs/testcases.md`

- Record executable validation commands and actual outcomes.
- Track stale tests and dependency/tooling blockers explicitly.

#### `docs/design.md`, `docs/context.md`, `docs/data.md`, `docs/hardcoded.md`

- Keep aligned with architecture and feature truth.
- Ensure data/state names match backend code contracts.

### Change Focus Rule

1. Prioritize docs updates around:
   - files changed in current branch
   - recently modified modules
   - ticket-related flows
2. Avoid unrelated rewrites unless contradiction repair is required.

### Evidence Format

When changing a behavior claim, include at least one of:

- Source path + function/service name
- Endpoint path + handler/service boundary
- Test file + result status

### Contradiction Check (Mandatory Before Save)

Run this mental checklist:

1. Does any file mark a ticket `Done` while another says `Partially Closed` for the same audit context?
2. Do feature docs claim runtime behavior that test/docs files say is unverified?
3. Do architecture/docs claim decoupling that code still centralizes?

If yes, fix inconsistencies before finalizing edits.

### Dead Flow Detection

During audit, explicitly flag these as debt when discovered:
- unused routes
- orphaned services
- stale feature flags
- unreachable UI flows
- tests targeting removed contracts

### Reviewer Attention Rule

Explicitly call out:
- areas requiring human validation
- flows not executable in current audit environment
- assumptions blocked by missing integrations

### Output Style Requirements

- Keep language concrete and audit-friendly.
- Use short sections, tables where helpful, and explicit status lines.
- Avoid vague words: "seems", "probably", "might be fixed".

### Final Footer Template (append to each touched doc)

- `Audit date: YYYY-MM-DD`
- `Branch: <branch-name>`
- `Evidence basis: code inspection | test run | both`
- `Verification limits: <none or explicit limits>`

---

## Primary Objective

The goal is not to make docs look complete.  
The goal is to make docs operationally truthful to the current branch state, with explicit uncertainty where verification is incomplete.

## Quick Mini-Checklist

- [ ] Branch + commit captured
- [ ] Mandatory audit order followed (code -> tests -> docs -> updates)
- [ ] Scope limited to requested docs
- [ ] Claims tied to code/test evidence
- [ ] Evidence freshness validated (active paths only)
- [ ] Cross-doc statuses consistent
- [ ] Runtime vs intended design split applied where relevant
- [ ] Mermaid diagram added/updated where process or state behavior changed
- [ ] Mermaid diagram matches current branch behavior and doc claims
- [ ] Stale Mermaid diagrams updated or removed
- [ ] Test limitations explicitly documented
- [ ] Validation command failures include blocker classification
- [ ] Dead flow/orphaned contract findings captured
- [ ] Reviewer-attention items explicitly listed
- [ ] Branch-diff and ticket-related focus respected
- [ ] No update to `docs/agent-context.md`
