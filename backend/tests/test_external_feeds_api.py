import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.collector import CollectedPage
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import EmployerNumber, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, UserSettings


class _FakeCollector:
    def fetch_page(self, _base_url: str, page: int) -> CollectedPage:
        if page > 0:
            return CollectedPage(url="https://www.nvoids.com/index.jsp?p=1", html="<html><body></body></html>")
        html = """
        <table>
          <tr><td><a href='job1.jsp?id=1'>Senior Python Developer</a></td><td>Dallas, Texas, USA</td><td>11:00 PM 07-May-26</td></tr>
          <tr><td><a href='job2.jsp?id=2'>React Developer</a></td><td>Remote, USA</td><td>10:00 PM 07-May-26</td></tr>
        </table>
        """
        return CollectedPage(url="https://www.nvoids.com/index.jsp", html=html)

    def fetch_search_page(self, *, query: str, hotlist_mode: str = "Exclude Hotlists", page: int = 0) -> CollectedPage:
        _ = query, hotlist_mode
        return self.fetch_page("", page)

    def fetch_detail_page(self, *, url: str) -> CollectedPage:
        html = f"""
        <html><body>
        <table>
          <tr><td>Email: recruiter_{'1' if 'id=1' in url else '2'}@example.com</td></tr>
          <tr><td>From: Sarika Singh</td></tr>
          <tr><td>Job Description: Java Spring Boot role in Texas</td></tr>
        </table>
        </body></html>
        """
        return CollectedPage(url=url, html=html)


class ExternalFeedsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        main.external_feed_service.collector = _FakeCollector()
        self.client = TestClient(main.app)
        with self.SessionLocal() as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    enabled=True,
                    gmail_query="is:unread in:inbox recruiter",
                    default_gmail_query="is:unread in:inbox recruiter",
                    saved_gmail_queries_json="[]",
                    feature_nvoids_enabled=True,
                    feature_nvoids_auto_sync=False,
                    feature_nvoids_poll_interval_minutes=30,
                    nvoids_batch_limit=10,
                )
            )
            employer_source_email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Employer Contact <employer.cc@example.com>",
                subject="Employer thread",
                body="Employer contact seed",
                role="",
                location="",
                salary_text="",
                skills_text="",
                score=0,
                decision="auto_rejected",
                state="failed",
                source="gmail",
            )
            db.add(employer_source_email)
            db.flush()
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="+12145550199",
                    display_phone_number="+1 214 555 0199",
                    owner_name="Employer Contact",
                    company="PoolCo",
                    source_email_id=employer_source_email.id,
                )
            )
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_manual_sync_returns_summary_and_runs(self) -> None:
        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(payload["source_type"], "nvoids")
        self.assertGreaterEqual(payload["fetched_count"], 2)
        with self.SessionLocal() as db:
            rows = (
                db.query(RecruiterEmail)
                .filter(
                    RecruiterEmail.owner_id == main.settings.owner_id,
                    RecruiterEmail.state == "needs_review",
                    RecruiterEmail.source == "nvoids",
                )
                .all()
            )
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(rows[0].cc_email, "employer.cc@example.com")
            self.assertEqual(rows[0].source, "nvoids")
            self.assertTrue((rows[0].external_thread_id or "").startswith("https://"))
            self.assertTrue((rows[0].external_message_id or "").startswith("nvoids:"))

        runs = self.client.get("/external-feeds/runs")
        self.assertEqual(runs.status_code, 200, runs.text)
        run_items = runs.json()
        self.assertGreaterEqual(len(run_items), 1)
        self.assertEqual(run_items[0]["source_type"], "nvoids")

    def test_backfill_phones_clears_noise_phone_and_normalizes_bridge_number(self) -> None:
        with self.SessionLocal() as db:
            source = ExternalFeedSource(owner_id=main.settings.owner_id, source_type="nvoids", base_url="https://nvoids.com")
            db.add(source)
            db.flush()
            ext = ExternalOpportunity(
                owner_id=main.settings.owner_id,
                feed_source_id=source.id,
                source_type="nvoids",
                external_post_id="nvoids:3385623",
                source_url="https://nvoids.com/job_details.jsp?id=3385623&uid=abc",
                recruiter_email="nitin.tehriya@tekinspirations.com",
                recruiter_phone="4849540944",
                recruiter_name="Unknown",
                role="Full Stack Developer",
                location="Lansing, Michigan, USA",
                raw_body="raw",
                raw_html=(
                    "<table><tr><td>Email: nitin.tehriya@tekinspirations.com</td></tr>"
                    "<tr><td>From: Nitin Tehriya</td></tr></table>"
                    "<div>Time Taken: 0 4849540944</div>"
                ),
                dedupe_hash="hash-1",
                parse_confidence=0.7,
                bridge_status="bridged",
            )
            db.add(ext)
            db.flush()
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="4849540944",
                display_phone_number="4849540944",
                recruiter_name="Unknown",
                company="Unknown",
                designation="Recruiter",
                recruiter_email="nitin.tehriya@tekinspirations.com",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.flush()
            db.add(
                RecruiterOpportunity(
                    owner_id=main.settings.owner_id,
                    recruiter_number_id=recruiter.id,
                    source_email_id=None,
                    gmail_message_id="nvoids:nvoids:3385623",
                    source_type="nvoids",
                    source_url=ext.source_url,
                    external_opportunity_id=ext.id,
                    email_subject=ext.role,
                    email_sender=ext.recruiter_email,
                    gmail_open_url=ext.source_url,
                    job_title=ext.role,
                    client="",
                    location=ext.location,
                    work_mode="Onsite",
                    visa_restrictions="Mentioned",
                    extracted_skills="Java",
                    evidence="External feed: nvoids",
                    status="New",
                )
            )
            db.commit()

        res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertGreaterEqual(payload["scanned"], 1)
        self.assertGreaterEqual(payload["corrected"], 1)

        with self.SessionLocal() as db:
            updated_ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.external_post_id == "nvoids:3385623").first()
            self.assertIsNotNone(updated_ext)
            assert updated_ext is not None
            self.assertEqual(updated_ext.recruiter_phone, "")
            self.assertEqual(updated_ext.recruiter_name, "Nitin Tehriya")
            updated_recruiter = db.query(RecruiterNumber).filter(RecruiterNumber.id == 1).first()
            self.assertIsNotNone(updated_recruiter)
            assert updated_recruiter is not None
            self.assertEqual(updated_recruiter.display_phone_number, "Unknown")
            self.assertTrue(updated_recruiter.normalized_phone_number.startswith("nvoids-"))


if __name__ == "__main__":
    unittest.main()
