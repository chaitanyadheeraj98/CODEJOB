from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from app.ai.resume_context import extract_resume_context
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.phase0 import ai_assist_score
from app.semantic.embeddings_service import embedding_from_json, embedding_to_json
from app.semantic.ranking import blend_scores, semantic_similarity


@dataclass
class ScoringRuntimeDeps:
    generate_embedding_with_health: Callable[[str], tuple[list[float], str]]


class ScoringRuntimeService:
    def __init__(self, deps: ScoringRuntimeDeps):
        self.deps = deps

    def semantic_text_for_email(self, subject: str, body: str, role: str, skills_text: str) -> str:
        return "\n".join(
            [
                f"Subject: {subject or ''}",
                f"Role: {role or ''}",
                f"Skills: {skills_text or ''}",
                f"Body: {body or ''}",
            ]
        )

    def semantic_text_for_resume(self, resume: ResumeAsset | None) -> str:
        if not resume:
            return ""
        return extract_resume_context(resume.file_path, resume.file_name)

    def ensure_embedding_cached(self, current_payload: str | None, text: str) -> tuple[list[float], str | None, str]:
        cached = embedding_from_json(current_payload)
        if cached:
            return cached, current_payload, "cache"
        vector, provider = self.deps.generate_embedding_with_health(text)
        return vector, embedding_to_json(vector), provider

    def compute_blended_ai_score(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        user_settings: UserSettings,
        email_row: RecruiterEmail | None,
        resume: ResumeAsset | None,
    ) -> tuple[float, str, str, str | None, str | None]:
        keyword_score, keyword_summary = ai_assist_score(parsed, user_settings)
        if not user_settings.feature_semantic_enabled:
            return keyword_score, keyword_summary, "v1_rules_plus_ai", None, None

        try:
            email_text = self.semantic_text_for_email(
                subject,
                body,
                str(parsed.get("role", "")),
                str(parsed.get("skills_text", "")),
            )
            resume_text = self.semantic_text_for_resume(resume)
            if not resume_text.strip():
                return keyword_score, f"{keyword_summary}; semantic skipped (resume text unavailable)", "v2_rules_plus_semantic", None, None

            email_embedding, email_embedding_json, _ = self.ensure_embedding_cached(
                email_row.semantic_embedding if email_row else None,
                email_text,
            )
            resume_embedding, resume_embedding_json, _ = self.ensure_embedding_cached(
                resume.semantic_embedding if resume else None,
                resume_text,
            )
            similarity = semantic_similarity(email_embedding, resume_embedding)
            blended = blend_scores(
                keyword_score=keyword_score,
                semantic_similarity=similarity,
                semantic_enabled=True,
            )
            summary = f"{keyword_summary}; {blended.detail}"
            return blended.final_score, summary, blended.source, email_embedding_json, resume_embedding_json
        except Exception as exc:
            return keyword_score, f"{keyword_summary}; semantic fallback ({exc})", "v2_rules_plus_semantic_fallback", None, None

    def percentile_ms(self, values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
        index = min(len(ordered) - 1, rank - 1)
        return ordered[index]
