"""Allow-listed distinct-value lookups behind GET /filter-options."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import case, func
from sqlalchemy.orm import Query, Session

from app.models import (
    Application,
    AppTSApplication,
    EmailConversation,
    GmailLabel,
    RecruiterEmail,
    RecruiterOpportunity,
)

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_QUERY_CHARS = 100

BaseQuery = Callable[[Session, str], Query]


# Source parents are excluded from every candidate bucket. A multi-requirement email
# is a container: run_orchestrator.py:222 deliberately skips the AI extractor when the
# manifest says "multiple", so `role` there holds the raw subject by design and the real
# roles live on the expanded children. Offering those subjects as job-title suggestions
# is what produced the "3 Requirements :: ..." entries in the picker.
#
# Deliberate asymmetry: list_candidates does NOT hide source parents, so a parent card
# stays visible in Needs Review while its role is no longer suggested. That is the safe
# direction - the picker offers fewer values than exist, never values that return zero
# rows (temp151 G14). Do not "fix" this by re-including them.
def _emails(state: str) -> BaseQuery:
    return lambda db, owner: db.query(RecruiterEmail).filter(
        RecruiterEmail.owner_id == owner,
        RecruiterEmail.state == state,
        RecruiterEmail.is_source_parent.is_(False),
    )


def _emails_joined_opportunity(state: str) -> BaseQuery:
    return lambda db, owner: (
        db.query(RecruiterEmail)
        .join(RecruiterOpportunity, RecruiterOpportunity.source_email_id == RecruiterEmail.id)
        .filter(
            RecruiterEmail.owner_id == owner,
            RecruiterEmail.state == state,
            RecruiterEmail.is_source_parent.is_(False),
            RecruiterOpportunity.owner_id == owner,
        )
    )


def _bookmarked() -> BaseQuery:
    return lambda db, owner: db.query(RecruiterEmail).filter(
        RecruiterEmail.owner_id == owner,
        RecruiterEmail.state == "needs_review",
        RecruiterEmail.marked_for_tracking.is_(True),
        RecruiterEmail.is_source_parent.is_(False),
    )


def _conversation_emails() -> BaseQuery:
    return lambda db, owner: (
        db.query(RecruiterEmail)
        .join(EmailConversation, EmailConversation.root_recruiter_email_id == RecruiterEmail.id)
        .filter(
            EmailConversation.owner_id == owner,
            RecruiterEmail.owner_id == owner,
        )
    )


def _opportunities() -> BaseQuery:
    return lambda db, owner: db.query(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == owner
    )


def _applications() -> BaseQuery:
    return lambda db, owner: db.query(Application).filter(
        Application.owner_id == owner,
        Application.deleted_at.is_(None),
    )


def _applications_joined_opportunity() -> BaseQuery:
    return lambda db, owner: (
        db.query(Application)
        .join(RecruiterOpportunity, RecruiterOpportunity.id == Application.recruiter_opportunity_id)
        .filter(
            Application.owner_id == owner,
            Application.deleted_at.is_(None),
            RecruiterOpportunity.owner_id == owner,
        )
    )


def _appts_applications() -> BaseQuery:
    return lambda db, owner: db.query(AppTSApplication).filter(
        AppTSApplication.owner_id == owner,
        AppTSApplication.deleted_at.is_(None),
    )


FILTER_OPTION_COLUMNS: dict[str, dict[str, tuple[object, BaseQuery]]] = {
    "gmail_labels": {
        "label": (GmailLabel.name, lambda db, owner: db.query(GmailLabel).filter(
            GmailLabel.owner_id == owner, GmailLabel.is_tracked.is_(True), GmailLabel.deleted_at.is_(None),
        )),
    },
    "needs_review": {
        "role": (RecruiterEmail.role, _emails("needs_review")),
        "location": (RecruiterEmail.location, _emails("needs_review")),
        "sender": (RecruiterEmail.sender, _emails("needs_review")),
        "interview_type": (RecruiterEmail.interview_type, _emails("needs_review")),
    },
    "failed": {
        "role": (RecruiterEmail.role, _emails("failed")),
        "location": (RecruiterEmail.location, _emails("failed")),
        "sender": (RecruiterEmail.sender, _emails("failed")),
    },
    "approved_sent": {
        "role": (RecruiterEmail.role, _emails("approved_sent")),
        "location": (RecruiterEmail.location, _emails("approved_sent")),
        "sender": (RecruiterEmail.sender, _emails("approved_sent")),
        "interview_type": (RecruiterEmail.interview_type, _emails("approved_sent")),
        "company": (
            RecruiterOpportunity.end_client,
            _emails_joined_opportunity("approved_sent"),
        ),
    },
    "appts_bookmarked": {
        "role": (RecruiterEmail.role, _bookmarked()),
        "location": (RecruiterEmail.location, _bookmarked()),
        "sender": (RecruiterEmail.sender, _bookmarked()),
        "interview_type": (RecruiterEmail.interview_type, _bookmarked()),
    },
    "inbox_conversations": {
        "role": (RecruiterEmail.role, _conversation_emails()),
        "location": (RecruiterEmail.location, _conversation_emails()),
        "interview_type": (RecruiterEmail.interview_type, _conversation_emails()),
        "recruiter": (RecruiterEmail.sender, _conversation_emails()),
    },
    "recruiter_opportunities": {
        "job_title": (RecruiterOpportunity.job_title, _opportunities()),
        "end_client": (RecruiterOpportunity.end_client, _opportunities()),
        "location": (RecruiterOpportunity.location, _opportunities()),
        "employment_type": (RecruiterOpportunity.employment_type, _opportunities()),
        "work_mode": (RecruiterOpportunity.work_mode, _opportunities()),
        "job_confidence": (RecruiterOpportunity.job_confidence, _opportunities()),
        "extension_likely": (RecruiterOpportunity.extension_likely, _opportunities()),
    },
    "applications": {
        "company": (Application.recruiter_company_snapshot, _applications()),
        "recruiter": (Application.recruiter_name_snapshot, _applications()),
        "role": (Application.resume_primary_role_snapshot, _applications()),
        "end_client": (
            RecruiterOpportunity.end_client,
            _applications_joined_opportunity(),
        ),
        "implementation_partner": (
            RecruiterOpportunity.implementation_partner,
            _applications_joined_opportunity(),
        ),
        "opportunity_domain": (
            RecruiterOpportunity.domain,
            _applications_joined_opportunity(),
        ),
    },
    "appts_applications": {
        "company": (AppTSApplication.recruiter_company_snapshot, _appts_applications()),
        "recruiter": (AppTSApplication.recruiter_name_snapshot, _appts_applications()),
        "end_client": (AppTSApplication.end_client_snapshot, _appts_applications()),
        "role": (AppTSApplication.job_title_snapshot, _appts_applications()),
    },
}
FILTER_OPTION_COLUMNS["resume_assets"] = FILTER_OPTION_COLUMNS["applications"]


def _escape_like(value: str) -> str:
    """Escape ILIKE wildcards. Backslash first, or it double-escapes the others."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def distinct_values(
    db: Session,
    owner_id: str,
    bucket: str,
    field: str,
    *,
    q: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[str]:
    """Most useful values first, not alphabetical.

    Alphabetical order plus a LIMIT returns the head of the alphabet, which is
    close to useless for a picker: typing "java" surfaced "2 Requirements :: ..."
    and "Advanced Java Concepts" while "Java Developer" (259 rows) never made the
    window, because digit- and A-prefixed values sort first. Ranking instead by
    where the match lands, then by how often the value actually occurs, then by
    brevity, puts the values a person is looking for at the top.

    Values are never rewritten - only reordered - so every suggestion stays a
    literal substring of its own rows and keeps matching the list endpoints.
    """
    entry = FILTER_OPTION_COLUMNS.get(bucket, {}).get(field)
    if entry is None:
        return []
    column, base = entry
    hits = func.count().label("hits")
    query = (
        base(db, owner_id)
        .with_entities(column.label("value"), hits)
        .filter(column.is_not(None), column != "")
    )

    # Rank 0 = value starts with the query, 1 = query starts a later word,
    # 2 = matches anywhere. ILIKE patterns (not regex) so the one escaping
    # scheme below covers every comparison.
    #
    # Only ordered by when there is a query. With no needle every row ranks the
    # same, and the constant this used to sort by rendered as `ORDER BY 0` -
    # which Postgres reads as an ordinal, not a value, and rejects with "ORDER BY
    # position 0 is not in select list". Every combobox 500'd the moment it was
    # opened without typing. SQLite evaluates it as a constant and sorts fine,
    # so the tests could not see it; only a Postgres request could.
    ordering: list[object] = [hits.desc(), func.length(column).asc(), column.asc()]
    if q and q.strip():
        needle = _escape_like(q.strip()[:MAX_QUERY_CHARS])
        query = query.filter(column.ilike(f"%{needle}%", escape="\\"))
        ordering.insert(0, case(
            (column.ilike(f"{needle}%", escape="\\"), 0),
            (column.ilike(f"% {needle}%", escape="\\"), 1),
            else_=2,
        ))

    rows = (
        query.group_by(column)
        .order_by(*ordering)
        .limit(max(1, min(limit, MAX_LIMIT)))
        .all()
    )
    return [row[0] for row in rows]
