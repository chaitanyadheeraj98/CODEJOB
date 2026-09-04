"""How much of a field is actually populated, measured live, so an answer can say so.

A tool that returns three rows naming an end client hands the model an invitation
to describe the corpus. Ninety-six percent of the corpus is silent on that field,
and nothing in the payload says so. This module puts the number next to the data.

**Corpus-wide by default.** `coverage()` measures the whole table, never the
filtered result set. A subset is self-selected - "of the 4 rows I found, 4 name a
client" is 100% and tells the reader nothing about reliability. `subset_coverage()`
exists for the cases where the narrower figure genuinely adds something, and it
is labelled so it cannot be mistaken for the corpus figure or replace it.

**Zero coverage is a refusal, not a low number.** `prime_vendor` and
`employment_type` are empty on all 1,117 production rows. There is no honest
partial answer, so `availability()` returns `UNAVAILABLE` and the tool returns a
refusal payload rather than an empty list the model can narrate around. Leaving
that judgment to the prompt means it holds until the model wants it not to.

Classifications come from the `temp162.md` §12.2 buckets. They are a statement
about *what the field is*, not about today's numbers - a field does not become
usable because a backfill nudged it to 12%. Changing one is a decision, so it
lives here in source rather than in a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RecruiterOpportunity

# `temp162.md` §12.2. USABLE may carry a claim; NEEDS_NORMALIZATION may, with the
# caveat that its values are not yet reconciled; UNAVAILABLE may not, at all.
USABLE = "usable"
NEEDS_NORMALIZATION = "needs_normalization"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FieldPolicy:
    column: str
    label: str
    classification: str
    # Shown verbatim to the model when a field is UNAVAILABLE. States what is
    # true of the *system* - never "no prime vendors found", which is a claim
    # about the world that the empty column does not support.
    reason: str = ""
    # Values known to be the same thing but not yet linked by an approved alias.
    # Listed so the model reports them separately instead of silently summing.
    unconfirmed_aliases: tuple[tuple[str, ...], ...] = dataclass_field(default_factory=tuple)


OPPORTUNITY_FIELDS: dict[str, FieldPolicy] = {
    "job_title": FieldPolicy("job_title", "Job title", USABLE),
    "extracted_skills": FieldPolicy("extracted_skills", "Skills", USABLE),
    "status": FieldPolicy("status", "Status", USABLE),
    "work_mode": FieldPolicy("work_mode", "Work mode", USABLE),
    "visa_restrictions": FieldPolicy("visa_restrictions", "Visa restrictions", NEEDS_NORMALIZATION),
    # 98.9% populated, but ~22.6% of those hold a work mode or "unknown" rather
    # than a place. The raw percentage overstates it; see temp162.md §12.1.
    "location": FieldPolicy("location", "Location", NEEDS_NORMALIZATION),
    "domain": FieldPolicy("domain", "Domain", NEEDS_NORMALIZATION),
    "end_client": FieldPolicy(
        "end_client",
        "End client",
        NEEDS_NORMALIZATION,
        unconfirmed_aliases=(("American Express", "AMEX"),),
    ),
    "implementation_partner": FieldPolicy(
        "implementation_partner",
        "Implementation partner",
        UNAVAILABLE,
        reason=(
            "Recorded on under 2% of opportunities - too few to support any claim "
            "about which partners are active."
        ),
    ),
    "prime_vendor": FieldPolicy(
        "prime_vendor",
        "Prime vendor",
        UNAVAILABLE,
        reason=(
            "The column exists but nothing has ever written to it. This is a "
            "collection gap, not a finding: it does not mean there are no prime "
            "vendors."
        ),
    ),
    "employment_type": FieldPolicy(
        "employment_type",
        "Employment type",
        UNAVAILABLE,
        reason=(
            "The column exists but nothing has ever written to it, so C2C, W2 and "
            "full-time cannot be told apart. It does not mean no roles are C2C."
        ),
    ),
}


@dataclass(frozen=True)
class Coverage:
    field: str
    label: str
    classification: str
    populated: int
    total: int
    reason: str = ""

    @property
    def percent(self) -> float:
        return round(100.0 * self.populated / self.total, 1) if self.total else 0.0

    @property
    def may_carry_a_claim(self) -> bool:
        return self.classification != UNAVAILABLE and self.populated > 0

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "field": self.field,
            "populated": self.populated,
            "total": self.total,
            "percent": self.percent,
            "scope": "corpus",
            "classification": self.classification,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.classification == UNAVAILABLE:
            payload["may_answer_from_this_field"] = False
        return payload


def coverage(db: Session, field: str, *, owner_id: str) -> Coverage:
    """Corpus-wide coverage for one opportunity field. Measured live, every call."""
    policy = OPPORTUNITY_FIELDS[field]
    column = getattr(RecruiterOpportunity, policy.column)
    base = db.query(func.count()).select_from(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == owner_id
    )
    total = base.scalar() or 0
    populated = base.filter(column.isnot(None), column != "").scalar() or 0
    return Coverage(
        field=field,
        label=policy.label,
        classification=policy.classification,
        populated=populated,
        total=total,
        reason=policy.reason,
    )


def coverage_for(db: Session, fields: list[str], *, owner_id: str) -> dict[str, dict[str, object]]:
    """Corpus coverage for several fields, keyed by field name."""
    return {
        name: coverage(db, name, owner_id=owner_id).as_dict()
        for name in fields
        if name in OPPORTUNITY_FIELDS
    }


def subset_coverage(rows: list, field: str) -> dict[str, object]:
    """Coverage within an already-filtered set.

    **Never a substitute for `coverage()`.** A filtered set is self-selected, so
    this figure can read 100% over four rows drawn from a field that is 4%
    populated. `scope: "subset"` is on the payload so the model cannot present it
    as the reliability of the field.
    """
    policy = OPPORTUNITY_FIELDS[field]
    populated = sum(1 for row in rows if (getattr(row, policy.column, "") or "").strip())
    total = len(rows)
    return {
        "field": field,
        "populated": populated,
        "total": total,
        "percent": round(100.0 * populated / total, 1) if total else 0.0,
        "scope": "subset",
        "note": "Within these results only. Not the reliability of the field - see the corpus figure.",
    }


def availability(field: str) -> str:
    return OPPORTUNITY_FIELDS[field].classification if field in OPPORTUNITY_FIELDS else USABLE


def unavailable_result(field: str) -> dict[str, object] | None:
    """The refusal payload for a field no answer may rest on, or None.

    Returned *instead of* rows. An empty list invites the model to narrate an
    absence as a finding; this says plainly that the system never collected the
    data, and offers the question that can be answered instead.
    """
    policy = OPPORTUNITY_FIELDS.get(field)
    if policy is None or policy.classification != UNAVAILABLE:
        return None
    return {
        "unavailable": True,
        "field": field,
        "label": policy.label,
        "reason": policy.reason,
        "may_answer_from_this_field": False,
        "instruction": (
            f"Decline this question. Say that {policy.label.lower()} is not collected "
            "reliably enough to answer from, and say what is missing. Do not report "
            "the absence as a finding about the world, and do not substitute a "
            "different field without saying so."
        ),
    }


def unconfirmed_alias_note(db: Session, field: str, *, owner_id: str) -> dict[str, object] | None:
    """Values that are probably the same entity but have no approved alias.

    Reported so the model shows the counts separately and names the link as
    unconfirmed. Merging them here would be the alias table's job, and it does
    not exist yet - guessing at it in a tool response is how a wrong merge
    becomes invisible.
    """
    policy = OPPORTUNITY_FIELDS.get(field)
    if policy is None or not policy.unconfirmed_aliases:
        return None
    column = getattr(RecruiterOpportunity, policy.column)
    groups: list[dict[str, object]] = []
    for group in policy.unconfirmed_aliases:
        counts = {
            value: (
                db.query(func.count())
                .select_from(RecruiterOpportunity)
                .filter(RecruiterOpportunity.owner_id == owner_id, column == value)
                .scalar()
                or 0
            )
            for value in group
        }
        if sum(counts.values()) > 0 and sum(1 for n in counts.values() if n) > 1:
            groups.append({"values": counts, "status": "unconfirmed"})
    if not groups:
        return None
    return {
        "field": field,
        "groups": groups,
        "instruction": (
            "Report these counts separately. Say the records are treated as distinct "
            "because no approved alias links them, and that they are likely the same "
            "entity but unconfirmed. Do not sum them."
        ),
    }
