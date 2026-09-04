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

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models import RecruiterOpportunity

# W14. A placeholder is not an answer, and no schema can tell the two apart: a
# NOT NULL text column with a default is a machine for producing them. Counting
# them as populated overstated four fields in `premium_number_contacts` alone -
# `owner_name` reads 100% and is the literal "Unknown" on 434 of 497 rows.
#
# This is the fourth appearance of one defect (`temp157.md` §7.5.3), which is why
# it is fixed in the measure rather than at each call site. Compared lowercased
# and trimmed, so "Unknown", "unknown" and " N/A " all count as missing.
PLACEHOLDER_VALUES = frozenset(
    {"", "unknown", "n/a", "na", "none", "not specified", "unspecified", "tbd", "-", "--"}
)


def is_placeholder(value: str | None) -> bool:
    """True when a stored string carries no information."""
    return (value or "").strip().lower() in PLACEHOLDER_VALUES


def _is_text(column) -> bool:
    try:
        return column.type.python_type is str
    except (AttributeError, NotImplementedError):
        return False


def populated_filter(column):
    """The predicate for "this row actually answered".

    Non-text columns keep the plain NOT NULL test - a counter of 0 is a
    measurement, not a placeholder, and `lower(trim(...))` on an integer is an
    error rather than a nicety.
    """
    if not _is_text(column):
        return column.isnot(None)
    return and_(
        column.isnot(None),
        func.lower(func.trim(column)).notin_(sorted(PLACEHOLDER_VALUES)),
    )

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
    populated = base.filter(populated_filter(column)).scalar() or 0
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
    populated = sum(1 for row in rows if not is_placeholder(getattr(row, policy.column, "")))
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


def unconfirmed_alias_note(
    db: Session,
    field: str,
    *,
    owner_id: str,
    policies: dict[str, FieldPolicy] | None = None,
    model=None,
    active_only: bool = False,
) -> dict[str, object] | None:
    """Values that are probably the same entity but have no approved alias.

    Reported so the model shows the counts separately and names the link as
    unconfirmed. Merging them here would be the alias table's job, and it does
    not exist yet - guessing at it in a tool response is how a wrong merge
    becomes invisible.

    Defaults to opportunities; pass `policies` and `model` for another table.
    `active_only` applies the soft-delete filter contacts default to, so a
    warning about the live population is not counted over the Recycle Bin.
    """
    table = model if model is not None else RecruiterOpportunity
    lookup = policies if policies is not None else OPPORTUNITY_FIELDS
    policy = lookup.get(field)
    if policy is None or not policy.unconfirmed_aliases:
        return None
    column = getattr(table, policy.column)
    groups: list[dict[str, object]] = []
    for group in policy.unconfirmed_aliases:
        def _count(value: str) -> int:
            query = (
                db.query(func.count())
                .select_from(table)
                .filter(table.owner_id == owner_id, column == value)
            )
            if active_only and hasattr(table, "deleted_at"):
                query = query.filter(table.deleted_at.is_(None))
            return query.scalar() or 0

        counts = {value: _count(value) for value in group}
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

# --- Aggregates -----------------------------------------------------------
#
# A chart is a stronger claim than a list. A list of four rows looks like four
# rows; a trend line over four rows looks like a trend. So an aggregate declares
# the columns it reads, and the coverage of those columns travels in the
# provenance block the frontend already validates.


def column_coverage(db: Session, model, column_name: str, *, owner_id: str, label: str = "") -> dict[str, object]:
    """Corpus coverage of any owner-scoped column, for aggregates outside
    `RecruiterOpportunity`.

    Same rule as `coverage()`: measured over the whole table, never over the
    rows the aggregate happened to select.
    """
    column = getattr(model, column_name)
    base = db.query(func.count()).select_from(model).filter(model.owner_id == owner_id)
    total = base.scalar() or 0
    populated = base.filter(populated_filter(column)).scalar() or 0
    return {
        "field": f"{model.__tablename__}.{column_name}",
        "label": label or column_name.replace("_", " ").title(),
        "populated": populated,
        "total": total,
        "percent": round(100.0 * populated / total, 1) if total else 0.0,
        "scope": "corpus",
        # Whether the chart may be drawn without a partial-data warning. Decided
        # here so the renderer shows a verdict instead of inventing a threshold:
        # a UI that picks its own "sparse enough to warn" cutoff is a second
        # opinion on data quality, and the two drift.
        "complete": total > 0 and populated == total,
    }


