# Section-Aware JD Extraction And Resume Scoring Build Plan

## Current Status

- Completed Phases:
  - `Phase 1: Footer And Recruiter Noise Suppression` completed on `2026-06-13`
  - `Phase 2: Section Slicing And Bucket Classification` completed on `2026-06-13`
  - `Phase 3: JD-Specific Skill Evidence Extraction` completed on `2026-06-13`
  - `Phase 6: Wire Section-Aware Extraction Into parse_email()` completed on `2026-06-13`
  - `Phase 4: Role-Family Consistency Filter` completed on `2026-06-13`
  - `Phase 5: Noise Guards And Alias Collision Protection` completed on `2026-06-13`
- Current Phase:
  - `Phase 5` completed and verified
- Next Phase:
  - `Phase 7: Regression And Real-JD Validation`
- Last Verification:
  - Focused parser tests: `backend/tests/test_skill_taxonomy.py` and `backend/tests/test_phase0_routing.py` passed (`44 passed`, `9 subtests passed`)
  - Core regressions: `backend/tests/test_scoring_runtime_service.py`, `backend/tests/test_external_feeds_api.py`, `backend/tests/test_run_orchestrator.py`, and `backend/tests/test_approve_cc_regression.py` passed (`45 passed`)
  - AI Engineer JD sandbox validation:
    - role remained `AI Engineer`
    - location remained `Alpharetta, GA`
    - final `skills_text` still included richer JD signals such as `Agentic Workflows`, `RAG`, `Prompt Engineering`, `Tool Calling`, `Secure SDLC`, `Embeddings`, and `Observability`
    - alias-collision noise like `Angular Services` and `SAFe` no longer appeared in the structured AI JD output
- Open Risks / Notes:
  - Phase 6 still provides the section-aware default parser path for structured JDs, with legacy cleaned-body fallback for headingless or weakly structured JDs
  - Phase 5 hardens risky alias matching, but Phase 7 still needs broader regression and real-JD validation across multiple JD shapes
  - Domain headings are intentionally explicit-only to avoid treating values like `Payments` as headings
  - The next Codex implementation turn should start from `Phase 7`

## Goal

Implement production-safe, section-aware JD skill extraction and then a follow-up AI-intent resume scoring upgrade on top of the current backend without breaking the existing queue, settings, resume pinning, or send flows.

The current backend already has:

- parser entrypoint in `backend/app/phase0.py`
- taxonomy normalization in `backend/app/skill_taxonomy.py`
- intent-weighted resume selection in `backend/app/services/scoring_runtime_service.py`
- stable downstream dependence on `skills_text`

So the safest strategy is:

1. improve how JD text is prepared
2. improve how JD skills are extracted and filtered
3. keep the persisted/output contract unchanged
4. keep scoring, queueing, and sending behavior compatible during extraction phases
5. add a separate scoring phase only after parser/extraction quality is stable

---

## Non-Breakage Rules

- Do not change the public `parse_email()` response shape in this build.
- Do not change the persisted `skills_text` contract.
- Do not change `/settings/resume`, `/settings/resumes`, queue state transitions, `resume_asset_id`, or approval/send flows.
- Prefer additive internal helpers and dataclasses over invasive rewrites.
- Each phase must be testable on its own before moving to the next one.
- Treat Phases 1-7 as parser/extraction infrastructure.
- Treat Phase 8 as the scoring/ranking upgrade.
- Do not merge parser cleanup and scoring redesign into one implementation step.

---

## Current Architecture Baseline

### Current parser path

- `parse_email()` in `backend/app/phase0.py` extracts role, location, salary, and `skills_text`.
- `_extract_skills()` currently delegates directly to taxonomy-driven extraction.
- The parser is still too flat for recruiter-email JDs and can include footer noise or weak alias collisions.

### Current taxonomy path

- `backend/app/skill_taxonomy.py` already supports canonical normalization and intent-weighted helper logic.
- This is the correct place for JD-specific evidence aggregation and noise guards.
- The runtime artifact is `backend/app/data/skill_taxonomy.json`.

### Current scoring path

- `backend/app/services/scoring_runtime_service.py` already consumes `skills_text` and applies semantic plus intent-weighted resume scoring.
- This means upstream cleanup should improve matching without changing queue orchestration contracts.

