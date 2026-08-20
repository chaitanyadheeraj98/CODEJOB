import os
import re
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

import httpx
os.environ["DEBUG"] = "false"
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.automation.queue_preparation import prepend_nvoids_listing_line
from app.db import Base
from app.external_feeds.collector import CollectedPage
from app.external_feeds.parser import parse_nvoids_detail
from app.external_feeds import service as external_feed_service_module
from app.external_feeds.service import ExternalFeedService
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import AttachmentAsset, CustomSkillTaxonomyEntry, NumberReviewQueue, PremiumNumberContact, PremiumNumberLead, RecentRun, RecentRunSkippedItem, RecruiterEmail, RecruiterOpportunity, ResumeAsset, UserSettings
from app.services.role_manifest_service import RoleManifestService
from role_manifest_fixtures import (
    REAL_NVOIDS_SIX_ROLE_MANIFEST,
    REAL_NVOIDS_SIX_ROLE_SOURCE,
    REAL_NVOIDS_SIX_ROLE_TITLES,
    REAL_NVOIDS_THREE_ROLE_MANIFEST,
    REAL_NVOIDS_THREE_ROLE_SOURCE,
    REAL_NVOIDS_THREE_ROLE_TITLES,
)


def RecruiterNumber(**values):
    return PremiumNumberContact(is_recruiter=True, **values)


def EmployerNumber(**values):
    return PremiumNumberContact(is_employer=True, **values)


class _FakeCollector:
    def __init__(self) -> None:
        self.last_hotlist_mode = "Exclude Hotlists"

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
        _ = query
        self.last_hotlist_mode = hotlist_mode
        return self.fetch_page("", page)

    def fetch_detail_page(self, *, url: str) -> CollectedPage:
        title = "Senior Python Developer"
        location = "Dallas, Texas, USA"
        posted = "11:00 PM 07-May-26"
        page_title = "Job Details"
        if "id=2" in url:
            title = "React Developer"
            location = "Remote, USA"
            posted = "10:00 PM 07-May-26"
        jd_body = (
            f"Role: {title}<br>"
            f"Client: ExampleCo<br>"
            f"Location: {location}<br>"
            "Must have skills<br>"
            "Java, Spring Boot, Kafka, AWS"
        )
        html = f"""
        <html><head><title>{page_title}</title></head><body>
        <table>
          <tr><td>{title} at {location}</td></tr>
          <tr><td>Email: recruiter_{'1' if 'id=1' in url else '2'}@example.com</td></tr>
          <tr><td>{jd_body}</td></tr>
          <tr><td>recruiter_{'1' if 'id=1' in url else '2'}@example.com | View All</td></tr>
          <tr><td>{posted}</td></tr>
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
                    nvoids_detail_title_mode="job_details",
                    nvoids_locations="",
                    qualification_threshold=0.0,
                    default_employer_cc_emails="employer.cc@example.com",
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
          <tr><td>04:49 AM 17-Jun-26</td></tr>
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

    def test_retry_role_detection_reuses_materialized_children(self) -> None:
        source_text = """VISA: USC/GC Only
