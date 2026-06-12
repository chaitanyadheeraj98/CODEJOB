# ATS Resume Matching, Resume Database & Skipped Drafts — Implementation Plan

## Feasibility Assessment

**Verdict: Fully feasible.** The current codebase architecture supports this enhancement with no fundamental architectural conflicts. The existing models, services, and UI structure provide natural extension points for every proposed feature.

---

## Current Architecture Summary

| Layer | Key Component | Current Responsibility |
|-------|--------------|----------------------|
| Models | `RecruiterEmail` | Stores candidates with `state`, `score`, `ai_score`, `resume_asset_id`, `resume_file_name` |
| Models | `ResumeAsset` | File metadata + `is_current` flag + `semantic_embedding` |
| Models | `UserSettings` | `qualification_threshold`, feature flags, `must_have_skills` |
| Scoring | `phase0.ai_assist_score()` | Keyword scoring (base 0.45 + role/skill hits) |
| Scoring | `ScoringRuntimeService` | Blended keyword + semantic similarity |
| Automation | `RunOrchestrator.execute()` | Per-candidate pipeline: parse → filter → score → route → draft |
| Orchestration | `OrchestrationService` | Gmail sync, run_once, approve_send, reject |
| External Feeds | `ExternalFeedService` | nVoids scraping + opportunity storage |
| Frontend | `candidateBuckets.ts` | 3 states: `needs_review`, `failed`, `approved_sent` |
| Frontend | `Sidebar.tsx` | 6 navigation items |
| Frontend | `App.tsx` | Tab-based rendering, candidate cards, resume upload |

---

## Why This Enhancement Is Feasible

### 1. Model Extensibility
- `ResumeAsset` already has `semantic_embedding` — adding `skills_text`, `normalized_skills_json`, `evidence_profile_json` is a natural extension.
- `RecruiterEmail` already has `resume_asset_id` and `resume_file_name` — the candidate-pinned resume concept already exists structurally.
- Adding a new `ResumeProfile` model or extending `ResumeAsset` both work with the existing Alembic migration system.

### 2. Scoring Pipeline Is Modular
- `ScoringRuntimeService` and `phase0` are separate modules injected into `RunOrchestrator`.
- The blended scoring can be replaced by the new structured scorer without touching the orchestrator's core loop.
- The existing `ai_score_source` field already supports versioning (`v1_rules_plus_ai`, `v2_rules_plus_semantic`).

### 3. State Machine Has Room
- `RecruiterEmail.state` currently uses: `needs_review`, `auto_rejected`, `approved_sent`, `failed`.
- Adding `resume_refinement_required` is a simple string enum extension. No migration of existing records needed.

### 4. Frontend Architecture Supports New Pages
- `activePage` state is a simple string selector — adding `'skipped_drafts'` is trivial.
- `Sidebar.tsx` takes counts as props — adding a new nav item requires one more prop.
- `features/` directory already exists for modular feature folders.

### 5. Resume Upload Infrastructure Exists
- `POST /settings/resume` endpoint already handles file upload.
- `extract_resume_context()` in `ai/resume_context.py` already parses PDF/DOCX.
- The existing upload flow just needs to be enhanced (not rebuilt) for multi-resume + evidence extraction.

### 6. External Feeds Integration Point
- `ExternalOpportunity` already stores scraped job data separately.
- Adding JD profile extraction to the nVoids ingestion pipeline is straightforward.

---

## Integration Strategy (Non-Breaking)

The enhancement can be integrated incrementally behind feature flags without breaking existing functionality:

1. **Additive-only model changes** — New columns/tables via Alembic migrations with nullable defaults.
2. **New state is opt-in** — `resume_refinement_required` only appears when new scoring is active.
3. **Feature flag gating** — A `feature_resume_matching_v2` flag enables the new flow; old scoring remains as fallback.
4. **New endpoints, not modified ones** — `/resume-database/*` and `/candidates/{id}/redraft` are new routes.
5. **Frontend behind new page** — Skipped Drafts is a new page; no existing pages modified until proven stable.

---

## Phase-by-Phase Implementation Plan

### Phase 1: Resume Database (Backend)

