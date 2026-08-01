from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Literal

from instructor.core.exceptions import IncompleteOutputException, InstructorError, InstructorRetryException
from instructor.core.hooks import HookName, Hooks
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.deepseek_client import DeepSeekJSONError, DeepSeekJSONResult, build_deepseek_instructor_client
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


@dataclass(frozen=True)
class RoleManifestResult:
    status: str
    manifest: RoleManifest | None = None
    requirements: tuple[MaterializedRequirement, ...] = ()
    inherited_constraints: tuple[SharedConstraint, ...] = ()
    diagnostics: RoleManifestDiagnostics = field(default_factory=RoleManifestDiagnostics)
    error: str | None = None


ManifestProvider = Callable[[str, str], dict[str, object] | DeepSeekJSONResult]


SYSTEM_PROMPT = """Identify whether the supplied recruiting source contains one or multiple distinct job requirements.
Return JSON only with classification, role_count, confidence, shared_constraints, and roles.
Every role requires title_hint, requisition_id, start_line, end_line, confidence. Preserve the supplied line numbers.
Do not infer boundaries or constraints without direct source evidence."""


def _default_provider(system_prompt: str, user_prompt: str) -> DeepSeekJSONResult:
    client = build_deepseek_instructor_client(timeout_seconds=30.0)
    hooks = Hooks()
    attempt_count = 0

    def count_attempt(*args, **kwargs) -> None:
        nonlocal attempt_count
        attempt_count += 1

    hooks.on(HookName.COMPLETION_KWARGS, count_attempt)
    started = time.perf_counter()
    manifest, response = client.create_with_completion(
        model=settings.deepseek_model_fast or "deepseek-v4-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_model=RoleManifest,
        max_retries=1,
        temperature=0.0,
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
        model=str(getattr(response, "model", "") or settings.deepseek_model_fast or "deepseek-v4-flash"),
        finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        duration_ms=int((time.perf_counter() - started) * 1000),
        response_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        repair_attempted=attempt_count > 1,
    )


