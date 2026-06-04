import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.collector import CollectedPage
from app.external_feeds import service as external_feed_service_module
from app.external_feeds.service import ExternalFeedService
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import EmployerNumber, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, ResumeAsset, UserSettings


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
                    nvoids_locations="",
                    qualification_threshold=0.0,
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

    def _add_resume(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        with self.SessionLocal() as db:
            db.add(
                ResumeAsset(
                    owner_id=main.settings.owner_id,
                    file_path=path,
                    file_name="resume.pdf",
                    mime_type="application/pdf",
                    sha256="resume123",
                    version=1,
                    is_current=True,
                    semantic_embedding=None,
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
        self.assertEqual(payload["skipped_location_count"], 0)
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

    def test_manual_sync_keeps_nvoids_candidate_but_skips_unknown_phone_bridge(self) -> None:
        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            ext_rows = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .order_by(ExternalOpportunity.id.asc())
                .all()
            )
            self.assertGreaterEqual(len(ext_rows), 1)
            self.assertTrue(all((row.bridge_status or "") == "ignored_no_phone" for row in ext_rows))

            email_rows = (
                db.query(RecruiterEmail)
                .filter(
                    RecruiterEmail.owner_id == main.settings.owner_id,
                    RecruiterEmail.state == "needs_review",
                    RecruiterEmail.source == "nvoids",
                )
                .all()
            )
            self.assertGreaterEqual(len(email_rows), 1)

            recruiter_numbers = db.query(RecruiterNumber).filter(RecruiterNumber.owner_id == main.settings.owner_id).all()
            recruiter_opportunities = (
                db.query(RecruiterOpportunity)
                .filter(RecruiterOpportunity.owner_id == main.settings.owner_id, RecruiterOpportunity.source_type == "nvoids")
                .all()
            )
            self.assertEqual(recruiter_numbers, [])
            self.assertEqual(recruiter_opportunities, [])

    def test_settings_round_trip_includes_nvoids_locations(self) -> None:
        res = self.client.put(
            "/settings",
            json={
                "enabled": True,
                "gmail_query": "is:unread",
                "default_gmail_query": "is:unread",
                "saved_gmail_queries": [],
                "mail_date": None,
                "default_date_mode": "today",
                "min_salary": None,
                "accepted_locations": [],
                "visa_required_allowed": False,
                "remote_preference": "any",
                "role_keywords": [],
                "must_have_skills": [],
                "employer_domains": [],
                "free_text_guidance": "",
                "qualification_threshold": 0.6,
                "feature_auto_polling": False,
                "feature_auto_poll_interval_minutes": 10,
                "feature_nvoids_enabled": True,
                "feature_nvoids_auto_sync": False,
                "feature_nvoids_poll_interval_minutes": 30,
                "nvoids_batch_limit": 10,
                "nvoids_locations": ["texas", "remote"],
                "feature_auto_send": False,
                "feature_retry_queue": False,
                "feature_ai_enabled": False,
                "feature_semantic_enabled": False,
                "fallback_draft_template": "",
                "signature_name": "",
                "signature_phone": "",
                "signature_email": "",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["nvoids_locations"], ["texas", "remote"])

    def test_sync_skips_rows_outside_nvoids_location_filter(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.nvoids_locations = "remote"
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(payload["fetched_count"], 2)
        self.assertEqual(payload["created_count"], 1)
        self.assertEqual(payload["skipped_location_count"], 1)

        with self.SessionLocal() as db:
            rows = db.query(ExternalOpportunity).filter(ExternalOpportunity.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].location, "Remote, USA")

    def test_sync_uses_ai_draft_and_semantic_metadata_when_enabled(self) -> None:
        self._add_resume()
        original_generate = external_feed_service_module.generate_reply_with_ai_or_fallback
        original_compute = main.external_feed_service.scoring_runtime.compute_blended_ai_score
        try:
            with self.SessionLocal() as db:
                settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
                assert settings is not None
                settings.feature_ai_enabled = True
                settings.feature_semantic_enabled = True
                settings.qualification_threshold = 0.6
                db.commit()

            external_feed_service_module.generate_reply_with_ai_or_fallback = lambda **_kwargs: SimpleNamespace(
                draft_text="AI draft for Nvoids",
                source="deepseek",
                ai_model="deepseek-chat",
                ai_error=None,
                resume_context_status="injected",
            )
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = lambda **_kwargs: (
                0.92,
                "strong match",
                "v2_rules_plus_semantic",
                "[0.1,0.2]",
                "[0.3,0.4]",
                SimpleNamespace(
                    input_source="latest_block",
                    input_chars=120,
                    chunks=2,
                    fallback_reason=None,
                    keyword_source="thread_carry_forward",
                    thread_snapshot_used=True,
                    thread_snapshot_email_id=77,
                ),
            )

            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)
            with self.SessionLocal() as db:
                row = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .order_by(RecruiterEmail.id.desc())
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.state, "needs_review")
                self.assertEqual(row.draft_reply, "AI draft for Nvoids")
                self.assertEqual(row.draft_source, "deepseek")
                self.assertEqual(row.draft_model, "deepseek-chat")
                self.assertEqual(row.draft_resume_context_status, "injected")
                self.assertEqual(row.ai_score_source, "v2_rules_plus_semantic")
                self.assertEqual(row.semantic_input_source, "latest_block")
                self.assertEqual(row.semantic_chunks, 2)
                self.assertEqual(row.semantic_embedding, "[0.1,0.2]")
        finally:
            external_feed_service_module.generate_reply_with_ai_or_fallback = original_generate
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = original_compute

    def test_sync_queues_rules_only_with_missing_resume_when_ai_enabled(self) -> None:
        original_compute = main.external_feed_service.scoring_runtime.compute_blended_ai_score
        try:
            with self.SessionLocal() as db:
                settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
                assert settings is not None
                settings.feature_ai_enabled = True
                settings.feature_semantic_enabled = False
                settings.qualification_threshold = 0.6
                db.commit()

            main.external_feed_service.scoring_runtime.compute_blended_ai_score = lambda **_kwargs: (
                0.91,
                "strong match",
                "v1_rules_plus_ai",
                None,
                None,
                SimpleNamespace(input_source="semantic_disabled", input_chars=0, chunks=0, fallback_reason=None),
            )
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                row = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .order_by(RecruiterEmail.id.desc())
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.draft_source, "rules_only")
                self.assertEqual(row.draft_resume_context_status, "missing_resume")
        finally:
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = original_compute

    def test_sync_skips_non_qualified_rows_instead_of_queueing_them(self) -> None:
        original_compute = main.external_feed_service.scoring_runtime.compute_blended_ai_score
        try:
            with self.SessionLocal() as db:
                settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
                assert settings is not None
                settings.qualification_threshold = 0.75
                db.commit()

            main.external_feed_service.scoring_runtime.compute_blended_ai_score = lambda **_kwargs: (
                0.2,
                "low match",
                "v1_rules_plus_ai",
                None,
                None,
                SimpleNamespace(input_source="semantic_disabled", input_chars=0, chunks=0, fallback_reason=None),
            )
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                rows = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .all()
                )
                self.assertEqual(rows, [])
        finally:
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = original_compute

    def test_sync_skips_queue_creation_when_employer_pool_cc_missing(self) -> None:
        with self.SessionLocal() as db:
            db.query(EmployerNumber).delete()
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            rows = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                .all()
            )
            self.assertEqual(rows, [])

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


class ExternalFeedServiceQueryTests(unittest.TestCase):
    def test_build_nvoids_query_uses_default_when_locations_empty(self) -> None:
        service = ExternalFeedService()
        self.assertEqual(service.build_nvoids_query([]), "(tx or texas) and java and spring* not(*js)")

    def test_build_nvoids_query_includes_location_tokens(self) -> None:
        service = ExternalFeedService()
        self.assertEqual(service.build_nvoids_query(["texas", "remote"]), "(texas or remote) and java and spring* not(*js)")

    def test_row_matches_locations_treats_remote_as_explicit_token(self) -> None:
        service = ExternalFeedService()
        self.assertTrue(service.row_matches_locations("Remote, Remote, USA", ["remote"]))
        self.assertFalse(service.row_matches_locations("Dallas, Texas, USA", ["remote"]))


if __name__ == "__main__":
    unittest.main()
