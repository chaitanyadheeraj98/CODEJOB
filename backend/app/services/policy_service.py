from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, TypedDict, cast


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
    draft_rules: "DraftRules"


RuleMode = Literal["ignore", "warn", "block"]


class DraftRule(TypedDict, total=False):
    mode: RuleMode


class AcceptedLocationRule(DraftRule, total=False):
    locations: list[str]


class MinimumSalaryRule(DraftRule, total=False):
    value: int | None


class MustHaveSkillsRule(DraftRule, total=False):
    skills: list[str]


class ScoreThresholdRule(DraftRule, total=False):
    value: float | None


class DraftRules(TypedDict):
    recruiter_like_gmail: DraftRule
    accepted_location: AcceptedLocationRule
    minimum_salary: MinimumSalaryRule
    must_have_skills: MustHaveSkillsRule
    score_threshold: ScoreThresholdRule
    f2f_non_texas: DraftRule
    unknown_location: DraftRule
    recipient_mapping: DraftRule


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


def default_draft_rules() -> DraftRules:
    return {
        "recruiter_like_gmail": {"mode": "block"},
        "accepted_location": {"mode": "block", "locations": []},
        "minimum_salary": {"mode": "block", "value": None},
        "must_have_skills": {"mode": "block", "skills": []},
        "score_threshold": {"mode": "block", "value": None},
        "f2f_non_texas": {"mode": "block"},
        "unknown_location": {"mode": "block"},
        "recipient_mapping": {"mode": "block"},
    }


def default_policy() -> PolicyConfig:
    return {
        "version": 2,
        "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "custom"},
        "run": {"run_mode": "all", "batch_limit": 20, "dry_run": False},
        "qualification": {
            "location_strictness": "balanced",
            "score_threshold_override_enabled": False,
            "score_threshold_override_value": 0.6,
            "draft_rules": default_draft_rules(),
        },
    }


def _normalize_rule_mode(value: object, default: RuleMode) -> RuleMode:
    normalized = as_str(value, default).lower()
    if normalized not in {"ignore", "warn", "block"}:
        return default
    return cast(RuleMode, normalized)


def _legacy_rule_mode(enabled: object, *, default: RuleMode = "block") -> RuleMode:
    if enabled is None:
        return default
    return "block" if bool(enabled) else "warn"


