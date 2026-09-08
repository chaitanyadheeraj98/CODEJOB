"""The repair script for sends that recorded whoever forwarded the requirement.

The script rewrites thousands of rows in one go, so the two claims that matter are
that it leaves alone what it says it leaves alone, and that a second run is a no-op.
"""

import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PremiumContactEmail, PremiumNumberContact, RecruiterEmail, ResumeAsset
from app.services import resume_tracking_service
from scripts import resnapshot_recruiter_identity as script

OWNER_ID = "owner"
FORWARDER = "Alekya <alekya@rpatechnologyinc.com>"
RECRUITER = "lalitha.y@metasisinfo.com"


class ResnapshotRecruiterIdentityTests(unittest.TestCase):
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

    def _resume(self, db: Session) -> ResumeAsset:
        resume = ResumeAsset(
            owner_id=OWNER_ID, file_path="java.pdf", file_name="java.pdf", sha256="a" * 64,
            version=4, skills_text="Java", primary_role="Java Developer",
        )
        db.add(resume)
        db.flush()
        return resume

    def _email(self, db: Session, **overrides: object) -> RecruiterEmail:
        defaults = dict(
            owner_id=OWNER_ID, sender=FORWARDER, subject="Jr. Java Full stack Developer",
            body="Java", company="RPATECHNOLOGY INC", role="Jr. Java Full stack Developer",
            recipient_email=RECRUITER, resolved_recruiter_email=RECRUITER,
        )
        defaults.update(overrides)
        email = RecruiterEmail(**defaults)
        db.add(email)
        db.flush()
        return email

    def _legacy_row(self, db: Session, resume: ResumeAsset, email: RecruiterEmail, **overrides: object):
        """A send recorded the way the auto-log path used to record it: the sender."""
        fields = dict(
            manual_recruiter_name="Alekya",
            manual_recruiter_company="RPATECHNOLOGY INC",
            manual_recruiter_email="alekya@rpatechnologyinc.com",
            dedupe_key=f"recruiter_email:{email.id}",
        )
        fields.update(overrides)
        application, _ = resume_tracking_service.create_manual_application(
            db, owner_id=OWNER_ID, resume_asset_id=resume.id,
            manual_job_title="Jr. Java Full stack Developer", manual_end_client="",
            resume_submitted_at=datetime(2026, 8, 24, tzinfo=UTC), **fields,
        )
        return application

    def _scan(self, db: Session):
        contacts, contact_review, _ = script.scan_contacts(db)
        applications, app_review, _ = script.scan_applications(
            db, contacts_losing_company={change.row_id for change in contacts}
        )
        return applications, contacts, app_review + contact_review

    def test_it_renames_the_recruiter_and_stops_after_one_pass(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            application = self._legacy_row(db, resume, self._email(db))
            db.commit()

            applications, contacts, review = self._scan(db)
            self.assertEqual(len(applications), 1)
            self.assertEqual(applications[0].reason, "renamed_the_recruiter")
            self.assertEqual(applications[0].fields["manual_recruiter_email"], ["alekya@rpatechnologyinc.com", RECRUITER])
            self.assertEqual(contacts, [])
            self.assertEqual(review, [])

            script._apply(db, applications, contacts)
            db.refresh(application)
            self.assertEqual(application.manual_recruiter_email, RECRUITER)
            self.assertEqual(application.recruiter_name_snapshot, RECRUITER)
            # The mail named RPATECHNOLOGY INC as its own sender's firm. It said
            # nothing about Lalitha's.
            self.assertEqual(application.recruiter_company_snapshot, "Unknown")

            # Idempotence: the script writes what it computes, so it computes the
            # same thing next time and has nothing left to write.
            self.assertEqual(self._scan(db), ([], [], []))

    def test_a_contact_loses_a_company_it_never_earned_and_the_card_follows(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = self._email(db)
            contact = PremiumNumberContact(
                owner_id=OWNER_ID, display_phone_number="", is_recruiter=True,
                recruiter_name="Unknown", recruiter_email=RECRUITER,
                company="RPATECHNOLOGY INC", first_detected_email_id=email.id,
            )
            db.add(contact)
            db.flush()
            db.add(PremiumContactEmail(
                owner_id=OWNER_ID, premium_contact_id=contact.id, normalized_email=RECRUITER,
                domain="metasisinfo.com", is_primary=True,
            ))
            application = self._legacy_row(db, resume, email)
            db.commit()

            applications, contacts, review = self._scan(db)
            self.assertEqual([change.row_id for change in contacts], [contact.id])
            self.assertEqual(contacts[0].fields["company"], ["RPATECHNOLOGY INC", "Unknown"])
            # The card must not re-inherit from the contact the value the contact is
            # about to lose.
            self.assertEqual(applications[0].fields["recruiter_company_snapshot"], ["RPATECHNOLOGY INC", "Unknown"])
            self.assertEqual(applications[0].fields["recruiter_contact_id"], [None, contact.id])
            self.assertEqual(review, [])

            script._apply(db, applications, contacts)
            db.refresh(application)
            db.refresh(contact)
            self.assertEqual(contact.company, "Unknown")
            self.assertEqual(application.recruiter_company_snapshot, "Unknown")
            self.assertEqual(self._scan(db), ([], [], []))

    def test_a_contact_on_the_senders_own_domain_keeps_its_company(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, recipient_email=None, resolved_recruiter_email=None)
            db.add(PremiumNumberContact(
                owner_id=OWNER_ID, display_phone_number="", is_recruiter=True,
                recruiter_name="Alekya", recruiter_email="alekya@rpatechnologyinc.com",
                company="RPATECHNOLOGY INC", first_detected_email_id=email.id,
            ))
            db.commit()

            _, contacts, review = self._scan(db)
            self.assertEqual(contacts, [])
            self.assertEqual(review, [])

    def test_a_hand_logged_row_is_never_touched(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = self._email(db)
            self._legacy_row(db, resume, email, dedupe_key="typed-by-hand")
            db.commit()

            applications, _, review = self._scan(db)
            # Not even reported: without a source-email key there is nothing to
            # recompute from, and the user's own typing is not the script's to judge.
            self.assertEqual(applications, [])
            self.assertEqual(review, [])

    def test_a_row_naming_a_third_party_is_reported_not_rewritten(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = self._email(db)
            self._legacy_row(
                db, resume, email,
                manual_recruiter_email="someone.else@thirdfirm.com",
                manual_recruiter_name="Someone Else",
            )
            db.commit()

            applications, _, review = self._scan(db)
            self.assertEqual(applications, [])
            self.assertEqual([change.reason for change in review], ["recorded_address_is_neither"])

    def test_a_row_that_already_names_the_recruiter_only_gains_the_contact_link(self) -> None:
        with Session(self.engine) as db:
            resume = self._resume(db)
            email = self._email(db, recipient_email=None, resolved_recruiter_email=None)
            application = self._legacy_row(
                db, resume, email,
                # The user tightened the name on the card. The address is right, so
                # the script has no business overruling that.
                manual_recruiter_name="Alekya Satarla",
            )
            contact = PremiumNumberContact(
                owner_id=OWNER_ID, display_phone_number="", is_recruiter=True,
                recruiter_name="Alekya", recruiter_email="alekya@rpatechnologyinc.com",
                company="RPATECHNOLOGY INC", first_detected_email_id=email.id,
            )
            db.add(contact)
            db.commit()

            applications, contacts, review = self._scan(db)
            self.assertEqual(contacts, [])
            self.assertEqual(review, [])
            self.assertEqual(len(applications), 1)
            self.assertEqual(applications[0].reason, "linked_the_contact_record")
            self.assertEqual(list(applications[0].fields), ["recruiter_contact_id"])

            script._apply(db, applications, contacts)
            db.refresh(application)
            self.assertEqual(application.manual_recruiter_name, "Alekya Satarla")
            self.assertEqual(application.recruiter_contact_id, contact.id)


if __name__ == "__main__":
    unittest.main()