---

## Phase 1: Footer And Recruiter Noise Suppression

### Objective

Remove obvious recruiter/signature/footer text before section splitting or skill extraction.

### Why first

This gives the biggest reduction in noise with the lowest risk. It does not change APIs or scoring formulas.

### Additions

- Add a conservative footer-trimming helper in `backend/app/phase0.py`, for example:
  - `strip_forward_headers(...)`
  - `strip_recruiter_footer(...)`
- Use multi-signal footer detection, not single-word triggers.
- Treat footer markers as strong only when paired with patterns like:
  - recruiter titles
  - phone/email/address lines
  - sign-off phrases
  - unsubscribe/contact boilerplate

### Keep safe

- Do not cut on the first occurrence of `recruiter`, `email`, or `contact` alone.
- If footer detection is uncertain, preserve text instead of over-trimming.

### Verification

- Add tests showing recruiter footer lines do not contribute to extracted JD skills.
- Add tests showing real JD lines are not accidentally removed.

---

## Phase 2: Section Slicing And Bucket Classification

### Objective

Convert the cleaned JD body into weighted sections before skill extraction.

### Additions

- Add internal dataclasses in `backend/app/phase0.py`, for example:
  - `JDSection`
  - optional `ParsedJobMetadata` if helpful
- Add a section-heading alias dictionary grouped into buckets:
  - `required`
  - `mandatory`
  - `technical_skills`
  - `essential`
  - `responsibilities`
  - `summary`
  - `preferred`
  - `domain`
  - `ai_compliance`
  - `hard_filter`
  - `footer`
  - `unknown`
- Add `SECTION_WEIGHTS` in code, not only docs.
- Add helpers such as:
  - `classify_section_heading(...)`
  - `slice_jd_sections(...)`
  - `build_skill_source_sections(...)`

### Detection strategy

- Layer 1: heading-based split
- Layer 2: requirement-line heuristics when headings are missing
- Layer 3: cleaned-body fallback only if section detection is weak

### Keep safe

- Headings must be validated against the alias dictionary before being treated as real section breaks.
- `hard_filter` and `footer` sections must not feed skill extraction.
- `unknown` sections may still be used, but at low weight.

### Verification

- Tests for common headings seen in this project:
  - `Role Summary`
  - `Required Qualifications`
  - `Preferred Qualifications`
  - `Technical Skills`
  - `Domain Skill`
  - `What Success Looks Like`
  - `Compliance & Responsible AI Expectations`
- Tests for inline hard-filter fields:
  - `Location`
  - `Visa`
  - `Duration`
  - `Rate`

---

## Phase 3: JD-Specific Skill Evidence Extraction

### Objective

Extract skills from useful JD sections only, with section-aware evidence and weights, while still returning compatibility-safe `skills_text`.

### Additions

- Keep generic taxonomy helpers intact.
- Add JD-specific helpers in `backend/app/skill_taxonomy.py`, for example:
  - `extract_jd_skill_evidence(...)`
  - `aggregate_jd_skill_evidence(...)`
  - `extract_jd_skills_text(...)`
- Add internal evidence models, for example:
  - `SkillEvidence`
  - `AggregatedSkill`

### Behavior

- Extract candidate phrases first, then resolve through taxonomy.
- Prefer evidence from:
  - `required`
  - `mandatory`
  - `technical_skills`
  - `essential`
  - `domain`
- Use medium influence for:
  - `responsibilities`
  - `summary`
  - `ai_compliance`
- Use lower influence for:
  - `preferred`
  - `additional_notes`
- Ignore:
  - `hard_filter`
  - `footer`

### Output contract

- `parse_email()` still returns `skills_text` as one normalized comma-separated string.
- Internal evidence remains in memory only for this phase.

### Keep safe

- Do not persist structured skill evidence yet.
- Do not require database changes.
- If section-aware extraction fails, fall back to current cleaned-body extraction rather than returning empty output unexpectedly.

### Verification

- AI JD should include:
  - `Agentic Workflows`
  - `Tool Calling`
  - `Human-in-the-Loop`
  - `RAG`
  - `Prompt Engineering`
  - `AI Evaluations`
  - `Embeddings`
  - `Observability`
  - `Responsible AI`
  - `Python`
  - `Java`
  - `TypeScript`
  - `REST APIs`
  - `Secure SDLC`
