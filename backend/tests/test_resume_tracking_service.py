import json
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ApplicationEvent, ApplicationSkillGapSnapshot, ResumeAsset
from app.services import application_service, resume_tracking_service


class ResumeTrackingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _resume(db: Session) -> ResumeAsset:
        resume = ResumeAsset(
            owner_id="owner",
            file_path="java.pdf",
            file_name="java.pdf",
            sha256="a" * 64,
            version=4,
            skills_text="Java, SQL",
            structured_skills_json='["Java","SQL"]',
            primary_role="Java Developer",
            variant_label="Java / Banking",
        )
        db.add(resume)
        db.flush()
        return resume

    @staticmethod
    def _manual(db: Session, resume: ResumeAsset, *, key: str, submitted_at: datetime):
        return resume_tracking_service.create_manual_application(
            db,
            owner_id="owner",
            resume_asset_id=resume.id,
            dedupe_key=key,
            manual_recruiter_name="Priya",
            manual_recruiter_company="ABC Staffing",
            manual_recruiter_email="priya@example.com",
            manual_job_title="Senior Java Developer",
            manual_end_client="Bank X",
            manual_jd_text="Java and AWS are required",
            resume_submitted_at=submitted_at,
        )

    def test_manual_create_is_atomic_backdated_and_proactively_snapshotted(self) -> None:
        submitted_at = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
        with Session(self.engine) as db:
            resume = self._resume(db)
            application, created = self._manual(db, resume, key="manual-action", submitted_at=submitted_at)
            db.commit()

            self.assertTrue(created)
            self.assertIsNone(application.recruiter_opportunity_id)
            self.assertIsNone(application.recruiter_contact_id)
            self.assertEqual(application.resume_submitted_at, submitted_at)
            self.assertEqual(application.recruiter_name_snapshot, "Priya")
            self.assertEqual(application.recruiter_company_snapshot, "ABC Staffing")
            self.assertEqual(json.loads(application.resume_skills_snapshot_json), ["Java", "SQL"])
            self.assertEqual(application.resume_primary_role_snapshot, "Java Developer")
            self.assertIsNotNone(db.query(ApplicationSkillGapSnapshot).filter_by(application_id=application.id).first())

            replay, replay_created = self._manual(db, resume, key="manual-action", submitted_at=submitted_at)
            self.assertFalse(replay_created)
            self.assertEqual(replay.id, application.id)
            self.assertEqual(db.query(ApplicationEvent).filter_by(application_id=application.id).count(), 2)
            self.assertEqual(db.query(ResumeAsset).count(), 1)

    def test_status_ratchet_preserves_milestones_and_funnel_history(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            application, _ = self._manual(
                db,
                resume,
                key="status-action",
                submitted_at=datetime.now(UTC) - timedelta(days=5),
            )
            resume_tracking_service.update_resume_submission_status(db, application, new_status="viewed")
            resume_tracking_service.update_resume_submission_status(db, application, new_status="shortlisted")
            application_service.update_status(db, application, new_status="interview_1")
            resume_tracking_service.update_resume_submission_status(
                db,
                application,
                new_status="rejected",
                rejection_detail_tags=[{"category": "missing_skill", "value": "AWS"}],
            )
            milestones = json.loads(application.milestones_reached_json)
            self.assertEqual(set(milestones), {"viewed", "shortlisted", "interview_scheduled"})
            self.assertEqual(application.status, "rejected")
            with self.assertRaises(application_service.ApplicationValidationError):
                resume_tracking_service.update_resume_submission_status(db, application, new_status="viewed")
            resume_tracking_service.update_resume_submission_status(db, application, new_status="viewed", force=True)
            self.assertEqual(application.resume_submission_status, "viewed")
            application.resume_submission_status = "rejected"

            metrics = resume_tracking_service.resume_funnel_metrics(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
            )
            self.assertEqual(metrics["acceptance_rate"], 1.0)
            self.assertEqual(metrics["interview_rate"], 1.0)
            self.assertEqual(metrics["top_missing_skills"][0], {"value": "AWS", "count": 1})

    def test_skill_gap_is_frozen_until_explicit_recompute(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            application, _ = self._manual(
                db,
                resume,
                key="gap-action",
                submitted_at=datetime.now(UTC),
            )
            snapshot = resume_tracking_service.compute_skill_gap(db, application)
            frozen_at = datetime(2020, 1, 1, tzinfo=UTC)
            snapshot.computed_at = frozen_at
            application.manual_jd_text = "Python is required"
            self.assertEqual(
                resume_tracking_service.compute_skill_gap(db, application).computed_at,
                frozen_at,
            )
            refreshed = resume_tracking_service.compute_skill_gap(db, application, force_recompute=True)
            self.assertGreater(refreshed.computed_at, frozen_at)


if __name__ == "__main__":
    unittest.main()