**Files to modify:**
- `backend/app/models.py` — Add `ResumeProfile` model
- `backend/app/ai/resume_context.py` — Add skills extraction + evidence extraction
- `backend/app/schemas.py` — Add resume database schemas
- `backend/app/main.py` — Register new router

**Files to create:**
- `backend/app/services/resume_database_service.py` — CRUD + processing orchestration
- `backend/app/ai/skills_extraction.py` — Skills section parsing, normalization, alias mapping
- `backend/app/ai/evidence_extraction.py` — Full-resume evidence profile builder
- `backend/app/routers/resume_database.py` — REST endpoints
- `backend/alembic/versions/xxx_add_resume_profile.py` — Migration

**Implementation steps:**

1. Create `ResumeProfile` model with fields:
   - `id`, `resume_asset_id` (FK), `display_name`, `is_enabled`, `is_default`
   - `skills_text`, `normalized_skills_json`, `skills_embedding`
   - `embedding_provider`, `embedding_model`, `embedding_dimension`
   - `skills_extraction_status` (pending | completed | failed)
   - `evidence_profile_json`, `evidence_extraction_status`
   - `profile_version`, `source_content_hash`
   - `created_at`, `updated_at`

2. Create `skills_extraction.py`:
   - `extract_skills_section(text: str) -> str` — Find Skills/Technical Skills heading, extract until next heading
   - `normalize_skills(skills_text: str) -> list[dict]` — Alias resolution, deduplication
   - `SKILL_ALIASES: dict` — Mapping (Postgres→PostgreSQL, K8s→Kubernetes, etc.)
   - Handle table-format skills, multi-page skills sections

3. Create `evidence_extraction.py`:
   - `extract_evidence_profile(text: str) -> dict` — Parse full resume into structured evidence
   - Identify: summary, skills section, project bullets, employer experience, certifications
   - Classify each skill occurrence: `project_bullet` | `environment_list` | `skills_section_only`
   - Extract years of experience per technology where inferable

4. Create `resume_database_service.py`:
   - `upload_resume(file, display_name) -> ResumeProfile`
   - `process_resume_profile(profile_id)` — Trigger extraction pipeline
   - `list_resumes() -> list[ResumeProfile]`
   - `update_resume(profile_id, display_name, is_enabled)`
   - `delete_resume(profile_id)`
   - `get_resume(profile_id) -> ResumeProfile`

5. Create REST router `/resume-database`:
   - `POST /resume-database` — Upload + create profile
   - `GET /resume-database` — List all profiles
   - `GET /resume-database/{id}` — Get profile with evidence summary
   - `PATCH /resume-database/{id}` — Update display_name, is_enabled
   - `DELETE /resume-database/{id}` — Soft delete

6. Generate Alembic migration.

**Validation:** Existing `POST /settings/resume` and `ResumeAsset` remain unchanged. New endpoints are additive.

---

### Phase 2: JD Profile Extraction (Backend)

**Files to modify:**
- `backend/app/models.py` — Add `jd_profile_json`, `jd_embedding` to `RecruiterEmail`
- `backend/app/automation/run_orchestrator.py` — Add JD extraction step (gated)

**Files to create:**
- `backend/app/ai/jd_extraction.py` — JD profile extraction logic
- `backend/alembic/versions/xxx_add_jd_profile_fields.py` — Migration

**Implementation steps:**

1. Create `jd_extraction.py`:
   - `extract_jd_profile(subject: str, body: str) -> JDProfile`
   - Parse into: `hard_requirements`, `preferred_requirements`, `responsibilities`
   - Extract: `exact_technologies`, `min_years_experience`, `location`, `work_mode`
   - Extract: `visa_restrictions`, `domain_expectations`, `cloud_requirements`
   - Classify mandatory vs preferred using signal words ("must have", "required", "X+ years" vs "nice to have", "preferred", "bonus")
   - Remove recruiter signatures and quoted email history before parsing

2. Add to `RecruiterEmail`:
   - `jd_profile_json: Column(JSON, nullable=True)`
   - `jd_embedding: Column(JSON, nullable=True)`

3. Integrate into `RunOrchestrator.execute()` (behind feature flag):
   - After `parse_email()`, call `extract_jd_profile()` if `feature_resume_matching_v2` enabled
   - Store result in `jd_profile_json`
   - Generate and store embedding in `jd_embedding`

