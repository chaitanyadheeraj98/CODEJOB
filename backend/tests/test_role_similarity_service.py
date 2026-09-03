import json
import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import RoleSimilarityCheck
from app.services import role_similarity_service

OWNER_ID = "owner-1"


class RoleSimilarityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        # Pin the blend weights so hybrid-path expectations are deterministic
        # regardless of what other test modules may have mutated at runtime.
        self._original_keyword_weight = settings.semantic_keyword_weight
        self._original_semantic_weight = settings.semantic_similarity_weight
        settings.semantic_keyword_weight = 0.6
        settings.semantic_similarity_weight = 0.4

    def tearDown(self) -> None:
        settings.semantic_keyword_weight = self._original_keyword_weight
        settings.semantic_similarity_weight = self._original_semantic_weight
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _compute(self, db: Session, **overrides: object) -> role_similarity_service.RoleSimilarityResult:
        defaults = dict(
            owner_id=OWNER_ID,
            left_type="candidate_role",
            left_id=1,
            left_role_text="",
            left_skills_text="",
            right_type="jd_role",
            right_id=2,
            right_role_text="",
            right_skills_text="",
        )
        defaults.update(overrides)
        return role_similarity_service.compute_role_similarity(db, **defaults)

    # -- fallback path (no extractable skills, no cached embeddings) ------------

    def test_fallback_same_title_after_normalization(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(
                db, left_role_text="Zzqx Wobblefloop Coordinator", right_role_text="Zzqx Wobblefloop Coordinator"
            )
            self.assertEqual(result.method, "fallback")
            self.assertEqual(result.tier, "same")
            self.assertEqual(result.final_score, 1.0)

    def test_fallback_different_titles(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(db, left_role_text="Zzqx Wobblefloop Coordinator", right_role_text="Yblorx Snerfle Handler")
            self.assertEqual(result.method, "fallback")
            self.assertEqual(result.tier, "different")
            self.assertEqual(result.final_score, 0.0)

    def test_fallback_tier_is_never_related(self) -> None:
        with Session(self.engine) as db:
            for left, right in (
                ("Zzqx Coordinator", "Zzqx Coordinator"),
                ("Zzqx Coordinator", "Totally Different Yblorx"),
            ):
                result = self._compute(db, left_role_text=left, right_role_text=right)
                self.assertIn(result.tier, {"same", "different"})

    def test_fallback_strips_seniority_words(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(db, left_role_text="Senior Wobblefloop Coordinator", right_role_text="Wobblefloop Coordinator")
            self.assertEqual(result.method, "fallback")
            self.assertEqual(result.tier, "same")

    def test_fallback_applies_synonym_mapping(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(db, left_role_text="Senior Wobblefloop Programmer", right_role_text="Wobblefloop Developer")
            self.assertEqual(result.method, "fallback")
            self.assertEqual(result.tier, "same")

    def test_fallback_blank_titles_are_different(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(db, left_role_text="", right_role_text="")
            self.assertEqual(result.method, "fallback")
            self.assertEqual(result.tier, "different")
            self.assertEqual(result.final_score, 0.0)

    def test_fallback_writes_role_similarity_check_row_with_null_component_scores(self) -> None:
        with Session(self.engine) as db:
            self._compute(
                db,
                left_type="candidate_role",
                left_id=11,
                left_role_text="Zzqx Coordinator",
                right_type="jd_role",
                right_id=22,
                right_role_text="Zzqx Coordinator",
            )
            db.commit()
            rows = db.query(RoleSimilarityCheck).all()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row.owner_id, OWNER_ID)
            self.assertEqual(row.left_record_type, "candidate_role")
            self.assertEqual(row.left_record_id, 11)
            self.assertEqual(row.right_record_type, "jd_role")
            self.assertEqual(row.right_record_id, 22)
            self.assertEqual(row.method, "fallback")
            self.assertEqual(row.tier, "same")
            self.assertEqual(row.final_score, 1.0)
            self.assertIsNone(row.skill_overlap_score)
            self.assertIsNone(row.embedding_score)

    # -- hybrid path: skill-overlap only (no embeddings) -------------------------

    def test_hybrid_full_skill_overlap_is_same_tier(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python, AWS, Docker",
                right_role_text="Backend Developer",
                right_skills_text="Python, AWS, Docker",
            )
            self.assertEqual(result.method, "hybrid")
            self.assertEqual(result.tier, "same")
            self.assertAlmostEqual(result.final_score, 1.0, places=6)

    def test_hybrid_no_skill_overlap_is_different_tier(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python",
                right_role_text="Frontend Developer",
                right_skills_text="React",
            )
            self.assertEqual(result.method, "hybrid")
            self.assertEqual(result.tier, "different")
            self.assertAlmostEqual(result.final_score, 0.0, places=6)

    def test_hybrid_partial_skill_overlap_is_related_tier(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python, AWS, Docker, React",
                right_role_text="Backend Developer",
                right_skills_text="Python, AWS, Kafka, SQL",
            )
            self.assertEqual(result.method, "hybrid")
            self.assertEqual(result.tier, "related")
            self.assertGreaterEqual(result.final_score, 0.25)
            self.assertLess(result.final_score, 0.6)

    def test_hybrid_writes_skill_overlap_score_on_row(self) -> None:
        with Session(self.engine) as db:
            self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python, AWS",
                right_role_text="Backend Developer",
                right_skills_text="Python, AWS",
            )
            db.commit()
            row = db.query(RoleSimilarityCheck).one()
            self.assertEqual(row.method, "hybrid")
            self.assertAlmostEqual(row.skill_overlap_score, 1.0, places=6)
            self.assertEqual(row.embedding_score, 0.0)

    # -- hybrid path: embeddings ---------------------------------------------------

    def test_hybrid_uses_cached_embeddings_when_no_skills_extractable(self) -> None:
        with Session(self.engine) as db:
            left_vec = json.dumps([1.0, 0.0, 0.0])
            right_vec = json.dumps([1.0, 0.0, 0.0])
            result = self._compute(
                db,
                left_role_text="Zzqx Wobblefloop",
                right_role_text="Yblorx Snerfle",
                left_embedding_cached=left_vec,
                right_embedding_cached=right_vec,
            )
            self.assertEqual(result.method, "hybrid")
            # keyword_score is 0 (no skills), semantic_score maps cosine=1.0 -> 1.0,
            # blended with default weights (0.6 keyword / 0.4 semantic) => 0.4.
            self.assertAlmostEqual(result.final_score, 0.4, places=6)
            self.assertEqual(result.tier, "related")

    def test_hybrid_combines_skill_overlap_and_embeddings_to_reach_same_tier(self) -> None:
        with Session(self.engine) as db:
            left_vec = json.dumps([1.0, 0.0])
            right_vec = json.dumps([1.0, 0.0])
            result = self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python, AWS",
                right_role_text="Backend Developer",
                right_skills_text="Python, AWS",
                left_embedding_cached=left_vec,
                right_embedding_cached=right_vec,
            )
            self.assertEqual(result.method, "hybrid")
            self.assertEqual(result.tier, "same")
            self.assertAlmostEqual(result.final_score, 1.0, places=6)
            db.commit()
            row = db.query(RoleSimilarityCheck).one()
            self.assertAlmostEqual(row.embedding_score, 1.0, places=6)

    def test_malformed_embedding_json_is_treated_as_no_embedding(self) -> None:
        with Session(self.engine) as db:
            result = self._compute(
                db,
                left_role_text="Backend Engineer",
                left_skills_text="Python, AWS",
                right_role_text="Backend Developer",
                right_skills_text="Python, AWS",
                left_embedding_cached="not-json",
                right_embedding_cached=json.dumps([1.0, 0.0]),
            )
            self.assertEqual(result.method, "hybrid")
            db.commit()
            row = db.query(RoleSimilarityCheck).one()
            self.assertEqual(row.embedding_score, 0.0)

    def test_every_call_writes_exactly_one_row(self) -> None:
        with Session(self.engine) as db:
            self._compute(db, left_role_text="A", right_role_text="A")
            self._compute(db, left_role_text="B", right_role_text="C")
            db.commit()
            self.assertEqual(db.query(RoleSimilarityCheck).count(), 2)


if __name__ == "__main__":
    unittest.main()
