from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings

# No schema-changing SQL belongs in this file — see backend/alembic/versions/ and backend/README.md's Alembic section.


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store naive UTC timestamps and return UTC-aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 10} if is_sqlite else {}
# SQLAlchemy's QueuePool defaults to 5 connections plus 10 overflow - fifteen -
# and those defaults were never changed. FastAPI runs this application's 261
# sync endpoints in the anyio threadpool, which holds **40** threads, so forty
# requests can each want a connection while fifteen exist. The rest queue on
# the pool and, past `pool_timeout`, fail.
#
# Sized here rather than left implicit, and configurable because the right
# number depends on the process count:
#
#     total connections = (pool_size + max_overflow) x API processes + worker
#
# At the defaults below that is 30 for a single API process, comfortably under
# PostgreSQL's default `max_connections` of 100 alongside the RQ worker. Four
# uvicorn workers would need 120 and would exhaust it, so raising the worker
# count means lowering these or raising `max_connections` - see C3.
pool_args = (
    {}
    if is_sqlite
    else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}
)
engine = create_engine(settings.database_url, connect_args=connect_args, **pool_args)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        # SQLite defaults foreign_keys OFF, which silently makes every ForeignKey in
        # models.py decorative - including the ondelete=SET NULL links that keep an
        # application from pointing at a deleted opportunity, contact, or resume.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with SessionLocal.begin() as db:
        yield db


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
