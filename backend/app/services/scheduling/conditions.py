"""Whitelisted condition predicates.

**Conditions are evaluated by this code, never by the model.** The model
translates a phrase into a predicate name and its parameters; `evaluate`
dispatches on a name in `PREDICATES` and raises on anything else. There is no
`eval`, no expression language, and no user-supplied SQL fragment anywhere in
this module.

Three of the four predicates already shipped as literals inside
`generate_reminder_sweep_suggestions` - "no reply in 3 business days", "no
update in 6 business days", "no activity in 21 days". W10's work is
parameterizing `N` and generalizing the subject, not designing an evaluator.
The defaults below are those same literals, because someone already reasoned
about them.

A note on subject types the plan got wrong: `resolve_record_reference` resolves
`contact`, `opportunity` and `candidate` - three kinds, and `application` is not
among them. So the vocabulary here is the union of what the user can name and
what the predicates can actually read, and each predicate declares which
subjects it supports rather than pretending to support all of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    Application,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    utc_now,
)
from app.services.evidence import EvidenceEntry

logger = logging.getLogger(__name__)

PREDICATES = (
    "no_reply_for_days",
    "status_unchanged_for_days",
    "date_reached",
    "count_threshold",
)

SUBJECT_TYPES = ("application", "opportunity", "candidate", "contact")

# Which subjects each predicate can actually read. A predicate asked about a
# subject it cannot read is refused, not approximated: a monitor that silently
# watches something adjacent to what was asked is worse than no monitor.
PREDICATE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "no_reply_for_days": ("application",),
    "status_unchanged_for_days": ("application",),
    "date_reached": SUBJECT_TYPES,
    "count_threshold": SUBJECT_TYPES,
}

# The shipped literals, carried forward as defaults.
DEFAULT_DAYS: dict[str, int] = {
    "no_reply_for_days": 3,
    "status_unchanged_for_days": 21,
}

PREDICATE_DESCRIPTIONS: dict[str, str] = {
    "no_reply_for_days": "an application has had no reply for N business days",
    "status_unchanged_for_days": "an application's status has not changed for N days",
    "date_reached": "a given date has arrived",
    "count_threshold": "the number of matching records reaches N",
}

MAX_SUBJECTS_REPORTED = 25


class UnknownPredicate(ValueError):
    """A predicate outside the whitelist, or one that cannot read this subject."""


@dataclass(frozen=True)
class ConditionResult:
    fired: bool
    subject_ids: list[str] = field(default_factory=list)
    evidence: list[EvidenceEntry] = field(default_factory=list)
    checked: int = 0
    truncated: bool = False


def _business_days_ago(now: datetime, days: int) -> datetime:
    """The same weekday arithmetic the shipped rules use."""
    value = now
    remaining = max(0, int(days))
    while remaining:
        value -= timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def _observation(signal: str, observed: str, threshold: str, subject: str) -> EvidenceEntry:
    """One observation, in v3's evidence shape so the shipped panel renders it.

    `weight` and `sub_score` carry no information for a threshold check - this
    is a labelled observation, not a scoring contribution - so both are 1.0 and
    the meaningful fields are signal, the two values, and source.
    """
    return EvidenceEntry(
        signal=signal,
        left_value=observed,
        right_value=threshold,
        normalized_to=subject,
        match="exact",
        weight=1.0,
        sub_score=1.0,
        source="condition_monitor",
    )


def describe_predicates() -> list[dict[str, str]]:
    """The supported predicates in plain language, for a refusal payload."""
    return [
        {
            "predicate": name,
            "description": PREDICATE_DESCRIPTIONS[name],
            "subjects": ", ".join(PREDICATE_SUBJECTS[name]),
            "default_days": str(DEFAULT_DAYS.get(name, "")),
        }
        for name in PREDICATES
    ]


def _validated(condition: dict) -> tuple[str, str, dict]:
    predicate = str(condition.get("predicate") or "").strip()
    if predicate not in PREDICATES:
        raise UnknownPredicate(f"Unknown predicate {predicate!r}")
    subject_type = str(condition.get("subject_type") or "application").strip()
    if subject_type not in PREDICATE_SUBJECTS[predicate]:
        raise UnknownPredicate(
            f"Predicate {predicate!r} cannot read subject {subject_type!r}; "
            f"it supports: {', '.join(PREDICATE_SUBJECTS[predicate])}"
        )
    return predicate, subject_type, condition


def evaluate(db: Session, *, owner_id: str, condition: dict, now: datetime | None = None) -> ConditionResult:
    """Evaluate one whitelisted predicate. Raises UnknownPredicate otherwise."""
    predicate, subject_type, params = _validated(condition)
    moment = now or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    if predicate == "no_reply_for_days":
        return _no_reply_for_days(db, owner_id=owner_id, params=params, now=moment)
    if predicate == "status_unchanged_for_days":
        return _status_unchanged_for_days(db, owner_id=owner_id, params=params, now=moment)
    if predicate == "date_reached":
        return _date_reached(params=params, now=moment)
    return _count_threshold(db, owner_id=owner_id, subject_type=subject_type, params=params)


def _live_applications(db: Session, owner_id: str):
    return db.query(Application).filter(
        Application.owner_id == owner_id,
        Application.deleted_at.is_(None),
        Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
    )


def _no_reply_for_days(db: Session, *, owner_id: str, params: dict, now: datetime) -> ConditionResult:
    days = max(1, int(params.get("days") or DEFAULT_DAYS["no_reply_for_days"]))
    cutoff = _business_days_ago(now, days)
    query = _live_applications(db, owner_id).filter(
        Application.next_action_at.is_(None),
        Application.status_changed_at <= cutoff,
    )
    status = str(params.get("status") or "").strip()
    if status:
        query = query.filter(Application.status == status)
    rows = query.all()
    return _result_from(rows, signal="no_reply_for_days", threshold=f"{days} business days",
                        value=lambda row: row.status_changed_at, now=now)


def _status_unchanged_for_days(db: Session, *, owner_id: str, params: dict, now: datetime) -> ConditionResult:
    days = max(1, int(params.get("days") or DEFAULT_DAYS["status_unchanged_for_days"]))
    cutoff = now - timedelta(days=days)
    rows = _live_applications(db, owner_id).filter(Application.status_changed_at <= cutoff).all()
    return _result_from(rows, signal="status_unchanged_for_days", threshold=f"{days} days",
                        value=lambda row: row.status_changed_at, now=now)


def _result_from(rows, *, signal: str, threshold: str, value, now: datetime) -> ConditionResult:
    reported = rows[:MAX_SUBJECTS_REPORTED]
    evidence = []
    for row in reported:
        observed = value(row)
        stamp = observed.date().isoformat() if observed else "never"
        elapsed = (now - observed).days if observed else None
        evidence.append(
            _observation(
                signal,
                f"since {stamp}" + (f", {elapsed} days" if elapsed is not None else ""),
                threshold,
                f"{row.job_title_snapshot or 'Application'} #{row.id}",
            )
        )
    return ConditionResult(
        fired=bool(rows),
        subject_ids=[str(row.id) for row in rows],
        evidence=evidence,
        checked=len(rows),
        truncated=len(rows) > len(reported),
    )


def _date_reached(*, params: dict, now: datetime) -> ConditionResult:
    raw = str(params.get("date") or "").strip()
    if not raw:
        raise UnknownPredicate("date_reached needs a date")
    try:
        target = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise UnknownPredicate(f"Not a date this system can read: {raw!r}") from exc
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    fired = now >= target
    return ConditionResult(
        fired=fired,
        subject_ids=[],
        evidence=[
            _observation(
                "date_reached",
                now.date().isoformat(),
                target.date().isoformat(),
                "clock",
            )
        ],
        checked=1,
    )


_COUNT_MODELS = {
    "application": Application,
    "opportunity": RecruiterOpportunity,
    "candidate": RecruiterEmail,
    "contact": PremiumNumberContact,
}


def _count_threshold(db: Session, *, owner_id: str, subject_type: str, params: dict) -> ConditionResult:
    threshold = max(1, int(params.get("threshold") or 1))
    model = _COUNT_MODELS[subject_type]
    query = db.query(model).filter(model.owner_id == owner_id)
    if hasattr(model, "deleted_at"):
        query = query.filter(model.deleted_at.is_(None))
    status = str(params.get("status") or "").strip()
    if status and hasattr(model, "status"):
        query = query.filter(model.status == status)
    total = query.count()
    return ConditionResult(
        fired=total >= threshold,
        subject_ids=[],
        evidence=[
            _observation(
                "count_threshold",
                f"{total} {subject_type}(s)" + (f" with status {status}" if status else ""),
                f"at least {threshold}",
                subject_type,
            )
        ],
        checked=total,
    )
