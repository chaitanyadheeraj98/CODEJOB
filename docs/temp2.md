# Parser Refactor Plan Aligned To Current `semantic-embeddings`

## Current Status

- `docs/temp2.md` has been realigned to the current branch instead of the older greenfield assumptions.
- The active parser hub is `backend/app/phase0.py::parse_email_with_details(...)`.
- The current live parser flow is:
  - base parser
  - Nvoids source-hint stabilization
  - optional AI extractor
  - merged final result
- `feature_ai_extractor_enabled` already exists and is already wired through Gmail, Nvoids, orchestrator, and manual parser entry points.
- Nvoids already has:
  - canonical title extraction
  - `source_hints`
  - empty-detail guards
  - row-level sync resilience
- `parser_details_json` already exists and is already exposed in candidate review UI.

## Completed Phases

- Phase 0: Freeze Current Branch Behavior
  - Locked the current parser-details shape in focused parser tests.
  - Locked that Gmail orchestration currently reuses `parse_email_with_details(...)` and passes the merged parsed result into resume selection.
  - Reconfirmed that Nvoids already passes `source_hints` and the AI extractor flag through the current sync path.
- Phase 1: Remove spaCy Enrichment From Execution Path
  - Removed the live `enrich_job_text(...)` execution path from `parse_email_with_details(...)`.
  - Trimmed parser details so they no longer depend on `enrichment_result` or `merge_notes`.
  - Preserved Nvoids title/location behavior by applying source hints directly in the parser layer.

## Completed Branch Reality

- `parse_email()` still returns the stable downstream contract:
  - `role`
  - `location`
  - `job_location_text`
  - `salary_text`
  - `skills_text`
  - `f2f_mentioned`
  - `asks_contact_fields`
  - `is_texas_role`
- Current parser details shape is enrichment-centric and merge-centric:
- Current parser details shape is base-parser + optional-AI-centric:
  - `base_parser_result`
  - `ai_extractor_result`
  - `approved_skills_text`
  - `unknown_skills`
  - `merged_result`
  - `ai_merge_notes`
  - `source_hints`

## Current Refactor Goal

- Simplify the current parser architecture into two clean modes:
  - AI extractor OFF -> base parser only
  - AI extractor ON -> AI extractor only
  - AI failure -> base parser emergency fallback with warning
- Move taxonomy into a post-extraction audit role instead of using it as the hard gate for final JD `skills_text`.
- Let resume matching compare the final JD `skills_text` directly against `ResumeAsset.skills_text`.

## Current Phase

- Phase 9 complete.

## Next Phase

- Plan complete. No next implementation phase in this document.

## Last Verification

- Verified against current branch code paths in:
  - `backend/app/phase0.py`
  - `backend/app/external_feeds/parser.py`
  - `backend/app/external_feeds/service.py`
  - `backend/app/automation/queue_preparation.py`
- Focused baseline regression passed with `DEBUG=false`:
- Focused Phase 1 regression passed with `DEBUG=false`:
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
- Result: `63 passed`
- Focused Phase 2 regression passed with `DEBUG=false`:
  - `backend/tests/test_ai_extractor.py`
  - `backend/tests/test_phase0_routing.py`
- Result: `30 passed`
- Focused Phase 3 regression passed with `DEBUG=false`:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_ai_extractor.py`
  - `backend/tests/test_phase0_routing.py`
- Result: `32 passed`
- Focused Phase 4 regression passed with `DEBUG=false`:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
- Result: `71 passed`
- Focused Phase 5 regression passed with `DEBUG=false`:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
- Result: `71 passed`
- Focused Phase 6 regression passed with `DEBUG=false`:
  - `backend/tests/test_scoring_runtime_service.py`
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
- Result: `83 passed`
- Focused Phase 7 regression passed with `DEBUG=false`:
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_schemas.py`
- Result: `44 passed`
- Focused Phase 8 frontend verification passed:
  - `dashboard/src/App.parserDetails.test.tsx`
- Result: `1 passed`
- Focused Phase 9 full regression validation passed:
  - `backend/tests/test_scoring_runtime_service.py`
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
  - `dashboard/src/App.parserDetails.test.tsx`
- Result:
  - backend: `85 passed`
  - frontend: `1 passed`

## Open Risks / Notes

- Current mismatch is architectural, not missing infrastructure:
  - AI is additive, not authoritative
  - taxonomy approval still narrows AI-extracted skill output
  - final matching still depends too much on normalized/taxonomy-approved skill paths
