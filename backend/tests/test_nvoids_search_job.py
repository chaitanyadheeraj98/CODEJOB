"""§16.9 acceptance tests #13-#19 — the async job contract."""

from __future__ import annotations

from app.external_feeds import service as external_feeds
from app.mcp_server import server
from app.services import nvoids_search_job as JOB


def criteria(**kwargs) -> JOB.SearchCriteria:
    return JOB.SearchCriteria(**kwargs)


# --- Confirmation (§16.9 #13, #14) ----------------------------------------


def test_the_proposal_tool_sits_behind_the_existing_confirmation_boundary() -> None:
    """§16.9 #13. An outbound crawl of a third party is a side effect, not a
    read. It uses the propose-then-confirm mechanism every other write action
    uses rather than a second one invented for this feature."""
    names = {tool.__name__ for tool in server.CHAT_ACTION_TOOLS}

    assert "propose_nvoids_search" in names
    # And it is not silently also available as a plain read.
    assert "propose_nvoids_search" not in {tool.__name__ for tool in server.BASE_TOOLS}


def test_the_generated_query_is_part_of_the_proposal() -> None:
    """§16.9 #14. The user must see the exact string before agreeing to it."""
    payload = criteria(end_client="Morgan Stanley", job_role="java").as_dict()

    assert payload["generated_query"] == "java and morgan and stanley"
    assert payload["end_client"] == "Morgan Stanley"
    assert payload["batch_limit"] == 10


def test_every_criterion_appears_in_the_summary() -> None:
    """§16.9 #5's reporting requirement: role, location, end client, mode and
    batch limit, so "what did it search for" never needs a second question."""
    summary = criteria(
        end_client="Citi", job_role="java", search_location="tx", batch_limit=25
    ).describe()

    for fragment in ("Citi", "java", "tx", "25"):
        assert fragment in summary


def test_end_client_only_mode_says_it_ignored_role_and_location() -> None:
    summary = criteria(
        end_client="Citi",
        job_role="java",
        search_location="tx",
        query_mode=external_feeds.QUERY_MODE_END_CLIENT_ONLY,
    ).describe()

    assert "ignoring role and location" in summary
    assert "java" not in summary


def test_a_one_off_search_does_not_read_the_saved_custom_query() -> None:
    """A saved raw override would silently search for something other than the
    company the user just named."""
    query = criteria(end_client="Citi").build_query()

    assert query == "citi"


# --- Outcomes (§16.9 #16-#19) ---------------------------------------------


def test_imported_reports_both_numbers_not_just_the_count_found() -> None:
    """§16.9 #19. The phone gate drops roughly three quarters of what is
    ingested - 2,348 of 3,818 rows - so "found 50" overstates the result about
    fourfold."""
    outcome = JOB.summarize_outcome(found=50, imported=12)

    assert outcome["outcome"] == JOB.OUTCOME_IMPORTED
    assert "Imported 12 of 50" in outcome["message"]
    assert "phone number" in outcome["message"]


def test_a_repeated_search_reports_no_new_results_not_a_failure() -> None:
    """§16.9 #17. The dedupe key is sound - 3,818 rows, 3,818 distinct post ids,
    zero collisions - so re-running imports nothing. That is a successful no-op."""
    outcome = JOB.summarize_outcome(found=8, imported=0)

    assert outcome["outcome"] == JOB.OUTCOME_NO_NEW
    assert "already stored" in outcome["message"]
    assert "not a failure" in outcome["instruction"]


def test_nothing_found_is_not_evidence_that_nothing_exists() -> None:
    """The system-not-world rule W12 established, applied to a crawl."""
    outcome = JOB.summarize_outcome(found=0, imported=0)

    assert outcome["outcome"] == JOB.OUTCOME_NOTHING_FOUND
    assert "not evidence the company has no requirements" in outcome["instruction"]


def test_a_failure_never_reads_like_an_empty_result() -> None:
    """§16.9 #16. "It broke" and "it found nothing" are different facts."""
    outcome = JOB.summarize_outcome(found=0, imported=0, failed=True, error="connection timeout")

    assert outcome["outcome"] == JOB.OUTCOME_FAILED
    assert "connection timeout" in outcome["message"]
    assert "different facts" in outcome["instruction"]


def test_the_four_outcomes_are_distinct() -> None:
    """§16.9 #16. Nothing may collapse two of these into one message."""
    messages = {
        JOB.summarize_outcome(found=50, imported=12)["message"],
        JOB.summarize_outcome(found=8, imported=0)["message"],
        JOB.summarize_outcome(found=0, imported=0)["message"],
        JOB.summarize_outcome(found=0, imported=0, failed=True, error="x")["message"],
    }

    assert len(messages) == 4


def test_the_import_instruction_forbids_quoting_the_external_count() -> None:
    """§16.9 #18. Nvoids caps at 500 and ranks by relevance rather than
    filtering, so its count is not a yield."""
    instruction = JOB.summarize_outcome(found=500, imported=3)["instruction"]

    assert "never nvoids' own count" in instruction
    assert "caps at 500" in instruction


# --- Reuse ----------------------------------------------------------------


def test_the_criteria_satisfy_the_service_override_protocol() -> None:
    """No new queue was built. `SearchCriteria` is accepted by the existing sync
    through a structural type, so the module graph stays acyclic."""
    assert isinstance(criteria(end_client="Citi"), external_feeds.SearchCriteriaLike)
