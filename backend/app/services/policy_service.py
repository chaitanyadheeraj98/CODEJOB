from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypedDict, cast


class PolicyQuery(TypedDict):
    force_unread: bool
    include_labels: list[str]
    exclude_labels: list[str]
    date_mode: str


class PolicyRun(TypedDict):
    run_mode: str
    batch_limit: int
    dry_run: bool


class PolicyQualification(TypedDict):
    location_strictness: str
    score_threshold_override_enabled: bool
    score_threshold_override_value: float


class PolicyConfig(TypedDict):
    version: int
    query: PolicyQuery
    run: PolicyRun
    qualification: PolicyQualification


@dataclass(frozen=True)
class EffectiveRunInputs:
    query: str
    effective_query: str
    policy: PolicyConfig
    mail_date: str | None


def as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict):
        return cast(Mapping[str, object], value)
    return {}


def as_str(value: object, default: str) -> str:
    return str(value).strip() if value is not None else default


def as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def default_policy() -> PolicyConfig:
    return {
        "version": 1,
        "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "custom"},
        "run": {"run_mode": "all", "batch_limit": 20, "dry_run": False},
        "qualification": {
            "location_strictness": "balanced",
            "score_threshold_override_enabled": False,
            "score_threshold_override_value": 0.6,
        },
    }


def normalize_policy(raw_policy: object) -> PolicyConfig:
    default = default_policy()
    if not isinstance(raw_policy, Mapping):
        return default
    raw_map = as_mapping(raw_policy)
    query = as_mapping(raw_map.get("query"))
    run = as_mapping(raw_map.get("run"))
    qualification = as_mapping(raw_map.get("qualification"))

    date_mode = as_str(query.get("date_mode"), "custom")
    if date_mode not in {"custom", "any"}:
        date_mode = "custom"
    run_mode = as_str(run.get("run_mode"), "all")
    if run_mode not in {"all"}:
        run_mode = "all"
    batch_limit = max(1, min(as_int(run.get("batch_limit"), 20), 200))
    score_override = max(0.0, min(as_float(qualification.get("score_threshold_override_value"), 0.6), 1.0))
    strictness = as_str(qualification.get("location_strictness"), "balanced")
    if strictness not in {"lenient", "balanced", "strict"}:
        strictness = "balanced"

    return {
        "version": 1,
        "query": {
            "force_unread": bool(query.get("force_unread", True)),
            "include_labels": as_string_list(query.get("include_labels", [])),
            "exclude_labels": as_string_list(query.get("exclude_labels", [])),
            "date_mode": date_mode,
        },
        "run": {"run_mode": run_mode, "batch_limit": batch_limit, "dry_run": bool(run.get("dry_run", False))},
        "qualification": {
            "location_strictness": strictness,
            "score_threshold_override_enabled": bool(qualification.get("score_threshold_override_enabled", False)),
            "score_threshold_override_value": score_override,
        },
    }


def policy_profiles() -> dict[str, PolicyConfig]:
    return {
        "Aggressive": normalize_policy(
            {
                "version": 1,
                "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "any"},
                "run": {"run_mode": "all", "batch_limit": 100, "dry_run": False},
                "qualification": {
                    "location_strictness": "lenient",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.50,
                },
            }
        ),
        "Balanced": normalize_policy(default_policy()),
        "Strict": normalize_policy(
            {
                "version": 1,
                "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "custom"},
                "run": {"run_mode": "all", "batch_limit": 10, "dry_run": False},
                "qualification": {
                    "location_strictness": "strict",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.75,
                },
            }
        ),
    }


def selected_policy_profile(policy: PolicyConfig) -> str | None:
    normalized = normalize_policy(policy)
    for name, profile in policy_profiles().items():
        if normalized == profile:
            return name
    return None


def read_policy_from_settings(policy_json: str | None) -> PolicyConfig:
    if not policy_json:
        return default_policy()
    try:
        parsed = json.loads(policy_json)
    except json.JSONDecodeError:
        return default_policy()
    return normalize_policy(parsed)


def policy_threshold(default_threshold: float, policy: PolicyConfig) -> float:
    normalized = normalize_policy(policy)
    qualification = normalized["qualification"]
    if bool(qualification.get("score_threshold_override_enabled", False)):
        value = as_float(qualification.get("score_threshold_override_value", default_threshold), default_threshold)
        return max(0.0, min(value, 1.0))
    return default_threshold


def policy_batch_limit(policy: PolicyConfig, default_value: int = 20) -> int:
    normalized = normalize_policy(policy)
    value = as_int(normalized["run"].get("batch_limit", default_value), default_value)
    return max(1, min(value, 200))


def policy_dry_run(policy: PolicyConfig) -> bool:
    return bool(normalize_policy(policy)["run"].get("dry_run", False))


def compose_gmail_query(base_query: str, mail_date: str | None = None, policy: PolicyConfig | None = None) -> str:
    normalized = normalize_policy(policy or default_policy())
    query_section = normalized["query"]
    parts = [base_query.strip()]
    if bool(query_section.get("force_unread", True)):
        parts.append("is:unread")
    for label in as_string_list(query_section.get("include_labels", [])):
        parts.append(f"label:{label}")
    for label in as_string_list(query_section.get("exclude_labels", [])):
        parts.append(f"-label:{label}")
    date_mode = as_str(query_section.get("date_mode", "custom"), "custom")
    if mail_date and date_mode == "custom":
        selected = date.fromisoformat(mail_date)
        next_day = selected + timedelta(days=1)
        parts.append(f"after:{selected.strftime('%Y/%m/%d')}")
        parts.append(f"before:{next_day.strftime('%Y/%m/%d')}")
    return " ".join(part for part in parts if part)


def normalize_default_date_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in {"today", "off"}:
        return "today"
    return normalized


def effective_run_inputs(
    *,
    gmail_query: str | None,
    default_gmail_query: str | None,
    default_date_mode: str | None,
    policy_json: str | None,
    saved_mail_date: str | None,
    requested_mail_date: str | None = None,
    today_iso: str | None = None,
) -> EffectiveRunInputs:
    active_query = (gmail_query or "").strip()
    default_query = (default_gmail_query or "").strip()
    final_query = active_query or default_query or "is:unread in:inbox recruiter"

    explicit_mail_date = requested_mail_date or saved_mail_date
    effective_policy = read_policy_from_settings(policy_json)
    effective_mail_date = explicit_mail_date
    normalized_date_mode = normalize_default_date_mode(default_date_mode)
    if not effective_mail_date and normalized_date_mode == "today":
        effective_mail_date = today_iso or date.today().isoformat()
        effective_policy = normalize_policy(
            {
                **effective_policy,
                "query": {**effective_policy["query"], "date_mode": "custom"},
            }
        )

    return EffectiveRunInputs(
        query=final_query,
        mail_date=effective_mail_date,
        policy=effective_policy,
        effective_query=compose_gmail_query(final_query, effective_mail_date, effective_policy),
    )
