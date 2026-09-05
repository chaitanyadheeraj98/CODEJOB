"""Routes that were deliberately removed, and must not come back.

A deleted endpoint leaves no trace to trip over: nothing imports it, no test
covers it, and a merge that reinstates it looks like any other addition. These
tests are the trace.

Add to the table below when a route is retired, with the reason - the reason is
the point. Someone reading a stale doc, or resurrecting an old branch, needs to
find out *why* it went rather than helpfully putting it back.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient

from app import main

# path -> why it was removed.
RETIRED_ROUTES: dict[str, str] = {
    "/phase0/emails/ingest": (
        "A fourth hand-rolled RecruiterEmail assembler, superseded by manual "
        "intake (POST /manual-requirements). It wrote source='manual' - the "
        "same value manual intake now uses - but set the lineage record to "
        "origin_type='gmail', never resolved routing so rows had no recipient "
        "or CC, drafted rules-only without ever calling "
        "prepare_candidate_for_queue, and skipped premium-contact capture and "
        "recruiter identity stamping. Verified unused before removal: no code "
        "callers, and zero rows attributable to it in production."
    ),
}


class RetiredRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_retired_routes_are_absent_from_the_schema(self) -> None:
        paths = main.app.openapi()["paths"]
        for path, reason in RETIRED_ROUTES.items():
            self.assertNotIn(path, paths, f"{path} is back. It was removed because: {reason}")

    def test_retired_routes_are_not_served(self) -> None:
        """The schema check alone would miss a route added with include_in_schema=False."""
        for path in RETIRED_ROUTES:
            response = self.client.post(path, json={})
            self.assertEqual(response.status_code, 404, f"{path} still answers")

    def test_the_request_schema_went_with_it(self) -> None:
        """IngestEmailRequest existed only to serve that endpoint."""
        import app.schemas as schemas

        self.assertFalse(hasattr(schemas, "IngestEmailRequest"))


if __name__ == "__main__":
    unittest.main()