def aggregate_refusal(fields: list[str], *, subject: str) -> dict[str, object] | None:
    """Refuse to build an aggregate over a field that cannot carry a claim.

    Returned instead of the chart or metric. `provenance` documents that the
    frontend renders nothing when provenance is missing rather than rendering
    with a warning, on the grounds that a chart with a caveat is still a chart.
    The same reasoning applies here: a trend line over a 1.6% column reads as a
    trend no matter what the caption says, so it is not drawn at all.
    """
    blocked = [name for name in fields if availability(name) == UNAVAILABLE]
    if not blocked:
        return None
    policies = [OPPORTUNITY_FIELDS[name] for name in blocked]
    return {
        "unavailable": True,
        "subject": subject,
        "blocked_fields": [
            {"field": policy.column, "label": policy.label, "reason": policy.reason}
            for policy in policies
        ],
        "may_answer_from_this_field": False,
        "instruction": (
            f"Do not present {subject} as a trend or a total. "
            + " ".join(policy.reason for policy in policies)
            + " Say the aggregate cannot be built and why. Do not describe the "
            "absence as a finding, and do not silently swap in another field."
        ),
    }


# --- Contacts (W13) -------------------------------------------------------
#
# `premium_number_contacts` was outside the coverage contract entirely, so
# recruiter answers carried no coverage, no caveats and no alias warnings. The
# classifications below are the `temp162.md` §15.1 buckets, measured after the
# W14 placeholder rule rather than before it - which is the whole reason §15.0
# had to correct an earlier reading of this table.

CONTACT_FIELDS: dict[str, FieldPolicy] = {
    "company": FieldPolicy(
        "company",
        "Company",
        NEEDS_NORMALIZATION,
        # Four surface forms of one firm live in this column:
        # "RPA Technology Inc", "RPA TECHNOLOGY INC", "RPATECHNOLOGY INC",
        # "Rpatechnologyinc". Case folding alone collapses 336 forms to 321;
        # "Horizonsoftech" and "Horizons of Tech" are 33 rows and need real
        # aliasing, which is W1-W3's job and not this module's.
        unconfirmed_aliases=(
            ("RPA Technology Inc", "RPA TECHNOLOGY INC", "RPATECHNOLOGY INC", "Rpatechnologyinc"),
            ("Horizonsoftech", "Horizons of Tech"),
            ("Vdart Inc", "VDART Inc"),
        ),
    ),
    "recruiter_name": FieldPolicy("recruiter_name", "Recruiter name", USABLE),
    "recruiter_email": FieldPolicy("recruiter_email", "Recruiter email", USABLE),
    "recruiter_email_domain": FieldPolicy("recruiter_email_domain", "Email domain", USABLE),
    "normalized_phone_number": FieldPolicy("normalized_phone_number", "Phone", USABLE),
    "display_phone_number": FieldPolicy("display_phone_number", "Phone", USABLE),
    # A counter, not a text field - genuinely 100%, max 77, mean 1.89.
    "seen_count": FieldPolicy("seen_count", "Times seen", USABLE),
    "designation": FieldPolicy(
        "designation",
        "Designation",
        NEEDS_NORMALIZATION,
        # 54.9% once "Unknown" stops counting. The 91 real values collapse to
        # about a dozen: Recruiter / Technical Recruiter / Sr. Technical
        # Recruiter / Senior Technical Recruiter are three spellings of one job.
    ),
    "owner_name": FieldPolicy(
        "owner_name",
        "Phone owner",
        UNAVAILABLE,
        reason=(
            "Recorded as \"Unknown\" on 434 of 497 live contacts. It reads as fully "
            "populated and carries information on 12.7% of them."
        ),
    ),
    "recruiter_verification_level": FieldPolicy(
        "recruiter_verification_level",
        "Verification",
        UNAVAILABLE,
        reason=(
            "Present on every contact, but only 19 of 497 are verified. The field "
            "is complete; the verification is not, so it cannot separate a trusted "
            "recruiter from an unchecked one."
        ),
    ),
    "linkedin_url": FieldPolicy(
        "linkedin_url", "LinkedIn", UNAVAILABLE,
        reason="Recorded on 11% of contacts - too few to survey.",
    ),
    "do_not_work_again": FieldPolicy(
        "do_not_work_again", "Do not work again", UNAVAILABLE,
        reason=(
            "Flagged on one contact, and no reason is recorded on any. The flag "
            "exists; the practice has not started. Absence is not endorsement."
        ),
    ),
    "is_favorite": FieldPolicy(
        "is_favorite", "Favourite", UNAVAILABLE,
        reason="Never used - zero contacts are marked.",
    ),
}

