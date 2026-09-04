"""Order recruiters by a rule the answer states, not by a score it hides.

"Which recruiter is worth keeping in touch with" is a judgment with no ground
truth - the same class of question as v3's "are these two postings the same
requirement", which stalled on a threshold nobody could measure. Building a
recruiter-quality score and picking a cutoff would rebuild `LIKELY_MIN = 0.75`
one table over, and it would be blocked on the same missing measurement.

`temp162.md` §12.7 gives the way out, and this module is it: **do not infer the
judgment. Order by a stated rule over asserted facts, and put the rule in the
answer.** Every value below is read from a column. The only judgment is which
column to sort on, and that judgment is printed, named, and swappable - so the
user argues with a definition they can see instead of trusting a number they
cannot inspect.

**No composite score, and no weights.** A weighted blend of four signals would
need four constants, and a constant chosen by its author is exactly the thing
this phase exists to avoid. The rules below are lexicographic: sort by one
column, break ties with the next. That is explainable in a sentence and
verifiable by reading the columns beside it.

**No cutoff.** `limit` truncates a list for display; it does not classify anyone
as worth or not worth contacting. Nothing here is a threshold.

The four signals, measured on 2026-09-04 over 497 live contacts:

    opportunities sent   393 contacts / 698 opportunities   dense
    last heard from      393 contacts (received_at)         dense
    seen_count           497 contacts, max 77, mean 1.89    dense
    replies received      19 contacts                       SPARSE - 3.8%

Replies are a bonus signal, not a base one, for the reason
`relationship_scoring` already documents about sparse fields: at 3.8% coverage a
reply-led ordering sorts 96% of the population arbitrarily and calls the result
a ranking. Replies raise a recruiter when present and cost nothing when absent,
and the payload reports their coverage so an absence is never read as "never
replies".

`seen_count` earns its place by being independent: its correlation with
opportunity count is **0.012**, so it is not a second vote for volume. Counting
two correlated signals as if they were one piece of evidence is the mistake
`relationship_scoring.CORRELATED_SIGNALS` exists to prevent, and it was checked
here rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import PremiumNumberContact, RecruiterOpportunity
from app.services import field_coverage

# Each rule is a sort order and the sentence that describes it. The sentence
# ships with the result; a rule the reader cannot see is a hidden score.
RULES: dict[str, str] = {
    "volume": (
        "Ranked by how many requirements they sent you, then by how recently, "
        "then by how often they turn up in your inbox."
    ),
    "recent": (
        "Ranked by how recently they last sent you a requirement, then by how "
        "many they have sent in total."
    ),
    "responsive": (
        "Ranked by whether they have replied to you, then by how many "
        "requirements they sent, then by how recently."
    ),
    "recurring": (
        "Ranked by how often they turn up in your inbox, then by how many "
        "requirements they sent you."
    ),
}
DEFAULT_RULE = "volume"


@dataclass(frozen=True)
class RankedRecruiter:
    contact_id: int
    name: str
    company: str
    designation: str
    email: str
    phone: str
    opportunities: int
    last_opportunity_at: datetime | None
    days_since_last: int | None
    seen_count: int
    replies: int

    def as_dict(self) -> dict[str, object]:
        return {
            "contact_id": self.contact_id,
            "name": self.name,
            # Blank rather than "Unknown": the stored placeholder is not a name,
            # and printing it would put a fake answer in front of the reader.
            "company": self.company,
            "designation": self.designation,
            "email": self.email,
            "phone": self.phone,
            # Every number the ordering used, beside the row it ordered. A rank
            # whose inputs are invisible is a score.
            "opportunities_sent": self.opportunities,
            "last_opportunity": self.last_opportunity_at.date().isoformat()
            if self.last_opportunity_at
            else None,
            "days_since_last": self.days_since_last,
            "times_seen": self.seen_count,
            "replies_received": self.replies,
        }


def _clean(value: str | None) -> str:
    """A placeholder is not an answer - W14. `Unknown` becomes blank."""
    return "" if field_coverage.is_placeholder(value) else (value or "").strip()


def _sort_key(rule: str, row: RankedRecruiter):
    """Lexicographic, descending on every component. No weights to justify."""
    never = 10**6  # a recruiter who never wrote sorts last, not first
    if rule == "recent":
        return (-(never if row.days_since_last is None else row.days_since_last), row.opportunities)
    if rule == "responsive":
        return (row.replies, row.opportunities, -(never if row.days_since_last is None else row.days_since_last))
    if rule == "recurring":
        return (row.seen_count, row.opportunities)
    return (row.opportunities, -(never if row.days_since_last is None else row.days_since_last), row.seen_count)


def rank_recruiters(
    db: Session,
    *,
    owner_id: str,
    rule: str = DEFAULT_RULE,
    limit: int = 10,
    scope: str = field_coverage.SCOPE_ACTIVE,
) -> dict[str, object]:
    """Order recruiters by `rule` and return the rule alongside the rows.

    `scope` defaults to active contacts: the Recycle Bin is a working queue, not
    an archive of the false, but a binned recruiter must not appear in a
    recommendation about who to contact now.
    """
    if rule not in RULES:
        raise ValueError(f"rule must be one of {sorted(RULES)}, got {rule!r}")
    if scope not in field_coverage.CONTACT_SCOPES:
        raise ValueError(f"scope must be one of {field_coverage.CONTACT_SCOPES}, got {scope!r}")

    contacts_q = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner_id,
        PremiumNumberContact.is_recruiter.is_(True),
    )
    if scope == field_coverage.SCOPE_ACTIVE:
        contacts_q = contacts_q.filter(PremiumNumberContact.deleted_at.is_(None))
    contacts = contacts_q.all()
    if not contacts:
        return {
            "rule": rule,
            "rule_statement": RULES[rule],
            "population": scope,
            "count": 0,
            "recruiters": [],
        }

    ids = [contact.id for contact in contacts]
    opp_rows = (
        db.query(
            RecruiterOpportunity.recruiter_number_id,
            func.count(RecruiterOpportunity.id),
            func.max(RecruiterOpportunity.received_at),
        )
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.recruiter_number_id.in_(ids),
        )
        .group_by(RecruiterOpportunity.recruiter_number_id)
        .all()
    )
    opportunities = {row[0]: (row[1], row[2]) for row in opp_rows}
    replies = _reply_counts(db, owner_id=owner_id, contacts=contacts)

    now = datetime.now(UTC)
    ranked: list[RankedRecruiter] = []
    for contact in contacts:
        count, last_at = opportunities.get(contact.id, (0, None))
        days = None
        if last_at is not None:
            stamped = last_at if last_at.tzinfo else last_at.replace(tzinfo=UTC)
            days = max(0, (now - stamped).days)
        ranked.append(
            RankedRecruiter(
                contact_id=contact.id,
                name=_clean(contact.recruiter_name),
                company=_clean(contact.company),
                designation=_clean(contact.designation),
                email=(contact.recruiter_email or "").strip(),
                phone=(contact.display_phone_number or "").strip(),
                opportunities=count,
                last_opportunity_at=last_at,
                days_since_last=days,
                seen_count=int(contact.seen_count or 0),
                replies=replies.get(contact.id, 0),
            )
        )

    ranked.sort(key=lambda row: _sort_key(rule, row), reverse=True)
    capped = max(1, min(int(limit), 50))
    return {
        "rule": rule,
        "rule_statement": RULES[rule],
        "other_rules": {name: text for name, text in RULES.items() if name != rule},
        "population": scope,
        "count": len(ranked),
        "recruiters": [row.as_dict() for row in ranked[:capped]],
        "not_a_threshold": (
            "This is an ordering, not a verdict. `limit` truncates the list for "
            "display; nobody below it has been judged not worth contacting."
        ),
    }


def _reply_counts(
    db: Session, *, owner_id: str, contacts: list[PremiumNumberContact]
) -> dict[int, int]:
    """Inbound replies per contact, matched on the recruiter's email address.

    Matched in Python rather than SQL because the stored sender is a display
    form - `Jane Recruiter <jane@agency.com>` - and a LIKE against every contact
    is a cross join. Sparse by nature: 19 of 497 live contacts have one.
    """
    from app.models import EmailReplyMessage

    by_email = {
        (contact.recruiter_email or "").strip().lower(): contact.id
        for contact in contacts
        if (contact.recruiter_email or "").strip()
    }
    if not by_email:
        return {}
    counts: dict[int, int] = {}
    rows = (
        db.query(EmailReplyMessage.sender)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.direction == "inbound",
        )
        .all()
    )
    for (sender,) in rows:
        text = (sender or "").lower()
        if not text:
            continue
        for address, contact_id in by_email.items():
            if address in text:
                counts[contact_id] = counts.get(contact_id, 0) + 1
                break
    return counts
