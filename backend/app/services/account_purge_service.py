"""G3 / §13: delete an account, exhaustively, or not at all.

**The sweep is generated from `Base.metadata`, never hand-written.** 69 of 71
tables carry `owner_id`; the other two reach an owner through a foreign key.
A hand-written list has two failure modes and both are silent: it misses a
table nobody remembered, or it deletes in the wrong order and stops halfway on
a constraint, leaving an account that is neither present nor gone.

Three properties do the work here.

**Reverse dependency order.** `Base.metadata.sorted_tables` lists parents
first, so reversed it lists children first. That is what makes the one
`RESTRICT` in this schema survivable:
`email_conversations.root_recruiter_email_id -> recruiter_emails` refuses to
let the mail go while a conversation still points at it, and reversed order
deletes the conversation first. 28 further foreign keys declare no rule at all,
which Postgres treats the same way; none of them were reasoned about
individually, and none of them need to be.

**Every table must resolve to a predicate.** A table that cannot be tied to an
owner is not skipped - it raises. A test walks the whole metadata and fails if
any table stops resolving, so a table added next year is covered the day it
appears rather than the day someone notices it was not.

**One transaction, and a rescan before commit.** The purge counts what it
deleted, then re-counts what remains for that owner across every table. A
non-zero answer aborts the transaction. A partially deleted account is worse
than either outcome it sits between, so this never commits one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Table, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.db import Base
from app.models import User
from app.services import account_service, gmail_credential_service

logger = logging.getLogger(__name__)


class PurgeIncomplete(RuntimeError):
    """Rows survived the sweep. The transaction is rolled back, not committed."""


@dataclass
class PurgeResult:
    owner_id: str
    purged_at: datetime
    google_revoked: bool
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(self.deleted.values())


def _owner_predicate(table: Table, owner_id: str) -> ColumnElement[bool] | None:
    """How this table's rows are tied to one account.

    Directly when it carries `owner_id`; otherwise through a foreign key to a
    table that does. Returns None when neither applies, which is a fact the
    caller must act on rather than ignore.
    """
    if "owner_id" in table.columns:
        return table.c.owner_id == owner_id

    for constraint in table.foreign_key_constraints:
        target = constraint.elements[0].column.table
        if "owner_id" not in target.columns:
            continue
        local = list(constraint.columns)[0]
        remote = constraint.elements[0].column
        return local.in_(select(remote).where(target.c.owner_id == owner_id))

    return None


def purge_order() -> list[Table]:
    """Children before parents. See the module docstring for the `RESTRICT`."""
    return list(reversed(Base.metadata.sorted_tables))


def unreachable_tables() -> list[str]:
    """Tables no generated predicate can tie to an owner.

    Empty for this schema. A test asserts it stays empty, which is what makes
    "exhaustive" a property of the code rather than a claim in a comment.
    """
    return [
        table.name for table in Base.metadata.sorted_tables
        if _owner_predicate(table, "probe") is None
    ]


def remaining_rows(db: Session, owner_id: str) -> dict[str, int]:
    """What is still here for this owner, table by table.

    Used as the post-purge assertion and, on its own, as the answer to "is this
    account really gone?"
    """
    surviving: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        predicate = _owner_predicate(table, owner_id)
        if predicate is None:
            continue
        count = db.execute(select(func.count()).select_from(table).where(predicate)).scalar() or 0
        if count:
            surviving[table.name] = count
    return surviving


def _revoke_at_google(db: Session, owner_id: str) -> bool:
    """Hand the grant back to Google, and report honestly whether it happened.

    `account_service` has a revoke helper already, but it swallows its own
    failures - fine where it is used, useless here, because a `google_revoked`
    that is always True is not a fact, it is decoration. This does the same
    work and returns what actually occurred.

    True also means "there was nothing to revoke": an account with no Google
    credential has no live grant to leave behind.
    """
    try:
        credentials = gmail_credential_service.get_credentials(db, owner_id)
    except Exception:
        logger.exception("account_purge_credential_lookup_failed owner=%s", owner_id)
        return False

    token = (credentials.refresh_token or credentials.token or "") if credentials else ""
    if not token:
        return True

    try:
        account_service.revoke_google_token(token)
        return True
    except Exception:
        # §13: record it, still purge, log loudly. Our copy of the credential
        # is about to go either way; the grant staying live is the user's to
        # remove from their Google account, and they cannot do that if this
        # silently pretends it succeeded.
        logger.exception("account_purge_google_revoke_failed owner=%s", owner_id)
        return False


def purge(db: Session, owner_id: str) -> PurgeResult:
    """Delete everything belonging to one account, or nothing.

    Order per §13: revoke at Google, then sessions and credentials and rows,
    then the `users` row - all of which the generated sweep covers, because
    every one of those tables is owner-scoped - then rescan and assert zero.

    The Google call is deliberately **outside** the transaction and before it.
    It is not rollback-able, and §13 is explicit: if revocation fails, record
    it, still purge, log loudly. Deleting our copy of a credential while the
    grant stays live on someone's Google account is the one outcome worse than
    failing outright, so it is attempted first and never silently skipped.
    """
    unreachable = unreachable_tables()
    if unreachable:
        raise PurgeIncomplete(
            f"no owner predicate for {unreachable}; refusing to purge a schema "
            "this module cannot account for"
        )

    google_revoked = _revoke_at_google(db, owner_id)

    result = PurgeResult(owner_id=owner_id, purged_at=datetime.now(UTC), google_revoked=google_revoked)

    for table in purge_order():
        predicate = _owner_predicate(table, owner_id)
        if predicate is None:  # pragma: no cover - guarded above
            continue
        deleted = db.execute(delete(table).where(predicate)).rowcount or 0
        if deleted:
            result.deleted[table.name] = deleted

    survivors = remaining_rows(db, owner_id)
    if survivors:
        db.rollback()
        raise PurgeIncomplete(f"rows survived the purge for {owner_id}: {survivors}")

    logger.info(
        "account_purged owner=%s tables=%s rows=%s google_revoked=%s",
        owner_id, len(result.deleted), result.rows, google_revoked,
    )
    return result


def accounts_due_for_purge(db: Session, *, now: datetime | None = None) -> list[User]:
    """Accounts whose recovery window has run out.

    The window is the product: G1 tells the user in as many words that an
    administrator can restore the account for 30 days. Restoring clears
    `deletion_requested_at`, so an account that came back is simply no longer
    in this list.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=account_service.RECOVERY_DAYS)
    return (
        db.query(User)
        .filter(User.deletion_requested_at.isnot(None), User.deletion_requested_at <= cutoff)
        .order_by(User.deletion_requested_at)
        .all()
    )


#: How many accounts one sweep will purge. A deletion is expensive and
#: irreversible; a cap means a surprise backlog drains over several ticks
#: instead of holding one transaction open across the whole table.
MAX_PER_SWEEP = 5


def purge_due_accounts(db: Session, *, now: datetime | None = None) -> list[PurgeResult]:
    """Purge every account whose recovery window has expired.

    Committed **per account**, not per sweep. Each purge is already
    all-or-nothing on its own; batching them into one transaction would mean
    one account's failure resurrects another account that was correctly
    deleted.

    This must run *outside* `tenancy.owner_scope`. The accounts it operates on
    are disabled by definition, and G1's `owner_scoped` guard skips disabled
    owners - so scheduling this as per-owner work would skip precisely the
    accounts it exists to remove.
    """
    results: list[PurgeResult] = []
    for user in accounts_due_for_purge(db, now=now)[:MAX_PER_SWEEP]:
        owner_id = user.owner_id
        try:
            results.append(purge(db, owner_id))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("account_purge_sweep_failed owner=%s", owner_id)
    return results

