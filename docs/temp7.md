# Parser Enrichment Rollout Status

## Current Status

Completed the safe additive parser-enrichment rollout for the current `semantic-embeddings` branch.

The current state is:

- `parse_email(subject, body)` still returns the same downstream contract
- enrichment was added as a parallel helper, not a parser replacement
- parser details are now persisted additively on `RecruiterEmail`
- candidate APIs now expose parser details additively
- Needs Review now has a read-only `View Details` expander backed only by backend data

No downstream queue, orchestration, routing, or resume-selection call signatures were changed.

## Completed Phases

### Phase 0: Contract Freeze And Baseline

Status: complete

What changed:

- confirmed and preserved the existing `parse_email()` contract in [backend/app/phase0.py](/D:/My%20Websites/CodeJob/backend/app/phase0.py)
- kept downstream callers aligned to the same parsed keys:
  - `role`
  - `location`
  - `job_location_text`
  - `salary_text`
  - `skills_text`
  - `f2f_mentioned`
  - `asks_contact_fields`
  - `is_texas_role`

Behavior change:

- none

Verification:

- covered later by targeted parser and orchestration regression tests

### Phase 1: Add Enrichment Module

Status: complete

Files / subsystems changed:

- [backend/app/parsing/__init__.py](/D:/My%20Websites/CodeJob/backend/app/parsing/__init__.py)
- [backend/app/parsing/spacy_enrichment.py](/D:/My%20Websites/CodeJob/backend/app/parsing/spacy_enrichment.py)
- [backend/pyproject.toml](/D:/My%20Websites/CodeJob/backend/pyproject.toml)

What changed:

- added a new additive parser-enrichment module
- used a low-risk runtime shape:
  - `spacy.blank("en")`
  - `EntityRuler`
  - `PhraseMatcher`
  - `Matcher`
- made the module runtime-safe if `spacy` is unavailable
- enrichment result includes:
  - `role_candidates`
  - `company`
  - `primary_location`
  - `mentioned_locations`
  - `work_mode`
  - `visa_hints`
  - `experience_years_min`
  - `skills_text`
  - `confidence`
  - `evidence`

Behavior change:

- none yet for live callers until merge/wiring phases

Verification:

- backend targeted tests passed after full rollout

### Phase 2: Merge Logic

Status: complete

Files / subsystems changed:

- [backend/app/phase0.py](/D:/My%20Websites/CodeJob/backend/app/phase0.py)

What changed:

- split deterministic parsing into `_base_parse_email(...)`
- added `parse_email_with_details(...)`
- merged:
  - base parser result
  - enrichment result
  - taxonomy-normalized skills
- base parser remains the source of truth
- enrichment only improves the result when it is cleaner or source-locked

Behavior change:

- `skills_text` can now be widened safely by combining deterministic and enrichment skills
- role/location remain conservative and only change when the base result is weak or the source is explicitly locked

Verification:

- [backend/tests/test_phase0_routing.py](/D:/My%20Websites/CodeJob/backend/tests/test_phase0_routing.py) now checks:
  - contract stability
  - parser-details payload presence
  - merged parser behavior

### Phase 3: Gmail Parser Wiring

Status: complete

Files / subsystems changed:

- [backend/app/phase0.py](/D:/My%20Websites/CodeJob/backend/app/phase0.py)
- [backend/app/services/orchestration_service.py](/D:/My%20Websites/CodeJob/backend/app/services/orchestration_service.py)
- [backend/app/automation/run_orchestrator.py](/D:/My%20Websites/CodeJob/backend/app/automation/run_orchestrator.py)
- [backend/app/main.py](/D:/My%20Websites/CodeJob/backend/app/main.py)

What changed:

- Gmail-style parsing now uses `parse_email_with_details(...)`
- parser details are serialized when Gmail candidates are created through:
  - orchestration service sync path
  - run orchestrator path
  - manual ingest path

Behavior change:

- Gmail candidates now keep additive parser debug metadata
- live parsed contract remains unchanged for downstream queue logic

Verification:

- targeted backend regressions passed:
  - `tests/test_phase0_routing.py`
  - `tests/test_run_orchestrator.py`
  - `tests/test_approve_cc_regression.py`

### Phase 4: Nvoids Parsing Wiring

Status: complete

Files / subsystems changed:

- [backend/app/external_feeds/service.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/service.py)
- [backend/app/external_feeds/parser.py](/D:/My%20Websites/CodeJob/backend/app/external_feeds/parser.py)

What changed:

- Nvoids queue path now uses `parse_email_with_details(...)` in a source-aware way
- source hints now flow into enrichment for Nvoids:
  - canonical title
  - canonical location
  - company
  - work mode
  - visa hints
