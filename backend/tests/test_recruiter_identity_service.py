"""Whose company is `email.company`, and who is the recruiter on a forwarded mail.

Both questions had two answers before this module owned them - one in
`resume_tracking_service`, one in `appts_service`, and a third in the sourcing
panel's contact fallback - and all three answered "the sender", which on a
forwarded requirement is whoever passed it along.
"""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PremiumContactEmail, PremiumNumberContact, RecruiterEmail
from app.services import recruiter_identity_service

OWNER_ID = "owner"

# The thread that produced this module: Alekya forwarded a Metasis requirement to a
# group, the resume went to Lalitha, and the card named Alekya.
FORWARDER = "Alekya <alekya@rpatechnologyinc.com>"
RECRUITER = "lalitha.y@metasisinfo.com"


class SenderCompanyTests(unittest.TestCase):
    @staticmethod
    def _email(**overrides: object) -> RecruiterEmail:
        defaults = dict(
            owner_id=OWNER_ID,
            sender=FORWARDER,
            subject="Jr. Java Full stack Developer",
            body="body",
            company="RPATECHNOLOGY INC",
        )
        defaults.update(overrides)
        return RecruiterEmail(**defaults)

    def test_company_is_not_claimed_across_a_domain_boundary(self) -> None:
        # The extractor defines `company` as the firm that *sent* the mail. A
        # recruiter on another domain was never named by it.
        self.assertEqual(recruiter_identity_service.sender_company_for(self._email(), RECRUITER), "")

    def test_company_stands_when_the_recruiter_is_the_sender(self) -> None:
        self.assertEqual(
            recruiter_identity_service.sender_company_for(self._email(), "alekya@rpatechnologyinc.com"),
            "RPATECHNOLOGY INC",
        )

    def test_company_stands_for_a_colleague_on_the_same_domain(self) -> None:
        self.assertEqual(
            recruiter_identity_service.sender_company_for(self._email(), "someone.else@rpatechnologyinc.com"),
            "RPATECHNOLOGY INC",
        )

    def test_unknown_and_blank_are_both_absent(self) -> None:
        for value in ("", "   ", "Unknown", None):
            with self.subTest(company=value):
                self.assertEqual(
                    recruiter_identity_service.sender_company_for(
                        self._email(company=value), "alekya@rpatechnologyinc.com"
                    ),
                    "",
                )

    def test_no_address_to_attribute_to_means_no_company(self) -> None:
        self.assertEqual(recruiter_identity_service.sender_company_for(self._email(), None), "")
        self.assertEqual(recruiter_identity_service.sender_company_for(self._email(sender=""), RECRUITER), "")


class RecruiterIdentityTests(unittest.TestCase):
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
    def _email(db: Session, **overrides: object) -> RecruiterEmail:
        defaults = dict(
            owner_id=OWNER_ID,
            sender=FORWARDER,
            subject="Jr. Java Full stack Developer",
            body="body",
            company="RPATECHNOLOGY INC",
            recipient_email=RECRUITER,
            resolved_recruiter_email=RECRUITER,
        )
        defaults.update(overrides)
        email = RecruiterEmail(**defaults)
        db.add(email)
        db.flush()
        return email

    @staticmethod
    def _contact(db: Session, *, address: str, name: str, company: str) -> PremiumNumberContact:
        contact = PremiumNumberContact(
            owner_id=OWNER_ID,
            display_phone_number="",
            recruiter_name=name,
            recruiter_email=address,
            company=company,
            is_recruiter=True,
        )
        db.add(contact)
        db.flush()
        db.add(
            PremiumContactEmail(
                owner_id=OWNER_ID,
                premium_contact_id=contact.id,
                normalized_email=address,
                domain=address.split("@")[-1],
                is_primary=True,
            )
        )
        db.flush()
        return contact

    def test_the_recipient_is_the_recruiter_not_the_forwarder(self) -> None:
        with Session(self.engine) as db:
            identity = recruiter_identity_service.recruiter_identity_for(
                db, self._email(db), owner_id=OWNER_ID
            )
            self.assertEqual(identity.address, RECRUITER)
            # No contact and no signature to read, so the address is the only true
            # thing to show. "Alekya" would name the wrong person.
            self.assertEqual(identity.name, RECRUITER)
            self.assertEqual(identity.company, "")
            self.assertIsNone(identity.contact_id)

    def test_a_contact_record_supplies_the_name_the_mail_never_did(self) -> None:
        with Session(self.engine) as db:
            contact = self._contact(
                db, address=RECRUITER, name="Lalitha Y", company="Metasis Information Systems LLC"
            )
            identity = recruiter_identity_service.recruiter_identity_for(
                db, self._email(db), owner_id=OWNER_ID
            )
            self.assertEqual(identity.name, "Lalitha Y")
            self.assertEqual(identity.company, "Metasis Information Systems LLC")
            self.assertEqual(identity.contact_id, contact.id)

    def test_a_contact_filed_under_unknown_does_not_erase_the_address(self) -> None:
        with Session(self.engine) as db:
            contact = self._contact(db, address=RECRUITER, name="Unknown", company="Unknown")
            identity = recruiter_identity_service.recruiter_identity_for(
                db, self._email(db), owner_id=OWNER_ID
            )
            self.assertEqual(identity.name, RECRUITER)
            self.assertEqual(identity.company, "")
            self.assertEqual(identity.contact_id, contact.id)

    def test_the_from_line_name_is_used_when_the_sender_is_the_recruiter(self) -> None:
        with Session(self.engine) as db:
            email = self._email(
                db,
                sender="Priya <priya@abcstaffing.com>",
                company="ABC Staffing",
                recipient_email=None,
                resolved_recruiter_email=None,
            )
            identity = recruiter_identity_service.recruiter_identity_for(db, email, owner_id=OWNER_ID)
            self.assertEqual(identity.address, "priya@abcstaffing.com")
            self.assertEqual(identity.name, "Priya")
            self.assertEqual(identity.company, "ABC Staffing")

    def test_the_domain_answers_when_the_mail_and_the_contact_cannot(self) -> None:
        # The forwarded case, which is most of them: the mail names the
        # forwarder's firm and there is no contact for the recruiter yet. Her
        # colleagues have named the firm, and that is a statement about her
        # address rather than a guess from its spelling.
        with Session(self.engine) as db:
            colleague = self._contact(
                db, address="ravi@metasisinfo.com", name="Ravi", company="Metasis Information Systems LLC"
            )
            colleague.recruiter_email_domain = "metasisinfo.com"
            db.flush()
            identity = recruiter_identity_service.recruiter_identity_for(
                db, self._email(db), owner_id=OWNER_ID
            )
            self.assertEqual(identity.address, RECRUITER)
            self.assertEqual(identity.company, "Metasis Information Systems LLC")
            self.assertIsNone(identity.contact_id)

    def test_an_unstamped_email_falls_back_to_the_sender(self) -> None:
        # `resolved_recruiter_email` is stamped at run commit and 14 of 3,400 rows
        # never got one. The sender is then the only address there is.
        with Session(self.engine) as db:
            email = self._email(db, resolved_recruiter_email=None, recipient_email=None)
            identity = recruiter_identity_service.recruiter_identity_for(db, email, owner_id=OWNER_ID)
            self.assertEqual(identity.address, "alekya@rpatechnologyinc.com")
            self.assertEqual(identity.name, "Alekya")
            self.assertEqual(identity.company, "RPATECHNOLOGY INC")


