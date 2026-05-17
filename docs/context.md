# CODEJOB Context

Audit date: 2026-05-17  
Branch: `copilot/update-markdown-docs-audit`

## 1) What this project currently is

CODEJOB is a single-owner recruiter-email operations system that:
- ingests unread Gmail messages,
- scores and routes candidate replies,
- queues manual approval before send,
- tracks premium-number intelligence,
- supports Telegram remote operations,
- records productivity analytics.

## 2) Core workflow outcomes implemented

- controlled manual approval before outbound send
- explainable routing evidence (`To`/`CC`, confidence, reason)
- recruiter/employer phone identity separation
- unknown-number review queue
- recruiter opportunity records with status, notes, and cold-call script generation
- Gmail labeling with rules-first + AI fallback

## 3) Runtime workflow summary

### Inbox automation
1. load effective query/date/policy
2. fetch unread Gmail candidates
3. parse/filter/score/route each candidate
4. write candidate state (`needs_review`/`failed`/`processed_skipped`)
5. run side effects (premium numbers, labeling, mark processed)

### Review and send
1. operator reviews `needs_review`
2. operator can edit draft and/or correct routing via failed-mapping flow
3. approve-send gate enforces safe routing + recipients + draft + active resume
4. successful send records productivity event and attempts Sheets append

### Manual recovery lanes
- `send-to-failed-mapping` then `resolve-recipients`
- number review classify/delete actions
- recruiter/employer bucket swap actions

## 4) Current implementation boundaries

Live and implemented:
- Gmail OAuth/sync
- run-once and auto-polling
- manual approval queue
- failed-mapping repair
- premium-number extraction + intelligence
- recruiter opportunities + cold-call generation
- analytics trend/events API
- Telegram polling control plane

Not fully implemented despite settings fields:
- `feature_auto_send`
- `feature_retry_queue`

## 5) Frontend behavior reality

Main sections in sidebar:
- Run Queue
- Needs Review
- Failed Mapping
- Premium Numbers
- Sent Items
- Recent Runs

UI placeholders still present:
- `New Campaign`
- footer `Settings`
- footer `Help Center`

## 6) Current operational limitations

- `backend/app/main.py` remains heavily centralized.
- `dashboard/src/App.tsx` remains monolithic.
- policy profiles are duplicated backend/frontend.
- startup still performs runtime schema patching.
- in-memory runtime state resets on restart.
