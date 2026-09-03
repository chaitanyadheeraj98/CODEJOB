"""The clustering pass, and the controls that keep it out of sight.

The tests that matter most are the negative ones. A pass that chains Possible
edges produces one component containing everything and still looks like it
works; a pass that surfaces before its precision is measured makes a claim
nobody checked.
"""

import json
import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import (
    OpportunityCluster,
    OpportunityClusterMember,
    RecruiterOpportunity,
    RelationshipJudgment,
)
from app.services import relationship_clustering_service as clustering
from app.services import relationship_scoring as scoring
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class ClusteringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        # SQLite ignores foreign keys unless asked, and the cascade behaviour
        # is part of what this migration promises.
        event.listen(self.engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
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

    def run_pass(self, db: Session, **kwargs) -> clustering.ClusteringPassResult:
        return clustering.run_clustering_pass(db, owner_id=OWNER, **kwargs)


class ShadowModeTests(ClusteringTestCase):
    def test_every_cluster_is_written_shadow_by_default(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            result = self.run_pass(db)
            statuses = {row.status for row in db.query(OpportunityCluster).all()}

        self.assertGreater(result.clusters_written, 0)
        self.assertEqual(statuses, {clustering.STATUS_SHADOW})
        self.assertFalse(result.surfaced)

    # The flag says an operator intended to surface. THRESHOLDS_CALIBRATED says
    # somebody actually measured the precision the claim rests on. An operator
    # cannot supply the second with an environment variable.
    def test_the_flag_alone_does_not_leave_shadow_mode(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            with patch.object(settings, "feature_relationship_surfacing_enabled", True):
                result = self.run_pass(db)
            statuses = {row.status for row in db.query(OpportunityCluster).all()}

        self.assertFalse(scoring.THRESHOLDS_CALIBRATED)
        self.assertFalse(result.surfaced)
        self.assertEqual(statuses, {clustering.STATUS_SHADOW})

    def test_both_gates_together_do_propose(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            with patch.object(settings, "feature_relationship_surfacing_enabled", True):
                with patch.object(scoring, "THRESHOLDS_CALIBRATED", True):
                    result = self.run_pass(db)
            statuses = {row.status for row in db.query(OpportunityCluster).all()}

        self.assertTrue(result.surfaced)
        self.assertEqual(statuses, {clustering.STATUS_PROPOSED})

    def test_shadow_mode_is_stated_in_the_assumptions(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            result = self.run_pass(db)

        self.assertTrue(any("shadow mode" in item for item in result.assumptions))


class ClosureTests(ClusteringTestCase):
    # Chaining weak edges is how a clustering pass produces a single giant
    # component, and it still looks like it is working.
    def test_possible_edges_never_merge_two_clusters(self) -> None:
        with Session(self.engine) as db:
            first_a = self.opportunity(db, recruiter_number_id=1, email_sender="a@one.com")
            first_b = self.opportunity(db, recruiter_number_id=1, email_sender="a@one.com")
            second_a = self.opportunity(db, recruiter_number_id=2, email_sender="b@two.com")
            second_b = self.opportunity(db, recruiter_number_id=2, email_sender="b@two.com")
            first_ids = (first_a.id, first_b.id)
            second_ids = (second_a.id, second_b.id)
            db.commit()
            self.run_pass(db)
            clusters = db.query(OpportunityCluster).all()
            cluster_count = len(clusters)
            memberships = {
                cluster.id: {row.opportunity_id for row in db.query(OpportunityClusterMember).filter_by(cluster_id=cluster.id)}
                for cluster in clusters
            }

        self.assertGreaterEqual(cluster_count, 2)
        for members in memberships.values():
            self.assertFalse({first_ids[0], second_ids[0]}.issubset(members))
        self.assertTrue(any(set(first_ids) <= members for members in memberships.values()))
        self.assertTrue(any(set(second_ids) <= members for members in memberships.values()))

    def test_a_cluster_takes_the_weakest_of_its_bands_not_the_strongest(self) -> None:
        with Session(self.engine) as db:
            # A confirmed edge (shared message) plus a likely one.
            left = self.opportunity(db, gmail_message_id="shared", recruiter_number_id=1)
            middle = self.opportunity(db, gmail_message_id="shared", recruiter_number_id=2, email_sender="sarah@acme-staffing.com")
            right = self.opportunity(db, recruiter_number_id=2, email_sender="sarah@acme-staffing.com")
            expected = {left.id, middle.id, right.id}
            db.commit()
            self.run_pass(db)
            cluster_band = db.query(OpportunityCluster).one().confidence
            members = {row.opportunity_id for row in db.query(OpportunityClusterMember).all()}

        self.assertEqual(members, expected)
        self.assertEqual(cluster_band, scoring.CONFIDENCE_LIKELY)

    def test_a_lone_pair_below_the_bar_produces_no_cluster(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(
                db, recruiter_number_id=99, email_sender="hr@othercorp.io",
                job_title="Registered Nurse", location="Boise, ID", extracted_skills="phlebotomy",
            )
            db.commit()
            result = self.run_pass(db)

        self.assertEqual(result.clusters_written, 0)
        self.assertEqual(db.query(OpportunityCluster).count(), 0)


class IdempotenceTests(ClusteringTestCase):
    def test_two_runs_produce_identical_clusters_and_no_duplicate_members(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            first = sorted((row.cluster_id, row.opportunity_id) for row in db.query(OpportunityClusterMember).all())
            cluster_ids = sorted(row.id for row in db.query(OpportunityCluster).all())

            self.run_pass(db)
            second = sorted((row.cluster_id, row.opportunity_id) for row in db.query(OpportunityClusterMember).all())

        self.assertEqual(first, second)
        # A judgment names a cluster id; re-running must not orphan it.
        self.assertEqual(cluster_ids, sorted(row.id for row in db.query(OpportunityCluster).all()))

    def test_a_dry_run_writes_nothing(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            result = self.run_pass(db, dry_run=True)

        self.assertGreater(result.clusters_written, 0)
        self.assertEqual(db.query(OpportunityCluster).count(), 0)
        self.assertEqual(db.query(OpportunityClusterMember).count(), 0)


class InferredAttributeTests(ClusteringTestCase):
    def test_members_that_disagree_leave_the_attribute_blank(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db, end_client="Wells Fargo")
            self.opportunity(db, end_client="Citibank")
            db.commit()
            self.run_pass(db)
            cluster = db.query(OpportunityCluster).one()

        self.assertEqual(cluster.inferred_end_client, "")

    def test_one_member_carrying_a_value_and_the_rest_blank_is_still_an_agreement(self) -> None:
        # Absent is not disagreement - the same rule the scorer follows.
        with Session(self.engine) as db:
            self.opportunity(db, end_client="Wells Fargo")
            self.opportunity(db, end_client="")
            db.commit()
            self.run_pass(db)
            cluster = db.query(OpportunityCluster).one()

        self.assertEqual(cluster.inferred_end_client, "Wells Fargo")

    def test_no_member_carrying_a_value_leaves_it_blank(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            cluster = db.query(OpportunityCluster).one()

        self.assertEqual(cluster.inferred_end_client, "")
        self.assertEqual(cluster.inferred_partner, "")
        self.assertEqual(cluster.inferred_domain, "")


class PopulationTests(ClusteringTestCase):
    def test_semantic_available_is_false_when_any_member_lacks_an_embedding(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            cluster = db.query(OpportunityCluster).one()

        self.assertFalse(cluster.semantic_available)

    def test_the_method_is_recorded_on_every_cluster(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)

        self.assertEqual({row.method for row in db.query(OpportunityCluster).all()}, {scoring.METHOD})


class SuppressionTests(ClusteringTestCase):
    def test_a_rejected_member_set_is_not_re_proposed(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            self.run_pass(db)
            key = clustering.member_key([left.id, right.id])
            db.query(OpportunityCluster).delete()
            db.add(RelationshipJudgment(
                owner_id=OWNER, subject_type="cluster", subject_id="gone",
                verdict="rejected", suppression_key=key,
            ))
            db.commit()
            result = self.run_pass(db)

        self.assertEqual(result.clusters_suppressed, 1)
        self.assertEqual(db.query(OpportunityCluster).count(), 0)

    # A different set overlapping a rejected one is a new claim, not the
    # rejected one again.
    def test_an_overlapping_but_different_set_is_not_suppressed(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            third = self.opportunity(db)
            expected = {left.id, right.id, third.id}
            db.commit()
            db.add(RelationshipJudgment(
                owner_id=OWNER, subject_type="cluster", subject_id="gone",
                verdict="rejected", suppression_key=clustering.member_key([left.id, right.id]),
            ))
            db.commit()
            result = self.run_pass(db)
            members = {row.opportunity_id for row in db.query(OpportunityClusterMember).all()}

        self.assertEqual(result.clusters_suppressed, 0)
        self.assertEqual(members, expected)

    def test_another_owners_rejection_does_not_suppress_anything(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            db.commit()
            db.add(RelationshipJudgment(
                owner_id="someone-else", subject_type="cluster", subject_id="gone",
                verdict="rejected", suppression_key=clustering.member_key([left.id, right.id]),
            ))
            db.commit()
            result = self.run_pass(db)

        self.assertEqual(result.clusters_suppressed, 0)


class LifecycleTests(ClusteringTestCase):
    def test_deleting_an_opportunity_cascades_its_member_rows_away(self) -> None:
        with Session(self.engine) as db:
            left = self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            self.assertEqual(db.query(OpportunityClusterMember).count(), 2)

            db.execute(RecruiterOpportunity.__table__.delete().where(RecruiterOpportunity.id == left.id))
            db.commit()

            self.assertEqual(db.query(OpportunityClusterMember).count(), 1)

    def test_a_judged_cluster_survives_a_pass_that_no_longer_produces_it(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            cluster = db.query(OpportunityCluster).one()
            cluster.status = clustering.STATUS_CONFIRMED
            cluster.member_key = "a-key-no-pass-will-reproduce"
            db.commit()

            self.run_pass(db)
            statuses = {row.status for row in db.query(OpportunityCluster).all()}

        # A claim somebody judged is a record. An unseen shadow cluster is not.
        self.assertIn(clustering.STATUS_CONFIRMED, statuses)

    def test_an_unjudged_shadow_cluster_is_dropped_when_no_longer_produced(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            self.run_pass(db)
            db.query(OpportunityCluster).update({OpportunityCluster.member_key: "stale-key"})
            db.commit()

            self.run_pass(db)
            keys = {row.member_key for row in db.query(OpportunityCluster).all()}

        self.assertNotIn("stale-key", keys)


class ReportingTests(ClusteringTestCase):
    def test_the_pass_reports_pairs_per_block_and_the_band_distribution(self) -> None:
        with Session(self.engine) as db:
            for _ in range(3):
                self.opportunity(db)
            db.commit()
            result = self.run_pass(db)

        self.assertEqual(sum(result.pairs_by_block.values()), result.scored_pairs)
        self.assertEqual(sum(result.band_counts.values()), result.scored_pairs)
        self.assertGreaterEqual(result.largest_cluster, 2)

    def test_the_result_serializes_for_a_route(self) -> None:
        with Session(self.engine) as db:
            self.opportunity(db)
            self.opportunity(db)
            db.commit()
            payload = self.run_pass(db).as_dict()

        json.dumps(payload)
        self.assertIn("assumptions", payload)
        self.assertFalse(payload["surfaced"])


if __name__ == "__main__":
    unittest.main()