def normalize_draft_rules(
    raw_rules: object,
    *,
    legacy_filters: object | None = None,
) -> DraftRules:
    defaults = default_draft_rules()
    raw_rule_map = as_mapping(raw_rules)
    raw_legacy_filters = as_mapping(legacy_filters)
    legacy_mode_by_rule: dict[str, RuleMode] = {
        "recruiter_like_gmail": _legacy_rule_mode(raw_legacy_filters.get("recruiter_like_filter_enabled"), default="block"),
        "accepted_location": _legacy_rule_mode(raw_legacy_filters.get("accepted_location_filter_enabled"), default="block"),
        "minimum_salary": _legacy_rule_mode(raw_legacy_filters.get("minimum_salary_filter_enabled"), default="block"),
        "must_have_skills": _legacy_rule_mode(raw_legacy_filters.get("must_have_skills_filter_enabled"), default="block"),
        "score_threshold": _legacy_rule_mode(raw_legacy_filters.get("score_threshold_filter_enabled"), default="block"),
        "f2f_non_texas": _legacy_rule_mode(raw_legacy_filters.get("f2f_non_texas_filter_enabled"), default="block"),
        "unknown_location": _legacy_rule_mode(raw_legacy_filters.get("strict_unknown_location_filter_enabled"), default="block"),
        "recipient_mapping": _legacy_rule_mode(raw_legacy_filters.get("require_to_and_cc_before_draft_enabled"), default="block"),
    }

    accepted_location = as_mapping(raw_rule_map.get("accepted_location"))
    minimum_salary = as_mapping(raw_rule_map.get("minimum_salary"))
    must_have_skills = as_mapping(raw_rule_map.get("must_have_skills"))
    score_threshold = as_mapping(raw_rule_map.get("score_threshold"))

    minimum_salary_value_raw = minimum_salary.get("value")
    minimum_salary_value = None if minimum_salary_value_raw in {None, ""} else as_int(minimum_salary_value_raw, 0)
    score_threshold_value_raw = score_threshold.get("value")
    score_threshold_value = (
        None
        if score_threshold_value_raw in {None, ""}
        else max(0.0, min(as_float(score_threshold_value_raw, 0.6), 1.0))
    )

    return {
        "recruiter_like_gmail": {
            "mode": _normalize_rule_mode(
                as_mapping(raw_rule_map.get("recruiter_like_gmail")).get("mode"),
                legacy_mode_by_rule["recruiter_like_gmail"],
            )
        },
        "accepted_location": {
            "mode": _normalize_rule_mode(accepted_location.get("mode"), legacy_mode_by_rule["accepted_location"]),
            "locations": as_string_list(accepted_location.get("locations", defaults["accepted_location"]["locations"])),
        },
        "minimum_salary": {
            "mode": _normalize_rule_mode(minimum_salary.get("mode"), legacy_mode_by_rule["minimum_salary"]),
            "value": minimum_salary_value,
        },
        "must_have_skills": {
            "mode": _normalize_rule_mode(must_have_skills.get("mode"), legacy_mode_by_rule["must_have_skills"]),
            "skills": as_string_list(must_have_skills.get("skills", defaults["must_have_skills"]["skills"])),
        },
        "score_threshold": {
            "mode": _normalize_rule_mode(score_threshold.get("mode"), legacy_mode_by_rule["score_threshold"]),
            "value": score_threshold_value,
        },
        "f2f_non_texas": {
            "mode": _normalize_rule_mode(
                as_mapping(raw_rule_map.get("f2f_non_texas")).get("mode"),
                legacy_mode_by_rule["f2f_non_texas"],
            )
        },
        "unknown_location": {
            "mode": _normalize_rule_mode(
                as_mapping(raw_rule_map.get("unknown_location")).get("mode"),
                legacy_mode_by_rule["unknown_location"],
            )
        },
        "recipient_mapping": {
            "mode": _normalize_rule_mode(
                as_mapping(raw_rule_map.get("recipient_mapping")).get("mode"),
                legacy_mode_by_rule["recipient_mapping"],
            )
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
    raw_draft_rules = qualification.get("draft_rules")
    raw_draft_filters = qualification.get("draft_filters")

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
        "version": 2,
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
            "draft_rules": normalize_draft_rules(raw_draft_rules, legacy_filters=raw_draft_filters),
        },
    }


