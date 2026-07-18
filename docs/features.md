<!-- markdownlint-configure-file {"MD013": false} -->

# CODEJOB Features (Current Implementation)

## Live Behavior

| Feature | Runtime behavior | Evidence |
| --- | --- | --- |
| Gmail OAuth and inbox sync | OAuth start/url/status and sync endpoints are live | `GET /gmail/status`, `POST /gmail/oauth/start`, `GET /gmail/oauth/url`, `POST /gmail/sync` in `backend/app/main.py` |
| Run-once automation | Run-once processing endpoint is live | `POST /automation/run-once` in `backend/app/main.py` and frontend call in `dashboard/src/App.tsx` |
| Candidate queue workflow | Needs review, failed, sent queues with approve/reject/fail actions | `/candidates*` endpoints in backend and queue actions in `App.tsx` |
| Premium number workflow | Re-extract, review classification, recruiter/employer buckets, opportunity CRUD and script generation are live | `/premium-numbers/*`, `/number-review/*`, `/recruiter-numbers/*`, `/employer-numbers/*`, `/recruiter-opportunities/*` |
| Productivity analytics | View-event write and trend/event read endpoints are live | `/analytics/events/view`, `/analytics/events`, `/analytics/trend` |
| Nvoids feed sync | External feed sync and run listing routes are live | `/external-feeds/nvoids/sync`, `/external-feeds/runs` |

## Live (Optional) Behavior

| Feature | Optional condition | Evidence |
| --- | --- | --- |
| AI drafting | Requires `feature_ai_enabled` and provider configuration | AI status and settings flags in `backend/app/main.py`, toggle/UI in `App.tsx` |
| Semantic embeddings and blended scoring | Requires semantic feature/provider settings | semantic settings fields and AI status metadata in `main.py`; semantic toggle in `App.tsx` |
| Telegram operations | Requires bot token/allowed chats and polling runtime | `GET /telegram/status`, `/review <email_id>` command handling, runtime telegram command/callback handling; bot replies with rich candidate detail (routing, draft, resume context) for `needs_review` candidates |
| Google Sheets append | Best-effort append path on send flows when configured | integration hooks in orchestration path (backend service wiring) |

## Persisted Flags With Runtime Effect

| Setting | Runtime effect |
| --- | --- |
| `feature_auto_polling` | Enables periodic automation loop |
| `feature_auto_poll_interval_minutes` | Controls auto-run interval bounds |
| `feature_nvoids_enabled` | Enables manual Nvoids sync endpoint usage |
| `feature_nvoids_auto_sync` | Enables periodic Nvoids sync behavior |
| `feature_auto_send` | Enables auto-send phase behavior |
| `feature_retry_queue` | Enables retry/promote behavior for failed items |
| `feature_ai_enabled` | Enables AI draft generation paths |
| `feature_semantic_enabled` | Enables semantic scoring paths |

## Placeholder Signals

- Sidebar actions `New Campaign`, `Settings`, and `Help Center` remain presentational in current shell (`dashboard/src/components/Sidebar.tsx`).

Mermaid not needed: this update is feature inventory and status normalization, not a flow change.

- Audit date: 2026-06-09
- Branch: copilot/update-md-files-another-one
- Commit: 7c71e7c
- Evidence basis: code inspection
- Verification limits: no end-to-end runtime integration test run in this session.
