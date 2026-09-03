from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models import RoleSimilarityCheck, utc_now
from app.semantic.ranking import blend_scores
from app.semantic.similarity import cosine_similarity
from app.skill_taxonomy import extract_taxonomy_skills

_SENIORITY_WORDS = {"senior", "sr", "jr", "junior", "lead", "principal", "i", "ii", "iii", "iv"}
_SYNONYMS = {"engineer": "developer", "programmer": "developer", "analyst": "specialist"}


@dataclass(frozen=True)
class RoleSimilarityResult:
    tier: Literal["same", "related", "different"]
    final_score: float
    method: Literal["hybrid", "fallback"]


def _normalized_title(text: str) -> str:
    words = [word for word in re.sub(r"[^a-z0-9\s]", " ", text.lower()).split() if word not in _SENIORITY_WORDS]
    return " ".join(_SYNONYMS.get(word, word) for word in words)


def _vector(raw: str | None) -> list[float] | None:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return [float(item) for item in value] if isinstance(value, list) and value else None


def compute_role_similarity(
    db: Session,
    *,
    owner_id: str,
    left_type: str,
    left_id: int,
    left_role_text: str,
    left_skills_text: str,
    right_type: str,
    right_id: int,
    right_role_text: str,
    right_skills_text: str,
    left_embedding_cached: str | None = None,
    right_embedding_cached: str | None = None,
) -> RoleSimilarityResult:
    left_skills = {skill.id for skill in extract_taxonomy_skills(f"{left_role_text} {left_skills_text}")}
    right_skills = {skill.id for skill in extract_taxonomy_skills(f"{right_role_text} {right_skills_text}")}
    left_vector, right_vector = _vector(left_embedding_cached), _vector(right_embedding_cached)
    jaccard: float | None = None
    cosine: float | None = None
    if not (left_skills or right_skills) and not (left_vector and right_vector):
        left_title, right_title = _normalized_title(left_role_text), _normalized_title(right_role_text)
        same = bool(left_title and right_title) and (left_title == right_title or left_title in right_title or right_title in left_title)
        result = RoleSimilarityResult("same" if same else "different", 1.0 if same else 0.0, "fallback")
    else:
        jaccard = len(left_skills & right_skills) / len(left_skills | right_skills) if left_skills or right_skills else 0.0
        cosine = cosine_similarity(left_vector, right_vector) if left_vector and right_vector else 0.0
        score = blend_scores(keyword_score=jaccard, semantic_similarity=cosine, semantic_enabled=bool(left_vector and right_vector)).final_score
        result = RoleSimilarityResult("same" if score >= 0.6 else "related" if score >= 0.25 else "different", score, "hybrid")
    db.add(RoleSimilarityCheck(
        owner_id=owner_id, left_record_type=left_type, left_record_id=left_id,
        right_record_type=right_type, right_record_id=right_id,
        skill_overlap_score=jaccard, embedding_score=cosine, final_score=result.final_score,
        tier=result.tier, method=result.method, created_at=utc_now(),
    ))
    return result
