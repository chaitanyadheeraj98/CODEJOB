import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationInterview,
    ApplicationSuggestion,
    AttachmentAsset,
    OpportunityLifecycleEvent,
    OpportunitySourceReference,
    PremiumNumberContact,
    ProductivityEvent,
    RecruiterOpportunity,
    RecruiterEmail,
    ResumeAsset,
)
from app.services import application_service, opportunity_lineage_service


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
        db.flush()
        opportunity_lineage_service.create_lineage(
            db,
            owner_id=owner_id,
            origin_type="gmail",
            source_type="gmail",
            external_id="",
            source_url="",
            process_name="test_fixture",
            recruiter_opportunity_id=opportunity.id,
        )
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
            lineage_status_events = (
                db.query(OpportunityLifecycleEvent)
                .filter(
                    OpportunityLifecycleEvent.event_type == "status_changed",
                    OpportunityLifecycleEvent.related_record_id == application.id,
                )
                .all()
            )
            self.assertEqual(len(lineage_status_events), 4)
            self.assertEqual(
                lineage_status_events[-1].process_name,
                "application_service",
            )

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

    @staticmethod
    def _application(
        *,
        owner_id: str = "owner",
        resume_asset_id: int,
        opportunity_id: int,
        status: str = "matched",
        job_title: str = "Senior Java Developer",
        end_client: str = "Bank X",
        created_at: datetime | None = None,
        deleted_at: datetime | None = None,
    ) -> Application:
        now = created_at or datetime.now(UTC)
        return Application(
            owner_id=owner_id,
            resume_asset_id=resume_asset_id,
            resume_version_snapshot=1,
            resume_file_name_snapshot=f"resume-{resume_asset_id}.pdf",
            resume_sha256_snapshot=str(resume_asset_id % 10) * 64,
            recruiter_opportunity_id=opportunity_id,
            recruiter_contact_id=1,
            job_title_snapshot=job_title,
            end_client_snapshot=end_client,
            status=status,
            created_at=now,
            updated_at=now,
            deleted_at=deleted_at,
        )

    def test_duplicate_candidates_apply_window_status_owner_and_normalization_rules(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            target = self._application(resume_asset_id=10, opportunity_id=10)
            recent_match = self._application(
                resume_asset_id=11,
                opportunity_id=11,
                job_title="  Java   Developer ",
                end_client=" bank   x ",
            )
            closed = self._application(resume_asset_id=12, opportunity_id=12, status="rejected")
            old = self._application(
                resume_asset_id=13,
                opportunity_id=13,
                created_at=now - timedelta(days=46),
            )
            other_owner = self._application(
                owner_id="other-owner",
                resume_asset_id=14,
                opportunity_id=14,
            )
            deleted = self._application(
                resume_asset_id=15,
                opportunity_id=15,
                deleted_at=now,
            )
            db.add_all([target, recent_match, closed, old, other_owner, deleted])
            db.commit()

            matches = application_service.find_duplicate_candidates(
                db,
                owner_id="owner",
                end_client="BANK X",
                job_title="Senior Java Developer",
                exclude_application_id=target.id,
            )
            self.assertEqual([row.id for row in matches], [recent_match.id])

            with self.assertRaises(application_service.ApplicationDuplicateWarning) as warning:
                application_service.submit_to_client(db, target)
            self.assertEqual([row.id for row in warning.exception.candidates], [recent_match.id])
            self.assertEqual(target.status, "matched")

            application_service.submit_to_client(db, target, override_duplicate_warning=True)
            db.commit()
            self.assertEqual(target.status, "submitted_to_client")
            override = (
                db.query(ApplicationEvent)
                .filter_by(application_id=target.id, event_type="duplicate_override")
                .one()
            )
            self.assertIn(str(recent_match.id), override.metadata_json)

            no_match = self._application(
                resume_asset_id=16,
                opportunity_id=16,
                job_title="Python Developer",
                end_client="Bank Y",
            )
            db.add(no_match)
            db.commit()
            row, candidates = application_service.submit_to_client(db, no_match)
            self.assertEqual(row.status, "submitted_to_client")
            self.assertEqual(candidates, [])

    def test_rtr_proof_interviews_and_closed_reason_validation(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(db)
            application = application_service.create_application(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            db.flush()

            rtr = application_service.request_rtr(
                db,
                application,
                role_scope="Senior Java Developer",
                end_client_scope="Bank X",
            )
            self.assertEqual(application.status, "rtr_requested")
            with self.assertRaises(application_service.ApplicationValidationError):
                application_service.confirm_rtr(db, application, rtr)

            other_attachment = AttachmentAsset(
                owner_id="other-owner",
                file_path="other.pdf",
                file_name="other.pdf",
                sha256="c" * 64,
                file_size=10,
            )
            attachment = AttachmentAsset(
                owner_id="owner",
                file_path="rtr.pdf",
                file_name="rtr.pdf",
                sha256="d" * 64,
                file_size=10,
            )
            db.add_all([other_attachment, attachment])
            db.flush()
            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                application_service.confirm_rtr(
                    db,
                    application,
                    rtr,
                    proof_attachment_id=other_attachment.id,
                )
            application_service.confirm_rtr(
                db,
                application,
                rtr,
                proof_attachment_id=attachment.id,
            )
            self.assertEqual(rtr.status, "confirmed")
            self.assertEqual(application.status, "rtr_confirmed")
            application_service.expire_or_revoke_rtr(
                db,
                application,
                rtr,
                new_status="revoked",
            )
            self.assertEqual(rtr.status, "revoked")
            db.flush()
            lineage_event = (
                db.query(OpportunityLifecycleEvent)
                .filter_by(event_type="rtr_status_changed")
                .one()
            )
            self.assertIn('"to":"revoked"', lineage_event.metadata_json)

            interview = application_service.add_interview(
                db,
                application,
                round_type="interview_2",
            )
            self.assertEqual(application.status, "interview_2")
            application_service.update_interview(db, interview, result="passed", feedback="Strong round")
            self.assertEqual(interview.result, "passed")
            self.assertEqual(interview.feedback, "Strong round")
            application_service.delete_interview(db, interview)
            self.assertIsNotNone(interview.deleted_at)
            self.assertIsInstance(interview, ApplicationInterview)

            with self.assertRaises(application_service.ApplicationValidationError):
                application_service.update_status(
                    db,
                    application,
                    new_status="contacted",
                    closed_reason_code="rate_mismatch",
                )
            application_service.update_status(
                db,
                application,
                new_status="withdrawn",
                closed_reason_code="rate_mismatch",
            )
            self.assertEqual(application.closed_reason_code, "rate_mismatch")

    def test_attach_source_reference_integrity_race_preserves_outer_transaction(self) -> None:
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id="owner",
                sender="source@example.com",
                subject="Role",
                body="Body",
                external_message_id="source-race",
            )
            db.add(email)
            db.flush()
            lineage = opportunity_lineage_service.create_lineage(
                db,
                owner_id="owner",
                origin_type="gmail",
                source_type="gmail",
                external_id=str(email.id),
                source_url="https://mail.example/source",
                process_name="test",
            )
            db.flush()
            existing = db.query(OpportunitySourceReference).one()
            outer_event = ProductivityEvent(
                owner_id="owner",
                event_type="outer_transaction_survives",
            )
            db.add(outer_event)

            with patch.object(
                opportunity_lineage_service,
                "_source_reference",
                side_effect=[None, existing],
            ):
                returned = opportunity_lineage_service.attach_source_reference(
                    db,
                    lineage_id=lineage.id,
                    source_type="gmail",
                    external_id=str(email.id),
                    source_url="https://mail.example/new-source",
                )
            db.commit()

            self.assertEqual(returned.id, existing.id)
            self.assertEqual(
                db.query(OpportunitySourceReference).count(),
                1,
            )
            self.assertIsNotNone(db.get(ProductivityEvent, outer_event.id))

    def test_accept_and_dismiss_application_suggestions(self) -> None:
        with Session(self.engine) as db:
            resume, opportunity = self._sources(db)
            application = application_service.create_application(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
            )
            email = RecruiterEmail(
                owner_id="owner",
                sender="recruiter@example.com",
                subject="Reply",
                body="Reply body",
                external_message_id="suggestion-proof",
            )
            db.add(email)
            db.flush()

            link = ApplicationSuggestion(
                owner_id="owner",
                application_id=application.id,
                suggestion_type="link_reply",
                recruiter_email_id=email.id,
                reply_message_id=7,
                reason="Link reply",
            )
            db.add(link)
            db.flush()
            application_service.accept_suggestion(db, link, application=application)
            self.assertEqual(link.status, "accepted")
            self.assertEqual(
                db.query(ApplicationEvent).filter_by(application_id=application.id, event_type="email_linked").count(),
                1,
            )

            status_change = ApplicationSuggestion(
                owner_id="owner",
                application_id=application.id,
                suggestion_type="status_change",
                suggested_status="client_reviewing",
                reason="Client is reviewing",
            )
            db.add(status_change)
            db.flush()
            application_service.accept_suggestion(db, status_change, application=application)
            self.assertEqual(application.status, "client_reviewing")

            next_action = ApplicationSuggestion(
                owner_id="owner",
                application_id=application.id,
                suggestion_type="next_action",
                suggested_next_action_type="Follow up",
                suggested_next_action_at=datetime.now(UTC) + timedelta(days=1),
                reason="Follow up",
            )
            db.add(next_action)
            db.flush()
            override = datetime.now(UTC) + timedelta(days=2)
            application_service.accept_suggestion(
                db,
                next_action,
                application=application,
                override_next_action_at=override,
            )
            self.assertEqual(application.next_action_type, "Follow up")
            self.assertEqual(application.next_action_at, override)

            stale = ApplicationSuggestion(
                owner_id="owner",
                application_id=application.id,
                suggestion_type="stale_prompt",
                reason="Close or continue?",
            )
            db.add(stale)
            db.flush()
            application_service.accept_suggestion(db, stale, application=application)
            self.assertEqual(application.status, "client_reviewing")
            self.assertEqual(db.query(ApplicationEvent).filter_by(event_type="note").count(), 1)

            dismissed = ApplicationSuggestion(
                owner_id="owner",
                application_id=application.id,
                suggestion_type="status_change",
                suggested_status="rejected",
                reason="Dismiss me",
            )
            db.add(dismissed)
            db.flush()
            application_service.dismiss_suggestion(db, dismissed)
            self.assertEqual(dismissed.status, "dismissed")
            self.assertEqual(application.status, "client_reviewing")


if __name__ == "__main__":
    unittest.main()
