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
from app.mcp_server.tools.charts import BUCKET_OPTIONS, CHART_TYPES, RANGE_OPTIONS, get_chart
from app.models import (
    APPLICATION_STATUS_VALUES,
    Application,
    ProductivityEvent,
    RecruiterEmail,
    ResumeAsset,
    UserSettings,
)

OTHER_OWNER = "someone-else"


class GetChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.charts.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        now = datetime.now(UTC)
        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            db.add(ResumeAsset(
                id=1, owner_id=settings.owner_id, file_path="r.pdf", file_name="java.pdf",
                sha256="a" * 64, version=1,
            ))
            for index in range(3):
                db.add(ProductivityEvent(
                    owner_id=settings.owner_id, event_type="approved_sent", event_source="test",
                    weight=1.0, occurred_at=now - timedelta(hours=index + 1),
                ))
            db.add(ProductivityEvent(
                owner_id=OTHER_OWNER, event_type="approved_sent", event_source="test",
                weight=1.0, occurred_at=now - timedelta(hours=1),
            ))
            for state, count in (("needs_review", 2), ("approved_sent", 1)):
                for index in range(count):
                    db.add(RecruiterEmail(
                        owner_id=settings.owner_id, sender=f"{state}{index}@example.com",
                        subject="Java", body="b", state=state, created_at=now - timedelta(hours=1),
                    ))
            db.add(RecruiterEmail(
                owner_id=OTHER_OWNER, sender="them@example.com", subject="Java", body="b",
                state="needs_review", created_at=now - timedelta(hours=1),
            ))
            for index, status in enumerate(("contacted", "interview_1", "contacted")):
                db.add(Application(
                    owner_id=settings.owner_id, resume_asset_id=1, resume_version_snapshot=1,
                    resume_file_name_snapshot="java.pdf", resume_sha256_snapshot="b" * 64,
                    recruiter_opportunity_id=1, recruiter_contact_id=1,
                    status=status, dedupe_key=f"app-{index}",
                ))
            db.add(Application(
                owner_id=OTHER_OWNER, resume_asset_id=1, resume_version_snapshot=1,
                resume_file_name_snapshot="x.pdf", resume_sha256_snapshot="c" * 64,
                recruiter_opportunity_id=1, recruiter_contact_id=1,
                status="contacted", dedupe_key="their-app",
            ))
            db.commit()

    def test_activity_trend_counts_this_owners_sends_only(self) -> None:
        payload = get_chart("activity_trend", range="current_day", bucket="hour")

        self.assertEqual(payload["action"], "render_chart")
        self.assertEqual(payload["chart_type"], "activity_trend")
        # Three of this owner's events; the fourth belongs to someone else.
        self.assertEqual(sum(int(point["value"]) for point in payload["series"]), 3)

    def test_empty_buckets_are_present_rather_than_omitted(self) -> None:
        payload = get_chart("activity_trend", range="current_day", bucket="hour")

        self.assertGreater(len(payload["series"]), 3)
        self.assertTrue(any(int(point["value"]) == 0 for point in payload["series"]))

    def test_candidate_states_are_owner_scoped(self) -> None:
        payload = get_chart("candidate_states", range="current_year")

        values = {str(point["label"]): point["value"] for point in payload["series"]}
        self.assertEqual(values["Needs review"], 2)
        self.assertEqual(values["Approved and sent"], 1)

    def test_application_pipeline_follows_the_pipelines_own_stage_order(self) -> None:
        payload = get_chart("application_pipeline")

        labels = [str(point["label"]) for point in payload["series"]]
        order = [status.replace("_", " ").capitalize() for status in APPLICATION_STATUS_VALUES]
        self.assertEqual(labels, [label for label in order if label in labels])
        values = {str(point["label"]): point["value"] for point in payload["series"]}
        self.assertEqual(values["Contacted"], 2)
        self.assertEqual(values["Interview 1"], 1)

    def test_max_value_is_computed_server_side(self) -> None:
        payload = get_chart("candidate_states", range="current_year")

        self.assertEqual(payload["max_value"], max(int(p["value"]) for p in payload["series"]))

    def test_funnel_stages_are_cumulative_and_carry_a_rate(self) -> None:
        payload = get_chart("resume_funnel", subject_id=1)

        labels = [str(point["label"]) for point in payload["series"]]
        self.assertEqual(labels, ["Submitted", "Viewed", "Shortlisted", "Interview", "Offered", "Hired"])
        self.assertIsNone(payload["series"][0]["rate_of_previous"])

    def test_resume_funnel_without_a_subject_asks_for_it(self) -> None:
        payload = get_chart("resume_funnel")

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["subject_id"])

    def test_every_chart_carries_provenance_even_when_empty(self) -> None:
        for chart in CHART_TYPES:
            with self.subTest(chart=chart):
                payload = get_chart(chart, range="last_1h", subject_id=1)
                self.assertIn("provenance", payload)
                self.assertTrue(payload["provenance"]["metric"])
                self.assertTrue(payload["provenance"]["assumptions"])

    def test_an_empty_range_returns_an_empty_series_not_an_error(self) -> None:
        payload = get_chart("candidate_states", range="last_1h")

        self.assertNotIn("error", payload)
        self.assertEqual(payload["series"], [])
        self.assertEqual(payload["max_value"], 0)
        self.assertTrue(payload["provenance"]["metric"])

    def test_unknown_chart_range_and_bucket_are_refused_with_the_valid_list(self) -> None:
        self.assertEqual(get_chart("pie")["charts"], list(CHART_TYPES))
        self.assertEqual(get_chart("activity_trend", range="last_century")["ranges"], list(RANGE_OPTIONS))
        self.assertEqual(get_chart("activity_trend", bucket="fortnight")["buckets"], list(BUCKET_OPTIONS))

    def test_the_extracted_trend_matches_the_route(self) -> None:
        # R4: the extracted function and /analytics/trend must not drift.
        from app.main import productivity_trend

        with self.SessionLocal() as db:
            route = productivity_trend(range="current_day", bucket="hour", db=db)

        tool = get_chart("activity_trend", range="current_day", bucket="hour")
        self.assertEqual(
            [point.sent_count for point in route.bars],
            [int(point["value"]) for point in tool["series"]],
        )


if __name__ == "__main__":
    unittest.main()
