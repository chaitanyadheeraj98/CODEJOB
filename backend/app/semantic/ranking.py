from __future__ import annotations

from app.config import settings
from app.semantic.similarity import cosine_similarity
from app.semantic.types import SemanticScoreBreakdown


def clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))


def blend_scores(
    *,
    keyword_score: float,
    semantic_similarity: float,
    semantic_enabled: bool,
) -> SemanticScoreBreakdown:
    keyword_score = clamp01(keyword_score)
    semantic_score = clamp01((semantic_similarity + 1.0) / 2.0)
    if not semantic_enabled:
        return SemanticScoreBreakdown(
            enabled=False,
            keyword_score=keyword_score,
            semantic_score=0.0,
            keyword_weight=1.0,
            semantic_weight=0.0,
            final_score=keyword_score,
            source="v1_rules_plus_ai",
            detail=f"Keyword score only ({keyword_score:.2f}); semantic disabled",
        )

    keyword_weight = clamp01(float(settings.semantic_keyword_weight))
    semantic_weight = clamp01(float(settings.semantic_similarity_weight))
    total = keyword_weight + semantic_weight
    if total <= 0:
        keyword_weight = 0.6
        semantic_weight = 0.4
        total = 1.0
    keyword_weight /= total
    semantic_weight /= total
    final = clamp01((keyword_score * keyword_weight) + (semantic_score * semantic_weight))
    return SemanticScoreBreakdown(
        enabled=True,
        keyword_score=keyword_score,
        semantic_score=semantic_score,
        keyword_weight=keyword_weight,
        semantic_weight=semantic_weight,
        final_score=final,
        source="v2_rules_plus_semantic",
        detail=(
            f"Blended keyword {keyword_score:.2f} (w={keyword_weight:.2f}) + "
            f"semantic {semantic_score:.2f} (w={semantic_weight:.2f}) => {final:.2f}"
        ),
    )


def semantic_similarity(email_embedding: list[float], resume_embedding: list[float]) -> float:
    return cosine_similarity(email_embedding, resume_embedding)

