from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings
from app.premium_numbers.phone_normalization import canonicalize_phone


class Base(DeclarativeBase):
    pass


is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 10} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _preferred_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "unknown":
            return text
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _dedupe_recruiter_opportunities(conn) -> None:
    duplicate_rows = conn.exec_driver_sql(
        """
        SELECT o1.id
        FROM recruiter_opportunities o1
        JOIN recruiter_opportunities o2
          ON o1.owner_id = o2.owner_id
         AND o1.recruiter_number_id = o2.recruiter_number_id
         AND o1.gmail_message_id = o2.gmail_message_id
         AND o1.id > o2.id
        """
    ).fetchall()
    for row in duplicate_rows:
        conn.exec_driver_sql("DELETE FROM recruiter_opportunities WHERE id = ?", (row[0],))


def _merge_phone_buckets(conn) -> None:
    recruiter_rows = conn.exec_driver_sql(
        """
        SELECT id, owner_id, normalized_phone_number, display_phone_number, recruiter_name, company,
               designation, recruiter_email, first_detected_email_id, created_at, updated_at
        FROM recruiter_numbers
        ORDER BY owner_id, id
        """
    ).mappings().all()
    recruiter_by_key: dict[tuple[str, str], dict] = {}
    recruiter_losers: list[int] = []
    for row in recruiter_rows:
        owner_id = str(row["owner_id"] or "")
        canonical = canonicalize_phone(str(row["normalized_phone_number"] or row["display_phone_number"] or ""))
        if not owner_id or not canonical:
            continue
        key = (owner_id, canonical)
        winner = recruiter_by_key.get(key)
        if not winner:
            recruiter_by_key[key] = dict(row)
            conn.exec_driver_sql(
                """
                UPDATE recruiter_numbers
                SET normalized_phone_number = ?
                WHERE id = ?
                """,
                (canonical, row["id"]),
            )
            continue
        recruiter_losers.append(row["id"])
        conn.exec_driver_sql(
            """
            UPDATE recruiter_opportunities
            SET recruiter_number_id = ?
            WHERE owner_id = ? AND recruiter_number_id = ?
            """,
            (winner["id"], owner_id, row["id"]),
        )
        merged_display = _preferred_text(winner.get("display_phone_number"), row["display_phone_number"]) or canonical
        merged_name = _preferred_text(winner.get("recruiter_name"), row["recruiter_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_designation = _preferred_text(winner.get("designation"), row["designation"]) or "Unknown"
        merged_email = _preferred_text(winner.get("recruiter_email"), row["recruiter_email"])
        merged_first_email = winner.get("first_detected_email_id") or row["first_detected_email_id"]
        conn.exec_driver_sql(
            """
            UPDATE recruiter_numbers
            SET display_phone_number = ?,
                recruiter_name = ?,
                company = ?,
                designation = ?,
                recruiter_email = ?,
                first_detected_email_id = ?,
                normalized_phone_number = ?
            WHERE id = ?
            """,
            (
                merged_display,
                merged_name,
                merged_company,
                merged_designation,
                merged_email,
                merged_first_email,
                canonical,
                winner["id"],
            ),
        )
        winner["display_phone_number"] = merged_display
        winner["recruiter_name"] = merged_name
        winner["company"] = merged_company
        winner["designation"] = merged_designation
        winner["recruiter_email"] = merged_email
        winner["first_detected_email_id"] = merged_first_email
    for loser_id in recruiter_losers:
        conn.exec_driver_sql("DELETE FROM recruiter_numbers WHERE id = ?", (loser_id,))

    _dedupe_recruiter_opportunities(conn)

    employer_rows = conn.exec_driver_sql(
        """
        SELECT id, owner_id, normalized_phone_number, display_phone_number, owner_name, company, source_email_id
        FROM employer_numbers
        ORDER BY owner_id, id
        """
    ).mappings().all()
    employer_by_key: dict[tuple[str, str], dict] = {}
    employer_losers: list[int] = []
    for row in employer_rows:
        owner_id = str(row["owner_id"] or "")
        canonical = canonicalize_phone(str(row["normalized_phone_number"] or row["display_phone_number"] or ""))
        if not owner_id or not canonical:
            continue
        key = (owner_id, canonical)
        if key in recruiter_by_key:
            conn.exec_driver_sql("DELETE FROM employer_numbers WHERE id = ?", (row["id"],))
            continue
        winner = employer_by_key.get(key)
        if not winner:
            employer_by_key[key] = dict(row)
            conn.exec_driver_sql(
                "UPDATE employer_numbers SET normalized_phone_number = ? WHERE id = ?",
                (canonical, row["id"]),
            )
            continue
        employer_losers.append(row["id"])
        merged_display = _preferred_text(winner.get("display_phone_number"), row["display_phone_number"]) or canonical
        merged_owner_name = _preferred_text(winner.get("owner_name"), row["owner_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_source_email = winner.get("source_email_id") or row["source_email_id"]
        conn.exec_driver_sql(
            """
            UPDATE employer_numbers
            SET display_phone_number = ?, owner_name = ?, company = ?, source_email_id = ?, normalized_phone_number = ?
            WHERE id = ?
            """,
            (merged_display, merged_owner_name, merged_company, merged_source_email, canonical, winner["id"]),
        )
        winner["display_phone_number"] = merged_display
        winner["owner_name"] = merged_owner_name
        winner["company"] = merged_company
        winner["source_email_id"] = merged_source_email
    for loser_id in employer_losers:
        conn.exec_driver_sql("DELETE FROM employer_numbers WHERE id = ?", (loser_id,))

    review_rows = conn.exec_driver_sql(
        """
        SELECT id, owner_id, source_email_id, normalized_phone_number, display_phone_number, owner_name, company,
               designation, confidence, purpose, evidence_snippet, email_subject, email_sender, gmail_open_url,
               state, created_at, updated_at
        FROM number_review_queue
        ORDER BY owner_id, id
        """
    ).mappings().all()
    review_by_key: dict[tuple[str, str, int], dict] = {}
    review_losers: list[int] = []
    for row in review_rows:
        owner_id = str(row["owner_id"] or "")
        source_email_id = int(row["source_email_id"] or 0)
        canonical = canonicalize_phone(str(row["normalized_phone_number"] or row["display_phone_number"] or ""))
        if not owner_id or not canonical:
            continue
        if (owner_id, canonical) in recruiter_by_key or (owner_id, canonical) in employer_by_key:
            review_losers.append(row["id"])
            continue
        key = (owner_id, canonical, source_email_id)
        winner = review_by_key.get(key)
        if not winner:
            review_by_key[key] = dict(row)
            conn.exec_driver_sql(
                "UPDATE number_review_queue SET normalized_phone_number = ? WHERE id = ?",
                (canonical, row["id"]),
            )
            continue
        review_losers.append(row["id"])
        merged_display = _preferred_text(winner.get("display_phone_number"), row["display_phone_number"]) or canonical
        merged_owner_name = _preferred_text(winner.get("owner_name"), row["owner_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_designation = _preferred_text(winner.get("designation"), row["designation"]) or "Unknown"
        merged_confidence = _preferred_text(winner.get("confidence"), row["confidence"]) or "low"
        merged_purpose = _preferred_text(winner.get("purpose"), row["purpose"])
        merged_evidence = _preferred_text(winner.get("evidence_snippet"), row["evidence_snippet"])
        merged_subject = _preferred_text(winner.get("email_subject"), row["email_subject"])
        merged_sender = _preferred_text(winner.get("email_sender"), row["email_sender"])
        merged_open = _preferred_text(winner.get("gmail_open_url"), row["gmail_open_url"])
        merged_state = "pending" if "pending" in {winner.get("state"), row["state"]} else _preferred_text(
            winner.get("state"), row["state"]
        )
        conn.exec_driver_sql(
            """
            UPDATE number_review_queue
            SET display_phone_number = ?, owner_name = ?, company = ?, designation = ?, confidence = ?,
                purpose = ?, evidence_snippet = ?, email_subject = ?, email_sender = ?, gmail_open_url = ?,
                state = ?, normalized_phone_number = ?
            WHERE id = ?
            """,
            (
                merged_display,
                merged_owner_name,
                merged_company,
                merged_designation,
                merged_confidence,
                merged_purpose,
                merged_evidence,
                merged_subject,
                merged_sender,
                merged_open,
                merged_state,
                canonical,
                winner["id"],
            ),
        )
    for loser_id in review_losers:
        conn.exec_driver_sql("DELETE FROM number_review_queue WHERE id = ?", (loser_id,))


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
            ("applied_gmail_label", "ALTER TABLE recruiter_emails ADD COLUMN applied_gmail_label VARCHAR(120)"),
            ("applied_gmail_label_id", "ALTER TABLE recruiter_emails ADD COLUMN applied_gmail_label_id VARCHAR(120)"),
            ("applied_gmail_label_at", "ALTER TABLE recruiter_emails ADD COLUMN applied_gmail_label_at DATETIME"),
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
            ("semantic_input_source", "ALTER TABLE recruiter_emails ADD COLUMN semantic_input_source VARCHAR(40)"),
            ("semantic_input_chars", "ALTER TABLE recruiter_emails ADD COLUMN semantic_input_chars INTEGER"),
            ("semantic_chunks", "ALTER TABLE recruiter_emails ADD COLUMN semantic_chunks INTEGER"),
            ("semantic_fallback_reason", "ALTER TABLE recruiter_emails ADD COLUMN semantic_fallback_reason TEXT"),
            ("keyword_source", "ALTER TABLE recruiter_emails ADD COLUMN keyword_source VARCHAR(40)"),
            ("thread_snapshot_used", "ALTER TABLE recruiter_emails ADD COLUMN thread_snapshot_used BOOLEAN"),
            ("thread_snapshot_email_id", "ALTER TABLE recruiter_emails ADD COLUMN thread_snapshot_email_id INTEGER"),
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
            ("saved_gmail_queries_json", "ALTER TABLE user_settings ADD COLUMN saved_gmail_queries_json TEXT DEFAULT '[]'"),
            ("default_date_mode", "ALTER TABLE user_settings ADD COLUMN default_date_mode VARCHAR(20) DEFAULT 'today'"),
            ("feature_ai_enabled", "ALTER TABLE user_settings ADD COLUMN feature_ai_enabled BOOLEAN DEFAULT 0"),
            ("feature_semantic_enabled", "ALTER TABLE user_settings ADD COLUMN feature_semantic_enabled BOOLEAN DEFAULT 0"),
            ("draft_text_size", "ALTER TABLE user_settings ADD COLUMN draft_text_size VARCHAR(20) DEFAULT 'normal'"),
            ("feature_auto_poll_interval_minutes", "ALTER TABLE user_settings ADD COLUMN feature_auto_poll_interval_minutes INTEGER DEFAULT 10"),
            ("feature_nvoids_enabled", "ALTER TABLE user_settings ADD COLUMN feature_nvoids_enabled BOOLEAN DEFAULT 1"),
            ("feature_nvoids_auto_sync", "ALTER TABLE user_settings ADD COLUMN feature_nvoids_auto_sync BOOLEAN DEFAULT 0"),
            ("feature_nvoids_poll_interval_minutes", "ALTER TABLE user_settings ADD COLUMN feature_nvoids_poll_interval_minutes INTEGER DEFAULT 30"),
            ("nvoids_batch_limit", "ALTER TABLE user_settings ADD COLUMN nvoids_batch_limit INTEGER DEFAULT 10"),
            ("nvoids_locations", "ALTER TABLE user_settings ADD COLUMN nvoids_locations TEXT DEFAULT ''"),
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
        existing_opportunities = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(recruiter_opportunities)")}
        opportunity_alter_statements = [
            ("cold_call_script", "ALTER TABLE recruiter_opportunities ADD COLUMN cold_call_script TEXT"),
            ("cold_call_script_updated_at", "ALTER TABLE recruiter_opportunities ADD COLUMN cold_call_script_updated_at DATETIME"),
        ]
        for column_name, statement in opportunity_alter_statements:
            if column_name not in existing_opportunities:
                conn.exec_driver_sql(statement)
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
        _merge_phone_buckets(conn)
        conn.commit()
