import os
import tempfile
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import AttachmentAsset, RecruiterEmail, ResumeAsset, UserSettings


class ApproveCcRegressionTests(unittest.TestCase):
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
        self.client = TestClient(main.app)

        with Session(self.engine) as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    enabled=True,
                    gmail_query="java is:unread",
                    default_gmail_query="java is:unread",
                    default_date_mode="off",
                    qualification_threshold=0.6,
                    feature_ai_enabled=False,
                    feature_semantic_enabled=False,
                    fallback_draft_template="Hi",
                    signature_name="Tester",
                    signature_phone="+1",
                    signature_email="tester@example.com",
                    policy_json="",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _add_resume(self, db: Session) -> ResumeAsset:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        resume = ResumeAsset(
            owner_id=main.settings.owner_id,
            file_path=path,
            file_name="resume.pdf",
            mime_type="application/pdf",
            sha256="abc123",
            version=1,
            is_current=True,
            semantic_embedding=None,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    def _add_needs_review_email(self, db: Session, *, cc_email: str | None) -> RecruiterEmail:
        now = datetime.now(UTC)
        email = RecruiterEmail(
            owner_id=main.settings.owner_id,
            sender="Recruiter <r@example.com>",
            subject="Java role",
            body="Body with ankit.negi@codinix.com and vaishnavi@horizonsoftech.net",
            role="Java Developer",
            location="hybrid",
            salary_text="$60/hr",
            skills_text="Java,Angular,Microservices",
            score=90,
            decision="Qualified",
            state="needs_review",
            decision_reason="Qualified and queued for manual approval",
            draft_reply="Subject: x\n\nHi,\n\nBody",
            draft_source="rules_only",
            draft_model=None,
            draft_ai_error=None,
            approval_status="pending",
            sent_status="not_sent",
            source="gmail",
            external_message_id=f"msg-{now.timestamp()}",
            external_thread_id=f"thread-{now.timestamp()}",
            gmail_received_at=now,
            recipient_email="ankit.negi@codinix.com",
            cc_email=cc_email,
            routing_status="safe",
            routing_confidence=0.9,
            routing_reason="Found distinct recruiter and employer contacts in the current email.",
            routing_evidence='[{"role":"to","email":"ankit.negi@codinix.com","source":"test","detail":"seed"},{"role":"cc","email":"vaishnavi@horizonsoftech.net","source":"test","detail":"seed"}]',
            routing_candidates="[]",
            routing_confirmed=False,
            created_at=now,
            updated_at=now,
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    def _add_needs_review_nvoids_email(self, db: Session, *, cc_email: str | None) -> RecruiterEmail:
        now = datetime.now(UTC)
        email = RecruiterEmail(
            owner_id=main.settings.owner_id,
            sender="Recruiter <nvoids@example.com>",
            subject="Nvoids Java role",
            body="Body from nvoids listing",
            role="Java Developer",
            location="remote",
            salary_text="$70/hr",
            skills_text="Java,Spring",
            score=88,
            decision="Qualified",
            state="needs_review",
            decision_reason="external_feed_nvoids",
            draft_reply="Hi Recruiter,\n\nInterested.\n\nRegards",
            draft_source="rules_only",
            draft_model=None,
            draft_ai_error=None,
            approval_status="pending",
            sent_status="not_sent",
            source="nvoids",
            external_message_id=f"nvoids:{now.timestamp()}",
            external_thread_id=f"nvoids:{now.timestamp()}",
            gmail_received_at=now,
            recipient_email="nvoids@example.com",
            cc_email=cc_email,
            routing_status="safe",
            routing_confidence=0.9,
            routing_reason="External feed recruiter import with employer pool cc.",
            routing_evidence="[]",
            routing_candidates="[]",
            routing_confirmed=False,
            created_at=now,
            updated_at=now,
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    def _add_attachment(self, db: Session, *, file_name: str = "cover-letter.pdf", enabled: bool = True) -> AttachmentAsset:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 extra")
        attachment = AttachmentAsset(
            owner_id=main.settings.owner_id,
            file_path=path,
            file_name=file_name,
            mime_type="application/pdf",
            sha256=f"sha-{file_name}",
            file_size=14,
            is_enabled=enabled,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        return attachment

    def test_approve_send_returns_400_when_cc_missing_even_if_routing_looks_safe(self) -> None:
        with Session(self.engine) as db:
            self._add_resume(db)
            email = self._add_needs_review_email(db, cc_email=None)

        response = self.client.post(f"/candidates/{email.id}/approve-send", json={"edited_reply": None})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "CC email is required before sending")

    def test_gmail_sync_persists_routing_to_and_cc_for_needs_review_candidate(self) -> None:
        original_is_gmail_configured = main.is_gmail_configured
        original_list_unread = main.list_unread_candidates_by_query
        original_is_recruiter_like = main.is_recruiter_like
        original_parse_email = main.parse_email
        original_hard_filter_check = main.hard_filter_check
        original_compute_blended = main._compute_blended_ai_score
        original_should_block_f2f = main.should_block_f2f
        original_analyze_routing = main._analyze_email_routing
        original_build_fallback = main._build_user_fallback_draft
        try:
            main.is_gmail_configured = lambda: True
            main.list_unread_candidates_by_query = lambda _q: [
                {
                    "sender": "Vaishnavi <vaishnavi@horizonsoftech.net>",
                    "subject": "Need local || Java Angular Developer",
                    "body": "Body with ankit.negi@codinix.com and vaishnavi@horizonsoftech.net",
                    "snippet": "Body",
                    "external_message_id": "ext-1",
                    "external_thread_id": "thr-1",
                    "external_rfc_message_id": "rfc-1",
                    "gmail_received_at": datetime.now(UTC),
                    "recipient_email": "legacy-to@example.com",
                }
            ]
            main.is_recruiter_like = lambda _s, _sub, _b: True
            main.parse_email = lambda _s, _b: {
                "role": "Java Developer",
                "location": "hybrid",
                "salary_text": "$60/hr",
                "skills_text": "java,angular,microservices",
            }
            main.hard_filter_check = lambda _p, _u: (True, "pass")
            main._compute_blended_ai_score = lambda **kwargs: (
                0.95,
                "ok",
                "v1_rules_plus_ai",
                None,
                None,
                SimpleNamespace(input_source="latest_block", input_chars=100, chunks=1, fallback_reason=None),
            )
            main.should_block_f2f = lambda _p: (False, None)
            main._analyze_email_routing = lambda _db, _sender, _subject, _body, _snippet="": main.RoutingResult(
                to_email="ankit.negi@codinix.com",
                cc_email="vaishnavi@horizonsoftech.net",
                status="safe",
                confidence=0.9,
                reason="Found distinct recruiter and employer contacts in the current email.",
                evidence=[],
                candidates=[],
            )
            main._build_user_fallback_draft = lambda *args, **kwargs: "Subject: x\n\nHi,\n\nBody"

            response = self.client.post("/gmail/sync")
            self.assertEqual(response.status_code, 200, response.text)

            with Session(self.engine) as db:
                row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "ext-1").first()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.state, "needs_review")
                self.assertIsNotNone(row.recipient_email)
                self.assertIsNotNone(row.cc_email)
                self.assertIn(row.routing_status, {"safe", "confirmed"})
                self.assertGreaterEqual(float(row.routing_confidence or 0.0), 0.8)
                self.assertNotEqual((row.routing_evidence or "").strip(), "")
        finally:
            main.is_gmail_configured = original_is_gmail_configured
            main.list_unread_candidates_by_query = original_list_unread
            main.is_recruiter_like = original_is_recruiter_like
            main.parse_email = original_parse_email
            main.hard_filter_check = original_hard_filter_check
            main._compute_blended_ai_score = original_compute_blended
            main.should_block_f2f = original_should_block_f2f
            main._analyze_email_routing = original_analyze_routing
            main._build_user_fallback_draft = original_build_fallback

    def test_approve_send_succeeds_when_to_cc_resume_and_routing_are_valid(self) -> None:
        original_send_reply = main.send_reply_with_attachment
        original_send_new = main.send_new_email_with_attachment
        original_mark_processed = main.mark_message_processed
        original_append_tracking = main.append_tracking_sheet_row
        sent_reply_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        try:
            main.send_reply_with_attachment = lambda *args, **kwargs: (sent_reply_calls.append((args, kwargs)), "sent-123")[1]
            main.send_new_email_with_attachment = lambda *_args, **_kwargs: "new-123"
            main.mark_message_processed = lambda *_args, **_kwargs: None
            main.append_tracking_sheet_row = lambda **_kwargs: None
            with Session(self.engine) as db:
                self._add_resume(db)
                self._add_attachment(db)
                email = self._add_needs_review_email(db, cc_email="vaishnavi@horizonsoftech.net")

            response = self.client.post(f"/candidates/{email.id}/approve-send", json={"edited_reply": None})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["state"], "approved_sent")
            self.assertEqual(payload["sent_status"], "sent")
            self.assertEqual(len(sent_reply_calls), 1)
            attachments = sent_reply_calls[0][1].get("attachments")
            self.assertIsInstance(attachments, list)
            assert isinstance(attachments, list)
            self.assertEqual(len(attachments), 2)
        finally:
            main.send_reply_with_attachment = original_send_reply
            main.send_new_email_with_attachment = original_send_new
            main.mark_message_processed = original_mark_processed
            main.append_tracking_sheet_row = original_append_tracking

    def test_nvoids_approve_send_uses_new_email_send_and_marks_sent(self) -> None:
        original_send_reply = main.send_reply_with_attachment
        original_send_new = main.send_new_email_with_attachment
        original_append_tracking = main.append_tracking_sheet_row
        sent_reply_calls: list[tuple[object, ...]] = []
        sent_new_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        try:
            main.send_reply_with_attachment = lambda *args, **_kwargs: (sent_reply_calls.append(args), "reply-123")[1]
            main.send_new_email_with_attachment = lambda *args, **kwargs: (sent_new_calls.append((args, kwargs)), "new-456")[1]
            main.append_tracking_sheet_row = lambda **_kwargs: None
            with Session(self.engine) as db:
                self._add_resume(db)
                self._add_attachment(db, file_name="portfolio.zip")
                email = self._add_needs_review_nvoids_email(db, cc_email="vaishnavi@horizonsoftech.net")

            response = self.client.post(f"/candidates/{email.id}/approve-send", json={"edited_reply": None})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["state"], "approved_sent")
            self.assertEqual(payload["sent_status"], "sent")
            self.assertEqual(payload["gmail_sent_id"], "new-456")
            self.assertEqual(len(sent_reply_calls), 0)
            self.assertEqual(len(sent_new_calls), 1)
            attachments = sent_new_calls[0][1].get("attachments")
            self.assertIsInstance(attachments, list)
            assert isinstance(attachments, list)
            self.assertEqual(len(attachments), 2)
        finally:
            main.send_reply_with_attachment = original_send_reply
            main.send_new_email_with_attachment = original_send_new
            main.append_tracking_sheet_row = original_append_tracking

    def test_nvoids_approve_send_requires_resume(self) -> None:
        original_send_new = main.send_new_email_with_attachment
        original_append_tracking = main.append_tracking_sheet_row
        try:
            main.send_new_email_with_attachment = lambda *_args, **_kwargs: "new-456"
            main.append_tracking_sheet_row = lambda **_kwargs: None
            with Session(self.engine) as db:
                email = self._add_needs_review_nvoids_email(db, cc_email="vaishnavi@horizonsoftech.net")

            response = self.client.post(f"/candidates/{email.id}/approve-send", json={"edited_reply": None})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"], "No active resume uploaded")
        finally:
            main.send_new_email_with_attachment = original_send_new
            main.append_tracking_sheet_row = original_append_tracking

    def test_nvoids_approve_send_failure_keeps_needs_review(self) -> None:
        original_send_new = main.send_new_email_with_attachment
        original_append_tracking = main.append_tracking_sheet_row
        try:
            def _raise(*_args, **_kwargs):
                raise RuntimeError("forced nvoids send failure")

            main.send_new_email_with_attachment = _raise
            main.append_tracking_sheet_row = lambda **_kwargs: None
            with Session(self.engine) as db:
                self._add_resume(db)
                email = self._add_needs_review_nvoids_email(db, cc_email="vaishnavi@horizonsoftech.net")

            response = self.client.post(f"/candidates/{email.id}/approve-send", json={"edited_reply": None})
            self.assertEqual(response.status_code, 502, response.text)
            self.assertIn("Gmail send failed", response.json()["detail"])
            with Session(self.engine) as db:
                updated = db.query(RecruiterEmail).filter(RecruiterEmail.id == email.id).first()
                assert updated is not None
                self.assertEqual(updated.state, "needs_review")
                self.assertEqual(updated.sent_status, "not_sent")
                self.assertIn("forced nvoids send failure", updated.last_error or "")
        finally:
            main.send_new_email_with_attachment = original_send_new
            main.append_tracking_sheet_row = original_append_tracking

    def test_send_to_failed_mapping_from_needs_review_marks_routing_unconfirmed(self) -> None:
        with Session(self.engine) as db:
            email = self._add_needs_review_email(db, cc_email="vaishnavi@horizonsoftech.net")

        response = self.client.post(f"/candidates/{email.id}/send-to-failed-mapping")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "failed")
        self.assertFalse(payload["routing_confirmed"])
        self.assertEqual(payload["routing_status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