def _numbered_source(lines: list[str], start_line: int = 1) -> str:
    return "\n".join(f"{start_line + idx}: {line}" for idx, line in enumerate(lines))


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


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
        repair_attempts: int = 1,
        max_window_lines: int = 250,
        window_overlap_lines: int = 30,
    ) -> None:
        uses_default_provider = provider is None
        self._provider = provider or _default_provider
        self._confidence_threshold = confidence_threshold
        self._repair_attempts = 0 if uses_default_provider else max(0, repair_attempts)
        self._max_window_lines = max(20, max_window_lines)
        self._window_overlap_lines = max(1, min(window_overlap_lines, self._max_window_lines // 2))

    def detect(self, source_text: str) -> RoleManifestResult:
        lines = source_text.splitlines()
        if not any(line.strip() for line in lines):
            return RoleManifestResult(status="invalid", error="Source text is empty")
        if len(lines) > self._max_window_lines:
            return self._detect_large_source(lines)

        started = time.perf_counter()
        diagnostics = RoleManifestDiagnostics()
        last_error: Exception | None = None
        repair_content = ""
        for attempt in range(self._repair_attempts + 1):
            prompt = _numbered_source(lines)
            if attempt:
                prompt = (
                    "Repair the prior invalid manifest by returning a complete valid JSON object.\n"
                    f"Prior provider content (do not repeat commentary):\n{repair_content}\n\n{prompt}"
                )
            try:
                payload, provider_diagnostics = _provider_payload(self._provider(SYSTEM_PROMPT, prompt))
                manifest = RoleManifest.model_validate(payload)
                if manifest.classification == "uncertain":
                    elapsed = int((time.perf_counter() - started) * 1000)
                    diagnostics = replace(
                        provider_diagnostics,
                        duration_ms=elapsed,
                        repair_attempted=provider_diagnostics.repair_attempted or bool(attempt),
                    )
                    return RoleManifestResult(
                        status="uncertain",
                        manifest=manifest,
                        inherited_constraints=tuple(manifest.shared_constraints),
                        diagnostics=diagnostics,
                    )
                requirements = self._validate_and_materialize(manifest, lines)
                elapsed = int((time.perf_counter() - started) * 1000)
                diagnostics = replace(
                    provider_diagnostics,
                    duration_ms=elapsed,
                    repair_attempted=provider_diagnostics.repair_attempted or bool(attempt),
                )
                return RoleManifestResult(
                    status=manifest.classification,
                    manifest=manifest,
                    requirements=requirements,
                    inherited_constraints=tuple(manifest.shared_constraints),
                    diagnostics=diagnostics,
                )
            except (RuntimeError, ValueError, ValidationError, InstructorError) as exc:
                last_error = exc
                if isinstance(exc, DeepSeekJSONError):
                    repair_content = exc.raw_content

        elapsed = int((time.perf_counter() - started) * 1000)
        failure = last_error or RuntimeError("manifest failure")
        logger.warning(
            "role_manifest_detect_failed category=%s error=%s",
            _error_category(failure),
            _safe_error_message(failure),
        )
        return RoleManifestResult(
            status="invalid",
            diagnostics=_failure_diagnostics(
                failure,
                duration_ms=elapsed,
                repair_attempted=self._repair_attempts > 0 or int(getattr(failure, "n_attempts", 1) or 1) > 1,
            ),
            error=_safe_error_message(failure),
        )

    def _detect_large_source(self, lines: list[str]) -> RoleManifestResult:
        started = time.perf_counter()
        detected_roles: list[DetectedRole] = []
        constraints: list[SharedConstraint] = []
        response_hashes: list[str] = []
        step = self._max_window_lines - self._window_overlap_lines
        for start in range(0, len(lines), step):
            window = lines[start : start + self._max_window_lines]
            if not window:
                break
            try:
                payload, diagnostics = _provider_payload(
                    self._provider(SYSTEM_PROMPT, _numbered_source(window, start_line=start + 1))
                )
                manifest = RoleManifest.model_validate(payload)
            except (RuntimeError, ValueError, ValidationError, InstructorError) as exc:
                logger.warning(
                    "role_manifest_detect_large_source_failed category=%s error=%s",
                    _error_category(exc),
                    _safe_error_message(exc),
                )
                return RoleManifestResult(
                    status="invalid",
                    diagnostics=_failure_diagnostics(
                        exc,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                        repair_attempted=int(getattr(exc, "n_attempts", 1) or 1) > 1,
                    ),
                    error=_safe_error_message(exc),
                )
            if manifest.classification == "uncertain" or manifest.confidence < self._confidence_threshold:
                elapsed = int((time.perf_counter() - started) * 1000)
                logger.warning("role_manifest_detect_large_source_uncertain duration_ms=%s", elapsed)
                return RoleManifestResult(
                    status="uncertain",
                    manifest=manifest,
                    error="A source window was uncertain",
                    diagnostics=RoleManifestDiagnostics(duration_ms=elapsed),
                )
            response_hashes.append(diagnostics.response_hash)
            for role in manifest.roles:
                # Identity (title/requisition) match takes priority over boundary overlap:
                # a duplicate role reported in an overlap window can carry a corrupted
                # start/end line (e.g. relative-to-window instead of absolute), which
                # would otherwise falsely "overlap" an unrelated, already-detected role.
                duplicate_index: int | None = None
                for index, existing in enumerate(detected_roles):
                    same_requisition = bool(
                        role.requisition_id
                        and existing.requisition_id
                        and role.requisition_id.casefold() == existing.requisition_id.casefold()
                    )
                    same_title = _normalize_title(role.title_hint) == _normalize_title(existing.title_hint)
                    if same_requisition or same_title:
                        duplicate_index = index
                        break
                if duplicate_index is None:
                    conflicting = any(
                        role.start_line <= existing.end_line and existing.start_line <= role.end_line
                        for existing in detected_roles
                    )
                    if conflicting:
                        elapsed = int((time.perf_counter() - started) * 1000)
                        logger.warning("role_manifest_detect_large_source_conflict duration_ms=%s", elapsed)
                        return RoleManifestResult(
                            status="invalid",
                            error="Conflicting overlapping window detections",
                            diagnostics=RoleManifestDiagnostics(duration_ms=elapsed),
                        )
                role_boundary_valid = not (role.start_line < start + 1 or role.end_line > start + len(window))
                if duplicate_index is None:
                    if not role_boundary_valid:
                        elapsed = int((time.perf_counter() - started) * 1000)
                        logger.warning("role_manifest_detect_large_source_boundary_invalid duration_ms=%s", elapsed)
                        return RoleManifestResult(
                            status="invalid",
                            error="Window role boundary is outside its source window",
                            diagnostics=RoleManifestDiagnostics(duration_ms=elapsed),
                        )
                    detected_roles.append(role)
                else:
                    existing = detected_roles[duplicate_index]
                    detected_roles[duplicate_index] = DetectedRole(
                        index=existing.index,
                        title_hint=existing.title_hint,
                        requisition_id=existing.requisition_id or role.requisition_id,
                        start_line=min(existing.start_line, role.start_line) if role_boundary_valid else existing.start_line,
                        end_line=max(existing.end_line, role.end_line) if role_boundary_valid else existing.end_line,
                        confidence=max(existing.confidence, role.confidence),
                    )
            for constraint in manifest.shared_constraints:
                key = (constraint.type, constraint.value.casefold(), constraint.start_line, constraint.end_line)
                if key not in {(item.type, item.value.casefold(), item.start_line, item.end_line) for item in constraints}:
                    constraints.append(constraint)
            if start + self._max_window_lines >= len(lines):
                break

        detected_roles.sort(key=lambda role: (role.start_line, role.end_line))
        if not detected_roles or len(detected_roles) > MAX_ROLES_PER_SOURCE:
            elapsed = int((time.perf_counter() - started) * 1000)
            logger.warning("role_manifest_merge_role_count_invalid count=%s duration_ms=%s", len(detected_roles), elapsed)
            return RoleManifestResult(
                status="invalid",
                error="Merged manifest role count is invalid",
                diagnostics=RoleManifestDiagnostics(duration_ms=elapsed),
            )
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
            return RoleManifestResult(status="invalid", manifest=merged, error=str(exc))
        combined_hash = hashlib.sha256("|".join(response_hashes).encode("utf-8")).hexdigest()
        return RoleManifestResult(
            status=classification,
            manifest=merged,
            requirements=requirements,
            inherited_constraints=tuple(constraints),
            diagnostics=RoleManifestDiagnostics(
                duration_ms=int((time.perf_counter() - started) * 1000),
                response_hash=combined_hash,
            ),
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

        previous_end = 0
        requirements: list[MaterializedRequirement] = []
        for role in manifest.roles:
            if role.confidence < self._confidence_threshold:
                raise ValueError("Role confidence is below threshold")
            if role.end_line < role.start_line or role.end_line > len(lines) or role.start_line <= previous_end:
                raise ValueError("Role boundaries are invalid or overlapping")
            bounded = "\n".join(lines[role.start_line - 1 : role.end_line]).strip()
            if not bounded:
                raise ValueError("Role boundary contains no source evidence")
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
            previous_end = role.end_line

        for constraint in list(manifest.shared_constraints):
            if constraint.end_line < constraint.start_line or constraint.end_line > len(lines):
                manifest.shared_constraints.remove(constraint)
                continue
            evidence = "\n".join(lines[constraint.start_line - 1 : constraint.end_line]).strip()
            if not evidence or constraint.value.casefold() not in evidence.casefold():
                manifest.shared_constraints.remove(constraint)
        return tuple(requirements)
