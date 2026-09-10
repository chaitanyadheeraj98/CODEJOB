"""Gmail label tracking: label catalog, tracked threads, recruiter watches.

Index safety, restated because this migration runs on backend boot via
`alembic upgrade head` and migration 0051 took production down with an index on
an unbounded Text column:

Indexed here - every one a bounded String, an integer or a datetime:
    owner_id, label_type, is_tracked, external_thread_id, conversation_id,
    last_message_at, untracked_at, watch_type, value, released_at, origin,
    matched_watch_id, tracking_origin, source_thread_id.

Deliberately NOT indexed - all Text, all unbounded:
    label_external_ids_json, participants_json, source_thread_ids_json,
    label_ids_json, to_header, cc_header.

`gmail_labels.name` is String(255) and still unindexed: labels are filtered by
external id, and the name is resolved to one before any query runs.

Every step is guarded on the current schema. The migration tests build a
database with `Base.metadata.create_all()`, stamp it at an old revision and
replay forward, so a bare `create_table`/`add_column` here fails against a
database that already has the model's shape (see test_migration_0014).

Revision ID: 20260923_0074
Revises: 20260909_0073
"""

from alembic import op
import sqlalchemy as sa

revision = "20260923_0074"
down_revision = "20260909_0073"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {row["name"] for row in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {row["name"] for row in inspector.get_indexes(table) if row.get("name")}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("gmail_labels"):
        _create_tables()
    _create_table_indexes(sa.inspect(op.get_bind()))
    _alter_email_conversations(sa.inspect(op.get_bind()))
    _alter_email_reply_messages(sa.inspect(op.get_bind()))
    _alter_appts_applications(sa.inspect(op.get_bind()))
    if "feature_label_tracking_enabled" not in _columns(sa.inspect(op.get_bind()), "user_settings"):
        op.add_column("user_settings", sa.Column("feature_label_tracking_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def _create_tables() -> None:
    op.create_table(
        "gmail_labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("external_label_id", sa.String(120), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("label_type", sa.String(20), nullable=False),
        sa.Column("is_tracked", sa.Boolean(), nullable=False),
        sa.Column("color_background", sa.String(16), nullable=True),
        sa.Column("color_text", sa.String(16), nullable=True),
        sa.Column("message_count_snapshot", sa.Integer(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "external_label_id", name="ux_gmail_labels_owner_external"),
    )
    op.create_table(
        "tracked_threads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("external_thread_id", sa.String(255), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("label_external_ids_json", sa.Text(), nullable=False),
        sa.Column("subject_snapshot", sa.String(500), nullable=False),
        sa.Column("participants_json", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=False),
        sa.Column("untracked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "external_thread_id", name="ux_tracked_threads_owner_thread"),
    )
    op.create_table(
        "recruiter_watches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False),
        sa.Column("watch_type", sa.String(10), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("source_thread_ids_json", sa.Text(), nullable=False),
        sa.Column("origin_label_external_id", sa.String(120), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_matched_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "watch_type", "value", name="ux_recruiter_watches_owner_value"),
    )

def _create_table_indexes(inspector) -> None:
    for table, columns in {
        "gmail_labels": ("owner_id", "label_type", "is_tracked"),
        "tracked_threads": ("owner_id", "external_thread_id", "conversation_id", "last_message_at", "untracked_at"),
        "recruiter_watches": ("owner_id", "watch_type", "value", "released_at"),
    }.items():
        existing = _indexes(inspector, table)
        for column in columns:
            name = f"ix_{table}_{column}"
            if name not in existing:
                op.create_index(name, table, [column])


def _alter_email_conversations(inspector) -> None:
    columns = _columns(inspector, "email_conversations")
    indexes = _indexes(inspector, "email_conversations")
    additions = [
        sa.Column("origin", sa.String(20), nullable=False, server_default="sent"),
        sa.Column("source_label_external_id", sa.String(120), nullable=True),
        sa.Column("subject_snapshot", sa.String(500), nullable=False, server_default=""),
        sa.Column("recruiter_snapshot", sa.String(255), nullable=False, server_default=""),
        sa.Column("recruiter_email_snapshot", sa.String(255), nullable=False, server_default=""),
    ]
    with op.batch_alter_table("email_conversations") as batch:
        batch.alter_column("root_recruiter_email_id", existing_type=sa.Integer(), nullable=True)
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
        if "ix_email_conversations_origin" not in indexes:
            batch.create_index("ix_email_conversations_origin", ["origin"])


def _alter_email_reply_messages(inspector) -> None:
    columns = _columns(inspector, "email_reply_messages")
    indexes = _indexes(inspector, "email_reply_messages")
    additions = [
        sa.Column("label_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("to_header", sa.Text(), nullable=True),
        sa.Column("cc_header", sa.Text(), nullable=True),
        sa.Column("matched_watch_id", sa.Integer(), nullable=True),
    ]
    if all(column.name in columns for column in additions) and "ix_email_reply_messages_matched_watch_id" in indexes:
        return
    with op.batch_alter_table("email_reply_messages") as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
        if "ix_email_reply_messages_matched_watch_id" not in indexes:
            batch.create_index("ix_email_reply_messages_matched_watch_id", ["matched_watch_id"])


def _alter_appts_applications(inspector) -> None:
    columns = _columns(inspector, "appts_applications")
    indexes = _indexes(inspector, "appts_applications")
    additions = [
        sa.Column("tracking_origin", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("source_thread_id", sa.String(255), nullable=True),
        sa.Column("source_label_external_id", sa.String(120), nullable=True),
    ]
    new_indexes = {
        "ix_appts_applications_tracking_origin": "tracking_origin",
        "ix_appts_applications_source_thread_id": "source_thread_id",
    }
    if all(column.name in columns for column in additions) and not set(new_indexes) - indexes:
        return
    with op.batch_alter_table("appts_applications") as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
        for name, column_name in new_indexes.items():
            if name not in indexes:
                batch.create_index(name, [column_name])


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM email_conversations WHERE root_recruiter_email_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade label tracking while rootless conversations exist; retain or export them before rollback")
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_column("feature_label_tracking_enabled")
    with op.batch_alter_table("appts_applications") as batch:
        batch.drop_index("ix_appts_applications_tracking_origin")
        batch.drop_index("ix_appts_applications_source_thread_id")
        for column in ("tracking_origin", "source_thread_id", "source_label_external_id"):
            batch.drop_column(column)
    with op.batch_alter_table("email_reply_messages") as batch:
        batch.drop_index("ix_email_reply_messages_matched_watch_id")
        for column in ("label_ids_json", "to_header", "cc_header", "matched_watch_id"):
            batch.drop_column(column)
    with op.batch_alter_table("email_conversations") as batch:
        batch.drop_index("ix_email_conversations_origin")
        for column in ("origin", "source_label_external_id", "subject_snapshot", "recruiter_snapshot", "recruiter_email_snapshot"):
            batch.drop_column(column)
        batch.alter_column("root_recruiter_email_id", existing_type=sa.Integer(), nullable=False)
    for table in ("recruiter_watches", "tracked_threads", "gmail_labels"):
        op.drop_table(table)
