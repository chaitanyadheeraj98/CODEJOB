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
- Strictly follow `docs/Rules.md` while writing or updating any `.md` files in `docs/`.
- Do not claim anything as fixed/closed unless code evidence supports it in this branch.
- If tests are not executable, explicitly say so and downgrade certainty.

### Protected Files Rule (Do Not Modify)

Do not edit these files during docs audit/update execution:

1. `D:\My Websites\CodeJob\docs\agent-context.md`
2. `D:\My Websites\CodeJob\docs\follow-this-prompt-for-updating-md-files.md`
3. `D:\My Websites\CodeJob\docs\Rules.md`
4. `D:\My Websites\CodeJob\docs\taxonomy.md`

### Markdown Lint Gate (Mandatory)

1. Treat markdownlint compliance as a hard completion gate for touched docs files.
2. Before finalizing, run markdownlint on all updated `.md` files and fix violations.
3. Do not mark docs work complete while markdownlint errors remain.
4. If lint cannot be executed in the current environment, explicitly report:
   - exact command attempted
   - exact failure output
   - impacted files
   - blocker class
5. Enforced-rule extraction is required:
   - identify active markdownlint rules affecting touched content (for example `MD060` table style)
   - apply those rules consistently before final output.

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

### Mermaid Feature Diagram Update Contract

Use this contract whenever the target file is `docs/mermaid-features.md`
or whenever a change affects feature workflow diagrams.

Primary objective:

- regenerate or update Mermaid diagrams for every live feature in the current
  application
- show how each feature works from frontend action to backend endpoint,
  service/domain logic, persistence side effects, external integrations, and
  UI result
- keep diagrams evidence-backed and remove stale diagrams immediately

Mandatory workflow before editing `docs/mermaid-features.md`:

1. Inspect frontend feature entry points first.
   - Identify user-visible action, component, button, tab, form, or screen.
   - Capture file path and handler/function name.
2. Inspect API calls second.
   - Identify endpoint path, HTTP method, request payload, response fields, and
     frontend caller.
3. Inspect backend route/service flow third.
   - Identify route handler, service boundary, orchestration helper, models
     touched, external API calls, and state transitions.
4. Inspect tests fourth.
   - Record any test that validates the flow.
   - If no test exists, mark verification as code inspection only.
5. Inspect existing `docs/mermaid-features.md` last.
   - Update diagrams that match current runtime behavior.
   - Remove or rewrite diagrams that no longer match code.

Diagram requirements for each feature:

- Include one Mermaid block per major feature.
- Prefer `sequenceDiagram` when explaining frontend-to-backend API interaction.
- Prefer `flowchart TD` when explaining backend decision logic or processing.
- Prefer `stateDiagram-v2` when explaining queue/status transitions.
- Every feature diagram must include, when applicable:
  - frontend screen/component
  - user action
  - API endpoint and method
  - backend route handler or service
  - database model/table touched
  - external integration such as Gmail, AI provider, Telegram, or Sheets
  - final UI state or response shown to the user
- Use business-readable node labels.
- Do not include implementation guesses.
- Do not draw a backend step unless a current source path/function supports it.
- Do not claim a frontend path exists unless a current component/action calls it.
- If a feature is placeholder-only, diagram it as placeholder-only or mark it
  as `No live flow`.

Required evidence table below each diagram:

| Evidence type | Source |
| --- | --- |
| Frontend entry | `<path>:<function/component>` |
| API endpoint | `<METHOD> <path>` |
| Backend logic | `<path>:<function/service>` |
| Data touched | `<model/table/field>` |
| Tests | `<test path + result>` or `No direct test found` |
| Verification limit | `<explicit limit or none>` |

Coverage checklist for current CODEJOB feature diagrams:

- Gmail OAuth and inbox sync
- Run-once automation
- Candidate scoring, routing, and queue state assignment
- Needs Review approval and send gate
- Reject and bulk reject
- Failed Mapping recovery
- Premium number extraction
- Unknown number review classification
- Recruiter and employer number buckets
- Recruiter opportunity cards
- Cold-call script generation
- Gmail labeling
- Productivity analytics
- Query bucket saved searches
- Resume upload and active resume selection
- Settings and execution controls
- Auto polling
- HR-5 auto-send and retry queue behavior
- Telegram operations
- Google Sheets append, if configured as live optional behavior

Required output structure for `docs/mermaid-features.md`:

1. `# CODEJOB Mermaid Feature Flows`
2. Audit metadata:
   - `Audit date`
   - `Branch`
   - `Commit`
   - `Evidence basis`
   - `Verification limits`
3. Feature coverage summary table:
   - Feature
   - Frontend entry
   - Backend endpoint/service
   - Status: `Live`, `Live (optional)`, `Placeholder`, `Unknown`
   - Diagram updated: `Yes` or `No`
4. One section per feature:
   - short runtime summary
   - Mermaid diagram
   - evidence table
   - verification limits
5. Reviewer attention section:
   - flows not executable locally
   - stale or missing tests
   - diagrams needing human validation
6. Final footer using the standard docs footer template.

Status rules for `docs/mermaid-features.md`:

- `Live` means frontend and backend runtime path are both code-backed.
- `Live (optional)` means the path exists but depends on settings,
  credentials, external services, or configured integrations.
- `Placeholder` means UI/docs mention the feature but no complete runtime path
  exists.
- `Unknown` means evidence was insufficient; include the missing path or blocker.

Mermaid correctness checks before save:

1. Does every diagram match the current frontend action and backend endpoint?
2. Does every decision branch match actual route/service conditions?
3. Do all model/state names match current backend contracts?
4. Are optional integrations clearly marked optional?
5. Did you remove old diagrams for deleted or unreachable flows?
6. Does each diagram have a nearby evidence table?
7. Did markdownlint pass for `docs/mermaid-features.md`?

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
- [ ] markdownlint run on all touched docs files
- [ ] markdownlint errors fixed (or execution blocker documented with command/output/class)
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
