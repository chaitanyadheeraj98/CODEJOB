"""Judgments, and the feedback loop they close.

The suppression tests carry the product promise: a rejected relationship is
never re-proposed identically. "Identically" is load-bearing - a different
member set overlapping a rejected one is a new claim, and refusing to make it
would silently discard real findings.
"""

import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    OpportunityCluster,
    OpportunityClusterMember,
    RecruiterOpportunity,
    RelationshipJudgment,
    RelationshipLabel,
)
from app.services import relationship_clustering_service as clustering
from app.services import relationship_judgment_service as judgments
from app.services import relationship_labeling_service as labeling
from app.services import relationship_scoring as scoring
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class JudgmentTestCase(unittest.TestCase):
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

    def cluster_with(self, db: Session, count: int = 2) -> tuple[str, list[int]]:
        ids = [self.opportunity(db).id for _ in range(count)]
        db.commit()
        clustering.run_clustering_pass(db, owner_id=OWNER)
        cluster = db.query(OpportunityCluster).first()
        return str(cluster.id), ids


class ConfirmTests(JudgmentTestCase):
    def test_confirming_promotes_the_cluster_and_its_badge(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            result = judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="confirmed")
            cluster = db.get(OpportunityCluster, cluster_id)
            member_bands = {row.confidence for row in db.query(OpportunityClusterMember).all()}

        self.assertEqual(result.status, clustering.STATUS_CONFIRMED)
        self.assertEqual(cluster.confidence, scoring.CONFIDENCE_CONFIRMED)
        self.assertEqual(member_bands, {scoring.CONFIDENCE_CONFIRMED})

    # A person asserting a relationship is a recorded fact, and it is the only
    # route into the Confirmed band beyond a shared email thread: the field the
    # spec designed that band around is false on every production row.
    def test_a_confirmation_is_recorded_as_its_own_row(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="confirmed", note="Same program.")
            row = db.query(RelationshipJudgment).one()

        self.assertEqual(row.verdict, "confirmed")
        self.assertEqual(row.subject_type, "cluster")
        self.assertEqual(row.note, "Same program.")
        self.assertEqual(row.suppression_key, "")


class RejectTests(JudgmentTestCase):
    def test_rejecting_marks_the_cluster_and_records_a_suppression_key(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db)
            result = judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="rejected")
            cluster = db.get(OpportunityCluster, cluster_id)

        self.assertEqual(cluster.status, clustering.STATUS_REJECTED)
        self.assertEqual(result.suppression_key, clustering.member_key(ids))

    def test_the_same_member_set_is_not_re_proposed_on_the_next_pass(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="rejected")
            db.query(OpportunityClusterMember).delete()
            db.query(OpportunityCluster).delete()
            db.commit()

            result = clustering.run_clustering_pass(db, owner_id=OWNER)

        self.assertEqual(result.clusters_suppressed, 1)
        self.assertEqual(result.clusters_written, 0)

    # "Never re-proposed identically" is precise on purpose. A different set is
    # a different claim, and refusing to make it would discard real findings.
    def test_a_different_overlapping_set_is_still_proposed(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="rejected")
            db.query(OpportunityClusterMember).delete()
            db.query(OpportunityCluster).delete()
            db.commit()

            self.opportunity(db)
            db.commit()
            result = clustering.run_clustering_pass(db, owner_id=OWNER)

        self.assertEqual(result.clusters_suppressed, 0)
        self.assertEqual(result.clusters_written, 1)