- This plan is a refactor-on-top-of-existing-parser-stack, not an initial feature build.
- Phase 1 intentionally kept UI untouched; parser-details UI can still render the removed enrichment fields as empty until the later cleanup phase.
- Phase 2 intentionally kept legacy compatibility fields alive inside the AI extractor payload:
  - `skills_approved`
  - `skills_unknown`
- The extractor now treats free-form `skills_text` as the primary skills output, but downstream parser callers still consume compatibility buckets until Phase 3 and later parser-flow refactors land.
- Phase 3 keeps the current public parser contract unchanged, but parser details now carry a dedicated `skills_audit` payload.
- `approved_skills_text` and `unknown_skills` are now compatibility mirrors of the post-extraction audit result, not direct mirrors of the raw AI extractor payload.
- AI-enabled parser paths now preserve unknown AI-extracted skills inside final `skills_text` instead of dropping them during merge.
- Phase 4 adds DB/runtime persistence for structured audited skills, but does not expose `skills_json` in candidate APIs yet.
- `skills_json` is now persisted from manual ingest, Gmail orchestration, and Nvoids queueing using the post-audit parser payload when available and a `skills_text` audit fallback otherwise.
- A narrow Gmail orchestrator regression was fixed during Phase 4 by building `skills_json` from the already-available merged parser result before queue preparation finishes.
- Phase 5 intentionally keeps legacy parser-details compatibility fields alive:
  - `approved_skills_text`
  - `unknown_skills`
  - `merged_result`
  - `ai_merge_notes`
  so the current Needs Review parser-details UI and pending-skill flow remain stable until the later cleanup phase.
- Live parser execution is now mode-based instead of merge-based:
  - AI extractor OFF -> base parser only
  - AI extractor ON and successful -> AI extractor owns the final parse result
  - AI extractor failure -> base parser fallback with explicit parser warning
- Nvoids source hints still apply in both modes, so canonical title/location locking remains intact even after the parser-mode refactor.
- Phase 6 shifts resume matching to trust saved skill strings directly:
  - raw JD `skills_text` vs raw `ResumeAsset.skills_text` overlap is now the primary resume-side comparison signal
  - taxonomy/intent weighting remains a secondary signal
  - resumes without saved `skills_text` are scored weak instead of falling back to resume file extraction during matching
- Semantic scoring still runs when enabled, but resume-side semantic text no longer falls back to file extraction in the matching path when saved resume skills are missing.
- Phase 7 narrows manual upgrade inputs to structured unknown skills first:
  - `/settings/skills/pending` now prefers `RecruiterEmail.skills_json["unknown"]`
  - legacy `parser_details_json["unknown_skills"]` remains as a backward-compatible fallback
  - known audited skills in `skills_json["known"]` no longer enter the pending-skill list when structured audit data exists
- Phase 8 simplifies the Needs Review parser-details UI to the current parser contract:
  - removed dependence on `enrichment_result`, `merge_notes`, `ai_merge_notes`, and old winning-source summaries
  - panel now centers on `parser_mode`, `fallback_used`, `parser_warning`, final result, base fallback, `skills_audit`, source hints, and AI evidence
  - expand/collapse behavior and review/draft controls remain unchanged
- Phase 9 confirms the refactor is stable across Gmail, Nvoids, parser-mode toggles, unknown-skill retention, pending-skill narrowing, raw-skill resume matching, and the current parser-details UI.

---

## Summary

This branch is already far enough along that the safest path is to refactor the existing parser stack instead of replacing it blindly. The real work now is to simplify `parse_email_with_details(...)`, decouple AI extraction from taxonomy gating, persist a structured skill audit payload, and make resume matching trust the final parsed JD skill string against the saved resume skill string.

---

## Phase Plan

### Phase 0: Freeze Current Branch Behavior

Document and test the current live behavior before changing parser logic.

- Confirm Gmail path uses `parse_email_with_details(..., ai_extractor_enabled=...)`.
- Confirm Nvoids path uses `parse_email_with_details(..., source_hints=...)`.
- Lock the current parser details shape with focused tests.
- Confirm resume selection currently receives merged parsed output, not raw AI-only output.

Expected result:

- A stable baseline exists before removing enrichment or changing parser ownership.

After completion, update this file with:

- `Current Phase: Phase 0 complete`
- `Next Phase: Phase 1: Remove spaCy Enrichment From Execution Path`
- tests run and result

