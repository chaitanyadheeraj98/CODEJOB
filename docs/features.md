# CODEJOB Features

## Feature summary

CODEJOB is a recruiter-email operations system that automates inbox intake, qualification, queueing, and assisted response drafting, while keeping manual approval before sends.

## Complete feature list

| Feature | Purpose | User interaction | Expected outcome | Key dependencies/limitations |
|---|---|---|---|---|
| Gmail connection status | Check OAuth readiness | View Gmail card in dashboard | Shows configured/authenticated state and last sync | Requires Google OAuth config/token |
| Gmail OAuth bootstrap | Start auth flow | Click **Connect Gmail** | OAuth process starts and status is logged | Backend logs must be accessible to complete sign-in |
| Sync + Queue run | Import and evaluate unread emails | Click **Sync + Queue** / **Sync Now** | Emails are categorized into queue/failed/skipped with run summary | Requires authenticated Gmail and active resume |
| Effective query/date filters | Control what inbox messages are processed | Search/query fields + date picker + settings | Runs only evaluate matching inbox messages | Query quality directly impacts matching |
| Settings management | Persist pipeline behavior | Edit and **Save Filters** | Backend settings updated and reused by run + Telegram commands | Invalid/incomplete values can reduce qualification quality |
| Dynamic policy profile | Choose Aggressive/Balanced/Strict behavior | Select profile + apply; optional beta switches | Batch/date/filter strictness changes for runs | Some advanced policy controls are behind toggle |
| AI toggle and AI status | Enable AI draft generation and monitor health | Toggle **Enable AI Features**, view AI card | Drafts come from DeepSeek when available, fallback otherwise | Needs API key/config; may fall back on errors |
| Resume upload/versioning | Attach resume context to outbound replies | Upload/replace file in settings | Latest resume stored and marked active | Approval/send requires active resume |
| Needs Review queue | Manual review before send | Open **Needs Review** section | Review candidates with editable draft + preview | Approval blocked until routing is safe and required fields exist |
| Draft editor + live preview | Improve final email quality | Edit textarea and inspect preview panel | Final sent content matches reviewed draft | Preview is formatting approximation |
| Approve & Send | Send qualified response | Click **Approve & Send** | Reply sent via Gmail with attachment; state moves to approved_sent | Requires safe routing, To/CC, draft body, active resume |
| Reject (single) | Remove unsuitable candidate from active queue | Click **Reject** | Candidate marked rejected | Only allowed from needs_review state |
| Failed Mapping queue | Fix recipient routing failures | Open **Failed Mapping**, edit To/CC, save mapping | Candidate is re-queued for manual review with updated routing | Requires valid manual mapping inputs |
| Routing evidence panel | Explain recipient resolution confidence | Inspect routing panel/evidence cards | User sees why send is blocked/allowed | Confidence/rules depend on parsed body evidence |
| Sent Items history | Track successful sends | Open **Sent Items** | View subject/sender/sent timestamp and Gmail link | Depends on successful send metadata |
| Recent Runs log | Review run outcomes | Open **Recent Runs** | Status/detail/counters and reasons visible | In-memory UI list depth is capped |
| Productivity analytics | Observe operational trends | Open run_queue monitor and select range | Bar chart + trend delta + activity history | Accuracy depends on event emission and retained data |
| Telegram bot operations | Remote monitor/control actions | Use commands (`/status`, `/run`, `/approve`, etc.) | Read/write operational control from Telegram | Requires token + allowed chat IDs + optional PIN auth |
| Google Sheets tracking (optional) | External logging of approved sends | Enabled via settings/env | Appends structured row after approval/send | Requires Sheets config/credentials |
| Auto-run polling | Scheduled periodic run-once execution | Enable auto-run + interval | Backend runs automatically at interval | Uses thread loop; still depends on OAuth/resume/config |
| Dry-run mode | Simulate processing without side effects | Toggle Dry Run in policy | Returns run insights without DB/Gmail mutation | Intended for validation/testing workflows |

## Screen and workflow highlights

- **Sidebar**: Run Queue, Needs Review, Failed Mapping, Sent Items, Recent Runs.
- **Top bar**: Query field, action buttons, date filter.
- **Run Queue page**: Config cards + live monitor + settings form.
- **Needs Review page**: Candidate review, routing inspection, draft edit/preview, approve/reject.
- **Failed Mapping page**: Full-email context and correction form.
- **Recent Runs page**: Execution outcome log.
- **Sent Items page**: Delivered history.

## Notable user-facing limitations

- No auto-send in run-once path; manual approval is intentional.
- OAuth completion requires access to backend log output.
- AI generation can fail or timeout and fallback to rules-only draft.
- Routing confidence may block approval until user correction.
- Local/SQLite default setup is single-node and not multi-tenant hardened.
