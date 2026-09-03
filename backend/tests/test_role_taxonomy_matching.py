"""The lexical role matcher: approved vocabulary, no AI required.

This is the piece that makes curation pay off. `feature_ai_enabled` is off on this
deployment and skill extraction still works, because the skill taxonomy is a
word-bounded alias matcher over approved entries rather than a model call. Roles
now work the same way, so dismissing bad candidates measurably improves what the
parser can do with AI unavailable.

`test_matches_with_no_ai_or_embeddings_available` is the point of the whole
design: nothing here touches a model, a network or an API key.
"""

import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry
from app.services.role_provenance import RoleSource, assign_role
from app.services.role_taxonomy import (
    clear_role_taxonomy_cache,
    load_role_taxonomy,
    match_role_from_taxonomy,
    role_matcher_for,
)

OWNER = "owner-under-test"


class RoleTaxonomyMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def _entry(
        self,
        db: Session,
        canonical: str,
        *,
        aliases: list[str] | None = None,
        status: str = "approved",
        owner_id: str = OWNER,
    ) -> None:
        db.add(
            CanonicalEntityTaxonomyEntry(
                owner_id=owner_id,
                entity_type="role",
                canonical_name=canonical,
                aliases_json=json.dumps(aliases or []),
                occurrence_count=5,
                status=status,
            )
        )
        db.flush()

    def test_matches_an_approved_role_named_in_the_subject(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Java Developer")
            db.commit()
            match = match_role_from_taxonomy(
                db, owner_id=OWNER, text="Urgent Hiring for Java Developer - Austin"
            )
        self.assertIsNotNone(match)
        self.assertEqual(match.canonical_name, "Java Developer")

    def test_longest_alias_wins(self) -> None:
        """Specificity beats generality when both are approved."""
        with Session(self.engine) as db:
            self._entry(db, "Java Developer")
            self._entry(db, "Java Full Stack Developer")
            db.commit()
            match = match_role_from_taxonomy(
                db, owner_id=OWNER, text="Hiring a Java Full Stack Developer now"
            )
        self.assertEqual(match.canonical_name, "Java Full Stack Developer")

    def test_aliases_fold_variants_onto_the_canonical_name(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Java Developer", aliases=["Java Programmer", "Java Engineer"])
            db.commit()
            match = match_role_from_taxonomy(db, owner_id=OWNER, text="Senior Java Engineer needed")
        self.assertEqual(match.canonical_name, "Java Developer")

    def test_only_approved_entries_are_loaded(self) -> None:
        """Pending is a proposal; dismissed is a rejection. Neither is vocabulary."""
        with Session(self.engine) as db:
            self._entry(db, "Pending Role Title", status="pending")
            self._entry(db, "Dismissed Role Title", status="dismissed")
            db.commit()
            taxonomy = load_role_taxonomy(db, owner_id=OWNER)
            pending_match = match_role_from_taxonomy(db, owner_id=OWNER, text="Pending Role Title")
            dismissed_match = match_role_from_taxonomy(db, owner_id=OWNER, text="Dismissed Role Title")

        self.assertEqual(taxonomy.size, 0)
        self.assertIsNone(pending_match)
        self.assertIsNone(dismissed_match)

    def test_dismissing_junk_keeps_it_out_of_the_parser(self) -> None:
        """The user's stated reason for manual review, asserted directly."""
        junk = "C2C Requirements Kindly Share Resumes"
        with Session(self.engine) as db:
            self._entry(db, junk, status="dismissed")
            self._entry(db, "Java Developer")
            db.commit()
            match = match_role_from_taxonomy(
                db, owner_id=OWNER, text=f"{junk} for Java Developer roles"
            )
        self.assertEqual(match.canonical_name, "Java Developer")

    def test_matching_is_word_bounded(self) -> None:
        """An approved role must not match inside a larger word."""
        with Session(self.engine) as db:
            self._entry(db, "Java Developer")
            db.commit()
            match = match_role_from_taxonomy(db, owner_id=OWNER, text="JavaDeveloperish nonsense")
        self.assertIsNone(match)

    def test_single_word_entries_are_ignored(self) -> None:
        """"Java" is a technology, not a job title - it would match everything."""
        with Session(self.engine) as db:
            self._entry(db, "Java")
            db.commit()
            taxonomy = load_role_taxonomy(db, owner_id=OWNER)
        self.assertEqual(taxonomy.size, 0)

    def test_is_owner_scoped(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Java Developer", owner_id="someone-else")
            db.commit()
            match = match_role_from_taxonomy(db, owner_id=OWNER, text="Java Developer wanted")
        self.assertIsNone(match)

    def test_body_is_only_scanned_near_the_top(self) -> None:
        """A role buried in a benefits paragraph is not this email's role."""
        with Session(self.engine) as db:
            self._entry(db, "Data Engineer")
            db.commit()
            buried = ("unrelated filler " * 200) + " we also hire Data Engineer sometimes"
            deep = match_role_from_taxonomy(db, owner_id=OWNER, text="Urgent hiring", body=buried)
            near = match_role_from_taxonomy(
                db, owner_id=OWNER, text="Urgent hiring", body="Data Engineer role in Austin."
            )
        self.assertIsNone(deep)
        self.assertIsNotNone(near)

    def test_empty_vocabulary_returns_none(self) -> None:
        """Before anything is approved the ladder must fall through, not error."""
        with Session(self.engine) as db:
            match = match_role_from_taxonomy(db, owner_id=OWNER, text="Java Developer")
        self.assertIsNone(match)

    def test_cache_is_invalidated_when_the_vocabulary_changes(self) -> None:
        with Session(self.engine) as db:
            self.assertIsNone(match_role_from_taxonomy(db, owner_id=OWNER, text="Java Developer"))
            self._entry(db, "Java Developer")
            db.commit()
            # Still cached as empty until the approve/dismiss handler clears it.
            self.assertIsNone(match_role_from_taxonomy(db, owner_id=OWNER, text="Java Developer"))
            clear_role_taxonomy_cache()
            self.assertIsNotNone(match_role_from_taxonomy(db, owner_id=OWNER, text="Java Developer"))

    def test_matches_with_no_ai_or_embeddings_available(self) -> None:
        """The whole point: curation works when the models do not.

        Nothing in this path calls a model, opens a socket or reads an API key.
        """
        with Session(self.engine) as db:
            self._entry(db, "QA Automation Engineer")
            db.commit()
            result = assign_role(
                extracted=None,
                subject="Immediate requirement !!!",
                body="Looking for a QA Automation Engineer with Selenium.",
                matcher=role_matcher_for(db, OWNER),
            )
        self.assertEqual(result.role, "QA Automation Engineer")
        self.assertEqual(result.role_canonical, "QA Automation Engineer")
        self.assertEqual(result.role_source, RoleSource.TAXONOMY_MATCHED)

    def test_unmatched_still_falls_through_to_a_labelled_subject(self) -> None:
        """The confidence gate: a lexical miss is a real miss, never a near-match."""
        with Session(self.engine) as db:
            self._entry(db, "Java Developer")
            db.commit()
            result = assign_role(
                extracted=None,
                subject="Salesforce Administrator opening",
                body="Apex, Lightning, Flows.",
                matcher=role_matcher_for(db, OWNER),
            )
        self.assertEqual(result.role_source, RoleSource.SUBJECT_FALLBACK)
        self.assertIsNone(result.role_canonical)

    def test_extracted_roles_gain_a_canonical_without_losing_specificity(self) -> None:
        with Session(self.engine) as db:
            self._entry(db, "Java Developer", aliases=["Java Full Stack Developer"])
            db.commit()
            result = assign_role(
                extracted="Java Full Stack Developer with React and Kafka",
                subject="Hiring",
                body="",
                matcher=role_matcher_for(db, OWNER),
            )
        # `role` keeps the specific title for draft copy; `role_canonical` collapses
        # it for aggregation.
        self.assertEqual(result.role, "Java Full Stack Developer with React and Kafka")
        self.assertEqual(result.role_canonical, "Java Developer")
        self.assertEqual(result.role_source, RoleSource.EXTRACTED)


if __name__ == "__main__":
    unittest.main()
