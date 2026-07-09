from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from app.ai.resume_context import extract_resume_context
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.parsing.skill_audit import audit_skills_text
from app.phase0 import ai_assist_score, build_skill_source_sections, slice_jd_sections
from app.semantic.embeddings_service import embedding_from_json, embedding_to_json
from app.semantic.ranking import blend_scores, clamp01, semantic_similarity
from app.skill_taxonomy import (
    aggregate_jd_skill_evidence,
    build_semantic_skill_summary,
    compute_intent_weighted_match,
    detect_role_family,
    extract_jd_skill_evidence,
    extract_taxonomy_skills,
    load_skill_taxonomy,
    normalize_skill_token,
    normalize_taxonomy_text,
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
    final_resume_score: float
    selection_reason: str | None
    picker_breakdown_json: str | None
    candidate_rankings_json: str | None
    ats_score: float | None
    ats_score_source: str | None
    ats_summary: str | None
    ats_breakdown_json: str | None
    email_embedding_json: str | None
    resume_embedding_json: str | None
    semantic_diag: SemanticDiagnostics
    mandatory_gate_status: str = "not_applicable"
    mandatory_coverage: float = 1.0


@dataclass(frozen=True)
class PrioritySkillRule:
    canonical_name: str
    aliases: tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class MandatorySkillRule:
    canonical_name: str
    aliases: tuple[str, ...]
    source: str
    buckets: tuple[str, ...] = ()
    critical: bool = False


@dataclass(frozen=True)
class MandatorySkillGateResult:
    status: str
    coverage: float
    required_skills: tuple[str, ...]
    matched_required_skills: tuple[str, ...]
    missing_required_skills: tuple[str, ...]
    weak_required_skills: tuple[str, ...]
    critical_missing: tuple[str, ...]
    evidence: dict[str, object]


_PRIORITY_SKILL_RULES: tuple[PrioritySkillRule, ...] = (
    PrioritySkillRule("Oracle", ("oracle", "oracle database", "oracle db"), 1.15),
    PrioritySkillRule("PL/SQL", ("pl/sql", "plsql", "pl sql"), 1.15),
    PrioritySkillRule("AI tools", ("ai tools", "artificial intelligence tools", "genai tools", "github copilot", "chatgpt"), 1.0),
    PrioritySkillRule("Cloud-native development", ("cloud native", "cloud-native", "cloud native development", "cloud-native development"), 0.95),
    PrioritySkillRule("OpenShift", ("openshift", "open shift"), 1.0),
    PrioritySkillRule("Testing automation", ("testing automation", "test automation", "automation testing"), 0.9),
    PrioritySkillRule("GitHub Enterprise", ("github enterprise", "github enterprise server", "ghe"), 0.85),
    PrioritySkillRule("Java", ("java",), 0.9),
    PrioritySkillRule("Spring Boot", ("spring boot", "springboot"), 0.9),
    PrioritySkillRule("Architecture", ("architecture",), 0.75),
    PrioritySkillRule("React", ("react", "react.js", "reactjs"), 0.8),
    PrioritySkillRule("Angular", ("angular", "angularjs"), 0.8),
    PrioritySkillRule("Microservices", ("microservices", "microservice"), 0.8),
)

_PROJECT_CONTEXT_TERMS = ("project", "projects", "platform", "solution", "support", "context", "integration", "delivery")
_WEAK_PARTIAL_TERMS = ("awareness", "familiarity", "exposure")
_MANDATORY_SECTION_BUCKETS = {"mandatory", "required", "technical_skills", "essential"}
_MANDATORY_STRICT_BUCKETS = {"mandatory", "required", "essential"}
_MANDATORY_STATUS_RANK = {"pass": 0, "needs_review": 1, "not_applicable": 2, "fail": 3}
_MANDATORY_ALIAS_OVERRIDES: dict[str, tuple[str, ...]] = {
    "Spring Reactive": ("spring reactive", "spring webflux", "project reactor", "reactive spring"),
    "Procedural SQL": ("procedural sql", "pl/sql", "plsql", "stored procedures", "database procedures"),
    "Oracle Cloud Infrastructure (OCI)": ("oracle cloud infrastructure", "oracle cloud", "oci"),
    "SaaS Security Posture Management": ("saas security posture management", "sspm"),
    "GitLab CI/CD Pipeline Implementation": (
        "gitlab ci/cd pipeline implementation",
        "gitlab cicd pipeline implementation",
        "gitlab ci",
        "gitlab ci/cd",
        "gitlab pipelines",
        ".gitlab-ci.yml",
    ),
    "CI/CD Pipeline Implementation": ("ci/cd pipeline implementation", "cicd pipeline implementation", "pipeline implementation"),
    "Twistlock (Prisma Cloud) Security Scanning": (
        "twistlock (prisma cloud) security scanning",
        "twistlock prisma cloud security scanning",
        "twistlock",
        "prisma cloud",
        "prisma cloud compute",
        "prisma cloud compute edition",
    ),
    "P&C Knowledge": (
        "p&c knowledge",
        "property and casualty",
        "property & casualty",
        "property casualty insurance",
        "property and casualty insurance",
    ),
    "CASB": ("casb", "cloud access security broker"),
    "Vulnerability Scanning": ("vulnerability scanning", "vulnerability code scanning", "sast", "dast", "sonarqube", "veracode", "checkmarx"),
    "Linux Scripting": ("linux scripting", "shell scripting", "bash"),
    "Terraform": ("terraform", "infrastructure as code", "iac"),
    "PowerShell": ("powershell", "powershell scripting"),
}
_RAW_SKILL_SPLIT_RE = re.compile(r"[,;\n|•]+")
_GROUPED_SKILL_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "spring boot & microservices": ("Spring Boot", "Microservices"),
    "spring boot and microservices": ("Spring Boot", "Microservices"),
    "docker & kubernetes": ("Docker", "Kubernetes"),
    "docker and kubernetes": ("Docker", "Kubernetes"),
    "sql/nosql databases": ("SQL", "NoSQL"),
    "sql nosql databases": ("SQL", "NoSQL"),
    "sql and nosql databases": ("SQL", "NoSQL"),
    "maven/gradle": ("Maven", "Gradle"),
    "maven gradle": ("Maven", "Gradle"),
    "maven and gradle": ("Maven", "Gradle"),
    "agile/scrum methodologies": ("Agile", "Scrum"),
    "agile scrum methodologies": ("Agile", "Scrum"),
    "agile and scrum methodologies": ("Agile", "Scrum"),
}


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

    def semantic_text_for_resume(self, resume: ResumeAsset | None, *, allow_file_fallback: bool = True) -> str:
        if not resume:
            return ""
        skills_text = str(getattr(resume, "skills_text", "") or "").strip()
        if skills_text and skills_text.lower() != "none_detected":
            compact_skills = build_semantic_skill_summary(skills_text, limit=12) or skills_text
            return f"Skills: {compact_skills}"
        if not allow_file_fallback:
            return ""
        return extract_resume_context(resume.file_path, resume.file_name)

    def _picker_sort_key(self, selection: ResumeMatchSelection) -> tuple[int, float, float, float, float]:
        return (
            _MANDATORY_STATUS_RANK.get(selection.mandatory_gate_status, len(_MANDATORY_STATUS_RANK)),
            -selection.final_resume_score,
            -selection.mandatory_coverage,
            -(selection.ats_score or 0.0),
            -selection.ai_score,
        )

    def _split_skill_candidates(self, text: str | None) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for part in _RAW_SKILL_SPLIT_RE.split(str(text or "")):
            token = re.sub(r"^[\-\*\u2022:\s]+|[\-\*\u2022:\s]+$", "", str(part or "").strip())
            token = re.sub(r"\s+", " ", token).strip()
            if not token or len(token) > 80:
                continue
            if len(token.split()) > 8 and token.lower() not in _MANDATORY_ALIAS_OVERRIDES:
                continue
            normalized = normalize_taxonomy_text(token)
            expanded = _GROUPED_SKILL_EXPANSIONS.get(normalized)
            if expanded:
                for item in expanded:
                    item_key = normalize_taxonomy_text(item)
                    if not item_key or item_key == "none detected" or item_key in seen:
                        continue
                    seen.add(item_key)
                    candidates.append(item)
                continue
            if not normalized or normalized == "none detected" or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(token)
        return candidates

    def _rule_aliases(self, canonical_name: str) -> tuple[str, ...]:
        override = _MANDATORY_ALIAS_OVERRIDES.get(canonical_name)
        if override:
            return override
        normalized = normalize_skill_token(canonical_name, preserve_unknown=True) or canonical_name
        aliases = [normalized]
        seen_aliases = {normalize_taxonomy_text(item) for item in aliases}
        entry = load_skill_taxonomy().exact_lookup.get(normalize_taxonomy_text(normalized))
        if entry:
            for alias in (entry.canonical_name, *entry.aliases):
                key = normalize_taxonomy_text(alias)
                if key and key not in seen_aliases:
                    aliases.append(alias)
                    seen_aliases.add(key)
        normalized_aliases = tuple(normalize_taxonomy_text(item) for item in aliases if normalize_taxonomy_text(item))
        return normalized_aliases or (normalize_taxonomy_text(normalized),)

    def _append_mandatory_rule(
        self,
        rules: list[MandatorySkillRule],
        seen: set[str],
        *,
        canonical_name: str,
        source: str,
        buckets: tuple[str, ...] = (),
        critical: bool = False,
    ) -> None:
        normalized_input = normalize_taxonomy_text(canonical_name)
        override_canonical = next(
            (
                canonical
                for canonical, aliases in _MANDATORY_ALIAS_OVERRIDES.items()
                if normalized_input == normalize_taxonomy_text(canonical)
                or normalized_input in {normalize_taxonomy_text(alias) for alias in aliases}
            ),
            None,
        )
        normalized_name = override_canonical or normalize_skill_token(canonical_name, preserve_unknown=True) or canonical_name
        key = normalize_taxonomy_text(normalized_name)
        if not key or key in seen:
            return
        seen.add(key)
        aliases = self._rule_aliases(normalized_name)
        rules.append(
            MandatorySkillRule(
                canonical_name=normalized_name,
                aliases=aliases or (key,),
                source=source,
                buckets=buckets,
                critical=critical,
            )
        )

    def _prune_mandatory_rules(self, rules: list[MandatorySkillRule]) -> list[MandatorySkillRule]:
        normalized_present = {normalize_taxonomy_text(rule.canonical_name) for rule in rules}
        drop_keys: set[str] = set()
        if "pl sql" in normalized_present:
            drop_keys.update({"sql", "pl"})
        if "procedural sql" in normalized_present:
            drop_keys.update({"sql", "stored procedures", "stored procedure"})
        if "spring boot" in normalized_present:
            drop_keys.update({"spring framework", "spring"})
        if "spring reactive" in normalized_present:
            drop_keys.update({"spring framework", "spring"})
        return [rule for rule in rules if normalize_taxonomy_text(rule.canonical_name) not in drop_keys]

    def _extract_mandatory_skill_rules(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        parser_details: dict[str, object] | None,
    ) -> list[MandatorySkillRule]:
        rules: list[MandatorySkillRule] = []
        seen: set[str] = set()

        skill_sections = build_skill_source_sections(slice_jd_sections("\n".join(part for part in [subject, body] if part).strip()))
        strong_sections = [section for section in skill_sections if section.bucket in _MANDATORY_SECTION_BUCKETS]
        if strong_sections:
            aggregated = aggregate_jd_skill_evidence(extract_jd_skill_evidence(strong_sections))
            for skill in aggregated:
                buckets = tuple(skill.buckets)
                critical = any(bucket in _MANDATORY_STRICT_BUCKETS for bucket in buckets)
                self._append_mandatory_rule(
                    rules,
                    seen,
                    canonical_name=skill.canonical_name,
                    source="jd_sections",
                    buckets=buckets,
                    critical=critical,
                )
            for section in strong_sections:
                audit = audit_skills_text(", ".join(self._split_skill_candidates(section.text)))
                bucket_tuple = (section.bucket,)
                critical = section.bucket in _MANDATORY_STRICT_BUCKETS
                for skill_name in [*audit.known, *audit.unknown]:
                    self._append_mandatory_rule(
                        rules,
                        seen,
                        canonical_name=skill_name,
                        source="jd_sections_raw",
                        buckets=bucket_tuple,
                        critical=critical,
                    )
            rules = self._prune_mandatory_rules(rules)
            if rules:
                return rules

        parser_skills: list[str] = []
        if parser_details:
            approved_skills_text = parser_details.get("approved_skills_text")
            if isinstance(approved_skills_text, str):
                parser_skills.extend(self._split_skill_candidates(approved_skills_text))
            skills_audit = parser_details.get("skills_audit")
            if isinstance(skills_audit, dict):
                parser_skills.extend(str(item).strip() for item in (skills_audit.get("known") or []) if str(item).strip())
                parser_skills.extend(str(item).strip() for item in (skills_audit.get("unknown") or []) if str(item).strip())

        parser_skills.extend(self._split_skill_candidates(str(parsed.get("skills_text", "") or "")))
        parser_skills.extend(entry.canonical_name for entry in extract_taxonomy_skills(str(parsed.get("role", "") or "")))
        fallback_unique: list[str] = []
        for skill_name in parser_skills:
            normalized = normalize_skill_token(skill_name, preserve_unknown=True) or skill_name
            key = normalize_taxonomy_text(normalized)
            if key and key not in {normalize_taxonomy_text(item) for item in fallback_unique}:
                fallback_unique.append(normalized)
        for idx, skill_name in enumerate(fallback_unique):
            self._append_mandatory_rule(
                rules,
                seen,
                canonical_name=skill_name,
                source="parsed_fallback",
                critical=idx < 3,
            )
        return self._prune_mandatory_rules(rules)

    def select_best_resume_match(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        parser_details: dict[str, object] | None = None,
        user_settings: UserSettings,
        email_row: RecruiterEmail | None,
        resumes: list[ResumeAsset],
        fallback_resume: ResumeAsset | None,
        db: Any | None = None,
        owner_id: str | None = None,
        external_thread_id: str | None = None,
    ) -> ResumeMatchSelection:
        enabled_resumes = [resume for resume in resumes if getattr(resume, "is_enabled", False)]
        mandatory_rules = self._extract_mandatory_skill_rules(
            subject=subject,
            body=body,
            parsed=parsed,
            parser_details=parser_details,
        )

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
            ats_score, ats_score_source, ats_summary, ats_breakdown_json = self.compute_ats_score(
                subject=subject,
                body=body,
                parsed=parsed,
                user_settings=user_settings,
                resume=resume,
                email_embedding_json=email_embedding_json,
                resume_embedding_json=resume_embedding_json,
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
                self._build_picker_selection(
                    resume=resume,
                    ai_score=ai_score,
                    ai_summary=ai_summary,
                    ai_score_source=ai_score_source,
                    ats_score=ats_score,
                    ats_score_source=ats_score_source,
                    ats_summary=ats_summary,
                    ats_breakdown_json=ats_breakdown_json,
                    email_embedding_json=email_embedding_json,
                    resume_embedding_json=resume_embedding_json,
                    semantic_diag=semantic_diag,
                    subject=subject,
                    body=body,
                    parsed=parsed,
                    parser_details=parser_details,
                    mandatory_rules=mandatory_rules,
                ),
                next_email_ctx,
            )

        if not enabled_resumes:
            selection, _ = _score_resume(fallback_resume, email_row)
            return selection

        best_selection: ResumeMatchSelection | None = None
        scored_selections: list[ResumeMatchSelection] = []
        email_ctx: RecruiterEmail | Any | None = email_row
        for resume in enabled_resumes:
            selection, email_ctx = _score_resume(resume, email_ctx)
            scored_selections.append(selection)
            if best_selection is None or self._picker_sort_key(selection) < self._picker_sort_key(best_selection):
                best_selection = selection

        assert best_selection is not None
        scored_selections.sort(key=self._picker_sort_key)
        best_selection.candidate_rankings_json = self._json_payload(
            {
                "selected_resume_file_name": getattr(best_selection.resume, "file_name", None),
                "rankings": [
                    {
                        "resume_file_name": getattr(item.resume, "file_name", None),
                        "final_resume_score": round(item.final_resume_score, 4),
                        "ai_score": round(item.ai_score, 4),
                        "ats_score": round(item.ats_score or 0.0, 2) if item.ats_score is not None else None,
                        "selection_reason": item.selection_reason,
                        "picker_breakdown": json.loads(item.picker_breakdown_json or "{}"),
                    }
                    for item in scored_selections
                ],
            }
        )
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

    def _raw_skill_tokens(self, skills_text: str | None) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()
        for part in self._split_skill_candidates(skills_text):
            token = normalize_taxonomy_text(part)
            if not token or token == "none detected" or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
        return tokens

    def _raw_skill_overlap(self, jd_skills_text: str | None, resume_skills_text: str | None) -> tuple[float, list[str], list[str]]:
        jd_tokens = self._raw_skill_tokens(jd_skills_text)
        resume_tokens = set(self._raw_skill_tokens(resume_skills_text))
        if not jd_tokens or not resume_tokens:
            return 0.0, [], jd_tokens
        matched = [token for token in jd_tokens if token in resume_tokens]
        missing = [token for token in jd_tokens if token not in resume_tokens]
        return (len(matched) / len(jd_tokens)) if jd_tokens else 0.0, matched, missing

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

    def _json_payload(self, payload: dict[str, object]) -> str:
        return json.dumps(payload, separators=(",", ":"))

    def _normalize_skill_phrase(self, value: str | None) -> str:
        return normalize_taxonomy_text(value)

    def _jd_priority_pool(self, parsed: dict[str, str | int], parser_details: dict[str, object] | None) -> str:
        parts = [str(parsed.get("role", "")), str(parsed.get("skills_text", ""))]
        if parser_details:
            skills_audit = parser_details.get("skills_audit")
            if isinstance(skills_audit, dict):
                parts.extend(str(item) for item in (skills_audit.get("known") or []) if str(item).strip())
                parts.extend(str(item) for item in (skills_audit.get("unknown") or []) if str(item).strip())
            approved_skills_text = parser_details.get("approved_skills_text")
            if isinstance(approved_skills_text, str):
                parts.append(approved_skills_text)
        return " ".join(parts)

    def _extract_priority_rules(
        self,
        *,
        parsed: dict[str, str | int],
        parser_details: dict[str, object] | None,
    ) -> list[PrioritySkillRule]:
        normalized_pool = self._normalize_skill_phrase(self._jd_priority_pool(parsed, parser_details))
        found: list[PrioritySkillRule] = []
        seen: set[str] = set()
        for rule in _PRIORITY_SKILL_RULES:
            if rule.canonical_name in seen:
                continue
            for alias in rule.aliases:
                normalized_alias = self._normalize_skill_phrase(alias)
                if normalized_alias and f" {normalized_alias} " in f" {normalized_pool} ":
                    found.append(rule)
                    seen.add(rule.canonical_name)
                    break
        return found

    def _match_resume_evidence(
        self,
        *,
        resume_skills_text: str,
        rule: PrioritySkillRule,
    ) -> tuple[float, str | None]:
        resume_chunks = [chunk.strip() for chunk in resume_skills_text.split(",") if chunk.strip()]
        best_score = 0.0
        best_label: str | None = None
        for chunk in resume_chunks:
            normalized_chunk = self._normalize_skill_phrase(chunk)
            if not normalized_chunk:
                continue
            if not any(f" {self._normalize_skill_phrase(alias)} " in f" {normalized_chunk} " for alias in rule.aliases):
                continue
            score = 1.0
            label = "direct"
            if "concepts" in normalized_chunk:
                score = 0.60
                label = "concepts"
            elif any(term in normalized_chunk for term in _WEAK_PARTIAL_TERMS):
                score = 0.40
                label = "awareness"
            elif any(term in normalized_chunk for term in _PROJECT_CONTEXT_TERMS):
                score = 0.85
                label = "project_context"
            if score > best_score:
                best_score = score
                best_label = label
        return best_score, best_label

    def _match_mandatory_rule(
        self,
        *,
        resume_skills_text: str,
        rule: MandatorySkillRule,
    ) -> tuple[float, str | None, str | None]:
        resume_chunks = [chunk.strip() for chunk in resume_skills_text.split(",") if chunk.strip()]
        best_score = 0.0
        best_label: str | None = None
        matched_alias: str | None = None
        for chunk in resume_chunks:
            normalized_chunk = self._normalize_skill_phrase(chunk)
            if not normalized_chunk:
                continue
            matched = next(
                (
                    alias
                    for alias in rule.aliases
                    if alias and f" {normalize_taxonomy_text(alias)} " in f" {normalized_chunk} "
                ),
                None,
            )
            if not matched:
                continue
            score = 1.0
            label = "direct"
            if "concepts" in normalized_chunk:
                score = 0.60
                label = "concepts"
            elif any(term in normalized_chunk for term in _WEAK_PARTIAL_TERMS):
                score = 0.40
                label = "awareness"
            elif any(term in normalized_chunk for term in _PROJECT_CONTEXT_TERMS):
                score = 0.85
                label = "project_context"
            if score > best_score:
                best_score = score
                best_label = label
                matched_alias = matched
        return best_score, best_label, matched_alias

    def _compute_mandatory_skill_gate(
        self,
        *,
        rules: list[MandatorySkillRule],
        resume: ResumeAsset | None,
    ) -> MandatorySkillGateResult:
        if not rules:
            return MandatorySkillGateResult(
                status="not_applicable",
                coverage=1.0,
                required_skills=(),
                matched_required_skills=(),
                missing_required_skills=(),
                weak_required_skills=(),
                critical_missing=(),
                evidence={},
            )

        resume_skills_text = str(getattr(resume, "skills_text", "") or "")
        required_skills = tuple(rule.canonical_name for rule in rules)
        strong_matches: list[str] = []
        weak_matches: list[str] = []
        missing_matches: list[str] = []
        critical_missing: list[str] = []
        evidence: dict[str, object] = {}
        strong_count = 0
        weak_count = 0
        for rule in rules:
            evidence_score, evidence_type, matched_alias = self._match_mandatory_rule(
                resume_skills_text=resume_skills_text,
                rule=rule,
            )
            if evidence_score >= 0.85:
                strong_matches.append(rule.canonical_name)
                strong_count += 1
            elif evidence_score > 0.0:
                weak_matches.append(rule.canonical_name)
                weak_count += 1
            else:
                missing_matches.append(rule.canonical_name)
                if rule.critical:
                    critical_missing.append(rule.canonical_name)
            evidence[rule.canonical_name] = {
                "source": rule.source,
                "buckets": list(rule.buckets),
                "critical": rule.critical,
                "matched": evidence_score > 0.0,
                "match_type": evidence_type,
                "matched_alias": matched_alias,
                "evidence_score": round(evidence_score, 3),
            }

        total_rules = len(rules) or 1
        coverage = clamp01((strong_count + (weak_count * 0.5)) / total_rules)
        if not required_skills:
            status = "not_applicable"
        elif strong_count == total_rules and not critical_missing:
            status = "pass"
        elif critical_missing or coverage < 0.5:
            status = "fail"
        elif strong_count > 0 or weak_count > 0:
            status = "needs_review"
        else:
            status = "fail"

        return MandatorySkillGateResult(
            status=status,
            coverage=coverage,
            required_skills=required_skills,
            matched_required_skills=tuple(strong_matches),
            missing_required_skills=tuple(missing_matches),
            weak_required_skills=tuple(weak_matches),
            critical_missing=tuple(critical_missing),
            evidence=evidence,
        )

    def _compute_jd_priority_scores(
        self,
        *,
        parsed: dict[str, str | int],
        parser_details: dict[str, object] | None,
        resume: ResumeAsset | None,
    ) -> dict[str, object]:
        resume_skills_text = str(getattr(resume, "skills_text", "") or "")
        rules = self._extract_priority_rules(parsed=parsed, parser_details=parser_details)
        if not rules:
            return {
                "priority_rules": [],
                "jd_priority_score": 0.0,
                "partial_credit_score": 0.0,
                "matched_priority_skills": [],
                "missing_priority_skills": [],
                "priority_evidence": {},
            }

        total_weight = sum(rule.weight for rule in rules) or 1.0
        matched_weight = 0.0
        partial_weight = 0.0
        matched_priority_skills: list[str] = []
        missing_priority_skills: list[str] = []
        priority_evidence: dict[str, object] = {}
        for rule in rules:
            evidence_score, evidence_label = self._match_resume_evidence(resume_skills_text=resume_skills_text, rule=rule)
            if evidence_score > 0:
                matched_weight += rule.weight
                partial_weight += rule.weight * evidence_score
                matched_priority_skills.append(rule.canonical_name)
            else:
                missing_priority_skills.append(rule.canonical_name)
            priority_evidence[rule.canonical_name] = {
                "weight": round(rule.weight, 3),
                "evidence_score": round(evidence_score, 3),
                "evidence_type": evidence_label,
            }
        return {
            "priority_rules": [rule.canonical_name for rule in rules],
            "jd_priority_score": clamp01(matched_weight / total_weight),
            "partial_credit_score": clamp01(partial_weight / total_weight),
            "matched_priority_skills": matched_priority_skills,
            "missing_priority_skills": missing_priority_skills,
            "priority_evidence": priority_evidence,
        }

    def _role_family_fit_score(
        self,
        *,
        jd_role_family: str,
        resume_role_family: str,
        role_alignment_score: float,
        foundation_score: float,
        jd_priority_score: float,
    ) -> tuple[float, str]:
        adjusted = role_alignment_score
        if jd_role_family == resume_role_family:
            return clamp01(adjusted), "direct_family_alignment"
        if jd_role_family == "general":
            return clamp01(max(adjusted, 0.7)), "general_family_fallback"
        if jd_role_family != "ai" and resume_role_family == "ai":
            if foundation_score >= 0.60 and jd_priority_score >= 0.50:
                return clamp01(max(adjusted, 0.72)), "ai_enabled_fullstack_override"
            if foundation_score < 0.55 and jd_priority_score < 0.50:
                return clamp01(min(adjusted, 0.25)), "generic_ai_guardrail"
        if foundation_score >= 0.65 and jd_priority_score >= 0.45:
            return clamp01(max(adjusted, 0.68)), "foundation_priority_override"
        return clamp01(adjusted), "role_alignment_only"

    def _build_picker_selection(
        self,
        *,
        resume: ResumeAsset | None,
        ai_score: float,
        ai_summary: str,
        ai_score_source: str,
        ats_score: float | None,
        ats_score_source: str | None,
        ats_summary: str | None,
        ats_breakdown_json: str | None,
        email_embedding_json: str | None,
        resume_embedding_json: str | None,
        semantic_diag: SemanticDiagnostics,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        parser_details: dict[str, object] | None,
        mandatory_rules: list[MandatorySkillRule],
    ) -> ResumeMatchSelection:
        _ = subject, body
        ats_score_01 = clamp01((ats_score or 0.0) / 100.0)
        resume_skills_text = str(getattr(resume, "skills_text", "") or "").strip()
        intent = compute_intent_weighted_match(
            jd_role=str(parsed.get("role", "")),
            jd_skills_text=str(parsed.get("skills_text", "")),
            resume_skills_text=resume_skills_text,
        )
        mandatory_gate = self._compute_mandatory_skill_gate(rules=mandatory_rules, resume=resume)
        priority_data = self._compute_jd_priority_scores(parsed=parsed, parser_details=parser_details, resume=resume)
        jd_priority_score = float(priority_data["jd_priority_score"])
        partial_credit_score = float(priority_data["partial_credit_score"])
        role_family_fit_score, role_family_reason = self._role_family_fit_score(
            jd_role_family=intent.jd_role_family,
            resume_role_family=intent.resume_role_family,
            role_alignment_score=intent.role_alignment_score,
            foundation_score=intent.foundation_score,
            jd_priority_score=jd_priority_score,
        )
        final_resume_score = clamp01(
            (ai_score * 0.45)
            + (ats_score_01 * 0.20)
            + (jd_priority_score * 0.20)
            + (role_family_fit_score * 0.10)
            + (partial_credit_score * 0.05)
        )
        matched_priority = list(priority_data["matched_priority_skills"])
        missing_priority = list(priority_data["missing_priority_skills"])
        selected_resume_file_name = getattr(resume, "file_name", None)
        selection_status = "ready_to_submit" if mandatory_gate.status == "pass" else "needs_review"
        selection_warning = (
            "Mandatory FAIL - closest available resume selected; new resume generation may be needed."
            if mandatory_gate.status == "fail"
            else None
        )
        picker_breakdown = {
            "selected_resume_file_name": selected_resume_file_name,
            "final_resume_score": round(final_resume_score, 4),
            "ai_score": round(ai_score, 4),
            "ats_score": round(ats_score or 0.0, 2) if ats_score is not None else None,
            "ats_score_01": round(ats_score_01, 4),
            "jd_priority_score": round(jd_priority_score, 4),
            "role_family_fit_score": round(role_family_fit_score, 4),
            "partial_credit_score": round(partial_credit_score, 4),
            "jd_role_family": intent.jd_role_family,
            "resume_role_family": intent.resume_role_family,
            "foundation_score": round(intent.foundation_score, 4),
            "role_alignment_score": round(intent.role_alignment_score, 4),
            "role_family_reason": role_family_reason,
            "matched_priority_skills": matched_priority,
            "missing_priority_skills": missing_priority,
            "priority_evidence": priority_data["priority_evidence"],
            "mandatory_gate_status": mandatory_gate.status,
            "mandatory_coverage": round(mandatory_gate.coverage, 4),
            "mandatory_required_skills": list(mandatory_gate.required_skills),
            "mandatory_matched_skills": list(mandatory_gate.matched_required_skills),
            "mandatory_missing_skills": list(mandatory_gate.missing_required_skills),
            "mandatory_weak_skills": list(mandatory_gate.weak_required_skills),
            "critical_missing": list(mandatory_gate.critical_missing),
            "mandatory_evidence": mandatory_gate.evidence,
            "selection_status": selection_status,
            "selection_warning": selection_warning,
            "weak_signal_hits": list(intent.weak_signal_hits),
        }
        selection_reason_parts = [f"Mandatory {mandatory_gate.status.upper()} {mandatory_gate.coverage:.2f}"]
        if mandatory_gate.status == "fail":
            selection_reason_parts.append("closest available resume selected")
            selection_reason_parts.append("new resume generation may be needed")
        selection_reason_parts.extend(
            [
                f"Final {final_resume_score:.2f}",
                f"ai={ai_score:.2f}",
                f"ats={(ats_score or 0.0):.2f}",
                f"priority={jd_priority_score:.2f}",
                f"role_fit={role_family_fit_score:.2f}",
                f"matched={', '.join(matched_priority[:4]) or 'none'}",
                f"missing_required={', '.join(mandatory_gate.missing_required_skills[:4]) or 'none'}",
            ]
        )
        selection_reason = "; ".join(selection_reason_parts)
        return ResumeMatchSelection(
            resume=resume,
            ai_score=ai_score,
            ai_summary=ai_summary,
            ai_score_source=ai_score_source,
            final_resume_score=final_resume_score,
            selection_reason=selection_reason,
            picker_breakdown_json=self._json_payload(picker_breakdown),
            candidate_rankings_json=None,
            ats_score=ats_score,
            ats_score_source=ats_score_source,
            ats_summary=ats_summary,
            ats_breakdown_json=ats_breakdown_json,
            email_embedding_json=email_embedding_json,
            resume_embedding_json=resume_embedding_json,
            semantic_diag=semantic_diag,
            mandatory_gate_status=mandatory_gate.status,
            mandatory_coverage=mandatory_gate.coverage,
        )

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

        resume_skills_text = str(getattr(resume, "skills_text", "") or "").strip()
        if not resume_skills_text or resume_skills_text.lower() == "none_detected":
            weak_score = clamp01(min(base_score, 0.18))
            return weak_score, f"Resume skills unavailable ({weak_score:.2f}); scored weak because matching trusts saved resume skills_text"

        intent = compute_intent_weighted_match(
            jd_role=str(parsed.get("role", "")),
            jd_skills_text=str(parsed.get("skills_text", "")),
            resume_skills_text=resume_skills_text,
        )
        raw_overlap_score, matched_raw, missing_raw = self._raw_skill_overlap(
            str(parsed.get("skills_text", "")),
            resume_skills_text,
        )
        jd_role_family = detect_role_family(str(parsed.get("role", "")), str(parsed.get("skills_text", "")))
        if jd_role_family == "ai":
            final_score = (raw_overlap_score * 0.5) + (intent.score * 0.35) + (base_score * 0.15)
        else:
            final_score = (raw_overlap_score * 0.55) + (base_score * 0.3) + (intent.score * 0.15)
        final_score = clamp01(final_score)

        summary_parts = [
            f"Intent-weighted fit ({final_score:.2f})",
            f"raw_overlap={raw_overlap_score:.2f}",
            f"specialization={intent.specialization_score:.2f}",
            f"foundation={intent.foundation_score:.2f}",
            f"role_alignment={intent.role_alignment_score:.2f}",
            f"jd_role_family={intent.jd_role_family}",
            f"resume_role_family={intent.resume_role_family}",
        ]
        if matched_raw:
            summary_parts.append(f"matched_raw_skills={', '.join(matched_raw[:8])}")
        if missing_raw:
            summary_parts.append(f"missing_raw_skills={', '.join(missing_raw[:8])}")
        if intent.matched_clusters:
            summary_parts.append(f"matched_clusters={', '.join(intent.matched_clusters)}")
        if intent.matched_specialization_skills:
            summary_parts.append(f"matched_ai_core={', '.join(intent.matched_specialization_skills)}")
        if intent.missing_specialization_skills:
            summary_parts.append(f"missing_ai_core={', '.join(intent.missing_specialization_skills)}")
        if intent.weak_signal_hits:
            summary_parts.append(f"weak_signals={', '.join(intent.weak_signal_hits)}")
        return final_score, "; ".join(summary_parts)

    def compute_ats_score(
        self,
        *,
        subject: str,
        body: str,
        parsed: dict[str, str | int],
        user_settings: UserSettings,
        resume: ResumeAsset | None,
        email_embedding_json: str | None = None,
        resume_embedding_json: str | None = None,
    ) -> tuple[float | None, str | None, str | None, str | None]:
        _ = subject, body
        if not resume:
            return None, None, None, None

        resume_skills_text = str(getattr(resume, "skills_text", "") or "").strip()
        jd_skills_text = str(parsed.get("skills_text", "") or "")
        intent = compute_intent_weighted_match(
            jd_role=str(parsed.get("role", "")),
            jd_skills_text=jd_skills_text,
            resume_skills_text=resume_skills_text,
        )
        raw_overlap, matched_raw, missing_raw = self._raw_skill_overlap(jd_skills_text, resume_skills_text)
        role_foundation_score = clamp01((intent.foundation_score * 0.55) + (intent.role_alignment_score * 0.45))
        weak_penalty = min(0.12, 0.04 * len(intent.weak_signal_hits)) if intent.jd_role_family == "ai" else 0.0

        semantic_similarity_score = 0.0
        semantic_used = False
        if user_settings.feature_semantic_enabled:
            email_embedding = embedding_from_json(email_embedding_json)
            resume_embedding = embedding_from_json(resume_embedding_json)
            if email_embedding and resume_embedding:
                semantic_similarity_score = semantic_similarity(email_embedding, resume_embedding)
                semantic_used = True

        weighted_sum = (
            (raw_overlap * 0.45)
            + (intent.score * 0.30)
            + (role_foundation_score * 0.15)
            + ((semantic_similarity_score if semantic_used else 0.0) * 0.10)
        )
        total_weight = 1.0 if semantic_used else 0.90
        final_score_01 = clamp01((weighted_sum / total_weight) - weak_penalty)
        final_score = round(final_score_01 * 100.0, 2)

        breakdown_payload: dict[str, object] = {
            "raw_overlap": round(raw_overlap, 4),
            "intent_match": round(intent.score, 4),
            "role_alignment": round(intent.role_alignment_score, 4),
            "foundation_coverage": round(intent.foundation_score, 4),
            "semantic_similarity": round(semantic_similarity_score, 4) if semantic_used else None,
            "matched_raw_skills": matched_raw,
            "missing_raw_skills": missing_raw,
            "matched_clusters": list(intent.matched_clusters),
            "matched_specialization_skills": list(intent.matched_specialization_skills),
            "missing_specialization_skills": list(intent.missing_specialization_skills),
            "weak_signal_hits": list(intent.weak_signal_hits),
            "selected_resume_file_name": getattr(resume, "file_name", None),
        }
        summary_parts = [
            f"ATS hybrid score {int(round(final_score))}/100",
            f"raw_overlap={raw_overlap:.2f}",
            f"intent_match={intent.score:.2f}",
            f"role_alignment={intent.role_alignment_score:.2f}",
        ]
        if semantic_used:
            summary_parts.append(f"semantic_similarity={semantic_similarity_score:.2f}")
        else:
            summary_parts.append("semantic_similarity=unavailable")
        if intent.weak_signal_hits:
            summary_parts.append(f"weak_signals={', '.join(intent.weak_signal_hits)}")

        source = "hybrid_structured_plus_semantic" if semantic_used else "hybrid_structured_only"
        return final_score, source, "; ".join(summary_parts), self._json_payload(breakdown_payload)

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
            resume_text = self.semantic_text_for_resume(resume, allow_file_fallback=False)
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
