import unittest

from app.semantic.ranking import blend_scores, semantic_similarity


class SemanticRankingTests(unittest.TestCase):
    def test_semantic_similarity_identical_vectors(self) -> None:
        score = semantic_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(score, 1.0, places=6)

    def test_blend_scores_disabled_uses_keyword_only(self) -> None:
        blended = blend_scores(
            keyword_score=0.72,
            semantic_similarity=0.99,
            semantic_enabled=False,
        )
        self.assertFalse(blended.enabled)
        self.assertAlmostEqual(blended.final_score, 0.72, places=6)
        self.assertEqual(blended.source, "v1_rules_plus_ai")

    def test_blend_scores_enabled_combines_components(self) -> None:
        blended = blend_scores(
            keyword_score=0.60,
            semantic_similarity=1.0,
            semantic_enabled=True,
        )
        self.assertTrue(blended.enabled)
        self.assertGreater(blended.final_score, 0.60)
        self.assertLessEqual(blended.final_score, 1.0)
        self.assertEqual(blended.source, "v2_rules_plus_semantic")


if __name__ == "__main__":
    unittest.main()

