import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Application, ApplicationEvent, PremiumNumberContact, RecruiterOpportunity, ResumeAsset
from app.services import application_service


class ApplicationServiceTests(unittest.TestCase):
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
    def _sources(db: Session, owner_id: str = "owner") -> tuple[ResumeAsset, RecruiterOpportunity]:
        resume = ResumeAsset(
            owner_id=owner_id,
            file_path="resume.pdf",
            file_name="java-backend.pdf",
            sha256="a" * 64,
            version=3,
        )
        recruiter = PremiumNumberContact(
            owner_id=owner_id,
            normalized_phone_number="12145551212",
            display_phone_number="+1 214 555 1212",
            is_recruiter=True,
            recruiter_name="Priya Patel",
            company="ABC Staffing",
        )
        db.add_all([resume, recruiter])
        db.flush()
        opportunity = RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=recruiter.id,
            gmail_message_id="application-service-opportunity",
            job_title="Senior Java Developer",
            end_client="Bank X",
        )
        db.add(opportunity)
        db.commit()
        return resume, opportunity

    def test_create_snapshots_sources_and_rejects_duplicate(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(db)
            application = application_service.create_application(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            db.commit()

            self.assertEqual(application.resume_version_snapshot, 3)
            self.assertEqual(application.resume_file_name_snapshot, "java-backend.pdf")
            self.assertEqual(application.recruiter_name_snapshot, "Priya Patel")
            self.assertEqual(application.recruiter_company_snapshot, "ABC Staffing")
            self.assertEqual(application.job_title_snapshot, "Senior Java Developer")
            self.assertEqual(application.end_client_snapshot, "Bank X")
            event = db.query(ApplicationEvent).one()
            self.assertEqual(event.event_type, "created")
            self.assertEqual(event.event_source, "system")

            with self.assertRaises(application_service.ApplicationConflictError):
                application_service.create_application(
                    db,
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    recruiter_opportunity_id=opportunity.id,
                )

    def test_status_transitions_set_milestones_and_events(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(db)
            application = application_service.create_application(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            application_service.update_status(db, application, new_status="resume_shared")
            self.assertIsNotNone(application.resume_shared_at)
            self.assertIsNone(application.submitted_to_client_at)
            self.assertIsNone(application.closed_at)

            application_service.update_status(db, application, new_status="submitted_to_client")
            self.assertIsNotNone(application.submitted_to_client_at)
            application_service.update_status(db, application, new_status="hired")
            self.assertIsNotNone(application.closed_at)
            application_service.update_status(db, application, new_status="contacted")
            self.assertIsNone(application.closed_at)
            db.commit()

            status_events = (
                db.query(ApplicationEvent)
                .filter(ApplicationEvent.application_id == application.id, ApplicationEvent.event_type == "status_changed")
                .all()
            )
            self.assertEqual(len(status_events), 4)
            self.assertIn('"from":"hired","to":"contacted"', status_events[-1].metadata_json)

    def test_dashboard_summary_counts_owner_scoped_active_rows(self) -> None:
        now = datetime.now(UTC)

        def application(owner_id: str, status: str, **values: object) -> Application:
            return Application(
                owner_id=owner_id,
                resume_asset_id=int(values.pop("resume_asset_id", 1)),
                resume_version_snapshot=1,
                resume_file_name_snapshot="resume.pdf",
                resume_sha256_snapshot="b" * 64,
                recruiter_opportunity_id=int(values.pop("recruiter_opportunity_id", 1)),
                recruiter_contact_id=1,
                status=status,
                **values,
            )

        with Session(self.engine) as db:
            db.add_all(
                [
                    application("owner", "contacted", next_action_at=now),
                    application("owner", "interview_1", resume_asset_id=2, recruiter_opportunity_id=2),
                    application(
                        "owner",
                        "hired",
                        resume_asset_id=3,
                        recruiter_opportunity_id=3,
                        closed_at=now - timedelta(days=2),
                    ),
                    application(
                        "owner",
                        "contacted",
                        resume_asset_id=4,
                        recruiter_opportunity_id=4,
                        deleted_at=now,
                        next_action_at=now,
                    ),
                    application(
                        "other-owner",
                        "contacted",
                        resume_asset_id=5,
                        recruiter_opportunity_id=5,
                        next_action_at=now,
                    ),
                ]
            )
            db.commit()

            self.assertEqual(
                application_service.dashboard_summary(db, "owner"),
                {"due_today": 1, "waiting_on_recruiter": 1, "interviews": 1, "closed_recent": 1},
            )


if __name__ == "__main__":
    unittest.main()
