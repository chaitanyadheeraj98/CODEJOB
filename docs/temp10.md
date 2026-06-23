Codex Plan:

# AI Extractor + Taxonomy Extension Rollout Aligned To Current `semantic-embeddings`

## Summary

Implement `temp9.md` as an additive rollout on top of the current branch, not as a greenfield parser overhaul.

The current branch already has:

- `parse_email_with_details(...)`
- rule-based spaCy enrichment
- parser-details persistence on `RecruiterEmail`
- candidate API exposure for parser details
- Needs Review `View Details` UI
- Nvoids source-aware parser hints

So the remaining work should focus on:

- adding a separate `feature_ai_extractor_enabled` toggle
- introducing a DeepSeek structured extractor that enriches, not replaces, the current parser
- extending the parser-details payload with AI extractor evidence
- preserving unknown skills separately from approved taxonomy skills
- adding user-managed skill approval into an additive custom taxonomy layer
- upgrading the Settings UI for approved/pending skills
- avoiding duplicate AI extractor calls across Gmail/Nvoids/orchestrator flows

After each completed phase, update `docs/temp9.md` with:

- `Current Status`
- `Completed Phases`
- `Current Phase`
- `Next Phase`
- `Last Verification`
- `Open Risks / Notes`

Each phase entry should record:

- phase title
- high-level subsystems changed
- tests run and result
- whether Gmail/Nvoids behavior changed
- exact next phase to start from

## Phase Plan

### Phase 0: Baseline Realignment

Lock the current branch reality into `temp9.md` before further work.

- Record that parser-details persistence and Needs Review details UI already exist.
- Record that Nvoids already passes source hints into `parse_email_with_details(...)`.
- Record that current parser contract remains:
  - `role`
  - `location`
  - `job_location_text`
  - `salary_text`
  - `skills_text`
  - `f2f_mentioned`
  - `asks_contact_fields`
  - `is_texas_role`
- Record that current settings already separate:
  - `feature_ai_enabled`
  - `feature_semantic_enabled`
- Next phase = `Phase 1: AI Extractor Toggle And Wiring Skeleton`

### Phase 1: AI Extractor Toggle And Wiring Skeleton

Add a new settings-controlled feature gate without changing existing parser behavior yet.

- Add `feature_ai_extractor_enabled` to:
  - `UserSettings`
  - settings schemas
  - bootstrap defaults
  - SQLite runtime patch path
  - `/settings` request/response
- Default it to `false`.
- Keep it fully independent from:
  - `feature_ai_enabled`
  - `feature_semantic_enabled`
- No parsing behavior change in this phase beyond making the flag available end to end.
- Next phase = `Phase 2: Structured DeepSeek Extractor Service`

### Phase 2: Structured DeepSeek Extractor Service

Introduce a dedicated AI extraction helper for messy JD parsing.

- Add a backend module such as `backend/app/parsing/ai_extractor.py`.
- Build it around the existing DeepSeek client, but add a structured helper that requests strict JSON output.
- The extractor should return an additive structured payload, for example:
  - `role_candidates`
  - `company`
  - `primary_location`
  - `mentioned_locations`
  - `work_mode`
  - `visa_hints`
  - `experience_years_min`
  - `skills_approved`
  - `skills_unknown`
  - `confidence`
  - `evidence`
- Keep this service parser-only:
  - no queue state logic
  - no scoring thresholds
  - no draft generation
- Add defensive parsing:
  - JSON extraction fallback
  - malformed response safe failure
  - timeout-safe no-op behavior
- Next phase = `Phase 3: Merge AI Extractor Into Existing Parser Details`

### Phase 3: Merge AI Extractor Into Existing Parser Details

Extend the current parser-details pipeline instead of replacing it.

- Update `parse_email_with_details(...)` to optionally run the AI extractor only when:
  - `feature_ai_extractor_enabled` is true
  - parser input is substantial enough
  - source is eligible (`gmail`, later `nvoids`)
