from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.config import settings
from app.db import engine

logger = logging.getLogger(__name__)


class MigrationRuntimeService:
    def __init__(self) -> None:
        self._alembic_ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"

    def _alembic_config(self) -> Config:
        cfg = Config(str(self._alembic_ini_path))
        cfg.set_main_option("sqlalchemy.url", settings.database_url)
        return cfg

    def _current_revision(self) -> str | None:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()

    def _head_revision(self, cfg: Config) -> str:
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        if not head:
            raise RuntimeError("Alembic head revision is not available.")
        return head

    def ensure_schema_ready(self) -> None:
        cfg = self._alembic_config()
        head = self._head_revision(cfg)
        current = self._current_revision()

        if current == head:
            return

        logger.error("schema_migration_required current=%s head=%s", current, head)

        raise RuntimeError(
            "Database migration is required before startup. "
            "Run 'alembic upgrade head' in backend/ and restart."
        )
