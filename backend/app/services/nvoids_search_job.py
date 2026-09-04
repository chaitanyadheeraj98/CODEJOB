"""Queue a one-off nvoids search for a named company, and report what it did.

§16.7 steps 3-8. The chat must never block on a crawl, and the crawl must never
start without the user saying so.

**Not a new queue.** `_enqueue_background_job` and `run_nvoids_sync_job` already
exist and already do this - RQ, a run key, progress rows, a worker container.
What was missing is a way to run that machinery against criteria supplied for one
search rather than the saved settings, so `SearchCriteria` carries an override
down to `build_nvoids_query` and nothing else changes.

**What a completion message may claim.** Not what nvoids counted. It caps its
count at 500 and ranks by relevance rather than filtering (§16.0), so the number
it reports is not a yield. And the phone gate drops roughly three quarters of
what is ingested - 2,348 of 3,818 rows are `ignored_no_phone` - so "found 50" and
"imported 12" are different facts and the second is the honest one.

**Nothing found is not a failure.** A repeated search imports zero because the
dedupe key is sound: 3,818 rows, 3,818 distinct `external_post_id`, no
collisions. `OUTCOME_NO_NEW` exists so that reads as the no-op it is rather than
as an error.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.external_feeds import service as external_feeds

# What the job did, for the message the user finally sees. Each is a distinct
# sentence: "it broke", "it worked and found nothing new", and "it worked" must
# never be rendered the same way.
OUTCOME_IMPORTED = "imported"
OUTCOME_NO_NEW = "no_new_results"
OUTCOME_NOTHING_FOUND = "nothing_found"
OUTCOME_FAILED = "failed"


@dataclass(frozen=True)
class SearchCriteria:
    """One search's criteria, overriding the saved settings for this run only."""

    end_client: str = ""
    job_role: str = ""
    search_location: str = ""
    query_mode: str = external_feeds.QUERY_MODE_COMPOSED
    batch_limit: int = 10

    def build_query(self, *, default_query: str = external_feeds.DEFAULT_NVOIDS_QUERY) -> str:
        return external_feeds.compose_nvoids_query(
            job_role=self.job_role,
            search_location=self.search_location,
            # Never applied here: this run is about one company, and a saved raw
            # override would silently search for something else entirely.
            custom_query="",
            end_client=self.end_client,
            query_mode=self.query_mode,
            default_query=default_query,
        )

    def as_dict(self) -> dict[str, object]:
        """Every criterion, so the confirmation shows what will actually run."""
        return {
            "end_client": self.end_client or None,
            "job_role": self.job_role or None,
            "search_location": self.search_location or None,
            "query_mode": self.query_mode,
            "batch_limit": self.batch_limit,
            "generated_query": self.build_query(),
        }

    def describe(self) -> str:
        parts = [f"end client {self.end_client!r}"] if self.end_client else []
        if self.query_mode == external_feeds.QUERY_MODE_COMPOSED:
            if self.job_role:
                parts.append(f"role {self.job_role!r}")
            if self.search_location:
                parts.append(f"location {self.search_location!r}")
        else:
            parts.append("end-client only, ignoring role and location")
        parts.append(f"batch limit {self.batch_limit}")
        return ", ".join(parts) if parts else "the saved criteria"


def summarize_outcome(
    *,
    found: int,
    imported: int,
    failed: bool = False,
    error: str = "",
) -> dict[str, object]:
    """Turn raw counts into the message the user is owed.

    `found` is what the crawl retrieved and `imported` what survived dedupe and
    the phone gate. Reporting only the first would overstate the result roughly
    fourfold, which is what the measured 24.2% bridge rate means in practice.
    """
    if failed:
        return {
            "outcome": OUTCOME_FAILED,
            "message": f"The nvoids search did not finish: {error or 'unknown error'}.",
            "found": found,
            "imported": 0,
            "instruction": (
                "Say the search failed and why. Do not present it as having found "
                "nothing - a failed search and an empty one are different facts."
            ),
        }
    if found == 0:
        return {
            "outcome": OUTCOME_NOTHING_FOUND,
            "message": "The nvoids search returned no postings for these criteria.",
            "found": 0,
            "imported": 0,
            "instruction": (
                "Say nothing matched these criteria. That is not evidence the "
                "company has no requirements - only that this search found none. "
                "Offer to widen: end-client-only mode drops role and location."
            ),
        }
    if imported == 0:
        return {
            "outcome": OUTCOME_NO_NEW,
            "message": (
                f"The nvoids search found {found} posting(s) and imported none - "
                "every one was already stored."
            ),
            "found": found,
            "imported": 0,
            "instruction": (
                "Say nothing new arrived and that the existing records already "
                "cover it. This is a successful no-op, not a failure."
            ),
        }
    dropped = max(0, found - imported)
    message = f"Imported {imported} of {found} posting(s) found."
    if dropped:
        message += (
            f" {dropped} were not imported - already stored, or dropped because no "
            "recruiter phone number could be extracted, which the ingestion "
            "requires before a posting becomes an opportunity."
        )
    return {
        "outcome": OUTCOME_IMPORTED,
        "message": message,
        "found": found,
        "imported": imported,
        "instruction": (
            "Report the imported count, never nvoids' own count - it caps at 500 "
            "and ranks by relevance rather than filtering. Then search the newly "
            "imported rows and summarise them, keeping stored and imported "
            "records distinguishable."
        ),
    }
