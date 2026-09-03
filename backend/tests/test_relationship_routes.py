"""The ten v3 routes.

Two things they must get right. Every one of them 404s when the feature is off,
so an internal calibration surface is not reachable on a normal deployment. And
the judgment route - the only v3 write path - gives the same 404 for another
owner's cluster as for one that does not exist.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry, OpportunityCluster, RecruiterOpportunity
from app.services import relationship_clustering_service as clustering
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class RelationshipRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.next_id = 1

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.previous_flag = main.settings.feature_relationship_intelligence_enabled
        self.previous_owner = main.settings.owner_id
        main.settings.feature_relationship_intelligence_enabled = True
        main.settings.owner_id = OWNER
        clear_role_taxonomy_cache()

    def tearDown(self) -> None:
        main.settings.feature_relationship_intelligence_enabled = self.previous_flag
        main.settings.owner_id = self.previous_owner
        main.app.dependency_overrides.clear()
        clear_role_taxonomy_cache()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def opportunity(self, db, **overrides) -> RecruiterOpportunity:
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

    def seed_cluster(self) -> str:
        with self.SessionLocal() as db:
            for _ in range(2):
                self.opportunity(db)
            db.commit()
            clustering.run_clustering_pass(db, owner_id=OWNER)
            cluster = db.query(OpportunityCluster).one()
            return str(cluster.id)


class FeatureGateTests(RelationshipRouteTests):
    # An internal calibration surface must not be reachable on a normal
    # deployment, and "not found" is the right answer - not "forbidden", which
    # would confirm it exists.
    def test_every_route_is_not_found_when_the_feature_is_off(self) -> None:
        main.settings.feature_relationship_intelligence_enabled = False

        for method, path in (
            ("post", "/taxonomy/entities/embed"),
            ("get", "/taxonomy/entities/alias-suggestions"),
            ("get", "/relationships/label-queue"),
            ("get", "/relationships/labels/summary"),
            ("post", "/relationships/cluster-pass"),
            ("get", "/relationships/clusters/anything"),
            ("get", "/relationships/opportunities/1/clusters"),
        ):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 404, f"{method} {path}")

    def test_the_write_routes_are_not_found_when_the_feature_is_off(self) -> None:
        main.settings.feature_relationship_intelligence_enabled = False

        self.assertEqual(
            self.client.post("/relationships/labels", json={
                "left_opportunity_id": 1, "right_opportunity_id": 2, "verdict": "same_program",
            }).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/relationships/clusters/x/judgment", json={"verdict": "confirmed"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/taxonomy/entities/aliases/merge", json={
                "entity_type": "company", "keep_id": 1, "alias_id": 2,
            }).status_code,
            404,
        )


class LabelQueueTests(RelationshipRouteTests):
    def test_the_queue_returns_candidates_with_their_fields(self) -> None:
        with self.SessionLocal() as db:
            for _ in range(2):
                self.opportunity(db)
            db.commit()

        payload = self.client.get("/relationships/label-queue?limit=5").json()

        self.assertTrue(payload["candidates"])
        self.assertEqual(payload["verdicts"], ["same_program", "related_distinct", "unrelated", "unsure"])
        self.assertTrue(payload["candidates"][0]["fields"])

    def test_a_verdict_is_recorded_and_summarized(self) -> None:
        with self.SessionLocal() as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = (left.id, right.id)
            db.commit()

        recorded = self.client.post("/relationships/labels", json={
            "left_opportunity_id": ids[0], "right_opportunity_id": ids[1],
            "verdict": "same_program", "sampler": "same_recruiter",
        })
        summary = self.client.get("/relationships/labels/summary").json()

        self.assertEqual(recorded.status_code, 200)
        self.assertIn(recorded.json()["split"], ("train", "test"))
        self.assertEqual(summary["total"], 1)

    def test_an_unknown_verdict_is_a_bad_request(self) -> None:
        with self.SessionLocal() as db:
            left = self.opportunity(db)
            right = self.opportunity(db)
            ids = (left.id, right.id)
            db.commit()

        response = self.client.post("/relationships/labels", json={
            "left_opportunity_id": ids[0], "right_opportunity_id": ids[1], "verdict": "probably",
        })

        self.assertEqual(response.status_code, 400)


class ClusterPassTests(RelationshipRouteTests):
    def test_a_dry_run_reports_without_writing(self) -> None:
        with self.SessionLocal() as db:
            for _ in range(2):
                self.opportunity(db)
            db.commit()

        payload = self.client.post("/relationships/cluster-pass?dry_run=true").json()

        self.assertGreater(payload["clusters_written"], 0)
        self.assertFalse(payload["surfaced"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(OpportunityCluster).count(), 0)

    def test_the_pass_states_its_exclusions(self) -> None:
        with self.SessionLocal() as db:
            for _ in range(2):
                self.opportunity(db)
            db.commit()

        assumptions = self.client.post("/relationships/cluster-pass").json()["assumptions"]

        self.assertTrue(any("shadow mode" in item for item in assumptions))
        self.assertTrue(any("exhaustive" in item for item in assumptions))


class JudgmentRouteTests(RelationshipRouteTests):
    def test_a_cluster_can_be_read_and_confirmed(self) -> None:
        cluster_id = self.seed_cluster()

        detail = self.client.get(f"/relationships/clusters/{cluster_id}").json()
        judged = self.client.post(f"/relationships/clusters/{cluster_id}/judgment", json={"verdict": "confirmed"})

        self.assertEqual(len(detail["members"]), 2)
        self.assertEqual(judged.status_code, 200)
        self.assertEqual(judged.json()["status"], "confirmed")

    # Out of scope and nonexistent must be indistinguishable, or the response
    # confirms that another owner's cluster exists.
    def test_another_owners_cluster_and_a_missing_one_answer_identically(self) -> None:
        cluster_id = self.seed_cluster()
        with self.SessionLocal() as db:
            db.query(OpportunityCluster).update({OpportunityCluster.owner_id: "someone-else"})
            db.commit()

        scoped = self.client.post(f"/relationships/clusters/{cluster_id}/judgment", json={"verdict": "confirmed"})
        missing = self.client.post("/relationships/clusters/no-such-cluster/judgment", json={"verdict": "confirmed"})

        self.assertEqual(scoped.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(scoped.json()["detail"], missing.json()["detail"])

    def test_an_unknown_verdict_is_a_bad_request(self) -> None:
        cluster_id = self.seed_cluster()

        response = self.client.post(f"/relationships/clusters/{cluster_id}/judgment", json={"verdict": "maybe"})

        self.assertEqual(response.status_code, 400)

    def test_clusters_for_an_opportunity_are_listed(self) -> None:
        self.seed_cluster()
        with self.SessionLocal() as db:
            first = db.query(RecruiterOpportunity).order_by(RecruiterOpportunity.id).first()
            opportunity_id = int(first.id)

        payload = self.client.get(f"/relationships/opportunities/{opportunity_id}/clusters").json()

        self.assertEqual(len(payload["clusters"]), 1)


class EntityRouteTests(RelationshipRouteTests):
    def test_alias_suggestions_are_returned_and_nothing_is_written(self) -> None:
        with self.SessionLocal() as db:
            for name, occurrences in (("Horizon Softech Inc", 40), ("Horizon Softech LLC", 3)):
                db.add(CanonicalEntityTaxonomyEntry(
                    owner_id=OWNER, entity_type="company", canonical_name=name,
                    aliases_json="[]", occurrence_count=occurrences, status="approved",
                ))
            db.commit()

        payload = self.client.get("/taxonomy/entities/alias-suggestions?entity_type=company").json()

        self.assertEqual(len(payload["suggestions"]), 1)
        self.assertEqual(payload["suggestions"][0]["keep_name"], "Horizon Softech Inc")
        with self.SessionLocal() as db:
            self.assertEqual(
                {row.status for row in db.query(CanonicalEntityTaxonomyEntry).all()}, {"approved"}
            )

    def test_a_reviewed_merge_is_applied(self) -> None:
        with self.SessionLocal() as db:
            keep = CanonicalEntityTaxonomyEntry(
                owner_id=OWNER, entity_type="company", canonical_name="Horizon Softech Inc",
                aliases_json="[]", occurrence_count=40, status="approved",
            )
            drop = CanonicalEntityTaxonomyEntry(
                owner_id=OWNER, entity_type="company", canonical_name="Horizon Softech LLC",
                aliases_json="[]", occurrence_count=3, status="approved",
            )
            db.add_all([keep, drop])
            db.commit()
            ids = (keep.id, drop.id)

        response = self.client.post("/taxonomy/entities/aliases/merge", json={
            "entity_type": "company", "keep_id": ids[0], "alias_id": ids[1],
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("Horizon Softech LLC", response.json()["aliases"])

    def test_merging_a_missing_entry_is_not_found(self) -> None:
        response = self.client.post("/taxonomy/entities/aliases/merge", json={
            "entity_type": "company", "keep_id": 1, "alias_id": 2,
        })

        self.assertEqual(response.status_code, 404)

    def test_the_embed_backfill_reports_per_entity_type(self) -> None:
        payload = self.client.post("/taxonomy/entities/embed").json()

        self.assertEqual(set(payload), {"role", "company", "location"})


if __name__ == "__main__":
    unittest.main()
