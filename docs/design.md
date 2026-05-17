# CODEJOB UI and Interaction Design (Current Branch)

Audit date: 2026-05-17  
Branch: `copilot/update-markdown-docs-audit`

## 1) Layout and navigation

The dashboard is a single-page interface with left rail navigation and one active content pane.

Navigation sections:
- Run Queue
- Needs Review
- Failed Mapping
- Premium Numbers
- Sent Items
- Recent Runs

Placeholder-only controls still rendered:
- `New Campaign`
- `Settings` (footer)
- `Help Center` (footer)

## 2) Run Queue surface

Run Queue combines controls + status + settings:
- Gmail/AI/Telegram status cards
- OAuth controls
- query/date controls with saved-query suggestions
- policy and execution controls
- resume upload/replace
- productivity live monitor

## 3) Query bucket UX

Implemented behavior:
- inline query input updates active query state
- `+` saves current query
- `-` removes exact current saved query
- keyboard suggestion navigation (`ArrowUp/Down`, `Enter`, `Escape`)
- case-insensitive dedupe and max 10 saved queries

## 4) Needs Review UX

Each card supports:
- sender/subject/context visibility
- routing panel and routing evidence
- editable draft + rendered preview
- verdict badge
- actions: `Approve & Send`, `Reject`, `Send to Failed Mapping`

Approval button remains blocked until frontend checks pass:
- recipient + CC
- non-empty draft
- resume filename
- routing considered trusted

## 5) Failed Mapping UX

Each failed item supports:
- routing evidence review
- editable corrected `To`/`CC`
- `Save Mapping & Move to Review` action

## 6) Premium Numbers UX

Scope selector supports:
- unknown review cards
- recruiter numbers
- employer numbers
- recruiter opportunities

Implemented actions:
- mark unknown number as recruiter/employer
- delete review card
- swap recruiter/employer bucket identity
- recruiter opportunity status updates
- recruiter opportunity notes update
- cold-call script generation + copy

## 7) Sent Items and Recent Runs

- Sent Items: approved/sent history list
- Recent Runs: recent run summaries, counts, effective query details
- Analytics trend/activity is rendered inside Run Queue (not a separate routed page)

## 8) Design debt still active

- `App.tsx` still controls most state and network flows.
- Refresh coordination still depends on `schedulePostMutationRefresh()` timers.
- Placeholder sidebar actions are not feature-routed.
- `features/ai` is not yet a full UI feature module.
