# CODEJOB UI/UX Design Notes (Current Branch)

## 1) UI structure

The dashboard remains a single-page React app (`dashboard/src/App.tsx`) with a sidebar and section panes.

Sidebar sections:
- Run Queue
- Needs Review
- Failed Mapping
- Premium Numbers
- Sent Items
- Recent Runs

## 2) Interaction contracts

Top controls must preserve:
- Gmail connect/auth entry point
- Sync run trigger
- Save Filters/settings behavior
- Resume upload/replace flow

## 3) Needs Review safety UX

Each review card should preserve:
- routing evidence/candidate context
- visible To/CC recipients
- editable draft and live preview
- `Approve & Send` and `Reject`

Approve must remain disabled until send gate prerequisites are met.

## 4) Failed Mapping UX

Failed Mapping cards must preserve:
- source context visibility
- editable recipient correction fields
- `Save Mapping & Move to Review` recovery action

## 5) Premium Numbers UX

Manual controls that must remain:
- `Mark as Recruiter`
- `Mark as Employer`

Additional operations currently available:
- Recruiter/Employer bucket listing
- Swap bucket actions
- Recruiter opportunity status/notes updates
- Generate cold-call script action

## 6) Known design debt

- `App.tsx` is still monolithic and state-coupled.
- Post-mutation refresh sequencing is spread across multiple paths.
- Sidebar footer buttons (`Settings`, `Help Center`) are placeholder-only.
