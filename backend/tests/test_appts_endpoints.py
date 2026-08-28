"""HTTP-level endpoint tests for the AppTS / Needs Review badges / Premium Contacts /
Date filter feature set added to app/main.py on the semantic-embeddings branch.

Fixture pattern mirrors tests/test_approve_cc_regression.py: in-memory sqlite +
StaticPool, app.dependency_overrides[main.get_db], TestClient(main.app), a seeded
UserSettings row for main.settings.owner_id.
"""

import os
import uuid
import unittest
from datetime import UTC, date, datetime, timedelta

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    Application,
    ApplicationInterview,
    ApplicationRTR,
    AppTSApplication,
    AppTSApplicationInterview,
    AppTSApplicationRTR,
    PremiumNumberContact,
    RecruiterEmail,
    ResumeAsset,
    UserSettings,
)


class AppTSEndpointTestBase(unittest.TestCase):
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

    # ---- fixture helpers -------------------------------------------------

    def _add_resume(self, db: Session, *, file_name: str = "resume.pdf") -> ResumeAsset:
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        resume = ResumeAsset(
            owner_id=main.settings.owner_id,
            file_path=path,
            file_name=file_name,
            mime_type="application/pdf",
            sha256=f"sha-{uuid.uuid4().hex[:8]}",
            version=1,
            is_enabled=True,
            is_current=True,
            semantic_embedding=None,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    def _add_email(
        self,
        db: Session,
        *,
        state: str = "needs_review",
        marked: bool = False,
        sender: str = "Recruiter <recruiter@example.com>",
        subject: str = "Java role",
        recipient_email: str | None = None,
        cc_email: str | None = None,
        source: str = "manual",
        role: str = "Java Developer",
        company: str | None = None,
        end_client: str | None = None,
        resume_asset_id: int | None = None,
        received_at: datetime | None = None,
        routing_confirmed: bool = True,
        sent_at: datetime | None = None,
    ) -> RecruiterEmail:
        now = received_at or datetime.now(UTC)
        suffix = uuid.uuid4().hex[:10]
        email = RecruiterEmail(
            owner_id=main.settings.owner_id,
            sender=sender,
            subject=subject,
            body="Body text mentioning the role and requirements.",
            role=role,
            location="remote",
            salary_text="$60/hr",
            skills_text="Java,Spring",
            score=85,
            decision="Qualified",
            state=state,
            decision_reason="test fixture",
            draft_reply="Subject: x\n\nHi,\n\nBody",
            draft_source="rules_only",
            approval_status="pending",
            sent_status="sent" if state == "approved_sent" else "not_sent",
            source=source,
            external_message_id=f"msg-{suffix}",
            external_thread_id=f"thread-{suffix}",
            gmail_received_at=now,
            recipient_email=recipient_email,
            cc_email=cc_email,
            routing_status="safe",
            routing_confidence=0.9,
            routing_reason="test fixture",
            routing_evidence="[]",
            routing_candidates="[]",
            routing_confirmed=routing_confirmed,
            marked_for_tracking=marked,
            resume_asset_id=resume_asset_id,
            company=company,
            end_client=end_client,
            sent_at=sent_at,
            created_at=now,
            updated_at=now,
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    def _add_contact(
        self,
        db: Session,
        *,
        recruiter_email: str,
        phone: str | None = None,
        verification_level: str = "unverified",
        created_at: datetime | None = None,
        recruiter_name: str = "Some Recruiter",
        company: str = "Some Co",
    ) -> PremiumNumberContact:
        now = created_at or datetime.now(UTC)
        contact = PremiumNumberContact(
            owner_id=main.settings.owner_id,
            normalized_phone_number=phone,
            display_phone_number=phone or "",
            phone_is_valid=True,
            is_recruiter=True,
            recruiter_name=recruiter_name,
            company=company,
            recruiter_email=recruiter_email.strip().lower(),
            recruiter_verification_level=verification_level,
            created_at=now,
            updated_at=now,
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        return contact

    def _business_ts(self, offset_days: int = 0, hour: int = 12) -> datetime:
        today = datetime.now(main.BUSINESS_TZ).date()
        start, _ = main._mail_date_utc_window(today - timedelta(days=offset_days))
        return start + timedelta(hours=hour)

    def _add_legacy_application(self, db: Session, resume: ResumeAsset, *, dedupe_key: str) -> Application:
        row = Application(
            owner_id=main.settings.owner_id,
            resume_asset_id=resume.id,
            resume_version_snapshot=resume.version,
            resume_file_name_snapshot=resume.file_name,
            resume_sha256_snapshot=resume.sha256,
            manual_recruiter_name="Legacy Recruiter",
            manual_recruiter_company="Legacy Co",
            manual_job_title="Legacy Role",
            manual_end_client="Legacy Client",
            dedupe_key=dedupe_key,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _manual_payload(self, resume_id: int, *, dedupe_key: str | None = None, **overrides: object) -> dict:
        payload = {
            "resume_asset_id": resume_id,
            "dedupe_key": dedupe_key or f"manual-{uuid.uuid4().hex[:10]}",
            "manual_recruiter_name": "Jane Recruiter",
            "manual_recruiter_company": "Staffing Co",
            "manual_job_title": "Java Developer",
            "manual_end_client": "Acme Bank",
        }
        payload.update(overrides)
        return payload


# ---------------------------------------------------------------------------
# 1. Track / track-bulk
# ---------------------------------------------------------------------------


class TrackToggleTests(AppTSEndpointTestBase):
    def test_track_toggle_on_needs_review_flips_flag(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="needs_review", marked=False)

        response = self.client.post(f"/candidates/{email.id}/track")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["marked_for_tracking"])
        with Session(self.engine) as db:
            refreshed = db.get(RecruiterEmail, email.id)
            assert refreshed is not None
            self.assertTrue(refreshed.marked_for_tracking)

        response2 = self.client.post(f"/candidates/{email.id}/track")
        self.assertEqual(response2.status_code, 200, response2.text)
        self.assertFalse(response2.json()["marked_for_tracking"])
        with Session(self.engine) as db:
            refreshed = db.get(RecruiterEmail, email.id)
            assert refreshed is not None
            self.assertFalse(refreshed.marked_for_tracking)

    def test_track_on_approved_sent_creates_appts_application_retroactively(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            email = self._add_email(
                db,
                state="approved_sent",
                resume_asset_id=resume.id,
                company="Acme Recruiting",
                end_client="Acme Client",
                sent_at=datetime.now(UTC),
            )
            resume_id = resume.id
            email_id = email.id

        response = self.client.post(f"/candidates/{email_id}/track")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            app_row = db.query(AppTSApplication).filter(AppTSApplication.dedupe_key == f"appts_email:{email_id}").first()
            assert app_row is not None
            self.assertEqual(app_row.resume_asset_id, resume_id)
            self.assertEqual(app_row.recruiter_company_snapshot, "Acme Recruiting")
            self.assertEqual(app_row.end_client_snapshot, "Acme Client")
            self.assertEqual(app_row.resume_submission_status, "submitted")

        # Calling /track again on the same already-sent card must not duplicate the row.
        response2 = self.client.post(f"/candidates/{email_id}/track")
        self.assertEqual(response2.status_code, 200, response2.text)
        with Session(self.engine) as db:
            count = db.query(AppTSApplication).filter(AppTSApplication.dedupe_key == f"appts_email:{email_id}").count()
            self.assertEqual(count, 1)

    def test_track_on_approved_sent_without_resume_does_not_create_or_error(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="approved_sent", resume_asset_id=None, sent_at=datetime.now(UTC))
            email_id = email.id

        response = self.client.post(f"/candidates/{email_id}/track")
        self.assertEqual(response.status_code, 200, response.text)
        with Session(self.engine) as db:
            self.assertEqual(db.query(AppTSApplication).count(), 0)

    def test_track_on_other_state_returns_400(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="rejected")

        response = self.client.post(f"/candidates/{email.id}/track")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "Only needs_review or approved_sent candidates can be tracked")

    def test_track_missing_candidate_returns_404(self) -> None:
        response = self.client.post("/candidates/999999/track")
        self.assertEqual(response.status_code, 404, response.text)


class TrackBulkTests(AppTSEndpointTestBase):
    def test_track_bulk_sets_explicit_value_and_leaves_other_rows_untouched(self) -> None:
        with Session(self.engine) as db:
            e1 = self._add_email(db, marked=False)
            e2 = self._add_email(db, marked=False)
            e3 = self._add_email(db, marked=False)
            ids = (e1.id, e2.id, e3.id)

        response = self.client.post("/candidates/track-bulk", json={"ids": [ids[0], ids[1]], "tracked": True})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(sorted(body["succeeded_ids"]), sorted([ids[0], ids[1]]))
        self.assertEqual(body["failed"], [])
        with Session(self.engine) as db:
            self.assertTrue(db.get(RecruiterEmail, ids[0]).marked_for_tracking)
            self.assertTrue(db.get(RecruiterEmail, ids[1]).marked_for_tracking)
            self.assertFalse(db.get(RecruiterEmail, ids[2]).marked_for_tracking)

        response2 = self.client.post("/candidates/track-bulk", json={"ids": [ids[0]], "tracked": False})
        self.assertEqual(response2.status_code, 200, response2.text)
        with Session(self.engine) as db:
            self.assertFalse(db.get(RecruiterEmail, ids[0]).marked_for_tracking)
            self.assertTrue(db.get(RecruiterEmail, ids[1]).marked_for_tracking)
            self.assertFalse(db.get(RecruiterEmail, ids[2]).marked_for_tracking)

    def test_track_bulk_reports_failure_for_non_needs_review_row(self) -> None:
        with Session(self.engine) as db:
            e1 = self._add_email(db, state="needs_review")
            e2 = self._add_email(db, state="rejected")
            ids = (e1.id, e2.id)

        response = self.client.post("/candidates/track-bulk", json={"ids": list(ids), "tracked": True})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["succeeded_ids"], [ids[0]])
        self.assertEqual(len(body["failed"]), 1)
        self.assertEqual(body["failed"][0]["id"], ids[1])
        with Session(self.engine) as db:
            self.assertTrue(db.get(RecruiterEmail, ids[0]).marked_for_tracking)
            self.assertFalse(db.get(RecruiterEmail, ids[1]).marked_for_tracking)


# ---------------------------------------------------------------------------
# 2. Bookmarked -> Tracked lifecycle
# ---------------------------------------------------------------------------


class BookmarkedLifecycleTests(AppTSEndpointTestBase):
    def test_bookmark_then_approve_send_moves_card_from_bookmarked_to_tracked(self) -> None:
        original_append_tracking = main.append_tracking_sheet_row
        try:
            main.append_tracking_sheet_row = lambda **_kwargs: None
            with Session(self.engine) as db:
                self._add_resume(db)
                email = self._add_email(
                    db,
                    state="needs_review",
                    source="manual",
                    recipient_email="ankit.negi@codinix.com",
                    cc_email="vaishnavi@horizonsoftech.net",
                )
                email_id = email.id

            track_response = self.client.post(f"/candidates/{email_id}/track")
            self.assertEqual(track_response.status_code, 200, track_response.text)
            self.assertTrue(track_response.json()["marked_for_tracking"])

            bookmarked = self.client.get("/appts/bookmarked-requirements")
            self.assertEqual(bookmarked.status_code, 200, bookmarked.text)
            self.assertIn(email_id, [item["id"] for item in bookmarked.json()["items"]])

            approve_response = self.client.post(f"/candidates/{email_id}/approve-send", json={"edited_reply": None})
            self.assertEqual(approve_response.status_code, 200, approve_response.text)
            self.assertEqual(approve_response.json()["state"], "approved_sent")

            bookmarked_after = self.client.get("/appts/bookmarked-requirements")
            self.assertEqual(bookmarked_after.status_code, 200, bookmarked_after.text)
            self.assertNotIn(email_id, [item["id"] for item in bookmarked_after.json()["items"]])

            tracked = self.client.get("/appts/applications")
            self.assertEqual(tracked.status_code, 200, tracked.text)
            self.assertEqual(tracked.json()["total"], 1)
            item = tracked.json()["items"][0]
            self.assertEqual(item["dedupe_key"], f"appts_email:{email_id}")
            self.assertEqual(item["status"], "resume_shared")
            self.assertEqual(item["resume_submission_status"], "submitted")

            with Session(self.engine) as db:
                self.assertEqual(db.query(Application).count(), 0)
        finally:
            main.append_tracking_sheet_row = original_append_tracking


# ---------------------------------------------------------------------------
# 3. Reject / fail clears marked_for_tracking
# ---------------------------------------------------------------------------


class ClearTrackingFlagTests(AppTSEndpointTestBase):
    def test_reject_clears_flag_and_drops_from_bookmarked(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="needs_review", marked=True)
            email_id = email.id

        bookmarked = self.client.get("/appts/bookmarked-requirements")
        self.assertIn(email_id, [item["id"] for item in bookmarked.json()["items"]])

        response = self.client.post(f"/candidates/{email_id}/reject", json={"reason": "not a fit"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["marked_for_tracking"])
        self.assertEqual(response.json()["state"], "rejected")

        with Session(self.engine) as db:
            refreshed = db.get(RecruiterEmail, email_id)
            assert refreshed is not None
            self.assertFalse(refreshed.marked_for_tracking)

        bookmarked_after = self.client.get("/appts/bookmarked-requirements")
        self.assertNotIn(email_id, [item["id"] for item in bookmarked_after.json()["items"]])

    def test_send_to_failed_mapping_clears_flag_and_drops_from_bookmarked(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="needs_review", marked=True)
            email_id = email.id

        response = self.client.post(f"/candidates/{email_id}/send-to-failed-mapping")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["marked_for_tracking"])
        self.assertEqual(response.json()["state"], "failed")

        with Session(self.engine) as db:
            refreshed = db.get(RecruiterEmail, email_id)
            assert refreshed is not None
            self.assertFalse(refreshed.marked_for_tracking)

        bookmarked_after = self.client.get("/appts/bookmarked-requirements")
        self.assertNotIn(email_id, [item["id"] for item in bookmarked_after.json()["items"]])

    def test_reject_bulk_clears_flag_for_all_rows(self) -> None:
        with Session(self.engine) as db:
            e1 = self._add_email(db, state="needs_review", marked=True)
            e2 = self._add_email(db, state="needs_review", marked=True)
            ids = (e1.id, e2.id)

        response = self.client.post("/candidates/reject-bulk", json={"ids": list(ids)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(response.json()["succeeded_ids"]), sorted(ids))

        with Session(self.engine) as db:
            for candidate_id in ids:
                row = db.get(RecruiterEmail, candidate_id)
                assert row is not None
                self.assertFalse(row.marked_for_tracking)
                self.assertEqual(row.state, "rejected")

    def test_send_to_failed_mapping_bulk_clears_flag_for_all_rows(self) -> None:
        with Session(self.engine) as db:
            e1 = self._add_email(db, state="needs_review", marked=True)
            e2 = self._add_email(db, state="needs_review", marked=True)
            ids = (e1.id, e2.id)

        response = self.client.post("/candidates/send-to-failed-mapping-bulk", json={"ids": list(ids)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sorted(response.json()["succeeded_ids"]), sorted(ids))

        with Session(self.engine) as db:
            for candidate_id in ids:
                row = db.get(RecruiterEmail, candidate_id)
                assert row is not None
                self.assertFalse(row.marked_for_tracking)
                self.assertEqual(row.state, "failed")


# ---------------------------------------------------------------------------
# 4. Manual AppTS creation
# ---------------------------------------------------------------------------


class ManualAppTSCreationTests(AppTSEndpointTestBase):
    def test_create_manual_success_then_idempotent_on_same_dedupe_key(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            resume_id = resume.id

        payload = self._manual_payload(resume_id, dedupe_key="manual-dedupe-1")
        response = self.client.post("/appts/applications/manual", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "resume_shared")
        self.assertEqual(body["resume_submission_status"], "submitted")
        self.assertEqual(body["dedupe_key"], "manual-dedupe-1")
        created_id = body["id"]

        response2 = self.client.post("/appts/applications/manual", json=payload)
        self.assertEqual(response2.status_code, 200, response2.text)
        self.assertEqual(response2.json()["id"], created_id)

        with Session(self.engine) as db:
            self.assertEqual(db.query(AppTSApplication).count(), 1)

    def test_create_manual_missing_required_field_returns_4xx(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            resume_id = resume.id

        payload = self._manual_payload(resume_id, manual_recruiter_name="")
        response = self.client.post("/appts/applications/manual", json=payload)
        self.assertEqual(response.status_code, 422, response.text)

    def test_create_manual_whitespace_only_field_returns_422_from_service_validation(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            resume_id = resume.id

        # Passes pydantic's min_length=1 (one space char) but fails the service-level
        # .strip() check in appts_service.create_tracked_application_manual.
        payload = self._manual_payload(resume_id, manual_recruiter_name=" ")
        response = self.client.post("/appts/applications/manual", json=payload)
        self.assertEqual(response.status_code, 422, response.text)

    def test_create_manual_missing_resume_returns_404(self) -> None:
        payload = self._manual_payload(999999)
        response = self.client.post("/appts/applications/manual", json=payload)
        self.assertEqual(response.status_code, 404, response.text)


# ---------------------------------------------------------------------------
# 5. Idempotent from-submission promotion
# ---------------------------------------------------------------------------


class PromoteFromSubmissionTests(AppTSEndpointTestBase):
    def test_promote_creates_appts_row_without_touching_legacy_and_is_idempotent(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            legacy = self._add_legacy_application(db, resume, dedupe_key="legacy-dedupe-1")
            legacy_id = legacy.id
            legacy_status_before = legacy.status
            legacy_updated_at_before = legacy.updated_at

        response = self.client.post(f"/appts/applications/from-submission/{legacy_id}")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["dedupe_key"], f"appts_promoted:{legacy_id}")
        self.assertEqual(body["manual_recruiter_name"], "Legacy Recruiter")
        appts_id = body["id"]

        with Session(self.engine) as db:
            legacy_after = db.get(Application, legacy_id)
            assert legacy_after is not None
            self.assertEqual(legacy_after.status, legacy_status_before)
            self.assertEqual(legacy_after.updated_at, legacy_updated_at_before)
            self.assertIsNone(legacy_after.deleted_at)

        response2 = self.client.post(f"/appts/applications/from-submission/{legacy_id}")
        self.assertEqual(response2.status_code, 200, response2.text)
        self.assertEqual(response2.json()["id"], appts_id)

        with Session(self.engine) as db:
            self.assertEqual(
                db.query(AppTSApplication).filter(AppTSApplication.dedupe_key == f"appts_promoted:{legacy_id}").count(),
                1,
            )

    def test_promote_missing_legacy_application_returns_404(self) -> None:
        response = self.client.post("/appts/applications/from-submission/999999")
        self.assertEqual(response.status_code, 404, response.text)


# ---------------------------------------------------------------------------
# 6. RTR / interview / submit-to-client lifecycle on an AppTS row
# ---------------------------------------------------------------------------


class AppTSLifecycleTests(AppTSEndpointTestBase):
    def test_full_lifecycle_writes_only_to_appts_tables(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            resume_id = resume.id
            proof_email = self._add_email(db, state="needs_review")
            proof_email_id = proof_email.id

        created = self.client.post("/appts/applications/manual", json=self._manual_payload(resume_id))
        self.assertEqual(created.status_code, 201, created.text)
        app_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "resume_shared")

        rtr_response = self.client.post(
            f"/appts/applications/{app_id}/rtr",
            json={"role_scope": "Java", "end_client_scope": "Acme Bank"},
        )
        self.assertEqual(rtr_response.status_code, 201, rtr_response.text)
        rtr_body = rtr_response.json()
        self.assertEqual(rtr_body["status"], "rtr_requested")
        self.assertEqual(len(rtr_body["rtr_history"]), 1)
        self.assertEqual(rtr_body["rtr_history"][0]["status"], "requested")
        rtr_id = rtr_body["rtr_history"][0]["id"]

        confirm_response = self.client.patch(
            f"/appts/applications/{app_id}/rtr/{rtr_id}",
            json={"status": "confirmed", "proof_recruiter_email_id": proof_email_id},
        )
        self.assertEqual(confirm_response.status_code, 200, confirm_response.text)
        confirm_body = confirm_response.json()
        self.assertEqual(confirm_body["status"], "rtr_confirmed")
        self.assertEqual(confirm_body["rtr_history"][0]["status"], "confirmed")

        interview_response = self.client.post(
            f"/appts/applications/{app_id}/interviews",
            json={"round_type": "interview_1"},
        )
        self.assertEqual(interview_response.status_code, 201, interview_response.text)
        interview_body = interview_response.json()
        self.assertEqual(interview_body["status"], "interview_1")
        self.assertEqual(len(interview_body["interviews"]), 1)
        interview_id = interview_body["interviews"][0]["id"]

        patch_interview_response = self.client.patch(
            f"/appts/applications/{app_id}/interviews/{interview_id}",
            json={"result": "passed", "feedback": "Went well"},
        )
        self.assertEqual(patch_interview_response.status_code, 200, patch_interview_response.text)
        patched_interview = patch_interview_response.json()["interviews"][0]
        self.assertEqual(patched_interview["result"], "passed")
        self.assertEqual(patched_interview["feedback"], "Went well")

        delete_response = self.client.delete(f"/appts/applications/{app_id}/interviews/{interview_id}")
        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        self.assertEqual(delete_response.json()["interviews"], [])

        submit_response = self.client.post(f"/appts/applications/{app_id}/submit-to-client", json={})
        self.assertEqual(submit_response.status_code, 200, submit_response.text)
        self.assertEqual(submit_response.json()["status"], "submitted_to_client")

        with Session(self.engine) as db:
            self.assertEqual(db.query(AppTSApplicationRTR).filter(AppTSApplicationRTR.application_id == app_id).count(), 1)
            interview_row = db.query(AppTSApplicationInterview).filter(AppTSApplicationInterview.application_id == app_id).first()
            assert interview_row is not None
            self.assertIsNotNone(interview_row.deleted_at)
            self.assertEqual(interview_row.result, "passed")
            final = db.get(AppTSApplication, app_id)
            assert final is not None
            self.assertEqual(final.status, "submitted_to_client")
            # Nothing should have touched the legacy application_service tables.
            self.assertEqual(db.query(Application).count(), 0)
            self.assertEqual(db.query(ApplicationRTR).count(), 0)
            self.assertEqual(db.query(ApplicationInterview).count(), 0)

    def test_rtr_confirm_requires_proof(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            resume_id = resume.id

        created = self.client.post("/appts/applications/manual", json=self._manual_payload(resume_id))
        app_id = created.json()["id"]
        rtr_response = self.client.post(f"/appts/applications/{app_id}/rtr", json={})
        rtr_id = rtr_response.json()["rtr_history"][0]["id"]

        response = self.client.patch(
            f"/appts/applications/{app_id}/rtr/{rtr_id}",
            json={"status": "confirmed"},
        )
        self.assertEqual(response.status_code, 422, response.text)


# ---------------------------------------------------------------------------
# 7. Date filter precedence
# ---------------------------------------------------------------------------


class DateFilterPrecedenceTests(AppTSEndpointTestBase):
    def test_list_candidates_needs_review_date_filter_precedence(self) -> None:
        with Session(self.engine) as db:
            today_email = self._add_email(db, state="needs_review", received_at=self._business_ts(0))
            yesterday_email = self._add_email(db, state="needs_review", received_at=self._business_ts(1))
            today_id, yesterday_id = today_email.id, yesterday_email.id
            settings_row = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings_row is not None
            settings_row.mail_date = "2020-01-01"
            db.commit()

        # (a) No date_filter/date_from/date_to -> unchanged legacy behavior: no date
        # narrowing at all when mail_date query param is also omitted, both rows show up.
        plain = self.client.get("/candidates", params={"state": "needs_review"})
        self.assertEqual(plain.status_code, 200, plain.text)
        plain_ids = {item["id"] for item in plain.json()["items"]}
        self.assertIn(today_id, plain_ids)
        self.assertIn(yesterday_id, plain_ids)

        # Legacy mail_date query param still narrows exactly as before.
        today_str = datetime.now(main.BUSINESS_TZ).date().isoformat()
        legacy_filtered = self.client.get("/candidates", params={"state": "needs_review", "mail_date": today_str})
        self.assertEqual(legacy_filtered.status_code, 200, legacy_filtered.text)
        legacy_ids = {item["id"] for item in legacy_filtered.json()["items"]}
        self.assertIn(today_id, legacy_ids)
        self.assertNotIn(yesterday_id, legacy_ids)

        # (b) Explicit date_filter override takes precedence for this request only.
        today_filtered = self.client.get("/candidates", params={"state": "needs_review", "date_filter": "today"})
        self.assertEqual(today_filtered.status_code, 200, today_filtered.text)
        today_ids = {item["id"] for item in today_filtered.json()["items"]}
        self.assertIn(today_id, today_ids)
        self.assertNotIn(yesterday_id, today_ids)

        yesterday_filtered = self.client.get("/candidates", params={"state": "needs_review", "date_filter": "yesterday"})
        self.assertEqual(yesterday_filtered.status_code, 200, yesterday_filtered.text)
        yesterday_ids = {item["id"] for item in yesterday_filtered.json()["items"]}
        self.assertIn(yesterday_id, yesterday_ids)
        self.assertNotIn(today_id, yesterday_ids)

        # settings.mail_date must never be mutated by the override request.
        with Session(self.engine) as db:
            settings_row = db.query(UserSettings).filter(UserSettings.owner_id == main.settings.owner_id).first()
            assert settings_row is not None
            self.assertEqual(settings_row.mail_date, "2020-01-01")

        # A follow-up plain request (no override) proves the override didn't leak state.
        plain_again = self.client.get("/candidates", params={"state": "needs_review"})
        plain_again_ids = {item["id"] for item in plain_again.json()["items"]}
        self.assertIn(today_id, plain_again_ids)
        self.assertIn(yesterday_id, plain_again_ids)

    def test_list_premium_number_inventory_date_filter_precedence(self) -> None:
        with Session(self.engine) as db:
            today_contact = self._add_contact(
                db, recruiter_email="today@example.com", phone="+15550000001", created_at=self._business_ts(0)
            )
            today_contact_id = today_contact.id
            yesterday_contact = self._add_contact(
                db, recruiter_email="yesterday@example.com", phone="+15550000002", created_at=self._business_ts(1)
            )
            yesterday_contact_id = yesterday_contact.id

        # Untouched by default: Number Inventory is not driven by the main calendar at all.
        plain = self.client.get("/premium-numbers/inventory")
        self.assertEqual(plain.status_code, 200, plain.text)
        plain_keys = {item["key"] for item in plain.json()["items"]}
        self.assertIn(f"contact:{today_contact_id}", plain_keys)
        self.assertIn(f"contact:{yesterday_contact_id}", plain_keys)

        today_filtered = self.client.get("/premium-numbers/inventory", params={"date_filter": "today"})
        self.assertEqual(today_filtered.status_code, 200, today_filtered.text)
        today_keys = {item["key"] for item in today_filtered.json()["items"]}
        self.assertIn(f"contact:{today_contact_id}", today_keys)
        self.assertNotIn(f"contact:{yesterday_contact_id}", today_keys)

        # And the override never leaks into a subsequent plain request.
        plain_again = self.client.get("/premium-numbers/inventory")
        plain_again_keys = {item["key"] for item in plain_again.json()["items"]}
        self.assertIn(f"contact:{today_contact_id}", plain_again_keys)
        self.assertIn(f"contact:{yesterday_contact_id}", plain_again_keys)


# ---------------------------------------------------------------------------
# 8. Badge resolution smoke test
# ---------------------------------------------------------------------------


class BadgeResolutionTests(AppTSEndpointTestBase):
    def test_premium_status_and_verification_level_populate_when_contact_resolves(self) -> None:
        with Session(self.engine) as db:
            self._add_contact(
                db,
                recruiter_email="recruiter1@example.com",
                phone="+15559990001",
                verification_level="verified",
            )
            matching_email = self._add_email(
                db, state="needs_review", sender="Recruiter One <recruiter1@example.com>"
            )
            unrelated_email = self._add_email(
                db, state="needs_review", sender="Nobody <nobody@doesnotexist.example>"
            )
            matching_id, unrelated_id = matching_email.id, unrelated_email.id

        matched_response = self.client.get(f"/candidates/{matching_id}")
        self.assertEqual(matched_response.status_code, 200, matched_response.text)
        matched_body = matched_response.json()
        self.assertEqual(matched_body["premium_status"], "Active")
        self.assertEqual(matched_body["premium_verification_level"], "verified")

        unrelated_response = self.client.get(f"/candidates/{unrelated_id}")
        self.assertEqual(unrelated_response.status_code, 200, unrelated_response.text)
        unrelated_body = unrelated_response.json()
        self.assertIsNone(unrelated_body["premium_status"])
        self.assertIsNone(unrelated_body["premium_verification_level"])

    def test_following_badge_bookmarked_priority_for_third_email_from_same_recruiter(self) -> None:
        with Session(self.engine) as db:
            self._add_contact(
                db,
                recruiter_email="recruiter2@example.com",
                phone="+15559990002",
                verification_level="unverified",
            )
            first_email = self._add_email(
                db, state="needs_review", sender="Recruiter Two <recruiter2@example.com>", marked=False
            )
            second_email = self._add_email(
                db, state="needs_review", sender="Recruiter Two <recruiter2@example.com>", marked=True
            )
            first_id, second_id = first_email.id, second_email.id

        # Resolve + commit identity for the first two rows before the third is created,
        # since _populate_badge_fields runs with autoflush=False: an in-request identity
        # resolution for OTHER rows only becomes visible to the cross-reference query on a
        # later request once it has been committed.
        self.client.get(f"/candidates/{first_id}")
        self.client.get(f"/candidates/{second_id}")

        with Session(self.engine) as db:
            third_email = self._add_email(
                db, state="needs_review", sender="Recruiter Two <recruiter2@example.com>", marked=False
            )
            third_id = third_email.id

        third_response = self.client.get(f"/candidates/{third_id}")
        self.assertEqual(third_response.status_code, 200, third_response.text)
        body = third_response.json()
        self.assertEqual(body["following_badge"], "bookmarked")
        self.assertIsNotNone(body["following_warning"])


# ---------------------------------------------------------------------------
# 9. sent-details guard
# ---------------------------------------------------------------------------


class SentDetailsGuardTests(AppTSEndpointTestBase):
    def test_needs_review_returns_200(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="needs_review")

        response = self.client.get(f"/candidates/{email.id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)

    def test_approved_sent_returns_200(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="approved_sent", sent_at=datetime.now(UTC))

        response = self.client.get(f"/candidates/{email.id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)

    def test_other_state_returns_400(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="rejected")

        response = self.client.get(f"/candidates/{email.id}/sent-details")
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