# The Recycle Bin is a working queue, not an archive of the false, so deleted
# contacts are excluded by default and reachable on request rather than dropped.
# A recruiter who was binned still posted the requirements they posted; what
# they cannot do is appear in a recommendation about who to contact now.
SCOPE_ACTIVE = "active"
SCOPE_ALL_TIME = "all_time"
CONTACT_SCOPES = (SCOPE_ACTIVE, SCOPE_ALL_TIME)


def contact_coverage(
    db: Session,
    field: str,
    *,
    owner_id: str,
    scope: str = SCOPE_ACTIVE,
) -> Coverage:
    """Corpus coverage for one contact field, placeholder-aware.

    `scope="active"` counts live contacts only - the default population for
    rankings, recommendations and company counts. `scope="all_time"` includes
    the 255 soft-deleted rows and exists for questions explicitly about history.
    """
    from app.models import PremiumNumberContact

    if scope not in CONTACT_SCOPES:
        raise ValueError(f"scope must be one of {CONTACT_SCOPES}, got {scope!r}")
    policy = CONTACT_FIELDS[field]
    column = getattr(PremiumNumberContact, policy.column)
    base = db.query(func.count()).select_from(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner_id
    )
    if scope == SCOPE_ACTIVE:
        base = base.filter(PremiumNumberContact.deleted_at.is_(None))
    total = base.scalar() or 0
    populated = base.filter(populated_filter(column)).scalar() or 0
    return Coverage(
        field=field,
        label=policy.label,
        classification=policy.classification,
        populated=populated,
        total=total,
        reason=policy.reason,
    )


def contact_coverage_for(
    db: Session,
    fields: list[str],
    *,
    owner_id: str,
    scope: str = SCOPE_ACTIVE,
) -> dict[str, dict[str, object]]:
    """Contact coverage for several fields, each tagged with the scope it counted."""
    result: dict[str, dict[str, object]] = {}
    for name in fields:
        if name not in CONTACT_FIELDS:
            continue
        payload = contact_coverage(db, name, owner_id=owner_id, scope=scope).as_dict()
        payload["population"] = scope
        result[name] = payload
    return result


def contact_unavailable_result(field: str) -> dict[str, object] | None:
    """The refusal payload for a contact field no answer may rest on."""
    policy = CONTACT_FIELDS.get(field)
    if policy is None or policy.classification != UNAVAILABLE:
        return None
    return {
        "unavailable": True,
        "field": field,
        "label": policy.label,
        "reason": policy.reason,
        "may_answer_from_this_field": False,
        "instruction": (
            f"Decline this question. Say that {policy.label.lower()} is not recorded "
            "reliably enough to answer from, and say what is missing. Do not report "
            "the absence as a finding about the world."
        ),
    }
