import os
import tempfile
import unittest
from types import SimpleNamespace

import httpx
os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.automation.queue_preparation import prepend_nvoids_listing_line
from app.db import Base
from app.external_feeds.collector import CollectedPage
from app.external_feeds.parser import parse_nvoids_detail
from app.external_feeds import service as external_feed_service_module
from app.external_feeds.service import ExternalFeedService
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import AttachmentAsset, CustomSkillTaxonomyEntry, EmployerNumber, NumberReviewQueue, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, ResumeAsset, UserSettings


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
        title = "Senior Python Developer"
        location = "Dallas, Texas, USA"
        if "id=2" in url:
            title = "React Developer"
            location = "Remote, USA"
        jd_body = (
            f"Role: {title}<br>"
            f"Client: ExampleCo<br>"
            f"Location: {location}<br>"
            "Must have skills<br>"
            "Java, Spring Boot, Kafka, AWS"
        )
        html = f"""
        <html><body>
        <table>
          <tr><td>{title}</td></tr>
          <tr><td>Email: recruiter_{'1' if 'id=1' in url else '2'}@example.com</td></tr>
          <tr><td>{jd_body}</td></tr>
          <tr><td>From: Sarika Singh</td></tr>
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

    def _seed_nvoids_placeholder_recruiter(
        self,
        *,
        normalized_phone_number: str = "nvoids-123",
        display_phone_number: str = "Unknown",
        recruiter_email: str = "placeholder@example.com",
        recruiter_name: str = "Unknown",
        company: str = "Unknown",
        external_phone: str = "",
    ) -> tuple[int, int, int]:
        with self.SessionLocal() as db:
            source = ExternalFeedSource(owner_id=main.settings.owner_id, source_type="nvoids", base_url="https://nvoids.com")
            db.add(source)
            db.flush()
            raw_html = "<table><tr><td>Email: placeholder@example.com</td></tr></table>"
            if external_phone:
                raw_html = (
                    "<table>"
                    "<tr><td>Email: placeholder@example.com</td></tr>"
                    f"<tr><td>Phone: {external_phone}</td></tr>"
                    "</table>"
                )
            ext = ExternalOpportunity(
                owner_id=main.settings.owner_id,
                feed_source_id=source.id,
                source_type="nvoids",
                external_post_id="nvoids:seed-placeholder",
                source_url="https://nvoids.com/job_details.jsp?id=seed&uid=abc",
                recruiter_email=recruiter_email,
                recruiter_phone=external_phone,
                recruiter_name=recruiter_name,
                company=company,
                role="Seed Role",
                location="Remote, USA",
                raw_body="raw",
                raw_html=raw_html,
                dedupe_hash=f"hash-{normalized_phone_number}",
                parse_confidence=0.7,
                bridge_status="bridged",
            )
            db.add(ext)
            db.flush()
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number=normalized_phone_number,
                display_phone_number=display_phone_number,
                recruiter_name=recruiter_name,
                company=company,
                designation="Recruiter",
                recruiter_email=recruiter_email,
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.flush()
            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="nvoids:nvoids:seed-placeholder",
                source_type="nvoids",
                source_url=ext.source_url,
                external_opportunity_id=ext.id,
                email_subject=ext.role,
                email_sender=ext.recruiter_email,
                gmail_open_url=ext.source_url,
                job_title=ext.role,
                client=ext.company,
                location=ext.location,
                work_mode="Remote",
                visa_restrictions="Mentioned",
                extracted_skills="Java",
                evidence="External feed: nvoids",
                status="New",
            )
            db.add(opportunity)
            db.commit()
            return recruiter.id, opportunity.id, ext.id

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
                    is_enabled=True,
                    is_current=True,
                    semantic_embedding=None,
                )
            )
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_parse_nvoids_detail_extracts_jd_body_from_literal_third_detail_row(self) -> None:
        html = """
        <html><body>
        <a href="/home">Home</a>
        <table border="1">
          <tr><td>Principal Software Engineer Java</td></tr>
          <tr><td>Email: recruiter@example.com</td></tr>
          <tr><td>Role: Principal Software Engineer Java<br>Client: Acme<br>Location: Gwynn Oak, MD<br>Must have skills<br>Java, Spring Boot, Kafka, REST</td></tr>
          <tr><td>recruiter@example.com | View All</td></tr>
        </table>
        </body></html>
        """

        detail = parse_nvoids_detail(html, "Fallback Title", "Fallback Location")

        self.assertIn("Role: Principal Software Engineer Java", detail.jd_body)
        self.assertIn("Client: Acme", detail.jd_body)
        self.assertIn("Java, Spring Boot, Kafka, REST", detail.jd_body)
        self.assertEqual(detail.jd_body_source, "nvoids_detail_table_row_3")
        self.assertNotIn("Email:", detail.jd_body)
        self.assertNotIn("View All", detail.jd_body)

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

    def test_manual_sync_standardizes_nvoids_recruiter_phone_display(self) -> None:
        class _PhoneCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                phone = "240-657-1540"
                if "id=2" in url:
                    phone = "+1 (201) 277-2419"
                html = f"""
                <html><body>
                <table>
                  <tr><td>Email: recruiter@example.com</td></tr>
                  <tr><td>From: Sarika Singh</td></tr>
                  <tr><td>Phone: {phone}</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        main.external_feed_service.collector = _PhoneCollector()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            rows = (
                db.query(RecruiterNumber)
                .filter(RecruiterNumber.owner_id == main.settings.owner_id)
                .order_by(RecruiterNumber.id.asc())
                .all()
            )
            self.assertEqual([row.display_phone_number for row in rows], ["(240) 657-1540", "(201) 277-2419"])

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
                "feature_ai_extractor_enabled": True,
                "feature_semantic_enabled": False,
                "draft_text_size": "huge",
                "fallback_draft_template": "",
                "signature_name": "",
                "signature_phone": "",
                "signature_email": "",
                "resume_display_name": "Chaithanya Dheeraj Resume",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["nvoids_locations"], ["texas", "remote"])
        self.assertTrue(payload["feature_ai_extractor_enabled"])
        self.assertEqual(payload["draft_text_size"], "huge")
        self.assertEqual(payload["resume_display_name"], "Chaithanya Dheeraj Resume")

    def test_settings_reject_invalid_draft_text_size(self) -> None:
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
                "nvoids_locations": [],
                "feature_auto_send": False,
                "feature_retry_queue": False,
                "feature_ai_enabled": False,
                "feature_ai_extractor_enabled": False,
                "feature_semantic_enabled": False,
                "draft_text_size": "gigantic",
                "fallback_draft_template": "",
                "signature_name": "",
                "signature_phone": "",
                "signature_email": "",
                "resume_display_name": "",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 422, res.text)

    def test_attachment_file_crud_round_trip(self) -> None:
        first_fd, first_path = tempfile.mkstemp(suffix=".txt")
        second_fd, second_path = tempfile.mkstemp(suffix=".pdf")
        os.close(first_fd)
        os.close(second_fd)
        try:
            with open(first_path, "wb") as handle:
                handle.write(b"first attachment")
            with open(second_path, "wb") as handle:
                handle.write(b"%PDF-1.4 second attachment")
            with open(first_path, "rb") as first_handle, open(second_path, "rb") as second_handle:
                upload = self.client.post(
                    "/settings/attachments",
                    files=[
                        ("files", ("notes.txt", first_handle, "text/plain")),
                        ("files", ("portfolio.pdf", second_handle, "application/pdf")),
                    ],
                )
            self.assertEqual(upload.status_code, 200, upload.text)
            upload_items = upload.json()
            self.assertEqual(len(upload_items), 2)
            attachment_id = upload_items[0]["id"]

            listed = self.client.get("/settings/attachments")
            self.assertEqual(listed.status_code, 200, listed.text)
            listed_items = listed.json()
            self.assertEqual({item["file_name"] for item in listed_items}, {"notes.txt", "portfolio.pdf"})
            self.assertTrue(all(item["is_enabled"] for item in listed_items))

            toggled = self.client.patch(f"/settings/attachments/{attachment_id}", json={"is_enabled": False})
            self.assertEqual(toggled.status_code, 200, toggled.text)
            self.assertFalse(toggled.json()["is_enabled"])

            deleted = self.client.delete(f"/settings/attachments/{attachment_id}")
            self.assertEqual(deleted.status_code, 200, deleted.text)

            with self.SessionLocal() as db:
                rows = db.query(AttachmentAsset).filter(AttachmentAsset.owner_id == main.settings.owner_id).all()
                self.assertEqual(len(rows), 1)
        finally:
            if os.path.exists(first_path):
                os.unlink(first_path)
            if os.path.exists(second_path):
                os.unlink(second_path)

    def test_resume_database_toggle_and_delete_maintains_legacy_current_fallback(self) -> None:
        first_fd, first_path = tempfile.mkstemp(suffix=".pdf")
        second_fd, second_path = tempfile.mkstemp(suffix=".pdf")
        os.close(first_fd)
        os.close(second_fd)
        try:
            with open(first_path, "wb") as handle:
                handle.write(b"%PDF-1.4 first resume")
            with open(second_path, "wb") as handle:
                handle.write(b"%PDF-1.4 second resume")

            with open(first_path, "rb") as first_handle:
                first_upload = self.client.post(
                    "/settings/resume",
                    files={"file": ("resume-one.pdf", first_handle, "application/pdf")},
                    data={"skills_text": "java, spring boot"},
                )
            self.assertEqual(first_upload.status_code, 200, first_upload.text)
            first_resume = first_upload.json()
            self.assertTrue(first_resume["is_enabled"])
            self.assertTrue(first_resume["is_current"])
            self.assertEqual(first_resume["skills_text"], "Java, Spring Boot")

            with open(second_path, "rb") as second_handle:
                second_upload = self.client.post(
                    "/settings/resume",
                    files={"file": ("resume-two.pdf", second_handle, "application/pdf")},
                    data={"skills_text": "java, angular"},
                )
            self.assertEqual(second_upload.status_code, 200, second_upload.text)
            second_resume = second_upload.json()
            self.assertTrue(second_resume["is_enabled"])
            self.assertTrue(second_resume["is_current"])
            self.assertEqual(second_resume["skills_text"], "Java, Angular")

            listed = self.client.get("/settings/resumes")
            self.assertEqual(listed.status_code, 200, listed.text)
            items = listed.json()
            self.assertEqual(len(items), 2)
            self.assertEqual(sum(1 for item in items if item["is_current"]), 1)
            self.assertEqual(sum(1 for item in items if item["is_enabled"]), 2)

            updated_skills = self.client.patch(
                f"/settings/resumes/{second_resume['id']}",
                json={"skills_text": "java, angular, microservices"},
            )
            self.assertEqual(updated_skills.status_code, 200, updated_skills.text)
            self.assertEqual(updated_skills.json()["skills_text"], "Java, Angular, Microservices")

            disabled = self.client.patch(f"/settings/resumes/{second_resume['id']}", json={"is_enabled": False})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertFalse(disabled.json()["is_enabled"])
            self.assertFalse(disabled.json()["is_current"])

            listed_after_disable = self.client.get("/settings/resumes")
            self.assertEqual(listed_after_disable.status_code, 200, listed_after_disable.text)
            items_after_disable = listed_after_disable.json()
            fallback = next(item for item in items_after_disable if item["file_name"] == "resume-one.pdf")
            self.assertTrue(fallback["is_current"])
            self.assertTrue(fallback["is_enabled"])

            deleted = self.client.delete(f"/settings/resumes/{fallback['id']}")
            self.assertEqual(deleted.status_code, 200, deleted.text)

            listed_after_delete = self.client.get("/settings/resumes")
            self.assertEqual(listed_after_delete.status_code, 200, listed_after_delete.text)
            final_items = listed_after_delete.json()
            self.assertEqual(len(final_items), 1)
            self.assertEqual(final_items[0]["file_name"], "resume-two.pdf")
            self.assertFalse(final_items[0]["is_enabled"])
            self.assertFalse(final_items[0]["is_current"])
        finally:
            if os.path.exists(first_path):
                os.unlink(first_path)
            if os.path.exists(second_path):
                os.unlink(second_path)

    def test_pending_skill_api_lists_unknown_parser_skills_until_approved(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="one@example.com",
                        subject="First",
                        body="Body",
                        role="Engineer",
                        location="Remote",
                        salary_text="",
                        skills_text="Java",
                        skills_json='{"skills_text":"Java, Temporal Workflow, Graph Orchestration","known":["Java"],"unknown":["Temporal Workflow","Temporal Workflow","Graph Orchestration"],"evidence":{"Temporal Workflow":"ai_extractor","Graph Orchestration":"ai_extractor"}}',
                        score=0,
                        decision="qualified",
                        state="needs_review",
                        source="gmail",
                        parser_details_json='{"unknown_skills":["Temporal Workflow","Temporal Workflow","Graph Orchestration"]}',
                    ),
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="two@example.com",
                        subject="Second",
                        body="Body",
                        role="Engineer",
                        location="Remote",
                        salary_text="",
                        skills_text="Java",
                        skills_json='{"skills_text":"Java, Temporal Workflow, Agent Studio","known":["Java"],"unknown":["Temporal Workflow","Agent Studio"],"evidence":{"Temporal Workflow":"ai_extractor","Agent Studio":"ai_extractor"}}',
                        score=0,
                        decision="qualified",
                        state="needs_review",
                        source="nvoids",
                        parser_details_json='{"unknown_skills":["Temporal Workflow","Agent Studio"]}',
                    ),
                ]
            )
            db.commit()

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        payload = pending.json()
        self.assertEqual(payload[0]["skill_name"], "Temporal Workflow")
        self.assertEqual(payload[0]["occurrence_count"], 2)
        self.assertEqual(len(payload[0]["candidate_ids"]), 2)
        self.assertEqual([item["skill_name"] for item in payload], ["Temporal Workflow", "Agent Studio", "Graph Orchestration"])

        approved = self.client.post(
            "/settings/skills/approve",
            json={"skill_name": "Temporal Workflow", "aliases": ["Temporal"], "category": "workflow"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        approved_payload = approved.json()
        self.assertEqual(approved_payload["canonical_name"], "Temporal Workflow")
        self.assertEqual(approved_payload["aliases"], ["Temporal"])
        self.assertEqual(approved_payload["status"], "approved")

        pending_after = self.client.get("/settings/skills/pending")
        self.assertEqual(pending_after.status_code, 200, pending_after.text)
        self.assertEqual([item["skill_name"] for item in pending_after.json()], ["Agent Studio", "Graph Orchestration"])

    def test_pending_skill_api_prefers_skills_json_unknown_over_legacy_parser_bucket(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="skillsjson@example.com",
                    subject="Structured",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java, Temporal Workflow",
                    skills_json='{"skills_text":"Java, Temporal Workflow","known":["Java"],"unknown":["Temporal Workflow"],"evidence":{"Temporal Workflow":"ai_extractor"}}',
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    parser_details_json='{"unknown_skills":["Legacy Ghost Skill"]}',
                )
            )
            db.commit()

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual([item["skill_name"] for item in pending.json()], ["Temporal Workflow"])

    def test_pending_skill_api_falls_back_to_legacy_unknown_skills_when_skills_json_missing(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="legacy@example.com",
                    subject="Legacy",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java",
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    parser_details_json='{"unknown_skills":["Legacy Graph Skill"]}',
                )
            )
            db.commit()

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual([item["skill_name"] for item in pending.json()], ["Legacy Graph Skill"])

    def test_dismissed_skill_is_suppressed_from_pending_results(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="dismiss@example.com",
                    subject="Dismiss",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java",
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    parser_details_json='{"unknown_skills":["Resume Ghost Skill"]}',
                )
            )
            db.commit()

        dismissed = self.client.post("/settings/skills/dismiss", json={"skill_name": "Resume Ghost Skill"})
        self.assertEqual(dismissed.status_code, 200, dismissed.text)
        self.assertEqual(dismissed.json()["status"], "dismissed")

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json(), [])

    def test_approved_skills_api_lists_only_approved_custom_entries(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    CustomSkillTaxonomyEntry(
                        owner_id=main.settings.owner_id,
                        canonical_name="Temporal Workflow",
                        aliases_json='["Temporal","Workflow Temporal"]',
                        category="workflow",
                        cluster_hint="custom_workflow",
                        status="approved",
                    ),
                    CustomSkillTaxonomyEntry(
                        owner_id=main.settings.owner_id,
                        canonical_name="Dismissed Skill",
                        aliases_json="[]",
                        category="custom",
                        cluster_hint=None,
                        status="dismissed",
                    ),
                ]
            )
            db.commit()

        listed = self.client.get("/settings/skills/approved")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["canonical_name"], "Temporal Workflow")
        self.assertEqual(payload[0]["aliases"], ["Temporal", "Workflow Temporal"])
        self.assertEqual(payload[0]["status"], "approved")

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
                self.assertTrue((row.external_thread_id or "").startswith("https://"))
                self.assertEqual(row.draft_reply, f"Nvoids Listing: {row.external_thread_id}\n\nAI draft for Nvoids")
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
                self.assertTrue((row.external_thread_id or "").startswith("https://"))
                self.assertTrue((row.draft_reply or "").startswith(f"Nvoids Listing: {row.external_thread_id}\n\n"))
        finally:
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = original_compute

    def test_prepend_nvoids_listing_line_skips_non_nvoids_urls_and_avoids_duplicates(self) -> None:
        listing_url = "https://nvoids.com/job_details.jsp?id=3435393&uid=abc"
        prefixed = prepend_nvoids_listing_line("Subject: Example\n\nHi,\n\nBody", listing_url)
        self.assertEqual(
            prefixed,
            f"Nvoids Listing: {listing_url}\n\nSubject: Example\n\nHi,\n\nBody",
        )
        self.assertEqual(prepend_nvoids_listing_line(prefixed, listing_url), prefixed)
        self.assertEqual(prepend_nvoids_listing_line("Hi,\n\nBody", "https://example.com/job_details.jsp?id=1"), "Hi,\n\nBody")
        self.assertEqual(prepend_nvoids_listing_line("Hi,\n\nBody", "nvoids:3435393"), "Hi,\n\nBody")
        self.assertEqual(prepend_nvoids_listing_line("Hi,\n\nBody", None), "Hi,\n\nBody")

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

    def test_sync_uses_canonical_nvoids_title_and_clean_body_for_rules_fallback(self) -> None:
        class _UglyNvoidsCollector(_FakeCollector):
            def fetch_search_page(self, *, query: str, hotlist_mode: str = "Exclude Hotlists", page: int = 0) -> CollectedPage:
                _ = query, hotlist_mode
                if page > 0:
                    return CollectedPage(url="https://nvoids.com/search_sph.jsp?p=1", html="<html><body></body></html>")
                html = """
                <table>
                  <tr><td><a href='job_details.jsp?id=3445247&uid=abc'>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX</a></td><td>Charlotte, North Carolina, USA</td><td>11:00 PM 07-May-26</td></tr>
                </table>
                """
                return CollectedPage(url="https://nvoids.com/search_sph.jsp", html=html)

            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                html = """
                <html><body>
                <a>Home</a>
                <table>
                  <tr><td>Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA</td></tr>
                  <tr><td>Email: saurabhampstek@gmail.com</td></tr>
                  <tr><td>http://bit.ly/4ey8w48</td></tr>
                  <tr><td>https://jobs.nvoids.com/job_details.jsp?id=3445247&uid=115e12ace9214a28804a59d7aa3da1ba</td></tr>
                  <tr><td>Hi,</td></tr>
                  <tr><td>Job description</td></tr>
                  <tr><td>Backend Development Design, develop, and maintain scalable backend services using Java, Spring Boot, and Microservices architecture.</td></tr>
                  <tr><td>Thanks and Regards</td></tr>
                  <tr><td>data-cfemail protected</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        original_collector = main.external_feed_service.collector
        try:
            main.external_feed_service.collector = _UglyNvoidsCollector()
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
                expected_role = "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX at Charlotte, North Carolina, USA"
                self.assertEqual(row.role, expected_role)
                self.assertIn(f"Subject: Application for {expected_role}", row.draft_reply or "")
                self.assertNotIn("<br", row.draft_reply or "")
                self.assertNotIn("data-cfemail", row.draft_reply or "")
                self.assertNotIn("Thanks and Regards", row.draft_reply or "")
        finally:
            main.external_feed_service.collector = original_collector

    def test_sync_uses_structured_nvoids_detail_email_role_and_location(self) -> None:
        class _StructuredNvoidsCollector(_FakeCollector):
            def fetch_search_page(self, *, query: str, hotlist_mode: str = "Exclude Hotlists", page: int = 0) -> CollectedPage:
                _ = query, hotlist_mode
                if page > 0:
                    return CollectedPage(url="https://nvoids.com/search_sph.jsp?p=1", html="<html><body></body></html>")
                html = """
                <table>
                  <tr><td><a href='job_details.jsp?id=3550001&uid=abc'>Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite</a></td><td>Irving, Texas, USA</td><td>04:49 AM 17-Jun-26</td></tr>
                </table>
                """
                return CollectedPage(url="https://nvoids.com/search_sph.jsp", html=html)

            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                html = """
                <html><body>
                <a href='index.jsp'>Home</a>
                <table border="1">
                  <tr><td>Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite at Irving, Texas, USA</td></tr>
                  <tr><td>Email: <a href='mailto:tanuja@digitaldhara.com'>tanuja@digitaldhara.com</a></td></tr>
                  <tr><td>Job Title:</td></tr>
                  <tr><td>GCP AI Engineer</td></tr>
                  <tr><td>Location: Irving, TX, or Charlotte NC - Onsite</td></tr>
                  <tr><td>Experience with Vertex AI, GKE, Python, and GenAI workflows.</td></tr>
                  <tr><td>tanuja@digitaldhara.com | View All</td></tr>
                  <tr><td>04:49 AM 17-Jun-26</td></tr>
                </table>
                <div>job_kill Pages not loading. Time Taken: 0. Footer Location: Dallas, Texas</div>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        original_collector = main.external_feed_service.collector
        try:
            main.external_feed_service.collector = _StructuredNvoidsCollector()
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                ext = (
                    db.query(ExternalOpportunity)
                    .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.external_post_id == "nvoids:3550001")
                    .first()
                )
                self.assertIsNotNone(ext)
                assert ext is not None
                self.assertEqual(ext.recruiter_email, "tanuja@digitaldhara.com")
                self.assertEqual(ext.role, "GCP AI Engineer")
                self.assertEqual(ext.location, "Irving, TX, or Charlotte NC - Onsite")
                self.assertIn("Vertex AI, GKE, Python, and GenAI workflows.", ext.raw_body)
                self.assertNotIn("job_kill", ext.raw_body)
                self.assertNotIn("View All", ext.raw_body)

                row = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .order_by(RecruiterEmail.id.desc())
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.recipient_email, "tanuja@digitaldhara.com")
                self.assertEqual(row.role, "GCP AI Engineer")
                self.assertIn("Subject: Application for GCP AI Engineer", row.draft_reply or "")
                self.assertNotIn("job_kill", row.draft_reply or "")
        finally:
            main.external_feed_service.collector = original_collector

    def test_sync_passes_ai_extractor_flag_and_source_hints_for_nvoids_candidates(self) -> None:
        parse_calls: list[dict[str, object]] = []

        def _fake_parse_email_with_details(subject: str, body: str, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
            source_hints = dict(kwargs.get("source_hints") or {})
            parse_calls.append(
                {
                    "subject": subject,
                    "body": body,
                    "ai_body_override": kwargs.get("ai_body_override"),
                    "source": kwargs.get("source"),
                    "ai_extractor_enabled": kwargs.get("ai_extractor_enabled"),
                    "source_hints": source_hints,
                }
            )
            canonical_title = str(source_hints.get("canonical_title") or subject)
            canonical_location = str(source_hints.get("canonical_location") or "")
            parsed = {
                "role": canonical_title,
                "location": canonical_location or "Dallas, Texas, USA",
                "job_location_text": canonical_location or "Dallas, Texas, USA",
                "salary_text": "not_specified",
                "skills_text": "Java, Spring Boot",
                "f2f_mentioned": False,
                "asks_contact_fields": False,
                "is_texas_role": "texas" in canonical_location.lower(),
            }
            return parsed, {
                "parser_version": "base_ai_extractor_v1",
                "source": "nvoids",
                "base_parser_result": {"role": canonical_title},
                "ai_extractor_result": {
                    "skills_approved": ["Java", "Spring Boot"],
                    "skills_unknown": [],
                    "confidence": 0.84,
                },
                "approved_skills_text": "Java, Spring Boot",
                "unknown_skills": [],
                "merged_result": dict(parsed),
                "ai_merge_notes": ["nvoids flow reused the merged parse with AI extractor enabled"],
                "parser_warning": None,
                "fallback_used": False,
                "source_hints": source_hints,
                "ai_input_source": str(source_hints.get("ai_input_source") or ""),
                "ai_input_chars": int(source_hints.get("ai_input_chars") or 0),
            }

        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.feature_ai_extractor_enabled = True
            db.commit()

        original_parse_email_with_details = external_feed_service_module.parse_email_with_details
        try:
            external_feed_service_module.parse_email_with_details = _fake_parse_email_with_details
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            self.assertGreaterEqual(len(parse_calls), 1)
            first_call = parse_calls[0]
            expected_ai_body = (
                "Role: Senior Python Developer\n"
                "Client: ExampleCo\n"
                "Location: Dallas, Texas, USA\n"
                "Must have skills\n"
                "Java, Spring Boot, Kafka, AWS"
            )
            self.assertEqual(first_call["source"], "nvoids")
            self.assertTrue(bool(first_call["ai_extractor_enabled"]))
            self.assertEqual(first_call["ai_body_override"], expected_ai_body)
            self.assertEqual(first_call["source_hints"], {
                "canonical_title": "Senior Python Developer",
                "canonical_location": "Dallas, Texas, USA",
                "company": "ExampleCo",
                "work_mode": "",
                "visa_hints": "",
                "ai_input_source": "nvoids_detail_table_row_3",
                "ai_input_chars": len(expected_ai_body),
            })

            with self.SessionLocal() as db:
                row = (
                    db.query(RecruiterEmail)
                    .filter(
                        RecruiterEmail.owner_id == main.settings.owner_id,
                        RecruiterEmail.source == "nvoids",
                        RecruiterEmail.role == "Senior Python Developer",
                    )
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.role, "Senior Python Developer")
                self.assertIn("Subject: Application for Senior Python Developer", row.draft_reply or "")
                self.assertIsNotNone(row.parser_details_json)
                self.assertIsNotNone(row.skills_json)
                payload = row.parser_details_json or ""
                skills_payload = row.skills_json or ""
                self.assertIn('"ai_extractor_result"', payload)
                self.assertIn('"skills_text":"Java, Spring Boot"', payload)
                self.assertIn('"skills_text":"Java, Spring Boot"', skills_payload)
                self.assertIn('"known":["Java","Spring Boot"]', skills_payload)
                self.assertIn('"canonical_title":"Senior Python Developer"', payload)
                self.assertIn('"ai_input_source":"nvoids_detail_table_row_3"', payload)
        finally:
            external_feed_service_module.parse_email_with_details = original_parse_email_with_details

    def test_sync_continues_when_detail_fetch_times_out(self) -> None:
        class _TimeoutCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                if "id=1" in url:
                    raise httpx.ReadTimeout("The read operation timed out")
                return super().fetch_detail_page(url=url)

        original_collector = main.external_feed_service.collector
        try:
            main.external_feed_service.collector = _TimeoutCollector()
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                ext_rows = (
                    db.query(ExternalOpportunity)
                    .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                    .order_by(ExternalOpportunity.external_post_id.asc())
                    .all()
                )
                self.assertEqual(len(ext_rows), 2)
                timed_out_row = next(row for row in ext_rows if row.external_post_id == "nvoids:1")
                self.assertEqual(timed_out_row.recruiter_email, "")
                self.assertEqual(timed_out_row.role, "Senior Python Developer")
                self.assertEqual(timed_out_row.location, "Dallas, Texas, USA")
        finally:
            main.external_feed_service.collector = original_collector

    def test_sync_continues_when_one_row_parser_fails(self) -> None:
        original_parse_external_post = external_feed_service_module.parse_external_post

        def _boom(*, source_type: str, source_url: str, title: str, location: str, posted_text: str, raw_body: str, raw_html: str):
            if title == "Senior Python Developer":
                raise RuntimeError("forced row parse failure")
            return original_parse_external_post(
                source_type=source_type,
                source_url=source_url,
                title=title,
                location=location,
                posted_text=posted_text,
                raw_body=raw_body,
                raw_html=raw_html,
            )

        external_feed_service_module.parse_external_post = _boom
        try:
            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                ext_rows = (
                    db.query(ExternalOpportunity)
                    .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                    .all()
                )
                self.assertEqual(len(ext_rows), 1)
                self.assertEqual(ext_rows[0].external_post_id, "nvoids:2")
        finally:
            external_feed_service_module.parse_external_post = original_parse_external_post

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

    def test_recruiter_numbers_hides_nvoids_placeholder_rows_but_keeps_real_rows(self) -> None:
        self._seed_nvoids_placeholder_recruiter()
        with self.SessionLocal() as db:
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12145550123",
                    display_phone_number="+1 214 555 0123",
                    recruiter_name="Real Recruiter",
                    company="Real Co",
                    designation="Recruiter",
                    recruiter_email="real@example.com",
                    first_detected_email_id=99,
                )
            )
            db.commit()

        res = self.client.get("/recruiter-numbers")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        emails = {row["recruiter_email"] for row in payload["items"]}
        self.assertIn("real@example.com", emails)
        self.assertNotIn("placeholder@example.com", emails)

    def test_recruiter_opportunities_hides_nvoids_placeholder_rows_but_keeps_real_rows(self) -> None:
        self._seed_nvoids_placeholder_recruiter(recruiter_email="hidden@example.com")
        with self.SessionLocal() as db:
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550124",
                display_phone_number="+1 214 555 0124",
                recruiter_name="Visible Recruiter",
                company="Visible Co",
                designation="Recruiter",
                recruiter_email="visible@example.com",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.flush()
            db.add(
                RecruiterOpportunity(
                    owner_id=main.settings.owner_id,
                    recruiter_number_id=recruiter.id,
                    source_email_id=None,
                    gmail_message_id="gmail-visible-1",
                    source_type="gmail",
                    source_url=None,
                    external_opportunity_id=None,
                    email_subject="Visible subject",
                    email_sender="visible@example.com",
                    gmail_open_url="https://mail.google.com/",
                    received_at=None,
                    job_title="Visible role",
                    client="Visible Co",
                    location="Texas",
                    work_mode="Remote",
                    visa_restrictions="",
                    extracted_skills="Java",
                    evidence="gmail",
                    status="New",
                    notes="",
                )
            )
            db.commit()

        res = self.client.get("/recruiter-opportunities")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        emails = {row["recruiter_email"] for row in payload["items"]}
        self.assertIn("visible@example.com", emails)
        self.assertNotIn("hidden@example.com", emails)

    def test_backfill_phones_clears_noise_phone_and_deletes_placeholder_bridge(self) -> None:
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
        self.assertEqual(payload["deleted_placeholder_opportunities"], 1)
        self.assertEqual(payload["deleted_placeholder_recruiters"], 1)

        with self.SessionLocal() as db:
            updated_ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.external_post_id == "nvoids:3385623").first()
            self.assertIsNotNone(updated_ext)
            assert updated_ext is not None
            self.assertEqual(updated_ext.recruiter_phone, "")
            self.assertEqual(updated_ext.recruiter_name, "Nitin Tehriya")
            updated_recruiter = db.query(RecruiterNumber).filter(RecruiterNumber.id == 1).first()
            self.assertIsNone(updated_recruiter)
            recruiter_opp = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.recruiter_number_id == 1).first()
            self.assertIsNone(recruiter_opp)

    def test_backfill_keeps_valid_nvoids_recruiter_bucket_with_real_phone(self) -> None:
        recruiter_id, opportunity_id, ext_id = self._seed_nvoids_placeholder_recruiter(
            normalized_phone_number="12145550125",
            display_phone_number="+1 214 555 0125",
            recruiter_email="valid@example.com",
            recruiter_name="Valid Recruiter",
            company="Valid Co",
            external_phone="+1 214 555 0125",
        )

        res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["deleted_placeholder_opportunities"], 0)
        self.assertEqual(payload["deleted_placeholder_recruiters"], 0)

        with self.SessionLocal() as db:
            self.assertIsNotNone(db.query(RecruiterNumber).filter(RecruiterNumber.id == recruiter_id).first())
            self.assertIsNotNone(db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == opportunity_id).first())
            self.assertIsNotNone(db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first())

    def test_backfill_standardizes_existing_stored_phone_displays(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12406571540",
                    display_phone_number="240-657-1540",
                    recruiter_name="Unknown",
                    company="Unknown",
                    designation="Recruiter",
                    recruiter_email="nancy@example.com",
                    first_detected_email_id=None,
                )
            )
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="19809070802",
                    display_phone_number="+1 (980) 9070802",
                    owner_name="Owner",
                    company="Corp",
                    source_email_id=1,
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="sender@example.com",
                subject="Role",
                body="Body",
                role="Developer",
                location="Remote",
                salary_text="",
                skills_text="Java",
                score=0,
                decision="Qualified",
                state="needs_review",
                source="gmail",
            )
            db.add(email)
            db.flush()
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=email.id,
                    normalized_phone_number="12012772419",
                    display_phone_number="+1 (201) 277-2419",
                    owner_name="Review Owner",
                    company="Review Co",
                    designation="Recruiter",
                    confidence="high",
                    purpose="Recruiter contact number",
                    evidence_snippet="snippet",
                    email_subject="subject",
                    email_sender="review@example.com",
                    gmail_open_url="",
                    state="pending",
                )
            )
            db.commit()

        res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["recruiter_numbers_reformatted"], 1)
        self.assertEqual(payload["employer_numbers_reformatted"], 2)
        self.assertEqual(payload["review_numbers_reformatted"], 1)

        with self.SessionLocal() as db:
            recruiter = db.query(RecruiterNumber).filter(RecruiterNumber.recruiter_email == "nancy@example.com").first()
            employer = db.query(EmployerNumber).filter(EmployerNumber.company == "Corp").first()
            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_name == "Review Owner").first()
            assert recruiter is not None and employer is not None and review is not None
            self.assertEqual(recruiter.display_phone_number, "(240) 657-1540")
            self.assertEqual(employer.display_phone_number, "(980) 907-0802")
            self.assertEqual(review.display_phone_number, "(201) 277-2419")

    def test_backfill_standardizes_extension_style_recruiter_numbers(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="9727561212128",
                    display_phone_number="(972) - 756 - 1212 Ext 128",
                    recruiter_name="Dharma Veer",
                    company="Intellisoft",
                    designation="Recruiter",
                    recruiter_email="dharma@example.com",
                    first_detected_email_id=1,
                )
            )
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="6098886198113",
                    display_phone_number="(609) 888 6198 * 113",
                    recruiter_name="Vikas Rao",
                    company="DVG Tech",
                    designation="Recruiter",
                    recruiter_email="vikas@example.com",
                    first_detected_email_id=2,
                )
            )
            db.commit()

        res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["recruiter_numbers_reformatted"], 2)

        with self.SessionLocal() as db:
            rows = (
                db.query(RecruiterNumber)
                .filter(RecruiterNumber.recruiter_email.in_(["dharma@example.com", "vikas@example.com"]))
                .order_by(RecruiterNumber.recruiter_email.asc())
                .all()
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].display_phone_number, "(972) 756-1212 ext 128")
            self.assertEqual(rows[1].display_phone_number, "(609) 888-6198 ext 113")


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
