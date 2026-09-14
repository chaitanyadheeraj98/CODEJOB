"""Users and browser sessions, for multi-tenant sign-in.

Creates empty tables and nothing else. In particular it does **not** create a
row for the existing `default-owner`: there is no email or Google subject to
put in one, and `tenancy.owner_id()` falls back to `settings.owner_id` when no
session is present, so single-tenant behaviour continues unchanged while
`feature_auth_enabled` is off.

No `password_hash` column, by design. Access is decided by the Google OAuth
consent screen's test-user list; the application never holds a local
credential. See temp176 §8.1.

Index discipline as established by 0074: index only what is filtered on, never
a Text column. Here that is `owner_id`, `email` and `google_subject` (login
lookups), `is_admin` and `disabled_at` (admin listing and the sign-in check),
and on sessions `user_id`, `token_hash`, `expires_at` and `revoked_at` - every
one of which is read on the session-resolution path.

Replay-safe from the start, per 0076.

Revision ID: 20260926_0077
Revises: 20260925_0076
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260926_0077"
down_revision = "20260925_0076"
branch_labels = None
depends_on = None

USERS = "users"
SESSIONS = "user_sessions"


def _create_users() -> None:
    op.create_table(
        USERS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("google_subject", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner_id", name="ux_users_owner_id"),
        sa.UniqueConstraint("email", name="ux_users_email"),
        sa.UniqueConstraint("google_subject", name="ux_users_google_subject"),
    )
    op.create_index("ix_users_owner_id", USERS, ["owner_id"])
    op.create_index("ix_users_email", USERS, ["email"])
    op.create_index("ix_users_is_admin", USERS, ["is_admin"])
    op.create_index("ix_users_disabled_at", USERS, ["disabled_at"])


def _create_sessions() -> None:
    op.create_table(
        SESSIONS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("remote_ip", sa.String(length=64), nullable=False, server_default=""),
        # CASCADE and not RESTRICT: deleting an account must take its sessions
        # with it, or G3's purge trips over them the way it would on any
        # RESTRICT edge.
        sa.ForeignKeyConstraint(
            ["user_id"], [f"{USERS}.id"], name="fk_user_sessions_user", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="ux_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", SESSIONS, ["user_id"])
    op.create_index("ix_user_sessions_token_hash", SESSIONS, ["token_hash"])
    op.create_index("ix_user_sessions_expires_at", SESSIONS, ["expires_at"])
    op.create_index("ix_user_sessions_revoked_at", SESSIONS, ["revoked_at"])


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(USERS):
        _create_users()
    if not sa.inspect(op.get_bind()).has_table(SESSIONS):
        _create_sessions()


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(SESSIONS):
        op.drop_index("ix_user_sessions_revoked_at", table_name=SESSIONS)
        op.drop_index("ix_user_sessions_expires_at", table_name=SESSIONS)
        op.drop_index("ix_user_sessions_token_hash", table_name=SESSIONS)
        op.drop_index("ix_user_sessions_user_id", table_name=SESSIONS)
        op.drop_table(SESSIONS)
    if sa.inspect(op.get_bind()).has_table(USERS):
        op.drop_index("ix_users_disabled_at", table_name=USERS)
        op.drop_index("ix_users_is_admin", table_name=USERS)
        op.drop_index("ix_users_email", table_name=USERS)
        op.drop_index("ix_users_owner_id", table_name=USERS)
        op.drop_table(USERS)
