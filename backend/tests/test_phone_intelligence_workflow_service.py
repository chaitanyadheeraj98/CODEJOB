import inspect
import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import (
    NumberReviewQueue,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    PremiumContactEmail,
    PremiumContactPhone,
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
)
from app.premium_numbers.extraction import ExtractedContactGroup
from app.services.phone_intelligence_workflow_service import (
    JobMetadataAiExtraction,
    PhoneIntelligenceWorkflowService,
    PhoneWorkflowSourceContext,
    _context_from_external_opportunity,
    _context_from_recruiter_email,
    apply_contact_version,
    job_metadata_ai_extraction_from_parsed,
)


def _lead(
    *,
    role: str = "unknown",
    owner_name: str = "Unknown",
    contact_email: str = "",
    company: str = "Unknown",
    relevance_score: int = 0,
    relevant: bool = False,
    reason: str = "insufficient_signals",
    phone_display: str = "(214) 555-1212",
    phone_normalized: str = "12145551212",
    phone_extension: str = "",
    block_id: str = "",
    evidence_text: str = "",
    colocation_verified: bool = False,
    line_type: str = "phone",
) -> ExtractedContactGroup:
    return ExtractedContactGroup(
        phone_number_display=phone_display,
        phone_number_normalized=phone_normalized,
        phone_extension=phone_extension,
        owner_name=owner_name,
        contact_email=contact_email,
        company=company,
        designation="Recruiter" if role == "recruiter" else "Manager",
        purpose="Direct contact",
        confidence="high",
        contact_type="recruiter_direct" if role == "recruiter" else "unknown",
        recruiter_relevance_score=relevance_score,
        is_recruiter_relevant=relevant,
        relevance_reason=reason,
        source_fragment="Call this contact",
        role=role,
        extraction_source="ai",
        block_id=block_id,
        evidence_text=evidence_text,
        colocation_verified=colocation_verified,
        line_type=line_type,
    )