- Keep current deterministic parser result as the base contract.
- Keep current spaCy enrichment result as a second signal.
- Add AI extractor as a third additive signal inside parser details.
- Merge rules:
  - base deterministic result remains the fallback source of truth
  - spaCy may improve weak fields conservatively
  - AI extractor may improve fields only when cleaner / stronger than current result
  - if AI extractor fails, parser returns current branch behavior unchanged
- Extend `parser_details_json` to include:
  - `ai_extractor_result`
  - `ai_merge_notes`
  - parser version bump such as `spacy_ai_enrichment_v2`
- Do not change `parse_email()` response shape.
- Next phase = `Phase 4: Gmail Flow Wiring`

### Phase 4: Gmail Flow Wiring

Enable AI extractor in the Gmail parser path safely.

- Ensure Gmail queue/orchestration paths can pass settings into the parse-details path so the extractor is flag-controlled.
- Avoid calling the AI extractor twice for the same candidate in one request path.
- Preferred approach:
  - compute enriched parse once
  - pass merged parsed result plus parser details downstream as overrides / context
- Preserve all existing Gmail downstream contracts:
  - routing
  - hard filters
  - F2F checks
  - resume selection inputs
  - draft generation
- Next phase = `Phase 5: Nvoids Flow Wiring`

### Phase 5: Nvoids Flow Wiring

Apply AI extraction to Nvoids after existing source cleanup, not before.

- Keep current Nvoids canonical-title and cleaned-body behavior.
- Run AI extraction on the cleaned Nvoids content after source hints are prepared.
- Preserve source locking:
  - canonical title from Nvoids source hints remains high-priority
  - AI extractor may refine role text, company, location, work-mode, and skills only when it is cleaner than the current merged result
- Explicitly handle title/body conflict cases like the current screenshot example:
  - title says remote
  - body contains another city
- Keep parser details transparent about which source won for:
  - role
  - location
  - skills
- Next phase = `Phase 6: Approved vs Unknown Skill Separation`

### Phase 6: Approved vs Unknown Skill Separation

Separate safe scoring/display skills from pending user-review skills.

- Keep existing `skills_text` compatibility for downstream systems.
- Introduce internal distinction between:
  - approved normalized skills
  - unknown / unapproved extracted skills
- Store both in parser details, for example:
  - `approved_skills_text`
  - `unknown_skills`
- Downstream matching/scoring should continue to use only approved normalized skills in this phase.
- Unknown skills must not be dropped; they should survive for user review.
- Preserve existing behavior when no unknown skills exist.
- Next phase = `Phase 7: Custom Taxonomy Extension Persistence`

### Phase 7: Custom Taxonomy Extension Persistence

Add additive user-managed taxonomy extension storage.

- Do not rewrite the checked-in taxonomy artifact.
- Add a separate user-owned persistence model for approved custom skills, for example with fields like:
  - canonical skill name
  - aliases
  - category
  - optional parent/cluster hint
  - owner
  - status
  - created/updated timestamps
- Runtime taxonomy loading should merge:
  1. built-in checked-in taxonomy
  2. approved user custom skills
- Keep this merge additive and deterministic.
- If custom taxonomy loading fails, fall back safely to built-in taxonomy only.
- Next phase = `Phase 8: Upgrade Skills API`

### Phase 8: Upgrade Skills API

Expose pending and approved skill management through backend APIs.

- Add additive settings-side endpoints to:
  - list pending unknown skills
  - approve a pending skill into custom taxonomy
  - optionally reject / dismiss a pending skill
  - list approved custom skills
- Ensure candidate parser details and global pending-skill management can coexist.
- Approval flow should normalize the chosen skill before persistence.
- Rejected/dismissed skills should not keep reappearing indefinitely from the same normalized text if a suppression rule is needed.
- Keep existing settings and candidate routes backward compatible.
- Next phase = `Phase 9: Settings UI Upgrade Skills Panel`

### Phase 9: Settings UI Upgrade Skills Panel

Add the skill-management UI in Settings without disturbing existing panels.

- Add a new `Upgrade Skills` section in the current dashboard settings area.
- Show:
  - pending unknown skills
  - approve action
  - reject/dismiss action if supported
  - approved custom skills list
