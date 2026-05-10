# CODEJOB Test Cases and Edge Cases

## 1) Functional test cases

| Test case name | Feature/module | Objective | Steps | Expected result | Edge cases to verify | Priority | Automation |
|---|---|---|---|---|---|---|---|
| Gmail OAuth bootstrap start | Backend `/gmail/oauth/start`, UI connect button | Ensure OAuth bootstrap status works | 1) Open dashboard 2) Click Connect Gmail | Response shows `ready` or `oauth_in_progress` with detail | Missing config should return clear error | High | Automatable (API) + Manual UI |
| Load settings and status | `/settings`, `/gmail/status`, `/ai/status`, `/telegram/status` | Validate startup data loading | Open app and observe cards | Cards populate without crash | Partial failures should surface user-friendly error | High | Automatable (API) + Manual |
| Save settings | `PUT /settings` | Persist filter/profile config | Edit settings and Save Filters | Reload shows persisted values | Empty/default query fallback behavior | High | Automatable |
| Upload resume and set active version | `/settings/resume`, `/settings/resumes` | Ensure resume lifecycle and current flag logic | Upload resume twice | Latest marked current; older unmarked | Empty file, missing filename, unsupported file type handling | High | Automatable + Manual |
| Run automation with qualified email | `/automation/run-once` | Verify queueing flow | Seed Gmail-like data; run once | Items move to needs_review with draft and routing | Batch limit, dry-run true, low thresholds | High | Automatable |
| Run automation no matches | `/automation/run-once` | Validate idle response | Use query with no matches | Status returns idle with matched=0 | Date mode interactions | Medium | Automatable |
| Needs Review approve & send | `/candidates/{id}/approve-send` | Validate final send pipeline | Open needs_review item, approve | State becomes approved_sent; sent_at populated | Missing CC/To/resume/draft should block | High | Automatable + Manual |
| Needs Review reject | `/candidates/{id}/reject` | Validate rejection transition | Reject queued item | State becomes rejected | Reject terminal/non-needs_review states | Medium | Automatable |
| Bulk reject | `/candidates/reject-bulk` | Validate multi-item reject path | Send multiple IDs | Returns rejected_count; only needs_review changed | Empty IDs list returns 0 | Medium | Automatable |
| Fix failed mapping and requeue | `/candidates/{id}/resolve-recipients` | Confirm correction loop | Enter To/CC and save | Item becomes needs_review, routing confirmed, draft refreshed | Invalid/empty To or CC should be blocked client/server side | High | Automatable + Manual |
| Candidate listing filters | `/candidates` | Verify state/date/sort filtering | Query with various state/mail_date/sort values | Correct subset and ordering | Invalid date format returns 422 | High | Automatable |
| Analytics event tracking | `/analytics/events/view` | Ensure view event recording | Trigger page views | Event appears with metadata | Unsupported event_type returns 400 | Medium | Automatable |
| Productivity trend rendering | `/analytics/trend` + UI chart | Verify trend KPI and chart behavior | Generate events and open monitor | KPI/bars/delta reflect data | Empty-history state displays fallback text | Medium | Automatable + Manual |
| Telegram command handling | telegram_bot service + handler | Validate remote control and auth | Send `/status`, `/run`, `/auth` flows | Correct response + permission checks | Duplicate commands are deduped | Medium | Mostly manual/integration |
| Google Sheets append on send | `append_tracking_sheet_row` | Verify optional tracking write | Enable tracking and approve send | Row appended; warning stored on failure | Missing spreadsheet ID should fail gracefully | Medium | Automatable (mocked) |

## 2) Error handling test cases

| Test case name | Module | Steps | Expected result | Priority | Automation |
|---|---|---|---|---|---|
| Approve without safe routing | approve-send endpoint | Approve item with low routing confidence | 400 with routing safety detail | High | Automatable |
| Approve without active resume | approve-send endpoint | Remove active resume and approve | 400 no active resume uploaded | High | Automatable |
| Gmail send failure path | gmail_client + approve-send | Mock Gmail send exception | 502 returned; `last_error` updated | High | Automatable |
| OAuth required on run | automation run endpoint | Run with config but unauthenticated token | `oauth_required`/`oauth_in_progress` status | High | Automatable |
| Invalid analytics range/bucket | analytics endpoints | Pass unsupported range/bucket | 400 validation error | Medium | Automatable |

