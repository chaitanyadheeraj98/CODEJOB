# CODEJOB Features (Current Implementation)

## Live behavior

| Feature | Current behavior | Evidence |
| --- | --- | --- |
| Run-once automation | processes unread Gmail candidates through orchestration | `backend/app/services/orchestration_service.py` |
| Candidate workflow | needs-review, failed-mapping repair, approve/reject actions | `backend/app/main.py`, `dashboard/src/App.tsx` |
| Phone intelligence | extraction + review queue + recruiter/employer buckets + opportunities | `backend/app/premium_numbers/*` |
| Gmail labeling | rules-first selection and label application | `backend/app/gmail_labeling/*` |
| Analytics | event record and trend APIs | `backend/app/main.py` analytics endpoints |

## Optional behavior

| Feature | Trigger/control | Evidence |
| --- | --- | --- |
| AI draft generation | `feature_ai_enabled` | `backend/app/main.py`, `backend/app/schemas.py` |
| Semantic score blending | `feature_semantic_enabled` | `backend/app/services/scoring_runtime_service.py` |
| Auto polling | `feature_auto_polling` + interval | `backend/app/services/auto_runner_service.py` |
| Telegram control plane | token + allowed chats configured | `backend/app/telegram_bot.py` |

## Persisted-and-implemented flags

| Flag | Runtime behavior | Evidence |
| --- | --- | --- |
| `feature_auto_send` | auto-sends only current-run queued IDs | `backend/app/services/orchestration_service.py:346-349` |
| `feature_retry_queue` | retries failed queue and promotes sendable rows | `backend/app/services/orchestration_service.py:341-343`, `:456-497` |

Additive `POST /automation/run-once` response counters:

- `auto_sent_count`
- `auto_send_failed_count`
- `retry_promoted_count`
- `retry_skipped_count`

## Persisted-but-not-implemented flags

- None found in the inspected settings schema/runtime paths for this branch.

- Audit date: 2026-05-18
- Branch: `copilot/update-docs-md-files`
- Evidence basis: code inspection
- Verification limits: runtime execution in this session was constrained by missing backend/frontend dependencies.