- Keep parser-details `View Details` in Needs Review as read-only diagnostics.
- Do not let the frontend re-run extraction logic.
- Use backend-provided data only.
- Keep layout contained within current card/grid structure.
- Next phase = `Phase 10: Candidate Detail Enhancements`

### Phase 10: Candidate Detail Enhancements

Extend the existing Needs Review details view rather than replacing it.

- Add clearer display for:
  - approved skills
  - unknown skills
  - which parser source won each field
  - source-hint vs body conflict notes
  - AI extractor confidence/evidence
- Keep the current expand/collapse behavior and draft controls unchanged.
- Make long evidence payloads wrap cleanly inside the current card layout.
- Next phase = `Phase 11: Regression And Real-Case Validation`

### Phase 11: Regression And Real-Case Validation

Validate the rollout against the current branch’s real problem cases.

- Re-test messy Gmail recruiter emails.
- Re-test Nvoids listings using the current cleaned-body/canonical-title path and the provided `Job Details.pdf` as reference for expected structure.
- Validate the screenshot-class issue where current extraction produces role/location noise like:
  - `Urgent Hiring :- Senior Java Software Engineer at Remote, Remote, USA`
- Confirm the new flow improves:
  - role quality
  - company extraction
  - location conflict handling
  - skills richness
  - unknown-skill capture
- Confirm no regressions in:
  - queue states
  - routing
  - resume selection inputs
  - AI draft flow
  - Nvoids sync/queue bridge

## Important Interface Changes

### Backend / Settings

Add one new setting:

- `feature_ai_extractor_enabled: bool` default `false`

### Candidate API

Keep existing candidate routes compatible, but extend current parser-details payload with additive fields such as:

- `ai_extractor_result`
- `ai_merge_notes`
- `approved_skills_text`
- `unknown_skills`

### Taxonomy / Skill Management

Add additive backend APIs for:

- pending extracted skills
- approve skill
- reject/dismiss skill
- approved custom skills listing

The exact route names can follow the current settings-route style, but they should remain separate from `/settings` JSON payload for operational clarity if list sizes become large.

## Test Plan

### Backend

- Settings API persists and returns `feature_ai_extractor_enabled`.
- AI extractor service:
  - returns structured JSON on valid outputs
  - fails safely on malformed or timed-out outputs
  - does not break parsing when disabled
- `parse_email_with_details(...)`:
  - preserves current parser contract
  - does not overwrite clean deterministic results with weak AI output
  - captures unknown skills separately from approved skills
- Gmail flow:
  - enriched parser runs once per candidate path
  - downstream queue/routing behavior remains stable
- Nvoids flow:
  - source hints still win when they should
  - AI extractor improves noisy body parsing without breaking canonical-title locking
- Custom taxonomy extension:
  - approved custom skills load into runtime normalization
  - failure falls back to built-in taxonomy
- Candidate serialization:
  - parser details remain available and additive

### Frontend

- Upgrade Skills panel renders pending and approved skill sections.
- Approve/reject actions call backend and refresh state.
- Existing Settings panels remain usable.
- Needs Review details still render when new parser-details fields are absent.
- Long AI evidence / unknown-skill payloads stay contained.

### Real Scenarios

- One messy Gmail JD with recruiter noise.
- One messy Nvoids listing based on the `Job Details.pdf` structure.
- One AI-heavy JD.
- One generic Java full-stack JD.
- One case where title/body location conflict exists.
- One case where unknown skills are extracted and later approved.

## Assumptions And Defaults

- Current parser-details persistence/UI is already the baseline and should be extended, not rebuilt.
- `feature_ai_extractor_enabled` remains separate from `feature_ai_enabled`.
- AI extractor is additive and must never become a hard dependency for queue execution.
- Unknown skills are preserved for review but should not affect scoring until approved.
- Built-in taxonomy stays checked in; user-approved custom skills live in separate persistence.
- `docs/temp9.md` remains the handoff source of truth and must be updated after every completed phase before ending that implementation turn.
