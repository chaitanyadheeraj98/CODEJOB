# CODEJOB Features (Current Implementation)

## Live Behavior

| Feature | Runtime behavior | Evidence |
| --- | --- | --- |
| Gmail OAuth and status | OAuth bootstrap/status endpoints and auth polling support in UI | `backend/app/main.py`, `dashboard/src/App.tsx` |
| Run-once automation | Fetch/process candidates and queue state transitions | `backend/app/main.py`, `backend/app/automation/run_orchestrator.py` |
| Candidate queues | `run_queue`, `needs_review`, `failed_mapping`, `sent_items` views with actions | `dashboard/src/App.tsx` |
| Premium numbers workflow | extraction, review queue, recruiter/employer buckets, opportunity tracking | `backend/app/premium_numbers/*`, `backend/app/main.py` |
| Productivity analytics | event write + trend/read APIs and UI rendering | `backend/app/main.py`, `dashboard/src/App.tsx` |
| External feed ingestion | Nvoids sync endpoints and source-type handling | `backend/app/main.py`, `backend/app/external_feeds/service.py` |

## Optional Behavior

| Feature | Behavior when enabled | Evidence |
| --- | --- | --- |
| AI drafting | AI model-assisted drafts with fallback paths | `backend/app/ai/*`, `dashboard/src/App.tsx` |
| Semantic scoring | embedding-based scoring blend | `backend/app/semantic/*`, `backend/app/services/scoring_runtime_service.py` |
| Telegram operations | remote runtime controls via polling bot | `backend/app/telegram_bot.py`, `backend/app/services/telegram_runtime_service.py` |
| Google Sheets append | post-send best-effort tracking row append | `backend/app/gmail_client.py` send/append helpers |

## Persisted and Runtime-Active Flags

| Setting | Runtime effect |
| --- | --- |
| `feature_auto_polling` | enables periodic run loop |
| `feature_auto_poll_interval_minutes` | controls loop interval bounds |
| `feature_nvoids_enabled` | allows manual sync endpoint execution |
| `feature_nvoids_auto_sync` | enables periodic external-feed sync in auto runner paths |
| `feature_auto_send` | permits auto-send phase for current-run queued IDs |
| `feature_retry_queue` | enables failed-queue retry/promote behavior |
| `feature_ai_enabled` | toggles AI draft generation paths |
| `feature_semantic_enabled` | toggles semantic ranking behavior |

## Placeholder or Not Fully Implemented Signals

- Sidebar buttons `New Campaign`, `Settings`, and `Help Center` are presentational actions in current UI shell.
- Reviewer note: treat these as UI placeholders, not fully implemented workflow features.

Mermaid not needed: this update is a feature inventory/state alignment, not a flow change.

- Audit date: 2026-05-22
- Branch: external-recruiter-feed-ingestion
- Evidence basis: both
- Verification limits: backend full pytest is currently blocked by stale import in `test_phone_attribution.py`.
