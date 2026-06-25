# Preferred Employer CC in Execution Control

## Summary

Add a saved `Execution Control` setting named `preferred_employer_cc_email` so Nvoids and other external-feed candidates can use one explicit employer CC email instead of relying only on the backend employer-contact pool. This v1 scope applies the override only during external-feed candidate creation and keeps manual per-candidate recipient fixes untouched.

## Implemented Changes

### Backend settings contract

- Added `preferred_employer_cc_email` to `UserSettings`
- Added the field to `SettingsRequest` and `SettingsResponse`
- Added startup migration support in `backend/app/db.py`
- Added bootstrap/default handling so fresh and existing settings rows safely initialize the field
- `/settings` now returns and persists the value in normalized lowercase form

### Execution Control UI

- Added `preferred_employer_cc_email` to the dashboard settings payload
- Added a plain email input to the `Execution Control` card
- Added help text:
  `Used as the employer CC for Nvoids/external-feed drafts. Manual per-candidate recipient fixes still win.`

### External-feed CC selection

- Updated `backend/app/external_feeds/service.py` so CC selection priority is now:
  1. use `preferred_employer_cc_email` when present and valid
  2. ignore it when it matches the recruiter `To` email
  3. otherwise fall back to `_pick_cc_from_employer_pool(...)`
- Routing reason now reflects the source:
  - preferred setting path: `External feed recruiter import with preferred employer CC from Execution Control.`
  - fallback path: `External feed recruiter import with employer pool cc.`

## Guardrails

- Empty preferred CC is allowed
- Invalid preferred CC is rejected by the settings API
- Preferred CC is normalized to lowercase before persistence/use
- Preferred CC is ignored when it equals the recruiter `To`
- Manual `/resolve-recipients` edits are not overridden because this setting only applies during external-feed enqueue

## Test Coverage

- Settings round-trip includes `preferred_employer_cc_email`
- Settings reject invalid preferred CC emails
- Nvoids sync uses the preferred CC when configured
- Nvoids sync falls back to employer-pool CC when the preferred CC is blank
- Nvoids sync falls back when the preferred CC matches recruiter `To`
- Dashboard test verifies the `Execution Control` field loads from `/settings` and is sent back on save

## Scope Notes

- Gmail routing logic is intentionally unchanged in this version
- The setting controls stored `cc_email` routing, not draft-body text
