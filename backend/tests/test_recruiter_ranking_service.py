"""Recruiter recommendation.

The limited-history tests are the point of this file. Production has 44
applications across one recruiter out of 610, so almost every ranking this
service produces rests on no interaction history at all. Presenting that as a
confident number is the most likely way this work item misleads its only user.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PremiumNumberContact, RecruiterOpportunity
from app.services import recruiter_ranking_service as ranking
from app.services.application_intelligence_service import compute_recruiter_reputation
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class RankingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.next_id = 1
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def contact(self, db: Session, *, name: str, company: str = "Acme Staffing", email: str = "") -> PremiumNumberContact:
        row = PremiumNumberContact(
            owner_id=OWNER,
            normalized_phone_number=f"+1555000{self.next_id:04d}",
            display_phone_number=f"555-000-{self.next_id:04d}",
            is_recruiter=True,
            recruiter_name=name,
            recruiter_email=email or f"{name.lower().replace(' ', '.')}@acme.com",
            company=company,
        )
        self.next_id += 1
        db.add(row)
        db.flush()
        return row

    def opportunity(self, db: Session, *, contact_id: int, **overrides) -> RecruiterOpportunity:
        values = {
            "owner_id": OWNER,
            "recruiter_number_id": contact_id,
            "gmail_message_id": f"msg-{self.next_id}",
            "email_subject": "Java Developer needed",
            "email_sender": "sarah@acme-staffing.com",
            "job_title": "Java Developer",
            "location": "Austin, TX",
            "extracted_skills": "java, spring, sql",
        }
        values.update(overrides)
        self.next_id += 1
        row = RecruiterOpportunity(**values)
        db.add(row)
        db.flush()
        return row


class FocusTests(RankingTestCase):
    def test_focus_reads_100_percent_populated_columns_so_it_works_for_every_recruiter(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            self.opportunity(db, contact_id=contact.id, job_title="Java Developer", location="Austin, TX")
            self.opportunity(db, contact_id=contact.id, job_title="Java Developer", location="Dallas, TX")
            db.commit()
            focus = ranking.compute_recruiter_focus(db, owner_id=OWNER, recruiter_contact_id=contact.id)

        self.assertEqual(focus.opportunity_count, 2)
        self.assertIn("Java Developer", focus.top_roles)
        self.assertTrue(focus.top_skills)
        self.assertEqual(sorted(focus.top_locations), ["Austin, TX", "Dallas, TX"])

    # end_client is 6.7% populated. An empty list contributes nothing; it is
    # never a penalty.
    def test_associated_companies_empty_contributes_zero_rather_than_a_penalty(self) -> None:
        with Session(self.engine) as db:
            known = self.contact(db, name="Known Firm", company="Acme Staffing")
            unknown = self.contact(db, name="Unknown Firm", company="Unknown")
            for target in (known, unknown):
                self.opportunity(db, contact_id=target.id)
            db.commit()
            with_company = ranking.compute_recruiter_focus(db, owner_id=OWNER, recruiter_contact_id=known.id)
            without = ranking.compute_recruiter_focus(db, owner_id=OWNER, recruiter_contact_id=unknown.id)
            opportunity_id = db.query(RecruiterOpportunity).first().id
            ranked = {item.recruiter_contact_id: item for item in ranking.rank_recruiters_for_opportunity(
                db, owner_id=OWNER, opportunity_id=opportunity_id, limit=5
            )}

        self.assertEqual(with_company.associated_companies, ["Acme Staffing"])
        self.assertEqual(without.associated_companies, [])
        self.assertEqual(ranked[known.id].score, ranked[unknown.id].score)


class LimitedHistoryTests(RankingTestCase):
    def test_a_recruiter_with_no_applications_is_still_rankable(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)

        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0].score, 0.0)

    # Structural, not prose: the user is told the factor could not be measured,
    # in a field a renderer will display whether or not the model mentions it.
    def test_limited_history_is_stated_in_the_evidence_and_the_assumptions(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)

        reputation = next(entry for entry in ranked[0].evidence if entry.signal == "reputation")
        self.assertEqual(reputation.match, "absent")
        self.assertEqual(ranked[0].history_label, "limited_history")
        self.assertIn(ranking.LIMITED_HISTORY_ASSUMPTION, ranked[0].assumptions)

    # Substituting a neutral value would let an unmeasured recruiter outrank a
    # measured one on a factor neither of them has.
    def test_an_unmeasured_reputation_scores_zero_not_a_neutral_half(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)

        reputation = next(entry for entry in ranked[0].evidence if entry.signal == "reputation")
        self.assertEqual(reputation.sub_score, 0.0)

    def test_missing_email_threads_are_stated_too(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)

        threads = next(entry for entry in ranked[0].evidence if entry.signal == "thread_responsiveness")
        self.assertEqual(threads.match, "absent")
        self.assertTrue(any("responsiveness" in item for item in ranked[0].assumptions))


class DecompositionTests(RankingTestCase):
    def test_the_evidence_reconstructs_the_score(self) -> None:
        with Session(self.engine) as db:
            first = self.contact(db, name="Sarah Jones")
            second = self.contact(db, name="Marcus Bell")
            target = self.opportunity(db, contact_id=first.id)
            self.opportunity(db, contact_id=second.id, job_title="Data Engineer", extracted_skills="python, spark")
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id, limit=5)

        for item in ranked:
            total = sum(entry.weight * entry.sub_score for entry in item.evidence)
            self.assertAlmostEqual(total, item.score, places=5)

    def test_associated_companies_are_reported_but_never_scored(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones", company="Acme Staffing")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)

        companies = next(entry for entry in ranked[0].evidence if entry.signal == "associated_companies")
        self.assertEqual(companies.weight, 0.0)
        self.assertIn("Acme Staffing", companies.right_value)


class OrderingTests(RankingTestCase):
    def test_the_closer_topic_match_ranks_first(self) -> None:
        with Session(self.engine) as db:
            java = self.contact(db, name="Java Recruiter")
            nurse = self.contact(db, name="Healthcare Recruiter")
            target = self.opportunity(db, contact_id=java.id, job_title="Java Developer", extracted_skills="java, spring")
            self.opportunity(db, contact_id=nurse.id, job_title="Registered Nurse", extracted_skills="phlebotomy", location="Boise, ID")
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id, limit=5)

        self.assertEqual(ranked[0].recruiter_contact_id, java.id)

    # A ranking that reorders on refresh is not a ranking anybody can act on.
    def test_ties_break_deterministically_on_contact_id(self) -> None:
        with Session(self.engine) as db:
            first = self.contact(db, name="A Recruiter")
            second = self.contact(db, name="B Recruiter")
            target = self.opportunity(db, contact_id=first.id)
            self.opportunity(db, contact_id=second.id)
            db.commit()
            once = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id, limit=5)
            twice = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id, limit=5)

        self.assertEqual(
            [item.recruiter_contact_id for item in once],
            [item.recruiter_contact_id for item in twice],
        )
        tied = [item for item in once if item.score == once[0].score]
        self.assertEqual(
            [item.recruiter_contact_id for item in tied],
            sorted(item.recruiter_contact_id for item in tied),
        )

    def test_the_limit_is_respected(self) -> None:
        with Session(self.engine) as db:
            target = None
            for index in range(4):
                contact = self.contact(db, name=f"Recruiter {index}")
                row = self.opportunity(db, contact_id=contact.id)
                target = target or row
            db.commit()
            ranked = ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id, limit=2)

        self.assertEqual(len(ranked), 2)


class ScopingTests(RankingTestCase):
    def test_another_owners_requirement_is_not_found(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id, owner_id="someone-else")
            db.commit()
            with self.assertRaises(LookupError) as scoped:
                ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)
            with self.assertRaises(LookupError) as missing:
                ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=999999)

        self.assertEqual(str(scoped.exception), str(missing.exception))


class RegressionTests(RankingTestCase):
    # compute_recruiter_reputation feeds the shipped rank_opportunities and
    # compare_records. W10 reads it and must not change it.
    def test_compute_recruiter_reputation_is_unchanged_by_ranking(self) -> None:
        with Session(self.engine) as db:
            contact = self.contact(db, name="Sarah Jones")
            target = self.opportunity(db, contact_id=contact.id)
            db.commit()
            before = compute_recruiter_reputation(db, owner_id=OWNER, recruiter_contact_id=contact.id)
            ranking.rank_recruiters_for_opportunity(db, owner_id=OWNER, opportunity_id=target.id)
            after = compute_recruiter_reputation(db, owner_id=OWNER, recruiter_contact_id=contact.id)

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
