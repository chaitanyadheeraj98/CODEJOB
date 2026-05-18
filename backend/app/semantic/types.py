from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SemanticScoreBreakdown:
    enabled: bool
    keyword_score: float
    semantic_score: float
    keyword_weight: float
    semantic_weight: float
    final_score: float
    source: str
    detail: str