Status: Complete

- Added parser baseline coverage in:
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
- Current frozen behavior now covered:
  - parser hub remains `parse_email_with_details(...)`
  - parser details still include `enrichment_result`, `merge_notes`, and `ai_merge_notes`
  - Gmail path currently passes merged parsed output into resume selection
  - Nvoids source-hint + AI-flag path remains covered by existing external-feed tests

### Phase 1: Remove spaCy Enrichment From Execution Path

Refactor the parser flow so `backend/app/parsing/spacy_enrichment.py` is no longer part of live parse execution.

- Stop calling `enrich_job_text(...)` inside `parse_email_with_details(...)`.
- Keep file deletion optional at first; unused is acceptable.
- Remove parser-details dependence on:
  - `enrichment_result`
  - `merge_notes`
- Reduce the current 3-way merge toward a 2-mode parser contract.

Expected result:

- Live parser execution no longer depends on the enrichment layer.

After completion, update this file with:

- changed subsystems
- behavior change verification
- `Next Phase: Phase 2: Redesign AI Extractor To Return Free Skills`

Status: Complete

- Changed subsystems:
  - `backend/app/phase0.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
- Verified behavior:
  - live parser execution no longer calls spaCy enrichment
  - parser details no longer depend on `enrichment_result` or `merge_notes`
  - Gmail orchestration still reuses `parse_email_with_details(...)`
  - Nvoids still keeps canonical title/location behavior through direct source-hint application
- Verification result:
  - `63 passed`
- Exact next phase:
  - `Phase 2: Redesign AI Extractor To Return Free Skills`

### Phase 2: Redesign AI Extractor To Return Free Skills

Refactor `backend/app/parsing/ai_extractor.py` so AI extraction is no longer taxonomy-gated at extraction time.

- Replace `skills_approved` / `skills_unknown` as the primary extraction interface with a freer result centered on:
  - `skills_text`
  - `role_candidates`
  - `primary_location`
  - `mentioned_locations`
  - `work_mode`
  - `salary_text`
  - `company`
  - `visa_hints`
  - `experience_years_min`
  - `f2f_mentioned`
  - `asks_contact_fields`
  - `is_texas_role`
  - `confidence`
  - `evidence`
  - `error`
- AI should return free skills text.
- Backend should only clean and dedupe formatting at extraction time.
- Taxonomy audit must happen after extraction, not during extraction.

Expected result:

- AI extractor can surface richer JD skills without collapsing them immediately into approved-only taxonomy values.

After completion, update this file with:

- payload shape changes
- tests run
- `Next Phase: Phase 3: Add Post-Extraction Skill Audit`

Status: Complete

- Changed subsystems:
  - `backend/app/parsing/ai_extractor.py`
  - `backend/tests/test_ai_extractor.py`
- Payload changes now live in the AI extractor:
  - added `skills_text` as the primary free-form skills output
  - added `salary_text`
  - added `f2f_mentioned`
  - added `asks_contact_fields`
  - added `is_texas_role`
  - preserved legacy compatibility buckets:
    - `skills_approved`
    - `skills_unknown`
- Behavior verified:
  - extractor now accepts mixed skill sources and returns one deduped free `skills_text`
  - malformed extractor responses still fail safely
  - live Gmail/Nvoids parser behavior has not changed yet, because Phase 2 only upgraded the isolated extractor contract
- Verification result:
  - `backend/tests/test_ai_extractor.py`
  - `backend/tests/test_phase0_routing.py`
  - `30 passed`
- Exact next phase:
  - `Phase 3: Add Post-Extraction Skill Audit`

### Phase 3: Add Post-Extraction Skill Audit

Introduce a separate audit layer, for example `backend/app/parsing/skill_audit.py`.

- Input:
  - free comma-separated `skills_text`
- Output:
  - `skills_text`
  - `known`
  - `unknown`
  - `evidence`
- Unknown skills must stay in final `skills_text`.
- Manual Upgrade should consume only `unknown`.
- Taxonomy should audit, not suppress the final parsed skills text.

Expected result:

- JD skills can remain expressive while still supporting controlled custom-skill review.

After completion, update this file with:

- audit payload details
- tests run
- `Next Phase: Phase 4: Add Structured skills_json Persistence`

Status: Complete

- Changed subsystems:
  - `backend/app/parsing/skill_audit.py`
  - `backend/app/parsing/__init__.py`
  - `backend/app/phase0.py`
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
- Audit behavior now live:
  - input: free comma-separated `skills_text`
  - output:
    - `skills_text`
    - `known`
    - `unknown`
    - `evidence`
  - unknown skills remain in final parser `skills_text`
  - compatibility fields still exposed:
    - `approved_skills_text`
    - `unknown_skills`
  - additive parser-details field added:
    - `skills_audit`
- Behavior verified:
  - audited `skills_text` preserves unknown skill terms instead of dropping them
  - parser contract returned by `parse_email()` is unchanged
  - AI-enabled parse paths can now merge free AI skills while preserving unknown skills for later review
  - pending-skill compatibility remains intact because `unknown_skills` still exists at the top level
- Verification result:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_ai_extractor.py`
  - `backend/tests/test_phase0_routing.py`
  - `32 passed`