class PhoneIntelligenceWorkflowServiceTests(unittest.TestCase):
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
    def _email(db: Session, message_id: str) -> RecruiterEmail:
        email = RecruiterEmail(
            owner_id="default-owner",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            role="Python Developer",
            location="Dallas",
            salary_text="",
            skills_text="Python, SQL",
            score=80,
            decision="Qualified",
            state="needs_review",
            draft_reply="Thanks",
            source="gmail",
            external_message_id=message_id,
            external_thread_id=f"thread-{message_id}",
            gmail_received_at=datetime.now(UTC),
            end_client="End Client Co",
            implementation_partner="Implementation Co",
            domain="Healthcare",
            resume_file_name="python-resume.pdf",
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    @staticmethod
    def _external(db: Session) -> ExternalOpportunity:
        source = ExternalFeedSource(
            owner_id="default-owner",
            source_type="nvoids",
            base_url="https://nvoids.example",
            enabled=True,
        )
        db.add(source)
        db.flush()
        item = ExternalOpportunity(
            owner_id="default-owner",
            feed_source_id=source.id,
            source_type="nvoids",
            external_post_id="post-1",
            source_url="https://nvoids.example/post-1",
            recruiter_email="row2@agency.example",
            recruiter_phone="(214) 555-1212",
            recruiter_name="Regex Guess",
            company="Client Co",
            role="Python Developer",
            location="Dallas",
            skills_text="Python",
            raw_body="Call (214) 555-1212",
            raw_html="",
            dedupe_hash="hash-1",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_conflicting_identity_does_not_overwrite_active_fields(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Legacy Name",
                company="Legacy Co",
                designation="Legacy Recruiter",
                recruiter_email="legacy@example.com",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            email = self._email(db, "gmail-1")
            current = _lead(
                role="recruiter",
                owner_name="New Name",
                contact_email="new@agency.example",
                company="New Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[current],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            versions = db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).all()
            self.assertEqual(versions, [])
            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "identity_conflict")
            self.assertEqual(review.target_contact_id, contact.id)
            self.assertEqual(contact.recruiter_name, "Legacy Name")
            self.assertEqual(db.query(RecruiterOpportunity).count(), 0)

    def test_rescoring_email_after_its_new_number_review_already_resolved_does_not_crash(self) -> None:
        low_signal = _lead(
            role="unknown",
            owner_name="Ashutosh Rath",
            contact_email="ashutoshr@sysmind.com",
            company="SysMind LLC",
            relevance_score=60,
            relevant=False,
            reason="external_domain,purpose_negative",
            phone_display="(640) 261-1081",
            phone_normalized="16402611081",
        )
        with Session(self.engine) as db:
            email = self._email(db, "resolved-new-number")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[low_signal],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "new_number")
            # Simulate the review getting resolved through some other route (e.g. Mark as
            # Recruiter) after the fact - _idempotency_point only checks *pending* reviews,
            # so a later rescore of the same email/phone no longer finds it there.
            review.state = "classified_recruiter"
            db.commit()

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[low_signal],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            self.assertEqual(db.query(NumberReviewQueue).count(), 1)

    def test_rescoring_email_after_its_identity_conflict_review_was_dismissed_does_not_crash(self) -> None:
        existing = _lead(
            role="recruiter",
            owner_name="Yashasvi Hasija",
            contact_email="yashasvi@empowerprofessionals.com",
            company="Empower Professionals",
            relevance_score=90,
            relevant=True,
            reason="external_domain",
            phone_display="(732) 356-8008 ext 368",
            phone_normalized="17323568008",
            phone_extension="368",
        )
        conflicting = _lead(
            role="recruiter",
            owner_name="Tushar Bhardwaj",
            contact_email="tushar@empowerprofessionals.com",
            company="Empower Professionals Inc",
            relevance_score=85,
            relevant=True,
            reason="external_domain",
            phone_display="(732) 356-8008",
            phone_normalized="17323568008",
            phone_extension="",
        )
        with Session(self.engine) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[existing],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "empower-existing"))

            email = self._email(db, "empower-conflict")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[conflicting],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "identity_conflict").one()
            # Simulate a human dismissing the conflict (contact_identity_service.dismiss) -
            # _upsert_open_conflict_review only looks for a *pending* row before deciding
            # to insert, so a later rescore of the same email/phone no longer finds it there.
            review.state = "dismissed"
            db.commit()

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[conflicting],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            self.assertEqual(
                db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "identity_conflict").count(), 1
            )

    def test_find_contact_for_lead_returns_none_for_switchboard_phone_email_split(self) -> None:
        with Session(self.engine) as db:
            db.add(PremiumNumberContact(
                owner_id="default-owner", is_recruiter=True,
                normalized_phone_number="17323568008", display_phone_number="(732) 356-8008 ext 368",
                phone_extension="368", recruiter_name="Yashasvi Hasija", company="Empower Professionals",
                recruiter_email="yashasvi@empowerprofessionals.com",
            ))
            tushar = PremiumNumberContact(
                owner_id="default-owner", is_recruiter=True,
                normalized_phone_number=None, display_phone_number="",
                recruiter_name="Tushar Bhardwaj", company="Empower Professionals Inc",
                recruiter_email="tushar@empowerprofessionals.com",
            )
            db.add(tushar)
            db.commit()

            # Blank extension - the same lead that would otherwise arbitrarily match
            # whichever switchboard contact sorts first, but this one's email already
            # belongs to a specific, different contact on file.
            lead = _lead(
                role="recruiter",
                owner_name="Tushar Bhardwaj",
                contact_email="tushar@empowerprofessionals.com",
                company="Empower Professionals Inc",
                phone_display="(732) 356-8008",
                phone_normalized="17323568008",
                phone_extension="",
            )
            self.assertIsNone(
                PhoneIntelligenceWorkflowService._find_contact_for_lead(db, "default-owner", lead)
            )

    def test_find_contact_for_lead_ignores_a_soft_deleted_contact_on_phone_and_email(self) -> None:
        with Session(self.engine) as db:
            db.add(PremiumNumberContact(
                owner_id="default-owner", is_recruiter=True,
                normalized_phone_number="12482476165", display_phone_number="(248) 247-6165",
                recruiter_name="Vikas Rao", company="DVG Tech Solutions",
                recruiter_email="vikas@dvgtech.example",
                deleted_at=datetime.now(UTC),
            ))
            db.commit()

            by_phone = _lead(
                role="recruiter", owner_name="Sheshwika Kukkala",
                contact_email="sheshwika@horizonsoftech.net", company="Horizon Softech Inc",
                phone_display="(248) 247-6165", phone_normalized="12482476165",
            )
            by_email = _lead(
                role="recruiter", owner_name="Vikas Rao",
                contact_email="vikas@dvgtech.example", company="DVG Tech Solutions",
                phone_display="", phone_normalized="",
            )
            self.assertIsNone(PhoneIntelligenceWorkflowService._find_contact_for_lead(db, "default-owner", by_phone))
            self.assertIsNone(PhoneIntelligenceWorkflowService._find_contact_for_lead(db, "default-owner", by_email))

    def test_find_contact_for_lead_uses_child_email_but_not_role_addresses(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner", display_phone_number="", is_recruiter=True,
                recruiter_name="Jane", company="Acme",
            )
            role_contact = PremiumNumberContact(
                owner_id="default-owner", display_phone_number="", is_recruiter=True,
                recruiter_name="Hiring Desk", company="Acme",
            )
            db.add_all([contact, role_contact])
            db.flush()
            db.add_all([
                PremiumContactEmail(
                    owner_id="default-owner", premium_contact_id=contact.id,
                    normalized_email="jane@acme.example", domain="acme.example", role="recruiter",
                ),
                PremiumContactEmail(
                    owner_id="default-owner", premium_contact_id=role_contact.id,
                    normalized_email="hr@acme.example", domain="acme.example", role="recruiter",
                ),
            ])
            db.commit()

            child_only = _lead(contact_email="jane@acme.example", phone_display="", phone_normalized="")
            role_address = _lead(contact_email="hr@acme.example", phone_display="", phone_normalized="")
            self.assertEqual(
                PhoneIntelligenceWorkflowService._find_contact_for_lead(db, "default-owner", child_only).id,
                contact.id,
            )
            self.assertIsNone(
                PhoneIntelligenceWorkflowService._find_contact_for_lead(db, "default-owner", role_address)
            )

    def test_pipeline_emits_three_way_review_for_split_identity_without_creating_contact(self) -> None:
        with Session(self.engine) as db:
            phone_owner = PremiumNumberContact(
                owner_id="default-owner", normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212", is_recruiter=True,
                recruiter_name="Phone Owner", company="Acme",
            )
            email_owner = PremiumNumberContact(
                owner_id="default-owner", normalized_phone_number="12145550000",
                display_phone_number="(214) 555-0000", is_recruiter=True,
                recruiter_name="Email Owner", company="Beta",
            )
            db.add_all([phone_owner, email_owner])
            db.flush()
            db.add(PremiumContactEmail(
                owner_id="default-owner", premium_contact_id=email_owner.id,
                normalized_email="email.owner@example.com", domain="example.com", role="recruiter",
            ))
            db.commit()
            original_count = db.query(PremiumNumberContact).count()
            lead = _lead(
                role="recruiter", owner_name="Incoming", contact_email="email.owner@example.com",
                company="Gamma", relevance_score=95, relevant=True, reason="external_domain",
            )

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "split-identity"))

            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "phone_email_cross_conflict")
            self.assertEqual(review.target_contact_id, phone_owner.id)
            self.assertEqual(review.secondary_contact_id, email_owner.id)
            self.assertEqual(db.query(PremiumNumberContact).count(), original_count)

    def test_shared_switchboard_different_extensions_creates_separate_contacts(self) -> None:
        with Session(self.engine) as db:
            saurabh = _lead(
                role="recruiter",
                owner_name="Saurabh Chaudhary",
                contact_email="saurabhc@sysmind.com",
                company="SysMind",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
                phone_display="(609) 897-9670 ext 2197",
                phone_normalized="16098979670",
                phone_extension="2197",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[saurabh],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "sysmind-1"))

            priyanka = _lead(
                role="recruiter",
                owner_name="Priyanka Sinha",
                contact_email="priyankas@sysmind.com",
                company="SysMind LLC",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
                phone_display="(609) 897-9670 ext 2162",
                phone_normalized="16098979670",
                phone_extension="2162",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[priyanka],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "sysmind-2"))

            contacts = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number == "16098979670"
            ).order_by(PremiumNumberContact.id).all()
            self.assertEqual(len(contacts), 2)
            self.assertEqual({c.recruiter_name for c in contacts}, {"Saurabh Chaudhary", "Priyanka Sinha"})
            self.assertEqual({c.phone_extension for c in contacts}, {"2197", "2162"})
            self.assertEqual(db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "identity_conflict").count(), 0)

    def test_legacy_self_duplicate_secondary_phone_does_not_bypass_extension_check(self) -> None:
        # The original multi-identifier migration copied every contact's own primary phone
        # into premium_contact_phones too, with a blank extension (extension tracking didn't
        # exist yet). That legacy row must not be treated as "a genuinely different secondary
        # number, extension doesn't matter" - live proof: contact 49 (Saurabh, ext 2197) had
        # exactly this leftover row and it kept swallowing Priyanka's ext-2162 lead into an
        # identity_conflict against him instead of her own already-split contact.
        with Session(self.engine) as db:
            saurabh = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="16098979670",
                display_phone_number="(609) 897-9670 ext 2197",
                phone_extension="2197",
                is_recruiter=True,
                recruiter_name="Saurabh Chaudhary",
                company="SYSMIND, LLC",
                recruiter_email="saurabhc@sysmind.com",
            )
            db.add(saurabh)
            db.commit()
            db.add(
                PremiumContactPhone(
                    owner_id="default-owner",
                    premium_contact_id=saurabh.id,
                    normalized_phone_number="16098979670",
                    phone_extension="",
                    is_primary=True,
                    is_verified=True,
                    source="migration",
                )
            )
            db.commit()

            priyanka = _lead(
                role="recruiter",
                owner_name="Priyanka Sinha",
                contact_email="priyankas@sysmind.com",
                company="SysMind LLC",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
                phone_display="(609) 897-9670 ext 2162",
                phone_normalized="16098979670",
                phone_extension="2162",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[priyanka],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "sysmind-legacy-dup"))

            self.assertEqual(db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "identity_conflict").count(), 0)
            priyanka_contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "priyankas@sysmind.com"
            ).one()
            self.assertEqual(priyanka_contact.phone_extension, "2162")
            self.assertNotEqual(priyanka_contact.id, saurabh.id)

    def test_two_numbers_in_one_signature_block_resolve_to_one_contact(self) -> None:
        cell = _lead(
            role="recruiter",
            owner_name="Priyanka Sinha",
            contact_email="priyankas@sysmind.com",
            company="SysMind LLC",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(609) 897-9670 ext 2162",
            phone_normalized="16098979670",
            phone_extension="2162",
            block_id="sig-1",
        )
        direct = _lead(
            role="recruiter",
            owner_name="Priyanka Sinha",
            contact_email="priyankas@sysmind.com",
            company="SysMind LLC",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(640) 261-1080",
            phone_normalized="16402611080",
            block_id="sig-1",
        )
        with Session(self.engine) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[cell, direct],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "priyanka-both-numbers"))

            contacts = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "priyankas@sysmind.com"
            ).all()
            self.assertEqual(len(contacts), 1)
            contact = contacts[0]
            # The no-extension number is the direct/desk line and takes over as primary,
            # even though the extension (switchboard) number was seen first.
            self.assertEqual(contact.normalized_phone_number, "16402611080")
            self.assertEqual(contact.phone_extension, "")
            secondary = db.query(PremiumContactPhone).filter(
                PremiumContactPhone.premium_contact_id == contact.id
            ).all()
            self.assertEqual([row.normalized_phone_number for row in secondary], ["16098979670"])

    def test_fax_number_in_the_same_block_never_becomes_primary(self) -> None:
        real_phone = _lead(
            role="employer", owner_name="Mohan Edara", contact_email="mohan@horizonsoftech.net",
            company="Horizon Softech Inc", reason="employer_domain",
            phone_display="(248) 722-2694", phone_normalized="12487222694", block_id="signature-1",
        )
        fax = _lead(
            role="employer", owner_name="Mohan Edara", contact_email="mohan@horizonsoftech.net",
            company="Horizon Softech Inc", reason="employer_domain",
            phone_display="(248) 688-9655", phone_normalized="12486889655", block_id="signature-1",
            line_type="fax",
        )
        with Session(self.engine, autoflush=False) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[real_phone, fax],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "mohan-fax-and-phone"))

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.employer_email == "mohan@horizonsoftech.net"
            ).one()
            self.assertEqual(contact.normalized_phone_number, "12487222694")
            fax_row = db.query(PremiumContactPhone).filter(
                PremiumContactPhone.premium_contact_id == contact.id, PremiumContactPhone.label == "fax",
            ).one()
            self.assertEqual(fax_row.normalized_phone_number, "12486889655")
            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").one()
            changes = json.loads(review.field_changes_json)
            self.assertEqual(changes, [{
                "field": "display_phone_number", "label": "Fax number",
                "old": "", "new": "(248) 688-9655",
            }])

    def test_two_numbers_same_email_different_blocks_merge_with_direct_line_as_primary(self) -> None:
        # Reproduces a real production bug: app/db.py's actual session runs with
        # autoflush=False, so without an explicit flush after the first lead sets the
        # contact's email, the second lead's email lookup below queries stale DB state
        # and never finds the contact it should merge into instead of duplicating.
        # block_id differs on purpose - these are two numbers from different parts of
        # the email, not one signature block (which _resolve_block_contacts already
        # pre-links before this code path even runs).
        company_line = _lead(
            role="recruiter",
            owner_name="Sunitha Sanu",
            contact_email="sunitha@example.com",
            company="Momentousa",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(856) 456-1805 ext 1025",
            phone_normalized="18564561805",
            phone_extension="1025",
            block_id="para-1",
        )
        direct_line = _lead(
            role="recruiter",
            owner_name="Sunitha Sanu",
            contact_email="sunitha@example.com",
            company="Momentousa",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(856) 372-4625",
            phone_normalized="18563724625",
            block_id="para-2",
        )
        with Session(self.engine, autoflush=False) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[company_line, direct_line],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "sunitha-two-numbers"))

            contacts = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "sunitha@example.com"
            ).all()
            self.assertEqual(len(contacts), 1)
            contact = contacts[0]
            # The no-extension (direct/desk) line takes over as primary even though the
            # extension (company) line was seen first.
            self.assertEqual(contact.normalized_phone_number, "18563724625")
            self.assertEqual(contact.phone_extension, "")
            secondary = db.query(PremiumContactPhone).filter(
                PremiumContactPhone.premium_contact_id == contact.id
            ).all()
            self.assertEqual(
                [(row.normalized_phone_number, row.phone_extension) for row in secondary],
                [("18564561805", "1025")],
            )
            # The user should see what the AI merged, not just have it silently applied -
            # a "contact_enriched" review card is flagged with the old-vs-new diff.
            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").one()
            self.assertEqual(review.target_contact_id, contact.id)
            changes = json.loads(review.field_changes_json)
            self.assertEqual(changes, [{
                "field": "display_phone_number", "label": "Phone",
                "old": "(856) 456-1805 ext 1025", "new": "(856) 372-4625",
            }])

    def test_extension_number_arriving_after_the_direct_line_does_not_demote_the_primary(self) -> None:
        direct_line = _lead(
            role="recruiter",
            owner_name="Sunitha Sanu",
            contact_email="sunitha@example.com",
            company="Momentousa",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(856) 372-4625",
            phone_normalized="18563724625",
            block_id="para-1",
        )
        company_line = _lead(
            role="recruiter",
            owner_name="Sunitha Sanu",
            contact_email="sunitha@example.com",
            company="Momentousa",
            relevance_score=95,
            relevant=True,
            reason="external_domain",
            phone_display="(856) 456-1805 ext 1025",
            phone_normalized="18564561805",
            phone_extension="1025",
            block_id="para-2",
        )
        with Session(self.engine, autoflush=False) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[direct_line, company_line],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "sunitha-reverse-order"))

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "sunitha@example.com"
            ).one()
            self.assertEqual(contact.normalized_phone_number, "18563724625")
            self.assertEqual(contact.phone_extension, "")
            secondary = db.query(PremiumContactPhone).filter(
                PremiumContactPhone.premium_contact_id == contact.id
            ).all()
            self.assertEqual(
                [(row.normalized_phone_number, row.phone_extension) for row in secondary],
                [("18564561805", "1025")],
            )

    def test_same_phone_and_email_different_company_is_recorded_as_a_sister_company_not_overwritten(self) -> None:
        # Same email confirms identity outright (classify_identity_match's fast path), so
        # this is fully automatic - the second company seen for that phone must not
        # silently replace or drop the first.
        first_email_lead = _lead(
            role="recruiter", owner_name="Vikas Rao", contact_email="vikas@dvgts.com",
            company="DVG Tech Solutions LLC", relevance_score=90, relevant=True, reason="external_domain",
            phone_display="(609) 888-6198", phone_normalized="16098886198",
        )
        second_email_lead = _lead(
            role="recruiter", owner_name="Vikas Rao", contact_email="vikas@dvgts.com",
            company="DVG Staffing Sister LLC", relevance_score=90, relevant=True, reason="external_domain",
            phone_display="(609) 888-6198", phone_normalized="16098886198",
        )
        with Session(self.engine, autoflush=False) as db:
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[first_email_lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "vikas-first-company"))
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[second_email_lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "vikas-sister-company"))

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number == "16098886198"
            ).one()
            self.assertEqual(contact.company, "DVG Tech Solutions LLC")
            self.assertEqual(contact.secondary_company, "DVG Staffing Sister LLC")
            review = db.query(NumberReviewQueue).filter(
                NumberReviewQueue.reason_code == "contact_enriched", NumberReviewQueue.target_contact_id == contact.id,
            ).one()
            changes = json.loads(review.field_changes_json)
            self.assertEqual(changes, [{
                "field": "secondary_company", "label": "Sister company",
                "old": "", "new": "DVG Staffing Sister LLC",
            }])

            # A third email reusing the exact same sister company must not re-flag it.
            third_email_lead = _lead(
                role="recruiter", owner_name="Vikas Rao", contact_email="vikas@dvgts.com",
                company="DVG Staffing Sister LLC", relevance_score=90, relevant=True, reason="external_domain",
                phone_display="(609) 888-6198", phone_normalized="16098886198",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[third_email_lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "vikas-sister-company-again"))
            self.assertEqual(
                db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").count(), 1,
            )

    def test_evidence_recency_decides_a_direct_line_conflict(self) -> None:
        with Session(self.engine, autoflush=False) as db:
            older_email = self._email(db, "sunitha-first-direct")
            older_email.gmail_received_at = datetime(2026, 1, 1, tzinfo=UTC)
            db.commit()
            first_lead = _lead(
                role="recruiter", owner_name="Sunitha Sanu", contact_email="sunitha@example.com",
                company="Momentousa", relevance_score=95, relevant=True, reason="external_domain",
                phone_display="(856) 111-1111", phone_normalized="18561111111",
            )
            with patch("app.services.phone_intelligence_workflow_service.extract_phone_leads", return_value=[first_lead]):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, older_email)
            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "sunitha@example.com"
            ).one()
            self.assertEqual(contact.normalized_phone_number, "18561111111")

            # Older evidence must not overwrite a direct line the contact already has.
            stale_email = self._email(db, "sunitha-stale-direct")
            stale_email.gmail_received_at = datetime(2025, 12, 1, tzinfo=UTC)
            db.commit()
            stale_lead = _lead(
                role="recruiter", owner_name="Sunitha Sanu", contact_email="sunitha@example.com",
                company="Momentousa", relevance_score=95, relevant=True, reason="external_domain",
                phone_display="(856) 222-2222", phone_normalized="18562222222",
            )
            with patch("app.services.phone_intelligence_workflow_service.extract_phone_leads", return_value=[stale_lead]):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, stale_email)
            db.refresh(contact)
            self.assertEqual(contact.normalized_phone_number, "18561111111")
            self.assertEqual(
                db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").count(), 0,
            )

            # Newer evidence correctly wins and gets flagged for one verification review.
            newer_email = self._email(db, "sunitha-newer-direct")
            newer_email.gmail_received_at = datetime(2026, 2, 1, tzinfo=UTC)
            db.commit()
            newer_lead = _lead(
                role="recruiter", owner_name="Sunitha Sanu", contact_email="sunitha@example.com",
                company="Momentousa", relevance_score=95, relevant=True, reason="external_domain",
                phone_display="(856) 333-3333", phone_normalized="18563333333",
            )
            with patch("app.services.phone_intelligence_workflow_service.extract_phone_leads", return_value=[newer_lead]):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, newer_email)
            db.refresh(contact)
            self.assertEqual(contact.normalized_phone_number, "18563333333")
            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").one()
            self.assertEqual(review.target_contact_id, contact.id)

    def test_nvoids_lead_merges_into_an_existing_gmail_contact_by_email(self) -> None:
        with Session(self.engine, autoflush=False) as db:
            email = self._email(db, "sunitha-gmail-direct")
            direct_lead = _lead(
                role="recruiter", owner_name="Sunitha Sanu", contact_email="sunitha@example.com",
                company="Momentousa", relevance_score=95, relevant=True, reason="external_domain",
                phone_display="(856) 372-4625", phone_normalized="18563724625",
            )
            with patch("app.services.phone_intelligence_workflow_service.extract_phone_leads", return_value=[direct_lead]):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)
            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "sunitha@example.com"
            ).one()
            self.assertEqual(contact.normalized_phone_number, "18563724625")

            item = self._external(db)
            item.recruiter_email = "sunitha@example.com"
            db.commit()
            company_lead = _lead(
                role="recruiter", owner_name="Sunitha Sanu", contact_email="sunitha@example.com",
                company="Momentousa", relevance_score=95, relevant=True, reason="external_domain",
                phone_display="(856) 456-1805 ext 1025", phone_normalized="18564561805", phone_extension="1025",
            )
            with patch("app.services.phone_intelligence_workflow_service.extract_phone_leads", return_value=[company_lead]):
                PhoneIntelligenceWorkflowService().capture_premium_numbers_for_nvoids(db, item, item.raw_body)

            self.assertEqual(
                db.query(PremiumNumberContact).filter(PremiumNumberContact.recruiter_email == "sunitha@example.com").count(), 1,
            )
            db.refresh(contact)
            # The direct (no-extension) line stays primary; the Nvoids company line is
            # recorded as a secondary number on the same contact.
            self.assertEqual(contact.normalized_phone_number, "18563724625")
            secondary = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).all()
            self.assertEqual([row.normalized_phone_number for row in secondary], ["18564561805"])
            review = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "contact_enriched").one()
            self.assertEqual(review.source_external_opportunity_id, item.id)
            self.assertIsNone(review.source_email_id)

    def test_second_number_in_signature_block_does_not_orphan_when_first_number_conflicts(self) -> None:
        with Session(self.engine) as db:
            existing = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="16098979670",
                display_phone_number="(609) 897-9670",
                is_recruiter=True,
                recruiter_name="Saurabh Chaudhary",
                company="SYSMIND, LLC",
                recruiter_email="saurabhc@sysmind.com",
                recruiter_verification_level="verified",
            )
            db.add(existing)
            db.commit()

            cell = _lead(
                role="recruiter",
                owner_name="Priyanka Sinha",
                contact_email="priyankas@sysmind.com",
                company="SysMind LLC",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
                phone_display="(609) 897-9670",
                phone_normalized="16098979670",
                block_id="sig-2",
            )
            direct = _lead(
                role="recruiter",
                owner_name="Priyanka Sinha",
                contact_email="priyankas@sysmind.com",
                company="SysMind LLC",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
                phone_display="(640) 261-1080",
                phone_normalized="16402611080",
                block_id="sig-2",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[cell, direct],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "priyanka-conflict-both-numbers"))

            # Both numbers belong to the same signature block that collided with Saurabh's
            # verified contact - neither should silently promote into its own orphaned
            # contact just because ITS OWN phone alone didn't hit the conflict.
            self.assertEqual(
                db.query(PremiumNumberContact).filter(
                    PremiumNumberContact.recruiter_email == "priyankas@sysmind.com"
                ).count(),
                0,
            )
            reviews = db.query(NumberReviewQueue).filter(NumberReviewQueue.reason_code == "identity_conflict").all()
            self.assertEqual({r.normalized_phone_number for r in reviews}, {"16098979670", "16402611080"})
            self.assertTrue(all(r.target_contact_id == existing.id for r in reviews))

    def test_legacy_snapshot_created_on_first_new_version(self) -> None:
        """temp122.md `## 7. Workstream D` cutover behavior: the first new-model
        version linked onto an already-existing legacy contact (zero linked
        `premium_number_leads` rows for that role) synthesizes a
        `legacy_snapshot` version and sets it active *before* the new
        incoming version is appended as a second, non-active row.
        """
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Legacy Name",
                company="Legacy Co",
                designation="Legacy Recruiter",
                recruiter_email="legacy@example.com",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            self.assertIsNone(contact.active_recruiter_lead_id)
            self.assertEqual(
                db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).count(),
                0,
            )

            email = self._email(db, "gmail-legacy-snapshot")
            current = _lead(
                role="recruiter",
                owner_name="Legacy Name",
                contact_email="legacy@example.com",
                company="Legacy Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[current],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            versions = db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).all()
            self.assertEqual(len(versions), 2)
            self.assertIn("legacy_snapshot", {version.extraction_source for version in versions})
            active = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertIsNotNone(active)
            self.assertEqual(active.extraction_source, "legacy_snapshot")

    def test_shared_switchboard_without_identity_evidence_routes_to_review(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Recruiter",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            email = self._email(db, "gmail-employer")
            employer = _lead(
                role="employer",
                owner_name="Hiring Desk",
                contact_email="desk@client.example",
                company="Client Co",
                reason="employer_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[employer],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            self.assertTrue(contact.is_recruiter)
            self.assertFalse(contact.is_employer)
            self.assertIsNone(contact.active_employer_lead_id)
            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "insufficient_evidence")
            self.assertEqual(review.target_contact_id, contact.id)

    def test_nvoids_uncertain_lead_lands_in_needs_review_with_row2_email(self) -> None:
        with Session(self.engine) as db:
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                result = PhoneIntelligenceWorkflowService().capture_premium_numbers_for_nvoids(
                    db,
                    item,
                    item.raw_body,
                )

            self.assertEqual(result.review_created, 1)
            review = db.query(NumberReviewQueue).one()
            self.assertIsNone(review.source_email_id)
            self.assertEqual(review.source_external_opportunity_id, item.id)
            self.assertEqual(review.contact_email, "row2@agency.example")
            self.assertIsNotNone(review.lineage_id)
            lineage = db.get(OpportunityLineage, review.lineage_id)
            self.assertIsNotNone(lineage)
            self.assertEqual(lineage.origin_type, "nvoids")
            self.assertIsNone(lineage.recruiter_opportunity_id)
            version = db.get(PremiumNumberLead, review.source_lead_id)
            self.assertEqual(version.contact_email, "row2@agency.example")
            self.assertEqual(db.query(PremiumNumberContact).count(), 0)

    def test_idempotency_distinguishes_gmail_and_nvoids_rows(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-distinct")
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers(db, email)
                service.capture_premium_numbers_for_nvoids(db, item, item.raw_body)
                service.capture_premium_numbers_for_nvoids(db, item, item.raw_body)

            self.assertEqual(db.query(PremiumNumberLead).count(), 2)
            self.assertEqual(db.query(NumberReviewQueue).count(), 2)
            self.assertEqual(db.query(OpportunityLineage).count(), 2)

    def test_refresh_nvoids_opportunity_metadata_updates_existing_card(self) -> None:
        # capture_premium_numbers_for_nvoids only writes job-metadata fields once, at first
        # creation (_run's idempotency check skips _create_opportunity for an existing
        # opportunity) - so an already-bridged card never gets fresh AI values without going
        # through this refresh path. This is the fix for the live card that came back blank
        # (Job Details.pdf / "JAVA / SPRING BOOT / KAFKA" posting): reprocessing/rescoring it
        # doesn't touch job_title/location/work_mode/visa/domain/end_client/implementation_partner
        # on the existing row - only this method does.
        with Session(self.engine) as db:
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead(role="recruiter", relevant=True, relevance_score=80)],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers_for_nvoids(db, item, item.raw_body)

            opportunity = db.query(RecruiterOpportunity).one()
            self.assertEqual(opportunity.location, "Dallas")
            self.assertEqual(opportunity.work_mode, "")
            self.assertEqual(opportunity.domain, "")

            ai_extraction = JobMetadataAiExtraction(
                job_title="Senior Java Developer",
                location="Fort Worth, TX",
                work_mode="Onsite",
                visa_restrictions="H1B, GC",
                domain="Airline",
                end_client="Major Airline Co",
                implementation_partner="Jasvik Solutions",
            )
            service.refresh_nvoids_opportunity_metadata(db, opportunity, item, item.raw_body, ai_extraction)

            db.refresh(opportunity)
            self.assertEqual(opportunity.job_title, "Senior Java Developer")
            self.assertEqual(opportunity.location, "Fort Worth, TX")
            self.assertEqual(opportunity.work_mode, "Onsite")
            self.assertEqual(opportunity.visa_restrictions, "H1B, GC")
            self.assertEqual(opportunity.domain, "Airline")
            self.assertEqual(opportunity.end_client, "Major Airline Co")
            self.assertEqual(opportunity.implementation_partner, "Jasvik Solutions")

    def test_refresh_nvoids_opportunity_metadata_never_blanks_existing_value(self) -> None:
        with Session(self.engine) as db:
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead(role="recruiter", relevant=True, relevance_score=80)],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers_for_nvoids(
                    db,
                    item,
                    item.raw_body,
                    JobMetadataAiExtraction(work_mode="Remote", domain="Fintech"),
                )

            opportunity = db.query(RecruiterOpportunity).one()
            self.assertEqual(opportunity.work_mode, "Remote")
            self.assertEqual(opportunity.domain, "Fintech")

            # A later refresh that finds nothing new (AI blank, regex blank) must not erase
            # the values a previous, better extraction already produced.
            service.refresh_nvoids_opportunity_metadata(
                db, opportunity, item, "no metadata in this body", JobMetadataAiExtraction()
            )

            db.refresh(opportunity)
            self.assertEqual(opportunity.work_mode, "Remote")
            self.assertEqual(opportunity.domain, "Fintech")

    def test_context_from_recruiter_email_prefers_stored_ai_role_over_raw_subject(self) -> None:
        # Regression guard for a live card that showed the raw, un-parsed email subject as its
        # Job Title ("FW: Request ID 102705-1 - Java Microservices with GCP") even though
        # email.role already held the clean AI-extracted title from ingest
        # ("Java Microservices with GCP") - _context_from_recruiter_email never read it, so
        # _build_snapshot's regex fallback (which defaults to the raw subject) won every time.
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id="default-owner",
                sender="kartheek@horizonsoftech.net",
                subject="FW: Request ID 102705-1 - Java Microservices with GCP",
                body="Looking for a Java Microservices engineer with GCP experience.",
                role="Java Microservices with GCP",
                location="Alpharetta, GA",
            )
            db.add(email)
            db.commit()
            db.refresh(email)

            context = _context_from_recruiter_email(email)
            self.assertEqual(context.job_title, "Java Microservices with GCP")
            self.assertNotEqual(context.job_title, email.subject)

    def test_refresh_gmail_opportunity_metadata_updates_existing_card(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-refresh")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead(role="recruiter", relevant=True, relevance_score=80)],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers(db, email)

            opportunity = db.query(RecruiterOpportunity).one()
            self.assertEqual(opportunity.job_title, "Python Developer")
            self.assertEqual(opportunity.work_mode, "")
            self.assertEqual(opportunity.visa_restrictions, "")

            ai_extraction = JobMetadataAiExtraction(
                job_title="Senior Python Engineer",
                location="Remote, USA",
                work_mode="Remote",
                visa_restrictions="H1B",
                domain="Fintech",
                end_client="Fresh End Client",
                implementation_partner="Fresh Partner",
            )
            service.refresh_gmail_opportunity_metadata(db, opportunity, email, ai_extraction)

            db.refresh(opportunity)
            self.assertEqual(opportunity.job_title, "Senior Python Engineer")
            self.assertEqual(opportunity.location, "Remote, USA")
            self.assertEqual(opportunity.work_mode, "Remote")
            self.assertEqual(opportunity.visa_restrictions, "H1B")
            self.assertEqual(opportunity.domain, "Fintech")
            self.assertEqual(opportunity.end_client, "Fresh End Client")
            self.assertEqual(opportunity.implementation_partner, "Fresh Partner")

    def test_build_snapshot_uses_recruiter_email_entity_fields(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="gmail",
            subject="Python Developer",
            body="Client: Wrong Regex Client\nLocation: Dallas",
            sender="jobs@agency.example",
            open_url="https://mail.example/message",
            dedupe_key="gmail-entity-fields",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=1,
            external_opportunity_row_id=None,
            end_client="AI End Client",
            implementation_partner="AI Partner",
            domain="Healthcare",
            skills_text="Python, SQL, Kubernetes",
            resume_file_name="python-resume.pdf",
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.end_client, "AI End Client")
        self.assertEqual(snapshot.implementation_partner, "AI Partner")
        self.assertEqual(snapshot.domain, "Healthcare")
        self.assertEqual(snapshot.extracted_skills, "Python, SQL, Kubernetes")
        self.assertEqual(snapshot.resume_file_name, "python-resume.pdf")

    def test_build_snapshot_reports_comma_joined_visa_types(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="gmail",
            subject="Python Developer",
            body="Visa: H1B or GC accepted. No C2C.",
            sender="jobs@agency.example",
            open_url="https://mail.example/message",
            dedupe_key="gmail-visa-types",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=1,
            external_opportunity_row_id=None,
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.visa_restrictions, "GC, H1B")

    def test_build_snapshot_prefers_context_location_over_regex(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="gmail",
            subject="Python Developer",
            body="Location: Wrong Regex Location",
            sender="jobs@agency.example",
            open_url="https://mail.example/message",
            dedupe_key="gmail-location-precedence",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=1,
            external_opportunity_row_id=None,
            location="Fort Worth, TX",
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.location, "Fort Worth, TX")

    def test_build_snapshot_falls_back_to_regex_location_when_context_blank(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="gmail",
            subject="Python Developer",
            body="Location: Dallas",
            sender="jobs@agency.example",
            open_url="https://mail.example/message",
            dedupe_key="gmail-location-fallback",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=1,
            external_opportunity_row_id=None,
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.location, "Dallas")

    def test_build_snapshot_prefers_context_job_metadata_over_regex(self) -> None:
        """Round 4/5 follow-up (Nvoids AI-first restructure): when the context already
        carries AI-derived job_title/work_mode/visa_restrictions, _build_snapshot must
        use those instead of re-deriving from the crude subject/body regex.
        """
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="nvoids",
            subject="Java Developer",
            body="Locals, F2F interview. TEXAS,FORT WORTH. Experience with Airline domain.",
            sender="kevin@jasvik.example",
            open_url="https://nvoids.example/post",
            dedupe_key="nvoids-ai-job-metadata",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=None,
            external_opportunity_row_id=42,
            job_title="Senior Java Developer",
            work_mode="Onsite",
            visa_restrictions="H1B, GC",
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.job_title, "Senior Java Developer")
        self.assertEqual(snapshot.work_mode, "Onsite")
        self.assertEqual(snapshot.visa_restrictions, "H1B, GC")

    def test_build_snapshot_falls_back_to_regex_job_metadata_when_context_blank(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="nvoids",
            subject="Java Developer",
            body="Role: Java Backend Engineer\nRemote work available.",
            sender="kevin@jasvik.example",
            open_url="https://nvoids.example/post",
            dedupe_key="nvoids-ai-job-metadata-fallback",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=None,
            external_opportunity_row_id=43,
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.job_title, "Java Backend Engineer")
        self.assertEqual(snapshot.work_mode, "Remote")

    def test_job_metadata_ai_extraction_from_parsed_uses_ai_result_when_primary(self) -> None:
        parsed = {
            "role": "Senior Java Developer",
            "location": "Fort Worth, TX",
            "domain": "Airline",
            "end_client": "Major Airline Co",
            "implementation_partner": "Jasvik Solutions",
        }
        parser_details = {
            "parser_mode": "ai_primary",
            "ai_extractor_result": {
                "work_mode": "Onsite",
                "visa_hints": ["H1B", "GC"],
            },
        }
        extraction = job_metadata_ai_extraction_from_parsed(parsed, parser_details)
        self.assertEqual(extraction.job_title, "Senior Java Developer")
        self.assertEqual(extraction.location, "Fort Worth, TX")
        self.assertEqual(extraction.work_mode, "Onsite")
        self.assertEqual(extraction.visa_restrictions, "H1B, GC")
        self.assertEqual(extraction.domain, "Airline")
        self.assertEqual(extraction.end_client, "Major Airline Co")
        self.assertEqual(extraction.implementation_partner, "Jasvik Solutions")

    def test_job_metadata_ai_extraction_from_parsed_leaves_work_mode_and_visa_blank_when_not_ai_primary(self) -> None:
        # AI extractor failed or was disabled: parse_email_with_details already fell back
        # to the base regex parser for `parsed`, but work_mode/visa_hints only ever come
        # from the AI payload, so those two must stay blank -> _build_snapshot's own
        # regex fallback (`_extract_job_metadata`) is what fills them, not this function.
        parsed = {"role": "Java Developer", "location": "Dallas"}
        parser_details = {"parser_mode": "ai_fallback", "ai_extractor_result": None}
        extraction = job_metadata_ai_extraction_from_parsed(parsed, parser_details)
        self.assertEqual(extraction.job_title, "Java Developer")
        self.assertEqual(extraction.location, "Dallas")
        self.assertEqual(extraction.work_mode, "")
        self.assertEqual(extraction.visa_restrictions, "")

    def test_context_from_external_opportunity_prefers_ai_extraction_over_regex_fields(self) -> None:
        item = ExternalOpportunity(
            id=90103,
            owner_id="owner-adapter-nvoids",
            feed_source_id=1,
            source_type="nvoids",
            external_post_id="post-adapter-ai",
            recruiter_email="kevin@jasvik.example",
            company="Jasvik Solutions",
            role="Java Developer",
            location="",
            skills_text="Java, Spring Boot, Kafka",
            raw_body="Locals, F2F interview.",
            dedupe_hash="hash-adapter-ai",
        )
        ai_extraction = JobMetadataAiExtraction(
            job_title="Senior Java Developer",
            location="Fort Worth, TX",
            work_mode="Onsite",
            visa_restrictions="H1B, GC",
            domain="Airline",
            end_client="Major Airline Co",
            implementation_partner="Jasvik Solutions",
        )

        context = _context_from_external_opportunity(item, "full jd body", ai_extraction)

        self.assertEqual(context.job_title, "Senior Java Developer")
        self.assertEqual(context.location, "Fort Worth, TX")
        self.assertEqual(context.work_mode, "Onsite")
        self.assertEqual(context.visa_restrictions, "H1B, GC")
        self.assertEqual(context.domain, "Airline")
        self.assertEqual(context.end_client, "Major Airline Co")
        self.assertEqual(context.implementation_partner, "Jasvik Solutions")

        # AI blank on a field -> falls back to the regex-derived item field
        # (location), or stays blank when there is no regex equivalent
        # (work_mode/visa/domain/implementation_partner).
        #
        # `end_client` is deliberately NOT in the first group. `item.company` is
        # the posting company, and mapping it here produced every invalid
        # end_client value in the 2026-09-03 audit - 21 of 29 populated Nvoids
        # rows, including "facing skills<br />..." scraped from "client-facing".
        # An unstated end client stays blank: *not identified*.
        blank_ai_context = _context_from_external_opportunity(item, "full jd body", JobMetadataAiExtraction())
        self.assertEqual(blank_ai_context.job_title, "")
        self.assertEqual(blank_ai_context.location, "")
        self.assertEqual(blank_ai_context.end_client, "")
        self.assertEqual(blank_ai_context.work_mode, "")
        self.assertEqual(blank_ai_context.domain, "")

    def test_context_from_recruiter_email_matches_existing_fields(self) -> None:
        """temp121.md `### 25.10`: the gmail adapter must map every source
        field from a `RecruiterEmail` onto `PhoneWorkflowSourceContext`,
        including the mutually-exclusive row-id invariant.
        """
        email = RecruiterEmail(
            id=90001,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
            external_message_id="adapter-gmail-msg-1",
            external_thread_id="adapter-gmail-thread-1",
            gmail_received_at=datetime(2026, 1, 5, tzinfo=UTC),
            end_client="End Client Co",
            implementation_partner="Implementation Co",
            domain="Healthcare",
            skills_text="Python, SQL",
            resume_file_name="python-resume.pdf",
            location="Fort Worth, TX",
        )

        context = _context_from_recruiter_email(email, source="gmail")

        self.assertEqual(context.owner_id, email.owner_id)
        self.assertEqual(context.source, "gmail")
        self.assertEqual(context.subject, email.subject)
        self.assertEqual(context.body, email.body)
        self.assertEqual(context.sender, email.sender)
        self.assertEqual(context.open_url, email.gmail_message_url)
        self.assertEqual(context.dedupe_key, email.external_message_id)
        self.assertEqual(context.received_at, email.gmail_received_at)
        self.assertEqual(context.recruiter_email_row_id, email.id)
        self.assertIsNone(context.external_opportunity_row_id)
        self.assertEqual(context.end_client, email.end_client)
        self.assertEqual(context.implementation_partner, email.implementation_partner)
        self.assertEqual(context.domain, email.domain)
        self.assertEqual(context.skills_text, email.skills_text)
        self.assertEqual(context.resume_file_name, email.resume_file_name)
        self.assertEqual(context.location, email.location)

        # dedupe_key falls back to "manual-{id}" when there's no external_message_id.
        manual_email = RecruiterEmail(
            id=90002,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
        )
        manual_context = _context_from_recruiter_email(manual_email)
        self.assertEqual(manual_context.dedupe_key, "manual-90002")
        self.assertEqual(manual_context.recruiter_email_row_id, 90002)
        self.assertIsNone(manual_context.external_opportunity_row_id)

        # nullable text fields fall back to "" rather than surfacing None.
        empty_fields_email = RecruiterEmail(
            id=90003,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
            end_client=None,
            implementation_partner=None,
            domain=None,
            skills_text=None,
            resume_file_name=None,
            location=None,
        )
        empty_context = _context_from_recruiter_email(empty_fields_email)
        self.assertEqual(empty_context.end_client, "")
        self.assertEqual(empty_context.implementation_partner, "")
        self.assertEqual(empty_context.domain, "")
        self.assertEqual(empty_context.skills_text, "")
        self.assertEqual(empty_context.resume_file_name, "")
        self.assertEqual(empty_context.location, "")

    def test_context_from_external_opportunity_matches_existing_fields(self) -> None:
        """temp121.md `### 25.10`: the nvoids adapter must map every source
        field from an `ExternalOpportunity` onto `PhoneWorkflowSourceContext`,
        including the mutually-exclusive row-id invariant.
        """
        item = ExternalOpportunity(
            id=90101,
            owner_id="owner-adapter-nvoids",
            feed_source_id=1,
            source_type="nvoids",
            external_post_id="post-adapter-1",
            source_url="https://nvoids.example/post-adapter-1",
            posted_at=datetime(2026, 1, 6, tzinfo=UTC),
            recruiter_email="row2@agency.example",
            recruiter_phone="(214) 555-1212",
            recruiter_name="Regex Guess",
            company="Client Co",
            role="Python Developer",
            location="Dallas",
            skills_text="Python",
            raw_body="Call (214) 555-1212",
            raw_html="",
            dedupe_hash="hash-adapter-1",
        )
        jd_body = "Full JD body text with (214) 555-1212"

        context = _context_from_external_opportunity(item, jd_body)

        self.assertEqual(context.owner_id, item.owner_id)
        self.assertEqual(context.source, "nvoids")
        self.assertEqual(context.subject, item.role)
        self.assertEqual(context.body, jd_body)
        self.assertEqual(context.sender, item.recruiter_email)
        self.assertEqual(context.open_url, item.source_url)
        self.assertEqual(context.dedupe_key, f"nvoids:{item.external_post_id}")
        self.assertEqual(context.received_at, item.posted_at)
        self.assertIsNone(context.recruiter_email_row_id)
        self.assertEqual(context.external_opportunity_row_id, item.id)
        # Not item.company - see the note above on the mapping defect.
        self.assertEqual(context.end_client, "")
        self.assertEqual(context.skills_text, item.skills_text)
        self.assertEqual(context.location, item.location)
        # No equivalent source for these fields on the nvoids side (§23.1) —
        # the adapter leaves them at the dataclass defaults.
        self.assertEqual(context.implementation_partner, "")
        self.assertEqual(context.domain, "")
        self.assertEqual(context.resume_file_name, "")

        # falls back to raw_body when jd_body is falsy.
        fallback_context = _context_from_external_opportunity(item, "")
        self.assertEqual(fallback_context.body, item.raw_body)

        # nullable/blank text fields fall back to "" rather than surfacing None.
        empty_item = ExternalOpportunity(
            id=90102,
            owner_id="owner-adapter-nvoids",
            feed_source_id=1,
            source_type="nvoids",
            external_post_id="post-adapter-2",
            recruiter_email="",
            company="",
            role="",
            skills_text="",
            dedupe_hash="hash-adapter-2",
        )
        empty_context = _context_from_external_opportunity(empty_item, "")
        self.assertEqual(empty_context.subject, "")
        self.assertEqual(empty_context.sender, "")
        self.assertEqual(empty_context.end_client, "")
        self.assertEqual(empty_context.skills_text, "")
        self.assertEqual(empty_context.location, "")
        self.assertEqual(empty_context.body, "")

    def test_gmail_public_methods_unchanged(self) -> None:
        """temp121.md `### 25.10`/`### 25.12`/`### 25.13`: "the single most
        load-bearing test in this entire plan" and "the primary regression
        gate for the highest-risk part of this change." Asserts (a) the four
        public wrapper methods (`process_email`, `extract_only`,
        `classify_only`, `capture_premium_numbers`) still expose the exact
        call conventions every existing caller depends on (§25.5: 4 sites in
        `run_orchestrator.py`, 3 in `main.py`, 1 each in
        `candidate_runtime_service.py` and `premium_numbers/service.py`), and
        (b) each one still produces correct behavior end to end through the
        refactored `PhoneWorkflowSourceContext`/`_run()` internals.
        """
        service_cls = PhoneIntelligenceWorkflowService

        def _param_shape(method: object) -> list[tuple[str, object, bool]]:
            return [
                (name, param.kind, param.default is inspect.Parameter.empty)
                for name, param in inspect.signature(method).parameters.items()
            ]

        # (a) call-convention regression gate.
        self.assertEqual(
            _param_shape(service_cls.process_email),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.extract_only),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.classify_only),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.capture_premium_numbers),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
            ],
        )

        # (b) behavioral regression gate, run against a real database.
        with Session(self.engine) as db:
            service = service_cls()
            recruiter_lead = _lead(
                role="recruiter",
                owner_name="Regression Recruiter",
                contact_email="recruiter@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            # capture_premium_numbers delegates to process_email(source="gmail")
            # (phone_intelligence_workflow_service.py:199) — prove the
            # delegation is intact. The second equivalent input is a no-op
            # version write, while its routing outcomes remain identical.
            email_a = self._email(db, "gmail-unchanged-a")
            email_b = self._email(db, "gmail-unchanged-b")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ):
                via_capture = service.capture_premium_numbers(db, email_a)
                via_process = service.process_email(db, email_b, source="gmail")

            self.assertEqual(via_capture.source, "gmail")
            self.assertEqual(via_process.source, "gmail")
            self.assertEqual(via_capture.stored_count, 1)
            self.assertEqual(via_process.stored_count, 0)
            self.assertEqual(
                (
                    via_capture.review_created,
                    via_capture.recruiter_matches,
                    via_capture.opportunity_created,
                ),
                (
                    via_process.review_created,
                    via_process.recruiter_matches,
                    via_process.opportunity_created,
                ),
            )
            self.assertEqual(via_capture.recruiter_matches, 1)
            self.assertEqual(db.query(PremiumNumberContact).count(), 1)

            # extract_only performs no classification/routing
            # (include_intelligence=False) and skips the identical version;
            # it returns
            # stored_count as a plain int (premium_numbers/service.py:11's
            # calling convention).
            email_c = self._email(db, "gmail-unchanged-extract")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ):
                stored = service.extract_only(db, email_c, source="legacy_extract")
            self.assertIsInstance(stored, int)
            self.assertEqual(stored, 0)
            self.assertEqual(db.query(NumberReviewQueue).count(), 0)
            version = (
                db.query(PremiumNumberLead)
                .filter(PremiumNumberLead.recruiter_email_id == email_c.id)
                .one_or_none()
            )
            self.assertIsNone(version)

            # classify_only: performs classification/routing but does not
            # upsert a premium_number_leads version
            # (include_premium_lead_upsert=False).
            email_d = self._email(db, "gmail-unchanged-classify")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                result = service.classify_only(db, email_d, source="candidate_runtime")
            self.assertEqual(result.review_created, 1)
            self.assertEqual(result.stored_count, 0)
            review = (
                db.query(NumberReviewQueue)
                .filter(NumberReviewQueue.source_email_id == email_d.id)
                .one()
            )
            self.assertIsNone(review.source_lead_id)

    def test_run_produces_equivalent_result_for_equivalent_context(self) -> None:
        """temp121.md `### 25.10`/`### 25.1`: proves `_run()`'s shared
        classification/routing logic is genuinely source-agnostic — not just
        source-agnostic in signature — by feeding it two independently
        adapter-built contexts carrying equivalent subject/body/sender data
        and asserting equivalent classification/routing outcomes.
        """
        with Session(self.engine) as db:
            gmail_email = RecruiterEmail(
                owner_id="owner-run-equiv-gmail",
                sender="jobs@agency.example",
                subject="Python Developer",
                body="Call (214) 555-1212",
                role="Python Developer",
                location="Dallas",
                salary_text="",
                skills_text="Python, SQL",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="",
                source="gmail",
                external_message_id="run-equiv-gmail-1",
                external_thread_id="run-equiv-thread-1",
                gmail_received_at=datetime.now(UTC),
            )
            db.add(gmail_email)
            db.commit()
            db.refresh(gmail_email)

            feed_source = ExternalFeedSource(
                owner_id="owner-run-equiv-nvoids",
                source_type="nvoids",
                base_url="https://nvoids.example",
                enabled=True,
            )
            db.add(feed_source)
            db.flush()
            nvoids_item = ExternalOpportunity(
                owner_id="owner-run-equiv-nvoids",
                feed_source_id=feed_source.id,
                source_type="nvoids",
                external_post_id="run-equiv-post-1",
                source_url="https://nvoids.example/run-equiv-post-1",
                recruiter_email="jobs@agency.example",
                role="Python Developer",
                company="Client Co",
                skills_text="Python",
                raw_body="Call (214) 555-1212",
                raw_html="",
                dedupe_hash="run-equiv-hash-1",
            )
            db.add(nvoids_item)
            db.commit()
            db.refresh(nvoids_item)

            gmail_context = _context_from_recruiter_email(gmail_email, source="gmail")
            nvoids_context = _context_from_external_opportunity(nvoids_item, "Call (214) 555-1212")

            # The two contexts must actually carry equivalent input data —
            # otherwise an equal result wouldn't prove source-agnosticism.
            self.assertEqual(gmail_context.subject, nvoids_context.subject)
            self.assertEqual(gmail_context.body, nvoids_context.body)
            self.assertEqual(gmail_context.sender, nvoids_context.sender)

            recruiter_lead = _lead(
                role="recruiter",
                owner_name="Equivalence Recruiter",
                contact_email="jobs@agency.example",
                company="Agency Co",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
            )
            service = PhoneIntelligenceWorkflowService()
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ) as mocked_extract:
                gmail_result = service._run(
                    db,
                    gmail_context,
                    include_premium_lead_upsert=True,
                    include_intelligence=True,
                )
                nvoids_result = service._run(
                    db,
                    nvoids_context,
                    include_premium_lead_upsert=True,
                    include_intelligence=True,
                )

            # The shared extraction call itself received equivalent
            # sender/subject/body/employer_domains regardless of source.
            first_call, second_call = mocked_extract.call_args_list
            self.assertEqual(first_call.args, second_call.args)
            self.assertEqual(
                first_call.kwargs["employer_domains"],
                second_call.kwargs["employer_domains"],
            )

            def _outcome(result: object) -> tuple:
                return (
                    result.processed_numbers,
                    result.stored_count,
                    result.review_created,
                    result.review_existing,
                    result.recruiter_matches,
                    result.employer_matches,
                    result.opportunity_created,
                    result.opportunity_existing,
                    result.skipped_by_domain_guard,
                )

            self.assertEqual(_outcome(gmail_result), _outcome(nvoids_result))
            self.assertEqual(gmail_result.recruiter_matches, 1)

    def test_company_derived_from_recruiter_domain_when_unknown(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-company-derive")
            lead = _lead(
                role="recruiter",
                owner_name="Ram",
                contact_email="ram@tekwings.com",
                company="Unknown",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number == "12145551212"
            ).one()
            self.assertEqual(contact.company, "Tekwings")

    def test_company_not_derived_from_personal_email_domain(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-company-personal")
            lead = _lead(
                role="recruiter",
                owner_name="Ram",
                contact_email="ram@gmail.com",
                company="Unknown",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number == "12145551212"
            ).one()
            self.assertEqual(contact.company, "Unknown")

    def test_company_not_derived_from_employer_domain(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-company-employer-domain")
            lead = _lead(
                role="recruiter",
                owner_name="HR",
                contact_email="hr@horizonsofttech.net",
                company="Unknown",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number == "12145551212"
            ).one()
            self.assertEqual(contact.company, "Unknown")

    def test_unpromoted_lead_still_gets_premium_number_lead_company_derived(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-company-unpromoted")
            lead = _lead(
                role="unknown",
                owner_name="Unknown",
                contact_email="ram@tekwings.com",
                company="Unknown",
                relevance_score=0,
                relevant=False,
                reason="insufficient_signals",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            stored = db.query(PremiumNumberLead).filter(
                PremiumNumberLead.phone_number_normalized == "12145551212"
            ).one()
            self.assertEqual(stored.company, "Tekwings")

    def test_phone_less_recruiter_lead_creates_contact_with_null_phone(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-no-phone")
            lead = _lead(
                role="recruiter",
                owner_name="Mani",
                contact_email="mani@itbtalent.com",
                company="ITB Talent",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
                phone_display="",
                phone_normalized="",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "mani@itbtalent.com"
            ).one()
            self.assertIsNone(contact.normalized_phone_number)
            self.assertFalse(contact.phone_is_valid)
            self.assertTrue(contact.is_recruiter)
            opportunity = db.query(RecruiterOpportunity).filter(
                RecruiterOpportunity.recruiter_number_id == contact.id
            ).one_or_none()
            self.assertIsNone(opportunity)

    def test_two_phone_less_recruiters_from_same_email_do_not_collide(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-two-no-phone")
            lead_a = _lead(
                role="recruiter", owner_name="Mani", contact_email="mani@itbtalent.com",
                company="ITB Talent", relevance_score=90, relevant=True, reason="external_domain",
                phone_display="", phone_normalized="",
            )
            lead_b = _lead(
                role="recruiter", owner_name="Priya", contact_email="priya@otheragency.example",
                company="Other Agency", relevance_score=90, relevant=True, reason="external_domain",
                phone_display="", phone_normalized="",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead_a, lead_b],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            contacts = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.normalized_phone_number.is_(None)
            ).all()
            self.assertEqual(
                {c.recruiter_email for c in contacts},
                {"mani@itbtalent.com", "priya@otheragency.example"},
            )
            leads = db.query(PremiumNumberLead).filter(
                PremiumNumberLead.phone_number_normalized == ""
            ).all()
            self.assertEqual(
                {lead.contact_email for lead in leads},
                {"mani@itbtalent.com", "priya@otheragency.example"},
            )

    def test_rescore_of_phone_less_lead_matches_existing_contact_by_email(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-rescore-no-phone")
            lead = _lead(
                role="recruiter", owner_name="Mani", contact_email="mani@itbtalent.com",
                company="ITB Talent", relevance_score=90, relevant=True, reason="external_domain",
                phone_display="", phone_normalized="",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers(db, email)
                service.capture_premium_numbers(db, email)

            contacts = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "mani@itbtalent.com"
            ).all()
            self.assertEqual(len(contacts), 1)
            self.assertEqual(contacts[0].seen_count, 2)

    def test_apply_contact_version_recomputes_recruiter_email_domain_on_overwrite(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Shubham Rajak",
                company="Evizot",
                recruiter_email="Shubham Rajak <shubham@horizonsoftech.net>",
                recruiter_email_domain="horizonsoftech.net>",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            version = PremiumNumberLead(
                owner_id="default-owner",
                phone_number_normalized="12145551212",
                phone_number_display="(214) 555-1212",
                role="recruiter",
                contact_email="shubham.rajak@evizot.com",
                owner_name="Shubham Rajak",
                company="Evizot",
            )
            db.add(version)
            db.commit()
            db.refresh(version)

            apply_contact_version(db, contact, version, "recruiter", overwrite=True)

            self.assertEqual(contact.recruiter_email, "shubham.rajak@evizot.com")
            self.assertEqual(contact.recruiter_email_domain, "evizot.com")

    def test_apply_contact_version_preserves_overwritten_company_as_secondary(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner", display_phone_number="", is_recruiter=True, recruiter_name="Jane",
                company="Old Company", recruiter_email="jane@old.example",
            )
            version = PremiumNumberLead(
                owner_id="default-owner", role="recruiter", owner_name="Jane",
                company="New Company", contact_email="jane@new.example",
                phone_number_normalized="", phone_number_display="",
            )
            db.add_all([contact, version])
            db.commit()

            apply_contact_version(db, contact, version, "recruiter", overwrite=True)

            self.assertEqual(contact.company, "New Company")
            self.assertEqual(contact.secondary_company, "Old Company")

    def test_apply_contact_version_recomputes_employer_email_domain_fill_only(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551213",
                display_phone_number="(214) 555-1213",
                is_employer=True,
                owner_name="Unknown",
                company="Unknown",
                employer_email="",
                employer_email_domain="",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            version = PremiumNumberLead(
                owner_id="default-owner",
                phone_number_normalized="12145551213",
                phone_number_display="(214) 555-1213",
                role="employer",
                contact_email="hr@acme.example",
                owner_name="HR Team",
                company="Acme",
            )
            db.add(version)
            db.commit()
            db.refresh(version)

            apply_contact_version(db, contact, version, "employer")

            self.assertEqual(contact.employer_email, "hr@acme.example")
            self.assertEqual(contact.employer_email_domain, "acme.example")

    def test_nvoids_masked_email_synthesizes_review_when_extraction_finds_nothing(self) -> None:
        with Session(self.engine) as db:
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[],
            ):
                result = PhoneIntelligenceWorkflowService().capture_premium_numbers_for_nvoids(
                    db, item, item.raw_body,
                )

            self.assertEqual(result.review_created, 1)
            self.assertEqual(db.query(PremiumNumberContact).count(), 0)
            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.contact_email, "row2@agency.example")
            self.assertEqual(review.reason_code, "new_number")
            self.assertEqual(review.normalized_phone_number, "")

    def test_phone_less_nvoids_lead_creates_contact_with_source_link(self) -> None:
        with Session(self.engine) as db:
            item = self._external(db)
            lead = _lead(
                role="recruiter", owner_name="Priya", contact_email="priya@otheragency.example",
                company="Other Agency", relevance_score=90, relevant=True, reason="external_domain",
                phone_display="", phone_normalized="",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers_for_nvoids(
                    db, item, item.raw_body,
                )

            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "priya@otheragency.example"
            ).one()
            self.assertIsNone(contact.normalized_phone_number)
            self.assertEqual(contact.source_type, "nvoids")
            self.assertEqual(contact.source_link_url, item.source_url)

    def test_recruiter_promotion_blocked_for_unverified_employer_contact(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_employer=True,
                is_recruiter=False,
                owner_name="Hiring Desk",
                company="Client Co",
                recruiter_verification_level="unverified",
            )
            db.add(contact)
            db.commit()

            email = self._email(db, "gmail-flagged-conflict")
            lead = _lead(
                role="recruiter",
                owner_name="New Recruiter",
                contact_email="new@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            self.assertFalse(contact.is_recruiter)
            pending = db.query(NumberReviewQueue).filter(
                NumberReviewQueue.normalized_phone_number == "12145551212"
            ).one()
            self.assertEqual(pending.state, "pending")

    def test_verified_contact_is_never_auto_promoted(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_employer=True,
                is_recruiter=False,
                owner_name="Hiring Desk",
                company="Client Co",
                recruiter_verification_level="verified",
            )
            db.add(contact)
            db.commit()

            email = self._email(db, "gmail-flagged-verified")
            lead = _lead(
                role="recruiter",
                owner_name="New Recruiter",
                contact_email="new@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[lead],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            self.assertFalse(contact.is_recruiter)
            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "identity_conflict")
            self.assertIn("verified_contact_locked", review.relevance_reason)

    def test_confirmed_match_fills_blanks_and_increments_seen_count(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Stable Name",
                recruiter_email="stable@agency.example",
                company="Unknown",
                designation="Unknown",
            )
            db.add(contact)
            db.commit()
            email = self._email(db, "gmail-confirmed-fill")
            candidate = _lead(
                role="recruiter",
                owner_name="Different Model Name",
                contact_email="stable@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[candidate],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            self.assertEqual(contact.recruiter_name, "Stable Name")
            self.assertEqual(contact.company, "Agency Co")
            self.assertEqual(contact.designation, "Recruiter")
            self.assertEqual(contact.seen_count, 2)

    def test_identical_repeat_skips_new_version(self) -> None:
        with Session(self.engine) as db:
            first_email = self._email(db, "gmail-repeat-1")
            second_email = self._email(db, "gmail-repeat-2")
            candidate = _lead(
                role="recruiter",
                owner_name="Stable Name",
                contact_email="stable@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[candidate],
            ):
                first = PhoneIntelligenceWorkflowService().capture_premium_numbers(db, first_email)
                second = PhoneIntelligenceWorkflowService().capture_premium_numbers(db, second_email)

            self.assertEqual(first.stored_count, 1)
            self.assertEqual(second.stored_count, 0)
            self.assertEqual(db.query(PremiumNumberLead).count(), 1)
            self.assertEqual(db.query(PremiumNumberContact).one().seen_count, 2)

    def test_recurring_identity_conflict_reuses_open_review(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Existing Name",
                recruiter_email="existing@agency.example",
                company="Existing Co",
            )
            db.add(contact)
            db.commit()
            candidate = _lead(
                role="recruiter",
                owner_name="Conflicting Name",
                contact_email="conflict@other.example",
                company="Other Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[candidate],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "conflict-1"))
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, self._email(db, "conflict-2"))

            review = db.query(NumberReviewQueue).one()
            self.assertEqual(review.reason_code, "identity_conflict")
            self.assertEqual(review.occurrence_count, 2)
            self.assertEqual(review.target_contact_id, contact.id)
            db.refresh(contact)
            self.assertEqual(contact.recruiter_name, "Existing Name")

    def test_untrusted_international_and_source_mismatch_route_to_review(self) -> None:
        cases = (
            (
                "international_number_needs_verification",
                _lead(
                    role="recruiter",
                    owner_name="Global Recruiter",
                    contact_email="global@agency.example",
                    company="Global Agency",
                    relevance_score=95,
                    relevant=True,
                    reason="external_domain",
                    phone_display="+442079460958",
                    phone_normalized="+442079460958",
                    colocation_verified=True,
                ),
            ),
            (
                "source_attribution_failure",
                _lead(
                    role="recruiter",
                    owner_name="Distant Signature",
                    contact_email="signature@agency.example",
                    company="Agency Co",
                    relevance_score=95,
                    relevant=True,
                    reason="external_domain",
                    evidence_text="Distant signature evidence",
                    colocation_verified=False,
                ),
            ),
        )
        with Session(self.engine) as db:
            for index, (reason_code, candidate) in enumerate(cases):
                with self.subTest(reason_code=reason_code), patch(
                    "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                    return_value=[candidate],
                ):
                    PhoneIntelligenceWorkflowService().capture_premium_numbers(
                        db,
                        self._email(db, f"review-route-{index}"),
                    )
                review = db.query(NumberReviewQueue).filter(
                    NumberReviewQueue.normalized_phone_number == candidate.phone_number_normalized
                ).one()
                self.assertEqual(review.reason_code, reason_code)
                self.assertEqual(review.role, "recruiter")
            self.assertEqual(db.query(PremiumNumberContact).count(), 0)


if __name__ == "__main__":
    unittest.main()
