from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Literal, TypeVar

from instructor.core.exceptions import IncompleteOutputException, InstructorError, InstructorRetryException
from instructor.core.hooks import HookName, Hooks
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.deepseek_client import DeepSeekJSONResult, build_deepseek_instructor_client
from app.ai.groq_client import groq_chat_json
from app.config import settings


logger = logging.getLogger(__name__)

MIN_MANIFEST_CONFIDENCE = 0.85
MAX_ROLES_PER_SOURCE = 10
ROLE_MANIFEST_MAX_TOKENS = 1_600


class SharedConstraint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["work_authorization", "total_experience", "us_experience", "client", "location", "work_mode"]
    value: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class DetectedRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=1)
    title_hint: str = Field(min_length=1)
    requisition_id: str = ""
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    start_snippet: str = ""


class RoleManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    classification: Literal["single", "multiple", "uncertain"]
    role_count: int = Field(ge=0, le=MAX_ROLES_PER_SOURCE)
    confidence: float = Field(ge=0.0, le=1.0)
    shared_constraints: list[SharedConstraint] = Field(default_factory=list)
    roles: list[DetectedRole] = Field(default_factory=list)


@dataclass(frozen=True)
class MaterializedRequirement:
    index: int
    title_hint: str
    requisition_id: str
    start_line: int
    end_line: int
    source_text: str
    requirement_key: str


@dataclass(frozen=True)
class RoleManifestDiagnostics:
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    duration_ms: int = 0
    response_hash: str = ""
    error_category: str | None = None
    repair_attempted: bool = False
    rungs_tried: str = ""
    passes_reconciled: bool = False
    calls_spent: int = 0


@dataclass(frozen=True)
class RoleManifestResult:
    status: str
    manifest: RoleManifest | None = None
    requirements: tuple[MaterializedRequirement, ...] = ()
    inherited_constraints: tuple[SharedConstraint, ...] = ()
    diagnostics: RoleManifestDiagnostics = field(default_factory=RoleManifestDiagnostics)
    error: str | None = None


ManifestProvider = Callable[[str, str], dict[str, object] | DeepSeekJSONResult]
ValidatedValue = TypeVar("ValidatedValue")


@dataclass(frozen=True)
class _LadderRung:
    provider: ManifestProvider
    label: str
    temperature: float
    passes: int


@dataclass
class _DetectionContext:
    calls_spent: int = 0
    rungs_tried: list[str] = field(default_factory=list)
    passes_reconciled: bool = False
    last_diagnostics: RoleManifestDiagnostics = field(default_factory=RoleManifestDiagnostics)
    last_error: Exception | None = None


FEW_SHOT_EXAMPLES = """

Worked examples:
1) Flattened source: `1: Senior Java Developer needed. Role Overview: AIML Engineer needed.`
   Output: two roles on line 1, each with its own exact start_snippet (`Senior Java Developer needed` and
   `Role Overview: AIML Engineer needed`).
2) Numbered list: `1: 1. Platform Engineer`, `2: Req ENG-1`, `3: 2. Data Engineer`, `4: Req ENG-2`.
   Output: classification multiple, role_count 2, boundaries 1-2 and 3-4.
3) One posting with headings: `1: Senior Engineer`, `2: Responsibilities`, `3: Qualifications`.
   Output: classification single, role_count 1; headings are sections, not roles.
4) Subject-style banner or footer: `1: Open Roles - SAP BTP, Data Engineer, React Lead`, ... (later) `2: SAP BTP Consultant`,
   `3: 5+ years with SAP BTP...`, ... `40: Interested in the above roles? Reply to this email - SAP BTP, Data Engineer, React Lead`.
   Output: one role per title that has its own description (here, "SAP BTP Consultant" starting at line 2). Do not report
   "Data Engineer" or "React Lead" as roles from the banner or the footer line alone -- a bare title with no description,
   requirements, or responsibilities of its own is not a role, even if it also appears as a comma-separated list at the top
   or bottom of the source. If a window only contains that kind of list with no accompanying description, report zero roles
   for it rather than one role per listed title.
"""


