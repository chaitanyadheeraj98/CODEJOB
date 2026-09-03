import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.candidate_actions import ACTIONS, propose_candidate_action
from app.models import RecruiterEmail, UserSettings

OTHER_OWNER = "someone-else"


class ProposeCandidateActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.candidate_actions.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            db.add(RecruiterEmail(
                id=1, owner_id=settings.owner_id, sender="a@example.com", subject="Java",
                body="b", role="Java Developer", state="needs_review",
            ))
            db.add(RecruiterEmail(
                id=2, owner_id=settings.owner_id, sender="b@example.com", subject="Python",
                body="b", role="Python Developer", state="needs_review",
            ))
            db.add(RecruiterEmail(
                id=3, owner_id=settings.owner_id, sender="c@example.com", subject="Sent",
                body="b", role="Go Developer", state="approved_sent",
            ))
            db.add(RecruiterEmail(
                id=4, owner_id=OTHER_OWNER, sender="d@example.com", subject="Theirs",
                body="b", role="Rust Developer", state="needs_review",
            ))
            db.commit()

    def states(self) -> dict[int, str]:
        with self.SessionLocal() as db:
            return {row.id: row.state for row in db.query(RecruiterEmail)}

    def test_a_proposal_mutates_nothing(self) -> None:
        before = self.states()

        for action in ACTIONS:
            with self.subTest(action=action):
                propose_candidate_action(action, [1, 2, 3])

        self.assertEqual(self.states(), before)

    def test_proposal_names_the_count_the_roles_and_reversibility(self) -> None:
        payload = propose_candidate_action("reject", [1, 2], reason="Not a fit")

        self.assertEqual(payload["action"], "propose_candidate_action")
        self.assertEqual(payload["candidate_ids"], [1, 2])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["roles"], ["Java Developer", "Python Developer"])
        self.assertEqual(payload["reason"], "Not a fit")
        # From the endpoint audit, set by the tool - never from the model.
        self.assertIs(payload["reversible"], False)
        self.assertTrue(payload["reversible_detail"])

    def test_track_and_untrack_carry_the_tracked_flag(self) -> None:
        self.assertIs(propose_candidate_action("track", [1])["tracked"], True)
        self.assertIs(propose_candidate_action("untrack", [1])["tracked"], False)
        self.assertIs(propose_candidate_action("track", [1])["reversible"], True)

    def test_cross_owner_and_missing_ids_are_dropped_with_one_reason(self) -> None:
        payload = propose_candidate_action("reject", [1, 4, 999])

        self.assertEqual(payload["candidate_ids"], [1])
        reasons = {item["candidate_id"]: item["reason"] for item in payload["dropped"]}
        self.assertEqual(reasons, {4: "not_found", 999: "not_found"})

    def test_precondition_failures_are_reported_before_the_click(self) -> None:
        # reject_candidate raises 400 unless state == needs_review. Reporting
        # id 3 here beats reporting it as a failure after the user confirms.
        payload = propose_candidate_action("reject", [1, 3])

        self.assertEqual(payload["candidate_ids"], [1])
        self.assertEqual(
            payload["dropped"],
            [{"candidate_id": 3, "reason": "wrong_state", "state": "approved_sent", "required_state": "needs_review"}],
        )

    def test_an_action_with_no_state_precondition_accepts_any_state(self) -> None:
        payload = propose_candidate_action("track", [1, 3])

        self.assertEqual(payload["candidate_ids"], [1, 3])
        self.assertEqual(payload["dropped"], [])

    def test_empty_surviving_list_returns_missing_fields(self) -> None:
        payload = propose_candidate_action("reject", [3, 4])

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["candidate_ids"])
        self.assertNotIn("candidate_ids", payload)

    def test_unknown_action_returns_the_valid_list(self) -> None:
        payload = propose_candidate_action("teleport", [1])

        self.assertIn("error", payload)
        self.assertEqual(payload["actions"], sorted(ACTIONS))

    def test_duplicate_ids_are_collapsed(self) -> None:
        self.assertEqual(propose_candidate_action("track", [1, 1, 2, 1])["candidate_ids"], [1, 2])

    def test_every_action_names_a_bulk_endpoint_that_exists(self) -> None:
        from app.main import app

        routes = {route.path for route in app.routes if hasattr(route, "path")}
        for action, spec in ACTIONS.items():
            with self.subTest(action=action):
                self.assertIn(spec["endpoint"], routes)

    def test_delete_is_not_offered(self) -> None:
        # Irreversible actions are deliberately out of v2 (plan 16).
        self.assertNotIn("delete", ACTIONS)


if __name__ == "__main__":
    unittest.main()
