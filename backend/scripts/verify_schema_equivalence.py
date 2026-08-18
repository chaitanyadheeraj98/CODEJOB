"""Compare an introspected database schema against CodeJob's target metadata.

Usage: python scripts/verify_schema_equivalence.py --database-url <url>
Exit code 0 means no unapproved drift; non-zero prints each mismatch.

The target is ``Base.metadata`` plus a narrowly documented legacy preservation
set discovered in the live-volume audit for the declarative baseline. Keeping
that set explicit avoids mapping retired columns back into the ORM while still
making fresh SQLite, fresh PostgreSQL, and the existing SQLite schema
mechanically comparable.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models as _models  # noqa: E402,F401
from app.db import Base  # noqa: E402


LegacyColumn = tuple[sa.types.TypeEngine, bool, bool]


LEGACY_TABLES: dict[str, dict[str, LegacyColumn]] = {
    "candidate_resume_matches": {
        "id": (sa.Integer(), False, True),
        "owner_id": (sa.String(100), False, False),
        "candidate_id": (sa.Integer(), False, False),
        "resume_asset_id": (sa.Integer(), False, False),
        "overall_score": (sa.Float(), False, False),
        "hard_requirement_score": (sa.Float(), False, False),
        "technical_score": (sa.Float(), False, False),
        "semantic_score": (sa.Float(), False, False),
        "matched_requirements_json": (sa.Text(), False, False),
        "missing_requirements_json": (sa.Text(), False, False),
        "evidence_json": (sa.Text(), False, False),
        "risk_flags_json": (sa.Text(), False, False),
        "is_selected": (sa.Boolean(), False, False),
        "created_at": (sa.DateTime(), False, False),
    }
}


LEGACY_COLUMNS: dict[str, dict[str, LegacyColumn]] = {
    "user_settings": {
        "feature_resume_matching_enabled": (sa.Boolean(), False, False),
    },
    "resume_assets": {
        "display_name": (sa.String(255), True, False),
        "normalized_skills_json": (sa.Text(), False, False),
        "skills_embedding": (sa.Text(), True, False),
        "embedding_provider": (sa.String(80), True, False),
        "embedding_model": (sa.String(120), True, False),
        "skills_extraction_status": (sa.String(40), False, False),
        "evidence_profile_json": (sa.Text(), False, False),
        "evidence_extraction_status": (sa.String(40), False, False),
        "profile_version": (sa.String(40), False, False),
        "source_content_hash": (sa.String(64), True, False),
    },
    "recruiter_emails": {
        "is_premium": (sa.Boolean(), True, False),
        "recruiter_phone": (sa.String(80), True, False),
        "recruiter_phone_confidence": (sa.Float(), True, False),
        "recruiter_phone_reason": (sa.Text(), True, False),
        "employer_phone": (sa.String(80), True, False),
        "employer_phone_confidence": (sa.Float(), True, False),
        "cold_call_script": (sa.Text(), True, False),
        "cold_call_script_source": (sa.String(50), True, False),
        "cold_call_script_error": (sa.Text(), True, False),
        "message_intent": (sa.String(50), True, False),
        "intent_evidence": (sa.Text(), True, False),
        "jd_profile_json": (sa.Text(), True, False),
        "jd_embedding": (sa.Text(), True, False),
        "resume_match_score": (sa.Float(), True, False),
        "hard_requirement_score": (sa.Float(), True, False),
        "technical_stack_score": (sa.Float(), True, False),
        "responsibility_alignment_score": (sa.Float(), True, False),
        "evidence_summary_json": (sa.Text(), False, False),
        "missing_requirements_json": (sa.Text(), False, False),
        "risk_flags_json": (sa.Text(), False, False),
        "match_recommendation": (sa.String(80), True, False),
        "resume_match_source": (sa.String(80), True, False),
        "resume_match_version": (sa.String(40), True, False),
    },
}


# These exact nullable-live/non-null-model pairs were produced by historical
# SQLite ALTER TABLE additions. They are preserved until a dedicated data-safe
# constraint-tightening migration is approved.
ALLOWED_NULLABILITY_DRIFT = {
    ("canonical_entity_taxonomy_entries", "created_at"),
    ("canonical_entity_taxonomy_entries", "updated_at"),
    ("gmail_requirement_groups", "created_at"),
    ("gmail_requirement_groups", "updated_at"),
    ("premium_number_leads", "contact_type"),
    ("premium_number_leads", "recruiter_relevance_score"),
    ("premium_number_leads", "is_recruiter_relevant"),
    ("premium_number_leads", "relevance_reason"),
    ("recent_run_skipped_items", "created_at"),
    ("recent_runs", "created_at"),
    ("recent_runs", "updated_at"),
    ("recruiter_opportunities", "source_type"),
    ("user_settings", "default_gmail_query"),
    ("user_settings", "saved_gmail_queries_json"),
    ("user_settings", "default_date_mode"),
    ("user_settings", "employer_domains"),
    ("user_settings", "feature_auto_poll_interval_minutes"),
    ("user_settings", "feature_nvoids_enabled"),
    ("user_settings", "feature_nvoids_auto_sync"),
    ("user_settings", "feature_nvoids_poll_interval_minutes"),
    ("user_settings", "nvoids_batch_limit"),
    ("user_settings", "nvoids_detail_title_mode"),
    ("user_settings", "nvoids_locations"),
    ("user_settings", "feature_ai_extractor_enabled"),
    ("user_settings", "feature_semantic_enabled"),
    ("user_settings", "feature_groq_job_parser_enabled"),
    ("user_settings", "draft_text_size"),
    ("user_settings", "fallback_draft_template"),
    ("user_settings", "signature_name"),
    ("user_settings", "signature_phone"),
    ("user_settings", "signature_email"),
    ("user_settings", "preferred_employer_cc_email"),
    ("user_settings", "policy_json"),
}


# SQLite never enforces VARCHAR lengths. Revision 0020 deliberately avoids
# rebuilding the live 1 GB SQLite tables for representation-only type changes;
# PostgreSQL receives the widened declarations that match Base.metadata.
ALLOWED_SQLITE_TYPE_DRIFT = {
    ("custom_skill_taxonomy_entries", "canonical_name"),
    ("external_opportunities", "company"),
    ("external_opportunities", "role"),
    ("external_opportunities", "duration"),
    ("external_opportunities", "rate"),
    ("recent_run_skipped_items", "title_or_subject"),
    ("recruiter_emails", "role"),
    ("recruiter_opportunities", "job_title"),
    ("recruiter_opportunities", "client"),
}


def _type_signature(column_type: sa.types.TypeEngine) -> tuple[str, int | None]:
    if isinstance(column_type, sa.TypeDecorator):
        return _type_signature(column_type.impl)
    if isinstance(column_type, sa.Boolean):
        return "boolean", None
    if isinstance(column_type, sa.Integer):
        return "integer", None
    if isinstance(column_type, sa.Float):
        return "float", None
    if isinstance(column_type, sa.Text):
        return "text", None
    if isinstance(column_type, sa.String):
        return "string", column_type.length
    if isinstance(column_type, sa.DateTime):
        return "datetime", None
    return column_type.__class__.__name__.lower(), None


def _expected_columns(table_name: str) -> dict[str, LegacyColumn]:
    if table_name in LEGACY_TABLES:
        return dict(LEGACY_TABLES[table_name])
    table = Base.metadata.tables[table_name]
    columns = {
        column.name: (column.type, bool(column.nullable), bool(column.primary_key))
        for column in table.columns
    }
    columns.update(LEGACY_COLUMNS.get(table_name, {}))
    return columns


def _model_unique_sets(table_name: str) -> set[tuple[str, ...]]:
    if table_name in LEGACY_TABLES:
        return set()
    table = Base.metadata.tables[table_name]
    uniques = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    uniques.update(
        tuple(sorted(index.columns.keys()))
        for index in table.indexes
        if index.unique
    )
    return uniques


def _live_unique_sets(inspector: sa.Inspector, table_name: str) -> set[tuple[str, ...]]:
    uniques = {
        tuple(sorted(item["column_names"]))
        for item in inspector.get_unique_constraints(table_name)
        if item.get("column_names")
    }
    uniques.update(
        tuple(sorted(item["column_names"]))
        for item in inspector.get_indexes(table_name)
        if item.get("unique") and item.get("column_names")
    )
    return uniques


def compare(database_url: str) -> list[str]:
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        problems: list[str] = []
        model_tables = set(Base.metadata.tables)
        expected_tables = model_tables | set(LEGACY_TABLES)
        live_tables = set(inspector.get_table_names()) - {"alembic_version"}

        for missing in sorted(expected_tables - live_tables):
            problems.append(f"MISSING TABLE: {missing}")
        for extra in sorted(live_tables - expected_tables):
            problems.append(f"UNEXPECTED TABLE: {extra}")

        for table_name in sorted(expected_tables & live_tables):
            expected_columns = _expected_columns(table_name)
            live_columns: dict[str, dict[str, Any]] = {
                column["name"]: column for column in inspector.get_columns(table_name)
            }

            for missing in sorted(set(expected_columns) - set(live_columns)):
                problems.append(f"{table_name}: MISSING COLUMN {missing}")
            for extra in sorted(set(live_columns) - set(expected_columns)):
                problems.append(f"{table_name}: UNEXPECTED COLUMN {extra}")

            for name in sorted(set(expected_columns) & set(live_columns)):
                expected_type, expected_nullable, _ = expected_columns[name]
                live_column = live_columns[name]
                expected_signature = _type_signature(expected_type)
                live_signature = _type_signature(live_column["type"])
                if expected_signature != live_signature and not (
                    engine.dialect.name == "sqlite"
                    and (table_name, name) in ALLOWED_SQLITE_TYPE_DRIFT
                ):
                    problems.append(
                        f"{table_name}.{name}: TYPE MISMATCH "
                        f"expected={expected_signature} live={live_signature}"
                    )
                live_nullable = bool(live_column["nullable"])
                if expected_nullable != live_nullable and (
                    table_name,
                    name,
                ) not in ALLOWED_NULLABILITY_DRIFT:
                    problems.append(
                        f"{table_name}.{name}: NULLABILITY MISMATCH "
                        f"expected={expected_nullable} live={live_nullable}"
                    )

            missing_uniques = _model_unique_sets(table_name) - _live_unique_sets(
                inspector,
                table_name,
            )
            for missing in sorted(missing_uniques):
                problems.append(f"{table_name}: MISSING UNIQUE CONSTRAINT on {missing}")

            expected_pk = tuple(
                name for name, (_, _, primary_key) in expected_columns.items() if primary_key
            )
            live_pk = tuple(
                inspector.get_pk_constraint(table_name).get("constrained_columns", [])
            )
            if expected_pk != live_pk:
                problems.append(
                    f"{table_name}: PRIMARY KEY MISMATCH expected={expected_pk} live={live_pk}"
                )

        return problems
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()

    problems = compare(args.database_url)
    if not problems:
        print("OK: no unapproved schema drift found")
        return 0
    for problem in problems:
        print(problem)
    print(f"\n{len(problems)} problem(s) found")
    return 1


if __name__ == "__main__":
    sys.exit(main())
