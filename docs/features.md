# CODEJOB Features

## 1. Feature Inventory

| Feature | Purpose | User Interaction | Expected Outcome | Dependencies / Limits |
|---|---|---|---|---|
| Gmail OAuth bootstrap | Connect Gmail account to app | Click **Connect Gmail** in dashboard or call `/gmail/oauth/start` | Gmail auth status becomes authenticated | Requires Google OAuth env config and browser sign-in |
| Gmail status check | Show auth/config health | Dashboard status cards and `/gmail/status` | User sees configured/authenticated/token status | Token file must exist and be valid |
| Sync (import-only) | Pull recruiter emails and store candidates | Telegram `/sync` or `/gmail/sync` | New recruiter emails stored with parsed metadata | Skips duplicates and non-recruiter-like messages |
| Sync + Queue run | End-to-end processing and queue preparation | **Sync + Queue / Sync Now** button or `/automation/run-once` | Emails become queued, skipped, or failed mapping | Requires active resume and Gmail auth |
| Dynamic policy profiles | Tune query/run/qualification behavior | Choose Aggressive/Balanced/Strict + Apply Profile | Policy JSON in settings updates; affects future runs | Dynamic policy UI toggle controls advanced fields |
| Manual qualification controls | Adjust threshold and skills requirements | Edit filters in settings panel + Save | Hard/AI filtering behavior changes | Values normalized and bounded server-side |
| Date filtering | Restrict run and queue views by date | Date picker chip and `mail_date` setting | Query/date window narrowing for Gmail and candidate list | Date must be valid YYYY-MM-DD |
| Resume upload/versioning | Store active resume for reply attachment | Upload/Replace resume in UI | New resume version marked current | Empty files rejected |
| AI draft generation (optional) | Generate stronger recruiter reply drafts | Enable AI toggle and run queue | Draft source is DeepSeek or fallback | Requires DeepSeek key + resume context |
| Rules fallback draft | Guaranteed draft when AI disabled/fails | Automatic in queue generation | Draft still produced via template/rules | Uses static/default signature and template tokens |
| Needs Review queue | Human approval before sending | Open **Needs Review** tab | Candidate cards with editable draft and routing panel | Approve disabled until routing safe + resume + draft |
| Approve & Send | Send Gmail reply with attachment | Click **Approve & Send** on candidate | Candidate marked `approved_sent`, Gmail message sent | Requires safe routing, To/CC, thread id, active resume |
| Reject single candidate | Mark candidate rejected | Click **Reject** | Candidate state updated to rejected | Only works from `needs_review` state |
| Bulk reject endpoint | Reject multiple candidates at once | API `/candidates/reject-bulk` | Multiple records moved to rejected state | Requires IDs list |
| Failed Mapping workflow | Recover unresolved recipient routing | Open **Failed Mapping**, fill To/CC, save mapping | Candidate moved back to `needs_review` with regenerated draft | Requires manual To and CC values |
| Routing evidence display | Explain why routing was selected | View routing panel and candidate evidence chips | User can trust/verify routing confidence | Unsafe routing blocks approval |
| Learned routing feedback | Improve future routing using corrections | Save corrected mapping | New sender-domain correction record created | Sender domain must be resolvable |
| Recent Runs log | Show run summaries and status details | Open **Recent Runs** | User sees status/detail/counts/effective query | In-memory frontend list plus backend response fields |
| Sent Items view | Review successful sends | Open **Sent Items** | User sees sent candidates and send timestamps | Depends on approved send history |
| Telegram bot status/control | Operate automation remotely | Telegram commands (`/start`, `/status`, `/run`, `/approve`, etc.) | Read-only and action commands execute with auth | Requires token + allowed chat IDs; optional PIN |
| Auto-run polling | Run automation periodically | Enable auto-run and set interval | Background thread executes run loop | Controlled by `feature_auto_polling` + interval |
| Google Sheets tracking | Append approved-send metadata to sheet | Automatic after successful approve-send | Tracking row appended to configured spreadsheet tab | Requires Sheets enabled + spreadsheet id |

## 2. Main User Workflows

### A. Initial setup
1. Configure backend env values.
2. Open dashboard.
3. Connect Gmail.
4. Upload active resume.
5. Save settings/policy.

### B. Daily processing
1. Click **Sync + Queue**.
2. Review **Needs Review** candidates.
3. Edit draft if required.
4. Approve send or reject.
5. Resolve any **Failed Mapping** items.

### C. Monitoring
- Use status cards (Gmail/AI/Telegram).
- Review **Recent Runs** for run outcomes.
- Review **Sent Items** for sent history.

## 3. User-Facing Screens and Actions

- **Sidebar navigation:** Run Queue, Needs Review, Failed Mapping, Sent Items, Recent Runs.
- **Top action area:** search/query box, batch queue button (UI only), Sync/Connect button, date filter icon/chip.
- **Configuration cards:** Gmail Access, AI Access, Automation Filters, Dynamic Policy, Profile Settings, Execution Control.
- **Operational cards:** candidate cards with routing evidence, editable draft + live preview, action buttons.

## 4. Known Feature Constraints

- Manual review remains mandatory before sending (run-once does not auto-send).
- Gmail connection and resume upload are hard prerequisites for effective queue-to-send flow.
- Some ESLint/TypeScript issues and failing backend tests currently exist in baseline (pre-existing).
- Telegram actions can be blocked when PIN auth/session is required.