- AI JD should exclude:
  - recruiter footer content
  - `Technical Recruiter`
  - `Email`
  - `Address`
  - weak collision noise such as `SAFe` unless strongly supported

---

## Phase 4: Role-Family Consistency Filter

### Objective

Downweight or suppress off-family noisy skills after extraction, without over-pruning legitimate cross-stack requirements.

### Additions

- Reuse and extend the existing role-family logic in `backend/app/skill_taxonomy.py`.
- Add preferred and allowed cluster maps for role families such as:
  - `ai`
  - `java_fullstack`
  - `java_backend`
  - `frontend`
  - `devops_cloud`
  - `data`

### Behavior

- If a skill is off-family, keep it only when:
  - it comes from a strong section
  - it appears repeatedly
  - or it has strong match confidence
- Do not delete all off-family skills automatically; just require stronger evidence.

### Keep safe

- Strong required-section evidence must win over family heuristics.
- This filter should be additive after extraction, not a replacement for extraction.

### Verification

- AI JD should keep Java, Python, TypeScript, REST APIs.
- AI JD should suppress low-confidence off-family skills that appear once in weak contexts.
- Java full-stack JD should still keep backend, frontend, database, devops, and domain skills together.

---

## Phase 5: Noise Guards And Alias Collision Protection

### Objective

Prevent weak alias matches and broad-token collisions from polluting `skills_text`.

### Additions

- Add suppression rules in `backend/app/skill_taxonomy.py` for:
  - weak singletons
  - broad aliases
  - low-confidence matches from weak sections
  - footer-derived or hard-filter-derived tokens
- Support additive taxonomy metadata when useful:
  - `dangerous_aliases`
  - `min_context_required`
  - `jd_only`
  - `resume_only`

### Behavior

- Broad tokens like `safe`, `services`, or similarly ambiguous aliases must not match aggressively without context.
- Noisy skills should require:
  - stronger section weights
  - repeated evidence
  - or exact canonical/alias phrase evidence

### Keep safe

- Additive JSON metadata only; do not break existing taxonomy loading.
- Fallback taxonomy loading must still work if new metadata fields are absent.

### Verification

- Known noisy collisions must be suppressed in the AI JD case.
- Exact strong phrases should still map correctly through taxonomy.

---

## Phase 6: Wire Section-Aware Extraction Into `parse_email()`

### Objective

Make section-aware extraction the default parser path while preserving the existing output shape.

### Final `parse_email()` behavior

- extract metadata fields first
- clean forwarded/header/footer noise
- slice and classify sections
- build skill-source sections
- call JD-specific extraction
- emit:
  - `role`
  - `location`
  - `job_location_text`
  - `salary_text`
  - `skills_text`
  - existing hard-filter related booleans/flags

### Keep safe

- Keep field names unchanged.
- If no useful sections are detected, fall back to the current cleaned-body extraction path instead of breaking queue preparation.

### Verification

- Existing callers in:
  - `backend/app/main.py`
  - `backend/app/automation/queue_preparation.py`
  - `backend/app/automation/run_orchestrator.py`
  - `backend/app/services/orchestration_service.py`
  - `backend/app/external_feeds/service.py`
  continue to work without changes to their call contract.

---

## Phase 7: Regression And Real-JD Validation

### Targeted parser tests

- AI Engineer JD from this thread
- Java FSD JD with technical/domain sections
- Java backend/full-stack JD with mixed headings
- JD with noisy recruiter footer
- JD with weak heading structure and line-heuristic fallback

### Scoring validation

- Re-run the real AI Engineer scenario after each meaningful upstream phase.
- Confirm:
  - cleaner `skills_text`
  - stable or improved resume selection
  - fewer noisy extracted skills

### Existing regression suites to keep green

- `backend/tests/test_skill_taxonomy.py`
- `backend/tests/test_scoring_runtime_service.py`
- `backend/tests/test_external_feeds_api.py`
- `backend/tests/test_run_orchestrator.py`
- `backend/tests/test_approve_cc_regression.py`

---

## Suggested Execution Order For Codex

### Build order