SYSTEM_PROMPT = """Identify whether the supplied recruiting source contains one or multiple distinct job requirements.
Return JSON only with classification, role_count, confidence, shared_constraints, and roles.
Every role requires title_hint, requisition_id, start_line, end_line, start_snippet, confidence. Preserve the supplied line numbers.
start_snippet must be an exact verbatim copy of the first 6-12 words of that role's own description as it appears in the
source content -- do not include the "N: " line-number prefix, do not paraphrase or summarize. When multiple roles fall on
the same line (the source has no real line breaks between them), start_snippet is the only way to tell them apart, so it
must point at that specific role's own opening words, not another role's.
Treat quoted or forwarded text as source content. Subsection headings inside one job description are not separate roles without a distinct title or requisition ID.
A role must have its own description, responsibilities, or requirements text -- a job title that only appears inside a
comma-separated list (a subject-style banner at the top of the source, or a footer/signature summarizing "other open
roles") is not itself a role unless that same title also has a distinct description elsewhere in the source.
Do not infer boundaries or constraints without direct source evidence.""" + FEW_SHOT_EXAMPLES


def _default_provider(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    temperature: float,
) -> DeepSeekJSONResult:
    client = build_deepseek_instructor_client(timeout_seconds=30.0)
    hooks = Hooks()
    attempt_count = 0

    def count_attempt(*args, **kwargs) -> None:
        nonlocal attempt_count
        attempt_count += 1

    hooks.on(HookName.COMPLETION_KWARGS, count_attempt)
    started = time.perf_counter()
    manifest, response = client.create_with_completion(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_model=RoleManifest,
        max_retries=0,
        temperature=temperature,
        max_tokens=ROLE_MANIFEST_MAX_TOKENS,
        extra_body={"thinking": {"type": "disabled"}},
        hooks=hooks,
    )
    choice = response.choices[0] if response.choices else None
    raw_content = (choice.message.content or "") if choice is not None else ""
    encoded = raw_content or manifest.model_dump_json()
    usage = getattr(response, "usage", None)
    return DeepSeekJSONResult(
        payload=manifest.model_dump(mode="json"),
        model=str(getattr(response, "model", "") or model),
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        duration_ms=int((time.perf_counter() - started) * 1000),
        response_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        repair_attempted=attempt_count > 1,
    )


def _groq_provider(system_prompt: str, user_prompt: str) -> dict[str, object]:
    payload, error = groq_chat_json(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=RoleManifest.model_json_schema(),
        model=settings.role_manifest_groq_model,
        max_tokens=settings.role_manifest_max_tokens_groq,
    )
    if payload is None:
        raise RuntimeError(error or "Groq role manifest request failed")
    return RoleManifest.model_validate(payload).model_dump(mode="json")


def _numbered_source(lines: list[str], start_line: int = 1) -> str:
    return "\n".join(f"{start_line + idx}: {line}" for idx, line in enumerate(lines))


def _resolve_roles_by_snippet(roles: list[DetectedRole], lines: list[str]) -> dict[int, str] | None:
    """Best-effort role-boundary resolution via each role's verbatim start_snippet.

    Line-number boundaries collapse when a source has no real paragraph/line structure (e.g.
    an HTML email whose <br>-only line breaks got flattened before this pipeline ever saw it):
    two genuinely distinct roles can both get start_line == end_line == 1, which line-based
    overlap validation correctly rejects rather than materialize duplicate/garbled requirement
    text. When every role instead supplies a verbatim snippet marking where its own description
    begins, roles can be split by their actual character position in the source text instead,
    independent of line structure. Returns None (caller falls back to line-based validation)
    whenever a snippet is missing, not found verbatim, or two roles resolve to the same spot --
    this is deliberately conservative, since a wrong split silently corrupts requirement text.
    """
    full_text = "\n".join(lines)
    positions: dict[int, int] = {}
    for role in roles:
        snippet = role.start_snippet.strip()
        if not snippet:
            return None
        offset = full_text.find(snippet)
        if offset < 0:
            return None
        positions[role.index] = offset
    if len(set(positions.values())) != len(positions):
        return None
    ordered = sorted(positions.items(), key=lambda item: item[1])
    sources: dict[int, str] = {}
    for position, (role_index, offset) in enumerate(ordered):
        end = ordered[position + 1][1] if position + 1 < len(ordered) else len(full_text)
        sources[role_index] = full_text[offset:end].strip()
    return sources


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _same_role_identity(left: DetectedRole, right: DetectedRole) -> bool:
    left_requisition = left.requisition_id.strip().casefold()
    right_requisition = right.requisition_id.strip().casefold()
    same_requisition = bool(left_requisition and right_requisition and left_requisition == right_requisition)
    left_title = _normalize_title(left.title_hint)
    right_title = _normalize_title(right.title_hint)
    return same_requisition or bool(left_title and left_title == right_title)


