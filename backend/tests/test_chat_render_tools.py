import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools import render as render_tools
from app.mcp_server.tools.render import MAX_CELL_CHARS, render_candidate_table
from app.models import RecruiterEmail


class RenderCandidateTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.previous_session_local = render_tools.SessionLocal
        render_tools.SessionLocal = self.SessionLocal

    def tearDown(self) -> None:
        render_tools.SessionLocal = self.previous_session_local
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _candidate(self, **overrides: object) -> RecruiterEmail:
        db = self.SessionLocal()
        try:
            row = RecruiterEmail(
                owner_id=overrides.pop("owner_id", settings.owner_id),
                sender=overrides.pop("sender", "sarah@acme-staffing.com"),
                subject=overrides.pop("subject", "Senior Backend Engineer"),
                body="",
                role=overrides.pop("role", "Senior Backend Engineer"),
                state=overrides.pop("state", "needs_review"),
                **overrides,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row
        finally:
            db.close()

    def test_returns_default_columns_with_values_read_from_the_database(self) -> None:
        row = self._candidate(ats_score=76.5)
        result = render_candidate_table([row.id], title="Top matches")

        self.assertEqual(result["action"], "render_candidate_table")
        self.assertEqual(result["title"], "Top matches")
        self.assertEqual(result["columns"], ["role", "sender", "ats_score", "state"])
        self.assertEqual(
            result["rows"],
            [
                {
                    "candidate_id": row.id,
                    "record_id": row.record_id,
                    "role": "Senior Backend Engineer",
                    "sender": "sarah@acme-staffing.com",
                    "ats_score": 76.5,
                    "state": "needs_review",
                }
            ],
        )
        self.assertEqual(result["dropped"], [])
        self.assertFalse(result["truncated"])

    # The model chooses which rows appear; it never supplies what they say. A
    # fabricated or laundered cell has to be impossible, not merely unlikely.
    def test_cell_values_come_from_the_row_not_from_the_caller(self) -> None:
        row = self._candidate(role="Data Engineer")
        result = render_candidate_table([row.id], title="Ignore me")

        self.assertEqual(result["rows"][0]["role"], "Data Engineer")
        self.assertNotIn("untrusted_candidate_data", str(result["rows"]))

    def test_model_facing_result_marks_the_row_text_as_untrusted(self) -> None:
        row = self._candidate()
        result = render_candidate_table([row.id])

        self.assertIn("<untrusted_candidate_data>", str(result["notice"]))

    def test_out_of_scope_and_unknown_ids_are_dropped_and_reported(self) -> None:
        mine = self._candidate()
        theirs = self._candidate(owner_id="someone-else")

        result = render_candidate_table([mine.id, theirs.id, 999_999])

        self.assertEqual([row["candidate_id"] for row in result["rows"]], [mine.id])
        self.assertEqual(
            sorted(entry["candidate_id"] for entry in result["dropped"]),
            sorted([theirs.id, 999_999]),
        )
        # Same reason for both: saying "not yours" would confirm the id exists.
        self.assertEqual({entry["reason"] for entry in result["dropped"]}, {"not_found"})

    def test_unknown_columns_are_dropped_and_reported(self) -> None:
        row = self._candidate()

        result = render_candidate_table([row.id], columns=["role", "body", "draft_reply"])

        self.assertEqual(result["columns"], ["role"])
        self.assertEqual(
            sorted(entry["column"] for entry in result["dropped"] if "column" in entry),
            ["body", "draft_reply"],
        )

    def test_falls_back_to_defaults_when_no_requested_column_is_valid(self) -> None:
        row = self._candidate()

        result = render_candidate_table([row.id], columns=["password", "owner_id"])

        self.assertEqual(result["columns"], ["role", "sender", "ats_score", "state"])

    def test_row_cap_truncates_and_reports(self) -> None:
        rows = [self._candidate() for _ in range(3)]
        original_cap = render_tools.MAX_ROWS
        render_tools.MAX_ROWS = 2
        try:
            result = render_candidate_table([row.id for row in rows])
        finally:
            render_tools.MAX_ROWS = original_cap

        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["truncated"])
        self.assertEqual([entry["reason"] for entry in result["dropped"]], ["row_cap"])

    def test_long_cell_text_is_clipped(self) -> None:
        row = self._candidate(role="R" * 500)

        result = render_candidate_table([row.id], columns=["role"])

        self.assertEqual(len(str(result["rows"][0]["role"])), MAX_CELL_CHARS)

    def test_duplicate_ids_render_once(self) -> None:
        row = self._candidate()

        result = render_candidate_table([row.id, row.id, row.id])

        self.assertEqual(len(result["rows"]), 1)

    def test_empty_request_returns_an_empty_table_rather_than_an_error(self) -> None:
        result = render_candidate_table([])

        self.assertEqual(result["rows"], [])
        self.assertEqual(result["dropped"], [])
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()
