"""Entity resolution: exact, then alias, then semantic, then unresolved.

The order matters more than any single branch. A resolver that reaches for a
nearest neighbour before checking the vocabulary will merge two firms that
merely sound alike, and the merge then propagates into every cluster either
entity touches.
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
from app.models import CanonicalEntityTaxonomyEntry
from app.services import entity_resolution_service as resolution
from app.services.entity_embedding_job import embed_pending_entities
from app.services.role_taxonomy import clear_role_taxonomy_cache

OWNER = "owner-under-test"


def unit_vector(index: int, size: int = 8) -> list[float]:
    return [1.0 if position == index else 0.0 for position in range(size)]


class EntityResolutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        clear_role_taxonomy_cache()
        self.addCleanup(clear_role_taxonomy_cache)
        self.addCleanup(self.engine.dispose)

    def entry(
        self,
        db: Session,
        canonical: str,
        *,
        entity_type: str = "company",
        aliases: list[str] | None = None,
        embedding: list[float] | None = None,
        occurrences: int = 5,
        status: str = "approved",
        embedding_status: str = "pending",
    ) -> CanonicalEntityTaxonomyEntry:
        row = CanonicalEntityTaxonomyEntry(
            owner_id=OWNER,
            entity_type=entity_type,
            canonical_name=canonical,
            aliases_json=json.dumps(aliases or []),
            occurrence_count=occurrences,
            status=status,
            embedding_status="done" if embedding else embedding_status,
            embedding_json=json.dumps(embedding) if embedding else None,
        )
        db.add(row)
        db.flush()
        return row


class ResolutionOrderTests(EntityResolutionTestCase):
    def test_an_exact_canonical_name_resolves_exactly(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="cognizant")

        self.assertEqual(resolved.match, resolution.MATCH_EXACT)
        self.assertEqual(resolved.canonical, "Cognizant")
        self.assertEqual(resolved.score, 1.0)

    def test_an_alias_resolves_to_its_canonical_name(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", aliases=["Cognizant Technology Solutions", "CTS"])
            db.commit()
            resolved = resolution.resolve_entity(
                db, owner_id=OWNER, entity_type="company", surface="Cognizant Technology Solutions"
            )

        self.assertEqual(resolved.match, resolution.MATCH_ALIAS)
        self.assertEqual(resolved.canonical, "Cognizant")

    # Exact must beat alias, or an entry can be shadowed by another entry's
    # alias for its own name.
    def test_exact_wins_over_another_entrys_alias(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Horizon Softech Inc", aliases=["Horizon"])
            horizon = self.entry(db, "Horizon")
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="Horizon")

        self.assertEqual(resolved.match, resolution.MATCH_EXACT)
        self.assertEqual(resolved.entry_id, horizon.id)

    def test_the_longest_alias_wins(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Java Developer", entity_type="role", aliases=["java developer"])
            self.entry(db, "Java Full Stack Developer", entity_type="role", aliases=["java full stack developer"])
            db.commit()
            resolved = resolution.resolve_entity(
                db, owner_id=OWNER, entity_type="role", surface="Senior Java Full Stack Developer"
            )

        self.assertEqual(resolved.canonical, "Java Full Stack Developer")

    def test_an_unknown_surface_is_unresolved_and_keeps_its_own_text(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="Initech")

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)
        self.assertEqual(resolved.canonical, "Initech")
        self.assertIsNone(resolved.entry_id)

    def test_another_owners_entries_are_never_resolved_against(self) -> None:
        with Session(self.engine) as db:
            db.add(CanonicalEntityTaxonomyEntry(
                owner_id="someone-else", entity_type="company", canonical_name="Cognizant", aliases_json="[]"
            ))
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="Cognizant")

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)

    def test_a_pending_entry_is_not_vocabulary(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", status="pending")
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="Cognizant")

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)

    def test_a_blank_surface_resolves_to_nothing_rather_than_the_first_entry(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="   ")

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)


class SemanticBranchTests(EntityResolutionTestCase):
    def resolve(self, db: Session, surface: str, vector: list[float], min_semantic: float = 0.90):
        with patch.object(settings, "semantic_embedding_provider", "sbert"):
            with patch.object(resolution, "generate_embeddings", return_value=([vector], "sbert")):
                return resolution.resolve_entity(
                    db, owner_id=OWNER, entity_type="company", surface=surface, min_semantic=min_semantic
                )

    def test_a_close_embedding_resolves_semantically(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", embedding=unit_vector(0))
            db.commit()
            resolved = self.resolve(db, "Cognizent", unit_vector(0))

        self.assertEqual(resolved.match, resolution.MATCH_SEMANTIC)
        self.assertEqual(resolved.canonical, "Cognizant")

    # Below the bar is unresolved, never "the nearest entry". A nearest
    # neighbour is always available - that is exactly the problem.
    def test_below_the_bar_is_unresolved_not_the_nearest_entry(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", embedding=unit_vector(0))
            db.commit()
            resolved = self.resolve(db, "Initech", unit_vector(1))

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)
        self.assertEqual(resolved.canonical, "Initech")

    # Cosine between two hash vectors is noise, and noise clears a 0.90 bar
    # eventually. The branch is switched off rather than fed vectors that
    # cannot mean anything.
    def test_a_hash_fallback_never_produces_a_semantic_match(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", embedding=unit_vector(0))
            db.commit()
            with patch.object(settings, "semantic_embedding_provider", "sbert"):
                with patch.object(resolution, "generate_embeddings", return_value=([unit_vector(0)], "hash")):
                    resolved = resolution.resolve_entity(
                        db, owner_id=OWNER, entity_type="company", surface="Cognizent"
                    )

        self.assertEqual(resolved.match, resolution.MATCH_UNRESOLVED)

    def test_an_entry_with_no_embedding_never_breaks_resolution(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant", aliases=["CTS"])
            db.commit()
            resolved = resolution.resolve_entity(db, owner_id=OWNER, entity_type="company", surface="CTS")

        self.assertEqual(resolved.match, resolution.MATCH_ALIAS)


class BatchingTests(EntityResolutionTestCase):
    def test_resolve_many_issues_one_query_per_entity_type(self) -> None:
        # Counted, not timed: 603,801 candidate pairs makes a per-pair query
        # impossible, so the batching is a correctness requirement.
        with Session(self.engine) as db:
            for name in ("Cognizant", "Infosys", "Wipro"):
                self.entry(db, name)
            db.commit()

            statements: list[str] = []

            @event.listens_for(self.engine, "before_cursor_execute")
            def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)

            try:
                resolved = resolution.resolve_many(
                    db,
                    owner_id=OWNER,
                    entity_type="company",
                    surfaces=["Cognizant", "Infosys", "Wipro", "Initech", "Cognizant"],
                )
            finally:
                event.remove(self.engine, "before_cursor_execute", record)

        self.assertEqual(len(resolved), 4)
        self.assertLessEqual(len(statements), 2)

    def test_resolve_many_returns_one_entry_per_distinct_surface(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            resolved = resolution.resolve_many(
                db, owner_id=OWNER, entity_type="company", surfaces=["Cognizant", "Cognizant"]
            )

        self.assertEqual(list(resolved), ["Cognizant"])


class AliasSuggestionTests(EntityResolutionTestCase):
    def test_corporate_suffixes_are_stripped_for_comparison_only(self) -> None:
        self.assertEqual(
            resolution.strip_corporate_suffixes("Horizon Softech Inc"),
            resolution.strip_corporate_suffixes("Horizon Softech LLC"),
        )

    def test_entries_differing_only_by_legal_form_are_suggested(self) -> None:
        with Session(self.engine) as db:
            keep = self.entry(db, "Horizon Softech Inc", occurrences=40)
            drop = self.entry(db, "Horizon Softech LLC", occurrences=3)
            db.commit()
            suggestions = resolution.suggest_aliases(db, owner_id=OWNER, entity_type="company")

        self.assertEqual(suggestions, [(keep.id, drop.id, 1.0)])

    def test_the_more_frequent_entry_is_the_one_kept(self) -> None:
        with Session(self.engine) as db:
            small = self.entry(db, "Horizon Softech Inc", occurrences=2)
            large = self.entry(db, "Horizon Softech LLC", occurrences=90)
            db.commit()
            suggestions = resolution.suggest_aliases(db, owner_id=OWNER, entity_type="company")

        self.assertEqual(suggestions[0][0], large.id)
        self.assertEqual(suggestions[0][1], small.id)

    def test_unrelated_entries_are_not_suggested(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            self.entry(db, "Initech")
            db.commit()
            self.assertEqual(resolution.suggest_aliases(db, owner_id=OWNER, entity_type="company"), [])

    def test_an_existing_alias_is_not_re_suggested(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Horizon Softech Inc", aliases=["Horizon Softech LLC"], occurrences=40)
            self.entry(db, "Horizon Softech LLC", occurrences=3)
            db.commit()
            self.assertEqual(resolution.suggest_aliases(db, owner_id=OWNER, entity_type="company"), [])

    # A wrong merge is invisible and permanent, so the suggester is a reader.
    def test_suggest_aliases_writes_nothing(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Horizon Softech Inc", occurrences=40)
            self.entry(db, "Horizon Softech LLC", occurrences=3)
            db.commit()
            before = [
                (row.id, row.canonical_name, row.aliases_json, row.status, row.occurrence_count)
                for row in db.query(CanonicalEntityTaxonomyEntry).order_by(CanonicalEntityTaxonomyEntry.id).all()
            ]
            resolution.suggest_aliases(db, owner_id=OWNER, entity_type="company")
            db.expire_all()
            after = [
                (row.id, row.canonical_name, row.aliases_json, row.status, row.occurrence_count)
                for row in db.query(CanonicalEntityTaxonomyEntry).order_by(CanonicalEntityTaxonomyEntry.id).all()
            ]

        self.assertEqual(before, after)


class AliasMergeTests(EntityResolutionTestCase):
    def test_a_merge_folds_the_dropped_name_in_and_dismisses_it(self) -> None:
        with Session(self.engine) as db:
            keep = self.entry(db, "Horizon Softech Inc", occurrences=40)
            drop = self.entry(db, "Horizon Softech LLC", aliases=["Horizon Soft"], occurrences=3)
            db.commit()
            resolution.apply_alias_merge(db, owner_id=OWNER, entity_type="company", keep_id=keep.id, alias_id=drop.id)
            db.expire_all()
            merged = db.get(CanonicalEntityTaxonomyEntry, keep.id)
            dropped = db.get(CanonicalEntityTaxonomyEntry, drop.id)

            self.assertIn("Horizon Softech LLC", json.loads(merged.aliases_json))
            self.assertIn("Horizon Soft", json.loads(merged.aliases_json))
            self.assertEqual(merged.occurrence_count, 43)
            self.assertEqual(dropped.status, "dismissed")

    def test_the_merged_surface_now_resolves_to_the_surviving_entry(self) -> None:
        with Session(self.engine) as db:
            keep = self.entry(db, "Horizon Softech Inc", occurrences=40)
            drop = self.entry(db, "Horizon Softech LLC", occurrences=3)
            db.commit()
            # The vocabulary is cached per (owner, type); a merge that does not
            # clear it leaves the app matching the pre-merge names.
            resolution.build_index(db, owner_id=OWNER, entity_type="company")
            resolution.apply_alias_merge(db, owner_id=OWNER, entity_type="company", keep_id=keep.id, alias_id=drop.id)
            resolved = resolution.resolve_entity(
                db, owner_id=OWNER, entity_type="company", surface="Horizon Softech LLC"
            )

        self.assertEqual(resolved.canonical, "Horizon Softech Inc")

    def test_merging_an_entry_into_itself_is_refused(self) -> None:
        with Session(self.engine) as db:
            keep = self.entry(db, "Cognizant")
            db.commit()
            with self.assertRaises(ValueError):
                resolution.apply_alias_merge(
                    db, owner_id=OWNER, entity_type="company", keep_id=keep.id, alias_id=keep.id
                )

    def test_merging_another_owners_entry_is_refused(self) -> None:
        with Session(self.engine) as db:
            keep = self.entry(db, "Cognizant")
            other = CanonicalEntityTaxonomyEntry(
                owner_id="someone-else", entity_type="company", canonical_name="Initech", aliases_json="[]"
            )
            db.add(other)
            db.commit()
            with self.assertRaises(LookupError):
                resolution.apply_alias_merge(
                    db, owner_id=OWNER, entity_type="company", keep_id=keep.id, alias_id=other.id
                )


class EmbeddingJobTests(EntityResolutionTestCase):
    def run_job(self, db: Session, vectors: list[list[float]], provider: str = "sbert", **kwargs):
        with patch.object(settings, "semantic_embedding_provider", "sbert"):
            with patch(
                "app.services.entity_embedding_job.generate_embeddings",
                return_value=(vectors, provider),
            ):
                return embed_pending_entities(db, owner_id=OWNER, entity_type="company", **kwargs)

    def test_pending_entries_are_embedded_highest_occurrence_first(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Rare Vendor", occurrences=1)
            self.entry(db, "Common Vendor", occurrences=900)
            db.commit()
            result = self.run_job(db, [unit_vector(0)], batch_size=1)
            db.expire_all()
            common = db.query(CanonicalEntityTaxonomyEntry).filter_by(canonical_name="Common Vendor").one()
            rare = db.query(CanonicalEntityTaxonomyEntry).filter_by(canonical_name="Rare Vendor").one()

        self.assertEqual(result["embedded"], 1)
        self.assertEqual(common.embedding_status, "done")
        self.assertEqual(rare.embedding_status, "pending")

    def test_a_second_run_over_an_embedded_set_embeds_zero(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            self.run_job(db, [unit_vector(0)])
            second = self.run_job(db, [unit_vector(0)])

        self.assertEqual(second["embedded"], 0)
        self.assertEqual(second["remaining"], 0)

    # Storing a hash vector would put noise into a 0.90-bar resolver. Rows are
    # left pending so the backfill resumes once a real provider exists.
    def test_a_hash_fallback_writes_nothing_and_leaves_rows_pending(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            result = self.run_job(db, [unit_vector(0)], provider="hash")
            db.expire_all()
            row = db.query(CanonicalEntityTaxonomyEntry).one()

        self.assertEqual(result["embedded"], 0)
        self.assertIn("hash", str(result["reason"]))
        self.assertEqual(row.embedding_status, "pending")
        self.assertIsNone(row.embedding_json)

    def test_no_configured_provider_leaves_rows_pending_rather_than_failed(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            with patch.object(settings, "semantic_embedding_provider", "hash"):
                result = embed_pending_entities(db, owner_id=OWNER, entity_type="company")
            db.expire_all()
            row = db.query(CanonicalEntityTaxonomyEntry).one()

        self.assertEqual(result["embedded"], 0)
        self.assertEqual(row.embedding_status, "pending")

    def test_an_empty_vector_is_marked_failed_not_left_pending_forever(self) -> None:
        with Session(self.engine) as db:
            self.entry(db, "Cognizant")
            db.commit()
            result = self.run_job(db, [[]])
            db.expire_all()
            row = db.query(CanonicalEntityTaxonomyEntry).one()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(row.embedding_status, "failed")

    def test_an_unsupported_entity_type_is_refused(self) -> None:
        with Session(self.engine) as db:
            result = embed_pending_entities(db, owner_id=OWNER, entity_type="end_client")

        self.assertIn("unsupported", str(result["reason"]))


if __name__ == "__main__":
    unittest.main()
