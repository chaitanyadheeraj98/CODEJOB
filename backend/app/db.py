from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_sqlite_phase0_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(recruiter_emails)")}
        alter_statements = [
            ("source", "ALTER TABLE recruiter_emails ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"),
            ("external_message_id", "ALTER TABLE recruiter_emails ADD COLUMN external_message_id VARCHAR(255)"),
            ("external_thread_id", "ALTER TABLE recruiter_emails ADD COLUMN external_thread_id VARCHAR(255)"),
            ("recipient_email", "ALTER TABLE recruiter_emails ADD COLUMN recipient_email VARCHAR(255)"),
            ("sent_at", "ALTER TABLE recruiter_emails ADD COLUMN sent_at DATETIME"),
            ("last_error", "ALTER TABLE recruiter_emails ADD COLUMN last_error TEXT"),
        ]

        for column_name, statement in alter_statements:
            if column_name not in existing:
                conn.exec_driver_sql(statement)

        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_recruiter_emails_external_message_id "
            "ON recruiter_emails (external_message_id)"
        )
        conn.commit()
