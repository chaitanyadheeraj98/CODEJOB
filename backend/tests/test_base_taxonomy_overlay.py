"""E2: entity and intent taxonomy resolve as `overlay ?? base`.

A new account inherits the reviewed base artefact on day one and keeps
receiving later improvements to it, because nothing is copied into per-user
rows at signup - the base is read at request time.

Three rules, and the third is the one that is easy to leave out:

1. A user's approved entry for the same normalized key **wins**.
2. Everything else falls through to the base.
3. A user can **tombstone** a base entry, or the base could only ever be added
   to and a user who disagrees with a bundled entry would have no way to
   remove it.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import base_taxonomy, job_intent_learning
from app.job_intent_learning import NEGATIVE_JOB_BOARD, POSITIVE_RECRUITER_JD
from app.base_taxonomy import BaseEntityEntry, BaseJobIntentEntry
from app.config import settings
from app.models import Base, CanonicalEntityTaxonomyEntry, JobIntentTaxonomyEntry
from app.services import role_taxonomy

OWNER = "usr_overlay"


class _OverlayCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        # conftest pins this off for the suite. These tests are *about* the base,
        # so they turn it on for themselves.
        self._flag = patch.object(settings, "feature_base_taxonomy_enabled", True)
        self._flag.start()
        role_taxonomy.clear_role_taxonomy_cache()

    def tearDown(self):
        self._flag.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        role_taxonomy.clear_role_taxonomy_cache()

    def _base(self, *entries: BaseEntityEntry):
        return patch.object(role_taxonomy, "base_entity_entries", lambda _t: entries)

    def _base_intents(self, *entries: BaseJobIntentEntry):
        return patch.object(
            job_intent_learning, "base_job_intent_entries", lambda: entries
        )

    def _row(self, canonical: str, *, status: str = "approved", suppressed: bool = False, aliases: str = "[]"):
        row = CanonicalEntityTaxonomyEntry(
            owner_id=OWNER,
            entity_type="role",
            canonical_name=canonical,
            aliases_json=aliases,
            status=status,
            suppressed=suppressed,
        )
        self.db.add(row)
        self.db.commit()
        return row


class EntityOverlayTests(_OverlayCase):
    def test_a_new_account_inherits_the_base_with_no_rows_of_its_own(self):
        with self._base(BaseEntityEntry("role", "Java Developer", ("java dev",))):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertEqual(taxonomy.lookup.get("java developer"), "Java Developer")
        self.assertEqual(taxonomy.lookup.get("java dev"), "Java Developer")

    def test_the_users_own_entry_wins_over_the_base(self):
        self._row("Java Developer", aliases='["backend engineer"]')
        with self._base(BaseEntityEntry("role", "Java Developer", ("java dev",))):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        # The user's alias resolves; the base's does not, because the base entry
        # for that key was replaced rather than merged.
        self.assertEqual(taxonomy.lookup.get("backend engineer"), "Java Developer")
        self.assertNotIn("java dev", taxonomy.lookup)

    def test_everything_else_falls_through(self):
        self._row("Java Developer")
        with self._base(
            BaseEntityEntry("role", "Java Developer", ()),
            BaseEntityEntry("role", "Data Engineer", ()),
        ):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertEqual(taxonomy.lookup.get("data engineer"), "Data Engineer")

    def test_a_user_can_tombstone_a_base_entry(self):
        """Rule 3. Without it the base can only ever be added to."""
        self._row("Data Engineer", status="dismissed", suppressed=True)
        with self._base(BaseEntityEntry("role", "Data Engineer", ("data eng",))):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertNotIn("data engineer", taxonomy.lookup)
        self.assertNotIn("data eng", taxonomy.lookup, "the aliases must go too")

    def test_dismissing_removes_the_base_entry_as_well_as_the_suggestion(self):
        self._row("Data Engineer", status="dismissed")
        with self._base(BaseEntityEntry("role", "Data Engineer", ())):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertNotIn("data engineer", taxonomy.lookup)

    def test_a_pending_row_decides_nothing(self):
        """Only a decision suppresses the base; an unreviewed suggestion is not
        a decision, and a user should keep the bundled entry until they act."""
        self._row("Data Engineer", status="pending")
        with self._base(BaseEntityEntry("role", "Data Engineer", ())):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertEqual(taxonomy.lookup.get("data engineer"), "Data Engineer")

    def test_the_latest_decision_wins_not_the_oldest(self):
        """The unique constraint is on the raw name, not the normalized one.

        "Java Developer" and "java developer" are two permitted rows collapsing
        to one key, so a user who dismissed a name and later approved it must
        get the approval.
        """
        self._row("Java Developer", status="dismissed", suppressed=True)
        self._row("java developer", status="approved")
        with self._base(BaseEntityEntry("role", "Java Developer", ())):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertIn("java developer", taxonomy.lookup, "the later approval must win")

    def test_another_owner_is_unaffected_by_a_tombstone(self):
        self._row("Data Engineer", status="dismissed", suppressed=True)
        with self._base(BaseEntityEntry("role", "Data Engineer", ())):
            other = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id="usr_somebody_else", entity_type="role"
            )
        self.assertEqual(other.lookup.get("data engineer"), "Data Engineer")


class BaseCacheTests(unittest.TestCase):
    def test_clearing_the_role_cache_clears_the_base_cache(self):
        """Otherwise a deployment serves the previous artefact until restart."""
        base_taxonomy.load_base_entity_entries.cache_clear()
        base_taxonomy.load_base_entity_entries("role")
        self.assertGreater(base_taxonomy.load_base_entity_entries.cache_info().currsize, 0)
        role_taxonomy.clear_role_taxonomy_cache()
        self.assertEqual(base_taxonomy.load_base_entity_entries.cache_info().currsize, 0)

    def test_the_shipped_artefact_actually_loads(self):
        """The overlay is worthless if the base underneath it is empty."""
        base_taxonomy.clear_base_taxonomy_cache()
        roles = base_taxonomy.load_base_entity_entries("role")
        intents = base_taxonomy.load_base_job_intent_entries()
        self.assertGreater(len(roles), 100, "the bundled role artefact should be substantial")
        self.assertGreater(len(intents), 10)

    def test_an_unknown_entity_type_is_empty_rather_than_an_error(self):
        self.assertEqual(base_taxonomy.load_base_entity_entries("not_a_type"), ())


class JobIntentOverlayTests(_OverlayCase):
    def _intent(self, phrase: str, *, polarity: str = POSITIVE_RECRUITER_JD, status: str = "approved"):
        row = JobIntentTaxonomyEntry(
            owner_id=OWNER,
            phrase=phrase,
            normalized_phrase=phrase.lower(),
            polarity=polarity,
            status=status,
            confidence_aggregate=0.8,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def test_a_new_account_inherits_the_base_intents(self):
        with self._base_intents(BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9)):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual([s.phrase for s in signals], ["hiring now"])

    def test_a_dismissed_intent_tombstones_the_base_one(self):
        self._intent("hiring now", status="dismissed")
        with self._base_intents(BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9)):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual(signals, [])

    def test_the_users_own_confidence_wins_for_a_shared_phrase(self):
        self._intent("hiring now")
        with self._base_intents(BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.1)):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual(len(signals), 1)
        self.assertAlmostEqual(signals[0].confidence, 0.8)

    def test_a_users_own_phrase_is_added_to_the_base(self):
        self._intent("urgent requirement")
        with self._base_intents(BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9)):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual(
            sorted(s.phrase for s in signals), ["hiring now", "urgent requirement"]
        )

    def test_polarity_separates_otherwise_identical_phrases(self):
        with self._base_intents(
            BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9),
            BaseJobIntentEntry("hiring now", "hiring now", NEGATIVE_JOB_BOARD, 0.4),
        ):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual(len(signals), 2)


if __name__ == "__main__":
    unittest.main()


class BaseOffSwitchTests(_OverlayCase):
    """`feature_base_taxonomy_enabled` - on in production, off in the suite.

    Reversible by design: nothing is copied into per-user rows, so switching
    the base off hides it and switching it back on restores every entry.

    These patch the *inner* loader rather than the gated helper, so the gate
    itself is what is under test. Patching the helper would replace the gate
    and the flag would appear to do nothing.
    """

    @staticmethod
    def _cacheable(fn):
        """The real loaders are lru_cache-wrapped, and `clear_base_taxonomy_cache`
        calls `cache_clear()` on them. A bare lambda would not have it."""
        fn.cache_clear = lambda: None
        return fn

    def _artefact(self, *entries: BaseEntityEntry):
        return patch.object(
            base_taxonomy, "load_base_entity_entries", self._cacheable(lambda _t: entries)
        )

    def _intent_artefact(self, *entries: BaseJobIntentEntry):
        return patch.object(
            base_taxonomy, "load_base_job_intent_entries", self._cacheable(lambda: entries)
        )

    def test_with_the_base_off_only_the_owners_rows_remain(self):
        self._row("Java Developer")
        with patch.object(settings, "feature_base_taxonomy_enabled", False), self._artefact(
            BaseEntityEntry("role", "Data Engineer", ())
        ):
            taxonomy = role_taxonomy.load_entity_taxonomy(
                self.db, owner_id=OWNER, entity_type="role"
            )
        self.assertIn("java developer", taxonomy.lookup)
        self.assertNotIn("data engineer", taxonomy.lookup)

    def test_switching_it_back_on_restores_the_base(self):
        """The reversibility is the point: no data is deleted either way."""
        with self._artefact(BaseEntityEntry("role", "Data Engineer", ())):
            with patch.object(settings, "feature_base_taxonomy_enabled", False):
                off = role_taxonomy.load_entity_taxonomy(
                    self.db, owner_id=OWNER, entity_type="role"
                )
            self.assertNotIn("data engineer", off.lookup)

            role_taxonomy.clear_role_taxonomy_cache()
            on = role_taxonomy.load_entity_taxonomy(self.db, owner_id=OWNER, entity_type="role")
        self.assertIn("data engineer", on.lookup)

    def test_intents_honour_the_same_switch(self):
        with patch.object(settings, "feature_base_taxonomy_enabled", False), self._intent_artefact(
            BaseJobIntentEntry("hiring now", "hiring now", POSITIVE_RECRUITER_JD, 0.9)
        ):
            signals = job_intent_learning.approved_learning_signals_for_owner(self.db, OWNER)
        self.assertEqual(signals, [])

    def test_the_gate_is_read_per_call_not_baked_into_the_cache(self):
        """The flag is checked outside the lru_cache for exactly this reason."""
        with patch.object(settings, "feature_base_taxonomy_enabled", False):
            self.assertEqual(base_taxonomy.base_entity_entries("role"), ())
        with patch.object(settings, "feature_base_taxonomy_enabled", True):
            self.assertGreater(len(base_taxonomy.base_entity_entries("role")), 0)