## 3) Input validation test cases

| Test case name | Field | Steps | Expected result | Priority | Automation |
|---|---|---|---|---|---|
| `mail_date` schema validation | settings/run request | Send invalid date | Validation error | High | Automatable |
| `default_date_mode` validation | settings | Send unsupported value | Validation error | Medium | Automatable |
| Poll interval clamping | settings | Send 0 / very large value | Clamped to [1,1440] | Medium | Automatable |
| Candidate query date format | `/candidates` | send malformed `mail_date` | 422 with clear message | High | Automatable |

## 4) API regression test set

Run on each release candidate:
- Health check endpoint returns `status=ok`.
- Settings GET/PUT roundtrip.
- Gmail status + OAuth start behavior.
- Run-once response shape and counters.
- Candidate list/detail/approve/reject/resolve endpoints.
- Analytics events/trend endpoints.

Priority: **High** | Automation: **Automatable**

## 5) UI/UX test scenarios

- Sidebar navigation updates active page and counters correctly.
- Action button labels/states change correctly (`Running...`, `Connect Gmail`, `Sync + Queue`).
- Date chip appears/disappears correctly when date filter set/cleared.
- Draft live preview reflects markdown-like bullets/bold and escapes HTML.
- Routing panel safe/blocked visual state matches confidence logic.
- Responsive layout at <=1200px and <=900px maintains usability.

Priority: **High/Medium** | Automation: **Manual + UI automation (Playwright/Cypress)**

## 6) State management test scenarios

- Initial load populates all independent data slices without race-condition crashes.
- Failed API call in one loader does not block unrelated sections from rendering.
- After actions (approve/reject/resolve/run), queue and analytics refresh consistently.
- `draftEdits` and `routingFixes` maps preserve per-item local edits.

Priority: **Medium** | Automation: **UI automation/integration**

## 7) Database/storage test cases

- SQLite table creation and migration helper (`ensure_sqlite_phase0_columns`) idempotency.
- Resume file write/read and DB metadata consistency.
- Unique `external_message_id` behavior.
- Correct lifecycle transitions persisted for recruiter emails.
- Productivity event writes and trend aggregation correctness.

Priority: **High** | Automation: **Automatable**

## 8) Security-focused test cases

- CORS policy review and production hardening checks.
- Ensure personal signature/contact values are not leaked unintentionally in logs/errors.
- Validate no auth bypass in Telegram action commands when PIN configured.
- Ensure HTML/script content in drafts is safely escaped in preview rendering.
- Verify file upload path handling does not allow traversal/injection.

Priority: **High** | Automation: **Mixed (static + integration + manual)**

## 9) Regression matrix by feature

Minimum regression before merge:
1. Settings save/load
2. OAuth connect path
3. Run-once queueing
4. Needs-review approve/reject
5. Failed mapping correction
6. Sent items visibility
7. Analytics panel rendering
8. Resume upload replacement

All above: **High priority**, mostly **automatable** with targeted manual UI checks.

---

## Performance-focused testing section

### A) Load handling
- Repeated `run-once` calls with large unread candidate pool.
- Observe backend response latency and DB write throughput.
- Verify UI remains responsive while fetching queue/analytics.

### B) Large data input
- Very large email body/subject and long draft content.
- Ensure parse/routing/draft preview do not timeout or break layout.

### C) Slow network behavior
- Simulate high latency for backend APIs.
- Validate timeout/error messaging (`runAutomation` abort handling) and retry usability.

### D) Multiple users or repeated actions
- Concurrent approve/reject operations on different candidates.
- Repeated button taps (including Telegram duplicate command handling).

### E) Memory usage
- Long session with many run logs and queue refreshes.
- Watch browser memory for uncontrolled growth from state arrays.

### F) API response time
- Measure p50/p95 for:
  - `/automation/run-once`
  - `/candidates`
  - `/analytics/trend`
- Validate acceptable thresholds under expected load.

### G) UI rendering performance
- Large candidate lists in needs_review/failed/sent pages.
- Trend chart rendering with many buckets.

### H) Failure recovery behavior
- Mid-run API failures should preserve stable UI and clear user message.
- Recovery path after OAuth completion should succeed without app reload.

Recommended cadence: run performance suite before major releases and after changes to parsing/routing/run orchestration paths.