class DomainCompanyTests(unittest.TestCase):
    """Whose firm a domain belongs to, when the mail itself never says."""

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
    def _contact(db: Session, *, address: str, company: str) -> None:
        db.add(
            PremiumNumberContact(
                owner_id=OWNER_ID,
                display_phone_number="",
                is_recruiter=True,
                recruiter_name="Someone",
                recruiter_email=address,
                recruiter_email_domain=address.split("@")[-1],
                company=company,
            )
        )
        db.flush()

    @staticmethod
    def _mail(db: Session, *, sender: str, company: str) -> None:
        db.add(
            RecruiterEmail(
                owner_id=OWNER_ID, sender=sender, subject="Java role", body="body", company=company
            )
        )
        db.flush()

    def _company_for(self, db: Session, address: str) -> str:
        return recruiter_identity_service.domain_company_for(db, OWNER_ID, address)

    def test_a_contact_on_the_domain_names_the_firm(self) -> None:
        with Session(self.engine) as db:
            self._contact(db, address="ravi@metasisinfo.com", company="Metasis Information Systems LLC")
            self.assertEqual(self._company_for(db, RECRUITER), "Metasis Information Systems LLC")

    def test_the_most_stated_name_wins(self) -> None:
        with Session(self.engine) as db:
            self._contact(db, address="a@metasisinfo.com", company="Metasis Information Systems LLC")
            self._contact(db, address="b@metasisinfo.com", company="Metasis Information Systems LLC")
            self._contact(db, address="c@metasisinfo.com", company="Metasis Info")
            self.assertEqual(self._company_for(db, RECRUITER), "Metasis Information Systems LLC")

    def test_a_sender_writing_from_the_domain_answers_when_no_contact_does(self) -> None:
        # `email.company` is the sender's own firm, so a mail *from* the domain is
        # that firm naming itself - the one reading of the field that is always safe.
        with Session(self.engine) as db:
            self._mail(db, sender="Ravi <ravi@metasisinfo.com>", company="Metasis Information Systems LLC")
            self.assertEqual(self._company_for(db, RECRUITER), "Metasis Information Systems LLC")

    def test_a_mail_about_the_domain_is_not_a_mail_from_it(self) -> None:
        with Session(self.engine) as db:
            self._mail(db, sender=FORWARDER, company="RPATECHNOLOGY INC")
            self.assertEqual(self._company_for(db, RECRUITER), "")

    def test_free_mail_names_a_person_not_an_employer(self) -> None:
        with Session(self.engine) as db:
            self._contact(db, address="someone@gmail.com", company="Acme Staffing")
            self.assertEqual(self._company_for(db, "recruiter@gmail.com"), "")

    def test_unknown_and_blank_are_not_names(self) -> None:
        with Session(self.engine) as db:
            self._contact(db, address="a@metasisinfo.com", company="Unknown")
            self._contact(db, address="b@metasisinfo.com", company="")
            self.assertEqual(self._company_for(db, RECRUITER), "")

    def test_a_domain_nobody_has_named_stays_unnamed(self) -> None:
        with Session(self.engine) as db:
            self.assertEqual(self._company_for(db, RECRUITER), "")
            self.assertEqual(self._company_for(db, None), "")
            self.assertEqual(self._company_for(db, "not-an-address"), "")


if __name__ == "__main__":
    unittest.main()
