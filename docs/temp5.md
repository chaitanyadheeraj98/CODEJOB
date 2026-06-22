# Nvoids Sync Recursion Fix - Status

## Current Status

Completed. The Nvoids sync recursion fix has been implemented on the current `semantic-embeddings` branch.

The recursive parser dependency is removed, empty Nvoids detail HTML is now safe, and row-level sync handling now falls back cleanly instead of crashing the whole `/external-feeds/nvoids/sync` flow.

## Completed Phases

### Phase 1: Parser recursion removal

- Status: Complete
- Date: 2026-06-21
- Areas changed:
  - `backend/app/external_feeds/parser.py`
- What changed:
  - `extract_nvoids_detail_title(...)` was rewritten into a leaf helper.
  - It now extracts directly from `_extract_nvoids_table_rows(...)`.
  - It no longer calls `parse_nvoids_detail(...)`.
  - `parse_nvoids_detail(...)` title detection was tightened so URL rows are not treated as listing titles.
- Nvoids behavior impact:
  - removed the direct recursive title-recovery loop

### Phase 2: Empty detail HTML safety

- Status: Complete
- Date: 2026-06-21
- Areas changed:
  - `backend/app/external_feeds/parser.py`
- What changed:
  - `parse_nvoids_detail(...)` now returns a safe fallback `ParsedNvoidsDetail` when `detail_html` is empty or whitespace.
  - `parse_job_detail_contacts(...)` now returns `("", "", "")` immediately for empty detail HTML.
  - added parser-side diagnostic logs for skipped empty-detail parsing paths.
- Nvoids behavior impact:
  - empty detail HTML no longer enters recursive recovery
  - recursion is fully blocked on empty input

### Phase 3: Row-level sync resilience

- Status: Complete
- Date: 2026-06-21
- Areas changed:
  - `backend/app/external_feeds/service.py`
- What changed:
  - `sync_nvoids()` no longer sends empty `detail_html` into structured contact parsing.
  - detail fetch failures now keep row ingestion alive in fallback mode.
  - parser work for each row is now guarded so one malformed row increments `failed_count`, logs the failure, and continues instead of aborting the whole sync.
  - fallback parsing now uses empty `raw_html` when detail fetch failed, preventing search-page HTML from being misused as detail-page HTML.
- Nvoids behavior impact:
  - one bad or timed-out detail page no longer crashes the entire sync run

### Phase 4: Diagnostics and regression validation

- Status: Complete
- Date: 2026-06-21
- Areas changed:
  - `backend/app/external_feeds/parser.py`
  - `backend/app/external_feeds/service.py`
  - `backend/tests/test_external_feeds_parser.py`
  - `backend/tests/test_external_feeds_api.py`
- What changed:
  - added targeted logging for:
    - `nvoids_parse_detail_skipped_empty_html`
    - `nvoids_parse_contacts_skipped_empty_html`
    - `nvoids_sync_row_detail_fetch_failed`
    - `nvoids_sync_row_parse_failed`
    - `nvoids_sync_row_fallback_used`
  - added parser regressions for:
    - fallback title on empty HTML
    - safe structured fallback on empty HTML
    - empty contact extraction
    - malformed HTML non-recursive behavior
  - added service/API regressions for:
    - detail fetch timeout survival
    - row parser failure survival
- Nvoids behavior impact:
  - failure classes are now separated more clearly in logs

## Current Phase

Completed and verified.

## Next Phase

None. This fix is implemented.

If a follow-up pass is needed later, the next logical step would be:

```text
Docker/runtime verification against a live Nvoids sync run
```

not additional code changes for the recursion bug itself.

## Last Verification

Targeted backend tests passed:

```text
uv run pytest tests/test_external_feeds_parser.py
15 passed

uv run pytest tests/test_external_feeds_api.py
26 passed

uv --no-cache run pytest tests/test_run_orchestrator.py
6 passed

uv --no-cache run pytest tests/test_approve_cc_regression.py
8 passed
```

Notes:

- `uv --no-cache` was used for part of verification because the local `uv` cache path had a Windows permission issue unrelated to the Nvoids fix.
- pytest emitted `.pytest_cache` warnings in this environment, but the tests themselves passed.

## Open Risks / Notes

- Code-level recursion and empty-detail crash behavior are fixed.
- Focused regressions passed.
- Live Docker verification of an actual Nvoids sync run was not executed from this Codex environment because Docker access is restricted here.
- The untracked helper file `tmp_nvoids_run_inspect.py` was left untouched.

## Final Outcome

This fix now ensures:

1. `/external-feeds/nvoids/sync` no longer crashes from the verified recursive parser path.
2. Empty or timed-out detail pages are safe.
3. One bad Nvoids row becomes a row failure, not a sync-ending failure.
4. Existing clean Nvoids detail parsing behavior remains covered by regression tests.