def policy_profiles() -> dict[str, PolicyConfig]:
    return {
        "Flexible Drafting": normalize_policy(
            {
                "version": 2,
                "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "any"},
                "run": {"run_mode": "all", "batch_limit": 100, "dry_run": False},
                "qualification": {
                    "location_strictness": "lenient",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.50,
                    "draft_rules": {
                        "recruiter_like_gmail": {"mode": "warn"},
                        "accepted_location": {"mode": "warn", "locations": []},
                        "minimum_salary": {"mode": "ignore", "value": None},
                        "must_have_skills": {"mode": "warn", "skills": []},
                        "score_threshold": {"mode": "warn", "value": 0.5},
                        "f2f_non_texas": {"mode": "warn"},
                        "unknown_location": {"mode": "warn"},
                        "recipient_mapping": {"mode": "warn"},
                    },
                },
            }
        ),
        "Balanced": normalize_policy(
            {
                "version": 2,
                "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "custom"},
                "run": {"run_mode": "all", "batch_limit": 20, "dry_run": False},
                "qualification": {
                    "location_strictness": "balanced",
                    "score_threshold_override_enabled": False,
                    "score_threshold_override_value": 0.6,
                    "draft_rules": {
                        "recruiter_like_gmail": {"mode": "block"},
                        "accepted_location": {"mode": "warn", "locations": []},
                        "minimum_salary": {"mode": "warn", "value": None},
                        "must_have_skills": {"mode": "warn", "skills": []},
                        "score_threshold": {"mode": "warn", "value": 0.6},
                        "f2f_non_texas": {"mode": "block"},
                        "unknown_location": {"mode": "warn"},
                        "recipient_mapping": {"mode": "block"},
                    },
                },
            }
        ),
        "Strict": normalize_policy(
            {
                "version": 2,
                "query": {"force_unread": True, "include_labels": [], "exclude_labels": [], "date_mode": "custom"},
                "run": {"run_mode": "all", "batch_limit": 10, "dry_run": False},
                "qualification": {
                    "location_strictness": "strict",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.75,
                    "draft_rules": {key: {"mode": "block"} for key in default_draft_rules().keys()},
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
    score_rule = score_threshold_rule(normalized)
    if score_rule.get("value") is not None:
        value = as_float(score_rule.get("value"), default_threshold)
        return max(0.0, min(value, 1.0))
    qualification = normalized["qualification"]
    if bool(qualification.get("score_threshold_override_enabled", False)):
        value = as_float(qualification.get("score_threshold_override_value", default_threshold), default_threshold)
        return max(0.0, min(value, 1.0))
    return default_threshold


def draft_rules(policy: PolicyConfig | Mapping[str, object] | None) -> DraftRules:
    normalized = normalize_policy(policy or default_policy())
    return cast(DraftRules, normalized["qualification"]["draft_rules"])


def draft_rule_mode(
    policy: PolicyConfig | Mapping[str, object] | None,
    rule_key: str,
    *,
    default: RuleMode = "block",
) -> RuleMode:
    rule = as_mapping(draft_rules(policy).get(rule_key, {}))
    return _normalize_rule_mode(rule.get("mode"), default)


def recruiter_like_rule_mode(policy: PolicyConfig | Mapping[str, object] | None) -> RuleMode:
    return draft_rule_mode(policy, "recruiter_like_gmail")


def recipient_mapping_rule_mode(policy: PolicyConfig | Mapping[str, object] | None) -> RuleMode:
    return draft_rule_mode(policy, "recipient_mapping")


def score_threshold_rule(policy: PolicyConfig | Mapping[str, object] | None) -> ScoreThresholdRule:
    return cast(ScoreThresholdRule, draft_rules(policy).get("score_threshold", {"mode": "block", "value": 0.6}))


def combine_rule_messages(messages: list[str]) -> str:
    cleaned = [str(message or "").strip() for message in messages if str(message or "").strip()]
    if not cleaned:
        return "hard_filters_passed"
    return f"warnings: {', '.join(cleaned)}"


def should_block_non_texas_f2f(
    parsed: Mapping[str, object],
    policy: PolicyConfig | Mapping[str, object] | None,
    *,
    f2f_blocked: bool,
    f2f_reason: str,
) -> tuple[bool, str]:
    if not f2f_blocked:
        return False, ""
    mode = draft_rule_mode(policy, "f2f_non_texas")
    if mode == "ignore":
        return False, ""
    if mode == "warn":
        return False, f2f_reason
    return True, f2f_reason


def should_block_unknown_location_under_strict(
    parsed: Mapping[str, object],
    policy: PolicyConfig | Mapping[str, object] | None,
) -> tuple[bool, str]:
    normalized = normalize_policy(policy or default_policy())
    qualification = normalized["qualification"]
    strictness = as_str(qualification.get("location_strictness", "balanced"), "balanced")
    if strictness != "strict":
        return False, ""
    mode = draft_rule_mode(normalized, "unknown_location")
    if mode == "ignore":
        return False, ""
    location_text = str(parsed.get("job_location_text", "")).strip().lower()
    if not location_text or location_text == "unknown":
        if mode == "warn":
            return False, "Location is unclear under strict location policy"
        return True, "Location is unclear under strict location policy"
    return False, ""


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
