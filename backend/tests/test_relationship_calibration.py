"""The calibration harness, plus the regression guard on band assignment.

`BandAssignmentRegressionTests` is the one that earns its keep. A weight edited
without re-running calibration silently shifts every production cluster, and
nothing else in the suite would notice. These fixtures pin the boundary, so an
accidental change fails CI rather than changing what users are told.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterOpportunity
from app.services import relationship_labeling_service as labeling
from app.services import relationship_scoring as scoring
from app.services.role_taxonomy import clear_role_taxonomy_cache
from scripts.calibrate_relationships import build_report, score_labeled_set

OWNER = "owner-under-test"


class CalibrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.next_id = 1
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

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


class BandAssignmentRegressionTests(CalibrationTestCase):
    """Pinned band assignments for a fixed fixture set.

    A weight change that shifts any of these is a change to what users are told
    about their own records, and it must be a deliberate one accompanied by a
    fresh calibration run.
    """

    def test_two_identical_requirements_from_one_recruiter_are_likely(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            self.assertEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_LIKELY)

    def test_a_shared_email_thread_is_confirmed(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, gmail_message_id="shared")
            right = self.opportunity(db, gmail_message_id="shared", recruiter_number_id=9)
            db.commit()
            self.assertEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_CONFIRMED)

    def test_two_unrelated_requirements_yield_no_verdict(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(
                db, recruiter_number_id=42, email_sender="hr@othercorp.io",
                job_title="Registered Nurse", location="Boise, ID", extracted_skills="phlebotomy",
            )
            db.commit()
            self.assertEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_NONE)

    # The hard case the whole phase turns on: similar roles from different
    # recruiters are not the same hiring programme.
    def test_a_cross_recruiter_similar_role_does_not_reach_likely(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db, recruiter_number_id=1, email_sender="a@one.com", location="Austin, TX")
            right = self.opportunity(
                db, recruiter_number_id=2, email_sender="b@two.com",
                job_title="Java Backend Engineer", location="Dallas, TX", extracted_skills="java, kafka",
            )
            db.commit()
            self.assertNotEqual(self.score(db, left, right).confidence, scoring.CONFIDENCE_LIKELY)

    def test_the_weights_have_not_drifted(self) -> None:
        # Pinned deliberately: a weight edit without a calibration re-run is a
        # regression by default.
        self.assertEqual(scoring.BASE_WEIGHTS, {
            "recruiter": 0.20,
            "sender_domain": 0.10,
            "skills": 0.25,
            "job_title": 0.20,
            "location": 0.10,
            "requirement_text": 0.15,
        })
        self.assertEqual(scoring.BONUS_WEIGHTS, {
            "end_client": 0.10,
            "implementation_partner": 0.10,
            "domain": 0.05,
        })
        self.assertAlmostEqual(sum(scoring.BASE_WEIGHTS.values()), 1.0, places=6)


class HarnessTests(CalibrationTestCase):
    def labeled_corpus(self, db: Session) -> None:
        same_left = self.opportunity(db)
        same_right = self.opportunity(db)
        other = self.opportunity(
            db, recruiter_number_id=42, email_sender="hr@othercorp.io",
            job_title="Registered Nurse", location="Boise, ID", extracted_skills="phlebotomy",
        )
        db.commit()
        labeling.record_label(
            db, owner_id=OWNER, left_opportunity_id=same_left.id, right_opportunity_id=same_right.id,
            verdict="same_program", sampler=scoring.BLOCK_RECRUITER,
        )
        labeling.record_label(
            db, owner_id=OWNER, left_opportunity_id=same_left.id, right_opportunity_id=other.id,
            verdict="unrelated", sampler=scoring.BLOCK_LOCATION_SKILL,
        )

    def test_the_harness_scores_the_labeled_set(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            rows = score_labeled_set(db, owner_id=OWNER, split=None)

        self.assertEqual(len(rows), 2)

    def test_precision_is_reported_per_band(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        for band in (scoring.CONFIDENCE_CONFIRMED, scoring.CONFIDENCE_LIKELY, scoring.CONFIDENCE_POSSIBLE):
            self.assertIn(band, report.by_band)
        self.assertEqual(report.by_band[scoring.CONFIDENCE_LIKELY]["true_positive"], 1)
        self.assertEqual(report.by_band[scoring.CONFIDENCE_LIKELY]["false_positive"], 0)

    # A threshold calibrated across both populations is calibrated for neither.
    def test_the_two_populations_are_reported_separately_and_never_pooled(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        self.assertEqual(set(report.by_population), {"semantic_available", "keyword_only"})
        counts = sum(int(item["count"]) for item in report.by_population.values())
        self.assertEqual(counts, report.total)

    def test_figures_are_attributed_to_the_block_that_produced_the_pair(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        self.assertIn(scoring.BLOCK_RECRUITER, report.by_sampler)
        self.assertIn(scoring.BLOCK_LOCATION_SKILL, report.by_sampler)

    # A labeler forced to guess produces a set that measures guessing.
    def test_unsure_is_excluded_from_precision_and_counted_separately(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=left.id, right_opportunity_id=right.id,
                verdict="unsure", sampler=scoring.BLOCK_RECRUITER,
            )
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        self.assertEqual(report.abstained, 1)
        self.assertIsNone(report.by_band[scoring.CONFIDENCE_LIKELY]["precision"])

    def test_the_sweep_starts_above_the_shipped_banner_thresholds(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        thresholds = [point["threshold"] for point in report.threshold_sweep]
        self.assertEqual(min(thresholds), 0.5)
        self.assertTrue(any(threshold > 0.6 for threshold in thresholds))

    def test_a_set_below_the_target_size_says_so_rather_than_reporting_confidently(self) -> None:
        with Session(self.engine) as db:
            self.labeled_corpus(db)
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        self.assertTrue(any("provisional" in note for note in report.notes))
        self.assertTrue(any("THRESHOLDS_CALIBRATED" in note for note in report.notes))

    def test_related_distinct_counts_as_a_negative(self) -> None:
        # "These are similar roles" is exactly what the scorer must not be
        # allowed to call "the same hiring programme".
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=left.id, right_opportunity_id=right.id,
                verdict="related_distinct", sampler=scoring.BLOCK_RECRUITER,
            )
            report = build_report(score_labeled_set(db, owner_id=OWNER, split=None))

        self.assertEqual(report.by_band[scoring.CONFIDENCE_LIKELY]["false_positive"], 1)


if __name__ == "__main__":
    unittest.main()
