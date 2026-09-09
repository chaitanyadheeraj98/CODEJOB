import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_profile_binding_upgrade_is_idempotent_and_preserves_existing_drafts():
    path = Path(__file__).parents[1] / "alembic/versions/20260909_0072_resume_draft_format_profile.py"
    module_spec = importlib.util.spec_from_file_location("profile_binding_migration", path)
    migration = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE resume_drafts (id INTEGER PRIMARY KEY, content_markdown TEXT)"))
        connection.execute(sa.text("INSERT INTO resume_drafts VALUES (1, 'keep my draft')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        migration.upgrade()
        columns = {c["name"]: c for c in sa.inspect(connection).get_columns("resume_drafts")}
        assert columns["format_profile_id"]["nullable"]
        assert isinstance(columns["format_profile_id"]["type"], sa.Integer)
        assert not sa.inspect(connection).get_foreign_keys("resume_drafts")
        assert connection.execute(sa.text("SELECT content_markdown, format_profile_id FROM resume_drafts")).one() == ("keep my draft", None)
        migration.downgrade()
        migration.upgrade()
        assert len(sa.inspect(connection).get_indexes("resume_drafts")) == 1
    engine.dispose()
