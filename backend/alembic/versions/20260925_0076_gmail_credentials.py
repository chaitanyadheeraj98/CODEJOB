"""Per-owner Gmail OAuth credentials, refresh token encrypted at rest.

Creates an empty table and nothing else. The one-time import of an existing
`./data/google_token.json` happens in `gmail_credential_service` on first read,
not here, for two reasons: a migration runs in containers that may not carry
the encryption key, and decryption logic inside a migration is neither testable
nor replayable.

Index discipline follows the 0074 docstring - index only what is filtered on.
That is `owner_id` (every lookup), `google_email` (Pub/Sub routes by mailbox
address) and `revoked_at` (checked on every credential read). Nothing else,
and no index on a Text column.

Replay-safe from the start rather than retrofitted: 0074 was not, and
`test_migration_0014` failed with "table gmail_labels already exists" until it
was guarded.

Revision ID: 20260925_0076
Revises: 20260924_0075
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_0076"
down_revision = "20260924_0075"
branch_labels = None
depends_on = None

_TABLE = "gmail_credentials"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_TABLE):
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("google_email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("google_subject", sa.String(length=64), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "token_uri",
            sa.String(length=255),
            nullable=False,
            server_default="https://oauth2.googleapis.com/token",
        ),
        sa.Column("scopes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # One live connection per owner. When a person eventually needs two
        # mailboxes this becomes a composite unique plus a selector, not a
        # redesign.
        sa.UniqueConstraint("owner_id", name="ux_gmail_credentials_owner"),
    )
    op.create_index("ix_gmail_credentials_owner_id", _TABLE, ["owner_id"])
    op.create_index("ix_gmail_credentials_google_email", _TABLE, ["google_email"])
    op.create_index("ix_gmail_credentials_revoked_at", _TABLE, ["revoked_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return
    # Dropping the table destroys every stored connection; users reconnect.
    # Acceptable because the legacy token file is renamed, never deleted, on
    # import, so at minimum the owner's own credential is recoverable.
    op.drop_index("ix_gmail_credentials_revoked_at", table_name=_TABLE)
    op.drop_index("ix_gmail_credentials_google_email", table_name=_TABLE)
    op.drop_index("ix_gmail_credentials_owner_id", table_name=_TABLE)
    op.drop_table(_TABLE)
