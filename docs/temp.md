# Parser Refactor Plan Aligned To Current `semantic-embeddings`

## Current Status

- `docs/temp2.md` has been realigned to the current branch instead of the older greenfield assumptions.
- The active parser hub is `backend/app/phase0.py::parse_email_with_details(...)`.
- The current live parser flow is:
  - base parser
  - spaCy enrichment
  - optional AI extractor
  - merged final result
- `feature_ai_extractor_enabled` already exists and is already wired through Gmail, Nvoids, orchestrator, and manual parser entry points.
- Nvoids already has:
  - canonical title extraction
  - `source_hints`
  - empty-detail guards
  - row-level sync resilience
- `parser_details_json` already exists and is already exposed in candidate review UI.

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
  - `base_parser_result`
  - `enrichment_result`
  - `ai_extractor_result`
  - `approved_skills_text`
  - `unknown_skills`
  - `merged_result`
  - `merge_notes`
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

- Planning only. No code changes from this document yet.

## Next Phase

- Phase 0: Freeze Current Branch Behavior

## Last Verification

- Verified against current branch code paths in:
  - `backend/app/phase0.py`
  - `backend/app/external_feeds/parser.py`
  - `backend/app/external_feeds/service.py`
  - `backend/app/automation/queue_preparation.py`
- Confirmed that the older `temp2.md` assumptions were out of sync with the branch and needed a branch-reality rewrite.

## Open Risks / Notes

- Current mismatch is architectural, not missing infrastructure:
  - AI is additive, not authoritative
  - taxonomy approval still narrows AI-extracted skill output
  - final matching still depends too much on normalized/taxonomy-approved skill paths
- This plan is a refactor-on-top-of-existing-parser-stack, not an initial feature build.

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