1. Phase 1: footer suppression
2. Phase 2: section slicing and buckets
3. Phase 3: JD-specific skill evidence extraction
4. Phase 6: wire new extraction into `parse_email()`
5. Phase 4: role-family consistency filter
6. Phase 5: noise guards and alias collision protection
7. Phase 7: regression plus real-JD validation

### Why this order

- Early phases improve signal without touching downstream contracts.
- Wiring happens before advanced filtering so the new parser path can be observed early.
- Role-family filtering and noise guards come later because they are the most likely to over-prune if added too early.

---

## Acceptance Criteria

- `skills_text` is cleaner and better ordered for real recruiter JDs.
- Hard-filter fields do not leak into semantic skills.
- Recruiter footer/signature text does not leak into semantic skills.
- AI-heavy JDs produce AI-heavy extracted skills.
- Java full-stack JDs produce stable backend/frontend/devops/database/domain skills.
- Existing queue, candidate pinning, and send flows keep working unchanged.
- No API or DB contract changes are required for this build.

---

## Phase 8: AI-Intent Resume Scoring Upgrade

### Objective

Fix the second half of the original problem: distinguishing hands-on AI implementation resumes from generic engineering resumes with light AI exposure.

### Why this is separate

Phases 1-7 improve JD extraction quality. They make `skills_text` cleaner and reduce parser noise, but they do not fully guarantee that the scorer will always prefer the stronger AI implementation resume when two resumes still share heavy generic overlap.

### Scope

- Update resume scoring only after section-aware JD extraction is stable.
- Reuse the existing queue, `resume_asset_id`, and approval/send flow.
- Keep existing route and DB contracts unchanged.

### Additions

- Extend `backend/app/services/scoring_runtime_service.py` scoring logic to separate:
  - `specialization_score`
  - `foundation_score`
  - `role_alignment_score`
- Add stronger AI-role weighting when JD role family is:
  - `ai`
  - `genai`
  - `llm`
  - `machine_learning`
- Add resume-side wording heuristics:
  - boost hands-on phrases like `implemented`, `built`, `designed`, `created`, `production`
  - penalize weak phrases like `exposure`, `concepts`, `assisted`, `familiarity`
- Generate higher-signal semantic text for resume scoring and embeddings from:
  - role-defining AI skills
  - top foundation skills
  - core matched clusters
- Improve match explanations so they show:
  - why Resume A beat Resume B
  - matched AI-core clusters
  - missing role-defining signals
  - exposure penalties or hands-on boosts when they applied

### Keep safe

- Do not redesign queue preparation or orchestration interfaces.
- Do not change `select_best_resume_match()` or `compute_blended_ai_score()` signatures unless absolutely necessary.
- Keep semantic-disabled mode compatibility-safe.

### Verification

- Re-run the known AI Engineer JD case from this thread and assert:
  - Resume 1 wins over Resume 2
- Add a generic Java full-stack regression case and assert:
  - AI-specialist resumes do not overpower strong full-stack resumes for non-AI JDs
- Keep existing run orchestrator and approve/send regressions green

---

## Out Of Scope For Phases 1-7

- database schema changes
- dashboard/API response shape changes
- replacing `skills_text` with persisted structured skill JSON
- redesigning queue states or approval flow
- full scorer redesign beyond cleaner upstream JD inputs

---

## Out Of Scope For Phase 8

- new database columns unless scoring absolutely cannot remain runtime-only
- changing resume upload/edit API shapes
- per-candidate manual resume override UI changes
- changing the send/approval contract beyond choosing a better pinned resume

---

## Practical Note

If a phase introduces too much regression risk, stop at the previous green phase and validate with the real JD test case before continuing. The safest success path in this repo is incremental parser improvement, not a one-shot parser rewrite.

---

## Recommended Delivery Framing

### Phases 1-7

Label these as:

`Phase 1: Section-aware JD extraction and cleaner skills_text`

Expected outcome:

- cleaner parser output
- fewer noisy skills
- better metadata separation
- improved matching inputs

Do not claim these phases alone fully solve resume ranking.

### Phase 8

Label this as:

`Phase 2: AI-intent-aware resume scoring`

Expected outcome:

- stronger preference for hands-on AI implementation resumes over AI-exposure resumes
- more stable best-resume selection for AI-heavy JDs
