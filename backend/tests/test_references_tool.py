import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.references import (
    LOADERS,
    MAX_OPTIONS,
    SEARCHED_FIELDS,
    resolve_record_reference,
)
from app.models import PremiumNumberContact, RecruiterEmail, RecruiterOpportunity, UserSettings

OTHER_OWNER = "someone-else"


class ResolveRecordReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.references.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            # Two Sarahs at different companies - the case the tool exists for.
            db.add(PremiumNumberContact(
                id=1, owner_id=settings.owner_id, is_recruiter=True,
                normalized_phone_number="12145551211", display_phone_number="+1 214 555 1211",
                recruiter_name="Sarah Chen", company="Acme Staffing",
            ))
            db.add(PremiumNumberContact(
                id=2, owner_id=settings.owner_id, is_recruiter=True,
                normalized_phone_number="12145551212", display_phone_number="+1 214 555 1212",
                recruiter_name="Sarah Okonkwo", company="BigCo Talent",
            ))
            db.add(PremiumNumberContact(
                id=3, owner_id=settings.owner_id, is_recruiter=True,
                normalized_phone_number="12145551213", display_phone_number="+1 214 555 1213",
                recruiter_name="Priya Raman", company="Acme Staffing",
            ))
            db.add(PremiumNumberContact(
                id=4, owner_id=OTHER_OWNER, is_recruiter=True,
                normalized_phone_number="12145551214", display_phone_number="+1 214 555 1214",
                recruiter_name="Sarah Hidden", company="Other Co",
            ))
            db.add(RecruiterOpportunity(
                id=1, owner_id=settings.owner_id, recruiter_number_id=1, gmail_message_id="m1",
                job_title="Java Developer", end_client="BigCo", status="New",
            ))
            db.add(RecruiterEmail(
                id=1, owner_id=settings.owner_id, sender="rec@example.com",
                subject="Go role", body="b", role="Go Developer", state="needs_review",
            ))
            db.commit()

    def test_a_single_confident_match_resolves(self) -> None:
        payload = resolve_record_reference("contact", "Priya Raman")

        self.assertEqual(payload["action"], "resolved")
        self.assertEqual(payload["id"], 3)
        self.assertEqual(payload["label"], "Priya Raman")

    def test_an_exact_label_wins_over_a_substring_of_others(self) -> None:
        payload = resolve_record_reference("contact", "Sarah Chen")

        self.assertEqual(payload["action"], "resolved")
        self.assertEqual(payload["id"], 1)

    def test_two_matches_return_options_with_distinguishing_detail(self) -> None:
        payload = resolve_record_reference("contact", "Sarah")

        self.assertEqual(payload["action"], "render_disambiguation")
        self.assertEqual(payload["query"], "Sarah")
        labels = {option["label"] for option in payload["options"]}
        self.assertEqual(labels, {"Sarah Chen", "Sarah Okonkwo"})
        # Without detail the user is choosing between two identical names.
        for option in payload["options"]:
            self.assertTrue(option["detail"])
        self.assertNotIn("Sarah Hidden", str(payload))

    def test_a_cross_owner_record_is_never_returned(self) -> None:
        payload = resolve_record_reference("contact", "Sarah Hidden")

        # Indistinguishable from nonexistence: the loader is owner-scoped, so
        # the row simply is not there to match.
        self.assertIn("error", payload)
        self.assertEqual(payload["error"], "No contact found")

    def test_no_match_names_the_fields_searched(self) -> None:
        payload = resolve_record_reference("opportunity", "zzzzz-nothing")

        self.assertEqual(payload["error"], "No opportunity found")
        self.assertEqual(payload["searched"], SEARCHED_FIELDS["opportunity"])

    def test_a_company_substring_matches_several_contacts(self) -> None:
        payload = resolve_record_reference("contact", "Acme")

        self.assertEqual(payload["action"], "render_disambiguation")
        self.assertEqual({option["id"] for option in payload["options"]}, {1, 3})

    def test_limit_is_capped(self) -> None:
        payload = resolve_record_reference("contact", "Sarah", limit=999)

        self.assertLessEqual(len(payload["options"]), MAX_OPTIONS)

    def test_limit_of_one_resolves_rather_than_offering_a_choice(self) -> None:
        # One option is not a disambiguation; the frontend refuses to draw a
        # chooser with fewer than two options either.
        payload = resolve_record_reference("contact", "Sarah", limit=1)

        self.assertEqual(payload["action"], "resolved")

    def test_opportunity_and_candidate_kinds_resolve(self) -> None:
        self.assertEqual(resolve_record_reference("opportunity", "Java")["id"], 1)
        self.assertEqual(resolve_record_reference("candidate", "Go Developer")["id"], 1)

    def test_unknown_kind_and_empty_query(self) -> None:
        self.assertEqual(resolve_record_reference("planet", "x")["kinds"], sorted(LOADERS))
        self.assertEqual(resolve_record_reference("contact", "  ")["missing"], ["query"])

    def test_matching_is_case_insensitive(self) -> None:
        self.assertEqual(resolve_record_reference("contact", "priya raman")["id"], 3)


if __name__ == "__main__":
    unittest.main()
