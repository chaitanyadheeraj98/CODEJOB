"""Find everything the application already knows about one company.

§16.7 turns A and B, merged into one call. The budget an agent spends is spent on
round-trips (§16.8), so this returns the stored results *and* the nvoids query
that would find more - the model does not have to ask twice to offer the user a
choice.

**SQL narrows, Python decides.** Scanning 12,654 job descriptions in Python on
every query is too slow, so a broad `ILIKE` fetches candidates and word-bounded
matching then throws out the ones that only looked right. That is the same
recall-then-verify shape §16.0 concluded for nvoids itself, applied locally: the
cheap filter is allowed to over-return precisely because a strict one follows it.
The `ILIKE` is never the answer - it is what `Citi` matching *citizenship* looks
like, and 443 of its 455 hits die at the second step.

**Three populations, never merged.** What the application stored before this
search, what a search would add, and what a description merely mentions are
different kinds of fact, and an answer that blends them is wrong even when every
row in it is right.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import RecruiterEmail, RecruiterOpportunity
from app.services import company_mentions as CM
from app.services import field_coverage
from app.skill_taxonomy import normalize_taxonomy_text

# How many candidate rows the broad filter may return before the word-bounded
# pass runs. Generous: the second pass is what decides, and truncating the first
# would silently drop real matches.
CANDIDATE_LIMIT = 400


@dataclass
class CompanyHit:
    source: str            # "opportunity" | "email" | "nvoids"
    record_id: int
    title: str
    mention: CM.CompanyMention
    received_at: str | None = None
    posting_company: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload = {
            "source": self.source,
            "id": self.record_id,
            "title": self.title,
            "received_at": self.received_at,
            "posting_company": self.posting_company,
        }
        payload.update(self.mention.as_dict())
        return payload


@dataclass
class SearchResult:
    company: str
    hits: list[CompanyHit] = field(default_factory=list)
    scanned: int = 0
    rejected_by_word_boundary: int = 0


def _search_terms(company: str) -> list[str]:
    """The token a broad SQL filter should use.

    The longest word, because it is the most selective and because any row
    containing the whole name contains it. `Morgan Stanley` filters on `stanley`,
    not `morgan` - which is the token that dragged in *Morgan, Utah*.
    """
    tokens = [token for token in normalize_taxonomy_text(company).split() if len(token) > 2]
    if not tokens:
        tokens = normalize_taxonomy_text(company).split()
    return sorted(tokens, key=len, reverse=True)[:1]


def search(
    db: Session,
    company: str,
    *,
    owner_id: str,
    extra_aliases: tuple[str, ...] = (),
    limit: int = 25,
) -> dict[str, object]:
    """Every stored record that names `company`, labelled by what it proves."""
    company = (company or "").strip()
    if not company:
        return {"error": "A company name is required."}

    terms = _search_terms(company)
    if not terms:
        return {"error": f"'{company}' has no searchable token."}
    like = f"%{terms[0]}%"

    result = SearchResult(company=company)

    opportunities = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            or_(
                RecruiterOpportunity.end_client.ilike(like),
                RecruiterOpportunity.implementation_partner.ilike(like),
            ),
        )
        .limit(CANDIDATE_LIMIT)
        .all()
    )
    for row in opportunities:
        result.scanned += 1
        mention = CM.classify_mention(
            company,
            end_client_field=row.end_client,
            implementation_partner_field=row.implementation_partner,
            extra_aliases=extra_aliases,
        )
        if mention is None:
            result.rejected_by_word_boundary += 1
            continue
        result.hits.append(
            CompanyHit(
                source="opportunity",
                record_id=row.id,
                title=row.job_title or "",
                mention=mention,
                received_at=row.received_at.date().isoformat() if row.received_at else None,
            )
        )

    emails = (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == owner_id,
            or_(
                RecruiterEmail.end_client.ilike(like),
                RecruiterEmail.implementation_partner.ilike(like),
                RecruiterEmail.body.ilike(like),
            ),
        )
        .limit(CANDIDATE_LIMIT)
        .all()
    )
    for row in emails:
        result.scanned += 1
        mention = CM.classify_mention(
            company,
            end_client_field=row.end_client,
            implementation_partner_field=row.implementation_partner,
            body=row.body,
            extra_aliases=extra_aliases,
        )
        if mention is None:
            result.rejected_by_word_boundary += 1
            continue
        result.hits.append(
            CompanyHit(
                source="email",
                record_id=row.id,
                title=row.role or row.subject or "",
                mention=mention,
                received_at=row.gmail_received_at.date().isoformat() if row.gmail_received_at else None,
                posting_company=row.company or None,
            )
        )

    externals = (
        db.query(ExternalOpportunity)
        .filter(
            ExternalOpportunity.owner_id == owner_id,
            ExternalOpportunity.raw_body.ilike(like),
        )
        .limit(CANDIDATE_LIMIT)
        .all()
    )
    for row in externals:
        result.scanned += 1
        mention = CM.classify_mention(company, body=row.raw_body, extra_aliases=extra_aliases)
        if mention is None:
            result.rejected_by_word_boundary += 1
            continue
        result.hits.append(
            CompanyHit(
                source="nvoids",
                record_id=row.id,
                title=row.role or "",
                mention=mention,
                received_at=row.posted_at.date().isoformat() if row.posted_at else None,
                posting_company=row.company or None,
            )
        )

    # Strongest evidence first, so the rows that support a claim are not buried
    # under the ones that merely mention the name.
    order = {label: index for index, label in enumerate(CM.MENTION_STRENGTH)}
    result.hits.sort(key=lambda hit: (order.get(hit.mention.label, 99), hit.received_at or ""))

    by_evidence: dict[str, int] = {}
    for hit in result.hits:
        by_evidence[hit.mention.label] = by_evidence.get(hit.mention.label, 0) + 1
    confirmed = sum(1 for hit in result.hits if hit.mention.is_relationship_claim)

    payload: dict[str, object] = {
        "company": company,
        "count": len(result.hits),
        "results": [hit.as_dict() for hit in result.hits[: max(1, min(limit, 50))]],
        "by_evidence": by_evidence,
        "population": "already stored in the application",
        "summary": (
            f"{confirmed} record(s) name {company} in a field recorded for the purpose; "
            f"{len(result.hits) - confirmed} mention it in a description only."
        ),
        "field_coverage": field_coverage.coverage_for(
            db, ["end_client", "implementation_partner"], owner_id=owner_id
        ),
    }
    if result.rejected_by_word_boundary:
        payload["rejected_by_word_boundary"] = result.rejected_by_word_boundary
        payload["rejection_note"] = (
            f"{result.rejected_by_word_boundary} candidate row(s) contained the search "
            f"token but not the word '{company}' - the kind of match that makes 'Citi' "
            "find 'citizenship'. They are excluded."
        )
    if not result.hits:
        payload["nothing_found_note"] = (
            f"Nothing stored names {company}. That is not evidence that no such "
            "requirement exists - it means none has been recorded here."
        )
    return payload
