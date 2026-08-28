import os
import unittest

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
            assert result.contact is not None
            self.assertEqual(result.contact.id, contact_a.id)
            review = db.get(NumberReviewQueue, result.review_id)
            assert review is not None
            self.assertEqual(review.reason_code, "phone_email_cross_conflict")
            self.assertEqual(review.target_contact_id, contact_a.id)
            self.assertEqual(review.secondary_contact_id, contact_b.id)
            self.assertEqual(review.state, "pending")

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
            # Conflicting matches still hand back the contact when human_confirmed=True.
            assert result.contact is not None
            self.assertEqual(result.contact.id, contact_id)
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

            contact_identity_service.dismiss(review)
            db.commit()

            refreshed = db.get(NumberReviewQueue, review.id)
            assert refreshed is not None
            self.assertEqual(refreshed.state, "dismissed")


if __name__ == "__main__":
    unittest.main()