class CorrectTests(JudgmentTestCase):
    def test_correcting_rewrites_membership_and_confirms_it(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db, count=3)
            keep = ids[:2]
            result = judgments.record_judgment(
                db, owner_id=OWNER, cluster_id=cluster_id, verdict="corrected", correct_member_ids=keep
            )
            members = {row.opportunity_id for row in db.query(OpportunityClusterMember).all()}
            cluster = db.get(OpportunityCluster, cluster_id)

        self.assertEqual(members, set(keep))
        self.assertEqual(cluster.status, clustering.STATUS_CONFIRMED)
        self.assertEqual(result.member_ids, sorted(keep))

    def test_the_correction_is_stored_so_the_right_answer_survives(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db, count=3)
            judgments.record_judgment(
                db, owner_id=OWNER, cluster_id=cluster_id, verdict="corrected", correct_member_ids=ids[:2]
            )
            row = db.query(RelationshipJudgment).one()

        self.assertEqual(json.loads(row.correction_json)["member_ids"], sorted(ids[:2]))

    def test_a_correction_can_add_a_record_the_scorer_missed(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db, count=2)
            missed = self.opportunity(db, recruiter_number_id=88, email_sender="x@elsewhere.com", job_title="Nurse")
            missed_id = missed.id
            db.commit()
            judgments.record_judgment(
                db, owner_id=OWNER, cluster_id=cluster_id, verdict="corrected",
                correct_member_ids=[*ids, missed_id],
            )
            members = {row.opportunity_id for row in db.query(OpportunityClusterMember).all()}

        self.assertIn(missed_id, members)

    def test_a_correction_below_two_records_is_refused(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db)
            with self.assertRaises(ValueError):
                judgments.record_judgment(
                    db, owner_id=OWNER, cluster_id=cluster_id, verdict="corrected", correct_member_ids=ids[:1]
                )

    def test_a_correction_naming_another_owners_record_is_refused(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db)
            other = self.opportunity(db, owner_id="someone-else")
            other_id = other.id
            db.commit()
            with self.assertRaises(LookupError):
                judgments.record_judgment(
                    db, owner_id=OWNER, cluster_id=cluster_id, verdict="corrected",
                    correct_member_ids=[ids[0], other_id],
                )


class ScopingTests(JudgmentTestCase):
    # Out of scope and nonexistent must give the same answer, even in a
    # single-owner app: the response must never confirm that another owner's
    # cluster exists.
    def test_another_owners_cluster_is_not_found(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            db.query(OpportunityCluster).update({OpportunityCluster.owner_id: "someone-else"})
            db.commit()
            with self.assertRaises(LookupError) as scoped:
                judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="confirmed")
            with self.assertRaises(LookupError) as missing:
                judgments.record_judgment(db, owner_id=OWNER, cluster_id="no-such-cluster", verdict="confirmed")

        self.assertEqual(str(scoped.exception), str(missing.exception))

    def test_an_unknown_verdict_is_refused(self) -> None:
        with Session(self.engine) as db:
            cluster_id, _ids = self.cluster_with(db)
            with self.assertRaises(ValueError):
                judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="maybe")


class SeparationTests(JudgmentTestCase):
    # Labels are training data; judgments are production feedback on claims the
    # scorer made. Calibrating against judgments would measure the scorer's
    # agreement with itself.
    def test_judgments_never_appear_in_the_labeled_set(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db)
            judgments.record_judgment(db, owner_id=OWNER, cluster_id=cluster_id, verdict="confirmed")
            labeling.record_label(
                db, owner_id=OWNER, left_opportunity_id=ids[0], right_opportunity_id=ids[1],
                verdict="same_program", sampler="same_recruiter",
            )

            self.assertEqual(db.query(RelationshipLabel).count(), 1)
            self.assertEqual(db.query(RelationshipJudgment).count(), 1)
            self.assertEqual(len(labeling.labeled_pairs(db, owner_id=OWNER)), 1)


class DetailTests(JudgmentTestCase):
    def test_cluster_detail_carries_members_and_their_evidence(self) -> None:
        with Session(self.engine) as db:
            cluster_id, ids = self.cluster_with(db)
            detail = judgments.cluster_detail(db, owner_id=OWNER, cluster_id=cluster_id)

        self.assertEqual({member["opportunity_id"] for member in detail["members"]}, set(ids))
        self.assertTrue(detail["members"][0]["evidence"])
        self.assertIn("inferred", detail)

    def test_clusters_for_an_opportunity_returns_the_ones_containing_it(self) -> None:
        with Session(self.engine) as db:
            _cluster_id, ids = self.cluster_with(db)
            found = judgments.clusters_for_opportunity(db, owner_id=OWNER, opportunity_id=ids[0])

        self.assertEqual(len(found), 1)

    def test_an_opportunity_in_no_cluster_returns_nothing(self) -> None:
        with Session(self.engine) as db:
            self.cluster_with(db)
            lonely = self.opportunity(db, recruiter_number_id=77, email_sender="z@nowhere.com", job_title="Nurse")
            lonely_id = lonely.id
            db.commit()
            self.assertEqual(judgments.clusters_for_opportunity(db, owner_id=OWNER, opportunity_id=lonely_id), [])


if __name__ == "__main__":
    unittest.main()