1. Platform Engineer
Job ID: ENG-1
2. Data Engineer
Job ID: ENG-2"""
        manifest = RoleManifestService(
            provider=lambda system, user: {
                "classification": "multiple",
                "role_count": 2,
                "confidence": 0.96,
                "shared_constraints": [],
                "roles": [
                    {
                        "index": 1,
                        "title_hint": "Platform Engineer",
                        "requisition_id": "ENG-1",
                        "start_line": 2,
                        "end_line": 3,
                        "confidence": 0.98,
                    },
                    {
                        "index": 2,
                        "title_hint": "Data Engineer",
                        "requisition_id": "ENG-2",
                        "start_line": 4,
                        "end_line": 5,
                        "confidence": 0.98,
                    },
                ],
            }
        ).detect(source_text)
        with self.SessionLocal() as db:
            parent = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="recruiter@example.com",
                subject="Two roles",
                body=source_text,
                role="",
                location="",
                salary_text="",
                skills_text="",
                score=0,
                decision="Qualified",
                state="needs_review",
                source="gmail",
                external_message_id="gmail-multi-role-1",
            )
            db.add(parent)
            db.commit()
            parent_id = parent.id

        parsed = {
            "role": "Engineer",
            "location": "Remote",
            "salary_text": "not_specified",
            "skills_text": "Python",
        }
        details = {"structured_requirements": {}}
        with (
            patch.object(main.settings, "role_manifest_child_creation_enabled", True),
            patch.object(main, "RoleManifestService") as manifest_service_type,
            patch.object(main, "parse_email_with_details", return_value=(parsed, details)),
            patch.object(main, "_get_orchestration_service") as get_orchestration_service,
        ):
            manifest_service_type.return_value.detect.return_value = manifest
            get_orchestration_service.return_value.regenerate_candidate.return_value = None

            first = self.client.post(f"/candidates/{parent_id}/retry-role-detection")
            second = self.client.post(f"/candidates/{parent_id}/retry-role-detection")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["child_ids"], second.json()["child_ids"])
        self.assertEqual(manifest_service_type.call_args_list, [call(max_rung=4), call(max_rung=4)])
        with self.SessionLocal() as db:
            self.assertEqual(
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.source_parent_email_id == parent_id)
                .count(),
                2,
            )

    def test_retry_role_detection_processes_single_fallback_as_a_normal_source(self) -> None:
        source_text = "Senior Engineer role"
        manifest = RoleManifestService(
            provider=lambda system, user: {
                "classification": "uncertain",
                "role_count": 0,
                "confidence": 0.2,
                "roles": [],
            }
        ).detect(source_text)
        with self.SessionLocal() as db:
            parent = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="recruiter@example.com",
                subject="One unresolved role",
                body=source_text,
                role="",
                location="",
                salary_text="",
                skills_text="",
                score=0,
                decision="Qualified",
                state="needs_review",
                source="gmail",
                external_message_id="gmail-fallback-role-1",
                sendability_status="manifest_review",
            )
            db.add(parent)
            db.commit()
            parent_id = parent.id

        with (
            patch.object(main, "RoleManifestService") as manifest_service_type,
            patch.object(main, "extract_and_score_children") as extract_children,
        ):
            manifest_service_type.return_value.detect.return_value = manifest
            response = self.client.post(f"/candidates/{parent_id}/retry-role-detection")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["manifest_status"], "single_fallback")
        self.assertEqual(response.json()["requirement_count"], 1)
        manifest_service_type.assert_called_once_with(max_rung=4)
        self.assertEqual(extract_children.call_args.args[1], [parent_id])
        with self.SessionLocal() as db:
            parent = db.get(RecruiterEmail, parent_id)
            self.assertEqual(parent.role_manifest_status, "single_fallback")
            self.assertIsNone(parent.sendability_status)

    def test_retry_role_detection_processes_each_role_from_both_real_nvoids_shapes(self) -> None:
        cases = [
            (
                "nvoids:3563272",
                REAL_NVOIDS_SIX_ROLE_SOURCE,
                REAL_NVOIDS_SIX_ROLE_MANIFEST,
                REAL_NVOIDS_SIX_ROLE_TITLES,
            ),
            (
                "nvoids:3565603",
                REAL_NVOIDS_THREE_ROLE_SOURCE,
                REAL_NVOIDS_THREE_ROLE_MANIFEST,
                REAL_NVOIDS_THREE_ROLE_TITLES,
            ),
        ]
        manifests = [
            RoleManifestService(provider=lambda system, user, payload=payload: payload).detect(source)
            for _, source, payload, _ in cases
        ]
        with self.SessionLocal() as db:
            parents = [
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="recruiter@example.com",
                    subject="Multiple roles",
                    body=source,
                    role="Mixed requirement",
                    location="",
                    salary_text="",
                    skills_text="",
                    score=0,
                    decision="Qualified",
                    state="needs_review",
                    source="nvoids",
                    external_message_id=external_message_id,
                )
                for external_message_id, source, _, _ in cases
            ]
            db.add_all(parents)
            db.commit()
            parent_ids = [parent.id for parent in parents]

        def parse_child(_subject: str, body: str, **_kwargs):
            title = re.sub(r"^\d+\.\s*", "", body.splitlines()[0]).strip(" |")
            return (
                {
                    "role": title,
                    "location": "Remote",
                    "salary_text": "not_specified",
                    "skills_text": "role specific skills",
                },
                {"structured_requirements": {}},
            )

        with (
            patch.object(main.settings, "role_manifest_child_creation_enabled", True),
            patch.object(main, "RoleManifestService") as manifest_service_type,
            patch.object(main, "parse_email_with_details", side_effect=parse_child),
            patch.object(main, "_get_orchestration_service") as get_orchestration_service,
        ):
            manifest_service_type.return_value.detect.side_effect = manifests
            responses = [
                self.client.post(f"/candidates/{parent_id}/retry-role-detection")
                for parent_id in parent_ids
            ]

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual([response.json()["requirement_count"] for response in responses], [6, 3])
        with self.SessionLocal() as db:
            all_children = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.source_parent_email_id.in_(parent_ids))
                .all()
            )
            self.assertEqual(
                get_orchestration_service.return_value.regenerate_candidate.call_count,
                9,
                [(child.role, child.sendability_status, child.last_error) for child in all_children],
            )
            for parent_id, (_, _, _, expected_titles) in zip(parent_ids, cases, strict=True):
                parent = db.get(RecruiterEmail, parent_id)
                children = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.source_parent_email_id == parent_id)
                    .order_by(RecruiterEmail.requirement_index)
                    .all()
                )
                self.assertEqual([child.role for child in children], list(expected_titles))
                self.assertTrue(all(child.is_multi_role_child for child in children))
                self.assertEqual(parent.sendability_status if parent else None, "superseded_multi_role")

    def test_manual_sync_returns_summary_and_runs(self) -> None:
        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(payload["source_type"], "nvoids")
        self.assertTrue(str(payload["run_key"]).startswith("nvoids_sync:"))
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

    def test_manual_sync_continues_role_detection_after_one_row_fails(self) -> None:
        with self.SessionLocal() as db:
            user_settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).one()
            user_settings.feature_role_manifest_enabled = True
            db.commit()

        def sync_nvoids(db, **_kwargs):
            db.add_all(
                [
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="first@example.com",
                        subject="First role",
                        body="First role body",
                        role="First role",
                        location="Remote",
                        salary_text="",
                        skills_text="",
                        score=0,
                        decision="Qualified",
                        state="needs_review",
                        source="nvoids",
                        external_message_id="nvoids:batch-resilience-1",
                        created_at=datetime.now(UTC),
                    ),
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="second@example.com",
                        subject="Second role",
                        body="Second role body",
                        role="Second role",
                        location="Remote",
                        salary_text="",
                        skills_text="",
                        score=0,
                        decision="Qualified",
                        state="needs_review",
                        source="nvoids",
                        external_message_id="nvoids:batch-resilience-2",
                        created_at=datetime.now(UTC),
                    ),
                ]
            )
            db.commit()
            return SimpleNamespace(
                source_type="nvoids",
                run_key="nvoids_sync:batch-resilience",
                fetched_count=2,
                created_count=2,
                deduped_count=0,
                failed_count=0,
                skipped_location_count=0,
                run_id=1,
            )

        detection_calls: list[int] = []

        def retry_role_detection(email_id: int, _db, *, max_rung: int):
            self.assertEqual(max_rung, 2)
            detection_calls.append(email_id)
            if len(detection_calls) == 1:
                raise RuntimeError("first row failed")
            return SimpleNamespace(manifest_status="single")

        with (
            patch.object(main.external_feed_service, "sync_nvoids", side_effect=sync_nvoids),
            patch.object(main, "_retry_role_detection", side_effect=retry_role_detection),
            self.assertLogs(main.logger.name, level="ERROR") as captured_logs,
        ):
            sync = self.client.post("/external-feeds/nvoids/sync")

        self.assertEqual(sync.status_code, 200, sync.text)
        self.assertEqual(len(detection_calls), 2)
        self.assertIn("role_manifest_retry_failed", "\n".join(captured_logs.output))
        self.assertIn("nvoids_sync:batch-resilience", "\n".join(captured_logs.output))

    def test_recent_runs_list_returns_gmail_and_nvoids_runs_in_descending_order(self) -> None:
        with self.SessionLocal() as db:
            older = RecentRun(
                owner_id=main.settings.owner_id,
                run_source="gmail_sync",
                run_key="gmail_sync:older-batch",
                status="skipped",
                detail="Older Gmail run",
                skipped_count=1,
                created_at=datetime.fromisoformat("2026-06-30T20:00:00+00:00"),
            )
            newer = RecentRun(
                owner_id=main.settings.owner_id,
                run_source="nvoids_sync",
                run_key="nvoids_sync:77",
                status="ok",
                detail="Newer Nvoids run",
                skipped_count=0,
                created_at=datetime.fromisoformat("2026-06-30T21:00:00+00:00"),
            )
            db.add_all([older, newer])
            db.commit()

        res = self.client.get("/recent-runs?limit=10")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual([item["run_key"] for item in payload["items"][:2]], ["nvoids_sync:77", "gmail_sync:older-batch"])
        self.assertEqual([item["run_source"] for item in payload["items"][:2]], ["nvoids_sync", "gmail_sync"])
        self.assertFalse(payload["has_next"])

    def test_recent_runs_list_filters_by_mail_date_local_day(self) -> None:
        selected_day = date(2026, 7, 8)
        start_utc, end_utc = main._mail_date_utc_window(selected_day)
        with self.SessionLocal() as db:
            db.add_all(
                [
                    RecentRun(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:inside-window",
                        status="ok",
                        detail="Inside selected day",
                        created_at=start_utc,
                    ),
                    RecentRun(
                        owner_id=main.settings.owner_id,
                        run_source="nvoids_sync",
                        run_key="nvoids_sync:before-window",
                        status="ok",
                        detail="Before selected day",
                        created_at=start_utc - timedelta(seconds=1),
                    ),
                    RecentRun(
                        owner_id=main.settings.owner_id,
                        run_source="automation_run",
                        run_key="automation_run:after-window",
                        status="ok",
                        detail="After selected day",
                        created_at=end_utc,
                    ),
                ]
            )
            db.commit()

        res = self.client.get("/recent-runs?limit=10&mail_date=2026-07-08")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual([item["run_key"] for item in payload["items"]], ["gmail_sync:inside-window"])

    def test_recent_runs_list_mail_date_boundary_uses_business_timezone_window(self) -> None:
        selected_day = date(2026, 11, 1)
        start_utc, end_utc = main._mail_date_utc_window(selected_day)
        with self.SessionLocal() as db:
            db.add_all(
                [
                    RecentRun(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:start-boundary",
                        status="ok",
                        detail="Included at start boundary",
                        created_at=start_utc,
                    ),
                    RecentRun(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:end-excluded",
                        status="ok",
                        detail="Excluded at end boundary",
                        created_at=end_utc,
                    ),
                ]
            )
            db.commit()

        res = self.client.get("/recent-runs?limit=10&mail_date=2026-11-01")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual([item["run_key"] for item in payload["items"]], ["gmail_sync:start-boundary"])

    def test_recent_runs_list_rejects_impossible_mail_date(self) -> None:
        res = self.client.get("/recent-runs?limit=10&mail_date=2026-02-30")
        self.assertEqual(res.status_code, 422, res.text)
        self.assertEqual(res.json()["detail"], "mail_date must be a valid YYYY-MM-DD date")

    def test_recent_run_items_endpoint_paginates_skipped_items(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    RecentRunSkippedItem(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:batch-1",
                        source_type="gmail",
                        outcome="skipped",
                        reason_code="duplicate_existing_email",
                        reason_detail="first",
                        external_message_id="msg-first",
                        title_or_subject="First",
                        sender="Recruiter",
                        created_at=datetime.fromisoformat("2026-06-30T20:00:00+00:00"),
                    ),
                    RecentRunSkippedItem(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:batch-1",
                        source_type="gmail",
                        outcome="skipped",
                        reason_code="non_recruiter_like_gmail",
                        reason_detail="second",
                        external_message_id="msg-second",
                        title_or_subject="Second",
                        sender="Recruiter",
                        created_at=datetime.fromisoformat("2026-06-30T20:01:00+00:00"),
                    ),
                    RecentRunSkippedItem(
                        owner_id=main.settings.owner_id,
                        run_source="gmail_sync",
                        run_key="gmail_sync:batch-1",
                        source_type="gmail",
                        outcome="skipped",
                        reason_code="processed_skipped",
                        reason_detail="third",
                        external_message_id="msg-third",
                        title_or_subject="Third",
                        sender="Recruiter",
                        created_at=datetime.fromisoformat("2026-06-30T20:02:00+00:00"),
                    ),
                ]
            )
            db.commit()

        first_page = self.client.get("/recent-runs/gmail_sync:batch-1/items?outcome=skipped&limit=2")
        self.assertEqual(first_page.status_code, 200, first_page.text)
        first_payload = first_page.json()
        self.assertEqual([item["title_or_subject"] for item in first_payload["items"]], ["Third", "Second"])
        self.assertEqual(first_payload["items"][0]["gmail_message_url"], "https://mail.google.com/mail/u/0/#all/msg-third")
        self.assertEqual(first_payload["items"][1]["gmail_message_url"], "https://mail.google.com/mail/u/0/#all/msg-second")
        self.assertEqual(first_payload["next_cursor"], 2)
        self.assertTrue(first_payload["has_next"])

        second_page = self.client.get("/recent-runs/gmail_sync:batch-1/items?outcome=skipped&limit=2&cursor=2")
        self.assertEqual(second_page.status_code, 200, second_page.text)
        second_payload = second_page.json()
        self.assertEqual([item["title_or_subject"] for item in second_payload["items"]], ["First"])
        self.assertIsNone(second_payload["next_cursor"])
        self.assertFalse(second_payload["has_next"])

    def test_manual_sync_uses_preferred_employer_cc_when_configured(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.preferred_employer_cc_email = "Sheshwika@HorizonsOfTech.net"
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

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
            self.assertEqual(rows[0].cc_email, "sheshwika@horizonsoftech.net")
            self.assertEqual(rows[0].routing_reason, "Resolved the recruiter To and employer CC recipients.")
            self.assertIn("nvoids_listing_recruiter_email", rows[0].routing_candidates)
            self.assertIn("preferred_employer_cc", rows[0].routing_candidates)

    def test_manual_sync_falls_back_to_default_employer_cc_when_preferred_blank(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.preferred_employer_cc_email = ""
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            row = (
                db.query(RecruiterEmail)
                .filter(
                    RecruiterEmail.owner_id == main.settings.owner_id,
                    RecruiterEmail.state == "needs_review",
                    RecruiterEmail.source == "nvoids",
                )
                .first()
            )
            assert row is not None
            self.assertEqual(row.cc_email, "employer.cc@example.com")
            self.assertEqual(row.routing_reason, "Resolved the recruiter To and employer CC recipients.")

    def test_manual_sync_ignores_preferred_cc_when_it_matches_recruiter_to(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            # Two preferred CCs: one duplicates the recruiter's own "to" address (must be excluded so
            # a recipient never appears in both To and CC), the other is a genuine employer contact.
            settings.preferred_employer_cc_emails = "recruiter_1@example.com,employer.cc@example.com"
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            row = (
                db.query(RecruiterEmail)
                .filter(
                    RecruiterEmail.owner_id == main.settings.owner_id,
                    RecruiterEmail.state == "needs_review",
                    RecruiterEmail.source == "nvoids",
                    RecruiterEmail.recipient_email == "recruiter_1@example.com",
                )
                .first()
            )
            assert row is not None
            self.assertEqual(row.cc_email, "employer.cc@example.com")
            self.assertEqual(row.routing_reason, "Resolved the recruiter To and employer CC recipients.")

    def test_nvoids_adapter_builds_candidates_and_passes_default_to_shared_core(self) -> None:
        service = ExternalFeedService()
        item = ExternalOpportunity(recruiter_email="Recruiter <recruiter@example.com>")
        settings = UserSettings(
            owner_id=main.settings.owner_id,
            preferred_employer_cc_email="",
            preferred_employer_cc_emails="",
            default_employer_cc_emails="fallback@example.com",
        )
        with self.SessionLocal() as db:
            decision = service._nvoids_routing_decision(
                db,
                owner_id=main.settings.owner_id,
                item=item,
                settings=settings,
            )

        self.assertEqual(decision.to_email, "recruiter@example.com")
        self.assertEqual(decision.cc_email, "fallback@example.com")
        self.assertEqual(
            [entry.source for entry in decision.evidence],
            ["nvoids_listing_recruiter_email", "default_employer_cc"],
        )

    def test_nvoids_adapter_ignores_unrelated_employer_domain_address_not_present_on_this_listing(self) -> None:
        # Regression test for the Email 6135/6159-shaped bug: the CC pool used to pull in a stale,
        # unrelated employer contact from a past, unrelated email. The recruiter here is a genuine
        # external sender (not on a configured Employer Domain), so no employer-domain CC candidate
        # should be synthesized -- CC should resolve purely from the configured preferred list.
        service = ExternalFeedService()
        item = ExternalOpportunity(recruiter_email="eshwar@neodymtechnologies.com")
        settings = UserSettings(
            owner_id=main.settings.owner_id,
            employer_domains="horizonsoftech.net,horizonsofttech.net",
            preferred_employer_cc_email="",
            preferred_employer_cc_emails="kartheek@horizonsoftech.net,hr@horizonsoftech.net",
            default_employer_cc_emails="",
        )
        with self.SessionLocal() as db:
            decision = service._nvoids_routing_decision(
                db,
                owner_id=main.settings.owner_id,
                item=item,
                settings=settings,
            )

        self.assertEqual(decision.to_email, "eshwar@neodymtechnologies.com")
        self.assertEqual(decision.cc_email, "kartheek@horizonsoftech.net, hr@horizonsoftech.net")
        self.assertEqual(
            [entry.source for entry in decision.evidence],
            ["nvoids_listing_recruiter_email", "preferred_employer_cc", "preferred_employer_cc"],
        )

    def test_nvoids_adapter_adds_sender_to_cc_when_sender_domain_matches_employer_domain(self) -> None:
        service = ExternalFeedService()
        item = ExternalOpportunity(recruiter_email="Sheshwika Kukkala <sheshwika@horizonsoftech.net>")
        settings = UserSettings(
            owner_id=main.settings.owner_id,
            employer_domains="horizonsoftech.net,horizonsofttech.net",
            preferred_employer_cc_email="",
            preferred_employer_cc_emails="kartheek@horizonsoftech.net",
            default_employer_cc_emails="",
        )
        with self.SessionLocal() as db:
            decision = service._nvoids_routing_decision(
                db,
                owner_id=main.settings.owner_id,
                item=item,
                settings=settings,
            )

        # Sheshwika's domain matches an Employer Domain, but she is also the sole "to" recipient
        # for this listing, so the existing to/cc-exclusivity rule in routing/policy.py keeps her
        # out of CC (already addressed directly) -- only the configured preferred CC remains.
        self.assertEqual(decision.to_email, "sheshwika@horizonsoftech.net")
        self.assertEqual(decision.cc_email, "kartheek@horizonsoftech.net")

    def test_manual_sync_routes_uncertain_row_3_phones_to_needs_review(self) -> None:
        class _PhoneCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                phone_line = "From: Shivam Singh<br>Phone: 240-657-1540"
                if "id=2" in url:
                    phone_line = "Regards,<br>Nupur Kumari<br>Phone No: +1 (201) 277-2419"
                html = f"""
                <html><body>
                <table>
                  <tr><td>Senior Python Developer at Dallas, Texas, USA</td></tr>
                  <tr><td>Email: recruiter@example.com</td></tr>
                  <tr><td>{phone_line}<br>Java, Spring Boot</td></tr>
                  <tr><td>recruiter@example.com | View All</td></tr>
                  <tr><td>11:00 PM 07-May-26</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        main.external_feed_service.collector = _PhoneCollector()

        with patch.object(
            external_feed_service_module,
            "parse_nvoids_detail",
            wraps=parse_nvoids_detail,
        ) as parse_detail, patch(
            "app.premium_numbers.extraction._llm_extract",
            return_value=[],
        ), patch(
            "app.premium_numbers.extraction._sbert_keep_candidate",
            return_value=(True, "test"),
        ):
            sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        self.assertEqual(parse_detail.call_count, 2)

        with self.SessionLocal() as db:
            rows = (
                db.query(NumberReviewQueue)
                .filter(NumberReviewQueue.owner_id == main.settings.owner_id)
                .order_by(NumberReviewQueue.id.asc())
                .all()
            )
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row.owner_name == "Unknown" for row in rows))
            self.assertTrue(all(row.source_external_opportunity_id is not None for row in rows))
            self.assertEqual(rows[0].display_phone_number, "(240) 657-1540")
            self.assertEqual(rows[1].display_phone_number, "(201) 277-2419")

            ext_rows = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .order_by(ExternalOpportunity.id.asc())
                .all()
            )
            self.assertEqual(len(ext_rows), 2)
            self.assertTrue(all((row.bridge_status or "") == "needs_review" for row in ext_rows))

            recruiter_opportunities = (
                db.query(RecruiterOpportunity)
                .filter(RecruiterOpportunity.owner_id == main.settings.owner_id, RecruiterOpportunity.source_type == "nvoids")
                .order_by(RecruiterOpportunity.id.asc())
                .all()
            )
            self.assertEqual(recruiter_opportunities, [])

    def test_manual_sync_routes_ph_no_signature_variant_to_needs_review(self) -> None:
        class _PhNoCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                html = """
                <html><body>
                <table>
                  <tr><td>Java AWS Developer at Plano, Texas, USA</td></tr>
                  <tr><td>Email: sharma.gopal@net2source.com</td></tr>
                  <tr><td>Best Regards,<br>Gopal Sharma<br>Senior Talent Acquisition - USA<br>Email:<br>sharma.gopal@net2source.com<br>Ph no. (551) 303-0028</td></tr>
                  <tr><td>sharma.gopal@net2source.com | View All</td></tr>
                  <tr><td>02:27 AM 26-Jun-26</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        main.external_feed_service.collector = _PhNoCollector()

        with patch(
            "app.premium_numbers.extraction._llm_extract",
            return_value=[],
        ), patch(
            "app.premium_numbers.extraction._sbert_keep_candidate",
            return_value=(True, "test"),
        ):
            sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            review = (
                db.query(NumberReviewQueue)
                .filter(
                    NumberReviewQueue.owner_id == main.settings.owner_id,
                    NumberReviewQueue.contact_email == "sharma.gopal@net2source.com",
                )
                .first()
            )
            self.assertIsNotNone(review)
            assert review is not None
            self.assertEqual(review.owner_name, "Unknown")
            self.assertEqual(review.display_phone_number, "(551) 303-0028")

            ext_rows = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .all()
            )
            self.assertTrue(all((row.bridge_status or "") == "needs_review" for row in ext_rows))

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
            self.assertTrue(all((row.bridge_status or "") == "ignored" for row in ext_rows))

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

            recruiter_numbers = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).all()
            recruiter_opportunities = (
                db.query(RecruiterOpportunity)
                .filter(RecruiterOpportunity.owner_id == main.settings.owner_id, RecruiterOpportunity.source_type == "nvoids")
                .all()
            )
            self.assertEqual(recruiter_numbers, [])
            self.assertEqual(recruiter_opportunities, [])

    def test_nvoids_bridge_calls_shared_workflow(self) -> None:
        # Regression guard for the Premium Numbers Redesign: `_bridge_to_recruiter_opportunity`
        # must route every Nvoids posting through the shared `PhoneIntelligenceWorkflowService`
        # (`capture_premium_numbers_for_nvoids`) instead of constructing RecruiterNumber /
        # RecruiterOpportunity rows itself. Spy on the shared method (still delegating to the
        # real implementation, captured before patching, so behavior is unchanged) and assert
        # the *call* happened with the right (db, item, jd_body) shape - not just that some
        # end-state row exists, which a hand-built parallel path could fake too. Call-shape data
        # is captured inside the spy itself (not after the request), because the per-request DB
        # session - and the ExternalOpportunity instances loaded on it - are closed/detached by
        # the time `self.client.post(...)` returns.
        workflow = main.external_feed_service.phone_intelligence_workflow
        real_capture = workflow.capture_premium_numbers_for_nvoids
        captured_calls: list[tuple[bool, bool, str, str, bool]] = []

        def _capture_and_delegate(db, item, jd_body, ai_extraction=None):
            captured_calls.append(
                (
                    isinstance(db, Session),
                    isinstance(item, ExternalOpportunity),
                    item.source_type,
                    item.external_post_id,
                    bool(jd_body),
                )
            )
            return real_capture(db, item, jd_body, ai_extraction)

        with patch.object(
            workflow,
            "capture_premium_numbers_for_nvoids",
            side_effect=_capture_and_delegate,
        ) as capture_spy:
            sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            ext_rows = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .order_by(ExternalOpportunity.id.asc())
                .all()
            )
            ext_post_ids = {row.external_post_id for row in ext_rows}

        # One shared-workflow call per Nvoids posting the sync created - proves the bridge
        # is not skipping the shared workflow for some rows and hand-rolling others.
        self.assertGreaterEqual(len(ext_rows), 1)
        self.assertEqual(capture_spy.call_count, len(ext_rows))
        self.assertEqual(len(captured_calls), len(ext_rows))

        called_post_ids = set()
        for is_session_arg, is_ext_opportunity_arg, source_type, external_post_id, jd_body_is_truthy in captured_calls:
            self.assertTrue(is_session_arg)
            self.assertTrue(is_ext_opportunity_arg)
            self.assertEqual(source_type, "nvoids")
            self.assertTrue(jd_body_is_truthy)
            called_post_ids.add(external_post_id)

        self.assertEqual(called_post_ids, ext_post_ids)

    def test_nvoids_uncertain_lead_lands_in_needs_review(self) -> None:
        # A low-confidence / role="unknown" Nvoids extraction (regex fallback, no AI match) must
        # land in NumberReviewQueue keyed by source_external_opportunity_id (source_email_id null),
        # not get auto-promoted into a PremiumNumberContact.
        class _UncertainSinglePhoneCollector(_FakeCollector):
            def fetch_page(self, _base_url: str, page: int) -> CollectedPage:
                if page > 0:
                    return CollectedPage(url="https://www.nvoids.com/index.jsp?p=1", html="<html><body></body></html>")
                html = """
                <table>
                  <tr><td><a href='job1.jsp?id=1'>Senior Python Developer</a></td><td>Dallas, Texas, USA</td><td>11:00 PM 07-May-26</td></tr>
                </table>
                """
                return CollectedPage(url="https://www.nvoids.com/index.jsp", html=html)

            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                html = """
                <html><body>
                <table>
                  <tr><td>Senior Python Developer at Dallas, Texas, USA</td></tr>
                  <tr><td>Email: recruiter@example.com</td></tr>
                  <tr><td>From: Shivam Singh<br>Phone: 240-657-1540<br>Java, Spring Boot</td></tr>
                  <tr><td>recruiter@example.com | View All</td></tr>
                  <tr><td>11:00 PM 07-May-26</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        main.external_feed_service.collector = _UncertainSinglePhoneCollector()

        with patch(
            "app.premium_numbers.extraction._llm_extract",
            return_value=[],
        ), patch(
            "app.premium_numbers.extraction._sbert_keep_candidate",
            return_value=(True, "test"),
        ):
            sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            ext_row = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .one()
            )
            self.assertEqual(ext_row.bridge_status, "needs_review")

            review_rows = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(review_rows), 1)
            review = review_rows[0]
            self.assertEqual(review.source_external_opportunity_id, ext_row.id)
            self.assertIsNone(review.source_email_id)
            self.assertEqual(review.display_phone_number, "(240) 657-1540")

            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).count(), 0)

    def test_nvoids_zero_leads_sets_bridge_status_ignored(self) -> None:
        # §25.8 fallback: when neither AI nor the regex fallback finds any phone lead at all for a
        # posting, the bridge must set ExternalOpportunity.bridge_status to "ignored" and create
        # neither a NumberReviewQueue row nor a PremiumNumberContact row for it.
        class _NoPhoneSingleItemCollector(_FakeCollector):
            def fetch_page(self, _base_url: str, page: int) -> CollectedPage:
                if page > 0:
                    return CollectedPage(url="https://www.nvoids.com/index.jsp?p=1", html="<html><body></body></html>")
                html = """
                <table>
                  <tr><td><a href='job1.jsp?id=1'>Senior Python Developer</a></td><td>Dallas, Texas, USA</td><td>11:00 PM 07-May-26</td></tr>
                </table>
                """
                return CollectedPage(url="https://www.nvoids.com/index.jsp", html=html)

            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                html = """
                <html><body>
                <table>
                  <tr><td>Senior Python Developer at Dallas, Texas, USA</td></tr>
                  <tr><td>Email: recruiter@example.com</td></tr>
                  <tr><td>No phone number mentioned anywhere in this posting.<br>Java, Spring Boot</td></tr>
                  <tr><td>recruiter@example.com | View All</td></tr>
                  <tr><td>11:00 PM 07-May-26</td></tr>
                </table>
                </body></html>
                """
                return CollectedPage(url=url, html=html)

        main.external_feed_service.collector = _NoPhoneSingleItemCollector()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            ext_row = (
                db.query(ExternalOpportunity)
                .filter(ExternalOpportunity.owner_id == main.settings.owner_id, ExternalOpportunity.source_type == "nvoids")
                .one()
            )
            self.assertEqual(ext_row.bridge_status, "ignored")

            self.assertEqual(
                db.query(NumberReviewQueue)
                .filter(NumberReviewQueue.source_external_opportunity_id == ext_row.id)
                .count(),
                0,
            )
            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).count(), 0)

    def test_settings_round_trip_includes_nvoids_locations_and_preferred_employer_cc(self) -> None:
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
                "nvoids_detail_title_mode": "hotlist_details",
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
                "preferred_employer_cc_emails": [
                    "Sheshwika@HorizonsOfTech.net",
                    "Ops@HorizonsOfTech.net",
                    "sheshwika@horizonsoftech.net",
                ],
                "default_employer_cc_emails": ["Fallback@HorizonsOfTech.net"],
                "resume_display_name": "Chaithanya Dheeraj Resume",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["nvoids_detail_title_mode"], "hotlist_details")
        self.assertEqual(payload["nvoids_locations"], ["texas", "remote"])
        self.assertTrue(payload["feature_ai_extractor_enabled"])
        self.assertEqual(payload["draft_text_size"], "huge")
        self.assertEqual(
            payload["preferred_employer_cc_emails"],
            ["sheshwika@horizonsoftech.net", "ops@horizonsoftech.net"],
        )
        self.assertEqual(payload["default_employer_cc_emails"], ["fallback@horizonsoftech.net"])
        self.assertEqual(payload["preferred_employer_cc_email"], "sheshwika@horizonsoftech.net")
        self.assertEqual(payload["resume_display_name"], "Chaithanya Dheeraj Resume")

    def test_settings_default_includes_nvoids_detail_title_mode(self) -> None:
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["nvoids_detail_title_mode"], "job_details")

    def test_legacy_settings_payload_does_not_wipe_screening_profile_fields(self) -> None:
        with self.SessionLocal() as db:
            settings_row = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).one()
            settings_row.feature_role_manifest_enabled = True
            settings_row.feature_strict_candidate_screening_enabled = True
            settings_row.candidate_work_authorizations_json = '["H1B"]'
            settings_row.candidate_total_experience_years = 7
            settings_row.candidate_us_experience_years = 5
            settings_row.candidate_current_location = "Dallas, TX"
            db.commit()

        response = self.client.put("/settings", json={"enabled": False})

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertTrue(payload["feature_role_manifest_enabled"])
        self.assertTrue(payload["feature_strict_candidate_screening_enabled"])
        self.assertEqual(payload["candidate_work_authorizations"], ["H1B"])
        self.assertEqual(payload["candidate_total_experience_years"], 7)
        self.assertEqual(payload["candidate_us_experience_years"], 5)
        self.assertEqual(payload["candidate_current_location"], "Dallas, TX")

        cleared = self.client.put(
            "/settings",
            json={
                "feature_role_manifest_enabled": False,
                "feature_strict_candidate_screening_enabled": False,
                "candidate_work_authorizations": None,
                "candidate_total_experience_years": None,
                "candidate_us_experience_years": None,
                "candidate_current_location": None,
            },
        )

        self.assertEqual(cleared.status_code, 200, cleared.text)
        cleared_payload = cleared.json()
        self.assertFalse(cleared_payload["feature_role_manifest_enabled"])
        self.assertFalse(cleared_payload["feature_strict_candidate_screening_enabled"])
        self.assertEqual(cleared_payload["candidate_work_authorizations"], [])
        self.assertIsNone(cleared_payload["candidate_total_experience_years"])
        self.assertIsNone(cleared_payload["candidate_us_experience_years"])
        self.assertEqual(cleared_payload["candidate_current_location"], "")

    def test_settings_bootstrap_returns_atomic_settings_domain_payload(self) -> None:
        settings_res = self.client.get("/settings")
        self.assertEqual(settings_res.status_code, 200, settings_res.text)

        bootstrap_res = self.client.get("/settings/bootstrap")
        self.assertEqual(bootstrap_res.status_code, 200, bootstrap_res.text)
        payload = bootstrap_res.json()

        self.assertEqual(payload["settings"], settings_res.json())
        self.assertEqual(
            payload["role_manifest_child_creation_enabled"],
            main.settings.role_manifest_child_creation_enabled,
        )
        self.assertEqual(payload["owner_id"], settings_res.json()["owner_id"])
        self.assertIn("loaded_at", payload)
        self.assertIsInstance(payload["resumes"], list)
        self.assertIsInstance(payload["attachments"], list)
        self.assertIsInstance(payload["pending_skills"], list)
        self.assertIsInstance(payload["pending_job_intent_signals"], list)
        self.assertIsInstance(payload["approved_job_intent_signals"], list)

    def test_settings_bootstrap_can_defer_learning_queues(self) -> None:
        with (
            patch.object(
                main,
                "_list_pending_unknown_skills",
                side_effect=AssertionError("pending skills should be deferred"),
            ),
            patch.object(
                main,
                "_list_job_intent_entries",
                side_effect=AssertionError("job intent queues should be deferred"),
            ),
        ):
            response = self.client.get("/settings/bootstrap?include_learning_data=false")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pending_skills"], [])
        self.assertEqual(response.json()["pending_job_intent_signals"], [])
        self.assertEqual(response.json()["approved_job_intent_signals"], [])

    def test_settings_reject_invalid_preferred_employer_cc_email(self) -> None:
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
                "nvoids_detail_title_mode": "job_details",
                "nvoids_locations": [],
                "feature_auto_send": False,
                "feature_retry_queue": False,
                "feature_ai_enabled": False,
                "feature_ai_extractor_enabled": False,
                "feature_semantic_enabled": False,
                "draft_text_size": "normal",
                "fallback_draft_template": "",
                "signature_name": "",
                "signature_phone": "",
                "signature_email": "",
                "preferred_employer_cc_email": "not-an-email",
                "resume_display_name": "",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 422, res.text)

    def test_settings_reject_invalid_multi_value_employer_cc_email(self) -> None:
        res = self.client.put(
            "/settings",
            json={
                "preferred_employer_cc_emails": ["valid@example.com", "not-an-email"],
                "default_employer_cc_emails": ["fallback@example.com"],
            },
        )
        self.assertEqual(res.status_code, 422, res.text)

    def test_settings_reject_invalid_nvoids_detail_title_mode(self) -> None:
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
                "nvoids_detail_title_mode": "wrong",
                "nvoids_locations": [],
                "feature_auto_send": False,
                "feature_retry_queue": False,
                "feature_ai_enabled": False,
                "feature_ai_extractor_enabled": False,
                "feature_semantic_enabled": False,
                "draft_text_size": "normal",
                "fallback_draft_template": "",
                "signature_name": "",
                "signature_phone": "",
                "signature_email": "",
                "preferred_employer_cc_email": "",
                "resume_display_name": "",
                "policy": None,
            },
        )
        self.assertEqual(res.status_code, 422, res.text)

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

    def test_pending_skill_api_marks_suspicious_unknown_skill_blobs_from_skills_json(self) -> None:
        suspicious_blob = "angularjs next js jquery redux bootstrap material ui sass html node js express express js spring spring boot postgresql mysql mongodb redis dynamodb cassandra oracle aws azure gcp docker kubernetes terraform ansible jenkins github actions gitlab ci"
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="blob@example.com",
                    subject="Blob",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java",
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    skills_json=json.dumps(
                        {
                            "skills_text": "Java, PromptForge",
                            "known": ["Java"],
                            "unknown": [suspicious_blob, "PromptForge"],
                            "evidence": {"unknown": [suspicious_blob, "PromptForge"]},
                            "unknown_source": "ai",
                        }
                    ),
                )
            )
            db.commit()

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        by_name = {item["skill_name"]: item for item in pending.json()}
        self.assertEqual(set(by_name), {suspicious_blob, "PromptForge"})
        self.assertTrue(by_name[suspicious_blob]["suspicious"])
        self.assertTrue(by_name[suspicious_blob]["recoverable_skills"])
        self.assertEqual(by_name[suspicious_blob]["source_tags"], ["ai"])
        self.assertFalse(by_name["PromptForge"]["suspicious"])

    def test_pending_skill_api_marks_suspicious_legacy_unknown_skill_blobs(self) -> None:
        suspicious_blob = "javascript typescript java sql react react js angular angularjs next js jquery redux bootstrap material ui sass html node js express express js spring spring boot postgresql mysql mongodb redis"
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="legacy-blob@example.com",
                    subject="Legacy Blob",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java",
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    parser_details_json=json.dumps({"unknown_skills": [suspicious_blob, "PromptForge"]}),
                )
            )
            db.commit()

        pending = self.client.get("/settings/skills/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        by_name = {item["skill_name"]: item for item in pending.json()}
        self.assertEqual(set(by_name), {suspicious_blob, "PromptForge"})
        self.assertTrue(by_name[suspicious_blob]["suspicious"])
        self.assertEqual(by_name[suspicious_blob]["source_tags"], ["legacy"])

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

    def test_bulk_approve_pending_skills_approves_only_clean_atomic_entries(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="bulk-one@example.com",
                        subject="Bulk One",
                        body="Body",
                        role="Engineer",
                        location="Remote",
                        salary_text="",
                        skills_text="Java",
                        score=0,
                        decision="qualified",
                        state="needs_review",
                        source="gmail",
                        skills_json='{"skills_text":"Java, Nebula Workflow Grid, Adaptive Prompt Forge","known":["Java"],"unknown":["Nebula Workflow Grid","Adaptive Prompt Forge","with a focus on IAM"],"evidence":{"Nebula Workflow Grid":"ai_extractor"},"unknown_source":"ai"}',
                    ),
                    RecruiterEmail(
                        owner_id=main.settings.owner_id,
                        sender="bulk-two@example.com",
                        subject="Bulk Two",
                        body="Body",
                        role="Engineer",
                        location="Remote",
                        salary_text="",
                        skills_text="Java",
                        score=0,
                        decision="qualified",
                        state="needs_review",
                        source="gmail",
                        parser_details_json='{"unknown_skills":["Adaptive Prompt Forge","Nebula Workflow Grid","with a focus on IAM"]}',
                    ),
                ]
            )
            db.commit()

        pending_before = self.client.get("/settings/skills/pending")
        self.assertEqual(pending_before.status_code, 200, pending_before.text)
        self.assertEqual(
            [item["skill_name"] for item in pending_before.json()],
            ["Adaptive Prompt Forge", "Nebula Workflow Grid", "with a focus on IAM"],
        )
        polluted = next(item for item in pending_before.json() if item["skill_name"] == "with a focus on IAM")
        self.assertTrue(polluted["suspicious"])
        self.assertEqual(polluted["recoverable_skills"], ["IAM"])
        self.assertEqual(polluted["source_tags"], ["ai", "legacy"])

        approved = self.client.post("/settings/skills/approve-all")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(
            approved.json(),
            {
                "processed_count": 3,
                "approved_count": 2,
                "skipped_count": 1,
                "approved_skill_names": ["Adaptive Prompt Forge", "Nebula Workflow Grid"],
            },
        )

        pending_after = self.client.get("/settings/skills/pending")
        self.assertEqual(pending_after.status_code, 200, pending_after.text)
        self.assertEqual([item["skill_name"] for item in pending_after.json()], ["with a focus on IAM"])

        listed = self.client.get("/settings/skills/approved")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [item["canonical_name"] for item in listed.json()],
            ["Adaptive Prompt Forge", "Nebula Workflow Grid"],
        )

    def test_bulk_approve_pending_skills_returns_zero_counts_when_empty(self) -> None:
        approved = self.client.post("/settings/skills/approve-all")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(
            approved.json(),
            {
                "processed_count": 0,
                "approved_count": 0,
                "skipped_count": 0,
                "approved_skill_names": [],
            },
        )

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
        self.assertTrue(str(payload["run_key"]).startswith("nvoids_sync:"))

        with self.SessionLocal() as db:
            rows = db.query(ExternalOpportunity).filter(ExternalOpportunity.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].location, "Remote, USA")

        items = self.client.get(f"/recent-runs/{payload['run_key']}/items?outcome=skipped&limit=10")
        self.assertEqual(items.status_code, 200, items.text)
        item_payload = items.json()
        self.assertEqual(len(item_payload["items"]), 1)
        self.assertEqual(item_payload["items"][0]["reason_code"], "skipped_location")
        self.assertEqual(item_payload["items"][0]["source_type"], "nvoids")
        self.assertTrue(str(item_payload["items"][0]["source_url"]).startswith("https://"))

    def test_sync_job_details_mode_skips_hotlist_detail_pages(self) -> None:
        class _MixedTitleCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                page = super().fetch_detail_page(url=url)
                if "id=2" not in url:
                    return page
                return CollectedPage(
                    url=page.url,
                    html=page.html.replace("<title>Job Details</title>", "<title>Hotlist Details</title>", 1),
                )

        main.external_feed_service.collector = _MixedTitleCollector()
        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(main.external_feed_service.collector.last_hotlist_mode, "Exclude Hotlists")
        self.assertEqual(payload["created_count"], 1)

        with self.SessionLocal() as db:
            rows = db.query(ExternalOpportunity).filter(ExternalOpportunity.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(rows), 1)
            self.assertIn("Senior Python Developer", rows[0].role)

        items = self.client.get(f"/recent-runs/{payload['run_key']}/items?outcome=skipped&limit=10")
        self.assertEqual(items.status_code, 200, items.text)
        skipped_items = items.json()["items"]
        title_skip = next((row for row in skipped_items if row["reason_code"] == "skipped_nvoids_page_title"), None)
        self.assertIsNotNone(title_skip)
        assert title_skip is not None
        self.assertIn("Hotlist Details", title_skip["reason_detail"])

    def test_sync_hotlist_details_mode_uses_only_hotlists_and_skips_job_details_pages(self) -> None:
        class _MixedTitleCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                page = super().fetch_detail_page(url=url)
                if "id=2" not in url:
                    return page
                return CollectedPage(
                    url=page.url,
                    html=page.html.replace("<title>Job Details</title>", "<title>Hotlist Details</title>", 1),
                )

        main.external_feed_service.collector = _MixedTitleCollector()
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.nvoids_detail_title_mode = "hotlist_details"
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(main.external_feed_service.collector.last_hotlist_mode, "Only Hotlists")
        self.assertEqual(payload["created_count"], 1)

        with self.SessionLocal() as db:
            rows = db.query(ExternalOpportunity).filter(ExternalOpportunity.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(rows), 1)
            self.assertIn("React Developer", rows[0].role)

    def test_sync_all_mode_uses_include_hotlists_and_allows_both_titles(self) -> None:
        class _MixedTitleCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                page = super().fetch_detail_page(url=url)
                if "id=2" not in url:
                    return page
                return CollectedPage(
                    url=page.url,
                    html=page.html.replace("<title>Job Details</title>", "<title>Hotlist Details</title>", 1),
                )

        main.external_feed_service.collector = _MixedTitleCollector()
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.nvoids_detail_title_mode = "all"
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(main.external_feed_service.collector.last_hotlist_mode, "Include Hotlists")
        self.assertEqual(payload["created_count"], 2)

        with self.SessionLocal() as db:
            rows = db.query(ExternalOpportunity).filter(ExternalOpportunity.owner_id == main.settings.owner_id).all()
            self.assertEqual(len(rows), 2)

    def test_sync_records_duplicate_candidate_skip_item_for_recent_runs(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="Recruiter <recruiter_1@example.com>",
                    subject="Existing Nvoids Candidate",
                    body="Body",
                    role="Senior Python Developer",
                    location="Dallas, Texas, USA",
                    salary_text="",
                    skills_text="Java",
                    score=80,
                    decision="Qualified",
                    state="needs_review",
                    source="nvoids",
                    external_message_id="nvoids:nvoids:1",
                    external_thread_id="https://www.nvoids.com/job1.jsp?id=1",
                )
            )
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        items = self.client.get(f"/recent-runs/{payload['run_key']}/items?outcome=skipped&limit=20")
        self.assertEqual(items.status_code, 200, items.text)
        skipped_items = items.json()["items"]
        duplicate_item = next((row for row in skipped_items if row["reason_code"] == "duplicate_candidate"), None)
        self.assertIsNotNone(duplicate_item)
        assert duplicate_item is not None
        self.assertEqual(duplicate_item["source_type"], "nvoids")
        self.assertTrue(str(duplicate_item["source_url"]).startswith("https://"))

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
                self.assertIn("AI draft for Nvoids", row.draft_reply)
                self.assertEqual(row.sendability_status, "sendable")
                self.assertEqual(row.screening_mode, "compatibility")
                self.assertEqual(row.eligibility_status, "not_enforced")
                self.assertEqual(row.draft_source, "deepseek")
                self.assertEqual(row.draft_model, "deepseek-chat")
                self.assertEqual(row.draft_resume_context_status, "injected")
                self.assertEqual(row.ai_score_source, "v2_rules_plus_semantic")
                self.assertIsNotNone(row.ats_score)
                self.assertEqual(row.ats_score_source, "hybrid_structured_plus_semantic")
                self.assertIsNotNone(row.ats_summary)
                self.assertIsNotNone(row.ats_breakdown_json)
                self.assertIsNotNone(row.resume_picker_score)
                self.assertIsNotNone(row.resume_picker_reason)
                self.assertIsNotNone(row.resume_picker_candidates_json)
                self.assertIsNotNone(row.resume_picker_breakdown_json)
                self.assertEqual(row.semantic_input_source, "latest_block")
                self.assertEqual(row.semantic_chunks, 2)
                self.assertEqual(row.semantic_embedding, "[0.1,0.2]")
        finally:
            external_feed_service_module.generate_reply_with_ai_or_fallback = original_generate
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = original_compute

    def test_sync_reuses_resume_selection_semantic_result_without_queue_rescore(self) -> None:
        self._add_resume()
        original_select = main.external_feed_service.scoring_runtime.select_best_resume_match
        original_compute = main.external_feed_service.scoring_runtime.compute_blended_ai_score
        try:
            with self.SessionLocal() as db:
                settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
                assert settings is not None
                settings.feature_semantic_enabled = True
                settings.qualification_threshold = 0.6
                db.commit()

            rescore_calls = {"count": 0}

            def _unexpected_rescore(**_kwargs: object) -> tuple[float, str, str, str | None, str | None, object]:
                rescore_calls["count"] += 1
                return (
                    0.91,
                    "unexpected rescore",
                    "v2_rules_plus_semantic",
                    "[9.9,9.9]",
                    "[8.8,8.8]",
                    SimpleNamespace(input_source="latest_block", input_chars=42, chunks=1, fallback_reason=None),
                )

            def _precomputed_selection(**kwargs: object) -> object:
                resume = kwargs.get("fallback_resume") or (kwargs.get("resumes") or [None])[0]
                return SimpleNamespace(
                    resume=resume,
                    ai_score=0.93,
                    ai_summary="precomputed semantic match",
                    ai_score_source="v2_rules_plus_semantic",
                    final_resume_score=0.88,
                    selection_reason="Final 0.88; ai=0.93; ats=80.00",
                    candidate_rankings_json='{"rankings":[{"resume_file_name":"resume.pdf","final_resume_score":0.88}]}',
                    picker_breakdown_json='{"selection_status":"ready_to_submit"}',
                    ats_score=80.0,
                    ats_score_source="hybrid_structured_plus_semantic",
                    ats_summary="ATS hybrid score 80/100",
                    ats_breakdown_json='{"matched_raw_skills":["Java"]}',
                    email_embedding_json="[0.1,0.2]",
                    resume_embedding_json="[0.3,0.4]",
                    semantic_diag=SimpleNamespace(
                        input_source="chunked",
                        input_chars=1200,
                        chunks=3,
                        fallback_reason=None,
                        keyword_source="parsed_only",
                        thread_snapshot_used=False,
                        thread_snapshot_email_id=None,
                    ),
                )

            main.external_feed_service.scoring_runtime.select_best_resume_match = _precomputed_selection
            main.external_feed_service.scoring_runtime.compute_blended_ai_score = _unexpected_rescore

            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)
            self.assertEqual(rescore_calls["count"], 0)

            with self.SessionLocal() as db:
                row = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .order_by(RecruiterEmail.id.desc())
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.ai_score_source, "v2_rules_plus_semantic")
                self.assertEqual(row.semantic_input_source, "chunked")
                self.assertEqual(row.semantic_chunks, 3)
                self.assertEqual(row.semantic_embedding, "[0.1,0.2]")
        finally:
            main.external_feed_service.scoring_runtime.select_best_resume_match = original_select
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
                self.assertEqual(row.screening_mode, "compatibility")
                self.assertEqual(row.eligibility_status, "not_enforced")
                self.assertEqual(row.draft_resume_context_status, "missing_resume")
                self.assertTrue((row.external_thread_id or "").startswith("https://"))
                self.assertTrue(row.draft_reply.strip())
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

    def test_sync_skips_missing_cc_when_routing_toggle_is_on(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.preferred_employer_cc_email = ""
            settings.preferred_employer_cc_emails = ""
            settings.default_employer_cc_emails = ""
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

    def test_sync_allows_missing_cc_when_routing_toggle_is_off(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.preferred_employer_cc_email = ""
            settings.preferred_employer_cc_emails = ""
            settings.default_employer_cc_emails = ""
            settings.policy_json = json.dumps(
                {
                    "qualification": {
                        "draft_rules": {
                            "recipient_mapping": {"mode": "warn"},
                        }
                    }
                }
            )
            db.commit()

        sync = self.client.post("/external-feeds/nvoids/sync")
        self.assertEqual(sync.status_code, 200, sync.text)

        with self.SessionLocal() as db:
            row = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                .order_by(RecruiterEmail.id.asc())
                .first()
            )
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "needs_review")
            self.assertEqual(row.cc_email, None)
            self.assertEqual(row.routing_status, "missing")

    def test_sync_allows_missing_recruiter_email_when_routing_toggle_is_off(self) -> None:
        class _MissingEmailCollector(_FakeCollector):
            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                page = super().fetch_detail_page(url=url)
                html = page.html.replace("Email: recruiter_1@example.com", "Contact us soon").replace(
                    "recruiter_1@example.com | View All",
                    "View All",
                )
                return CollectedPage(url=page.url, html=html)

        original_collector = main.external_feed_service.collector
        try:
            main.external_feed_service.collector = _MissingEmailCollector()
            with self.SessionLocal() as db:
                settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
                assert settings is not None
                settings.policy_json = json.dumps(
                    {
                        "qualification": {
                            "draft_rules": {
                                "recipient_mapping": {"mode": "warn"},
                            }
                        }
                    }
                )
                db.commit()

            sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)

            with self.SessionLocal() as db:
                row = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == main.settings.owner_id, RecruiterEmail.source == "nvoids")
                    .order_by(RecruiterEmail.id.asc())
                    .first()
                )
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.state, "needs_review")
                self.assertEqual(row.recipient_email, None)
                self.assertEqual(row.routing_status, "missing")
        finally:
            main.external_feed_service.collector = original_collector

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
                  <tr><td>Backend Development Design, develop, and maintain scalable backend services using Java, Spring Boot, and Microservices architecture.</td></tr>
                  <tr><td>saurabhampstek@gmail.com | View All</td></tr>
                  <tr><td>11:00 PM 07-May-26</td></tr>
                </table>
                <div>http://bit.ly/4ey8w48 Thanks and Regards data-cfemail protected</div>
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
                expected_role = "Full Stack Developer (Java, Microservices, Spring Boot, API, ReactJS) -- Charlotte, NC, Islin, NJ & Irving, TX"
                self.assertEqual(row.role, expected_role)
                self.assertTrue(row.draft_reply.strip())
                self.assertEqual(row.screening_mode, "compatibility")
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
                self.assertEqual(ext.role, "Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite")
                self.assertEqual(ext.location, "Irving, Texas, USA")
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
                self.assertEqual(row.role, "Looking for GCP AI Engineer in Irving, TX, or Charlotte NC - Onsite")
                self.assertTrue(row.draft_reply.strip())
                self.assertEqual(row.screening_mode, "compatibility")
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
                "Role: Senior Python Developer\n\n"
                "Client: ExampleCo\n\n"
                "Location: Dallas, Texas, USA\n\n"
                "Must have skills\n\n"
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
                self.assertTrue(row.draft_reply.strip())
                self.assertEqual(row.screening_mode, "compatibility")
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

    def test_nvoids_bridge_passes_ai_extracted_job_metadata_to_shared_workflow(self) -> None:
        # Round 4/5 follow-up: Nvoids Opportunity-card job metadata (job_title, location,
        # work_mode, visa, domain, end_client, implementation_partner) must be AI-first, with
        # the crude subject/body regex used only when AI extraction did not run or returned
        # nothing. Fake a successful ai_primary DeepSeek parse and assert the exact values it
        # returns are the ones threaded into capture_premium_numbers_for_nvoids - proving the
        # sync loop -> _bridge_to_recruiter_opportunity -> capture_premium_numbers_for_nvoids
        # wiring, not just that some AI call happened somewhere.
        def _fake_parse_email_with_details(subject: str, body: str, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
            parsed = {
                "role": "Senior Java Developer",
                "location": "Fort Worth, TX",
                "domain": "Airline",
                "end_client": "Major Airline Co",
                "implementation_partner": "Jasvik Solutions",
                "skills_text": "Java, Spring Boot",
            }
            parser_details = {
                "parser_mode": "ai_primary",
                "ai_extractor_result": {
                    "work_mode": "Onsite",
                    "visa_hints": ["H1B", "GC"],
                },
            }
            return parsed, parser_details

        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.feature_ai_extractor_enabled = True
            db.commit()

        workflow = main.external_feed_service.phone_intelligence_workflow
        real_capture = workflow.capture_premium_numbers_for_nvoids
        captured_ai_extractions: list[object] = []

        def _capture_and_delegate(db, item, jd_body, ai_extraction=None):
            captured_ai_extractions.append(ai_extraction)
            return real_capture(db, item, jd_body, ai_extraction)

        original_parse_email_with_details = external_feed_service_module.parse_email_with_details
        try:
            external_feed_service_module.parse_email_with_details = _fake_parse_email_with_details
            with patch.object(
                workflow,
                "capture_premium_numbers_for_nvoids",
                side_effect=_capture_and_delegate,
            ):
                sync = self.client.post("/external-feeds/nvoids/sync")
            self.assertEqual(sync.status_code, 200, sync.text)
        finally:
            external_feed_service_module.parse_email_with_details = original_parse_email_with_details

        self.assertGreaterEqual(len(captured_ai_extractions), 1)
        for ai_extraction in captured_ai_extractions:
            self.assertIsNotNone(ai_extraction)
            self.assertEqual(ai_extraction.job_title, "Senior Java Developer")
            self.assertEqual(ai_extraction.location, "Fort Worth, TX")
            self.assertEqual(ai_extraction.work_mode, "Onsite")
            self.assertEqual(ai_extraction.visa_restrictions, "H1B, GC")
            self.assertEqual(ai_extraction.domain, "Airline")
            self.assertEqual(ai_extraction.end_client, "Major Airline Co")
            self.assertEqual(ai_extraction.implementation_partner, "Jasvik Solutions")

    def test_sync_continues_when_detail_fetch_times_out(self) -> None:
        class _TimeoutCollector(_FakeCollector):
            def reset_detail_fetch_metrics(self) -> None:
                return None

            def get_detail_fetch_metrics(self) -> dict[str, int]:
                return {"retry_count": 2, "failure_count": 1}

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
                latest_run = db.query(external_feed_service_module.ExternalScrapeRun).order_by(external_feed_service_module.ExternalScrapeRun.id.desc()).first()
                self.assertIsNotNone(latest_run)
                assert latest_run is not None
                self.assertIn("detail_fetch_failures=1", latest_run.notes or "")
                self.assertIn("detail_fetch_retries=2", latest_run.notes or "")
                self.assertIn("detail_fetch_fallback_rows=1", latest_run.notes or "")
        finally:
            main.external_feed_service.collector = original_collector

    def test_sync_continues_when_detail_fetch_returns_non_retriable_http_status_error(self) -> None:
        class _StatusErrorCollector(_FakeCollector):
            def reset_detail_fetch_metrics(self) -> None:
                return None

            def get_detail_fetch_metrics(self) -> dict[str, int]:
                return {"retry_count": 0, "failure_count": 1}

            def fetch_detail_page(self, *, url: str) -> CollectedPage:
                if "id=1" in url:
                    request = httpx.Request("GET", url)
                    response = httpx.Response(503, request=request)
                    raise httpx.HTTPStatusError("service unavailable", request=request, response=response)
                return super().fetch_detail_page(url=url)

        original_collector = main.external_feed_service.collector
        try:
            main.external_feed_service.collector = _StatusErrorCollector()
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
                errored_row = next(row for row in ext_rows if row.external_post_id == "nvoids:1")
                self.assertEqual(errored_row.recruiter_email, "")
                self.assertEqual(errored_row.role, "Senior Python Developer")
        finally:
            main.external_feed_service.collector = original_collector

    def test_sync_continues_when_one_row_parser_fails(self) -> None:
        original_parse_external_post = external_feed_service_module.parse_external_post

        def _boom(
            *,
            source_type: str,
            source_url: str,
            title: str,
            location: str,
            posted_text: str,
            raw_body: str,
            raw_html: str,
            nvoids_detail=None,
        ):
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
                nvoids_detail=nvoids_detail,
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

    def test_sync_skips_queue_creation_when_no_cc_configured(self) -> None:
        with self.SessionLocal() as db:
            settings = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings is not None
            settings.preferred_employer_cc_email = ""
            settings.preferred_employer_cc_emails = ""
            settings.default_employer_cc_emails = ""
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
            self.assertEqual(updated_ext.recruiter_name, "Unknown")
            updated_recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == 1).first()
            self.assertIsNone(updated_recruiter)
            recruiter_opp = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.recruiter_number_id == 1).first()
            self.assertIsNone(recruiter_opp)

    def test_backfill_clears_seeded_nvoids_phone_when_detail_rows_do_not_allow_phone_parsing(self) -> None:
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
        self.assertEqual(payload["deleted_placeholder_opportunities"], 1)
        self.assertEqual(payload["deleted_placeholder_recruiters"], 1)

        with self.SessionLocal() as db:
            ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first()
            self.assertIsNotNone(ext)
            assert ext is not None
            self.assertEqual(ext.recruiter_phone, "")
            self.assertIsNone(db.query(PremiumNumberContact).filter(PremiumNumberContact.id == recruiter_id).first())
            self.assertIsNone(db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == opportunity_id).first())

    def test_backfill_recovers_row_3_phone_and_routes_fallback_to_review(self) -> None:
        recruiter_id, opportunity_id, ext_id = self._seed_nvoids_placeholder_recruiter(
            normalized_phone_number="nvoids-seed-bridge",
            display_phone_number="Unknown",
            recruiter_email="bridge@example.com",
            recruiter_name="Unknown",
            company="Unknown",
            external_phone="",
        )

        with self.SessionLocal() as db:
            ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first()
            assert ext is not None
            ext.raw_html = (
                "<html><body><table>"
                "<tr><td>Kafka software Developer at Remote, Remote, USA</td></tr>"
                "<tr><td>Email: bridge@example.com</td></tr>"
                "<tr><td>Hello Professional,<br>From: Shivam Singh<br>Contact: +1 240-657-1540<br>Java/Kafka software Developer - Dallas/onsite 5 days - CTH</td></tr>"
                "<tr><td>bridge@example.com | View All</td></tr>"
                "<tr><td>11:00 PM 07-May-26</td></tr>"
                "</table><div>Outside junk 9999999999</div></body></html>"
            )
            ext.recruiter_phone = ""
            ext.recruiter_name = "Unknown"
            ext.bridge_status = "ignored_no_phone"
            db.commit()

        with patch(
            "app.premium_numbers.extraction._llm_extract",
            return_value=[],
        ), patch(
            "app.premium_numbers.extraction._sbert_keep_candidate",
            return_value=(True, "test"),
        ):
            res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertGreaterEqual(payload["corrected"], 1)
        self.assertEqual(payload["bridged"], 0)
        self.assertEqual(payload["deleted_placeholder_opportunities"], 1)
        self.assertEqual(payload["deleted_placeholder_recruiters"], 1)

        with self.SessionLocal() as db:
            ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first()
            self.assertIsNotNone(ext)
            assert ext is not None
            self.assertIn("240", ext.recruiter_phone or "")
            self.assertEqual(ext.recruiter_name, "Shivam Singh")
            self.assertEqual(ext.bridge_status, "needs_review")

            old_placeholder = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == recruiter_id).first()
            if old_placeholder is not None:
                self.assertEqual(old_placeholder.normalized_phone_number, "12406571540")
                self.assertEqual(old_placeholder.recruiter_name, "Shivam Singh")

            review = db.query(NumberReviewQueue).filter(
                NumberReviewQueue.source_external_opportunity_id == ext_id,
            ).one()
            self.assertEqual(review.owner_name, "Unknown")
            self.assertEqual(review.contact_email, "bridge@example.com")
            self.assertEqual(review.display_phone_number, "(240) 657-1540")
            linked_nvoids_opps = (
                db.query(RecruiterOpportunity)
                .filter(
                    RecruiterOpportunity.owner_id == main.settings.owner_id,
                    RecruiterOpportunity.source_type == "nvoids",
                    RecruiterOpportunity.external_opportunity_id == ext_id,
                )
                .all()
            )
            self.assertEqual(linked_nvoids_opps, [])

    def test_backfill_recovers_ph_no_signature_variant_into_review(self) -> None:
        recruiter_id, _opportunity_id, ext_id = self._seed_nvoids_placeholder_recruiter(
            normalized_phone_number="nvoids-seed-phno",
            display_phone_number="Unknown",
            recruiter_email="sharma.gopal@net2source.com",
            recruiter_name="Unknown",
            company="Unknown",
            external_phone="",
        )

        with self.SessionLocal() as db:
            ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first()
            assert ext is not None
            ext.raw_html = (
                "<html><body><table>"
                "<tr><td>Java AWS Developer at Plano, Texas, USA</td></tr>"
                "<tr><td>Email: sharma.gopal@net2source.com</td></tr>"
                "<tr><td>Best Regards,<br>Gopal Sharma<br>Senior Talent Acquisition - USA<br>Email:<br>sharma.gopal@net2source.com<br>Ph no. (551) 303-0028</td></tr>"
                "<tr><td>sharma.gopal@net2source.com | View All</td></tr>"
                "<tr><td>02:27 AM 26-Jun-26</td></tr>"
                "</table></body></html>"
            )
            ext.recruiter_phone = ""
            ext.recruiter_name = "Unknown"
            ext.bridge_status = "ignored_no_phone"
            db.commit()

        with patch(
            "app.premium_numbers.extraction._llm_extract",
            return_value=[],
        ), patch(
            "app.premium_numbers.extraction._sbert_keep_candidate",
            return_value=(True, "test"),
        ):
            res = self.client.post("/external-feeds/nvoids/backfill-phones?limit=100")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertGreaterEqual(payload["corrected"], 1)
        self.assertEqual(payload["bridged"], 0)

        with self.SessionLocal() as db:
            ext = db.query(ExternalOpportunity).filter(ExternalOpportunity.id == ext_id).first()
            self.assertIsNotNone(ext)
            assert ext is not None
            self.assertEqual(ext.recruiter_name, "Gopal Sharma")
            self.assertIn("551", ext.recruiter_phone or "")
            self.assertEqual(ext.bridge_status, "needs_review")

            review = db.query(NumberReviewQueue).filter(
                NumberReviewQueue.source_external_opportunity_id == ext_id,
            ).one()
            self.assertEqual(review.display_phone_number, "(551) 303-0028")
            self.assertEqual(review.owner_name, "Unknown")
            self.assertEqual(review.contact_email, "sharma.gopal@net2source.com")

            placeholder = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == recruiter_id).first()
            if placeholder is not None:
                self.assertEqual(placeholder.normalized_phone_number, "15513030028")

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
        self.assertEqual(payload["employer_numbers_reformatted"], 1)
        self.assertEqual(payload["review_numbers_reformatted"], 1)

        with self.SessionLocal() as db:
            recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.recruiter_email == "nancy@example.com").first()
            employer = db.query(PremiumNumberContact).filter(PremiumNumberContact.company == "Corp", PremiumNumberContact.is_employer.is_(True)).first()
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
                db.query(PremiumNumberContact)
                .filter(PremiumNumberContact.recruiter_email.in_(["dharma@example.com", "vikas@example.com"]))
                .order_by(PremiumNumberContact.recruiter_email.asc())
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