- Exact next phase:
  - `Phase 4: Add Structured skills_json Persistence`

### Phase 4: Add Structured `skills_json` Persistence

Extend candidate persistence with a structured skills payload.

- Add `RecruiterEmail.skills_json`.
- Keep `RecruiterEmail.skills_text` unchanged for compatibility.
- Keep `parser_details_json` for diagnostics.
- `skills_json` should contain:
  - `skills_text`
  - `known`
  - `unknown`
  - `evidence`

Expected result:

- Candidate rows can store both the compatibility string field and the structured post-audit skill payload.

After completion, update this file with:

- schema/runtime patch notes
- API/schema notes
- `Next Phase: Phase 5: Rewrite parse_email_with_details(...) Into Clean Toggle Modes`

Status: Complete

- Changed subsystems:
  - `backend/app/models.py`
  - `backend/app/db.py`
  - `backend/app/main.py`
  - `backend/app/automation/run_orchestrator.py`
  - `backend/app/services/orchestration_service.py`
  - `backend/app/external_feeds/service.py`
  - `backend/app/parsing/skill_audit.py`
  - `backend/app/parsing/__init__.py`
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
- Schema/runtime patch notes:
  - added additive `RecruiterEmail.skills_json`
  - added SQLite runtime patch support for `recruiter_emails.skills_json`
- Persistence behavior now live:
  - `skills_text` remains unchanged for compatibility
  - `skills_json` stores:
    - `skills_text`
    - `known`
    - `unknown`
    - `evidence`
  - when parser details already contain `skills_audit`, persistence uses that payload directly
  - when `skills_audit` is absent, persistence falls back to auditing the final `skills_text`
- Wiring verified:
  - manual ingest persists `skills_json`
  - Gmail orchestrator persists and refreshes `skills_json`
  - Gmail sync/orchestration service persists `skills_json`
  - Nvoids queue creation persists `skills_json`
- API/schema notes:
  - no candidate API exposure was added in Phase 4
  - `parser_details_json` remains the diagnostics payload
  - `skills_json` is persistence-only in this phase
- Verification result:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
  - `71 passed`
- Exact next phase:
  - `Phase 5: Rewrite parse_email_with_details(...) Into Clean Toggle Modes`

### Phase 5: Rewrite `parse_email_with_details(...)` Into Clean Toggle Modes

Rewrite parser control flow in `phase0.py` to match the intended architecture.

- `feature_ai_extractor_enabled = false`
  - base parser only
- `feature_ai_extractor_enabled = true`
  - AI extractor only
- AI failure / malformed JSON / timeout
  - base parser emergency fallback
  - explicit parser warning in `parser_details_json`

The returned parse contract must stay unchanged:

- `role`
- `location`
- `job_location_text`
- `salary_text`
- `skills_text`
- `f2f_mentioned`
- `asks_contact_fields`
- `is_texas_role`

Expected result:

- Parser ownership becomes clean and predictable.

After completion, update this file with:

- final parser mode behavior
- warning/fallback behavior
- `Next Phase: Phase 6: Make Resume Matching Use Raw skills_text`

Status: Complete

- Changed subsystems:
  - `backend/app/phase0.py`
  - `backend/tests/test_phase0_routing.py`
- Final parser mode behavior now live:
  - `feature_ai_extractor_enabled = false`
    - base parser only
    - parser details:
      - `parser_mode = "base_only"`
      - `parser_version = "base_only_v2"`
  - `feature_ai_extractor_enabled = true` with successful extraction
    - AI extractor owns the final parsed result
    - parser details:
      - `parser_mode = "ai_primary"`
      - `parser_version = "ai_primary_v2"`
  - AI extractor failure
    - base parser fallback is used
    - parser details:
      - `parser_mode = "ai_fallback"`
      - `parser_version = "ai_fallback_v2"`
      - `parser_warning` explains the fallback
      - `fallback_used = true`
