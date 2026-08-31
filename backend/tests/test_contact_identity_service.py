import os
import unittest
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    AppTSApplication,
    ContactIdentityAction,
    NumberReviewQueue,
    PremiumContactEmail,
    PremiumContactPhone,
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
)
from app.premium_numbers import contact_identity_service

OWNER_ID = "owner-1"

PHONE_A = "14155551111"
PHONE_B = "14155552222"
PHONE_C = "14155553333"
PHONE_D = "14155554444"
PHONE_E = "14155555555"


class ContactIdentityServiceTests(unittest.TestCase):
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

    # -- case (a): neither phone nor email found -----------------------------

    def test_reconcile_creates_new_contact_when_neither_phone_nor_email_found(self) -> None:
        with Session(self.engine) as db:
            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="Jane@Example.com",
                name="Jane Doe",
                company="Acme Corp",
                source_email_id=101,
            )
            db.commit()

            self.assertEqual(result.status, "created")
            contact = result.contact
            assert contact is not None
            self.assertEqual(contact.normalized_phone_number, PHONE_A)
            # Display phone must be nicely formatted, never raw digits.
            self.assertEqual(contact.display_phone_number, "(415) 555-1111")
            self.assertEqual(contact.recruiter_email, "jane@example.com")
            self.assertEqual(contact.recruiter_name, "Jane Doe")
            self.assertEqual(contact.company, "Acme Corp")
            self.assertTrue(contact.is_recruiter)

            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).all()
            self.assertEqual(len(emails), 1)
            self.assertTrue(emails[0].is_primary)

            phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).all()
            self.assertEqual(len(phones), 1)
            self.assertTrue(phones[0].is_primary)

            action = (
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.primary_contact_id == contact.id)
                .first()
            )
            self.assertIsNotNone(action)
            self.assertEqual(action.action_type, "create")

    def test_created_contact_without_phone_has_blank_display_phone(self) -> None:
        with Session(self.engine) as db:
            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_email="noPhone@example.com", name="No Phone", company="Acme"
            )
            db.commit()
            contact = result.contact
            assert contact is not None
            self.assertIsNone(contact.normalized_phone_number)
            self.assertEqual(contact.display_phone_number, "")

    # -- case (b): both found, pointing at different contacts ----------------

    def test_reconcile_phone_email_cross_conflict_queues_merge_review(self) -> None:
        with Session(self.engine) as db:
            contact_a = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="a@example.com", name="Jane A", company="Acme"
            ).contact
            contact_b = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="b@example.com", name="Jane B", company="Beta"
            ).contact
            db.commit()
            assert contact_a is not None and contact_b is not None

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="b@example.com"
            )
            db.commit()

            self.assertEqual(result.status, "pending_merge_approval")
            # Deliberately unresolved: which of the two claimants is right is the whole
            # question the card asks, so the caller must not be handed one of them to
            # overwrite. Both are named on the review below instead.
            self.assertIsNone(result.contact)
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "phone_email_cross_conflict")
            self.assertEqual(review.target_contact_id, contact_a.id)
            self.assertEqual(review.secondary_contact_id, contact_b.id)
            self.assertEqual(review.state, "pending")

    def test_reconcile_phone_email_cross_conflict_reuses_existing_review_slot_instead_of_crashing(self) -> None:
        with Session(self.engine) as db:
            contact_a = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="a@example.com", name="Jane A", company="Acme"
            ).contact
            contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="b@example.com", name="Jane B", company="Beta"
            )
            db.commit()
            assert contact_a is not None

            # A review already occupies (owner, PHONE_A, source_email_id=777) - e.g. this
            # exact email was reviewed once before under a different reason, then its phone
            # got corrected to PHONE_A by a manual edit.
            db.add(NumberReviewQueue(
                owner_id=OWNER_ID, source_email_id=777, normalized_phone_number=PHONE_A,
                display_phone_number=PHONE_A, owner_name="Someone", company="Unknown",
                designation="Unknown", confidence="low", purpose="", evidence_snippet="",
                email_subject="", email_sender="", contact_email="", reason_code="new_number",
                state="pending",
            ))
            db.commit()

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="b@example.com",
                source_email_id=777,
            )
            db.commit()

            self.assertEqual(result.status, "pending_merge_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.source_email_id, 777)
            self.assertEqual(
                db.query(NumberReviewQueue).filter(NumberReviewQueue.source_email_id == 777).count(), 1,
            )

    # -- case (c): phone found, email new -------------------------------------

    def test_reconcile_phone_only_confirmed_links_email_when_human_confirmed(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="jane.alt@example.com",
                name="Jane Doe",
                company="",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(result.status, "confirmed")
            assert result.contact is not None
            self.assertEqual(result.contact.id, contact_id)

            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 2)
            alt = next(e for e in emails if e.normalized_email == "jane.alt@example.com")
            self.assertFalse(alt.is_primary)
            refreshed = db.get(PremiumNumberContact, contact_id)
            assert refreshed is not None
            self.assertEqual(refreshed.recruiter_email, "jane@example.com")

            action = (
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.action_type == "link_email", ContactIdentityAction.primary_contact_id == contact_id)
                .first()
            )
            self.assertIsNotNone(action)
            self.assertEqual(action.value, "jane.alt@example.com")

    def test_reconcile_phone_only_confirmed_without_human_confirmation_queues_review(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="jane.alt@example.com",
                name="Jane Doe",
                company="",
                human_confirmed=False,
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            self.assertIsNone(result.contact)
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "suggested_email_match")

            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 1)

    def test_reconcile_phone_only_conflicting_identity_queues_review(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="new2@example.com",
                name="John Smith",
                company="Other Corp",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            # human_confirmed means "a person typed this in", NOT "a person adjudicated
            # this clash" - so a CONFLICTING match is withheld and the caller has to raise.
            # Handing it back is what let "Mark as Recruiter" overwrite a contact the
            # classifier had just rejected.
            self.assertIsNone(result.contact)
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "identity_conflict")

            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 1)

    def test_reconcile_phone_only_insufficient_evidence_queues_review(self) -> None:
        # Existing contact has no email/name/company on file (all blank), and
        # the candidate offers no name/company either -- classify_identity_match
        # has nothing to agree or conflict on.
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(db, owner_id=OWNER_ID, normalized_phone=PHONE_A).contact
            db.commit()
            assert contact is not None

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="new3@example.com", name="", company=""
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "insufficient_evidence")

    # -- case (d): email found, phone new -------------------------------------

    def test_reconcile_email_only_confirmed_links_phone_when_human_confirmed(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_C,
                normalized_email="jane@example.com",
                name="Jane Doe",
                company="",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(result.status, "confirmed")
            assert result.contact is not None
            self.assertEqual(result.contact.id, contact_id)

            phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact_id).all()
            self.assertEqual(len(phones), 2)
            new_phone = next(p for p in phones if p.normalized_phone_number == PHONE_C)
            self.assertFalse(new_phone.is_primary)
            refreshed = db.get(PremiumNumberContact, contact_id)
            assert refreshed is not None
            self.assertEqual(refreshed.normalized_phone_number, PHONE_A)

    def test_reconcile_email_only_no_phone_returns_confirmed_without_review(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(db, owner_id=OWNER_ID, normalized_email="jane@example.com")
            db.commit()

            self.assertEqual(result.status, "confirmed")
            assert result.contact is not None
            self.assertEqual(result.contact.id, contact_id)
            self.assertEqual(db.query(NumberReviewQueue).count(), 0)

    def test_reconcile_email_only_new_phone_without_human_confirmation_queues_review_and_withholds_contact(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_D,
                normalized_email="jane@example.com",
                name="Jane Doe",
                company="Acme Corp",
                human_confirmed=False,
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            # Symmetric with the phone-only-match branch: no contact handed back
            # unless a human already confirmed it.
            self.assertIsNone(result.contact)
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "suggested_phone_match")
            self.assertEqual(review.normalized_phone_number, PHONE_D)
            # The email is already-matched, not new -- must not be stored as a
            # pending "new email" value (see approve_link regression test below).
            self.assertEqual(review.contact_email, "")

            phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact_id).all()
            self.assertEqual(len(phones), 1)

    # -- case (e): both found, same contact -----------------------------------

    def test_reconcile_same_contact_fills_blank_fields_only(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id
            self.assertEqual(contact.recruiter_name, "Unknown")
            self.assertEqual(contact.company, "Unknown")

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            )
            db.commit()

            self.assertEqual(result.status, "confirmed")
            refreshed = db.get(PremiumNumberContact, contact_id)
            assert refreshed is not None
            self.assertEqual(refreshed.recruiter_name, "Jane Doe")
            self.assertEqual(refreshed.company, "Acme Corp")

    def test_reconcile_same_contact_does_not_overwrite_existing_values(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Someone Else", company="Other Corp"
            )
            db.commit()

            refreshed = db.get(PremiumNumberContact, contact_id)
            assert refreshed is not None
            self.assertEqual(refreshed.recruiter_name, "Jane Doe")
            self.assertEqual(refreshed.company, "Acme Corp")

    # -- multi-identifier support ----------------------------------------------

    def test_multi_identifier_two_emails_sharing_one_phone_reconcile_to_one_contact(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="jane.second@example.com",
                name="Jane Doe",
                company="Acme Corp",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == OWNER_ID).count(), 1)
            emails = {
                e.normalized_email
                for e in db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            }
            self.assertEqual(emails, {"jane@example.com", "jane.second@example.com"})

    def test_multi_identifier_two_phones_sharing_one_email_reconcile_to_one_contact(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_B,
                normalized_email="jane@example.com",
                name="Jane Doe",
                company="Acme Corp",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == OWNER_ID).count(), 1)
            phones = {
                p.normalized_phone_number
                for p in db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact_id).all()
            }
            self.assertEqual(phones, {PHONE_A, PHONE_B})

    def test_an_address_reused_across_roles_queues_a_review_instead_of_forking_the_owner(self) -> None:
        """An address has exactly ONE owner - premium_contact_emails enforces it. This
        used to silently create a second contact carrying the address as a bare headline
        with no child row, which is precisely the headline-vs-child divergence that put 14
        contradictory rows into production. Now the ambiguity goes to a human instead."""
        with Session(self.engine) as db:
            employer_contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="shared@example.com",
                name="Pat Employer", company="Acme Corp", role="employer",
            ).contact
            db.commit()
            assert employer_contact is not None
            # The employer side owns it on BOTH stores, in agreement.
            self.assertEqual(employer_contact.employer_email, "shared@example.com")
            self.assertEqual(employer_contact.recruiter_email, "")

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="shared@example.com",
                name="Pat Recruiter", company="Staffing Co", role="recruiter",
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.target_contact_id, employer_contact.id)
            self.assertEqual(review.role, "recruiter")
            # No forked second owner, and the one child row still points at one contact.
            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == OWNER_ID).count(), 1)
            rows = db.query(PremiumContactEmail).filter(PremiumContactEmail.normalized_email == "shared@example.com").all()
            self.assertEqual([row.premium_contact_id for row in rows], [employer_contact.id])

    def test_multi_identifier_conflicting_name_company_does_not_merge(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="conflict@example.com",
                name="John Smith",
                company="Other Corp",
                human_confirmed=True,
            )
            db.commit()

            self.assertEqual(result.status, "pending_link_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "identity_conflict")

            # Data stays separate: no new contact, no new email added, original
            # fields untouched.
            self.assertEqual(db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == OWNER_ID).count(), 1)
            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 1)
            refreshed = db.get(PremiumNumberContact, contact_id)
            assert refreshed is not None
            self.assertEqual(refreshed.recruiter_name, "Jane Doe")
            self.assertEqual(refreshed.company, "Acme Corp")

    # -- approve_link -----------------------------------------------------------

    def test_approve_link_email_backfills_recruiter_email_and_appts_application(self) -> None:
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_A,
                normalized_email="Jane.New@Example.com",
                name="Jane Doe",
                company="Acme Corp",
                human_confirmed=False,
            )
            db.commit()
            self.assertEqual(result.status, "pending_link_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None

            pre_existing_email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="Jane Doe <jane.new@example.com>",
                subject="subject",
                body="body",
                resolved_recruiter_email="jane.new@example.com",
                resolved_recruiter_contact_id=None,
            )
            appts_application = AppTSApplication(
                owner_id=OWNER_ID,
                resume_asset_id=1,
                resume_version_snapshot=1,
                resume_file_name_snapshot="resume.pdf",
                resume_sha256_snapshot="sha",
                resolved_recruiter_email="jane.new@example.com",
                resolved_recruiter_contact_id=None,
                dedupe_key="dk-1",
            )
            db.add_all([pre_existing_email, appts_application])
            db.commit()

            updated_contact = contact_identity_service.approve_link(db, review)
            db.commit()

            self.assertEqual(updated_contact.id, contact_id)
            self.assertEqual(updated_contact.recruiter_email, "jane.new@example.com")
            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 1)
            self.assertTrue(emails[0].is_primary)

            refreshed_review = db.get(NumberReviewQueue, review.id)
            assert refreshed_review is not None
            self.assertEqual(refreshed_review.state, "resolved")

            action = (
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.action_type == "link_email", ContactIdentityAction.source == "suggestion_approved")
                .first()
            )
            self.assertIsNotNone(action)

            refreshed_email = db.get(RecruiterEmail, pre_existing_email.id)
            assert refreshed_email is not None
            self.assertEqual(refreshed_email.resolved_recruiter_contact_id, contact_id)

            refreshed_app = db.get(AppTSApplication, appts_application.id)
            assert refreshed_app is not None
            self.assertEqual(refreshed_app.resolved_recruiter_contact_id, contact_id)

    def test_approve_link_phone_review_actually_links_the_new_phone(self) -> None:
        # Regression test: a "phone new, email already-matched" pending review
        # must add the new phone on approval, not silently no-op by trying to
        # re-add the already-matched email.
        with Session(self.engine) as db:
            contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="jane@example.com", name="Jane Doe", company="Acme Corp"
            ).contact
            db.commit()
            assert contact is not None
            contact_id = contact.id

            result = contact_identity_service.reconcile(
                db,
                owner_id=OWNER_ID,
                normalized_phone=PHONE_E,
                normalized_email="jane@example.com",
                name="Jane Doe",
                company="Acme Corp",
                human_confirmed=False,
            )
            db.commit()
            self.assertEqual(result.status, "pending_link_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.normalized_phone_number, PHONE_E)

            updated_contact = contact_identity_service.approve_link(db, review)
            db.commit()

            self.assertEqual(updated_contact.id, contact_id)
            phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact_id).all()
            self.assertEqual(len(phones), 2)
            self.assertIn(PHONE_E, {p.normalized_phone_number for p in phones})
            # The pre-existing email must not have been duplicated.
            emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).all()
            self.assertEqual(len(emails), 1)

            action = (
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.action_type == "link_phone", ContactIdentityAction.source == "suggestion_approved")
                .first()
            )
            self.assertIsNotNone(action)
            self.assertEqual(action.value, PHONE_E)

            refreshed_review = db.get(NumberReviewQueue, review.id)
            assert refreshed_review is not None
            self.assertEqual(refreshed_review.state, "resolved")

    def test_approve_link_email_already_on_a_different_contact_raises_instead_of_silently_resolving(self) -> None:
        # Regression test: two different contacts can each mention the same email (e.g. a
        # mismatched extraction). Linking that email onto the review's target must fail
        # loudly - not silently no-op while the review still gets marked resolved, which
        # made the reviewed person's identity vanish from every "pending" list with no
        # visible trace of what happened.
        with Session(self.engine) as db:
            other_contact = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="shared@example.com",
                name="Lokesh", company="Info Way Solutions",
            ).contact
            db.commit()
            assert other_contact is not None

            target = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, name="Madhavi Masannagari", company="Horizons of Tech",
            ).contact
            db.commit()
            assert target is not None
            target_id = target.id

            review = NumberReviewQueue(
                owner_id=OWNER_ID, target_contact_id=target_id, normalized_phone_number="",
                display_phone_number="", owner_name="Madhavi Masannagari", company="Horizons of Tech",
                contact_email="shared@example.com", role="employer", reason_code="identity_conflict", state="pending",
            )
            db.add(review)
            db.commit()

            with self.assertRaises(ValueError):
                contact_identity_service.approve_link(db, review)
            db.rollback()

            refreshed_target = db.get(PremiumNumberContact, target_id)
            assert refreshed_target is not None
            self.assertEqual(refreshed_target.recruiter_email, "")
            refreshed_review = db.get(NumberReviewQueue, review.id)
            assert refreshed_review is not None
            self.assertEqual(refreshed_review.state, "pending")
            self.assertIsNone(
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.action_type == "link_email", ContactIdentityAction.primary_contact_id == target_id)
                .first()
            )

    # -- approve_merge / dismiss --------------------------------------------

    def test_approve_merge_moves_children_reassigns_and_soft_deletes_loser(self) -> None:
        with Session(self.engine) as db:
            contact_a = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="a@example.com", name="Jane A", company="Acme"
            ).contact
            contact_b = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="b@example.com", name="Jane B", company="Beta"
            ).contact
            db.commit()
            assert contact_a is not None and contact_b is not None
            canonical_id, loser_id = contact_a.id, contact_b.id

            recruiter_email = RecruiterEmail(
                owner_id=OWNER_ID,
                sender="b@example.com",
                subject="subject",
                body="body",
                resolved_recruiter_email="b@example.com",
                resolved_recruiter_contact_id=loser_id,
            )
            appts_application = AppTSApplication(
                owner_id=OWNER_ID,
                resume_asset_id=1,
                resume_version_snapshot=1,
                resume_file_name_snapshot="resume.pdf",
                resume_sha256_snapshot="sha",
                resolved_recruiter_email="b@example.com",
                resolved_recruiter_contact_id=loser_id,
                dedupe_key="dk-2",
            )
            db.add_all([recruiter_email, appts_application])
            db.commit()

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="b@example.com"
            )
            db.commit()
            self.assertEqual(result.status, "pending_merge_approval")
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None

            canonical = contact_identity_service.approve_merge(db, review)
            db.commit()

            self.assertEqual(canonical.id, canonical_id)
            loser = db.get(PremiumNumberContact, loser_id)
            assert loser is not None
            self.assertIsNotNone(loser.deleted_at)

            emails = {
                e.normalized_email
                for e in db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == canonical_id).all()
            }
            self.assertEqual(emails, {"a@example.com", "b@example.com"})
            phones = {
                p.normalized_phone_number
                for p in db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == canonical_id).all()
            }
            self.assertEqual(phones, {PHONE_A, PHONE_B})

            refreshed_email = db.get(RecruiterEmail, recruiter_email.id)
            assert refreshed_email is not None
            self.assertEqual(refreshed_email.resolved_recruiter_contact_id, canonical_id)
            refreshed_app = db.get(AppTSApplication, appts_application.id)
            assert refreshed_app is not None
            self.assertEqual(refreshed_app.resolved_recruiter_contact_id, canonical_id)

            refreshed_review = db.get(NumberReviewQueue, review.id)
            assert refreshed_review is not None
            self.assertEqual(refreshed_review.state, "resolved")

            merge_action = (
                db.query(ContactIdentityAction)
                .filter(ContactIdentityAction.action_type == "merge")
                .first()
            )
            self.assertIsNotNone(merge_action)
            self.assertEqual(merge_action.primary_contact_id, canonical_id)
            self.assertEqual(merge_action.secondary_contact_id, loser_id)

    def test_merge_contacts_reassigns_leads_and_stray_reviews_without_a_review_row(self) -> None:
        # Regression test: the "Merge Contacts" UI action merges two contacts a human
        # picked directly, with no auto-detected conflict review pointing at them - and
        # unlike the older approve_merge path, the loser's own lead history and any other
        # review that happens to reference it must not be left dangling on a deleted contact.
        with Session(self.engine) as db:
            canonical = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                is_employer=True, owner_name="Prashanth Kinnera", company="Horizons of Tech",
                employer_email="kprashanth@horizonsoftech.net",
            )
            loser = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_B, display_phone_number="(415) 555-2222",
                is_employer=True, owner_name="Prashanth Kinnera", company="Horizons of Tech",
                employer_email="kprashanth@horizonsoftech.net",
            )
            db.add_all([canonical, loser])
            db.flush()
            canonical_id, loser_id = canonical.id, loser.id
            lead = PremiumNumberLead(
                owner_id=OWNER_ID, contact_id=loser_id, phone_number_normalized=PHONE_B,
                phone_number_display="(415) 555-2222", role="employer", owner_name="Prashanth Kinnera",
            )
            stray_review = NumberReviewQueue(
                owner_id=OWNER_ID, target_contact_id=loser_id, normalized_phone_number=PHONE_D,
                display_phone_number="(415) 555-4444", owner_name="Someone Else", company="Unrelated Co",
                designation="Unknown", role="recruiter", reason_code="insufficient_evidence", state="pending",
            )
            db.add_all([lead, stray_review])
            db.commit()

            result = contact_identity_service.merge_contacts(
                db, owner_id=OWNER_ID, canonical_contact_id=canonical_id, loser_contact_id=loser_id,
            )
            db.commit()

            self.assertEqual(result.id, canonical_id)
            refreshed_loser = db.get(PremiumNumberContact, loser_id)
            assert refreshed_loser is not None
            self.assertIsNotNone(refreshed_loser.deleted_at)

            refreshed_lead = db.get(PremiumNumberLead, lead.id)
            assert refreshed_lead is not None
            self.assertEqual(refreshed_lead.contact_id, canonical_id)

            refreshed_review = db.get(NumberReviewQueue, stray_review.id)
            assert refreshed_review is not None
            self.assertEqual(refreshed_review.target_contact_id, canonical_id)

            merge_action = db.query(ContactIdentityAction).filter(ContactIdentityAction.action_type == "merge").first()
            assert merge_action is not None
            self.assertEqual(merge_action.primary_contact_id, canonical_id)
            self.assertEqual(merge_action.secondary_contact_id, loser_id)
            self.assertEqual(merge_action.source, "manual_merge")

    def test_merge_contacts_backfills_the_canonicals_blank_phone_from_the_loser(self) -> None:
        # Regression test: merging used to only reassign child records, leaving a
        # canonical with no verified phone of its own permanently blank even after
        # absorbing a loser that had one.
        with Session(self.engine) as db:
            canonical = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=None, display_phone_number="",
                is_recruiter=True, recruiter_name="Prashanth Kinnera", company="Horizon Soft Tech",
                recruiter_email="kprashanth@horizonsoftech.net",
            )
            loser = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_B, display_phone_number="(415) 555-2222",
                is_recruiter=True, recruiter_name="Unknown", company="Horizon Soft Tech",
            )
            db.add_all([canonical, loser])
            db.flush()
            canonical_id, loser_id = canonical.id, loser.id
            db.add(PremiumContactPhone(owner_id=OWNER_ID, premium_contact_id=loser_id, normalized_phone_number=PHONE_B, is_primary=True))
            db.commit()

            result = contact_identity_service.merge_contacts(
                db, owner_id=OWNER_ID, canonical_contact_id=canonical_id, loser_contact_id=loser_id,
            )
            db.commit()

            self.assertEqual(result.normalized_phone_number, PHONE_B)
            self.assertEqual(result.display_phone_number, "(415) 555-2222")

    def test_release_contact_claims_frees_the_phone_slot_and_active_lead_pointers(self) -> None:
        # Regression test: a soft-deleted contact used to keep occupying its phone number
        # at the DB level (the unique constraint isn't scoped by deleted_at) and kept
        # dangling active_recruiter_lead_id/active_employer_lead_id pointers - the first
        # crashed a later contact trying to claim the same number, the second crashed
        # deleting a lead once its contact_id had been reassigned elsewhere.
        with Session(self.engine) as db:
            lead = PremiumNumberLead(
                owner_id=OWNER_ID, contact_id=None, phone_number_normalized=PHONE_A,
                phone_number_display="(415) 555-1111", role="recruiter", owner_name="Someone",
            )
            db.add(lead)
            db.flush()
            dead = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                is_recruiter=True, recruiter_name="Someone", company="Some Co",
                active_recruiter_lead_id=lead.id,
            )
            db.add(dead)
            db.flush()
            db.add(PremiumContactPhone(owner_id=OWNER_ID, premium_contact_id=dead.id, normalized_phone_number=PHONE_A, is_primary=True))
            db.commit()
            dead_id = dead.id

            contact_identity_service.release_contact_claims(db, dead)
            db.commit()

            refreshed = db.get(PremiumNumberContact, dead_id)
            assert refreshed is not None
            self.assertIsNone(refreshed.normalized_phone_number)
            self.assertIsNone(refreshed.active_recruiter_lead_id)
            self.assertEqual(db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == dead_id).count(), 0)

            # The number is now genuinely free for a brand-new contact to claim.
            revived = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="new@example.com", name="New Person",
            ).contact
            db.commit()
            assert revived is not None
            self.assertNotEqual(revived.id, dead_id)
            self.assertEqual(revived.normalized_phone_number, PHONE_A)

    def test_merge_contacts_rejects_merging_a_contact_with_itself(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                is_employer=True, owner_name="Solo Contact", company="Solo Co",
            )
            db.add(contact)
            db.commit()
            contact_id = contact.id

            with self.assertRaises(ValueError):
                contact_identity_service.merge_contacts(
                    db, owner_id=OWNER_ID, canonical_contact_id=contact_id, loser_contact_id=contact_id,
                )

    def test_dismiss_sets_state(self) -> None:
        with Session(self.engine) as db:
            contact_a = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="a@example.com"
            ).contact
            contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_B, normalized_email="b@example.com"
            )
            db.commit()
            assert contact_a is not None

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="b@example.com"
            )
            db.commit()
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None

            contact_identity_service.dismiss(db, review)
            db.commit()

            refreshed = db.get(NumberReviewQueue, review.id)
            assert refreshed is not None
            self.assertEqual(refreshed.state, "dismissed")

    def test_dismiss_creates_a_standalone_contact_from_the_reviews_own_data(self) -> None:
        with Session(self.engine) as db:
            review = NumberReviewQueue(
                owner_id=OWNER_ID,
                normalized_phone_number=PHONE_A,
                display_phone_number="(415) 555-1111",
                owner_name="Tushar Bhardwaj",
                company="Empower Professionals Inc",
                designation="Technical Recruiter",
                contact_email="tushar@empowerprofessionals.com",
                role="recruiter",
                reason_code="identity_conflict",
                target_contact_id=999,
                state="pending",
            )
            db.add(review)
            db.commit()

            created = contact_identity_service.dismiss(db, review).contact
            db.commit()

            self.assertEqual(review.state, "dismissed")
            standalone = db.get(PremiumNumberContact, created.id)
            assert standalone is not None
            self.assertEqual(standalone.normalized_phone_number, PHONE_A)
            self.assertEqual(standalone.recruiter_name, "Tushar Bhardwaj")
            self.assertEqual(standalone.company, "Empower Professionals Inc")
            self.assertEqual(standalone.recruiter_email, "tushar@empowerprofessionals.com")
            self.assertTrue(standalone.is_recruiter)

    def test_dismiss_keeps_the_person_when_the_phone_extension_pair_is_already_taken(self) -> None:
        with Session(self.engine) as db:
            contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="a@example.com"
            )
            db.commit()

            result = contact_identity_service.reconcile(
                db, owner_id=OWNER_ID, normalized_phone=PHONE_A, normalized_email="b@example.com"
            )
            db.commit()
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None

            created = contact_identity_service.dismiss(db, review).contact
            db.commit()

            self.assertEqual(created.normalized_phone_number, None)
            self.assertEqual(created.recruiter_email, "b@example.com")

    def test_dismiss_reassigns_the_number_when_the_new_persons_evidence_is_more_recent(self) -> None:
        with Session(self.engine) as db:
            target = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                is_recruiter=True, recruiter_name="Old Recruiter", owner_name="Old Recruiter", company="Old Co",
                recruiter_email="old@example.com",
            )
            db.add(target)
            db.flush()
            old_email = RecruiterEmail(
                owner_id=OWNER_ID, sender="old@example.com", subject="Role", body="Body", role="Developer",
                location="Remote", salary_text="", skills_text="", score=0, decision="auto_rejected",
                gmail_received_at=datetime(2026, 5, 18, tzinfo=UTC),
            )
            db.add(old_email)
            db.flush()
            db.add(PremiumNumberLead(
                owner_id=OWNER_ID, recruiter_email_id=old_email.id, contact_id=target.id,
                phone_number_normalized=PHONE_A, phone_number_display="(415) 555-1111", owner_name="Old Recruiter",
            ))
            new_email = RecruiterEmail(
                owner_id=OWNER_ID, sender="new@example.com", subject="Role", body="Body", role="Developer",
                location="Remote", salary_text="", skills_text="", score=0, decision="auto_rejected",
                gmail_received_at=datetime(2026, 8, 27, tzinfo=UTC),
            )
            db.add(new_email)
            db.flush()
            review = NumberReviewQueue(
                owner_id=OWNER_ID, source_email_id=new_email.id, target_contact_id=target.id,
                normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                owner_name="New Recruiter", company="New Co", contact_email="new@example.com",
                role="recruiter", reason_code="identity_conflict", state="pending",
            )
            db.add(review)
            db.commit()

            created = contact_identity_service.dismiss(db, review).contact
            db.commit()

            self.assertEqual(created.normalized_phone_number, PHONE_A)
            refreshed_target = db.get(PremiumNumberContact, target.id)
            assert refreshed_target is not None
            self.assertIsNone(refreshed_target.normalized_phone_number)
            self.assertEqual(
                db.query(PremiumContactPhone).filter(
                    PremiumContactPhone.premium_contact_id == target.id,
                    PremiumContactPhone.normalized_phone_number == PHONE_A,
                ).count(),
                0,
            )
            action = db.query(ContactIdentityAction).filter(ContactIdentityAction.action_type == "reassign_phone").first()
            assert action is not None
            self.assertEqual(action.primary_contact_id, created.id)
            self.assertEqual(action.secondary_contact_id, target.id)

    def test_dismiss_keeps_the_number_with_the_target_when_its_evidence_is_more_recent(self) -> None:
        with Session(self.engine) as db:
            target = PremiumNumberContact(
                owner_id=OWNER_ID, normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                is_recruiter=True, recruiter_name="Current Recruiter", owner_name="Current Recruiter",
                company="Current Co", recruiter_email="current@example.com",
            )
            db.add(target)
            db.flush()
            new_email = RecruiterEmail(
                owner_id=OWNER_ID, sender="current@example.com", subject="Role", body="Body", role="Developer",
                location="Remote", salary_text="", skills_text="", score=0, decision="auto_rejected",
                gmail_received_at=datetime(2026, 8, 27, tzinfo=UTC),
            )
            db.add(new_email)
            db.flush()
            db.add(PremiumNumberLead(
                owner_id=OWNER_ID, recruiter_email_id=new_email.id, contact_id=target.id,
                phone_number_normalized=PHONE_A, phone_number_display="(415) 555-1111", owner_name="Current Recruiter",
            ))
            old_email = RecruiterEmail(
                owner_id=OWNER_ID, sender="stale@example.com", subject="Role", body="Body", role="Developer",
                location="Remote", salary_text="", skills_text="", score=0, decision="auto_rejected",
                gmail_received_at=datetime(2026, 5, 18, tzinfo=UTC),
            )
            db.add(old_email)
            db.flush()
            review = NumberReviewQueue(
                owner_id=OWNER_ID, source_email_id=old_email.id, target_contact_id=target.id,
                normalized_phone_number=PHONE_A, display_phone_number="(415) 555-1111",
                owner_name="Stale Recruiter", company="Stale Co", contact_email="stale@example.com",
                role="recruiter", reason_code="identity_conflict", state="pending",
            )
            db.add(review)
            db.commit()

            created = contact_identity_service.dismiss(db, review).contact
            db.commit()

            self.assertIsNone(created.normalized_phone_number)
            refreshed_target = db.get(PremiumNumberContact, target.id)
            assert refreshed_target is not None
            self.assertEqual(refreshed_target.normalized_phone_number, PHONE_A)

    # -- the two stores are kept in sync -------------------------------------

    def _contact(self, db: Session, **kwargs) -> PremiumNumberContact:
        contact = PremiumNumberContact(owner_id=OWNER_ID, display_phone_number="", **kwargs)
        db.add(contact)
        db.flush()
        return contact

    def test_attaching_an_email_clears_the_stale_headline_on_its_previous_holder(self) -> None:
        """The headline columns have no unique constraint, so two contacts could each
        advertise the same address and every lookup would pick whichever it happened to
        check. 14 contacts were in this state in production."""
        with Session(self.engine) as db:
            squatter = self._contact(db, recruiter_name="Leo", company="Nascent", is_recruiter=True,
                                     recruiter_email="harshitha@example.com", recruiter_email_domain="example.com")
            real_owner = self._contact(db, recruiter_name="Harshitha", company="Horizons", is_recruiter=True)
            db.commit()

            self.assertTrue(contact_identity_service.attach_email(
                db, real_owner, "harshitha@example.com", "recruiter", None, primary=True))
            db.commit()

            self.assertEqual(real_owner.recruiter_email, "harshitha@example.com")
            self.assertEqual(db.get(PremiumNumberContact, squatter.id).recruiter_email, "")
            self.assertEqual(db.get(PremiumNumberContact, squatter.id).recruiter_email_domain, "")

    def test_attach_email_writes_the_role_correct_headline_column(self) -> None:
        """It used to write recruiter_email unconditionally, so an employer-role link
        landed on the wrong column and the employer lookup could never find it."""
        with Session(self.engine) as db:
            contact = self._contact(db, owner_name="Pat", company="Acme", is_employer=True)
            db.commit()

            contact_identity_service.attach_email(db, contact, "pat@acme.com", "employer", None, primary=True)
            db.commit()

            self.assertEqual(contact.employer_email, "pat@acme.com")
            self.assertEqual(contact.employer_email_domain, "acme.com")
            self.assertEqual(contact.recruiter_email, "")
            row = db.query(PremiumContactEmail).filter(PremiumContactEmail.normalized_email == "pat@acme.com").one()
            self.assertEqual(row.role, "employer")

    def test_add_phone_refuses_a_secondary_that_is_another_contacts_primary(self) -> None:
        """The child-table unique constraint only sees other child rows, so on its own it
        cannot stop a number being one contact's primary and another's secondary."""
        with Session(self.engine) as db:
            owner = self._contact(db, recruiter_name="First", is_recruiter=True, normalized_phone_number=PHONE_A)
            other = self._contact(db, recruiter_name="Second", is_recruiter=True, normalized_phone_number=PHONE_B)
            db.commit()

            self.assertFalse(contact_identity_service.add_phone(db, other, PHONE_A, primary=False))
            db.commit()

            self.assertEqual(other.normalized_phone_number, PHONE_B)
            self.assertEqual(db.query(PremiumContactPhone).filter(
                PremiumContactPhone.premium_contact_id == other.id,
                PremiumContactPhone.normalized_phone_number == PHONE_A).count(), 0)
            self.assertEqual(
                contact_identity_service.phone_claim_owner(db, OWNER_ID, PHONE_A, exclude_contact_id=other.id),
                owner.id,
            )

    def test_resolve_identity_reports_a_split_instead_of_picking_one_side(self) -> None:
        """The live case from the investigation: the phone belonged to one contact and the
        email to a completely different one, and the pipeline silently returned only the
        email's owner - so the reviewer never saw the real phone owner at all."""
        with Session(self.engine) as db:
            phone_owner = self._contact(db, recruiter_name="Unknown", company="Horizons", is_recruiter=True,
                                        normalized_phone_number=PHONE_A)
            email_owner = self._contact(db, recruiter_name="Leo", company="Nascent", is_recruiter=True)
            db.add(PremiumContactEmail(owner_id=OWNER_ID, premium_contact_id=email_owner.id,
                                       normalized_email="harshitha@example.com", domain="example.com",
                                       role="recruiter", created_at=datetime.now(UTC)))
            db.commit()

            resolution = contact_identity_service.resolve_identity(
                db, owner_id=OWNER_ID, phone=PHONE_A, email="harshitha@example.com", role="recruiter")

            self.assertEqual(resolution.outcome, "split")
            self.assertEqual(resolution.phone_owner.id, phone_owner.id)
            self.assertEqual(resolution.email_owner.id, email_owner.id)
            # Neither side wins by default - that is the reviewer's call.
            self.assertIsNone(resolution.contact)

    def test_find_email_owner_sees_a_child_row_with_no_matching_headline(self) -> None:
        """The ingestion matcher only ever read the headline columns, so an address held
        solely in premium_contact_emails looked unknown and spawned a duplicate contact."""
        with Session(self.engine) as db:
            contact = self._contact(db, recruiter_name="Priya", company="TechSys", is_recruiter=True,
                                    recruiter_email="priya@techsys.com")
            db.add(PremiumContactEmail(owner_id=OWNER_ID, premium_contact_id=contact.id,
                                       normalized_email="priya.k@techsys.com", domain="techsys.com",
                                       role="recruiter", created_at=datetime.now(UTC)))
            db.commit()

            found = contact_identity_service.find_email_owner(db, OWNER_ID, "priya.k@techsys.com", "recruiter")
            self.assertIsNotNone(found)
            self.assertEqual(found.id, contact.id)

    def test_dismiss_surfaces_a_conflict_rather_than_minting_a_phoneless_orphan(self) -> None:
        """Splitting someone off a card whose number a THIRD contact already owns used to
        fall back to a contact with no phone at all - unfindable, and the original dispute
        left unresolved. Now both claimants go onto a review card."""
        with Session(self.engine) as db:
            third_party = self._contact(db, recruiter_name="Existing Owner", company="Other Co",
                                        is_recruiter=True, normalized_phone_number=PHONE_A)
            target = self._contact(db, recruiter_name="Card Target", company="Target Co", is_recruiter=True)
            db.commit()

            review = NumberReviewQueue(
                owner_id=OWNER_ID, target_contact_id=target.id, normalized_phone_number=PHONE_A,
                display_phone_number="(415) 555-1111", owner_name="Split Person", company="Split Co",
                contact_email="split@example.com", role="recruiter", reason_code="identity_conflict",
                state="pending",
            )
            db.add(review)
            db.commit()

            result = contact_identity_service.dismiss(db, review)
            db.commit()

            self.assertEqual(review.state, "dismissed")
            # The person is kept - just without a number they cannot legitimately hold.
            self.assertEqual(result.contact.recruiter_name, "Split Person")
            self.assertIsNone(result.contact.normalized_phone_number)
            # ...and the real dispute is now visible, naming BOTH claimants, which routes
            # it to the three-way merge UI that already exists.
            self.assertIsNotNone(result.follow_up_review_id)
            follow_up = db.get(NumberReviewQueue, result.follow_up_review_id)
            self.assertEqual(follow_up.reason_code, "phone_owner_conflict")
            self.assertEqual(follow_up.target_contact_id, third_party.id)
            self.assertEqual(follow_up.secondary_contact_id, result.contact.id)
            self.assertEqual(follow_up.state, "pending")
            # The third party keeps the number it legitimately held.
            self.assertEqual(db.get(PremiumNumberContact, third_party.id).normalized_phone_number, PHONE_A)

    def test_snapshot_and_restore_round_trip_every_identifier(self) -> None:
        """release_contact_claims HAS to destroy these rows - neither unique constraint is
        scoped by deleted_at - so the snapshot is the only thing making a restore whole."""
        with Session(self.engine) as db:
            contact = self._contact(db, recruiter_name="Dana", company="Umbrella", is_recruiter=True,
                                    normalized_phone_number=PHONE_A)
            db.add_all([
                PremiumContactPhone(owner_id=OWNER_ID, premium_contact_id=contact.id,
                                    normalized_phone_number=PHONE_A, phone_extension="", is_primary=True,
                                    created_at=datetime.now(UTC)),
                PremiumContactPhone(owner_id=OWNER_ID, premium_contact_id=contact.id,
                                    normalized_phone_number=PHONE_B, phone_extension="", is_primary=False,
                                    created_at=datetime.now(UTC)),
                PremiumContactEmail(owner_id=OWNER_ID, premium_contact_id=contact.id,
                                    normalized_email="dana@umbrella.com", domain="umbrella.com",
                                    role="recruiter", is_primary=True, created_at=datetime.now(UTC)),
            ])
            db.commit()

            contact_identity_service.snapshot_contact_claims(db, contact)
            contact_identity_service.release_contact_claims(db, contact)
            contact.deleted_at = datetime.now(UTC)
            db.commit()
            self.assertIsNone(contact.normalized_phone_number)
            self.assertEqual(db.query(PremiumContactEmail).count(), 0)

            contact.deleted_at = None
            db.flush()
            skipped = contact_identity_service.restore_contact_claims(db, contact)
            db.commit()

            self.assertEqual(skipped, [])
            self.assertEqual(contact.normalized_phone_number, PHONE_A)
            self.assertEqual(contact.recruiter_email, "dana@umbrella.com")
            self.assertEqual(
                {row.normalized_phone_number for row in db.query(PremiumContactPhone).all()}, {PHONE_A, PHONE_B})

    def test_restore_reports_identifiers_another_contact_took_meanwhile(self) -> None:
        with Session(self.engine) as db:
            contact = self._contact(db, recruiter_name="Dana", company="Umbrella", is_recruiter=True,
                                    normalized_phone_number=PHONE_A)
            db.add(PremiumContactEmail(owner_id=OWNER_ID, premium_contact_id=contact.id,
                                       normalized_email="dana@umbrella.com", domain="umbrella.com",
                                       role="recruiter", is_primary=True, created_at=datetime.now(UTC)))
            db.commit()
            contact_identity_service.snapshot_contact_claims(db, contact)
            contact_identity_service.release_contact_claims(db, contact)
            contact.deleted_at = datetime.now(UTC)
            db.commit()

            # Somebody else legitimately claims both while it sits in the Recycle Bin.
            squatter = self._contact(db, recruiter_name="Newcomer", is_recruiter=True,
                                     normalized_phone_number=PHONE_A)
            contact_identity_service.attach_email(db, squatter, "dana@umbrella.com", "recruiter", None, primary=True)
            db.commit()

            contact.deleted_at = None
            db.flush()
            skipped = contact_identity_service.restore_contact_claims(db, contact)
            db.commit()

            self.assertEqual(sorted(skipped), sorted(["dana@umbrella.com", PHONE_A]))
            # Restored, but honestly incomplete - and the squatter keeps what it took.
            self.assertIsNone(contact.normalized_phone_number)
            self.assertEqual(db.get(PremiumNumberContact, squatter.id).recruiter_email, "dana@umbrella.com")


if __name__ == "__main__":
    unittest.main()
