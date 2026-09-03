import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.analysis import MAX_COMPARED, MAX_RANKED, compare_records, rank_opportunities
from app.models import (
    Application,
    PremiumNumberContact,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)

OTHER_OWNER = "someone-else"


class AnalysisToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.analysis.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            db.add(ResumeAsset(
                id=1, owner_id=settings.owner_id, file_path="r.pdf", file_name="java.pdf",
                sha256="a" * 64, version=1,
            ))
            db.add(ResumeAsset(
                id=2, owner_id=OTHER_OWNER, file_path="r.pdf", file_name="theirs.pdf",
                sha256="c" * 64, version=1,
            ))
            for contact_id, name, owner in ((1, "Sarah", settings.owner_id), (2, "Priya", settings.owner_id), (3, "Theirs", OTHER_OWNER)):
                db.add(PremiumNumberContact(
                    id=contact_id, owner_id=owner, is_recruiter=True,
                    normalized_phone_number=f"1214555121{contact_id}",
                    display_phone_number=f"+1 214 555 121{contact_id}",
                    recruiter_name=name, company="Acme",
                ))
            for opp_id, title, status, owner in (
                (1, "Java Developer", "New", settings.owner_id),
                (2, "Python Developer", "New", settings.owner_id),
                (3, "Closed Role", "Closed", settings.owner_id),
                (4, "Their Role", "New", OTHER_OWNER),
            ):
                db.add(RecruiterOpportunity(
                    id=opp_id, owner_id=owner, recruiter_number_id=1 if owner == settings.owner_id else 3,
                    gmail_message_id=f"msg-{opp_id}",
                    job_title=title, status=status, end_client="BigCo", location="Dallas, TX",
                ))
            db.commit()

    def test_ranked_list_excludes_closed_and_other_owners(self) -> None:
        payload = rank_opportunities(1)

        self.assertEqual(payload["action"], "render_ranked_list")
        titles = [row["label"] for row in payload["rows"]]
        self.assertIn("Java Developer", titles)
        self.assertNotIn("Closed Role", titles)
        self.assertNotIn("Their Role", titles)

    def test_ranked_rows_carry_reasons_and_a_drill_target(self) -> None:
        payload = rank_opportunities(1)

        for row in payload["rows"]:
            self.assertIsInstance(row["reasons"], list)
            self.assertIsInstance(row["score"], float)
            self.assertEqual(row["drill_to"]["page"], "premium_numbers")

    def test_ranking_provenance_names_the_services_own_exclusions(self) -> None:
        block = rank_opportunities(1)["provenance"]

        self.assertEqual(block["metric"], "Opportunity match score")
        joined = " ".join(block["assumptions"])
        self.assertIn("Closed", joined)
        self.assertIn("do not work again", joined)

    def test_limit_above_the_cap_is_clipped_and_reported(self) -> None:
        payload = rank_opportunities(1, limit=500)

        self.assertLessEqual(len(payload["rows"]), MAX_RANKED)
        self.assertIn({"key": "limit", "reason": "capped", "requested": 500, "applied": MAX_RANKED}, payload["dropped"])

    def test_a_resume_the_owner_does_not_have_is_an_error_not_an_empty_list(self) -> None:
        # Never a partial list: an empty ranking would read as "no matches".
        for missing in (2, 999):
            with self.subTest(resume_asset_id=missing):
                payload = rank_opportunities(missing)
                self.assertEqual(payload["error"], "Resume not found")
                self.assertNotIn("rows", payload)

    def test_comparison_returns_one_column_per_valid_id(self) -> None:
        payload = compare_records("recruiters", [1, 2])

        self.assertEqual(payload["action"], "render_comparison")
        self.assertEqual([column["label"] for column in payload["columns"]], ["Sarah", "Priya"])
        self.assertEqual(payload["dropped"], [])

    def test_cross_owner_id_is_dropped_with_the_same_reason_as_a_missing_one(self) -> None:
        payload = compare_records("recruiters", [1, 3, 4242])

        self.assertEqual([column["record_id"] for column in payload["columns"]], [1])
        reasons = {item["key"]: item["reason"] for item in payload["dropped"]}
        # Identical reasons: distinguishing them would confirm id 3 exists.
        self.assertEqual(reasons, {"3": "not_found", "4242": "not_found"})

    def test_ids_beyond_the_cap_are_dropped_and_reported(self) -> None:
        payload = compare_records("recruiters", list(range(1, 12)))

        self.assertLessEqual(len(payload["columns"]), MAX_COMPARED)
        self.assertTrue(any(item["reason"] == "cap" for item in payload["dropped"]))

    def test_unknown_kind_returns_the_valid_list(self) -> None:
        payload = compare_records("aliens", [1])

        self.assertIn("error", payload)
        self.assertEqual(payload["kinds"], ["recruiters"])
        self.assertNotIn("action", payload)

    def test_a_recruiter_with_no_history_reports_unknown_not_zero(self) -> None:
        payload = compare_records("recruiters", [2])

        values = payload["columns"][0]["values"]
        # median_first_reply is None when nobody ever replied; the frontend
        # renders that as an em dash rather than 0.
        self.assertIsNone(values["median_first_reply_business_days"])

    def test_comparison_carries_provenance(self) -> None:
        block = compare_records("recruiters", [1])["provenance"]

        self.assertEqual(block["metric"], "Recruiter activity")
        self.assertTrue(block["assumptions"])

    def test_reputation_reflects_recorded_applications(self) -> None:
        with self.SessionLocal() as db:
            db.add(Application(
                owner_id=settings.owner_id, resume_asset_id=1, resume_version_snapshot=1,
                resume_file_name_snapshot="java.pdf", resume_sha256_snapshot="a" * 64,
                recruiter_opportunity_id=1, recruiter_contact_id=1,
                status="offer", dedupe_key="offer-1",
                status_changed_at=datetime.now(UTC) - timedelta(days=2),
            ))
            db.commit()

        values = compare_records("recruiters", [1])["columns"][0]["values"]
        self.assertEqual(values["offers_count"], 1)


if __name__ == "__main__":
    unittest.main()