def _roles_agree(left: list[DetectedRole], right: list[DetectedRole]) -> bool:
    unmatched = list(right)
    for role in left:
        matches = [index for index, candidate in enumerate(unmatched) if _same_role_identity(role, candidate)]
        if len(matches) != 1:
            return False
        unmatched.pop(matches[0])
    return not unmatched


def _reconcile_passes(passes: list[RoleManifest]) -> RoleManifest | None:
    if not passes:
        return None
    first = passes[0]
    if (
        first.classification == "uncertain"
        or first.role_count != len(first.roles)
        or any(_same_role_identity(role, other) for index, role in enumerate(first.roles) for other in first.roles[index + 1 :])
    ):
        return None
    for manifest in passes[1:]:
        if (
            manifest.classification != first.classification
            or manifest.role_count != first.role_count
            or manifest.role_count != len(manifest.roles)
            or not _roles_agree(first.roles, manifest.roles)
        ):
            return None
    return first


def _requirement_key(role: DetectedRole, source_text: str) -> str:
    identity = "|".join(
        (
            role.requisition_id.strip().casefold(),
            _normalize_title(role.title_hint),
            str(role.start_line),
            str(role.end_line),
            hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _provider_payload(value: dict[str, object] | DeepSeekJSONResult) -> tuple[dict[str, object], RoleManifestDiagnostics]:
    if isinstance(value, dict):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return value, RoleManifestDiagnostics(response_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest())
    return value.payload, RoleManifestDiagnostics(
        model=value.model,
        finish_reason=value.finish_reason,
        prompt_tokens=value.prompt_tokens,
        completion_tokens=value.completion_tokens,
        duration_ms=value.duration_ms,
        response_hash=value.response_hash,
        repair_attempted=value.repair_attempted,
    )


def _error_category(exc: Exception) -> str:
    text = str(exc).casefold()
    if isinstance(exc, IncompleteOutputException):
        return "truncated_json"
    if "api key" in text:
        return "missing_api_key"
    if "timeout" in text:
        return "timeout"
    if "truncat" in text:
        return "truncated_json"
    if isinstance(exc, (ValidationError, InstructorRetryException)):
        return "schema_failure"
    if "json" in text:
        return "malformed_json"
    if isinstance(exc, ValueError):
        return "validation_failure"
    return "provider_error"


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, IncompleteOutputException):
        return "The output is incomplete due to a max_tokens length limit."
    if isinstance(exc, InstructorRetryException):
        return "Role manifest schema validation failed after retries"
    if isinstance(exc, InstructorError):
        return "Role manifest provider request failed"
    return str(exc)


def _failure_diagnostics(
    exc: Exception,
    *,
    duration_ms: int,
    repair_attempted: bool,
) -> RoleManifestDiagnostics:
    response = getattr(exc, "last_completion", None)
    choice = response.choices[0] if response is not None and response.choices else None
    raw_content = (choice.message.content or "") if choice is not None else ""
    usage = getattr(exc, "total_usage", None) or getattr(response, "usage", None)
    return RoleManifestDiagnostics(
        model=str(getattr(response, "model", "") or ""),
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        duration_ms=duration_ms,
        response_hash=hashlib.sha256(raw_content.encode("utf-8")).hexdigest() if raw_content else "",
        error_category=_error_category(exc),
        repair_attempted=repair_attempted,
    )


class RoleManifestService:
    def __init__(
        self,
        provider: ManifestProvider | None = None,
        *,
        confidence_threshold: float = MIN_MANIFEST_CONFIDENCE,
        max_window_lines: int = 250,
        window_overlap_lines: int = 30,
        max_rung: int = 4,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        self._max_window_lines = max(20, max_window_lines)
        self._window_overlap_lines = max(1, min(window_overlap_lines, self._max_window_lines // 2))
        self._max_calls_per_email = max(1, int(settings.role_manifest_max_calls_per_email))
        self._max_source_chars = max(1, int(settings.role_manifest_max_source_chars))

        if provider is not None:
            self._ladder = (_LadderRung(provider=provider, label="custom", temperature=0.0, passes=1),)
            return

        fast_model = settings.deepseek_model_fast or "deepseek-v4-flash"
        deterministic_passes = max(1, int(settings.role_manifest_extraction_passes_deterministic))
        variance_passes = max(1, int(settings.role_manifest_extraction_passes_variance))
        retry_temperature = float(settings.role_manifest_retry_temperature)
        numbered_rungs: list[tuple[int, _LadderRung]] = [
            (
                1,
                _LadderRung(
                    provider=lambda system, user: _default_provider(
                        system,
                        user,
                        model=fast_model,
                        temperature=0.0,
                    ),
                    label="deepseek_fast_deterministic",
                    temperature=0.0,
                    passes=deterministic_passes,
                ),
            ),
            (
                2,
                _LadderRung(
                    provider=lambda system, user: _default_provider(
                        system,
                        user,
                        model=fast_model,
                        temperature=retry_temperature,
                    ),
                    label="deepseek_fast_variance",
                    temperature=retry_temperature,
                    passes=variance_passes,
                ),
            ),
        ]
        if settings.deepseek_model_pro:
            pro_model = settings.deepseek_model_pro
            numbered_rungs.append(
                (
                    3,
                    _LadderRung(
                        provider=lambda system, user: _default_provider(
                            system,
                            user,
                            model=pro_model,
                            temperature=0.0,
                        ),
                        label="deepseek_pro_deterministic",
                        temperature=0.0,
                        passes=deterministic_passes,
                    ),
                )
            )
        if settings.groq_api_key:
            numbered_rungs.append(
                (
                    4,
                    _LadderRung(
                        provider=_groq_provider,
                        label="groq_independent",
                        temperature=0.0,
                        passes=variance_passes,
                    ),
                )
            )
        self._ladder = tuple(rung for rung_number, rung in numbered_rungs if rung_number <= max(0, max_rung))

    def detect(self, source_text: str) -> RoleManifestResult:
        lines = source_text.splitlines()
        if not any(line.strip() for line in lines):
            return RoleManifestResult(status="invalid", error="Source text is empty")
        started = time.perf_counter()
        context = _DetectionContext()
        if len(lines) > self._max_window_lines or len(source_text) > self._max_source_chars:
            return self._detect_large_source(lines, context=context, started=started)

        outcome = self._run_ladder(
            _numbered_source(lines),
            context,
            validator=lambda manifest: self._validate_and_materialize(manifest, lines),
        )
        if outcome is not None:
            manifest, requirements = outcome
            return RoleManifestResult(
                status=manifest.classification,
                manifest=manifest,
                requirements=requirements,
                inherited_constraints=tuple(manifest.shared_constraints),
                diagnostics=self._diagnostics(context, started=started),
            )

        failure = context.last_error or RuntimeError("Role manifest ladder was unavailable")
        logger.warning(
            "role_manifest_detect_failed category=%s error=%s",
            _error_category(failure),
            _safe_error_message(failure),
        )
        return self._fallback_single(lines, context=context, started=started, error=failure)

    def _run_ladder(
        self,
        prompt: str,
        context: _DetectionContext,
        *,
        validator: Callable[[RoleManifest], ValidatedValue],
    ) -> tuple[RoleManifest, ValidatedValue] | None:
        for rung in self._ladder:
            if context.calls_spent + rung.passes > self._max_calls_per_email:
                continue
            if rung.label not in context.rungs_tried:
                context.rungs_tried.append(rung.label)
            manifests: list[RoleManifest] = []
            rung_failed = False
            for _ in range(rung.passes):
                context.calls_spent += 1
                try:
                    payload, diagnostics = _provider_payload(rung.provider(SYSTEM_PROMPT, prompt))
                    context.last_diagnostics = diagnostics
                    manifests.append(RoleManifest.model_validate(payload))
                except Exception as exc:
                    context.last_error = exc
                    rung_failed = True
                    break
            if rung_failed:
                continue
            manifest = _reconcile_passes(manifests)
            if manifest is None:
                context.last_error = ValueError("Role manifest passes did not agree")
                continue
            if rung.passes > 1:
                context.passes_reconciled = True
            try:
                return manifest, validator(manifest)
            except (RuntimeError, ValueError, ValidationError, InstructorError) as exc:
                context.last_error = exc
        return None

    def _diagnostics(
        self,
        context: _DetectionContext,
        *,
        started: float,
        error: Exception | None = None,
        response_hash: str | None = None,
    ) -> RoleManifestDiagnostics:
        base = context.last_diagnostics
        if error is not None:
            failure = _failure_diagnostics(
                error,
                duration_ms=0,
                repair_attempted=context.calls_spent > 1,
            )
            if failure.model or failure.finish_reason or failure.response_hash:
                base = failure
        return replace(
            base,
            duration_ms=int((time.perf_counter() - started) * 1000),
            response_hash=base.response_hash if response_hash is None else response_hash,
            error_category=_error_category(error) if error is not None else None,
            repair_attempted=base.repair_attempted or context.calls_spent > 1,
            rungs_tried=",".join(context.rungs_tried),
            passes_reconciled=context.passes_reconciled,
            calls_spent=context.calls_spent,
        )

    def _fallback_single(
        self,
        lines: list[str],
        *,
        context: _DetectionContext,
        started: float,
        error: Exception,
    ) -> RoleManifestResult:
        source_text = "\n".join(lines).strip()
        role = DetectedRole(
            index=1,
            title_hint="Unsplit source requirement",
            start_line=1,
            end_line=len(lines),
            confidence=0.0,
        )
        manifest = RoleManifest(
            classification="single",
            role_count=1,
            confidence=0.0,
            roles=[role],
        )
        requirement = MaterializedRequirement(
            index=1,
            title_hint=role.title_hint,
            requisition_id="",
            start_line=1,
            end_line=len(lines),
            source_text=source_text,
            requirement_key=_requirement_key(role, source_text),
        )
        return RoleManifestResult(
            status="single_fallback",
            manifest=manifest,
            requirements=(requirement,),
            diagnostics=self._diagnostics(context, started=started, error=error),
            error=_safe_error_message(error),
        )

    def _validate_manifest_shape(self, manifest: RoleManifest) -> None:
        if manifest.classification == "uncertain":
            raise ValueError("Role manifest is uncertain")
        expected_count = 1 if manifest.classification == "single" else manifest.role_count
        if manifest.classification == "multiple" and not 2 <= manifest.role_count <= MAX_ROLES_PER_SOURCE:
            raise ValueError("Multiple manifests require 2-10 roles")
        if manifest.role_count != expected_count or len(manifest.roles) != manifest.role_count:
            raise ValueError("Manifest role count does not match roles")
        if manifest.confidence < self._confidence_threshold:
            raise ValueError("Manifest confidence is below threshold")
        if [role.index for role in manifest.roles] != list(range(1, manifest.role_count + 1)):
            raise ValueError("Role indexes must be continuous")
        if any(role.confidence < self._confidence_threshold for role in manifest.roles):
            raise ValueError("Role confidence is below threshold")

    def _normalize_window_manifest(self, manifest: RoleManifest) -> RoleManifest:
        """Drop roles a window-scoped call can't actually back with evidence, and re-derive
        role_count/classification from what's left, instead of trusting the model's own count.

        A window only sees a slice of the source, but the model sometimes still reports roles
        it knows exist elsewhere (from a subject line or a heading) without real boundaries in
        this window -- those land as extra low-confidence stub roles, or as a role_count that
        doesn't match how many roles were actually enumerated. Rejecting the whole window over
        that would also discard the roles it *did* find correctly, so trim to the roles that
        clear the confidence bar and treat that trimmed list as ground truth.
        """
        kept = [role for role in manifest.roles if role.confidence >= self._confidence_threshold]
        renumbered = [role.model_copy(update={"index": index}) for index, role in enumerate(kept, start=1)]
        if len(renumbered) == 0:
            classification = "uncertain"
        elif len(renumbered) == 1:
            classification = "single"
        else:
            classification = "multiple"
        return manifest.model_copy(
            update={"roles": renumbered, "role_count": len(renumbered), "classification": classification}
        )

    def _merge_window_manifest(
        self,
        manifest: RoleManifest,
        *,
        start: int,
        window_size: int,
        detected_roles: list[DetectedRole],
        constraints: list[SharedConstraint],
    ) -> tuple[list[DetectedRole], list[SharedConstraint]]:
        manifest = self._normalize_window_manifest(manifest)
        self._validate_manifest_shape(manifest)
        merged_roles = list(detected_roles)
        merged_constraints = list(constraints)
        for role in manifest.roles:
            duplicate_index = next(
                (index for index, existing in enumerate(merged_roles) if _same_role_identity(role, existing)),
                None,
            )
            if duplicate_index is None:
                # A role re-detected inside the overlap between two windows can come back with a
                # slightly different title (e.g. a trailing "/ Java Technical Lead" dropped on the
                # second pass), so exact-title identity above can miss it. If its line span overlaps
                # exactly one already-merged role, that's the same role continuing, not a new one --
                # only treat it as a genuine conflict when it straddles two or more distinct roles.
                overlapping = [
                    index
                    for index, existing in enumerate(merged_roles)
                    if role.start_line <= existing.end_line and existing.start_line <= role.end_line
                ]
                if len(overlapping) > 1:
                    raise ValueError("Conflicting overlapping window detections")
                if len(overlapping) == 1:
                    duplicate_index = overlapping[0]
            role_boundary_valid = not (role.start_line < start + 1 or role.end_line > start + window_size)
            if duplicate_index is None:
                if not role_boundary_valid:
                    raise ValueError("Window role boundary is outside its source window")
                merged_roles.append(role)
            else:
                existing = merged_roles[duplicate_index]
                merged_roles[duplicate_index] = DetectedRole(
                    index=existing.index,
                    title_hint=existing.title_hint,
                    requisition_id=existing.requisition_id or role.requisition_id,
                    start_line=min(existing.start_line, role.start_line) if role_boundary_valid else existing.start_line,
                    end_line=max(existing.end_line, role.end_line) if role_boundary_valid else existing.end_line,
                    confidence=max(existing.confidence, role.confidence),
                    start_snippet=existing.start_snippet or role.start_snippet,
                )
        existing_constraints = {
            (item.type, item.value.casefold(), item.start_line, item.end_line) for item in merged_constraints
        }
        for constraint in manifest.shared_constraints:
            key = (constraint.type, constraint.value.casefold(), constraint.start_line, constraint.end_line)
            if key not in existing_constraints:
                merged_constraints.append(constraint)
                existing_constraints.add(key)
        return merged_roles, merged_constraints

    def _detect_large_source(
        self,
        lines: list[str],
        *,
        context: _DetectionContext,
        started: float,
    ) -> RoleManifestResult:
        detected_roles: list[DetectedRole] = []
        constraints: list[SharedConstraint] = []
        response_hashes: list[str] = []
        step = self._max_window_lines - self._window_overlap_lines
        for start in range(0, len(lines), step):
            window = lines[start : start + self._max_window_lines]
            if not window:
                break
            outcome = self._run_ladder(
                _numbered_source(window, start_line=start + 1),
                context,
                validator=lambda manifest: self._merge_window_manifest(
                    manifest,
                    start=start,
                    window_size=len(window),
                    detected_roles=detected_roles,
                    constraints=constraints,
                ),
            )
            if outcome is None:
                failure = context.last_error or RuntimeError("Role manifest call ceiling reached")
                logger.warning(
                    "role_manifest_detect_large_source_failed category=%s error=%s",
                    _error_category(failure),
                    _safe_error_message(failure),
                )
                return self._fallback_single(lines, context=context, started=started, error=failure)
            _, (detected_roles, constraints) = outcome
            response_hashes.append(context.last_diagnostics.response_hash)
            if start + self._max_window_lines >= len(lines):
                break

        detected_roles.sort(key=lambda role: (role.start_line, role.end_line))
        if not detected_roles or len(detected_roles) > MAX_ROLES_PER_SOURCE:
            failure = ValueError("Merged manifest role count is invalid")
            logger.warning("role_manifest_merge_role_count_invalid count=%s", len(detected_roles))
            return self._fallback_single(lines, context=context, started=started, error=failure)
        normalized_roles = [role.model_copy(update={"index": index}) for index, role in enumerate(detected_roles, start=1)]
        classification = "single" if len(normalized_roles) == 1 else "multiple"
        merged = RoleManifest(
            classification=classification,
            role_count=len(normalized_roles),
            confidence=min(role.confidence for role in normalized_roles),
            shared_constraints=constraints,
            roles=normalized_roles,
        )
        try:
            requirements = self._validate_and_materialize(merged, lines)
        except ValueError as exc:
            logger.warning("role_manifest_merge_validation_failed error=%s", str(exc))
            context.last_error = exc
            return self._fallback_single(lines, context=context, started=started, error=exc)
        combined_hash = (
            hashlib.sha256("|".join(response_hashes).encode("utf-8")).hexdigest() if response_hashes else ""
        )
        return RoleManifestResult(
            status=classification,
            manifest=merged,
            requirements=requirements,
            inherited_constraints=tuple(constraints),
            diagnostics=self._diagnostics(context, started=started, response_hash=combined_hash),
        )

    def _validate_and_materialize(
        self,
        manifest: RoleManifest,
        lines: list[str],
    ) -> tuple[MaterializedRequirement, ...]:
        if manifest.classification == "uncertain":
            raise ValueError("Uncertain manifests cannot materialize requirements")
        expected_count = 1 if manifest.classification == "single" else manifest.role_count
        if manifest.classification == "multiple" and not 2 <= manifest.role_count <= MAX_ROLES_PER_SOURCE:
            raise ValueError("Multiple manifests require 2-10 roles")
        if manifest.role_count != expected_count or len(manifest.roles) != manifest.role_count:
            raise ValueError("Manifest role count does not match roles")
        if manifest.confidence < self._confidence_threshold:
            raise ValueError("Manifest confidence is below threshold")
        if [role.index for role in manifest.roles] != list(range(1, manifest.role_count + 1)):
            raise ValueError("Role indexes must be continuous")

        # For multiple-role manifests, prefer splitting roles by their verbatim start_snippet
        # over line numbers when every role supplies one that resolves cleanly -- this is the
        # only way to separate genuinely distinct roles that a flattened/line-less source
        # forced onto identical start_line/end_line values. Falls back to None (line-based
        # validation, unchanged) whenever the model didn't supply usable snippets.
        snippet_sources = (
            _resolve_roles_by_snippet(manifest.roles, lines) if manifest.classification == "multiple" else None
        )

        previous_end = 0
        requirements: list[MaterializedRequirement] = []
        for role in manifest.roles:
            if role.confidence < self._confidence_threshold:
                raise ValueError("Role confidence is below threshold")
            if manifest.classification == "single":
                # A single-role manifest has no other role to conflict with, so a model
                # that miscounts line spans on short/collapsed sources (e.g. an HTML email
                # whose whole body lands on one line) can be clamped to the real source
                # bounds instead of being rejected outright.
                role = role.model_copy(
                    update={
                        "start_line": max(1, min(role.start_line, len(lines))),
                        "end_line": max(1, min(role.end_line, len(lines))),
                    }
                )
            if snippet_sources is not None:
                bounded = snippet_sources[role.index]
                if not bounded:
                    raise ValueError("Role boundary contains no source evidence")
            else:
                if role.end_line < role.start_line or role.end_line > len(lines) or role.start_line <= previous_end:
                    raise ValueError("Role boundaries are invalid or overlapping")
                bounded = "\n".join(lines[role.start_line - 1 : role.end_line]).strip()
                if not bounded:
                    raise ValueError("Role boundary contains no source evidence")
                previous_end = role.end_line
            requirements.append(
                MaterializedRequirement(
                    index=role.index,
                    title_hint=role.title_hint.strip(),
                    requisition_id=role.requisition_id.strip(),
                    start_line=role.start_line,
                    end_line=role.end_line,
                    source_text=bounded,
                    requirement_key=_requirement_key(role, bounded),
                )
            )

        for constraint in list(manifest.shared_constraints):
            if constraint.end_line < constraint.start_line or constraint.end_line > len(lines):
                manifest.shared_constraints.remove(constraint)
                continue
            evidence = "\n".join(lines[constraint.start_line - 1 : constraint.end_line]).strip()
            if not evidence or constraint.value.casefold() not in evidence.casefold():
                manifest.shared_constraints.remove(constraint)
        return tuple(requirements)
