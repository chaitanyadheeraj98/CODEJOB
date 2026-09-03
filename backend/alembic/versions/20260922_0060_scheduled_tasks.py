"""Scheduled tasks, runs, checklist items, the user timezone, and suggestion expiry.

Index safety, restated because this migration runs on backend boot via
`alembic upgrade head` and migration 0051 took production down with an index on
an unbounded Text column:

Indexed here - every one fixed-width, a foreign key, a boolean, or a datetime:
    owner_id, kind, status, next_run_at, started_at, outcome, expires_at,
    task_id, done, id.

Deliberately NOT indexed - all Text, all unbounded:
    condition_json, action_json, prepared_json, last_error, error.

`scheduled_task_items.text` is String(500) rather than Text precisely so it
stays safely indexable if a search is ever wanted.

Revision ID: 20260922_0060
Revises: 20260921_0059
"""

from alembic import op
import sqlalchemy as sa


revision = "20260922_0060"
down_revision = "20260921_0059"
branch_labels = None
depends_on = None


def _drop_server_default(bind, table: str, column: str) -> None:
    """Drop a server default where the dialect supports it.

    The default exists so the column can be added to a populated table without
    a NULL sweep; keeping it afterwards would make every future autogenerate
    report drift against a model that declares no server default. SQLite has no
    ALTER COLUMN, and the tests run there, so it is skipped rather than fatal.
    """
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(table, column, server_default=None)


def _has_column(inspector, table: str, column: str) -> bool:
    if not inspector.has_table(table):
        return False
    return column in {row["name"] for row in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- W2: the timezone foundation -------------------------------------
    # A server_default so existing rows read "UTC" rather than NULL, then the
    # default is dropped: the application layer owns the value from here on.
    if not _has_column(inspector, "user_settings", "timezone"):
        op.add_column(
            "user_settings",
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        )
        _drop_server_default(bind, "user_settings", "timezone")
    if not _has_column(inspector, "user_settings", "feature_scheduling_sweep_interval_minutes"):
        op.add_column(
            "user_settings",
            sa.Column(
                "feature_scheduling_sweep_interval_minutes",
                sa.Integer(),
                nullable=False,
                server_default="15",
            ),
        )
        _drop_server_default(bind, "user_settings", "feature_scheduling_sweep_interval_minutes")

    # --- W3: suggestion expiry -------------------------------------------
    if not _has_column(inspector, "application_suggestions", "expires_at"):
        op.add_column(
            "application_suggestions", sa.Column("expires_at", sa.DateTime(), nullable=True)
        )
        # A datetime the expiry sweep filters on. Fixed width, safe to index.
        op.create_index(
            "ix_application_suggestions_expires_at", "application_suggestions", ["expires_at"]
        )
    if not _has_column(inspector, "application_suggestions", "expiry_reason"):
        op.add_column(
            "application_suggestions",
            sa.Column("expiry_reason", sa.String(length=20), nullable=False, server_default=""),
        )
        _drop_server_default(bind, "application_suggestions", "expiry_reason")

    # --- W4: the scheduling tables ---------------------------------------
    if not inspector.has_table("scheduled_tasks"):
        op.create_table(
            "scheduled_tasks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("schedule_kind", sa.String(length=20), nullable=False),
            sa.Column("cron_expression", sa.String(length=120), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("run_at", sa.DateTime(), nullable=True),
            sa.Column("condition_json", sa.Text(), nullable=True),
            sa.Column("action_json", sa.Text(), nullable=False),
            sa.Column("subject_type", sa.String(length=40), nullable=False),
            sa.Column("subject_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False),
            sa.Column("retention_hours", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scheduled_tasks_id", "scheduled_tasks", ["id"])
        op.create_index("ix_scheduled_tasks_owner_id", "scheduled_tasks", ["owner_id"])
        op.create_index("ix_scheduled_tasks_kind", "scheduled_tasks", ["kind"])
        op.create_index("ix_scheduled_tasks_status", "scheduled_tasks", ["status"])
        op.create_index("ix_scheduled_tasks_next_run_at", "scheduled_tasks", ["next_run_at"])

    if not inspector.has_table("scheduled_task_runs"):
        op.create_table(
            "scheduled_task_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("outcome", sa.String(length=20), nullable=False),
            sa.Column("prepared_json", sa.Text(), nullable=False),
            sa.Column("item_count", sa.Integer(), nullable=False),
            sa.Column("approved_count", sa.Integer(), nullable=False),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("expired_at", sa.DateTime(), nullable=True),
            sa.Column("expiry_reason", sa.String(length=20), nullable=False),
            sa.Column("warned_at", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["task_id"],
                ["scheduled_tasks.id"],
                name="fk_scheduled_run_task",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scheduled_task_runs_id", "scheduled_task_runs", ["id"])
        op.create_index("ix_scheduled_task_runs_owner_id", "scheduled_task_runs", ["owner_id"])
        op.create_index("ix_scheduled_task_runs_task_id", "scheduled_task_runs", ["task_id"])
        op.create_index("ix_scheduled_task_runs_started_at", "scheduled_task_runs", ["started_at"])
        op.create_index("ix_scheduled_task_runs_outcome", "scheduled_task_runs", ["outcome"])
        op.create_index("ix_scheduled_task_runs_expires_at", "scheduled_task_runs", ["expires_at"])

    if not inspector.has_table("scheduled_task_items"):
        op.create_table(
            "scheduled_task_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(length=500), nullable=False),
            sa.Column("done", sa.Boolean(), nullable=False),
            sa.Column("done_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["task_id"],
                ["scheduled_tasks.id"],
                name="fk_scheduled_item_task",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scheduled_task_items_id", "scheduled_task_items", ["id"])
        op.create_index("ix_scheduled_task_items_owner_id", "scheduled_task_items", ["owner_id"])
        op.create_index("ix_scheduled_task_items_task_id", "scheduled_task_items", ["task_id"])
        op.create_index("ix_scheduled_task_items_done", "scheduled_task_items", ["done"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in ("scheduled_task_items", "scheduled_task_runs", "scheduled_tasks"):
        if inspector.has_table(table):
            op.drop_table(table)

    if _has_column(inspector, "application_suggestions", "expires_at"):
        op.drop_index("ix_application_suggestions_expires_at", table_name="application_suggestions")
        op.drop_column("application_suggestions", "expires_at")
    if _has_column(inspector, "application_suggestions", "expiry_reason"):
        op.drop_column("application_suggestions", "expiry_reason")
    if _has_column(inspector, "user_settings", "feature_scheduling_sweep_interval_minutes"):
        op.drop_column("user_settings", "feature_scheduling_sweep_interval_minutes")
    if _has_column(inspector, "user_settings", "timezone"):
        op.drop_column("user_settings", "timezone")
