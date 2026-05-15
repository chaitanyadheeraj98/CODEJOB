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
            ("owner_id", "ALTER TABLE recruiter_emails ADD COLUMN owner_id VARCHAR(100) DEFAULT 'default-owner'"),
            ("source", "ALTER TABLE recruiter_emails ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"),
            ("external_message_id", "ALTER TABLE recruiter_emails ADD COLUMN external_message_id VARCHAR(255)"),
            ("external_thread_id", "ALTER TABLE recruiter_emails ADD COLUMN external_thread_id VARCHAR(255)"),
            ("external_rfc_message_id", "ALTER TABLE recruiter_emails ADD COLUMN external_rfc_message_id VARCHAR(500)"),
            ("gmail_received_at", "ALTER TABLE recruiter_emails ADD COLUMN gmail_received_at DATETIME"),
            ("recipient_email", "ALTER TABLE recruiter_emails ADD COLUMN recipient_email VARCHAR(255)"),
            ("cc_email", "ALTER TABLE recruiter_emails ADD COLUMN cc_email VARCHAR(255)"),
            ("routing_status", "ALTER TABLE recruiter_emails ADD COLUMN routing_status VARCHAR(50) DEFAULT 'unverified'"),
            ("routing_confidence", "ALTER TABLE recruiter_emails ADD COLUMN routing_confidence FLOAT DEFAULT 0.0"),
            ("routing_reason", "ALTER TABLE recruiter_emails ADD COLUMN routing_reason TEXT DEFAULT ''"),
            ("routing_evidence", "ALTER TABLE recruiter_emails ADD COLUMN routing_evidence TEXT DEFAULT '[]'"),
            ("routing_candidates", "ALTER TABLE recruiter_emails ADD COLUMN routing_candidates TEXT DEFAULT '[]'"),
            ("routing_confirmed", "ALTER TABLE recruiter_emails ADD COLUMN routing_confirmed BOOLEAN DEFAULT 0"),
            ("resume_asset_id", "ALTER TABLE recruiter_emails ADD COLUMN resume_asset_id INTEGER"),
            ("resume_file_name", "ALTER TABLE recruiter_emails ADD COLUMN resume_file_name VARCHAR(255)"),
            ("sent_at", "ALTER TABLE recruiter_emails ADD COLUMN sent_at DATETIME"),
            ("last_error", "ALTER TABLE recruiter_emails ADD COLUMN last_error TEXT"),
            ("state", "ALTER TABLE recruiter_emails ADD COLUMN state VARCHAR(50) DEFAULT 'auto_rejected'"),
            ("decision_reason", "ALTER TABLE recruiter_emails ADD COLUMN decision_reason TEXT"),
            ("hard_filter_result", "ALTER TABLE recruiter_emails ADD COLUMN hard_filter_result TEXT"),
            ("auto_reject_reason", "ALTER TABLE recruiter_emails ADD COLUMN auto_reject_reason VARCHAR(120)"),
            ("ai_score", "ALTER TABLE recruiter_emails ADD COLUMN ai_score FLOAT"),
            ("ai_score_source", "ALTER TABLE recruiter_emails ADD COLUMN ai_score_source VARCHAR(80)"),
            ("ai_summary", "ALTER TABLE recruiter_emails ADD COLUMN ai_summary TEXT"),
            ("skip_reason", "ALTER TABLE recruiter_emails ADD COLUMN skip_reason VARCHAR(120)"),
            ("sync_batch_id", "ALTER TABLE recruiter_emails ADD COLUMN sync_batch_id VARCHAR(100)"),
            ("gmail_sent_id", "ALTER TABLE recruiter_emails ADD COLUMN gmail_sent_id VARCHAR(255)"),
            ("draft_source", "ALTER TABLE recruiter_emails ADD COLUMN draft_source VARCHAR(50)"),
            ("draft_model", "ALTER TABLE recruiter_emails ADD COLUMN draft_model VARCHAR(120)"),
            ("draft_ai_error", "ALTER TABLE recruiter_emails ADD COLUMN draft_ai_error TEXT"),
            ("draft_resume_context_status", "ALTER TABLE recruiter_emails ADD COLUMN draft_resume_context_status VARCHAR(40)"),
            ("semantic_embedding", "ALTER TABLE recruiter_emails ADD COLUMN semantic_embedding TEXT"),
        ]

        for column_name, statement in alter_statements:
            if column_name not in existing:
                conn.exec_driver_sql(statement)

        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_recruiter_emails_external_message_id "
            "ON recruiter_emails (external_message_id)"
        )

        existing_feedback = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(recipient_routing_feedback)")}
        feedback_alter_statements = [
            ("sample_sender", "ALTER TABLE recipient_routing_feedback ADD COLUMN sample_sender VARCHAR(255)"),
            ("evidence_to_present", "ALTER TABLE recipient_routing_feedback ADD COLUMN evidence_to_present BOOLEAN DEFAULT 0"),
            ("evidence_cc_present", "ALTER TABLE recipient_routing_feedback ADD COLUMN evidence_cc_present BOOLEAN DEFAULT 0"),
        ]
        for column_name, statement in feedback_alter_statements:
            if column_name not in existing_feedback:
                conn.exec_driver_sql(statement)

        existing_settings = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user_settings)")}
        settings_alter_statements = [
            ("mail_date", "ALTER TABLE user_settings ADD COLUMN mail_date VARCHAR(10)"),
            ("default_gmail_query", "ALTER TABLE user_settings ADD COLUMN default_gmail_query TEXT DEFAULT 'is:unread in:inbox recruiter'"),
            ("default_date_mode", "ALTER TABLE user_settings ADD COLUMN default_date_mode VARCHAR(20) DEFAULT 'today'"),
            ("feature_ai_enabled", "ALTER TABLE user_settings ADD COLUMN feature_ai_enabled BOOLEAN DEFAULT 0"),
            ("feature_semantic_enabled", "ALTER TABLE user_settings ADD COLUMN feature_semantic_enabled BOOLEAN DEFAULT 0"),
            ("feature_auto_poll_interval_minutes", "ALTER TABLE user_settings ADD COLUMN feature_auto_poll_interval_minutes INTEGER DEFAULT 10"),
            ("fallback_draft_template", "ALTER TABLE user_settings ADD COLUMN fallback_draft_template TEXT DEFAULT ''"),
            ("signature_name", "ALTER TABLE user_settings ADD COLUMN signature_name VARCHAR(255) DEFAULT ''"),
            ("signature_phone", "ALTER TABLE user_settings ADD COLUMN signature_phone VARCHAR(80) DEFAULT ''"),
            ("signature_email", "ALTER TABLE user_settings ADD COLUMN signature_email VARCHAR(255) DEFAULT ''"),
            ("policy_json", "ALTER TABLE user_settings ADD COLUMN policy_json TEXT DEFAULT ''"),
            ("employer_domains", "ALTER TABLE user_settings ADD COLUMN employer_domains TEXT DEFAULT ''"),
        ]
        for column_name, statement in settings_alter_statements:
            if column_name not in existing_settings:
                conn.exec_driver_sql(statement)

        existing_resume_assets = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(resume_assets)")}
        resume_assets_alter_statements = [
            ("semantic_embedding", "ALTER TABLE resume_assets ADD COLUMN semantic_embedding TEXT"),
        ]
        for column_name, statement in resume_assets_alter_statements:
            if column_name not in existing_resume_assets:
                conn.exec_driver_sql(statement)

        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS productivity_events (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                event_type VARCHAR(80),
                event_source VARCHAR(40) DEFAULT 'system',
                entity_id INTEGER,
                weight FLOAT DEFAULT 0.0,
                metadata_json TEXT DEFAULT '{}',
                occurred_at DATETIME,
                created_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_productivity_events_owner_id ON productivity_events (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_productivity_events_event_type ON productivity_events (event_type)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_productivity_events_occurred_at ON productivity_events (occurred_at)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS premium_number_leads (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                recruiter_email_id INTEGER,
                phone_number_normalized VARCHAR(40),
                phone_number_display VARCHAR(80),
                owner_name VARCHAR(255) DEFAULT 'Unknown',
                company VARCHAR(255) DEFAULT 'Unknown',
                designation VARCHAR(255) DEFAULT 'Unknown',
                purpose VARCHAR(255) DEFAULT 'Recruiter contact',
                confidence VARCHAR(10) DEFAULT 'low',
                contact_type VARCHAR(40) DEFAULT 'unknown',
                recruiter_relevance_score INTEGER DEFAULT 0,
                is_recruiter_relevant BOOLEAN DEFAULT 0,
                relevance_reason VARCHAR(255) DEFAULT '',
                source_fragment TEXT DEFAULT '',
                source_email_sender VARCHAR(255) DEFAULT '',
                source_email_subject VARCHAR(500) DEFAULT '',
                source_email_message_id VARCHAR(255),
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_premium_number_leads_owner_id ON premium_number_leads (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_premium_number_leads_recruiter_email_id ON premium_number_leads (recruiter_email_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_premium_number_leads_phone_number_normalized ON premium_number_leads (phone_number_normalized)"
        )
        existing_premium = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(premium_number_leads)")}
        premium_alter_statements = [
            ("contact_type", "ALTER TABLE premium_number_leads ADD COLUMN contact_type VARCHAR(40) DEFAULT 'unknown'"),
            ("recruiter_relevance_score", "ALTER TABLE premium_number_leads ADD COLUMN recruiter_relevance_score INTEGER DEFAULT 0"),
            ("is_recruiter_relevant", "ALTER TABLE premium_number_leads ADD COLUMN is_recruiter_relevant BOOLEAN DEFAULT 0"),
            ("relevance_reason", "ALTER TABLE premium_number_leads ADD COLUMN relevance_reason VARCHAR(255) DEFAULT ''"),
        ]
        for column_name, statement in premium_alter_statements:
            if column_name not in existing_premium:
                conn.exec_driver_sql(statement)
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS recruiter_numbers (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                normalized_phone_number VARCHAR(40),
                display_phone_number VARCHAR(80),
                recruiter_name VARCHAR(255) DEFAULT 'Unknown',
                company VARCHAR(255) DEFAULT 'Unknown',
                designation VARCHAR(255) DEFAULT 'Unknown',
                recruiter_email VARCHAR(255) DEFAULT '',
                first_detected_email_id INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_recruiter_numbers_owner_id ON recruiter_numbers (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_recruiter_numbers_normalized_phone_number ON recruiter_numbers (normalized_phone_number)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_recruiter_numbers_owner_phone ON recruiter_numbers (owner_id, normalized_phone_number)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS employer_numbers (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                normalized_phone_number VARCHAR(40),
                display_phone_number VARCHAR(80),
                owner_name VARCHAR(255) DEFAULT 'Unknown',
                company VARCHAR(255) DEFAULT 'Unknown',
                source_email_id INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_employer_numbers_owner_id ON employer_numbers (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_employer_numbers_normalized_phone_number ON employer_numbers (normalized_phone_number)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_employer_numbers_owner_phone ON employer_numbers (owner_id, normalized_phone_number)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS recruiter_opportunities (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                recruiter_number_id INTEGER,
                source_email_id INTEGER,
                gmail_message_id VARCHAR(255),
                email_subject VARCHAR(500) DEFAULT '',
                email_sender VARCHAR(255) DEFAULT '',
                gmail_open_url VARCHAR(1000) DEFAULT '',
                received_at DATETIME,
                job_title VARCHAR(255) DEFAULT '',
                client VARCHAR(255) DEFAULT '',
                location VARCHAR(255) DEFAULT '',
                work_mode VARCHAR(80) DEFAULT '',
                visa_restrictions VARCHAR(255) DEFAULT '',
                extracted_skills TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                status VARCHAR(40) DEFAULT 'New',
                notes TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_recruiter_opportunities_owner_id ON recruiter_opportunities (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_recruiter_opportunities_recruiter_number_id ON recruiter_opportunities (recruiter_number_id)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_recruiter_opportunities_owner_recruiter_msg ON recruiter_opportunities (owner_id, recruiter_number_id, gmail_message_id)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS number_review_queue (
                id INTEGER PRIMARY KEY,
                owner_id VARCHAR(100),
                source_email_id INTEGER,
                normalized_phone_number VARCHAR(40),
                display_phone_number VARCHAR(80),
                owner_name VARCHAR(255) DEFAULT 'Unknown',
                company VARCHAR(255) DEFAULT 'Unknown',
                designation VARCHAR(255) DEFAULT 'Unknown',
                confidence VARCHAR(10) DEFAULT 'low',
                purpose VARCHAR(255) DEFAULT '',
                evidence_snippet TEXT DEFAULT '',
                email_subject VARCHAR(500) DEFAULT '',
                email_sender VARCHAR(255) DEFAULT '',
                gmail_open_url VARCHAR(1000) DEFAULT '',
                state VARCHAR(40) DEFAULT 'pending',
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_number_review_queue_owner_id ON number_review_queue (owner_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_number_review_queue_source_email_id ON number_review_queue (source_email_id)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_number_review_queue_owner_phone_email ON number_review_queue (owner_id, normalized_phone_number, source_email_id)"
        )
        conn.commit()
