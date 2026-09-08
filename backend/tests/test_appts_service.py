import os
import unittest
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Application,
    AppTSApplication,
    AppTSApplicationEvent,
    PremiumContactEmail,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    utc_now,
)
from app.services import appts_service, application_service

OWNER_ID = "owner-1"


class AppTSServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _add_resume(self, db: Session, **overrides: object) -> ResumeAsset:
        defaults = dict(
            owner_id=OWNER_ID,
            file_path="/fake/resume.pdf",
            file_name="resume.pdf",
            mime_type="application/pdf",
            sha256="sha-resume-1",
            version=1,
            skills_text="Java, Spring",
            is_enabled=True,
            is_current=True,
        )
        defaults.update(overrides)
        resume = ResumeAsset(**defaults)
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    # -- create_tracked_application_manual: validation --------------------------

    def test_manual_requires_all_core_fields_nonblank(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            base_kwargs = dict(
                db=db,
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                dedupe_key="dk-1",
                manual_recruiter_name="Jane Doe",
                manual_recruiter_company="Acme",
                manual_job_title="Java Developer",
                manual_end_client="Client Co",
            )
            for blank_field in ("manual_recruiter_name", "manual_recruiter_company", "manual_job_title"):
                kwargs = dict(base_kwargs)
                kwargs[blank_field] = "   "
                with self.assertRaises(application_service.ApplicationValidationError):
                    appts_service.create_tracked_application_manual(**kwargs)

            # End client is NOT in that list. Most postings never name one, and
            # requiring it made every caller substitute the recruiter or posting
            # company. A blank is accepted and stored as a blank.
            kwargs = dict(base_kwargs)
            kwargs["manual_end_client"] = "   "
            row, created = appts_service.create_tracked_application_manual(**kwargs)
            self.assertTrue(created)
            self.assertEqual(row.manual_end_client, "")
            self.assertEqual(row.end_client_snapshot, "")

    def test_manual_missing_resume_raises_reference_not_found(self) -> None:
        with Session(self.engine) as db:
            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                appts_service.create_tracked_application_manual(
                    db,
                    owner_id=OWNER_ID,
                    resume_asset_id=999999,
                    dedupe_key="dk-2",
                    manual_recruiter_name="Jane Doe",
                    manual_recruiter_company="Acme",
                    manual_job_title="Java Developer",
                    manual_end_client="Client Co",
                )

    def test_manual_dedupe_key_blank_raises_validation_error(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            with self.assertRaises(application_service.ApplicationValidationError):
                appts_service.create_tracked_application_manual(
                    db,
                    owner_id=OWNER_ID,
                    resume_asset_id=resume.id,
                    dedupe_key="   ",
                    manual_recruiter_name="Jane Doe",
                    manual_recruiter_company="Acme",
                    manual_job_title="Java Developer",
                    manual_end_client="Client Co",
                )

    def test_manual_dedupe_key_too_long_raises_validation_error(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            with self.assertRaises(application_service.ApplicationValidationError):
                appts_service.create_tracked_application_manual(
                    db,
                    owner_id=OWNER_ID,
                    resume_asset_id=resume.id,
                    dedupe_key="x" * 65,
                    manual_recruiter_name="Jane Doe",
                    manual_recruiter_company="Acme",
                    manual_job_title="Java Developer",
                    manual_end_client="Client Co",
                )

    # -- create_tracked_application_manual: idempotency --------------------------

    def test_manual_idempotent_on_repeat_dedupe_key(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            kwargs = dict(
                db=db,
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                dedupe_key="dk-idempotent",
                manual_recruiter_name="Jane Doe",
                manual_recruiter_company="Acme",
                manual_job_title="Java Developer",
                manual_end_client="Client Co",
            )
            first_row, first_created = appts_service.create_tracked_application_manual(**kwargs)
            db.commit()
            second_row, second_created = appts_service.create_tracked_application_manual(**kwargs)
            db.commit()

            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first_row.id, second_row.id)
            self.assertEqual(db.query(AppTSApplication).count(), 1)
            events = (
                db.query(AppTSApplicationEvent)
                .filter(AppTSApplicationEvent.application_id == first_row.id, AppTSApplicationEvent.event_type == "created")
                .all()
            )
            self.assertEqual(len(events), 1)

    # -- create_tracked_application_manual: identity resolution -----------------

    def test_manual_resolves_recruiter_contact_from_matching_email(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            contact = PremiumNumberContact(
                owner_id=OWNER_ID,
                normalized_phone_number=None,
                display_phone_number="",
                recruiter_name="Jane Doe",
                recruiter_email="jane@example.com",
                company="Acme",
            )
            db.add(contact)
            db.commit()
            db.add(
                PremiumContactEmail(
                    owner_id=OWNER_ID,
                    premium_contact_id=contact.id,
                    normalized_email="jane@example.com",
                    domain="example.com",
                    is_primary=True,
                )
            )
            db.commit()

            row, created = appts_service.create_tracked_application_manual(
                db,
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                dedupe_key="dk-resolve",
                manual_recruiter_name="Jane Doe",
                manual_recruiter_company="Acme",
                manual_job_title="Java Developer",
                manual_end_client="Client Co",
                manual_recruiter_email=" Jane@Example.com ",
            )
            db.commit()

            self.assertTrue(created)
            self.assertEqual(row.resolved_recruiter_contact_id, contact.id)
            self.assertEqual(row.resolved_recruiter_email, "jane@example.com")

    def test_manual_creates_created_event(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            row, created = appts_service.create_tracked_application_manual(
                db,
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                dedupe_key="dk-event",
                manual_recruiter_name="Jane Doe",
                manual_recruiter_company="Acme",
                manual_job_title="Java Developer",
                manual_end_client="Client Co",
            )
            db.commit()
            self.assertTrue(created)
            event = (
                db.query(AppTSApplicationEvent)
                .filter(AppTSApplicationEvent.application_id == row.id, AppTSApplicationEvent.event_type == "created")
                .first()
            )
            self.assertIsNotNone(event)
            self.assertEqual(event.event_source, "user")

    # -- create_tracked_application_from_email -----------------------------------

    def test_from_email_returns_none_without_resume(self) -> None:
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="Jane Recruiter <jane@example.com>",
                subject="Java role",
                body="body",
                resume_asset_id=None,
            )
            db.add(email)
            db.commit()

            result = appts_service.create_tracked_application_from_email(db, email, owner_id=OWNER_ID)
            self.assertIsNone(result)

    def test_from_email_creates_row_sourced_from_sender_and_email_fields(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            sent_at = datetime(2026, 1, 5, tzinfo=UTC)
            email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="Jane Recruiter <jane@example.com>",
                subject="Java role",
                body="body",
                resume_asset_id=resume.id,
                company="Acme Corp",
                role="Java Developer",
                end_client="",
                location="Remote",
                sent_at=sent_at,
            )
            db.add(email)
            db.commit()

            result = appts_service.create_tracked_application_from_email(db, email, owner_id=OWNER_ID)
            db.commit()
            assert result is not None
            row, created = result

            self.assertTrue(created)
            self.assertEqual(row.dedupe_key, f"appts_email:{email.id}")
            self.assertEqual(row.manual_recruiter_name, "Jane Recruiter")
            self.assertEqual(row.manual_recruiter_email, "jane@example.com")
            self.assertEqual(row.recruiter_company_snapshot, "Acme Corp")
            # end_client blank on the email stays blank. It used to fall back to
            # the recruiter company, which is why every tracked application
            # carried a staffing firm in an end-client column. Blank means *not
            # identified*, never "no end client" and never the vendor's name.
            self.assertEqual(row.end_client_snapshot, "")
            self.assertEqual(row.manual_end_client, "")
            self.assertEqual(row.job_title_snapshot, "Java Developer")
            self.assertEqual(row.location_snapshot, "Remote")

    def test_from_email_names_the_recruiter_the_resume_went_to_not_the_forwarder(self) -> None:
        # The same correction as resume_tracking_service, mirrored here because this
        # service carried a byte-identical copy of the defect: the sender of a
        # forwarded requirement is not the recruiter, and `email.company` is the
        # sender's firm by the extractor's own definition.
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="Alekya <alekya@rpatechnologyinc.com>",
                subject="Jr. Java Full stack Developer",
                body="body",
                resume_asset_id=resume.id,
                company="RPATECHNOLOGY INC",
                role="Jr. Java Full stack Developer",
                recipient_email="lalitha.y@metasisinfo.com",
                resolved_recruiter_email="lalitha.y@metasisinfo.com",
            )
            db.add(email)
            db.commit()

            result = appts_service.create_tracked_application_from_email(db, email, owner_id=OWNER_ID)
            db.commit()
            assert result is not None
            row, _ = result

            self.assertEqual(row.manual_recruiter_email, "lalitha.y@metasisinfo.com")
            self.assertEqual(row.manual_recruiter_name, "lalitha.y@metasisinfo.com")
            self.assertEqual(row.recruiter_company_snapshot, "Unknown")

    def test_from_email_falls_back_to_email_address_when_sender_has_no_display_name(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="onlyemail@example.com",
                subject="Java role",
                body="body",
                resume_asset_id=resume.id,
            )
            db.add(email)
            db.commit()

            result = appts_service.create_tracked_application_from_email(db, email, owner_id=OWNER_ID)
            db.commit()
            assert result is not None
            row, _created = result
            self.assertEqual(row.manual_recruiter_name, "onlyemail@example.com")
            self.assertEqual(row.manual_recruiter_email, "onlyemail@example.com")
            # role/end_client blank on the email -> "Not specified" / "Unknown" fallbacks.
            self.assertEqual(row.job_title_snapshot, "Not specified")
            # Was "Unknown" - a literal standing in for a fact nobody knows.
            self.assertEqual(row.end_client_snapshot, "")

    # -- create_tracked_application_from_opportunity ------------------------------

    def test_from_opportunity_raises_when_opportunity_missing(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                appts_service.create_tracked_application_from_opportunity(
                    db, owner_id=OWNER_ID, resume_asset_id=resume.id, recruiter_opportunity_id=999999, dedupe_key="dk-opp-1"
                )

    def test_from_opportunity_raises_when_recruiter_contact_missing(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            opportunity = RecruiterOpportunity(
                owner_id=OWNER_ID,
                recruiter_number_id=999999,
                gmail_message_id="msg-1",
                job_title="Java Developer",
                end_client="Client Co",
            )
            db.add(opportunity)
            db.commit()

            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                appts_service.create_tracked_application_from_opportunity(
                    db, owner_id=OWNER_ID, resume_asset_id=resume.id, recruiter_opportunity_id=opportunity.id, dedupe_key="dk-opp-2"
                )

    def test_from_opportunity_raises_when_recruiter_contact_soft_deleted(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            contact = PremiumNumberContact(
                owner_id=OWNER_ID,
                display_phone_number="",
                recruiter_name="Jane Doe",
                recruiter_email="jane@example.com",
                company="Acme",
                deleted_at=utc_now(),
            )
            db.add(contact)
            db.commit()
            opportunity = RecruiterOpportunity(
                owner_id=OWNER_ID,
                recruiter_number_id=contact.id,
                gmail_message_id="msg-2",
                job_title="Java Developer",
                end_client="Client Co",
            )
            db.add(opportunity)
            db.commit()

            with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                appts_service.create_tracked_application_from_opportunity(
                    db, owner_id=OWNER_ID, resume_asset_id=resume.id, recruiter_opportunity_id=opportunity.id, dedupe_key="dk-opp-3"
                )

    def test_from_opportunity_creates_row_sourced_from_opportunity_and_recruiter(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            contact = PremiumNumberContact(
                owner_id=OWNER_ID,
                display_phone_number="",
                recruiter_name="Jane Doe",
                recruiter_email="jane@example.com",
                company="Acme",
            )
            db.add(contact)
            db.commit()
            opportunity = RecruiterOpportunity(
                owner_id=OWNER_ID,
                recruiter_number_id=contact.id,
                resume_asset_id=resume.id,
                gmail_message_id="msg-3",
                job_title="Java Developer",
                end_client="Client Co",
                location="Remote",
            )
            db.add(opportunity)
            db.commit()

            row, created = appts_service.create_tracked_application_from_opportunity(
                db, owner_id=OWNER_ID, recruiter_opportunity_id=opportunity.id, dedupe_key="dk-opp-4"
            )
            db.commit()

            self.assertTrue(created)
            self.assertEqual(row.manual_recruiter_name, "Jane Doe")
            self.assertEqual(row.recruiter_company_snapshot, "Acme")
            self.assertEqual(row.manual_recruiter_email, "jane@example.com")
            self.assertEqual(row.job_title_snapshot, "Java Developer")
            self.assertEqual(row.end_client_snapshot, "Client Co")
            self.assertEqual(row.location_snapshot, "Remote")
            self.assertEqual(row.dedupe_key, "dk-opp-4")
            self.assertEqual(row.resume_asset_id, resume.id)
            self.assertEqual(row.recruiter_opportunity_id, opportunity.id)
            self.assertEqual(row.recruiter_contact_id, contact.id)
            self.assertEqual(row.resolved_recruiter_contact_id, contact.id)

    def test_from_opportunity_uses_record_email_fallback_and_requires_a_resume(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            contact = PremiumNumberContact(owner_id=OWNER_ID, display_phone_number="", recruiter_name="Jane", company="Acme")
            email = RecruiterEmail(owner_id=OWNER_ID, sender="jane@example.com", subject="Role", body="Body", record_id="record-1")
            db.add_all([contact, email])
            db.flush()
            opportunity = RecruiterOpportunity(
                owner_id=OWNER_ID,
                recruiter_number_id=contact.id,
                source_type="nvoids",
                gmail_message_id="nvoids:1",
                job_title="Java Developer",
                end_client="Client Co",
                record_id="record-1",
                resume_asset_id=resume.id,
            )
            db.add(opportunity)
            db.flush()

            row, _ = appts_service.create_tracked_application_from_opportunity(
                db, owner_id=OWNER_ID, recruiter_opportunity_id=opportunity.id, dedupe_key="dk-nvoids"
            )
            self.assertEqual(row.source_recruiter_email_id, email.id)

            opportunity.resume_asset_id = None
            with self.assertRaisesRegex(application_service.ApplicationReferenceNotFoundError, "No resume recorded"):
                appts_service.create_tracked_application_from_opportunity(
                    db, owner_id=OWNER_ID, recruiter_opportunity_id=opportunity.id, dedupe_key="dk-no-resume"
                )

    def test_manual_explicit_contact_is_preserved_without_an_email(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            contact = PremiumNumberContact(owner_id=OWNER_ID, display_phone_number="", recruiter_name="Jane", company="Acme")
            db.add(contact)
            db.flush()
            row, _ = appts_service.create_tracked_application_manual(
                db,
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                recruiter_contact_id=contact.id,
                dedupe_key="dk-contact",
                manual_recruiter_name="Jane",
                manual_recruiter_company="Acme",
                manual_job_title="Java Developer",
                manual_end_client="Client Co",
            )
            self.assertEqual(row.resolved_recruiter_contact_id, contact.id)

    # -- promote_legacy_application -----------------------------------------------

    def test_promote_legacy_application_copies_snapshot_fields_and_leaves_legacy_row_untouched(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            legacy = Application(
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                resume_version_snapshot=resume.version,
                resume_file_name_snapshot=resume.file_name,
                resume_sha256_snapshot=resume.sha256,
                recruiter_name_snapshot="Snapshot Recruiter",
                recruiter_company_snapshot="Snapshot Co",
                job_title_snapshot="Snapshot Title",
                end_client_snapshot="Snapshot Client",
                manual_recruiter_name="",
                manual_recruiter_company="",
                manual_job_title="",
                manual_end_client="",
                manual_recruiter_email="legacy@example.com",
                manual_recruiter_phone="14155550000",
                manual_recruiter_linkedin_url="https://linkedin.com/in/legacy",
                manual_jd_text="jd text",
                manual_source_note="legacy note",
                submission_method="email",
                status="matched",
                dedupe_key="legacy-app-1",
            )
            db.add(legacy)
            db.commit()
            legacy_id = legacy.id
            legacy_status_before = legacy.status

            row, created = appts_service.promote_legacy_application(db, legacy, owner_id=OWNER_ID)
            db.commit()

            self.assertTrue(created)
            self.assertEqual(row.dedupe_key, f"appts_promoted:{legacy_id}")
            # Falls back to *_snapshot fields since manual_* fields were blank.
            self.assertEqual(row.manual_recruiter_name, "Snapshot Recruiter")
            self.assertEqual(row.manual_recruiter_company, "Snapshot Co")
            self.assertEqual(row.manual_job_title, "Snapshot Title")
            self.assertEqual(row.manual_end_client, "Snapshot Client")
            self.assertEqual(row.manual_recruiter_email, "legacy@example.com")
            self.assertEqual(row.manual_recruiter_phone, "14155550000")
            self.assertEqual(row.manual_jd_text, "jd text")
            self.assertEqual(row.manual_source_note, "legacy note")

            refreshed_legacy = db.get(Application, legacy_id)
            assert refreshed_legacy is not None
            self.assertEqual(refreshed_legacy.status, legacy_status_before)
            self.assertIsNone(refreshed_legacy.deleted_at)
            self.assertEqual(refreshed_legacy.promoted_to_appts_application_id, row.id)
            self.assertEqual(db.query(Application).count(), 1)

            replay, replay_created = appts_service.promote_legacy_application(db, refreshed_legacy, owner_id=OWNER_ID)
            self.assertFalse(replay_created)
            self.assertEqual(replay.id, row.id)
            self.assertEqual(db.query(AppTSApplication).count(), 1)

    def test_promote_legacy_application_prefers_manual_fields_over_snapshot(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db)
            legacy = Application(
                owner_id=OWNER_ID,
                resume_asset_id=resume.id,
                resume_version_snapshot=resume.version,
                resume_file_name_snapshot=resume.file_name,
                resume_sha256_snapshot=resume.sha256,
                recruiter_name_snapshot="Snapshot Recruiter",
                recruiter_company_snapshot="Snapshot Co",
                job_title_snapshot="Snapshot Title",
                end_client_snapshot="Snapshot Client",
                manual_recruiter_name="Manual Recruiter",
                manual_recruiter_company="Manual Co",
                manual_job_title="Manual Title",
                manual_end_client="Manual Client",
                status="matched",
                dedupe_key="legacy-app-2",
            )
            db.add(legacy)
            db.commit()

            row, created = appts_service.promote_legacy_application(db, legacy, owner_id=OWNER_ID)
            db.commit()

            self.assertTrue(created)
            self.assertEqual(row.manual_recruiter_name, "Manual Recruiter")
            self.assertEqual(row.manual_recruiter_company, "Manual Co")
            self.assertEqual(row.manual_job_title, "Manual Title")
            self.assertEqual(row.manual_end_client, "Manual Client")


if __name__ == "__main__":
    unittest.main()
