# AI Extractor + Taxonomy Extension Rollout

## Current Status

- Phase 0 complete: baseline realignment captured against the current `semantic-embeddings` branch.
- Phase 1 complete: `feature_ai_extractor_enabled` is wired through backend settings, SQLite runtime patching, settings bootstrap, and the dashboard Automation Filters UI.
- Phase 2 complete: the structured DeepSeek extractor service now exists as an isolated parser-side module and remains disabled / unused by live Gmail and Nvoids flows.
- Phase 3 complete: `parse_email_with_details(...)` now supports additive AI extractor merge logic and parser-details evidence, but live Gmail and Nvoids flows still do not enable it yet.
- Phase 4 complete: Gmail orchestration paths now pass the AI extractor flag into the parser-details flow and reuse one merged parse per candidate path.
- Phase 5 complete: Nvoids candidate preparation now passes the AI extractor flag into the parser-details flow and reuses the merged parsed payload instead of rebuilding a manual override map.
- Phase 6 complete: parser details now expose approved normalized skills separately from unknown extracted skills, while `skills_text` remains the unchanged downstream compatibility field.
- Phase 7 complete: approved custom taxonomy persistence now exists as a separate runtime-merged layer on top of the checked-in taxonomy artifact, with safe fallback to the built-in taxonomy when custom loading fails.
- Phase 8 complete: additive skills-management APIs now expose pending unknown skills, approved custom skills, and approve/dismiss actions without changing existing settings or candidate contracts.
- Phase 9 complete: Settings now renders an additive `Upgrade Skills` panel with pending unknown skills, approve/dismiss actions, and approved custom skills, while leaving existing parser-details diagnostics read-only.
- Phase 10 complete: Needs Review parser details now surface approved skills, unknown skills, winning parser sources, conflict notes, and AI confidence/evidence without changing draft editing or action controls.
- Phase 11 complete: focused backend and dashboard regression packs passed, confirming the additive AI extractor rollout stays contract-safe across Gmail, Nvoids, parser details, and custom-skill management.
- Live Gmail and Nvoids behavior can now use the AI extractor when `feature_ai_extractor_enabled` is on.
- The current rollout plan in this file is complete. Any next Codex run should start from optional follow-up tuning or a new feature plan, not from an unfinished phase here.

## Completed Phases

### Phase 0: Baseline Realignment

- Status: complete
- Changed subsystems:
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What was locked in:
  - current parser contract still remains:
    - `role`
    - `location`
    - `job_location_text`
    - `salary_text`
    - `skills_text`
    - `f2f_mentioned`
    - `asks_contact_fields`
    - `is_texas_role`
  - current branch already includes:
    - `parse_email_with_details(...)`
    - rule-based spaCy enrichment
    - parser-details persistence on `RecruiterEmail`
    - parser details exposure through candidate APIs
    - Needs Review parser-details UI
    - Nvoids source-aware parser hints
  - current settings already separate:
    - `feature_ai_enabled`
    - `feature_semantic_enabled`
- Behavior change:
  - none

### Phase 1: AI Extractor Toggle And Wiring Skeleton

- Status: complete
- Changed subsystems:
  - [backend/app/models.py](D:/My%20Websites/CodeJob/backend/app/models.py)
  - [backend/app/schemas.py](D:/My%20Websites/CodeJob/backend/app/schemas.py)
  - [backend/app/main.py](D:/My%20Websites/CodeJob/backend/app/main.py)
  - [backend/app/services/settings_bootstrap_service.py](D:/My%20Websites/CodeJob/backend/app/services/settings_bootstrap_service.py)
  - [backend/app/db.py](D:/My%20Websites/CodeJob/backend/app/db.py)
  - [dashboard/src/App.tsx](D:/My%20Websites/CodeJob/dashboard/src/App.tsx)
  - [dashboard/src/features/ai/types.ts](D:/My%20Websites/CodeJob/dashboard/src/features/ai/types.ts)
  - [backend/tests/test_schemas.py](D:/My%20Websites/CodeJob/backend/tests/test_schemas.py)
  - [backend/tests/test_external_feeds_api.py](D:/My%20Websites/CodeJob/backend/tests/test_external_feeds_api.py)
