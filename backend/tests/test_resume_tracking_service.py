import json
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import _source_recruiter_email_id
from app.models import ApplicationEvent, ApplicationSkillGapSnapshot, PremiumContactEmail, PremiumNumberContact, RecruiterEmail, RecruiterOpportunity, ResumeAsset
from app.services import application_service, appts_service, resume_tracking_service


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

    def test_manual_create_links_an_owned_opportunity_and_rejects_a_foreign_one(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            contact = PremiumNumberContact(owner_id="owner", display_phone_number="", recruiter_name="Priya")
            foreign_contact = PremiumNumberContact(owner_id="other", display_phone_number="", recruiter_name="Other")
            db.add_all([contact, foreign_contact])
            db.flush()
            opportunity = RecruiterOpportunity(owner_id="owner", recruiter_number_id=contact.id, gmail_message_id="owned")
            foreign = RecruiterOpportunity(owner_id="other", recruiter_number_id=foreign_contact.id, gmail_message_id="foreign")
            db.add_all([opportunity, foreign])
            db.flush()

            application, _ = resume_tracking_service.create_manual_application(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                recruiter_opportunity_id=opportunity.id,
                dedupe_key="linked",
                manual_recruiter_name="Priya",
                manual_recruiter_company="ABC Staffing",
                manual_job_title="Senior Java Developer",
                manual_end_client="Bank X",
            )
            self.assertEqual(application.recruiter_opportunity_id, opportunity.id)
            self.assertEqual(application.recruiter_contact_id, contact.id)
            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                resume_tracking_service.create_manual_application(
                    db,
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    recruiter_opportunity_id=foreign.id,
                    dedupe_key="foreign",
                    manual_recruiter_name="Priya",
                    manual_recruiter_company="ABC Staffing",
                    manual_job_title="Senior Java Developer",
                    manual_end_client="Bank X",
                )

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

    def test_combined_metrics_recompute_rates_and_count_promoted_rows_once(self) -> None:
        submitted_at = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
        with Session(self.engine) as db:
            resume = self._resume(db)
            legacy, _ = self._manual(db, resume, key="promoted-legacy", submitted_at=submitted_at)
            promoted, _ = appts_service.promote_legacy_application(db, legacy, owner_id="owner")
            promoted.milestones_reached_json = json.dumps({"shortlisted": submitted_at.isoformat()})
            rejected, _ = appts_service.create_tracked_application_manual(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
                dedupe_key="current-rejected",
                manual_recruiter_name="Priya",
                manual_recruiter_company="ABC Staffing",
                manual_job_title="Senior Java Developer",
                manual_end_client="Bank X",
                resume_submitted_at=submitted_at,
            )
            rejected.status = "rejected"
            rejected.resume_submission_status = "rejected"
            metrics = resume_tracking_service.combined_resume_funnel_metrics(
                db,
                owner_id="owner",
                resume_asset_id=resume.id,
            )

            self.assertEqual(metrics["total_submissions"], 2)
            self.assertEqual(metrics["acceptance_rate"], 0.5)
            self.assertEqual(metrics["shortlist_rate"], 0.5)
            self.assertEqual(metrics["rejection_rate"], 0.5)

    def test_manual_create_accepts_a_blank_end_client(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            base = dict(
                db=db,
                owner_id="owner",
                resume_asset_id=resume.id,
                dedupe_key="blank-client",
                manual_recruiter_name="Priya",
                manual_recruiter_company="ABC Staffing",
                manual_job_title="Senior Java Developer",
                manual_end_client="Bank X",
            )
            for blank_field in ("manual_recruiter_name", "manual_recruiter_company", "manual_job_title"):
                kwargs = dict(base)
                kwargs[blank_field] = "   "
                with self.assertRaises(application_service.ApplicationValidationError):
                    resume_tracking_service.create_manual_application(**kwargs)

            # End client is deliberately not in that list, same as appts_service.
            # Requiring it is what forced the auto-log path below to substitute
            # the recruiter's own company. A blank is accepted and stored blank.
            kwargs = dict(base)
            kwargs["manual_end_client"] = "   "
            application, created = resume_tracking_service.create_manual_application(**kwargs)
            self.assertTrue(created)
            self.assertEqual(application.manual_end_client, "")
            self.assertEqual(application.end_client_snapshot, "")

    def test_auto_logged_send_never_inherits_the_recruiter_company_as_end_client(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = RecruiterEmail(
                owner_id="owner",
                sender="Priya <priya@abcstaffing.com>",
                subject="Senior Java Developer",
                body="Java and AWS",
                company="ABC Staffing",
                role="Senior Java Developer",
                end_client="",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.flush()

            result = resume_tracking_service.create_application_from_recruiter_email(db, email, owner_id="owner")
            assert result is not None
            application, created = result

            self.assertTrue(created)
            self.assertEqual(application.dedupe_key, f"recruiter_email:{email.id}")
            self.assertEqual(application.recruiter_company_snapshot, "ABC Staffing")
            # Blank means *not identified*. It used to read "ABC Staffing" here,
            # which is the vendor - and the value is a search filter and is
            # interpolated into outgoing follow-up mail, so it was not cosmetic.
            self.assertEqual(application.end_client_snapshot, "")
            self.assertEqual(application.manual_end_client, "")

    def test_auto_logged_send_keeps_an_end_client_the_posting_named(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = RecruiterEmail(
                owner_id="owner",
                sender="Priya <priya@abcstaffing.com>",
                subject="Senior Java Developer",
                body="Java and AWS",
                company="ABC Staffing",
                role="Senior Java Developer",
                end_client="State of New Jersey",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.flush()

            result = resume_tracking_service.create_application_from_recruiter_email(db, email, owner_id="owner")
            assert result is not None
            application, _ = result
            self.assertEqual(application.end_client_snapshot, "State of New Jersey")

    def test_auto_logged_send_names_the_recruiter_the_resume_went_to(self) -> None:
        # A forwarded requirement: Alekya passed it on, the resume went to Lalitha.
        # This used to snapshot `parseaddr(email.sender)`, so the card named Alekya -
        # 1,770 of the 3,386 tracked sends named somebody who never received one.
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = RecruiterEmail(
                owner_id="owner",
                sender="Alekya <alekya@rpatechnologyinc.com>",
                subject="Jr. Java Full stack Developer",
                body="Java and Spring",
                company="RPATECHNOLOGY INC",
                role="Jr. Java Full stack Developer",
                recipient_email="lalitha.y@metasisinfo.com",
                resolved_recruiter_email="lalitha.y@metasisinfo.com",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.flush()

            result = resume_tracking_service.create_application_from_recruiter_email(db, email, owner_id="owner")
            assert result is not None
            application, _ = result

            self.assertEqual(application.manual_recruiter_email, "lalitha.y@metasisinfo.com")
            self.assertEqual(application.recruiter_name_snapshot, "lalitha.y@metasisinfo.com")
            # The extractor's `company` is the *sender's* firm by its own definition,
            # so it says nothing about a recruiter on another domain. "Unknown" is the
            # honest answer and the card renders it as absent.
            self.assertEqual(application.recruiter_company_snapshot, "Unknown")

    def test_auto_logged_send_links_the_contact_that_owns_the_address(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            contact = PremiumNumberContact(
                owner_id="owner",
                display_phone_number="",
                recruiter_name="Lalitha Y",
                recruiter_email="lalitha.y@metasisinfo.com",
                company="Metasis Information Systems LLC",
                is_recruiter=True,
            )
            db.add(contact)
            db.flush()
            db.add(
                PremiumContactEmail(
                    owner_id="owner",
                    premium_contact_id=contact.id,
                    normalized_email="lalitha.y@metasisinfo.com",
                    domain="metasisinfo.com",
                    is_primary=True,
                )
            )
            email = RecruiterEmail(
                owner_id="owner",
                sender="Alekya <alekya@rpatechnologyinc.com>",
                subject="Jr. Java Full stack Developer",
                body="Java and Spring",
                company="RPATECHNOLOGY INC",
                role="Jr. Java Full stack Developer",
                resolved_recruiter_email="lalitha.y@metasisinfo.com",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.flush()

            result = resume_tracking_service.create_application_from_recruiter_email(db, email, owner_id="owner")
            assert result is not None
            application, _ = result

            self.assertEqual(application.recruiter_name_snapshot, "Lalitha Y")
            self.assertEqual(application.recruiter_company_snapshot, "Metasis Information Systems LLC")
            # Left NULL on all 3,400 existing rows, which is why every `current_*`
            # field on the card was blank and the live-vs-snapshot check compared
            # a snapshot against nothing.
            self.assertEqual(application.recruiter_contact_id, contact.id)

    def test_source_email_id_is_recoverable_from_the_dedupe_key(self) -> None:
        """`Application` has no source_recruiter_email_id column, only the key.

        The details panel needs the id to open the sourcing audit, and this is
        where it comes from - so the parsing has to stay agreed with the writer
        in create_application_from_recruiter_email above.
        """
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = RecruiterEmail(
                owner_id="owner",
                sender="Priya <priya@abcstaffing.com>",
                subject="Senior Java Developer",
                body="Java and AWS",
                company="ABC Staffing",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.flush()
            auto, _ = resume_tracking_service.create_application_from_recruiter_email(db, email, owner_id="owner")
            manual, _ = self._manual(db, resume, key="9f3c-typed-by-hand", submitted_at=datetime.now(UTC))

            self.assertEqual(_source_recruiter_email_id(auto), email.id)
            self.assertIsNone(_source_recruiter_email_id(manual))

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
