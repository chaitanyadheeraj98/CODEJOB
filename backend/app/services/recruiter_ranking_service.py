"""Which recruiter is most worth contacting about a given requirement.

This is a job-seeker's app. "Recommend a recruiter" means *which recruiter
should I approach about this requirement*, not which candidate to put forward,
and "placements" are the user's own applications reaching `submitted_to_client`
or beyond.

`compute_recruiter_reputation` already covers four of the six factors the spec
asks for - prior communication, responsiveness, recent activity, historical
placements. This module adds the two it does not (domain/technical focus and
associated companies) and a ranking wrapper. It does not write a second
reputation engine.

The one thing this module must get right is what it does when it knows nothing.
Production has 44 applications across **one** recruiter, out of 610. For 609 of
them there is no interaction history at all, so a rank derived from reputation
would be a rank derived from nothing. `compute_recruiter_reputation` already
labels anything under three outreaches `limited_history` and
`rank_opportunities_for_resume` substitutes a flat 0.5. That is not enough here:
this module says so **structurally**, in an evidence entry with
`match: "absent"` and in an assumption the user reads, rather than quietly
neutralising the factor and presenting a confident number.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.mcp_server.tools.provenance import EvidenceEntry
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
)
from app.services import entity_resolution_service as resolution
from app.services.application_intelligence_service import compute_recruiter_reputation
from app.services.role_taxonomy import LOCATION_ENTITY_TYPE, ROLE_ENTITY_TYPE
from app.skill_taxonomy import extract_taxonomy_skills

RECENCY_WINDOW_DAYS = 90

# Weights over the factors that exist for every recruiter. Reputation is
# deliberately small: it is computable for one recruiter in the entire corpus,
# and weighting it heavily would rank 609 recruiters on a factor none of them
# have.
FACTOR_WEIGHTS: dict[str, float] = {
    "skill_overlap": 0.30,
    "role_overlap": 0.15,
    "location_overlap": 0.10,
    "requirement_volume": 0.10,
    "recency": 0.15,
    "thread_responsiveness": 0.10,
    "reputation": 0.10,
}

LIMITED_HISTORY_ASSUMPTION = (
    "Ranked on requirement history and topic overlap only - you have no recorded "
    "outreach with this recruiter."
)


@dataclass(frozen=True)
class RecruiterFocus:
    recruiter_contact_id: int
    name: str
    top_skills: list[str]
    top_roles: list[str]
    top_locations: list[str]
    associated_companies: list[str]
    opportunity_count: int
    trailing_90d_count: int


@dataclass(frozen=True)
class RecruiterRanking:
    recruiter_contact_id: int
    name: str
    score: float
    history_label: str
    evidence: list[EvidenceEntry] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def _skills_of(rows: list[RecruiterOpportunity]) -> Counter:
    counter: Counter = Counter()
    for row in rows:
        text = f"{row.job_title or ''} {row.extracted_skills or ''}"
        counter.update(skill.id for skill in extract_taxonomy_skills(text))
    return counter


def compute_recruiter_focus(
    db: Session, *, owner_id: str, recruiter_contact_id: int
) -> RecruiterFocus:
    """What this recruiter actually works on, from 100%-populated columns only.

    Reading their own opportunity rows rather than their application history is
    what makes this work for all 610 recruiters instead of the one with
    applications.
    """
    rows = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.recruiter_number_id == int(recruiter_contact_id),
        )
        .all()
    )
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.id == int(recruiter_contact_id),
        )
        .one_or_none()
    )

    cutoff = datetime.now(UTC) - timedelta(days=RECENCY_WINDOW_DAYS)
    recent = 0
    for row in rows:
        received = row.received_at or row.created_at
        if received is None:
            continue
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        if received >= cutoff:
            recent += 1

    roles = Counter(str(row.job_title or "").strip() for row in rows if str(row.job_title or "").strip())
    locations = Counter(str(row.location or "").strip() for row in rows if str(row.location or "").strip())
    # The recruiter's own firm, plus any end client actually recorded. Absent
    # contributes nothing - it is never a penalty.
    companies = {str(contact.company).strip()} if contact and str(contact.company or "").strip() not in ("", "Unknown") else set()
    companies |= {str(row.end_client or "").strip() for row in rows if str(row.end_client or "").strip()}

    return RecruiterFocus(
        recruiter_contact_id=int(recruiter_contact_id),
        name=str(contact.recruiter_name) if contact else f"Recruiter {recruiter_contact_id}",
        top_skills=[skill for skill, _count in _skills_of(rows).most_common(8)],
        top_roles=[role for role, _count in roles.most_common(5)],
        top_locations=[location for location, _count in locations.most_common(5)],
        associated_companies=sorted(companies),
        opportunity_count=len(rows),
        trailing_90d_count=recent,
    )


def _thread_responsiveness(
    db: Session, *, owner_id: str, contact: PremiumNumberContact | None
) -> tuple[float, int]:
    """Share of this recruiter's threads that got a reply, and how many threads.

    Reads EmailConversation / EmailReplyMessage rather than
    OpportunityLifecycleEvent: that table's `actor` records which *kind* of
    agent acted and is 'system' on every row, so it cannot identify who
    communicated.
    """
    address = str(getattr(contact, "recruiter_email", "") or "").strip()
    if not address:
        return 0.0, 0
    email_ids = [
        int(row[0])
        for row in db.query(RecruiterEmail.id)
        .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.sender == address)
        .all()
    ]
    if not email_ids:
        return 0.0, 0
    conversations = (
        db.query(EmailConversation)
        .filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.root_recruiter_email_id.in_(email_ids),
        )
        .all()
    )
    if not conversations:
        return 0.0, 0
    replied = {
        int(row[0])
        for row in db.query(EmailReplyMessage.conversation_id)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.direction == "inbound",
            EmailReplyMessage.conversation_id.in_([int(item.id) for item in conversations]),
        )
        .distinct()
        .all()
    }
    return len(replied) / len(conversations), len(conversations)


def _overlap(left: list[str], right: list[str]) -> float:
    left_set, right_set = {item.lower() for item in left if item}, {item.lower() for item in right if item}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _entry(signal: str, left: str, right: str, *, match: str, weight: float, sub_score: float, source: str) -> EvidenceEntry:
    return EvidenceEntry(
        signal=signal, left_value=left, right_value=right, normalized_to="",
        match=match, weight=weight, sub_score=sub_score, source=source,
    )


def rank_recruiters_for_opportunity(
    db: Session, *, owner_id: str, opportunity_id: int, limit: int = 5
) -> list[RecruiterRanking]:
    """Rank recruiters by how well they fit this requirement, with reasons.

    Ties break on `recruiter_contact_id` so the order does not shuffle between
    calls - a ranking that reorders on refresh is not a ranking anybody can act
    on.
    """
    target = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.id == int(opportunity_id),
        )
        .one_or_none()
    )
    if target is None:
        # Out of scope and nonexistent give the same answer.
        raise LookupError("Requirement not found")

    target_skills = list(_skills_of([target]))
    target_roles = [str(target.job_title or "").strip()] if str(target.job_title or "").strip() else []
    target_locations = [str(target.location or "").strip()] if str(target.location or "").strip() else []

    contact_ids = [
        int(row[0])
        for row in db.query(RecruiterOpportunity.recruiter_number_id)
        .filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.recruiter_number_id.isnot(None))
        .distinct()
        .all()
    ]
    if not contact_ids:
        return []

    focuses = [
        compute_recruiter_focus(db, owner_id=owner_id, recruiter_contact_id=contact_id)
        for contact_id in contact_ids
    ]
    contacts = {
        int(row.id): row
        for row in db.query(PremiumNumberContact)
        .filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id.in_(contact_ids))
        .all()
    }
    max_volume = max((focus.opportunity_count for focus in focuses), default=0) or 1
    max_recent = max((focus.trailing_90d_count for focus in focuses), default=0) or 1

    # Canonical role and location, so "Sr. Java Developer" and "Java Developer"
    # are not treated as different topics.
    role_surfaces = list({*target_roles, *(role for focus in focuses for role in focus.top_roles)})
    location_surfaces = list({*target_locations, *(item for focus in focuses for item in focus.top_locations)})
    roles = resolution.resolve_many(db, owner_id=owner_id, entity_type=ROLE_ENTITY_TYPE, surfaces=role_surfaces)
    locations = resolution.resolve_many(db, owner_id=owner_id, entity_type=LOCATION_ENTITY_TYPE, surfaces=location_surfaces)

    def canonical(values: list[str], resolved: dict[str, resolution.ResolvedEntity]) -> list[str]:
        return [resolved[value].canonical if value in resolved else value for value in values]

    target_role_canonical = canonical(target_roles, roles)
    target_location_canonical = canonical(target_locations, locations)

    rankings: list[RecruiterRanking] = []
    for focus in focuses:
        reputation = compute_recruiter_reputation(
            db, owner_id=owner_id, recruiter_contact_id=focus.recruiter_contact_id
        )
        responsiveness, thread_count = _thread_responsiveness(
            db, owner_id=owner_id, contact=contacts.get(focus.recruiter_contact_id)
        )
        limited = reputation.history_label == "limited_history"

        subs: dict[str, float] = {
            "skill_overlap": _overlap(target_skills, focus.top_skills),
            "role_overlap": _overlap(target_role_canonical, canonical(focus.top_roles, roles)),
            "location_overlap": _overlap(target_location_canonical, canonical(focus.top_locations, locations)),
            "requirement_volume": focus.opportunity_count / max_volume,
            "recency": focus.trailing_90d_count / max_recent,
            "thread_responsiveness": responsiveness,
            # A recruiter with no recorded outreach scores zero here, and the
            # evidence entry below says so. Substituting a neutral 0.5 would
            # let an unknown recruiter outrank a measured one.
            "reputation": 0.0 if limited else min(1.0, reputation.replies_count / max(1, reputation.outreach_count)),
        }

        evidence = [
            _entry("skill_overlap", ", ".join(target_skills[:5]) or "—", ", ".join(focus.top_skills[:5]) or "—",
                   match="overlap" if focus.top_skills else "absent",
                   weight=FACTOR_WEIGHTS["skill_overlap"], sub_score=subs["skill_overlap"],
                   source="recruiter_opportunities"),
            _entry("role_overlap", ", ".join(target_role_canonical) or "—", ", ".join(focus.top_roles[:3]) or "—",
                   match="overlap" if focus.top_roles else "absent",
                   weight=FACTOR_WEIGHTS["role_overlap"], sub_score=subs["role_overlap"],
                   source="canonical_entity_taxonomy"),
            _entry("location_overlap", ", ".join(target_location_canonical) or "—", ", ".join(focus.top_locations[:3]) or "—",
                   match="overlap" if focus.top_locations else "absent",
                   weight=FACTOR_WEIGHTS["location_overlap"], sub_score=subs["location_overlap"],
                   source="canonical_entity_taxonomy"),
            _entry("requirement_volume", "", f"{focus.opportunity_count} requirements",
                   match="overlap", weight=FACTOR_WEIGHTS["requirement_volume"],
                   sub_score=subs["requirement_volume"], source="recruiter_opportunities"),
            _entry("recency", "", f"{focus.trailing_90d_count} in the last {RECENCY_WINDOW_DAYS} days",
                   match="overlap", weight=FACTOR_WEIGHTS["recency"],
                   sub_score=subs["recency"], source="recruiter_opportunities"),
            _entry("thread_responsiveness", "",
                   f"{thread_count} threads" if thread_count else "no email threads",
                   match="overlap" if thread_count else "absent",
                   weight=FACTOR_WEIGHTS["thread_responsiveness"],
                   sub_score=subs["thread_responsiveness"], source="email_conversations"),
            _entry("reputation", "",
                   "no recorded outreach" if limited else f"{reputation.replies_count}/{reputation.outreach_count} replies",
                   # Structural, not a neutral substitution: the user is told
                   # this factor could not be measured for this recruiter.
                   match="absent" if limited else "overlap",
                   weight=FACTOR_WEIGHTS["reputation"], sub_score=subs["reputation"],
                   source="application_intelligence_service"),
            _entry("associated_companies", "", ", ".join(focus.associated_companies[:3]) or "—",
                   match="overlap" if focus.associated_companies else "absent",
                   # Zero weight: recorded for the user to read, never scored.
                   # end_client is 6.7% populated, so scoring it would rank
                   # recruiters by extraction luck.
                   weight=0.0, sub_score=0.0, source="premium_number_contacts"),
        ]

        score = round(sum(entry.weight * entry.sub_score for entry in evidence), 6)
        assumptions = [LIMITED_HISTORY_ASSUMPTION] if limited else []
        if not thread_count:
            assumptions.append("No email threads with this recruiter, so responsiveness could not be measured.")
        rankings.append(RecruiterRanking(
            recruiter_contact_id=focus.recruiter_contact_id,
            name=focus.name,
            score=score,
            history_label=reputation.history_label,
            evidence=evidence,
            assumptions=assumptions,
        ))

    rankings.sort(key=lambda item: (-item.score, item.recruiter_contact_id))
    return rankings[: max(1, int(limit))]
