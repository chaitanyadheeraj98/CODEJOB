import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Application, RecruiterEmail, ResumeAsset
from app.services import why_this_resume_service as svc


class LinkedEmailIdTests(unittest.TestCase):
    """The application -> email link lives in dedupe_key, not a foreign key."""

    @staticmethod
    def _application(dedupe_key: str | None) -> Application:
        return Application(owner_id="owner", resume_asset_id=1, dedupe_key=dedupe_key)

    def test_recovers_the_email_id_the_backfill_encoded(self):
        self.assertEqual(svc.linked_email_id(self._application("recruiter_email:8042")), 8042)

    def test_manual_entries_have_no_source_email(self):
        # Manual submissions get a uuid dedupe_key, so there is nothing to link to.
        self.assertIsNone(svc.linked_email_id(self._application("0f7c8f1e-1a2b-4c3d-9e8f-000000000000")))
        self.assertIsNone(svc.linked_email_id(self._application(None)))
        self.assertIsNone(svc.linked_email_id(self._application("")))

    def test_a_malformed_key_does_not_raise(self):
        self.assertIsNone(svc.linked_email_id(self._application("recruiter_email:")))
        self.assertIsNone(svc.linked_email_id(self._application("recruiter_email:abc")))


class WhyThisResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _seed(db: Session, *, breakdown: dict | None, candidates: dict | None = None) -> Application:
        resume = ResumeAsset(
            owner_id="owner", file_path="r.docx", file_name="r.docx", sha256="a" * 64,
            version=1, skills_text="Java", variant_label="Banking",
        )
        db.add(resume)
        db.flush()
        email = RecruiterEmail(
            owner_id="owner", sender="rec@example.com", subject="Java Full Stack",
            body="jd", role="Java Full Stack Developer",
            resume_asset_id=resume.id, resume_file_name="r.docx",
            ats_score=60.57, ats_summary="fit 0.60",
            resume_picker_reason="Mandatory FAIL 0.77",
            resume_picker_breakdown_json=json.dumps(breakdown) if breakdown is not None else None,
            resume_picker_candidates_json=json.dumps(candidates) if candidates is not None else None,
        )
        db.add(email)
        db.flush()
        application = Application(
            owner_id="owner", resume_asset_id=resume.id, resume_version_snapshot=1,
            resume_file_name_snapshot="r.docx", resume_sha256_snapshot="a" * 64,
            dedupe_key=f"recruiter_email:{email.id}",
        )
        db.add(application)
        db.flush()
        return application

    def test_reports_the_gate_and_the_missing_skills_from_the_email(self):
        with Session(self.engine) as db:
            application = self._seed(db, breakdown={
                "mandatory_gate_status": "fail",
                "mandatory_coverage": 0.7692,
                "mandatory_missing_skills": ["Redux", "React Hooks"],
                "matched_priority_skills": ["Java", "Spring Boot"],
                "missing_priority_skills": ["Redux"],
                "role_family_fit_score": 0.9,
                "final_resume_score": 0.7226,
                "selection_status": "needs_review",
                "jd_role_family": "java_fullstack",
            })

            result = svc.why_this_resume(db, application)

            self.assertTrue(result["available"])
            self.assertEqual(result["variant_code"], "R01")
            self.assertEqual(result["mandatory_gate_status"], "fail")
            self.assertEqual(result["missing_required"], ["Redux", "React Hooks"])
            self.assertEqual(result["matched_priority"], ["Java", "Spring Boot"])
            self.assertEqual(result["ats_score"], 60.57)
            self.assertEqual(result["jd_role"], "Java Full Stack Developer")

    def test_marks_which_alternative_was_actually_chosen(self):
        with Session(self.engine) as db:
            application = self._seed(
                db,
                breakdown={"mandatory_gate_status": "pass"},
                candidates={
                    "selected_resume_file_name": "r.docx",
                    "rankings": [
                        {"resume_file_name": "r.docx", "final_resume_score": 0.72},
                        {"resume_file_name": "other.docx", "final_resume_score": 0.71},
                    ],
                },
            )

            alternatives = svc.why_this_resume(db, application)["alternatives"]

            self.assertEqual([alt["is_selected"] for alt in alternatives], [True, False])
            # A ranked resume that is no longer in the library has no code, but is
            # still worth showing - it is part of why the winner won.
            self.assertEqual(alternatives[1]["variant_code"], "")

    def test_explains_itself_when_the_email_was_never_scored(self):
        with Session(self.engine) as db:
            application = self._seed(db, breakdown=None)

            result = svc.why_this_resume(db, application)

            self.assertFalse(result["available"])
            self.assertIn("before resume scoring", result["reason_unavailable"])

    def test_explains_itself_for_a_hand_logged_submission(self):
        with Session(self.engine) as db:
            application = Application(
                owner_id="owner", resume_asset_id=1, resume_version_snapshot=1,
                resume_file_name_snapshot="r.docx", resume_sha256_snapshot="a" * 64,
                dedupe_key="manual-uuid",
            )
            db.add(application)
            db.flush()

            result = svc.why_this_resume(db, application)

            self.assertFalse(result["available"])
            self.assertIn("logged by hand", result["reason_unavailable"])

    def test_malformed_breakdown_json_degrades_instead_of_raising(self):
        with Session(self.engine) as db:
            application = self._seed(db, breakdown={"mandatory_gate_status": "pass"})
            email = db.query(RecruiterEmail).one()
            email.resume_picker_breakdown_json = "{not json"
            db.flush()

            result = svc.why_this_resume(db, application)

            self.assertFalse(result["available"])
