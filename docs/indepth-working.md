# In-depth working: ATS scoring, embeddings, Sync + Queue, and Nvoids Sync + Queue

## 1) End-to-end overview

There are two main ingestion paths that feed the same ATS scoring and queue decision logic:

1. **Sync + Queue (Gmail)**
   - Trigger: `POST /automation/run-once`
   - Runtime owner: `backend/app/services/orchestration_service.py`
   - Candidate pipeline: `backend/app/automation/run_orchestrator.py` + `backend/app/automation/queue_preparation.py`

2. **Sync + Queue (Nvoids)**
   - Trigger: `POST /external-feeds/nvoids/sync`
   - Runtime owner: `backend/app/external_feeds/service.py`
   - Nvoids rows are parsed, deduped, then bridged into the same queue-preparation/scoring flow used by Gmail.

Both paths eventually decide whether an item is:
- rejected/skipped,
- failed for routing,
- or queued in `needs_review`.

---

## 2) Core ATS scoring pipeline

Main scoring runtime:
- `backend/app/services/scoring_runtime_service.py` (`ScoringRuntimeService.compute_blended_ai_score`)

### 2.1 Inputs used for scoring
- Email/job text (`subject`, `body`, parsed role/skills)
- User settings (`qualification_threshold`, `feature_semantic_enabled`, role keywords, free-text guidance, etc.)
- Active resume (`ResumeAsset`)
- Optional thread context (same `external_thread_id`) for richer keyword fallback

### 2.2 Step-by-step scoring

1. **Parse + hard-filter prerequisites (outside scoring function) and parsed metadata**
   - `parse_email(...)` extracts role, location, salary text, skills (`backend/app/phase0.py`).
   - `hard_filter_check(...)` checks location/salary/must-have skills (`backend/app/phase0.py`).

2. **Keyword score (rule-assisted AI fit score)**
   - Starts from `ai_assist_score(...)` in `phase0.py`.
   - Base score starts at `0.45`.
   - Adds score from role keyword hits and skill keyword hits.

3. **Keyword enrichment fallbacks**
   - If current parsed skills are weak (`<=1` skill), scoring runtime switches to a richer text fallback (`keyword_source=rich_fallback`).
   - It can also pull a richer prior message from the same thread (`thread_carry_forward`) when historical skill extraction is stronger.

4. **Semantic scoring (if enabled)**
   - Controlled by `UserSettings.feature_semantic_enabled`.
   - Email semantic text is built from:
     - subject
     - latest message block from body (quoted thread history stripped)
     - parsed role
     - parsed skills
   - Resume semantic text is extracted from the current resume file.

5. **Embedding generation + reuse**
   - Existing stored embedding JSON is reused when present.
   - If text is long (>900 chars), it is chunked and chunk embeddings are averaged.
   - Cosine similarity is computed between email embedding and resume embedding.

6. **Blend score**
   - Implemented in `backend/app/semantic/ranking.py`.
   - Semantic similarity `[-1,1]` is normalized to `[0,1]`.
   - Final score = weighted blend of keyword score + semantic score.
   - Weights come from settings (`semantic_keyword_weight`, `semantic_similarity_weight`; defaults 0.6/0.4).

7. **Decision thresholding**
   - Qualification threshold comes from settings/policy.
   - If `hard_filter` fails or score < threshold => not qualified.
   - Otherwise candidate can proceed to routing and `needs_review` queue.

---

## 3) How embeddings work

Embedding runtime:
- `backend/app/semantic/embeddings_service.py`

### 3.1 Providers and fallback
- Effective provider resolves to `sbert` or `hash` (`backend/app/config.py`).
- If `sbert` fails, it falls back to deterministic local hash embeddings.
- Hash embeddings are deterministic token-hash vectors with configured dimensions.

### 3.2 Latency and health
- Embedding calls track latency samples (optional logging).
- `/ai/status` exposes embedding provider/model readiness and runtime health (`backend/app/main.py`).

### 3.3 Similarity math
- Cosine similarity in `backend/app/semantic/similarity.py`.
- If vectors are missing/mismatched/zero norm, similarity returns `0.0`.

---

## 4) How resume embeddings are produced

Resume upload path:
- `POST /resume/upload` in `backend/app/main.py`

Process:
1. Resume file is stored in `resume_storage_dir`.
2. Resume text is extracted by `extract_resume_context(...)` (`backend/app/ai/resume_context.py`):
   - PDF via `pypdf`
   - DOCX via XML extraction
   - fallback conservative text when extraction is limited
3. Embedding is generated and stored in `ResumeAsset.semantic_embedding`.
4. During scoring runs, if a newer/different resume embedding is computed, current resume row is updated.

---

## 5) How job description/email text is studied against resume

### 5.1 For Gmail Sync + Queue
- Gmail candidates are fetched by query.
- For each email:
  - parse structured fields,
  - compute blended ATS score,
  - run routing policy,
  - write queue state.
- Semantic comparison uses cleaned latest email block + parsed role/skills vs resume extracted text.

### 5.2 For Nvoids Sync + Queue
- Nvoids listing rows and detail pages are fetched/parsed in `external_feeds/parser.py`.
- Posts are deduped and stored as `ExternalOpportunity`.
- `_enqueue_needs_review_candidate(...)` creates pseudo-email candidates from Nvoids fields and passes them into `prepare_candidate_for_queue(...)`.
- That means Nvoids candidates use the same ATS scoring function (`compute_blended_ai_score`) and same embedding logic as Gmail.

---

## 6) Queue outcomes and scoring impact

Shared decision points (from `queue_preparation.py` and orchestrator paths):

1. **Not qualified**
   - hard filter failed OR score below threshold OR strict F2F policy block.
   - Marked as rejected/skipped path.

2. **Routing failed**
   - score qualified but safe To/CC routing unresolved.
   - Sent to failed mapping path.

3. **Needs review**
   - qualified by score/policy and routing safe.
   - Candidate enters manual approval queue.

4. **Optional automations after queue**
   - retry queue promotion (`feature_retry_queue`)
   - auto-send approved workflow (`feature_auto_send`)

---

## 7) Stored ATS/embedding diagnostics per candidate

`RecruiterEmail` stores:
- `ai_score`, `ai_score_source`, `ai_summary`
- `semantic_embedding` (email-side embedding JSON)
- semantic diagnostics:
  - `semantic_input_source`
  - `semantic_input_chars`
  - `semantic_chunks`
  - `semantic_fallback_reason`
  - `keyword_source`
  - `thread_snapshot_used`
  - `thread_snapshot_email_id`

This gives traceability for why a candidate got a specific score and which fallback path was used.

---

## 8) Practical summary

- **ATS score is not a single heuristic**: it is a blended score of keyword fit + semantic similarity when enabled.
- **Embeddings compare recruiter job text vs resume text**; resume vectors are cached on upload and reused.
- **Gmail Sync + Queue and Nvoids Sync + Queue converge into the same scoring/queue machinery**, so scoring behavior is consistent across sources.
- **Queue state is decided after scoring + policy + routing safety checks**, not by score alone.
