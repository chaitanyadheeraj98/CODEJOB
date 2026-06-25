# Fix Nvoids Detail Fetch Timeouts Without Breaking Sync

## Summary

Harden the Nvoids detail-page fetch path so slow or inconsistent job-detail pages do not fail after a single 20-second read timeout. The implementation stays backend-only, preserves the current row-level fallback behavior, and adds enough logging and run-summary data to diagnose future failures without touching Gmail, DeepSeek, or embedding timeout logic.

## Implemented Changes

### Dedicated Nvoids detail fetch settings

- Added Nvoids-specific detail fetch settings in `backend/app/config.py`
- New knobs:
  - `nvoids_detail_connect_timeout_seconds = 10.0`
  - `nvoids_detail_read_timeout_seconds = 45.0`
  - `nvoids_detail_write_timeout_seconds = 10.0`
  - `nvoids_detail_pool_timeout_seconds = 10.0`
  - `nvoids_detail_retry_attempts = 3`
  - `nvoids_detail_retry_backoff_seconds = "2,5,10"`

### Collector retry and timeout hardening

- `backend/app/external_feeds/collector.py` now uses:
  - split `httpx.Timeout(...)` values for detail fetches
  - browser-like headers for detail requests
  - retry/backoff for transient transport failures only
- Retriable errors:
  - `httpx.ReadTimeout`
  - `httpx.ConnectTimeout`
  - `httpx.RemoteProtocolError`
- Non-retriable HTTP status failures still fail immediately and fall back at the sync layer

### Observability and sync resilience

- Added attempt-level logs for Nvoids detail fetch start, success, retryable failure, and exhausted failure
- `sync_nvoids()` still continues when a detail page fails after all retries
- Enriched `nvoids_sync_row_detail_fetch_failed` warning with exception type and message
- `ExternalScrapeRun.notes` now records:
  - `detail_fetch_failures`
  - `detail_fetch_retries`
  - `detail_fetch_fallback_rows`
  - `skipped_location_count`

## Runtime Behavior

- Search-page fetching is unchanged
- Detail-page fetching now uses longer read timeout plus retries
- If detail fetch still fails:
  - the row still ingests via listing fallback
  - `failed_count` still increments once for that row
  - the overall Nvoids sync continues

## Test Coverage

- Added collector-level regression tests for:
  - browser-like headers and split timeout usage
  - retry once then succeed
  - repeated read timeouts exhausting retries
  - HTTP status errors not retrying
- Extended external-feed sync tests to verify:
  - timeout fallback still ingests listing-derived role/location
  - run notes include failure/retry/fallback summary
  - non-retriable HTTP status failure still falls back safely

## Scope Notes

- No frontend changes
- No API shape changes
- No database migration required
- Gmail, DeepSeek, and semantic embedding timeout behavior remains unchanged