- Warning/fallback behavior verified:
  - AI failure no longer depends on merge heuristics
  - fallback is explicit in parser details
  - compatibility fields are still present for the current UI and pending-skill flow
- Source-hint behavior verified:
  - Nvoids canonical title/location overlays still apply after the mode rewrite
- Verification result:
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
  - `71 passed`
- Exact next phase:
  - `Phase 6: Make Resume Matching Use Raw skills_text`

### Phase 6: Make Resume Matching Use Raw `skills_text`

Update scoring so JD-vs-resume comparison uses the final comma-separated `skills_text` directly.

- Compare parsed JD `skills_text` against `ResumeAsset.skills_text`.
- Do not require all JD skills to exist in taxonomy before they can affect matching.
- Keep taxonomy-based intent scoring as a secondary signal, not the only gate.
- If a resume has no saved `skills_text`, score it as weak or missing instead of falling back to file extraction for matching.

Expected result:

- Matching can benefit from free-form AI-extracted JD skills without losing deterministic scoring support.

After completion, update this file with:

- scoring behavior notes
- validation scenarios
- `Next Phase: Phase 7: Narrow Manual Upgrade To Unknown Skills Only`

Status: Complete

- Changed subsystems:
  - `backend/app/services/scoring_runtime_service.py`
  - `backend/tests/test_scoring_runtime_service.py`
- Scoring behavior now live:
  - raw JD `skills_text` token overlap with `ResumeAsset.skills_text` is now the primary resume-side match input
  - taxonomy intent scoring still contributes, but as a secondary signal
  - non-taxonomy terms can now still influence resume selection through raw overlap
- Matching behavior verified:
  - AI-heavy JD still prefers the hands-on AI resume
  - generic Java full-stack JD still prefers the foundation-heavy Java resume
  - direct unknown-skill matching like `Temporal Workflow` now works through raw overlap instead of being gated by taxonomy-only scoring
  - resumes without saved `skills_text` are scored weak and no longer gain hidden matching strength from file extraction
- Semantic behavior note:
  - resume-side semantic text now skips file extraction in the matching path when saved skills are missing
  - the standalone embedding refresh path is unchanged and can still use the existing resume text helper defaults outside matching
- Validation scenarios covered:
  - raw unknown-skill overlap wins over plain generic overlap
  - missing resume skills stay weak
  - no regressions in orchestrator or Nvoids queue tests
- Verification result:
  - `backend/tests/test_scoring_runtime_service.py`
  - `backend/tests/test_skill_audit.py`
  - `backend/tests/test_phase0_routing.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_schemas.py`
  - `83 passed`
- Exact next phase:
  - `Phase 7: Narrow Manual Upgrade To Unknown Skills Only`

### Phase 7: Narrow Manual Upgrade To Unknown Skills Only

Update the pending-skill flow to prefer the structured audit output.

- Read `skills_json.unknown` first.
- Fall back to top-level `unknown_skills` for backward compatibility.
- Do not treat known taxonomy skills as pending.
- Do not auto-approve skills merely because AI returned them.

Expected result:

- The Upgrade Skills workflow becomes narrower and more trustworthy.

After completion, update this file with:

- API behavior notes
- compatibility notes
- `Next Phase: Phase 8: Simplify Candidate Parser Details UI`

Status: Complete

- Changed subsystems:
  - `backend/app/main.py`
  - `backend/tests/test_external_feeds_api.py`
- Verified behavior:
  - pending skill aggregation now reads structured `skills_json["unknown"]` first
  - older candidates without `skills_json` still surface through `parser_details_json["unknown_skills"]`
  - approved and dismissed custom skills remain suppressed from pending results
- Verification result:
  - `backend/tests/test_external_feeds_api.py`
  - `backend/tests/test_run_orchestrator.py`
  - `backend/tests/test_schemas.py`
  - `44 passed`
- Exact next phase:
  - `Phase 8: Simplify Candidate Parser Details UI`

### Phase 8: Simplify Candidate Parser Details UI

Update the Needs Review parser-details UI to match the new parser contract.

The details view should show:

