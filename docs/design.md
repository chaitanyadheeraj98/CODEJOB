# CODEJOB UI and Interaction Design (Current Branch)

## Current UI Shape

- Single-page shell with left sidebar and content panes.
- Main workflow surface remains concentrated in `dashboard/src/App.tsx`.
- Active pages: `run_queue`, `needs_review`, `failed_mapping`, `premium_numbers`, `sent_items`, `recent_runs`.

## Interaction Contracts

| Area | Current behavior | Evidence |
| --- | --- | --- |
| Run queue controls | status cards, query/date controls, policy toggles, run actions | `dashboard/src/App.tsx` |
| Needs review actions | approve/send, reject, send-to-failed, draft/routing fixes | `dashboard/src/App.tsx` |
| Failed mapping repair | edit To/CC then move back to review queue | `dashboard/src/App.tsx` |
| Premium numbers operations | classify unknown numbers, swap recruiter/employer, manage opportunity status/notes, generate script | `dashboard/src/App.tsx` |
| Recent runs summary | displays run details and automation counters | `dashboard/src/App.tsx` |

## Placeholder and UX Debt

- Sidebar footer actions `Settings` and `Help Center` and top action `New Campaign` are presentational.
- UI state/effect orchestration remains monolithic; module extraction is partial.

Mermaid not needed: this is a static UI capability alignment update.

- Audit date: 2026-08-05
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: dashboard tests passed; lint was not run because `markdownlint-cli` is not installed, and live browser/API integration was not exercised.
