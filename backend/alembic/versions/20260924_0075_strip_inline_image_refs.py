"""Strip cid: inline-image references from captured reply bodies.

`email_reply_messages.body` is *derived* display text - capture runs the raw
Gmail payload through `clean_html_text` / `extract_gmail_reply_body` before
storing it - so rewriting it here restores what the fixed extractor would have
produced, rather than destroying anything.

`recruiter_emails.body` is deliberately left alone even though ~1526 rows carry
the same references. That column is the persisted *source* of a captured email,
and this codebase keeps source separate from parse input on purpose (see
`prepare_gmail_parse_body`, whose docstring says it builds parser input
"without changing the persisted source"). A migration that edits source bodies
would destroy provenance for a cosmetic gain. The forward fix in
`document_extraction` means no new row in either table acquires one.

Replay-safe: the UPDATE is a no-op once no row matches, and the regex removes
nothing it has not already removed.

Revision ID: 20260924_0075
Revises: 20260923_0074
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "20260924_0075"
down_revision = "20260923_0074"
branch_labels = None
depends_on = None

# Kept in step with _INLINE_IMAGE_REF_PATTERN in app/parsing/document_extraction.py.
# Duplicated rather than imported: a migration must keep working when the
# application module it borrowed from is refactored years later.
_PATTERN = re.compile(r"""[\[(<]?\s*cid:[^\s\]\)>"']+\s*[\]\)>]?""", re.IGNORECASE)


def _clean(text: str) -> str:
    cleaned = _PATTERN.sub("", text)
    cleaned = re.sub(r"(?m)^[ \t]+$", "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("email_reply_messages"):
        return

    messages = sa.table(
        "email_reply_messages",
        sa.column("id", sa.Integer),
        sa.column("body", sa.Text),
    )
    rows = bind.execute(
        sa.select(messages.c.id, messages.c.body).where(messages.c.body.ilike("%cid:%"))
    ).fetchall()
    for row in rows:
        cleaned = _clean(row.body or "")
        if cleaned != (row.body or ""):
            bind.execute(
                sa.update(messages).where(messages.c.id == row.id).values(body=cleaned)
            )


def downgrade() -> None:
    """Not reversible: the removed text is not recorded anywhere.

    Deliberately a no-op rather than an error. The references carried no
    information - they addressed an image attachment that was never displayed -
    so there is nothing a downgrade could restore, and failing here would block
    an otherwise valid rollback of later migrations.
    """
