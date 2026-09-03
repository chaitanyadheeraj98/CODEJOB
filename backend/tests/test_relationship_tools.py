"""The two v3 chat tools.

The shadow-mode test is the one that carries the phase's central promise: a
relationship the app is still evaluating is not shown to anyone, and the tool
filter is one of four independent points enforcing that.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools import relationships as tools
from app.mcp_server.tools.provenance import INFERENCE_ASSUMPTION
from app.models import OpportunityCluster, PremiumNumberContact, RecruiterOpportunity
from app.services import relationship_clustering_service as clustering
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


class ToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.next_id = 1
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

        owner_patch = patch.object(settings, "owner_id", OWNER)
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        session_patch = patch("app.mcp_server.tools.relationships.SessionLocal", self.SessionLocal)
        session_patch.start()
        self.addCleanup(session_patch.stop)

    def contact(self, db: Session, name: str) -> PremiumNumberContact:
        row = PremiumNumberContact(
            owner_id=OWNER,
            normalized_phone_number=f"+1555000{self.next_id:04d}",
            display_phone_number=f"555-000-{self.next_id:04d}",
            is_recruiter=True,
            recruiter_name=name,
            recruiter_email=f"{name.lower().replace(' ', '.')}@acme.com",
            company="Acme Staffing",
        )
        self.next_id += 1
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

    def build_cluster(self, *, surface: bool = True) -> tuple[str, list[int]]:
        with Session(self.engine) as db:
            ids = [self.opportunity(db).id for _ in range(2)]
            db.commit()
            clustering.run_clustering_pass(db, owner_id=OWNER)
            cluster = db.query(OpportunityCluster).one()
            if surface:
                cluster.status = clustering.STATUS_PROPOSED
                db.commit()
            return str(cluster.id), ids


class ShadowFilterTests(ToolTestCase):
    # The promise the whole phase rests on: nothing the app is still evaluating
    # is shown to anyone.
    def test_a_shadow_cluster_is_never_returned(self) -> None:
        _cluster_id, ids = self.build_cluster(surface=False)

        result = tools.get_relationships("opportunity", str(ids[0]))

        self.assertIn("error", result)

    def test_a_proposed_cluster_is_returned(self) -> None:
        _cluster_id, ids = self.build_cluster()

        result = tools.get_relationships("opportunity", str(ids[0]))

        self.assertEqual(result["action"], "render_relationship_cluster")
        self.assertEqual(result["status"], "proposed")

    # Out of scope, nonexistent and still-being-evaluated must give the same
    # answer, so a reply never confirms what it cannot show.
    def test_shadow_out_of_scope_and_missing_all_give_the_same_answer(self) -> None:
        _cluster_id, ids = self.build_cluster(surface=False)
        shadowed = tools.get_relationships("opportunity", str(ids[0]))
        missing = tools.get_relationships("opportunity", "999999")

        with Session(self.engine) as db:
            db.query(OpportunityCluster).update({OpportunityCluster.owner_id: "someone-else"})
            db.commit()
        scoped = tools.get_relationships("opportunity", str(ids[0]))

        self.assertEqual(shadowed["error"], missing["error"])
        self.assertEqual(shadowed["error"], scoped["error"])


class PayloadTests(ToolTestCase):
    def test_the_payload_always_carries_confidence_and_non_empty_evidence(self) -> None:
        _cluster_id, ids = self.build_cluster()

        provenance = tools.get_relationships("opportunity", str(ids[0]))["provenance"]

        self.assertIn(provenance["confidence"], ("confirmed", "likely", "possible"))
        self.assertTrue(provenance["evidence"])
        self.assertIsInstance(provenance["score"], float)

    def test_the_inference_assumption_is_always_first(self) -> None:
        _cluster_id, ids = self.build_cluster()

        provenance = tools.get_relationships("opportunity", str(ids[0]))["provenance"]

        self.assertEqual(provenance["assumptions"][0], INFERENCE_ASSUMPTION)

    def test_the_blocking_exclusions_are_stated(self) -> None:
        _cluster_id, ids = self.build_cluster()

        assumptions = tools.get_relationships("opportunity", str(ids[0]))["provenance"]["assumptions"]

        self.assertTrue(any("sharing a recruiter" in item for item in assumptions))
        self.assertTrue(any("still being evaluated" in item for item in assumptions))

    def test_recruiter_authored_text_carries_the_untrusted_wrapper(self) -> None:
        _cluster_id, ids = self.build_cluster()

        notice = tools.get_relationships("opportunity", str(ids[0]))["notice"]

        self.assertIn("<untrusted_opportunity_data>", notice)
        self.assertIn("</untrusted_opportunity_data>", notice)

    def test_every_member_carries_its_own_confidence(self) -> None:
        _cluster_id, ids = self.build_cluster()

        members = tools.get_relationships("opportunity", str(ids[0]))["members"]

        self.assertEqual({member["opportunity_id"] for member in members}, set(ids))
        for member in members:
            self.assertIn(member["confidence"], ("confirmed", "likely", "possible"))

    def test_a_cluster_can_be_addressed_by_its_own_id(self) -> None:
        cluster_id, _ids = self.build_cluster()

        result = tools.get_relationships("cluster", cluster_id, mode="cluster")

        self.assertEqual(result["cluster_id"], cluster_id)


class ArgumentTests(ToolTestCase):
    def test_an_unknown_mode_lists_the_valid_ones(self) -> None:
        result = tools.get_relationships("opportunity", "1", mode="guess")

        self.assertIn("error", result)
        self.assertEqual(result["modes"], list(tools.MODES))

    def test_an_unknown_subject_lists_the_valid_ones(self) -> None:
        result = tools.get_relationships("recruiter", "1")

        self.assertIn("error", result)
        self.assertEqual(result["subjects"], list(tools.SUBJECTS))

    def test_a_non_numeric_requirement_id_is_refused(self) -> None:
        self.assertIn("error", tools.get_relationships("opportunity", "the java one"))


class RecommendRecruiterTests(ToolTestCase):
    def seed(self) -> int:
        with Session(self.engine) as db:
            first = self.contact(db, "Sarah Jones")
            second = self.contact(db, "Marcus Bell")
            target = self.opportunity(db, recruiter_number_id=first.id)
            self.opportunity(db, recruiter_number_id=second.id, job_title="Data Engineer", extracted_skills="python")
            db.commit()
            return int(target.id)

    def test_it_returns_a_ranked_list_rather_than_a_tenth_render_kind(self) -> None:
        result = tools.recommend_recruiter(self.seed())

        self.assertEqual(result["action"], "render_ranked_list")
        self.assertTrue(result["rows"])

    def test_every_row_carries_a_confidence_the_renderer_can_badge(self) -> None:
        rows = tools.recommend_recruiter(self.seed())["rows"]

        for row in rows:
            self.assertIn(row["confidence"], ("confirmed", "likely", "possible"))

    # The most likely way this tool misleads its only user: 44 applications
    # across one recruiter out of 610.
    def test_no_recorded_outreach_is_stated_in_the_assumptions(self) -> None:
        result = tools.recommend_recruiter(self.seed())

        self.assertTrue(any("no recorded outreach" in item for item in result["provenance"]["assumptions"]))

    def test_the_reasons_are_read_from_the_service_not_composed(self) -> None:
        rows = tools.recommend_recruiter(self.seed())["rows"]

        self.assertTrue(rows[0]["reasons"])
        self.assertTrue(all(isinstance(reason, str) for reason in rows[0]["reasons"]))

    def test_a_missing_requirement_is_refused(self) -> None:
        self.seed()
        self.assertIn("error", tools.recommend_recruiter(999999))

    def test_the_limit_is_capped(self) -> None:
        target = self.seed()
        self.assertLessEqual(len(tools.recommend_recruiter(target, limit=500)["rows"]), tools.MAX_RECOMMENDED)


class RegistrationTests(unittest.TestCase):
    # v2 left the routing measurement at 35 tools owed. Adding to an unmeasured
    # baseline makes any regression unattributable, so with the flag off the
    # registry stays exactly where v2 left it.
    def test_the_relationship_tools_are_gated_behind_the_feature_flag(self) -> None:
        from app.mcp_server import server

        self.assertEqual(len(server.RELATIONSHIP_TOOLS), 2)
        self.assertFalse(settings.feature_relationship_intelligence_enabled)

    def test_v3_registers_no_propose_tool(self) -> None:
        from app.mcp_server import server

        self.assertFalse([tool for tool in server.RELATIONSHIP_TOOLS if tool.__name__.startswith("propose_")])


if __name__ == "__main__":
    unittest.main()