**Validation:** When flag is off, existing flow is unchanged. JD extraction is a new optional step that stores data but doesn't yet affect decisions.

---

### Phase 3: Resume Selector + Structured Scoring (Backend)

**Files to modify:**
- `backend/app/models.py` — Add `CandidateResumeMatch` model, add scoring fields to `RecruiterEmail`

**Files to create:**
- `backend/app/services/resume_selector_service.py` — Two-stage resume selection
- `backend/app/services/structured_scorer.py` — Weighted scoring + evidence confidence
- `backend/alembic/versions/xxx_add_resume_match_table.py` — Migration

**Implementation steps:**

1. Create `CandidateResumeMatch` model:
   - `id`, `candidate_id` (FK to RecruiterEmail), `resume_asset_id` (FK)
   - `overall_score`, `hard_requirement_score`, `technical_score`, `semantic_score`
   - `matched_requirements_json`, `missing_requirements_json`
   - `evidence_json`, `risk_flags_json`
   - `is_selected: bool`, `created_at`

2. Add to `RecruiterEmail`:
   - `selected_resume_asset_id`, `resume_match_score`, `hard_requirement_score`
   - `technical_stack_score`, `responsibility_alignment_score`
   - `evidence_summary_json`, `missing_requirements_json`, `risk_flags_json`
   - `match_recommendation`, `resume_match_source`, `resume_match_version`

3. Create `structured_scorer.py`:
   - `calculate_match(jd_profile: dict, resume_evidence: dict) -> MatchResult`
   - Weighted categories: Hard requirements (45%), Tech stack (25%), Responsibilities (15%), Cloud/DevOps/Testing (10%), Resume clarity (5%)
   - Evidence confidence levels: Strong, Partial, Weak, Missing
   - Knockout flag detection: `years_requirement_not_met`, `missing_required_framework`, `missing_required_database`, `missing_location_or_work_auth`, etc.
   - Exact vs related match distinction (Kafka ≠ Kafka Streams)

4. Create `resume_selector_service.py`:
   - `select_best_resume(jd_profile: dict, resume_profiles: list) -> SelectionResult`
   - **Stage 1 (Fast):** Normalized skill overlap + skills embedding similarity + role alignment → shortlist top 3
   - **Stage 2 (Detailed):** Full evidence verification on shortlisted resumes
   - Return: selected resume ID, scores breakdown, matched/missing requirements, knockout flags, explanation

**Validation:** New service is called only when flag is active. Does not modify existing `ai_assist_score` or `ScoringRuntimeService`.

---

### Phase 4: Queue Decision + Skipped Drafts State (Backend)

**Files to modify:**
- `backend/app/automation/run_orchestrator.py` — Add resume matching decision branch
- `backend/app/services/orchestration_service.py` — Add redraft method
- `backend/app/schemas.py` — Add redraft request/response schemas
- `backend/app/main.py` — Register redraft endpoint

**Files to create:**
- `backend/app/routers/redraft.py` — Redraft endpoint
- `backend/alembic/versions/xxx_add_resume_refinement_state.py` — Migration (if state needs DB-level validation)

**Implementation steps:**

1. Add `resume_refinement_required` to the state field's accepted values.

2. Modify `RunOrchestrator.execute()` (gated):
   - After JD extraction, call `resume_selector_service.select_best_resume()`
   - If no resume passes threshold → set state to `resume_refinement_required`, store scoring details, skip draft generation
   - If resume passes → pin `selected_resume_asset_id`, continue to draft generation
   - Existing hard filter / F2F / routing failures remain in their current states

3. Create Redraft endpoint `POST /candidates/{candidate_id}/redraft`:
   - Load candidate and cached `jd_profile_json`
   - Load all enabled `ResumeProfile` records
   - Run `resume_selector_service.select_best_resume()`
   - If passes: generate draft, pin resume, move to `needs_review`, return success response
   - If fails: update scores and missing requirements, remain in `resume_refinement_required`, return failure response

4. Modify `approve_send()` in `OrchestrationService`:
   - When attaching resume, use `candidate.selected_resume_asset_id` if set (instead of global `is_current` resume)
   - Fallback to `is_current` resume if `selected_resume_asset_id` is null (backward compatibility)

