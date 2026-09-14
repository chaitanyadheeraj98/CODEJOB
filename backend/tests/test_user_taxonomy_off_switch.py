"""E3 / §11.4: `feature_user_taxonomy_enabled`, and what "off" has to mean.

The requirement behind it: *the developer improves the base parser and user
editing is switched off*. So off must hide the overlay, the Settings UI and the
endpoints - and **delete nothing**, because turning it back on has to restore
every entry. That reversibility is the whole point; a switch that discarded
data would be a migration wearing a flag's clothes.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import base_taxonomy, job_intent_learning, main
from app.base_taxonomy import BaseEntityEntry, BaseJobIntentEntry
from app.config import settings
from app.job_intent_learning import POSITIVE_RECRUITER_JD
from app.models import Base, CanonicalEntityTaxonomyEntry, JobIntentTaxonomyEntry
from app.services import role_taxonomy

OWNER = "usr_offswitch"


def _cacheable(fn):
    fn.cache_clear = lambda: None
    return fn


class ResolutionOffSwitchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self._base_on = patch.object(settings, "feature_base_taxonomy_enabled", True)
        self._base_on.start()
        role_taxonomy.clear_role_taxonomy_cache()

    def tearDown(self):
        self._base_on.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        role_taxonomy.clear_role_taxonomy_cache()

    def _artefact(self, *entries: BaseEntityEntry):
        return patch.object(
            base_taxonomy, "load_base_entity_entries", _cacheable(lambda _t: entries)
        )

    def _row(self, canonical: str, *, status: str = "approved", suppressed: bool = False):
        self.db.add(
            CanonicalEntityTaxonomyEntry(
                owner_id=OWNER,
                entity_type="role",
                canonical_name=canonical,
                aliases_json="[]",
                status=status,
                suppressed=suppressed,
            )
        )
        self.db.commit()

    def _load(self):
        role_taxonomy.clear_role_taxonomy_cache()
        return role_taxonomy.load_entity_taxonomy(self.db, owner_id=OWNER, entity_type="role")

    def test_off_hides_the_users_own_entries(self):
        self._row("Prompt Engineer")
        with self._artefact(BaseEntityEntry("role", "Data Engineer", ())):
            with patch.object(settings, "feature_user_taxonomy_enabled", False):
                taxonomy = self._load()
        self.assertNotIn("prompt engineer", taxonomy.lookup)
        self.assertIn("data engineer", taxonomy.lookup, "the base must still stand")

    def test_off_also_ignores_a_tombstone(self):
        """Off means "the base as shipped", so a user's suppression stops
        applying too - otherwise switching off would leave their edits
        half-applied."""
        self._row("Data Engineer", status="dismissed", suppressed=True)
        with self._artefact(BaseEntityEntry("role", "Data Engineer", ())):
            with patch.object(settings, "feature_user_taxonomy_enabled", False):
                taxonomy = self._load()
        self.assertIn("data engineer", taxonomy.lookup)

    def test_nothing_is_deleted_and_switching_back_on_restores_everything(self):
        """The requirement §11.4 exists for."""
        self._row("Prompt Engineer")
        with self._artefact(BaseEntityEntry("role", "Data Engineer", ())):
            with patch.object(settings, "feature_user_taxonomy_enabled", False):
                off = self._load()
            self.assertNotIn("prompt engineer", off.lookup)

            with patch.object(settings, "feature_user_taxonomy_enabled", True):
                back_on = self._load()

        self.assertIn("prompt engineer", back_on.lookup, "the entry must come back")
        surviving = self.db.query(CanonicalEntityTaxonomyEntry).count()
        self.assertEqual(surviving, 1, "no row may be deleted by the switch")

    def test_intents_honour_the_switch_the_same_way(self):
        self.db.add(
            JobIntentTaxonomyEntry(
                owner_id=OWNER,
                phrase="urgent requirement",
                normalized_phrase="urgent requirement",
                polarity=POSITIVE_RECRUITER_JD,
                status="approved",
                confidence_aggregate=0.8,
            )
        )
        self.db.commit()
        artefact = patch.object(
            base_taxonomy,
            "load_base_job_intent_entries",
            _cacheable(lambda: (BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9),)),
        )
        with artefact:
            with patch.object(settings, "feature_user_taxonomy_enabled", False):
                off = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
            with patch.object(settings, "feature_user_taxonomy_enabled", True):
                on = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)

        self.assertEqual([s.phrase for s in off], ["hiring now"])
        self.assertIn("urgent requirement", [s.phrase for s in on])
        self.assertEqual(self.db.query(JobIntentTaxonomyEntry).count(), 1)


class EndpointOffSwitchTests(unittest.TestCase):
    """Hiding the UI is cosmetic unless the routes go too."""

    GUARDED = (
        ("GET", "/settings/skills/pending"),
        ("GET", "/settings/skills/approved"),
        ("POST", "/settings/skills/approve"),
        ("GET", "/settings/skills/embedding-status"),
        ("GET", "/settings/entities/role/pending"),
        ("GET", "/settings/entities/company/approved"),
        ("POST", "/settings/entities/role/approve"),
        ("POST", "/settings/taxonomy/bulk-review/classify"),
        ("GET", "/settings/taxonomy/metrics"),
        ("GET", "/settings/job-intent-learning/pending"),
        ("POST", "/settings/job-intent-learning/approve"),
        ("POST", "/taxonomy/entities/embed"),
    )

    def setUp(self):
        # A schema built from the models, not the ambient dev database, which
        # may be behind on migrations. The routes under test touch the DB only
        # to prove they are *reachable*; what they return is not the point.
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        main.app.dependency_overrides[main.get_db] = self._session
        self.client = TestClient(main.app)

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        main.app.dependency_overrides.pop(main.get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_every_taxonomy_route_is_absent_when_the_feature_is_off(self):
        with patch.object(settings, "feature_user_taxonomy_enabled", False):
            for method, path in self.GUARDED:
                with self.subTest(path=path):
                    response = self.client.request(method, path, json={})
                    self.assertEqual(response.status_code, 404, path)

    def test_they_are_reachable_again_when_it_is_on(self):
        """Otherwise the guard could be permanently on and this would pass."""
        with patch.object(settings, "feature_user_taxonomy_enabled", True):
            response = self.client.get("/settings/entities/role/pending")
        self.assertNotEqual(response.status_code, 404)

    def test_the_guard_does_not_catch_neighbouring_settings_routes(self):
        """`/settings/bootstrap` is how the whole page loads.

        A prefix of `/settings/` rather than the four specific ones would 404
        it and take the Settings screen down instead of hiding one card.
        Asserted against the prefix list itself, because exercising the route
        would drag in the ambient database this test has no business touching.
        """
        for path in (
            "/settings/bootstrap",
            "/settings",
            "/settings/gmail-groups",
            "/settings/visible-filters",
            "/settings/candidate-profile/entries",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    path.startswith(main.USER_TAXONOMY_PATH_PREFIXES),
                    f"{path} must survive the taxonomy guard",
                )

    def test_the_prefix_list_covers_every_taxonomy_route_the_app_declares(self):
        """A route added later that the prefixes miss is a route still editing a
        taxonomy the deployment switched off."""
        declared = {
            route.path
            for route in main.app.routes
            if getattr(route, "path", "").startswith(
                ("/settings/skills", "/settings/entities", "/settings/taxonomy",
                 "/settings/job-intent-learning", "/taxonomy")
            )
        }
        uncovered = {
            path for path in declared
            if not path.startswith(main.USER_TAXONOMY_PATH_PREFIXES)
        }
        self.assertEqual(uncovered, set(), f"not covered by the guard: {uncovered}")


if __name__ == "__main__":
    unittest.main()
