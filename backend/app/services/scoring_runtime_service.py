from __future__ import annotations

import math
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from app.ai.resume_context import extract_resume_context
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.phase0 import ai_assist_score
from app.semantic.embeddings_service import embedding_from_json, embedding_to_json
from app.semantic.ranking import blend_scores, clamp01, semantic_similarity
from app.skill_taxonomy import (
    build_semantic_skill_summary,
    compute_intent_weighted_match,
    detect_role_family,
    extract_taxonomy_skills,
    score_taxonomy_skills,
)


@dataclass
class ScoringRuntimeDeps:
    generate_embedding_with_health: Callable[[str], tuple[list[float], str]]


@dataclass
class SemanticDiagnostics:
    input_source: str
    input_chars: int
    chunks: int
    fallback_reason: str | None
    keyword_source: str | None = None
    thread_snapshot_used: bool | None = None
    thread_snapshot_email_id: int | None = None


@dataclass
class ResumeMatchSelection:
    resume: ResumeAsset | None
    ai_score: float
    ai_summary: str
    ai_score_source: str
    email_embedding_json: str | None
    resume_embedding_json: str | None
    semantic_diag: SemanticDiagnostics


class ScoringRuntimeService:
    def __init__(self, deps: ScoringRuntimeDeps):
        self.deps = deps

    def semantic_text_for_email(self, subject: str, body: str, role: str, skills_text: str) -> str:
        compact_skills = build_semantic_skill_summary(skills_text, role_text=role, limit=14) or skills_text or ""
        return "\n".join(
            [
                f"Subject: {subject or ''}",
                f"Role: {role or ''}",
                f"Skills: {compact_skills}",
                f"Body: {body or ''}",
            ]
        )

    def semantic_text_for_resume(self, resume: ResumeAsset | None) -> str:
        if not resume:
            return ""
        skills_text = str(getattr(resume, "skills_text", "") or "").strip()
        if skills_text and skills_text.lower() != "none_detected":
            compact_skills = build_semantic_skill_summary(skills_text, limit=12) or skills_text
            return f"Skills: {compact_skills}"
        return extract_resume_context(resume.file_path, resume.file_name)

    def select_best_resume_match(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        user_settings: UserSettings,
        email_row: RecruiterEmail | None,
        resumes: list[ResumeAsset],
        fallback_resume: ResumeAsset | None,
        db: Any | None = None,
        owner_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> ResumeMatchSelection:
        enabled_resumes = [resume for resume in resumes if getattr(resume, "is_enabled", False)]

        def _score_resume(resume: ResumeAsset | None, email_ctx: RecruiterEmail | Any | None) -> tuple[ResumeMatchSelection, RecruiterEmail | Any | None]:
            ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json, semantic_diag = self.compute_blended_ai_score(
                subject=subject,
                body=body,
                parsed=parsed,
                user_settings=user_settings,
                email_row=email_ctx,
                resume=resume,
                db=db,
                owner_id=owner_id,
                external_thread_id=external_thread_id,
            )
            if resume and resume_embedding_json and resume.semantic_embedding != resume_embedding_json:
                resume.semantic_embedding = resume_embedding_json
            next_email_ctx = email_ctx
            if email_embedding_json and (email_ctx is None or getattr(email_ctx, "semantic_embedding", None) != email_embedding_json):
                next_email_ctx = SimpleNamespace(
                    id=getattr(email_ctx, "id", None),
                    semantic_embedding=email_embedding_json,
                    external_thread_id=external_thread_id,
                )
            return (
                ResumeMatchSelection(
                    resume=resume,
                    ai_score=ai_score,
                    ai_summary=ai_summary,
                    ai_score_source=ai_score_source,
                    email_embedding_json=email_embedding_json,
                    resume_embedding_json=resume_embedding_json,
                    semantic_diag=semantic_diag,
                ),
                next_email_ctx,
            )

        if not user_settings.feature_semantic_enabled:
            selected_resume = fallback_resume or (enabled_resumes[0] if enabled_resumes else None)
            selection, _ = _score_resume(selected_resume, email_row)
            return selection

        if not enabled_resumes:
            selection, _ = _score_resume(fallback_resume, email_row)
            return selection

        best_selection: ResumeMatchSelection | None = None
        email_ctx: RecruiterEmail | Any | None = email_row
        for resume in enabled_resumes:
            selection, email_ctx = _score_resume(resume, email_ctx)
            if best_selection is None or selection.ai_score > best_selection.ai_score:
                best_selection = selection

        assert best_selection is not None
        return best_selection

    def ensure_embedding_cached(self, current_payload: str | None, text: str) -> tuple[list[float], str | None, str]:
        cached = embedding_from_json(current_payload)
        if cached:
            return cached, current_payload, "cache"
        vector, provider = self.deps.generate_embedding_with_health(text)
        return vector, embedding_to_json(vector), provider

    def _extract_latest_message_block(self, body: str) -> tuple[str, str]:
        text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return "", "full_body_fallback"
        # Keep content before quoted history separators that typically start older thread context.
        separators = [
            r"(?im)^\s*On .+wrote:\s*$",
            r"(?im)^\s*From:\s.+$",
            r"(?im)^\s*Sent:\s.+$",
            r"(?im)^\s*----+\s*Original Message\s*----+\s*$",
            r"(?im)^\s*>+.*$",
            r"(?is)<div[^>]+class=[\"'][^\"']*gmail_quote[^\"']*[\"'][^>]*>.*$",
        ]
        cut_positions: list[int] = []
        for pattern in separators:
            match = re.search(pattern, text)
            if match:
                cut_positions.append(match.start())
        if cut_positions:
            candidate = text[: min(cut_positions)].strip()
            if candidate:
                return candidate, "latest_block"
        return text, "full_body_fallback"

    def _normalize_for_embedding(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _chunk_text(self, text: str, chunk_size: int = 900) -> list[str]:
        normalized = self._normalize_for_embedding(text)
        if not normalized:
            return []
        return [normalized[idx : idx + chunk_size] for idx in range(0, len(normalized), chunk_size)]

    def _average_vectors(self, vectors: list[list[float]]) -> list[float]:
        if not vectors:
            return []
        dims = len(vectors[0])
        totals = [0.0] * dims
        for vector in vectors:
            if len(vector) != dims:
                raise ValueError("embedding_dim_mismatch")
            for idx, value in enumerate(vector):
                totals[idx] += float(value)
        return [value / len(vectors) for value in totals]

    def _safe_embed_with_chunking(
        self, current_payload: str | None, text: str
    ) -> tuple[list[float], str | None, str, int]:
        normalized = self._normalize_for_embedding(text)
        if not normalized:
            return [], current_payload, "empty", 0
        if len(normalized) <= 900:
            vector, payload, provider = self.ensure_embedding_cached(current_payload, normalized)
            return vector, payload, provider, 1

        chunks = self._chunk_text(normalized, chunk_size=900)
        vectors: list[list[float]] = []
        provider_name = "chunked"
        for chunk in chunks:
            vector, provider = self.deps.generate_embedding_with_health(chunk)
            provider_name = provider
            vectors.append(vector)
        averaged = self._average_vectors(vectors)
        return averaged, embedding_to_json(averaged), provider_name, len(chunks)

    def _skills_count(self, skills_text: str) -> int:
        skills = {s.strip().lower() for s in (skills_text or "").split(",") if s.strip() and s.strip().lower() != "none_detected"}
        return len(skills)

    def _keyword_score_from_text(self, parsed: dict[str, str | int], user_settings: UserSettings, text: str) -> tuple[float, str]:
        combined_text = text.lower()
        score = 0.45
        role_keywords = [k.strip().lower() for k in user_settings.role_keywords.split(",") if k.strip()]
        if role_keywords:
            hits = sum(1 for k in role_keywords if k in combined_text)
            score += min(hits * 0.08, 0.24)
        taxonomy_skills_text = ", ".join(entry.canonical_name for entry in extract_taxonomy_skills(text))
        score += score_taxonomy_skills(taxonomy_skills_text)
        score = max(0.0, min(score, 1.0))
        return score, f"AI fit score computed from role keywords and skill overlap ({score:.2f})"

    def _intent_weighted_keyword_score(
        self,
        *,
        parsed: dict[str, str | int],
        user_settings: UserSettings,
        resume: ResumeAsset | None,
    ) -> tuple[float, str]:
        base_score, _base_summary = ai_assist_score(parsed, user_settings)
        if not resume:
            return base_score, f"AI fit score computed from role keywords and skill overlap ({base_score:.2f})"

        intent = compute_intent_weighted_match(
            jd_role=str(parsed.get("role", "")),
            jd_skills_text=str(parsed.get("skills_text", "")),
            resume_skills_text=str(getattr(resume, "skills_text", "") or ""),
        )
        jd_role_family = detect_role_family(str(parsed.get("role", "")), str(parsed.get("skills_text", "")))
        if jd_role_family == "ai":
            final_score = (base_score * 0.35) + (intent.score * 0.65)
        else:
            final_score = (base_score * 0.7) + (intent.score * 0.3)
        final_score = clamp01(final_score)

        summary_parts = [
            f"Intent-weighted fit ({final_score:.2f})",
            f"specialization={intent.specialization_score:.2f}",
            f"foundation={intent.foundation_score:.2f}",
            f"role_alignment={intent.role_alignment_score:.2f}",
            f"jd_role_family={intent.jd_role_family}",
            f"resume_role_family={intent.resume_role_family}",
        ]
        if intent.matched_clusters:
            summary_parts.append(f"matched_clusters={', '.join(intent.matched_clusters)}")
        if intent.matched_specialization_skills:
            summary_parts.append(f"matched_ai_core={', '.join(intent.matched_specialization_skills)}")
        if intent.missing_specialization_skills:
            summary_parts.append(f"missing_ai_core={', '.join(intent.missing_specialization_skills)}")
        if intent.weak_signal_hits:
            summary_parts.append(f"weak_signals={', '.join(intent.weak_signal_hits)}")
        return final_score, "; ".join(summary_parts)

    def _best_thread_snapshot(
        self,
        *,
        db: Any | None,
        owner_id: str | None,
        external_thread_id: str | None,
        current_email_id: int | None,
    ) -> RecruiterEmail | None:
        if db is None or not owner_id or not external_thread_id:
            return None
        query = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == owner_id)
            .filter(RecruiterEmail.external_thread_id == external_thread_id)
            .order_by(RecruiterEmail.created_at.desc())
            .limit(25)
        )
        rows = query.all()
        best: RecruiterEmail | None = None
        best_score = -1
        for row in rows:
            if current_email_id is not None and row.id == current_email_id:
                continue
            richness = self._skills_count(row.skills_text or "")
            if richness > best_score:
                best = row
                best_score = richness
        return best

    def compute_blended_ai_score(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        user_settings: UserSettings,
        email_row: RecruiterEmail | None,
        resume: ResumeAsset | None,
        db: Any | None = None,
        owner_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> tuple[float, str, str, str | None, str | None, SemanticDiagnostics]:
        keyword_score, keyword_summary = self._intent_weighted_keyword_score(
            parsed=parsed,
            user_settings=user_settings,
            resume=resume,
        )
        current_skills_text = str(parsed.get("skills_text", ""))
        current_skill_count = self._skills_count(current_skills_text)
        keyword_source = "parsed_only"
        snapshot_used = False
        snapshot_email_id: int | None = None

        latest_block, _latest_source = self._extract_latest_message_block(body)
        if current_skill_count <= 1:
            richer_text = f"{parsed.get('role', '')} {current_skills_text} {latest_block} {user_settings.free_text_guidance}"
            keyword_score, keyword_summary = self._keyword_score_from_text(parsed, user_settings, richer_text)
            keyword_source = "rich_fallback"

        snapshot = self._best_thread_snapshot(
            db=db,
            owner_id=owner_id,
            external_thread_id=external_thread_id,
            current_email_id=email_row.id if email_row else None,
        )
        if snapshot:
            snapshot_skill_count = self._skills_count(snapshot.skills_text or "")
            if snapshot_skill_count > current_skill_count:
                snapshot_used = True
                snapshot_email_id = snapshot.id
                carry_text = f"{parsed.get('role', '')} {snapshot.skills_text or ''} {latest_block} {user_settings.free_text_guidance}"
                keyword_score, keyword_summary = self._keyword_score_from_text(parsed, user_settings, carry_text)
                keyword_source = "thread_carry_forward"

        base_diag = SemanticDiagnostics(
            input_source="semantic_disabled",
            input_chars=0,
            chunks=0,
            fallback_reason=None,
            keyword_source=keyword_source,
            thread_snapshot_used=snapshot_used,
            thread_snapshot_email_id=snapshot_email_id,
        )
        if not user_settings.feature_semantic_enabled:
            return keyword_score, keyword_summary, "v1_rules_plus_ai", None, None, base_diag

        try:
            latest_block, source = self._extract_latest_message_block(body)
            email_text = self.semantic_text_for_email(
                subject,
                latest_block,
                str(parsed.get("role", "")),
                str(parsed.get("skills_text", "")),
            )
            resume_text = self.semantic_text_for_resume(resume)
            if not resume_text.strip():
                diag = SemanticDiagnostics(
                    input_source=source,
                    input_chars=len(email_text),
                    chunks=0,
                    fallback_reason="resume_text_unavailable",
                    keyword_source=keyword_source,
                    thread_snapshot_used=snapshot_used,
                    thread_snapshot_email_id=snapshot_email_id,
                )
                return keyword_score, f"{keyword_summary}; keyword_source={keyword_source}; semantic skipped (resume text unavailable)", "v2_rules_plus_semantic", None, None, diag

            email_embedding, email_embedding_json, _provider, email_chunks = self._safe_embed_with_chunking(
                email_row.semantic_embedding if email_row else None,
                email_text,
            )
            resume_embedding, resume_embedding_json, _provider_resume, resume_chunks = self._safe_embed_with_chunking(
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
            diag = SemanticDiagnostics(
                input_source="chunked" if email_chunks > 1 or resume_chunks > 1 else source,
                input_chars=len(email_text),
                chunks=max(email_chunks, resume_chunks),
                fallback_reason=None,
                keyword_source=keyword_source,
                thread_snapshot_used=snapshot_used,
                thread_snapshot_email_id=snapshot_email_id,
            )
            return blended.final_score, f"{summary}; keyword_source={keyword_source}", blended.source, email_embedding_json, resume_embedding_json, diag
        except Exception as exc:
            neutral_blended = blend_scores(
                keyword_score=keyword_score,
                semantic_similarity=0.0,
                semantic_enabled=True,
            )
            fallback_score = clamp01(max(keyword_score, neutral_blended.final_score))
            diag = SemanticDiagnostics(
                input_source="latest_block",
                input_chars=len(body or ""),
                chunks=0,
                fallback_reason=str(exc),
                keyword_source=keyword_source,
                thread_snapshot_used=snapshot_used,
                thread_snapshot_email_id=snapshot_email_id,
            )
            summary = f"{keyword_summary}; keyword_source={keyword_source}; semantic fallback ({exc}); neutral semantic score applied ({fallback_score:.2f})"
            return fallback_score, summary, "v2_rules_plus_semantic_neutral_fallback", None, None, diag

    def percentile_ms(self, values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
        index = min(len(ordered) - 1, rank - 1)
        return ordered[index]