- parser mode
- final extracted result
- AI extractor result
- base fallback result when relevant
- `skills_json`
- known skills
- unknown skills
- parser warning
- source hints
- AI evidence

It should explicitly stop depending on:

- `enrichment_result`
- `merge_notes`
- `ai_merge_notes`
- winning-source logic from a 3-way merge

Expected result:

- Candidate details explain the real parser path instead of the older merged-parser model.

After completion, update this file with:

- UI verification notes
- test results
- `Next Phase: Phase 9: Full Regression Validation`

Status: Complete

- Changed subsystems:
  - `dashboard/src/App.tsx`
  - `dashboard/src/App.parserDetails.test.tsx`
- Verified behavior:
  - parser-details panel now shows parser mode and fallback status directly
  - unknown and approved skills render from the current `skills_audit` / compatibility payload
  - legacy merge-era blocks are no longer rendered
  - existing `View Details` / `Hide Details` interaction remains intact
- Verification result:
  - `npm test -- --run src/App.parserDetails.test.tsx`
  - `1 passed`
- Exact next phase:
  - `Phase 9: Full Regression Validation`

### Phase 9: Full Regression Validation

Re-run Gmail and Nvoids flows against the refactored parser contract.

Priority validations:

- AI extractor OFF = base parser only
- AI extractor ON = AI parser only
- AI failure = base fallback with warning
- unknown skills survive into final `skills_text`
- Manual Upgrade shows only unknown skills
- resume matching uses raw JD/resume skill strings
- Nvoids source hints still influence AI extraction when enabled

Expected result:

- Refactor is complete without breaking queueing, review, or matching flows.

After completion, update this file with:

- final pass/fail summary
- residual follow-up items
- whether this plan is fully complete

Status: Complete

- Final validation summary:
  - AI extractor OFF still uses base parser only
  - AI extractor ON still uses AI extractor primary mode
  - AI failure still falls back cleanly with parser warning and fallback flag
  - unknown skills survive in final `skills_text` and structured audit payloads
  - manual upgrade continues to surface only unknown skills
  - resume matching continues to use raw JD/resume skill strings as the primary overlap signal
  - Nvoids source hints remain active in parser execution
- Verification result:
  - `uv run pytest backend/tests/test_scoring_runtime_service.py backend/tests/test_skill_audit.py backend/tests/test_phase0_routing.py backend/tests/test_run_orchestrator.py backend/tests/test_external_feeds_api.py backend/tests/test_schemas.py`
  - `85 passed`
  - `npm test -- --run src/App.parserDetails.test.tsx`
  - `1 passed`
- Plan completion:
  - This `docs/temp2.md` rollout is fully complete.
- Residual follow-up items:
  - `docs/temp2.md` still contains some older prose near the top that references the pre-refactor branch reality; the implemented code and phase handoff notes are current, but the introductory historical description could be cleaned up later if you want a tighter final design doc.

---

## Public Interfaces / Types To Record

- Additive DB field:
  - `RecruiterEmail.skills_json`
- No change to `parse_email()` output shape.
- `parser_details_json` shape will change:
  - remove enrichment-centric fields
  - add mode/fallback/skills-audit fields
- Candidate API may later expose additive `skills_json`, but existing `skills_text` must remain intact.

---

## Test Plan

### Parser unit tests

- AI extractor OFF uses base parser only.
- AI extractor ON uses AI extractor only.
- AI failure falls back cleanly.
- Unknown extracted skills remain in final `skills_text`.

### Nvoids flow tests

- Source hints still reach parser.
- Canonical title remains preserved.
- Row-level fallback behavior still works.

### Resume matching tests

- Raw JD skills compare directly with `ResumeAsset.skills_text`.
- Unknown JD skills are not dropped before matching.
- Resumes without saved skills are scored weak, not file-extracted.

### UI tests

- Needs Review parser details no longer expects enrichment fields.
- Unknown/known skills display stays contained.

---

## Assumptions And Defaults

- The current branch is already far enough along that this document should be a refactor plan, not an initial feature-build plan.
- The safest architecture is:
  - AI extractor OFF = base parser only
  - AI extractor ON = AI extractor only
  - AI failure = base fallback
- Taxonomy should become a post-extraction audit layer, not the hard gate for final JD `skills_text`.
- Resume matching should trust saved resume skills as the authoritative resume-side comparison input.
- `docs/temp.md` is only supporting context; this file should stand alone as the actionable handoff plan.
