# Nvoids JD-Body AI Parsing Progress

## Summary

This file is now a progress handoff for the Nvoids row-3 AI parsing work on branch `semantic-embeddings`.

The implementation requested in the previous plan has been completed in code and validated with focused tests.

Verified test command:

```powershell
$env:DEBUG='false'; uv run pytest backend/tests/test_ai_extractor.py backend/tests/test_phase0_routing.py backend/tests/test_external_feeds_api.py
```

Result:

- `66 passed`

## What Has Been Implemented

### Phase 1. `ParsedNvoidsDetail.jd_body` added

Implemented in:

- `backend/app/external_feeds/types.py`

Current state:

- `ParsedNvoidsDetail` now includes `jd_body: str`

### Phase 2. Explicit third-meaningful-row extraction added

Implemented in:

- `backend/app/external_feeds/parser.py`

Current state:

- the parser now explicitly extracts the JD body in code
- it uses the main table on a single Nvoids `job_details.jsp` detail page
- it does **not** use the listing/search results table
- it does **not** ask AI to figure out the correct row
- the selected row is stored as `jd_body`

Current rule:

- rows are read from the chosen Nvoids detail-page table
- decorative/noise/contact-only rows are filtered out
- the 3rd remaining meaningful row is used as `jd_body`

Important implementation note:

- this is deterministic parser logic, not prompt-only steering

### Phase 3. No DB migration path implemented

Implemented in:

- `backend/app/external_feeds/service.py`

Current state:

- no database schema change was added
- the queue path reconstructs `jd_body` from stored `item.raw_html`
- this keeps the implementation migration-free

### Phase 4. `ai_body_override` added to parser entrypoint

Implemented in:

- `backend/app/phase0.py`

Current state:

- `parse_email_with_details(...)` now accepts `ai_body_override: str | None = None`

Behavior now:

- base parsing still uses the full original `body`
- AI extractor gating uses the override body when present
- AI extraction uses the override body when present
- only Nvoids currently passes the override

### Phase 5. Nvoids queue path now sends JD-only body to AI

Implemented in:

- `backend/app/external_feeds/service.py`

Current state:

- `_enqueue_needs_review_candidate(...)` reconstructs Nvoids detail parsing from `item.raw_html`
- it passes the extracted `jd_body` as `ai_body_override`
- it keeps the full `body` for normal parsing, scoring, queue preparation, and draft generation

Added parser metadata:

- `ai_input_source`
- `ai_input_chars`

Current Nvoids value:

- `ai_input_source = "nvoids_detail_table_row_3"`

### Phase 6. AI schema validation added

Implemented in:

- `backend/app/parsing/ai_extractor.py`

Current state:

- DeepSeek JSON is validated through a Pydantic model before being accepted
- field coercion is handled in the extractor
- weak-but-valid Nvoids payloads are rejected

Current validation behavior includes:

- structure validation
- free-skill normalization
- Nvoids quality checks for weak skill extraction
- Nvoids evidence requirement for substantial JD rows

### Phase 7. Failed AI validation now falls back cleanly

Implemented in:

- `backend/app/phase0.py`
- `backend/app/parsing/ai_extractor.py`

Current state:

- extractor error payloads are no longer promoted to `ai_primary`
- if the extractor returns an error condition, `phase0.py` switches to `ai_fallback`
- base parser output remains the effective parse on failure
- `parser_warning` and `fallback_used` are populated

Chosen behavior:

- fallback only
- no retry pass

### Phase 8. Tests added and updated

Implemented in:

- `backend/tests/test_ai_extractor.py`
- `backend/tests/test_phase0_routing.py`
- `backend/tests/test_external_feeds_api.py`

Coverage now includes:

1. `jd_body` extraction from the third meaningful Nvoids detail-page row
2. Nvoids queue path passing full body plus `ai_body_override`
3. parser using override only for AI input
4. weak valid Nvoids AI JSON causing fallback
5. extractor error payloads not becoming `ai_primary`
6. existing Gmail/manual behavior staying green in the focused parser tests

## Current Behavior Contract

The live intended flow is now:

1. Open a single Nvoids detail page.
2. Identify the main detail-page table.
3. Read rows in order.
4. Filter decorative/noise/contact-only rows.
5. Extract the 3rd meaningful remaining row as `jd_body`.
6. Keep the normal full parsed body for the rest of the app.
7. Send only `jd_body` to DeepSeek through `ai_body_override`.
8. Validate AI output with Pydantic and Nvoids quality checks.
9. Use `ai_primary` only if validation succeeds.
10. Otherwise fall back to base parsing with warning metadata.

## What The Next Run Should Do

No required code phase from this plan is still pending.

If a next Codex run continues this work, it should focus on validation against real-world Nvoids samples rather than adding more core plumbing.

Recommended next-run tasks:

1. Run a real or captured Nvoids sample through the end-to-end sync flow and inspect `parser_details_json`.
2. Verify that the chosen third meaningful row is correct on multiple page shapes, especially pages where the large JD content lives in one long row.
3. Tune the Nvoids quality thresholds only if live samples show false fallback or false acceptance.
4. If needed later, improve observability in the dashboard for `ai_input_source`, `ai_input_chars`, and fallback reasons.

## Defaults And Decisions Locked In

- use the Nvoids detail-page table, not the listing/search table
- extract the target row explicitly in code before AI
- use the 3rd meaningful remaining row as `jd_body`
- keep Gmail/manual behavior unchanged
- avoid DB migration
- fallback only on validation failure
- no retry step in this version
