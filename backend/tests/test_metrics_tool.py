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
from app.mcp_server.tools.metrics import METRICS, RANGE_OPTIONS, get_metrics
from app.models import Application, RecruiterEmail, UserSettings

OTHER_OWNER = "someone-else"


class GetMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.metrics.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        now = datetime.now(UTC)
        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            for state, count in (("needs_review", 3), ("approved_sent", 2), ("failed", 1)):
                for index in range(count):
                    db.add(RecruiterEmail(
                        owner_id=settings.owner_id,
                        sender=f"{state}{index}@example.com",
                        subject="Java Engineer",
                        body="body",
                        state=state,
                        created_at=now - timedelta(hours=1),
                    ))
            # Another owner's rows must never reach a metric.
            db.add(RecruiterEmail(
                owner_id=OTHER_OWNER,
                sender="other@example.com",
                subject="Java Engineer",
                body="body",
                state="needs_review",
                created_at=now - timedelta(hours=1),
            ))

            def application(owner_id: str, status: str, dedupe_key: str, age_days: int) -> Application:
                return Application(
                    owner_id=owner_id,
                    resume_asset_id=1,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot="resume.pdf",
                    resume_sha256_snapshot="b" * 64,
                    recruiter_opportunity_id=1,
                    recruiter_contact_id=1,
                    status=status,
                    dedupe_key=dedupe_key,
                    status_changed_at=now - timedelta(days=age_days),
                )

            db.add(application(settings.owner_id, "contacted", "stale-1", 40))
            db.add(application(settings.owner_id, "contacted", "fresh-1", 1))
            db.add(application(settings.owner_id, "hired", "closed-1", 90))
            db.add(application(OTHER_OWNER, "contacted", "other-stale", 40))
            db.commit()

    def cards(self, payload: dict) -> dict[str, object]:
        return {str(card["label"]): card["value"] for card in payload["cards"]}

    def test_candidate_queue_counts_are_correct_and_owner_scoped(self) -> None:
        payload = get_metrics("candidate_queue", range="current_year")

        values = self.cards(payload)
        self.assertEqual(values["Needs review"], 3)
        self.assertEqual(values["Approved and sent"], 2)
        self.assertEqual(values["Failed mapping"], 1)
        # 3 + 2 + 1, never 7: the other owner's needs_review row is excluded.
        self.assertEqual(payload["provenance"]["row_count"], 6)

    def test_stale_applications_measures_from_status_changed_at(self) -> None:
        payload = get_metrics("stale_applications", days=14)

        values = self.cards(payload)
        # One open application 40 days stale; the 1-day-old one and the hired
        # one are both excluded, and the other owner's stale row never counts.
        self.assertEqual(values["Stale (14d+)"], 1)
        self.assertEqual(values["Open applications"], 2)

    def test_stale_window_is_honoured(self) -> None:
        self.assertEqual(self.cards(get_metrics("stale_applications", days=60))["Stale (60d+)"], 0)
        self.assertEqual(self.cards(get_metrics("stale_applications", days=7))["Stale (7d+)"], 1)

    def test_stale_days_are_clamped(self) -> None:
        self.assertIn("Stale (1d+)", self.cards(get_metrics("stale_applications", days=0)))
        self.assertIn("Stale (365d+)", self.cards(get_metrics("stale_applications", days=99999)))

    def test_pipeline_summary_excludes_other_owners(self) -> None:
        payload = get_metrics("pipeline_summary")

        # Three applications belong to this owner; the fourth does not.
        self.assertEqual(payload["provenance"]["row_count"], 3)

    def test_every_metric_carries_a_complete_provenance_block(self) -> None:
        for metric in METRICS:
            with self.subTest(metric=metric):
                payload = get_metrics(metric)

                self.assertEqual(payload["action"], "render_metric_cards")
                block = payload["provenance"]
                self.assertTrue(block["metric"])
                self.assertTrue(block["source"].startswith("get_metrics/"))
                self.assertIsInstance(block["row_count"], int)
                self.assertIsInstance(block["date_range"], dict)
                self.assertIsInstance(block["filters"], dict)
                # Every exclusion the query makes has to be stated: the user
                # cannot see them any other way.
                self.assertTrue(block["assumptions"])

    def test_date_range_matches_the_range_that_ran(self) -> None:
        payload = get_metrics("candidate_queue", range="current_year")

        self.assertEqual(payload["provenance"]["filters"], {"range": "current_year"})
        self.assertEqual(
            payload["provenance"]["date_range"]["from"],
            datetime(datetime.now(UTC).year, 1, 1, tzinfo=UTC).date().isoformat(),
        )

    def test_unknown_metric_returns_the_valid_list(self) -> None:
        payload = get_metrics("vibes")

        self.assertIn("error", payload)
        self.assertEqual(payload["metrics"], sorted(METRICS))
        self.assertNotIn("action", payload)

    def test_unknown_range_is_refused(self) -> None:
        payload = get_metrics("candidate_queue", range="last_century")

        self.assertIn("error", payload)
        self.assertEqual(payload["ranges"], list(RANGE_OPTIONS))
        self.assertNotIn("action", payload)

    def test_drill_targets_name_real_pages(self) -> None:
        from app.mcp_server.tools.navigation import NAVIGABLE_PAGES

        for metric in METRICS:
            for card in get_metrics(metric)["cards"]:
                if card["drill_to"] is not None:
                    with self.subTest(metric=metric, label=card["label"]):
                        self.assertIn(card["drill_to"]["page"], NAVIGABLE_PAGES)


if __name__ == "__main__":
    unittest.main()
