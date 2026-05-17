# CODEJOB Features (Current Implementation)

Audit date: 2026-05-17  
Branch: `copilot/update-markdown-docs-audit`

## 1) Inbox automation

| Feature | Current behavior | Status |
|---|---|---|
| Gmail OAuth bootstrap | starts OAuth worker and exposes URL/status | Live |
| Run-once automation | processes unread candidates using policy/query/date inputs | Live |
| Auto polling | background loop calls run-once when enabled | Live |
| AI draft path | DeepSeek/OpenAI-compatible drafting with fallback | Live (optional) |
| Semantic scoring | embedding similarity blend when enabled | Live (optional) |
| Query bucket | saved-query sanitize/dedupe/cap(10) | Live |

## 2) Candidate workflow

| Feature | Current behavior | Status |
|---|---|---|
| Needs Review queue | qualified/routed candidates await manual action | Live |
| Approval gate | checks routing sendability, To/CC, draft, active resume | Live |
| Reject action | marks candidate rejected from needs_review | Live |
| Failed mapping repair | manual recipient correction returns to needs_review | Live |
| Bulk reject | rejects multiple needs_review records | Live |

## 3) Phone intelligence and opportunities

| Feature | Current behavior | Status |
|---|---|---|
| Premium lead extraction | upsert phone leads per source email | Live |
| Domain guard | only captures when sender domain is in configured employer domains | Live |
| Unknown number review queue | pending queue for unmatched phone identities | Live |
| Recruiter/employer buckets | separate identity tables with swap actions | Live |
| Opportunity cards | one per recruiter-number + gmail message combo | Live |
| Cold call script generation | sanitized script generation with fallback | Live |

Allowed opportunity statuses:
- `New`, `Called`, `Applied`, `Follow Up`, `Closed`, `Not Interested`

## 4) Labeling, analytics, Telegram

| Feature | Current behavior | Status |
|---|---|---|
| Gmail labeling | rules-first label selection + AI fallback, applies Gmail label | Live |
| Productivity analytics | event recording + trend APIs | Live |
| Telegram control plane | polling bot with auth-gated action commands | Live |
| Google Sheets append | post-send best-effort append | Live (optional/best-effort) |

Managed Gmail labels:
- `assessment`
- `AVAILABILITY ACTION`
- `Interview`
- `must reply`
- `must reply/important`
- `screening`

## 5) Feature-flag reality check

| Setting | Actual runtime behavior |
|---|---|
| `feature_auto_polling` | enables periodic run loop |
| `feature_auto_poll_interval_minutes` | sets polling interval (1..1440 clamp) |
| `feature_ai_enabled` | enables AI draft generation in orchestrated/manual draft paths |
| `feature_semantic_enabled` | enables semantic blending in score computation |
| `feature_auto_send` | persisted only; no bypass of manual approval gate |
| `feature_retry_queue` | persisted only; no dedicated retry worker |

## 6) Active debt tied to features

- backend/main + frontend/App remain central coupling points
- duplicated policy profile definitions (frontend/backend)
- stale tests reduce confidence in full automation regression coverage
