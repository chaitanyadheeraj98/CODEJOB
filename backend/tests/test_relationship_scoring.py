"""Decomposed pair scoring.

The load-bearing tests here are the sparsity ones. End client is populated on
6.7% of production opportunities and implementation partner on 1.6%; a scorer
that reads a blank column as a disagreement would rank pairs by extraction
completeness rather than by relatedness, and would look like it was working.
"""

import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterEmail, RecruiterOpportunity, RoleSimilarityCheck, utc_now
from app.services import relationship_scoring as scoring
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class ScoringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.next_id = 1
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def email(self, db: Session, *, thread: str = "", embedding: list[float] | None = None) -> RecruiterEmail:
        row = RecruiterEmail(
            owner_id=OWNER,
            sender="recruiter@example.com",
            subject="A requirement",
            body="Body text",
            role="Java Developer",
            skills_text="java spring",
            external_thread_id=thread or None,
            semantic_embedding=json.dumps(embedding) if embedding else None,
        )
        db.add(row)
        db.flush()
        return row

    def opportunity(self, db: Session, **overrides) -> RecruiterOpportunity:
        values = {
            "owner_id": OWNER,
            "recruiter_number_id": 1,
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

    def score(self, db: Session, left: RecruiterOpportunity, right: RecruiterOpportunity) -> scoring.PairScore:
        context = scoring.build_context(db, owner_id=OWNER, opportunities=[left, right])
        return scoring.score_pair(db, owner_id=OWNER, left=left, right=right, context=context)

    def reconstructs(self, result: scoring.PairScore) -> None:
        total = sum(entry.weight * entry.sub_score for entry in result.evidence)
        self.assertAlmostEqual(total, result.score, places=5, msg="evidence must reconstruct the score it explains")


class DecompositionTests(ScoringTestCase):
    # An explanation that does not add up to the number it explains is a
    # fiction, and this phase is entirely about claims a user can check.
    def test_the_evidence_reconstructs_the_score(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db, job_title="Senior Java Developer", location="Dallas, TX")
            db.commit()
            self.reconstructs(self.score(db, left, right))

    def test_every_scored_pair_carries_non_empty_evidence(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db, recruiter_number_id=9, email_sender="x@other.com", job_title="Nurse", location="", extracted_skills="")
            db.commit()
            result = self.score(db, left, right)

        self.assertTrue(result.evidence)

    def test_every_signal_appears_in_the_evidence_exactly_once(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            signals = [entry.signal for entry in self.score(db, left, right).evidence]

        expected = {"thread", *scoring.BASE_WEIGHTS, *scoring.BONUS_WEIGHTS}
        self.assertEqual(set(signals), expected)
        self.assertEqual(len(signals), len(expected))


class SparsityTests(ScoringTestCase):
    # The rule the production data forces: a blank column is a gap in what was
    # extracted, not a statement that the two records disagree.
    def test_a_blank_bonus_column_is_absent_and_costs_nothing(self) -> None:
        with Session(self.engine) as db:
            # Not an identical pair: a perfect base score leaves the bonus no
            # headroom, and the bonus can only ever add.
            left = self.opportunity(db, location="Austin, TX")
            right = self.opportunity(db, location="Dallas, TX")
            db.commit()
            blank = self.score(db, left, right)

            left.end_client = "Wells Fargo"
            right.end_client = "Wells Fargo"
            db.commit()
            populated = self.score(db, left, right)

        end_client = next(entry for entry in blank.evidence if entry.signal == "end_client")
        self.assertEqual(end_client.match, "absent")
        self.assertEqual(end_client.weight, 0.0)
        self.assertGreater(populated.score, blank.score)

    def test_a_disagreeing_bonus_column_scores_the_same_as_a_blank_one(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            blank = self.score(db, left, right)

            left.end_client = "Wells Fargo"
            right.end_client = "Citi"
            db.commit()
            disagreeing = self.score(db, left, right)

        # Absence never subtracts, and neither does a bonus that simply did not
        # fire - the bonus can only ever add.
        self.assertAlmostEqual(disagreeing.score, blank.score, places=6)

    def test_a_missing_requirement_embedding_renormalizes_rather_than_penalizes(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            without = self.score(db, left, right)

            vector = [1.0, 0.0, 0.0]
            left.source_email_id = self.email(db, embedding=vector).id
            right.source_email_id = self.email(db, embedding=vector).id
            db.commit()
            with_embedding = self.score(db, left, right)

        self.assertFalse(without.semantic_available)
        self.assertTrue(with_embedding.semantic_available)
        # Identical records: the extra signal agrees perfectly, so adding it
        # must not move a score that was already computed over agreement.
        self.assertAlmostEqual(without.score, 1.0, places=5)
        self.assertAlmostEqual(with_embedding.score, 1.0, places=5)

    def test_the_available_base_weights_still_sum_to_one(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, location="", extracted_skills="")
            right = self.opportunity(db, location="", extracted_skills="")
            db.commit()
            result = self.score(db, left, right)

        available = [
            entry for entry in result.evidence
            if entry.signal in scoring.BASE_WEIGHTS and entry.match != "absent"
        ]
        self.assertAlmostEqual(sum(entry.weight for entry in available), 1.0, places=6)

    def test_an_absent_signal_is_still_reported_to_the_user(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, location="")
            right = self.opportunity(db, location="")
            db.commit()
            result = self.score(db, left, right)

        location = next(entry for entry in result.evidence if entry.signal == "location")
        self.assertEqual(location.match, "absent")

    def test_a_pair_with_nothing_in_common_yields_no_surfaceable_verdict(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(
                db, recruiter_number_id=42, email_sender="hr@othercorp.io",
                job_title="Registered Nurse", location="Boise, ID", extracted_skills="phlebotomy",
            )
            db.commit()
            result = self.score(db, left, right)

        self.assertEqual(result.confidence, scoring.CONFIDENCE_NONE)
        self.assertFalse(result.surfaceable)


class ConfirmedBandTests(ScoringTestCase):
    def test_the_same_gmail_message_is_confirmed_without_being_scored(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, gmail_message_id="shared-message")
            right = self.opportunity(
                db, gmail_message_id="shared-message", recruiter_number_id=7,
                job_title="Registered Nurse", location="Boise, ID", extracted_skills="phlebotomy",
                email_sender="hr@othercorp.io",
            )
            db.commit()
            result = self.score(db, left, right)

        self.assertEqual(result.confidence, scoring.CONFIDENCE_CONFIRMED)
        self.assertEqual(result.score, 1.0)
        self.reconstructs(result)

    def test_the_same_email_thread_is_confirmed(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db, recruiter_number_id=7, email_sender="hr@othercorp.io", job_title="Nurse")
            left.source_email_id = self.email(db, thread="thread-1").id
            right.source_email_id = self.email(db, thread="thread-1").id
            db.commit()
            result = self.score(db, left, right)

        self.assertEqual(result.confidence, scoring.CONFIDENCE_CONFIRMED)

    # Confirmed is asserted, but the user still gets to see what agreed and
    # what did not.
    def test_a_confirmed_pair_still_reports_every_other_signal(self) -> None:
        with Session(self.engine) as db:
            # The unique constraint on (owner, recruiter, gmail_message_id)
            # means a shared message id is only ever a cross-recruiter fact -
            # which is precisely the case worth confirming.
            left = self.opportunity(db, gmail_message_id="shared-message")
            right = self.opportunity(db, gmail_message_id="shared-message", recruiter_number_id=7, job_title="Nurse")
            db.commit()
            signals = {entry.signal for entry in self.score(db, left, right).evidence}

        self.assertEqual(signals, {"thread", *scoring.BASE_WEIGHTS, *scoring.BONUS_WEIGHTS})

    # end_client_confirmed is False on all 1,099 production rows. The branch is
    # written and unreachable on purpose, so it activates for free if extraction
    # ever populates the column.
    def test_the_confirmed_end_client_branch_exists_and_fires_when_the_data_allows(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, end_client="Wells Fargo", end_client_confirmed=True)
            right = self.opportunity(
                db, end_client="wells fargo", end_client_confirmed=True,
                recruiter_number_id=7, email_sender="hr@othercorp.io", job_title="Nurse",
            )
            db.commit()
            self.assertEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_CONFIRMED)

    def test_one_sided_confirmation_is_not_a_confirmation(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, end_client="Wells Fargo", end_client_confirmed=True)
            right = self.opportunity(db, end_client="Wells Fargo", end_client_confirmed=False, job_title="Nurse")
            db.commit()
            self.assertNotEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_CONFIRMED)


class BandingTests(ScoringTestCase):
    def test_correlated_signals_alone_do_not_reach_likely(self) -> None:
        # Same recruiter implies the same sender domain. That is one fact, and
        # a Likely claim rests on two independent ones.
        with Session(self.engine) as db:
            left = self.opportunity(db, job_title="", location="", extracted_skills="")
            right = self.opportunity(db, job_title="", location="", extracted_skills="")
            db.commit()
            result = self.score(db, left, right)

        strong = {
            "recruiter_identity" if entry.signal in scoring.CORRELATED_SIGNALS else entry.signal
            for entry in result.evidence
            if entry.weight > 0 and entry.sub_score >= scoring.STRONG_SUB_SCORE and entry.match != "absent"
        }
        self.assertEqual(strong, {"recruiter_identity"})
        self.assertNotEqual(result.confidence, scoring.CONFIDENCE_LIKELY)

    def test_two_independent_strong_signals_reach_likely(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            self.assertEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_LIKELY)

    def test_the_v3_bands_are_stricter_than_the_shipped_banner_thresholds(self) -> None:
        # 89% of 7,056 stored pairs clear the shipped 0.60/0.25. A band that
        # admits a majority of candidate pairs is not a Likely band.
        self.assertGreater(scoring.LIKELY_MIN, 0.60)
        self.assertGreater(scoring.POSSIBLE_MIN, 0.25)

    # Surfacing must stay impossible until a labeled set has actually been
    # measured. This is the one control an environment variable cannot defeat.
    def test_the_thresholds_are_still_marked_uncalibrated(self) -> None:
        self.assertFalse(
            scoring.THRESHOLDS_CALIBRATED,
            "flip this only in the commit that records the measured precision",
        )


class SideEffectTests(ScoringTestCase):
    # compute_role_similarity writes a RoleSimilarityCheck row per call. A pass
    # that called it would add thousands of rows to a shipped table on every
    # run, so v3 uses the same primitives directly instead.
    def test_scoring_writes_no_role_similarity_check_rows(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            before = db.query(RoleSimilarityCheck).count()
            self.score(db, left, right)
            db.commit()
            after = db.query(RoleSimilarityCheck).count()

        self.assertEqual(before, 0)
        self.assertEqual(after, 0)


class CandidatePairTests(ScoringTestCase):
    def test_same_recruiter_pairs_are_generated(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db, recruiter_number_id=3)
            self.opportunity(db, recruiter_number_id=3)
            self.opportunity(db, recruiter_number_id=4, email_sender="x@other.io", location="Reno, NV", extracted_skills="")
            db.commit()
            result = scoring.candidate_pairs(db, owner_id=OWNER)

        self.assertEqual(result.by_block[scoring.BLOCK_RECRUITER], 1)

    def test_each_unordered_pair_appears_once(self) -> None:
        with Session(self.engine) as db:
            for _ in range(4):
                self.opportunity(db)
            db.commit()
            result = scoring.candidate_pairs(db, owner_id=OWNER)

        self.assertEqual(len(result.pairs), len(set(result.pairs)))
        for left, right in result.pairs:
            self.assertLess(left, right)

    def test_max_pairs_is_respected_and_the_cap_is_recorded(self) -> None:
        with Session(self.engine) as db:
            for _ in range(6):
                self.opportunity(db)
            db.commit()
            result = scoring.candidate_pairs(db, owner_id=OWNER, max_pairs=3)

        self.assertEqual(len(result.pairs), 3)
        self.assertTrue(result.capped_blocks)

    # An undocumented exclusion is exactly what the provenance contract exists
    # to prevent, and a cap is an exclusion.
    def test_a_cap_is_stated_in_the_assumptions(self) -> None:
        with Session(self.engine) as db:
            for _ in range(6):
                self.opportunity(db)
            db.commit()
            capped = scoring.candidate_pairs(db, owner_id=OWNER, max_pairs=3)
            uncapped = scoring.candidate_pairs(db, owner_id=OWNER)

        self.assertTrue(any("not compared" in item for item in capped.assumptions))
        self.assertFalse(any("not compared" in item for item in uncapped.assumptions))
        self.assertTrue(any("exhaustive" in item for item in uncapped.assumptions))

    def test_another_owners_opportunities_are_never_paired(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db, owner_id="someone-else")
            db.commit()
            result = scoring.candidate_pairs(db, owner_id=OWNER)

        self.assertEqual(result.pairs, [])

    def test_the_location_and_skill_block_needs_both(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db, recruiter_number_id=1, email_sender="a@one.com", location="Austin, TX", extracted_skills="java")
            self.opportunity(db, recruiter_number_id=2, email_sender="b@two.com", location="Austin, TX", extracted_skills="phlebotomy", job_title="Nurse")
            db.commit()
            shared_location_only = scoring.candidate_pairs(db, owner_id=OWNER)

        self.assertEqual(shared_location_only.by_block[scoring.BLOCK_LOCATION_SKILL], 0)


if __name__ == "__main__":
    unittest.main()