**Validation:** Existing candidates in `needs_review`/`failed`/`approved_sent` are unaffected. Only new candidates processed with the flag get the new state.

---

### Phase 5: Frontend — Resume Database UI

**Files to modify:**
- `dashboard/src/App.tsx` — Add Resume Database card to Run Queue settings area
- `dashboard/src/components/Sidebar.tsx` — No change needed yet (Resume Database is within settings/Run Queue)

**Files to create:**
- `dashboard/src/features/resume_database/ResumeDatabase.tsx` — Main component
- `dashboard/src/features/resume_database/ResumeUploadModal.tsx` — Upload dialog
- `dashboard/src/features/resume_database/types.ts` — Type definitions
- `dashboard/src/features/resume_database/api.ts` — API calls

**Implementation steps:**

1. Create Resume Database card component:
   - Table/list view of all uploaded resumes
   - Columns: Display Name, Filename, Upload Date, Status (extraction), Enabled toggle, Default indicator
   - Upload button → modal with file picker + display name input
   - Edit/delete actions per resume
   - Status indicators: extraction pending, completed, failed

2. Integrate into App.tsx:
   - Add `<ResumeDatabase />` card in the Run Queue page settings section (near existing resume upload)
   - Existing single-resume upload remains functional as a simplified entry point

**Validation:** Existing resume upload (`POST /settings/resume`) continues to work. The new Resume Database is an additional management layer.

---

### Phase 6: Frontend — Skipped Drafts Page

**Files to modify:**
- `dashboard/src/candidateBuckets.ts` — Add `resume_refinement_required` state
- `dashboard/src/components/Sidebar.tsx` — Add "Skipped Drafts" navigation item
- `dashboard/src/App.tsx` — Add `'skipped_drafts'` page rendering

**Files to create:**
- `dashboard/src/features/skipped_drafts/SkippedDrafts.tsx` — Main page component
- `dashboard/src/features/skipped_drafts/SkippedDraftCard.tsx` — Individual card
- `dashboard/src/features/skipped_drafts/types.ts` — Type definitions
- `dashboard/src/features/skipped_drafts/api.ts` — API calls (redraft, load skipped)

**Implementation steps:**

1. Add to `candidateBuckets.ts`:
   - New state: `'resume_refinement_required'`
   - New bucket: `skippedQueue` with same pagination pattern

2. Add Sidebar item:
   - "Skipped Drafts" with count badge between "Failed Mapping" and "Premium Numbers"

3. Create Skipped Draft Card showing:
   - Job title, company/recruiter, source link
   - Best available resume and its match score
   - Hard-requirement score
   - Matched mandatory skills (green chips)
   - Missing mandatory skills (red chips)
   - Knockout flags (warning badges)
   - Recommendation text
   - Message: "Resume Refinement Required according to the JD"
   - Buttons: **Upload Refined Resume** (opens Resume Database modal), **Redraft**

4. Redraft button action:
   - `POST /candidates/{id}/redraft`
   - On success: remove from skipped list, show success toast, refresh needs_review count
   - On failure: update card with new scores and missing requirements

**Validation:** No existing pages are modified. The new page is purely additive.

---

### Phase 7: nVoids Integration

**Files to modify:**
- `backend/app/external_feeds/service.py` — Add JD extraction to nVoids ingestion
- `backend/app/external_feeds/models.py` — Add `jd_profile_json` to `ExternalOpportunity`

**Implementation steps:**

1. After scraping job detail HTML, extract structured JD profile using `jd_extraction.py`
2. Store `jd_profile_json` on `ExternalOpportunity`
3. When bridging nVoids opportunity to `RecruiterEmail`, carry over the JD profile
4. Resume selector evaluates nVoids candidates the same as Gmail candidates

**Validation:** nVoids scraping continues to work. JD extraction is an additional processing step.

---

## Migration Safety

| Migration | Risk | Mitigation |
|-----------|------|-----------|
| Add `ResumeProfile` table | None | New table, no existing data affected |
| Add `CandidateResumeMatch` table | None | New table |
| Add `jd_profile_json` to `RecruiterEmail` | None | Nullable column, no default needed |
| Add scoring fields to `RecruiterEmail` | None | All nullable, existing records unaffected |
| Add `jd_profile_json` to `ExternalOpportunity` | None | Nullable column |
| Add fields to `ResumeAsset` (or separate model) | None | Nullable columns or new related table |

