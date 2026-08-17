"""Phone bucket dedupe/merge one-time migration.

Revision ID: 20260518_0002
Revises: 20260518_0001
Create Date: 2026-05-18
"""

from __future__ import annotations

import re

from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260518_0002"
down_revision = "20260518_0001"
branch_labels = None
depends_on = None


def _canonicalize_phone(raw: str) -> str:
    text = re.sub(
        r"(?:ext\.?|x|extension|\*)\s*[:\-]?\s*\d{1,6}\b",
        "",
        (raw or "").strip(),
        flags=re.IGNORECASE,
    )
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        return digits
    if len(digits) == 10:
        return f"1{digits}"
    return ""


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
        canonical = _canonicalize_phone(
            str(row["normalized_phone_number"] or row["display_phone_number"] or "")
        )
        if not owner_id or not canonical:
            continue
        key = (owner_id, canonical)
        winner = recruiter_by_key.get(key)
        if not winner:
            recruiter_by_key[key] = dict(row)
            conn.exec_driver_sql(
                "UPDATE recruiter_numbers SET normalized_phone_number = ? WHERE id = ?",
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
        merged_display = (
            _preferred_text(winner.get("display_phone_number"), row["display_phone_number"])
            or canonical
        )
        merged_name = _preferred_text(winner.get("recruiter_name"), row["recruiter_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_designation = (
            _preferred_text(winner.get("designation"), row["designation"]) or "Unknown"
        )
        merged_email = _preferred_text(winner.get("recruiter_email"), row["recruiter_email"])
        merged_first_email = winner.get("first_detected_email_id") or row["first_detected_email_id"]
        conn.exec_driver_sql(
            """
            UPDATE recruiter_numbers
            SET display_phone_number = ?, recruiter_name = ?, company = ?, designation = ?,
                recruiter_email = ?, first_detected_email_id = ?, normalized_phone_number = ?
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
        winner.update(
            display_phone_number=merged_display,
            recruiter_name=merged_name,
            company=merged_company,
            designation=merged_designation,
            recruiter_email=merged_email,
            first_detected_email_id=merged_first_email,
        )
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
        canonical = _canonicalize_phone(
            str(row["normalized_phone_number"] or row["display_phone_number"] or "")
        )
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
        merged_display = (
            _preferred_text(winner.get("display_phone_number"), row["display_phone_number"])
            or canonical
        )
        merged_owner_name = _preferred_text(winner.get("owner_name"), row["owner_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_source_email = winner.get("source_email_id") or row["source_email_id"]
        conn.exec_driver_sql(
            """
            UPDATE employer_numbers
            SET display_phone_number = ?, owner_name = ?, company = ?, source_email_id = ?,
                normalized_phone_number = ?
            WHERE id = ?
            """,
            (
                merged_display,
                merged_owner_name,
                merged_company,
                merged_source_email,
                canonical,
                winner["id"],
            ),
        )
        winner.update(
            display_phone_number=merged_display,
            owner_name=merged_owner_name,
            company=merged_company,
            source_email_id=merged_source_email,
        )
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
        canonical = _canonicalize_phone(
            str(row["normalized_phone_number"] or row["display_phone_number"] or "")
        )
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
        merged_display = (
            _preferred_text(winner.get("display_phone_number"), row["display_phone_number"])
            or canonical
        )
        merged_owner_name = _preferred_text(winner.get("owner_name"), row["owner_name"]) or "Unknown"
        merged_company = _preferred_text(winner.get("company"), row["company"]) or "Unknown"
        merged_designation = (
            _preferred_text(winner.get("designation"), row["designation"]) or "Unknown"
        )
        merged_confidence = _preferred_text(winner.get("confidence"), row["confidence"]) or "low"
        merged_purpose = _preferred_text(winner.get("purpose"), row["purpose"])
        merged_evidence = _preferred_text(winner.get("evidence_snippet"), row["evidence_snippet"])
        merged_subject = _preferred_text(winner.get("email_subject"), row["email_subject"])
        merged_sender = _preferred_text(winner.get("email_sender"), row["email_sender"])
        merged_open = _preferred_text(winner.get("gmail_open_url"), row["gmail_open_url"])
        merged_state = (
            "pending"
            if "pending" in {winner.get("state"), row["state"]}
            else _preferred_text(winner.get("state"), row["state"])
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


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    inspector = inspect(bind)
    required = {"recruiter_numbers", "employer_numbers", "number_review_queue", "recruiter_opportunities"}
    existing = set(inspector.get_table_names())
    if required.issubset(existing):
        _merge_phone_buckets(bind)


def downgrade() -> None:
    # Irreversible data cleanup.
    pass
