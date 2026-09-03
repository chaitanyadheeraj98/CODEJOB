"""Sampling and recording ground truth.

The canonical-ordering test is the one that protects the whole set: without it
the unique constraint permits both orderings, and the same pair can be labeled
twice with opposite verdicts by the same person.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RecruiterOpportunity, RelationshipLabel
from app.services import relationship_labeling_service as labeling
from app.services import relationship_scoring as scoring
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class LabelingTestCase(unittest.TestCase):
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


class CanonicalOrderingTests(LabelingTestCase):
    def test_a_pair_is_stored_lower_id_first_whichever_way_it_arrives(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = sorted((left.id, right.id))
            db.commit()
            row = labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=ids[1], right_opportunity_id=ids[0],
                verdict="same_program",
            )
            stored = (row.left_opportunity_id, row.right_opportunity_id)

        self.assertEqual(stored, (ids[0], ids[1]))

    def test_the_reversed_pair_updates_rather_than_duplicating(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = sorted((left.id, right.id))
            db.commit()
            labeling.record_label(db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="same_program")
            labeling.record_label(db, owner_id=OWNER, left_opportunity_id=ids[1], right_opportunity_id=ids[0], verdict="unrelated")
            rows = db.query(RelationshipLabel).all()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].verdict, "unrelated")

    def test_the_database_still_refuses_a_duplicate_written_directly(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = sorted((left.id, right.id))
            db.commit()
            labeling.record_label(db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="same_program")
            db.add(RelationshipLabel(
                owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="unrelated",
            ))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_a_pair_of_one_record_with_itself_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            labeling.canonical_pair(7, 7)


class VerdictTests(LabelingTestCase):
    def test_all_four_verdicts_are_accepted(self) -> None:
        with Session(self.engine) as db:
            rows = [self.opportunity(db) for _ in range(5)]
            ids = [row.id for row in rows]
            db.commit()
            for index, verdict in enumerate(labeling.VERDICTS):
                labeling.record_label(
                    db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[index + 1],
                    verdict=verdict,
                )
            verdicts = {row.verdict for row in db.query(RelationshipLabel).all()}

        self.assertEqual(verdicts, set(labeling.VERDICTS))

    # "Unsure" is a first-class verdict: it marks the genuine boundary, and a
    # labeler forced to guess produces a set that measures guessing.
    def test_unsure_is_one_of_them(self) -> None:
        self.assertIn("unsure", labeling.VERDICTS)

    def test_an_unknown_verdict_is_refused(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = (left.id, right.id)
            db.commit()
            with self.assertRaises(ValueError):
                labeling.record_label(
                    db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="probably",
                )


class SplitTests(LabelingTestCase):
    def test_the_split_is_assigned_at_insert(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = (left.id, right.id)
            db.commit()
            split = labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="same_program"
            ).split

        self.assertIn(split, labeling.SPLITS)

    # A pair that moved between train and test on a re-run would leak training
    # data into the held-out set without anybody noticing.
    def test_the_split_is_deterministic_for_a_given_pair(self) -> None:
        self.assertEqual(labeling._split_for(11, 42), labeling._split_for(11, 42))

    def test_both_splits_occur_across_a_realistic_number_of_pairs(self) -> None:
        splits = {labeling._split_for(1, index) for index in range(2, 60)}
        self.assertEqual(splits, set(labeling.SPLITS))

    def test_labeled_pairs_can_be_restricted_to_one_split(self) -> None:
        with Session(self.engine) as db:
            rows = [self.opportunity(db) for _ in range(6)]
            ids = [row.id for row in rows]
            db.commit()
            for other in ids[1:]:
                labeling.record_label(
                    db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=other, verdict="same_program"
                )
            everything = labeling.labeled_pairs(db, owner_id=OWNER)
            train = labeling.labeled_pairs(db, owner_id=OWNER, split="train")
            test = labeling.labeled_pairs(db, owner_id=OWNER, split="test")

        self.assertEqual(len(everything), len(train) + len(test))


class SamplerTests(LabelingTestCase):
    def build_corpus(self, db: Session) -> None:
        # Two same-recruiter pairs (easy) and cross-recruiter pairs sharing a
        # location and a skill (hard negatives).
        for _ in range(2):
            self.opportunity(db, recruiter_number_id=1, email_sender="a@one.com")
        for _ in range(2):
            self.opportunity(db, recruiter_number_id=2, email_sender="b@two.com")
        db.commit()

    def test_already_labeled_pairs_are_never_offered_again(self) -> None:
        with Session(self.engine) as db:
            self.build_corpus(db)
            first = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=1)
            labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=first[0].left_id,
                right_opportunity_id=first[0].right_id, verdict="same_program",
            )
            again = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=10)

        self.assertNotIn((first[0].left_id, first[0].right_id), [(item.left_id, item.right_id) for item in again])

    # A set of easy pairs produces thresholds that look excellent and fail in
    # production, and nothing in the report would reveal it.
    def test_hard_negatives_are_sampled_deliberately(self) -> None:
        with Session(self.engine) as db:
            self.build_corpus(db)
            picked = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=4, hard_negative_ratio=0.5)
            rows = {int(row.id): row for row in db.query(RecruiterOpportunity).all()}
            hard = [
                item for item in picked
                if labeling._is_hard_negative(rows[item.left_id], rows[item.right_id])
            ]

        self.assertGreaterEqual(len(hard), 1)

    def test_a_ratio_of_zero_asks_for_no_hard_negatives(self) -> None:
        with Session(self.engine) as db:
            self.build_corpus(db)
            picked = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=2, hard_negative_ratio=0.0)
            rows = {int(row.id): row for row in db.query(RecruiterOpportunity).all()}
            hard = [
                item for item in picked
                if labeling._is_hard_negative(rows[item.left_id], rows[item.right_id])
            ]

        self.assertEqual(hard, [])

    def test_each_candidate_records_which_block_produced_it(self) -> None:
        with Session(self.engine) as db:
            self.build_corpus(db)
            picked = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=10)

        self.assertTrue(picked)
        for item in picked:
            self.assertIn(item.sampler, scoring.BLOCKING_KEYS)

    def test_a_candidate_shows_exactly_the_signals_the_scorer_reads(self) -> None:
        with Session(self.engine) as db:
            self.build_corpus(db)
            payload = labeling.sample_pairs_for_labeling(db, owner_id=OWNER, limit=1)[0].as_dict()

        shown = {field["key"] for field in payload["fields"]}
        self.assertEqual(shown, set(payload["left"]))
        # Each field carries its production coverage, so a blank reads as a gap
        # in the data rather than a gap in the record.
        self.assertTrue(all(field["coverage"] for field in payload["fields"]))

    def test_an_empty_corpus_offers_nothing_rather_than_failing(self) -> None:
        with Session(self.engine) as db:
            self.assertEqual(labeling.sample_pairs_for_labeling(db, owner_id=OWNER), [])


class SummaryTests(LabelingTestCase):
    def test_the_summary_counts_by_verdict_split_and_sampler(self) -> None:
        with Session(self.engine) as db:
            rows = [self.opportunity(db) for _ in range(3)]
            ids = [row.id for row in rows]
            db.commit()
            labeling.record_label(db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="same_program", sampler="same_recruiter")
            labeling.record_label(db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[2], verdict="unrelated", sampler="same_location_and_skill")
            summary = labeling.label_summary(db, owner_id=OWNER)

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_verdict"], {"same_program": 1, "unrelated": 1})
        self.assertEqual(sum(summary["by_split"].values()), 2)
        self.assertEqual(summary["by_sampler"]["same_recruiter"], 1)

    def test_an_empty_set_summarizes_to_zero_rather_than_dividing_by_it(self) -> None:
        with Session(self.engine) as db:
            summary = labeling.label_summary(db, owner_id=OWNER)

        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["hard_negative_share"], 0.0)

    def test_another_owners_labels_are_not_counted(self) -> None:
        with Session(self.engine) as db:
            rows = [self.opportunity(db) for _ in range(2)]
            ids = [row.id for row in rows]
            db.commit()
            labeling.record_label(db, owner_id="someone-else", left_opportunity_id=ids[0], right_opportunity_id=ids[1], verdict="same_program")
            summary = labeling.label_summary(db, owner_id=OWNER)

        self.assertEqual(summary["total"], 0)


if __name__ == "__main__":
    unittest.main()