- queue prep now receives a full parsed override for Nvoids instead of only `role`
- Nvoids parser skill extraction now uses taxonomy extraction instead of the tiny hardcoded external-feed skill list

Behavior change:

- improved Nvoids-side `skills_text`
- parser details now persist for Nvoids-created `RecruiterEmail` candidates
- canonical Nvoids title locking remains intact

Verification:

- targeted backend regressions passed:
  - `tests/test_external_feeds_api.py`

### Phase 5: Parser Details Persistence

Status: complete

Files / subsystems changed:

- [backend/app/models.py](/D:/My%20Websites/CodeJob/backend/app/models.py)
- [backend/app/db.py](/D:/My%20Websites/CodeJob/backend/app/db.py)

What changed:

- added `RecruiterEmail.parser_details_json`
- added SQLite runtime patching for the new column

Behavior change:

- queue/candidate creation can now store parser-details metadata

Verification:

- targeted regressions passed
- serialization failures are still expected to remain non-fatal by design

### Phase 6: Candidate API Exposure

Status: complete

Files / subsystems changed:

- [backend/app/schemas.py](/D:/My%20Websites/CodeJob/backend/app/schemas.py)
- [backend/app/main.py](/D:/My%20Websites/CodeJob/backend/app/main.py)
- [backend/tests/test_schemas.py](/D:/My%20Websites/CodeJob/backend/tests/test_schemas.py)

What changed:

- `EmailResponse` now has additive optional `parser_details`
- schema accepts both:
  - `parser_details`
  - `parser_details_json`
- `/candidates` and candidate review serialization now expose parser details when present

Behavior change:

- API clients can inspect parser details without any contract break for callers that ignore the field

Verification:

- `tests/test_schemas.py` passed after fixing the `parser_details_json` hydration alias

### Phase 7: Needs Review View Details UI

Status: complete

Files / subsystems changed:

- [dashboard/src/App.tsx](/D:/My%20Websites/CodeJob/dashboard/src/App.tsx)
- [dashboard/src/App.css](/D:/My%20Websites/CodeJob/dashboard/src/App.css)
- [dashboard/src/App.parserDetails.test.tsx](/D:/My%20Websites/CodeJob/dashboard/src/App.parserDetails.test.tsx)

What changed:

- added a read-only `ParserDetailsPanel`
- added local expand/collapse state in Needs Review
- panel shows:
  - parser version
  - source
  - final extracted result
  - base parser result
  - enrichment result
  - merge notes
  - source hints
- frontend does not re-run parsing and only renders backend-provided details

Behavior change:

- Needs Review cards now support `View Details` / `Hide Details`

Verification:

- targeted frontend tests passed:
  - `App.parserDetails.test.tsx`
  - `App.resumeDatabase.test.tsx`
  - `App.sourceListingUrl.test.ts`
  - `App.draftTextSize.test.ts`

### Phase 8: Full Regression And Validation

Status: complete

What changed:

- completed targeted backend and frontend regression validation for this rollout

Backend verification:

- command run:
  - `uv run pytest tests/test_phase0_routing.py tests/test_schemas.py tests/test_external_feeds_api.py tests/test_run_orchestrator.py tests/test_approve_cc_regression.py`
- result:
  - `63 passed`

Frontend verification:

- command run:
  - `npm test -- App.parserDetails.test.tsx App.resumeDatabase.test.tsx App.sourceListingUrl.test.ts App.draftTextSize.test.ts`
- result:
  - `12 passed`

Behavior verified:

- parser contract remained stable
- parser details persist and serialize
- Gmail and Nvoids paths both use additive enrichment safely
- Needs Review can render parser details without re-parsing in the browser

## Current Phase

Completed and verified.

## Next Phase

No required implementation phase remains for the `temp7.md` rollout.

If a follow-up turn is needed, start from:

- optional manual UI validation and polish, or
- broader backend regression pass if the branch is about to be merged or tagged

## Last Verification

Backend:

- `63 passed` in targeted regression suite

Frontend:

- `12 passed` in targeted parser-details and existing dashboard regression tests

Notes:

- an earlier dashboard build attempt hit a local Windows permission issue writing `.tsbuildinfo`; that was environmental and was not reclassified as a code failure in this rollout

## Open Risks / Notes

- `spacy` was added as a dependency, but the enrichment module is still designed to fail safe if the dependency is unavailable at runtime
- parser-details payload shape is intentionally additive and may evolve in future turns
- manual browser verification of the new Needs Review details panel is still useful before a release/tag, even though the automated frontend tests passed
- broader full-backend pytest was not required for this phase because the targeted regressions covering parser, candidate serialization, Nvoids API, run orchestrator, and approve/send all passed