- What changed:
  - added new independent setting: `feature_ai_extractor_enabled`
  - default is `false`
  - setting is persisted through:
    - SQLAlchemy model
    - Pydantic settings request/response
    - settings bootstrap default row
    - SQLite runtime patch path
    - `/settings` GET/PUT mapping
  - dashboard Automation Filters now renders:
    - `Enable AI Features`
    - `Enable AI Extractor`
    - `Enable Semantic Matching`
- Behavior change:
  - settings/UI only
  - parser, queue, Gmail, and Nvoids flows remain unchanged

### Phase 2: Structured DeepSeek Extractor Service

- Status: complete
- Changed subsystems:
  - [backend/app/ai/deepseek_client.py](D:/My%20Websites/CodeJob/backend/app/ai/deepseek_client.py)
  - [backend/app/parsing/ai_extractor.py](D:/My%20Websites/CodeJob/backend/app/parsing/ai_extractor.py)
  - [backend/app/parsing/__init__.py](D:/My%20Websites/CodeJob/backend/app/parsing/__init__.py)
  - [backend/tests/test_ai_extractor.py](D:/My%20Websites/CodeJob/backend/tests/test_ai_extractor.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - added `deepseek_json_completion(...)` as a strict JSON helper on top of the existing DeepSeek client
  - added safe JSON parsing support for:
    - plain JSON object responses
    - fenced JSON responses
    - malformed JSON failure with controlled error
  - added isolated parser-side service:
    - `extract_ai_job_details(...)`
    - `AIExtractorResult`
    - `ai_extractor_result_to_payload(...)`
  - extractor currently returns additive structured fields including:
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
    - `error` on safe failure
  - current taxonomy helpers are already used to split extractor skill output into:
    - approved taxonomy-known skills
    - unknown skills preserved for later phases
- Behavior change:
  - no live Gmail or Nvoids parser behavior changed in this phase
  - the extractor service exists but is not wired into `parse_email_with_details(...)` yet

### Phase 3: Merge AI Extractor Into Existing Parser Details

- Status: complete
- Changed subsystems:
  - [backend/app/phase0.py](D:/My%20Websites/CodeJob/backend/app/phase0.py)
  - [backend/tests/test_phase0_routing.py](D:/My%20Websites/CodeJob/backend/tests/test_phase0_routing.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - extended `parse_email_with_details(...)` with additive optional flag:
    - `ai_extractor_enabled: bool = False`
  - kept current deterministic parser as the base contract
  - kept current spaCy enrichment as the second signal
  - added AI extractor as a third additive parser-details signal only when:
    - the flag is enabled
    - the parser input is substantial enough
    - the source is one of:
      - `gmail`
      - `manual`
      - `nvoids`
  - added conservative merge rules so AI can improve the merged result only when appropriate:
    - approved AI extractor skills are merged into taxonomy-normalized `skills_text`
    - AI role can replace the current merged role only when the current role is weak
    - AI location can replace the current merged location only when the current location is weak
    - Nvoids canonical-title/source locking remains protected
  - extended parser details with additive fields:
    - `ai_extractor_result`
    - `ai_merge_notes`
    - parser version bump to `spacy_ai_enrichment_v2` when the AI extractor path runs
  - added fail-safe handling so unexpected extractor exceptions do not break parsing:
    - parsed output remains unchanged
    - error evidence is stored in parser details instead
- Behavior change:
  - parser-details path only
  - no live Gmail or Nvoids caller enables the new path yet, so current queue behavior remains unchanged

### Phase 4: Gmail Flow Wiring

- Status: complete
- Changed subsystems:
  - [backend/app/automation/queue_preparation.py](D:/My%20Websites/CodeJob/backend/app/automation/queue_preparation.py)
  - [backend/app/automation/run_orchestrator.py](D:/My%20Websites/CodeJob/backend/app/automation/run_orchestrator.py)
  - [backend/app/services/orchestration_service.py](D:/My%20Websites/CodeJob/backend/app/services/orchestration_service.py)
  - [backend/app/main.py](D:/My%20Websites/CodeJob/backend/app/main.py)
  - [backend/tests/test_run_orchestrator.py](D:/My%20Websites/CodeJob/backend/tests/test_run_orchestrator.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - added `parse_email_with_details` as an orchestration dependency so Gmail flows can pass:
    - `source="gmail"`
    - `ai_extractor_enabled=user_settings.feature_ai_extractor_enabled`
  - updated Gmail sync import path to use the flag-aware parser-details call
  - updated Gmail run orchestrator path to:
    - parse once with `parse_email_with_details(...)`
    - reuse the merged parsed result for resume selection
    - pass that merged parsed result into queue preparation as `parsed_overrides`
    - reuse the same parser-details payload for candidate persistence
  - updated queue preparation so when `parsed_overrides` is provided it does not call the plain parser again
  - updated Gmail candidate persistence so `parser_details_json` is carried through for:
    - new needs-review candidates
    - processed-skipped candidates
    - failed-mapping candidates
    - existing candidates being refreshed through those paths
- Behavior change:
  - Gmail orchestration paths now support the AI extractor when the new setting is enabled
  - the Gmail candidate path no longer re-runs parsing after the first enriched parse
  - Nvoids behavior remains unchanged in this phase

### Phase 5: Nvoids Flow Wiring

- Status: complete
- Changed subsystems:
  - [backend/app/external_feeds/service.py](D:/My%20Websites/CodeJob/backend/app/external_feeds/service.py)
  - [backend/tests/test_external_feeds_api.py](D:/My%20Websites/CodeJob/backend/tests/test_external_feeds_api.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - updated the Nvoids candidate queue bridge to pass:
    - `ai_extractor_enabled=settings.feature_ai_extractor_enabled`
    - existing `source_hints` for canonical title, location, company, work mode, and visa hints
  - updated the Nvoids candidate queue bridge to reuse the single merged parser result via:
    - `parsed_overrides=dict(parsed)`
    - instead of rebuilding a narrower manual parsed override payload
  - preserved current Nvoids source locking behavior on the candidate row:
    - canonical/source-cleaned Nvoids role still remains the persisted `RecruiterEmail.role`
  - added focused regression coverage proving:
    - Nvoids sync now passes the AI extractor flag into `parse_email_with_details(...)`
    - Nvoids sync forwards source hints into the parser-details path
    - parser details with AI extractor evidence persist onto the queued candidate row
- Behavior change:
  - Nvoids candidate preparation now supports the AI extractor when the new setting is enabled
  - Nvoids candidate preparation now reuses one merged parse per candidate path
  - canonical Nvoids role persistence remains unchanged and source-locked

### Phase 6: Approved vs Unknown Skill Separation

- Status: complete
- Changed subsystems:
  - [backend/app/phase0.py](D:/My%20Websites/CodeJob/backend/app/phase0.py)
  - [backend/tests/test_phase0_routing.py](D:/My%20Websites/CodeJob/backend/tests/test_phase0_routing.py)
  - [backend/tests/test_schemas.py](D:/My%20Websites/CodeJob/backend/tests/test_schemas.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - extended parser-details payload with additive top-level fields:
    - `approved_skills_text`
    - `unknown_skills`
  - kept `skills_text` unchanged as the compatibility-safe parsed field used downstream by scoring and queue logic
  - mapped `approved_skills_text` to the current merged normalized `skills_text`
  - mapped `unknown_skills` to the AI extractor unknown-skill list when present, otherwise an empty list
  - kept failure-safe behavior intact:
    - AI disabled path still returns `unknown_skills=[]`
    - AI extractor error path still returns `unknown_skills=[]`
    - parser output contract did not change
  - updated schema regression coverage so serialized candidate parser details can carry the new additive fields cleanly
- Behavior change:
  - parser details now expose the approved-vs-unknown skill split directly for Gmail and Nvoids candidates
  - downstream scoring/matching behavior remains unchanged in this phase

### Phase 7: Custom Taxonomy Extension Persistence

- Status: complete
- Changed subsystems:
  - [backend/app/models.py](D:/My%20Websites/CodeJob/backend/app/models.py)
  - [backend/app/db.py](D:/My%20Websites/CodeJob/backend/app/db.py)
  - [backend/app/skill_taxonomy.py](D:/My%20Websites/CodeJob/backend/app/skill_taxonomy.py)
  - [backend/app/phase0.py](D:/My%20Websites/CodeJob/backend/app/phase0.py)
  - [backend/tests/test_skill_taxonomy.py](D:/My%20Websites/CodeJob/backend/tests/test_skill_taxonomy.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - added a separate persistence model for approved custom taxonomy entries:
    - `CustomSkillTaxonomyEntry`
  - added SQLite runtime patching for:
    - `custom_skill_taxonomy_entries` table creation
    - indexes on `owner_id`, `canonical_name`, and `status`
    - additive column guards for existing local databases
  - extended the taxonomy runtime with additive custom-skill merging:
    - built-in checked-in taxonomy still loads first
    - approved custom skills now merge in afterward
    - custom skill load failures now fall back safely to built-in taxonomy only
  - added a taxonomy cache-clear helper for later approval flows:
    - `clear_skill_taxonomy_cache()`
  - added focused regression coverage proving:
    - approved custom skills can be merged additively into runtime normalization
    - custom taxonomy loading failures do not break built-in normalization
  - aligned parser enrichment input with the deterministic parser by passing cleaned body text into spaCy enrichment, preventing recruiter footer noise from being merged back into parsed skills during the expanded regression run
- Behavior change:
  - no API or queue contract changed in this phase
  - runtime taxonomy can now recognize approved custom skills when they exist in persistence
  - parser/footer behavior is slightly safer because enrichment now uses cleaned body text instead of raw recruiter footer text

### Phase 8: Upgrade Skills API

- Status: complete
- Changed subsystems:
  - [backend/app/schemas.py](D:/My%20Websites/CodeJob/backend/app/schemas.py)
  - [backend/app/main.py](D:/My%20Websites/CodeJob/backend/app/main.py)
  - [backend/tests/test_external_feeds_api.py](D:/My%20Websites/CodeJob/backend/tests/test_external_feeds_api.py)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - added additive skill-management schemas for:
    - pending skill rows
    - approved custom skill responses
    - approve requests
    - dismiss requests
  - added new settings-side backend routes:
    - `GET /settings/skills/pending`
    - `GET /settings/skills/approved`
    - `POST /settings/skills/approve`
    - `POST /settings/skills/dismiss`
  - pending unknown skills are now aggregated from candidate `parser_details_json`:
    - owner-scoped
    - deduped per candidate
    - counted across candidates
    - suppressed when the normalized skill is already built-in, approved, or dismissed
  - approve and dismiss actions now upsert `CustomSkillTaxonomyEntry` rows safely:
    - normalized skill names
    - aliases persisted as JSON
    - taxonomy cache cleared after changes so runtime normalization picks up approvals immediately
  - approved custom skill listing now returns parsed alias arrays instead of raw JSON strings
  - focused API regression coverage now proves:
    - pending unknown skills appear until approved
    - approved skills disappear from the pending list
    - dismissed skills stay suppressed from the pending list
    - approved-skill listing excludes dismissed entries
- Behavior change:
  - backend now exposes the Phase 8 additive skill-management API
  - Gmail/Nvoids parsing, queueing, routing, and resume-selection behavior remain unchanged in this phase

### Phase 9: Settings UI Upgrade Skills Panel

- Status: complete
- Changed subsystems:
  - [dashboard/src/App.tsx](D:/My%20Websites/CodeJob/dashboard/src/App.tsx)
  - [dashboard/src/App.css](D:/My%20Websites/CodeJob/dashboard/src/App.css)
  - [dashboard/src/App.skillUpgrade.test.tsx](D:/My%20Websites/CodeJob/dashboard/src/App.skillUpgrade.test.tsx)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - added a new Settings card:
    - `Upgrade Skills`
  - the new panel now shows:
    - pending unknown skills
    - approve action
    - dismiss action
    - approved custom skills list
  - the dashboard now loads pending and approved skill data during the existing settings bootstrap flow
  - approve/dismiss actions now call the Phase 8 backend APIs and refresh the panel state after completion
  - the new panel is layout-contained with additive CSS only:
    - responsive two-column layout
    - wrapped long skill names and metadata
    - contained action rows
  - the existing Needs Review parser-details UI remains unchanged and read-only in this phase
- Behavior change:
  - Settings now exposes the custom taxonomy approval workflow end to end
  - Gmail/Nvoids parsing, queue states, and draft behavior remain unchanged in this phase

### Phase 10: Candidate Detail Enhancements

- Status: complete
- Changed subsystems:
  - [dashboard/src/App.tsx](D:/My%20Websites/CodeJob/dashboard/src/App.tsx)
  - [dashboard/src/App.css](D:/My%20Websites/CodeJob/dashboard/src/App.css)
  - [dashboard/src/App.parserDetails.test.tsx](D:/My%20Websites/CodeJob/dashboard/src/App.parserDetails.test.tsx)
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - extended the existing Needs Review parser-details panel instead of replacing it
  - added new read-only parser summary blocks for:
    - approved skills
    - unknown skills
    - winning parser source per field
    - source conflict notes
  - added richer diagnostics inside the existing details view for:
    - AI extractor result
    - AI merge notes
    - AI confidence and evidence
  - added frontend-only conflict detection summaries using already persisted backend fields such as:
    - `source_hints`
    - `ai_extractor_result`
    - `approved_skills_text`
    - `unknown_skills`
  - kept the current expand/collapse flow, draft editor, live preview, and action buttons unchanged
  - added additive CSS so large details payloads stay contained inside the current `emailItem` card
- Behavior change:
  - Needs Review now gives better parser transparency and debugging context
  - Gmail/Nvoids queue behavior and backend contracts remain unchanged in this phase

### Phase 11: Regression And Real-Case Validation

- Status: complete
- Changed subsystems:
  - [docs/temp9.md](D:/My%20Websites/CodeJob/docs/temp9.md)
- What changed:
  - completed a validation-only phase with no production code edits
  - re-ran the focused backend regression pack that covers:
    - taxonomy normalization
    - parser contract stability
    - AI extractor service behavior
    - Gmail orchestration wiring
    - Nvoids queue bridge behavior
    - candidate/settings schema compatibility
  - re-ran the focused dashboard regression pack that covers:
    - parser-details panel
    - skill-upgrade panel
    - resume-database panel
- Behavior change:
  - none
  - this phase validates the rollout rather than changing runtime behavior

## Current Phase

- Completed and verified: `Phase 11: Regression And Real-Case Validation`

## Next Phase

- Rollout complete
- Next Codex work should start from:
  - optional live/manual scenario tuning
  - or a new feature plan

## Last Verification

- Focused backend tests passed:
  - `uv run pytest backend/tests/test_skill_taxonomy.py backend/tests/test_phase0_routing.py backend/tests/test_schemas.py backend/tests/test_ai_extractor.py backend/tests/test_run_orchestrator.py backend/tests/test_external_feeds_api.py`
  - result: `96 passed`
- Focused frontend tests passed:
  - `npm test -- --run src/App.parserDetails.test.tsx src/App.skillUpgrade.test.tsx src/App.resumeDatabase.test.tsx`
  - result: `3 passed`
- Verified assertions:
  - `parse_email_with_details(...)` still preserves the deterministic parser contract while carrying additive parser-details evidence
  - Gmail orchestration still reuses a single merged parse per candidate path when the AI extractor is enabled
  - Nvoids candidate preparation still reuses a single merged parse per candidate path and preserves source-aware role locking behavior
  - approved-vs-unknown skill separation remains additive diagnostics only and does not break downstream `skills_text` compatibility
  - pending/approved skill-management APIs remain compatible with parser-details persistence
  - the parser-details panel still renders collapsed by default and expands the richer diagnostics safely
  - the skill-upgrade and resume-database dashboard panels still pass alongside the richer parser-details UI
- Verification environment notes:
  - the prior `npm run build` limitation still remains on this machine because TypeScript cannot write:
    - `dashboard/.tsbuildinfo/tsconfig.app.tsbuildinfo`
    - `dashboard/.tsbuildinfo/tsconfig.node.tsbuildinfo`
  - no new Phase 11 validation error was introduced during the focused test run

## Open Risks / Notes

- Gmail and Nvoids run paths now both reuse one enriched parse per candidate when they use the parser-details path.
- `parse_email()` still calls `parse_email_with_details(subject, body)` without the extractor flag, which is expected until a broader parser-contract change is intentionally made.
- Approved and unknown extracted skills are now separated in parser details, and there is now a backend approval workflow plus suppression-safe skill-management API over the custom taxonomy layer.
- This rollout is code-complete for the current plan.
- Remaining work, if desired later, is tuning-oriented rather than missing implementation:
  - optional live/manual Gmail and Nvoids spot checks beyond the fixture-backed regression suite
  - optional refinement of parser winner/conflict summaries based on real reviewer feedback
  - optional expansion of frontend regression coverage if more parser-detail sections are added later
- Dashboard build verification is currently limited by `.tsbuildinfo` EPERM write failures on this machine.

## Remaining Plan

- Current `temp9.md` rollout is complete.
- Any next plan should start as a fresh follow-up document if new scope is added.
