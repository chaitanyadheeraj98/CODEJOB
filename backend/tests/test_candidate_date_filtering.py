import json
import os
import unittest
from datetime import UTC, date, datetime

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.main import _mail_date_filter_field, _mail_date_utc_window
from app.models import RecruiterEmail


class CandidateDateFilteringTests(unittest.TestCase):
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

        self._original_repair = main._repair_unknown_role_drafts
        self._original_refresh = main._refresh_unconfirmed_routing
        self._original_fill = main._fill_missing_gmail_rfc_ids
        main._repair_unknown_role_drafts = lambda db, emails: None
        main._refresh_unconfirmed_routing = lambda db, emails: None
        main._fill_missing_gmail_rfc_ids = lambda db, emails: None
        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main._repair_unknown_role_drafts = self._original_repair
        main._refresh_unconfirmed_routing = self._original_refresh
        main._fill_missing_gmail_rfc_ids = self._original_fill
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def add_email(
        self,
        subject: str,
        state: str,
        *,
        gmail_received_at: datetime | None,
        sent_at: datetime | None = None,
        source: str = "gmail",
        created_at: datetime | None = None,
    ) -> None:
        timestamp = created_at or sent_at or gmail_received_at or datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
        with Session(self.engine) as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender="recruiter@example.com",
                    subject=subject,
                    body="Body",
                    role="Python Developer",
                    location="Remote",
                    salary_text="",
                    skills_text="Python",
                    score=80,
                    decision="approved",
                    state=state,
                    draft_reply="Thanks",
                    approval_status="approved" if state == "approved_sent" else "pending",
                    sent_status="sent" if state == "approved_sent" else "not_sent",
                    source=source,
                    external_message_id=f"msg-{subject}",
                    external_thread_id=f"thread-{subject}",
                    gmail_received_at=gmail_received_at,
                    recipient_email="candidate@example.com",
                    cc_email="client@example.com",
                    routing_status="confirmed",
                    routing_confidence=1.0,
                    routing_reason="test",
                    routing_evidence="[]",
                    routing_candidates="[]",
                    routing_confirmed=True,
                    sent_at=sent_at,
                    gmail_sent_id=f"sent-{subject}" if sent_at else None,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            db.commit()

    def candidate_subjects(self, state: str, mail_date: str = "2026-05-12") -> list[str]:
        response = self.client.get("/candidates", params={"state": state, "mail_date": mail_date, "limit": 100})
        self.assertEqual(response.status_code, 200, response.text)
        return [item["subject"] for item in response.json()["items"]]

    def test_sent_state_uses_sent_at_field(self) -> None:
        self.assertEqual(_mail_date_filter_field(["approved_sent"]), "sent_at")
        self.assertEqual(_mail_date_filter_field([" approved_sent "]), "sent_at")

    def test_non_sent_states_use_gmail_received_at_field(self) -> None:
        self.assertEqual(_mail_date_filter_field(["needs_review"]), "gmail_received_at")
        self.assertEqual(_mail_date_filter_field(["failed"]), "gmail_received_at")
        self.assertEqual(_mail_date_filter_field(["needs_review", "approved_sent"]), "gmail_received_at")

    def test_mail_date_window_regular_day_america_chicago(self) -> None:
        start_utc, end_utc = _mail_date_utc_window(date(2026, 5, 12))
        self.assertEqual(start_utc, datetime(2026, 5, 12, 5, 0, tzinfo=UTC))
        self.assertEqual(end_utc, datetime(2026, 5, 13, 5, 0, tzinfo=UTC))
        self.assertEqual((end_utc - start_utc).total_seconds(), 24 * 3600)

    def test_mail_date_window_dst_spring_forward(self) -> None:
        start_utc, end_utc = _mail_date_utc_window(date(2026, 3, 8))
        self.assertEqual(start_utc, datetime(2026, 3, 8, 6, 0, tzinfo=UTC))
        self.assertEqual(end_utc, datetime(2026, 3, 9, 5, 0, tzinfo=UTC))
        self.assertEqual((end_utc - start_utc).total_seconds(), 23 * 3600)

    def test_mail_date_window_dst_fall_back(self) -> None:
        start_utc, end_utc = _mail_date_utc_window(date(2026, 11, 1))
        self.assertEqual(start_utc, datetime(2026, 11, 1, 5, 0, tzinfo=UTC))
        self.assertEqual(end_utc, datetime(2026, 11, 2, 6, 0, tzinfo=UTC))
        self.assertEqual((end_utc - start_utc).total_seconds(), 25 * 3600)

    def test_approved_sent_filters_by_sent_at_local_day(self) -> None:
        self.add_email(
            "sent-on-selected-day",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )
        self.add_email(
            "received-only-on-selected-day",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 13, 6, 0, tzinfo=UTC),
        )

        self.assertEqual(self.candidate_subjects("approved_sent"), ["sent-on-selected-day"])

    def test_needs_review_and_failed_filter_by_gmail_received_at_local_day(self) -> None:
        self.add_email(
            "needs-review-received-selected-day",
            "needs_review",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )
        self.add_email(
            "failed-received-selected-day",
            "failed",
            gmail_received_at=datetime(2026, 5, 12, 17, 0, tzinfo=UTC),
        )
        self.add_email(
            "failed-received-next-day",
            "failed",
            gmail_received_at=datetime(2026, 5, 13, 6, 0, tzinfo=UTC),
        )

        self.assertEqual(
            self.candidate_subjects("needs_review,failed"),
            ["failed-received-selected-day", "needs-review-received-selected-day"],
        )

    def test_mixed_state_requests_keep_gmail_received_at_filtering(self) -> None:
        self.add_email(
            "approved-sent-received-selected-day",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 14, 16, 0, tzinfo=UTC),
        )
        self.add_email(
            "approved-sent-sent-selected-day-only",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 10, 16, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )
        self.add_email(
            "needs-review-received-selected-day",
            "needs_review",
            gmail_received_at=datetime(2026, 5, 12, 17, 0, tzinfo=UTC),
        )

        self.assertCountEqual(
            self.candidate_subjects("approved_sent,needs_review"),
            ["needs-review-received-selected-day", "approved-sent-received-selected-day"],
        )

    def test_cross_day_received_previous_day_sent_selected_day_appears_in_sent_view(self) -> None:
        self.add_email(
            "received-previous-local-day-sent-selected-day",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 12, 4, 30, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 5, 0, tzinfo=UTC),
        )

        self.assertEqual(
            self.candidate_subjects("approved_sent"),
            ["received-previous-local-day-sent-selected-day"],
        )

    def test_sent_filter_includes_start_boundary_and_excludes_end_boundary(self) -> None:
        self.add_email(
            "sent-at-start-boundary",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 11, 15, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 5, 0, tzinfo=UTC),
        )
        self.add_email(
            "sent-before-start-boundary",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 11, 15, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 4, 59, 59, tzinfo=UTC),
        )
        self.add_email(
            "sent-at-end-boundary",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 11, 15, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 13, 5, 0, tzinfo=UTC),
        )

        self.assertEqual(self.candidate_subjects("approved_sent"), ["sent-at-start-boundary"])

    def test_approved_sent_list_orders_by_sent_at_desc_then_created_at_desc(self) -> None:
        self.add_email(
            "older-created-but-most-recently-sent",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 18, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 10, 15, 0, tzinfo=UTC),
        )
        self.add_email(
            "newer-created-but-earlier-sent",
            "approved_sent",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
            sent_at=datetime(2026, 5, 12, 17, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )

        self.assertEqual(
            self.candidate_subjects("approved_sent"),
            ["older-created-but-most-recently-sent", "newer-created-but-earlier-sent"],
        )

    def test_candidate_list_compacts_nested_resume_picker_diagnostics(self) -> None:
        self.add_email(
            "compact-picker-payload",
            "needs_review",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )
        with Session(self.engine) as db:
            row = db.query(RecruiterEmail).filter(RecruiterEmail.subject == "compact-picker-payload").one()
            row.resume_picker_candidates_json = json.dumps(
                {
                    "selected_resume_file_name": "resume.docx",
                    "rankings": [
                        {
                            "resume_file_name": "resume.docx",
                            "final_resume_score": 0.81,
                            "ai_score": 0.82,
                            "ats_score": 79,
                            "selection_reason": "Best match",
                            "picker_breakdown": {"large_evidence": "x" * 10_000},
                        }
                    ],
                }
            )
            db.commit()
            email_id = row.id

        listed = self.client.get("/candidates", params={"state": "needs_review", "limit": 1})
        self.assertEqual(listed.status_code, 200, listed.text)
        list_picker = listed.json()["items"][0]["resume_picker_candidates"]
        self.assertEqual(list_picker["selected_resume_file_name"], "resume.docx")
        self.assertEqual(list_picker["rankings"][0]["selection_reason"], "Best match")
        self.assertNotIn("picker_breakdown", list_picker["rankings"][0])

        detail = self.client.get(f"/candidates/{email_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn("picker_breakdown", detail.json()["resume_picker_candidates"]["rankings"][0])

    def test_large_candidate_list_response_is_gzipped(self) -> None:
        self.add_email(
            "gzip-candidate-payload",
            "needs_review",
            gmail_received_at=datetime(2026, 5, 12, 16, 0, tzinfo=UTC),
        )
        with Session(self.engine) as db:
            row = db.query(RecruiterEmail).filter(RecruiterEmail.subject == "gzip-candidate-payload").one()
            row.body = "compressible body " * 500
            db.commit()

        response = self.client.get(
            "/candidates",
            params={"state": "needs_review", "limit": 1},
            headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")


if __name__ == "__main__":
    unittest.main()