All migrations are purely additive. No existing columns are removed or altered.

---

## Feature Flag Strategy

```python
# In UserSettings model
feature_resume_matching_v2: bool = False  # Master switch

# In RunOrchestrator
if settings.feature_resume_matching_v2:
    # New flow: JD extraction → resume selection → structured scoring
else:
    # Existing flow: keyword + semantic scoring (unchanged)
```

This allows:
- Gradual rollout
- Easy rollback
- A/B comparison of old vs new scoring
- Production-safe deployment

---

## Testing Strategy

### Unit Tests (Phase 1-3)
- `tests/test_skills_extraction.py` — DOCX tables, multi-page, PDF, missing headings
- `tests/test_evidence_extraction.py` — Project bullets vs skills-only, years calculation
- `tests/test_jd_extraction.py` — Mandatory vs preferred, years, location, visa
- `tests/test_structured_scorer.py` — Weighted scoring, knockout flags, confidence levels
- `tests/test_resume_selector.py` — Multi-resume selection, edge cases

### Integration Tests (Phase 4)
- `tests/test_redraft_flow.py` — End-to-end redraft success and failure
- `tests/test_queue_decisions.py` — State routing accuracy
- `tests/test_candidate_attachment.py` — Pinned resume on approval

### Frontend Tests (Phase 5-6)
- `candidateBuckets.test.ts` — New state handling
- Resume Database component tests
- Skipped Drafts component tests

---

## Dependency Impact

### New Backend Dependencies (if needed)
- None required for core functionality
- Existing `pypdf`, `zipfile` handle PDF/DOCX parsing
- Existing embedding infrastructure handles vector operations
- DeepSeek API handles AI extraction (JD parsing, evidence extraction)

### No Breaking Changes To
- Existing API endpoints (`/candidates`, `/settings`, `/gmail/*`)
- Existing candidate states (`needs_review`, `failed`, `approved_sent`)
- Existing resume upload (`POST /settings/resume`)
- Existing scoring logic (remains as fallback)
- Gmail send flow
- nVoids scraping
- Premium numbers
- Routing system

---

## Estimated Implementation Order & Dependencies

```
Phase 1 (Resume Database)          ← No dependencies, can start immediately
    ↓
Phase 2 (JD Extraction)            ← Depends on Phase 1 for testing with real resumes
    ↓
Phase 3 (Scorer + Selector)        ← Depends on Phase 1 + 2 outputs
    ↓
Phase 4 (Queue Decision + Redraft) ← Depends on Phase 3 scorer
    ↓
Phase 5 (Frontend: Resume DB UI)   ← Depends on Phase 1 API
    ↓
Phase 6 (Frontend: Skipped Drafts) ← Depends on Phase 4 API + Phase 5 upload modal
    ↓
Phase 7 (nVoids Integration)       ← Depends on Phase 2 + 3
```

Phases 1 and 5 can be developed in parallel (backend + frontend resume DB).
Phases 2 and 5 can overlap.
Phase 7 can be deferred without blocking the core flow.

---

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| AI extraction quality varies | Cache profiles; allow manual skill edits; use confidence levels |
| Embedding model changes | Store provider/model/dimension with each embedding; invalidation logic |
| Performance with many resumes | Two-stage selection (fast shortlist → detailed verify); cache everything at upload |
| False knockout flags | Flags are advisory; user can override via Redraft after resume update |
| Migration conflicts | All changes are nullable additions; no column renames or removals |
| Frontend regressions | New pages only; existing pages unchanged until Phase 6 sidebar addition |

---

## Summary

This enhancement is **fully achievable** within the current CODEJOB architecture. The codebase already has:
- Resume asset storage and embedding infrastructure
- Modular scoring pipeline with versioning support
- Feature flag system for gradual rollout
- Tab-based frontend extensible with new pages
- Alembic migrations for safe schema evolution

The key principle is **additive-only changes behind a feature flag**, ensuring zero risk to existing functionality while the new system is built and validated incrementally.
