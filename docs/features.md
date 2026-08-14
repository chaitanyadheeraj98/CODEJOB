<!-- markdownlint-configure-file {"MD013": false} -->

# CODEJOB Features (Current Implementation)

## Live Behavior

| Feature | Runtime behavior | Evidence |
| --- | --- | --- |
| Gmail controls | The dashboard calls status, OAuth bootstrap/URL, sync, and labeling-preview routes. | `GET /gmail/status`, `POST /gmail/oauth/start`, `GET /gmail/oauth/url`, `POST /gmail/sync`, and `POST /gmail/labeling/preview` in `backend/app/main.py`. |
| Candidate workflow | Candidate lists support review, approve-send, reject, regenerate, bulk reject, recipient resolution, and dismissal. | `GET /candidates` and `/candidates/{email_id}/*` handlers in `backend/app/main.py`; focused approval and routing tests were run. |
| Intake and routing | Manual email intake parses, filters, screens, scores, and routes a candidate. | `POST /phase0/emails/ingest` in `backend/app/main.py:ingest_email`. |
| Premium-number workflow | Re-extraction, review classification, recruiter/employer buckets, opportunity CRUD, and cold-call script generation have routes and UI callers. | `/premium-numbers/*`, `/number-review/*`, `/recruiter-numbers/*`, `/employer-numbers/*`, and `/recruiter-opportunities/*`. |
| Analytics | The UI records view events and requests event lists and trends. | `POST /analytics/events/view`, `GET /analytics/events`, and `GET /analytics/trend`. |
| Settings and assets | Settings bootstrap/save plus resume and attachment CRUD routes are implemented. | `GET /settings/bootstrap`, `GET/PUT /settings`, and `/settings/resumes*` and `/settings/attachments*`. |
| Recent runs and jobs | The dashboard reads recent runs and can enqueue/cancel RQ work. | `/recent-runs*` and `/jobs/*` routes. |

## Live (Optional) Behavior

| Feature | Optional condition | Evidence |
| --- | --- | --- |
| AI drafting and semantic embeddings | Provider configuration and relevant settings flags are required. | `GET /ai/status`, settings schema, and semantic service paths. |
| Automation and auto polling | Execution depends on settings and running backend workers. | `POST /automation/run-once`, `POST /jobs/automation-run`, and settings-driven runtime loop. |
| Nvoids sync | Sync rejects requests when `feature_nvoids_enabled` is false and requires the external integration. | `POST /external-feeds/nvoids/sync` checks `feature_nvoids_enabled`. |
| Telegram | Bot configuration and polling runtime are required. | `GET /telegram/status` and `app/telegram_bot.py`. |
| Google Sheets append | The send-side integration requires configuration and was not exercised in this audit. | Send orchestration integration path. |
| In-app assistant | `FEATURE_CHAT_ENABLED=true` and a reachable local Ollama daemon are required. The widget remains visible with an explanatory disabled state otherwise. | `/chat/*`, `/mcp`, `app/ai/chat/*`, and `dashboard/src/features/chat/*`. |

## Persisted Flags With Runtime Effect

`feature_auto_polling`, `feature_nvoids_enabled`, `feature_nvoids_auto_sync`, `feature_auto_send`, `feature_retry_queue`, `feature_ai_enabled`, and `feature_semantic_enabled` are persisted settings used by runtime paths. Their configured values do not by themselves prove an external integration is operational.

## Placeholder Signals

- The current sidebar includes presentational `New Campaign`, `Settings`, and `Help Center` controls in `dashboard/src/components/Sidebar.tsx`.

The in-app assistant flow is documented in `docs/mermaids-features.md`.

- Audit date: 2026-08-14
- Branch: semantic-embeddings
- Evidence basis: both
- Verification limits: focused chat tests and a live tool-backed Ollama exchange passed; unrelated external integrations were not executed.
