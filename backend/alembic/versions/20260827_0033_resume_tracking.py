'''Add resume submission tracking, skill-gap snapshots, and outreach history.

Revision ID: 20260827_0033
Revises: 20260826_0032
Create Date: 2026-08-27

Historical submission state and resume-content snapshots are best-effort proxies:
viewed/shortlisted were never recorded, and pre-migration resume content may have
changed since it was sent. Downgrade is intentionally a one-way door once manual
applications with nullable recruiter references exist.
'''

from __future__ import annotations

import json
import re

from alembic import op
import sqlalchemy as sa


revision = '20260827_0033'
down_revision = '20260826_0032'
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {
        index['name']
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get('name')
    }


def _unique_constraints(table_name: str) -> set[str]:
    return {
        constraint['name']
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if constraint.get('name')
    }


def _add_columns(table_name: str, additions: tuple[sa.Column, ...]) -> None:
    existing = _columns(table_name)
    for column in additions:
        if column.name not in existing:
            op.add_column(table_name, column)


def _create_index(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns)


def _create_side_tables() -> None:
    tables = _table_names()
    if 'application_skill_gap_snapshots' not in tables:
        op.create_table(
            'application_skill_gap_snapshots',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('owner_id', sa.String(length=100), nullable=False),
            sa.Column('application_id', sa.Integer(), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False, server_default='fallback_text'),
            sa.Column('matched_required_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('missing_required_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('matched_preferred_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('missing_preferred_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('computed_at', sa.DateTime(), nullable=False),
            sa.UniqueConstraint('owner_id', 'application_id', name='ux_skill_gap_snapshot_application'),
        )
        for name, columns in (
            ('ix_application_skill_gap_snapshots_id', ['id']),
            ('ix_application_skill_gap_snapshots_owner_id', ['owner_id']),
            ('ix_application_skill_gap_snapshots_application_id', ['application_id']),
        ):
            _create_index(name, 'application_skill_gap_snapshots', columns)
    if 'application_outreach_messages' not in tables:
        op.create_table(
            'application_outreach_messages',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('owner_id', sa.String(length=100), nullable=False),
            sa.Column('application_id', sa.Integer(), nullable=False),
            sa.Column('message_kind', sa.String(length=40), nullable=False),
            sa.Column('draft_source', sa.String(length=20), nullable=False, server_default='unknown'),
            sa.Column('ai_model', sa.String(length=80), nullable=True),
            sa.Column('subject', sa.Text(), nullable=False, server_default=''),
            sa.Column('body', sa.Text(), nullable=False, server_default=''),
            sa.Column('sent_at', sa.DateTime(), nullable=False),
        )
        for name, columns in (
            ('ix_application_outreach_messages_id', ['id']),
            ('ix_application_outreach_messages_owner_id', ['owner_id']),
            ('ix_application_outreach_messages_application_id', ['application_id']),
            ('ix_application_outreach_messages_sent_at', ['sent_at']),
        ):
            _create_index(name, 'application_outreach_messages', columns)


def _add_model_columns() -> None:
    _add_columns(
        'applications',
        (
            sa.Column('manual_recruiter_name', sa.String(length=255), nullable=True, server_default=''),
            sa.Column('manual_recruiter_company', sa.String(length=255), nullable=True, server_default=''),
            sa.Column('manual_recruiter_email', sa.String(length=255), nullable=True, server_default=''),
            sa.Column('manual_recruiter_phone', sa.String(length=80), nullable=True, server_default=''),
            sa.Column('manual_recruiter_linkedin_url', sa.Text(), nullable=True, server_default=''),
            sa.Column('manual_job_title', sa.Text(), nullable=True, server_default=''),
            sa.Column('manual_end_client', sa.Text(), nullable=True, server_default=''),
            sa.Column('manual_jd_text', sa.Text(), nullable=True, server_default=''),
            sa.Column('manual_source_note', sa.Text(), nullable=True, server_default=''),
            sa.Column('resume_submission_status', sa.String(length=30), nullable=True, server_default='not_submitted'),
            sa.Column('resume_submitted_at', sa.DateTime(), nullable=True),
            sa.Column('submission_method', sa.String(length=20), nullable=True, server_default='email'),
            sa.Column('rejection_detail_tags_json', sa.Text(), nullable=True, server_default='[]'),
            sa.Column('dedupe_key', sa.String(length=64), nullable=True),
            sa.Column('resume_skills_snapshot_json', sa.Text(), nullable=True, server_default='[]'),
            sa.Column('resume_primary_role_snapshot', sa.String(length=255), nullable=True, server_default=''),
            sa.Column('milestones_reached_json', sa.Text(), nullable=True, server_default='{}'),
        ),
    )
    _add_columns(
        'resume_assets',
        (
            sa.Column('primary_role', sa.String(length=255), nullable=False, server_default=''),
            sa.Column('structured_skills_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('variant_label', sa.String(length=120), nullable=False, server_default=''),
        ),
    )
    _add_columns(
        'application_suggestions',
        (sa.Column('payload_json', sa.Text(), nullable=False, server_default='{}'),),
    )
    _add_columns(
        'user_settings',
        (
            sa.Column('feature_resume_tracking_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('feature_resume_tracking_sweep_interval_minutes', sa.Integer(), nullable=False, server_default='240'),
        ),
    )


def _skill_snapshot(resume: dict[str, object] | None) -> list[str]:
    if not resume:
        return []
    try:
        structured = json.loads(str(resume.get('structured_skills_json') or '[]'))
    except (TypeError, ValueError, json.JSONDecodeError):
        structured = []
    if isinstance(structured, list):
        values = [str(value).strip() for value in structured if str(value).strip()]
        if values:
            return list(dict.fromkeys(values))
    return list(
        dict.fromkeys(
            value.strip()
            for value in re.split(r'[,;\n]+', str(resume.get('skills_text') or ''))
            if value.strip()
        )
    )


def _backfill_applications() -> None:
    bind = op.get_bind()
    applications = sa.table(
        'applications',
        *(
            sa.column(name)
            for name in (
                'id', 'status', 'resume_asset_id', 'resume_shared_at', 'created_at',
                'resume_submission_status', 'resume_submitted_at', 'submission_method',
                'rejection_detail_tags_json', 'resume_skills_snapshot_json',
                'resume_primary_role_snapshot', 'milestones_reached_json',
            )
        ),
    )
    resumes = sa.table(
        'resume_assets',
        sa.column('id'),
        sa.column('skills_text'),
        sa.column('structured_skills_json'),
        sa.column('primary_role'),
    )
    events = sa.table(
        'application_events',
        sa.column('application_id'),
        sa.column('event_type'),
        sa.column('metadata_json'),
        sa.column('occurred_at'),
    )
    resume_rows = {
        row['id']: dict(row)
        for row in bind.execute(sa.select(resumes)).mappings()
    }
    event_rows: dict[int, list[dict[str, object]]] = {}
    for row in bind.execute(
        sa.select(events)
        .where(events.c.event_type == 'status_changed')
        .order_by(events.c.occurred_at.asc())
    ).mappings():
        event_rows.setdefault(row['application_id'], []).append(dict(row))

    not_submitted = {'matched', 'contacted', 'recruiter_responded'}
    interviews = {'interview_1', 'interview_2', 'final_interview'}
    for row in bind.execute(sa.select(applications)).mappings():
        status = str(row['status'] or 'matched')
        if status in not_submitted:
            resume_status = 'not_submitted'
            submitted_at = None
        elif status in interviews:
            resume_status = 'interview_scheduled'
            submitted_at = row['resume_shared_at'] or row['created_at']
        elif status == 'offer':
            resume_status = 'offered'
            submitted_at = row['resume_shared_at'] or row['created_at']
        elif status in {'hired', 'rejected', 'withdrawn'}:
            resume_status = status
            submitted_at = row['resume_shared_at'] or row['created_at']
        else:
            resume_status = 'submitted'
            submitted_at = row['resume_shared_at'] or row['created_at']

        milestones: dict[str, str] = {}
        for event in event_rows.get(row['id'], []):
            try:
                target = json.loads(str(event['metadata_json'] or '{}')).get('to')
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            milestone = (
                'interview_scheduled' if target in interviews
                else 'offered' if target == 'offer'
                else 'hired' if target == 'hired'
                else None
            )
            if milestone and milestone not in milestones and event['occurred_at'] is not None:
                occurred_at = event['occurred_at']
                milestones[milestone] = (
                    occurred_at.isoformat() if hasattr(occurred_at, 'isoformat') else str(occurred_at)
                )

        resume = resume_rows.get(row['resume_asset_id'])
        bind.execute(
            applications.update()
            .where(applications.c.id == row['id'])
            .values(
                resume_submission_status=resume_status,
                resume_submitted_at=submitted_at,
                submission_method='email',
                rejection_detail_tags_json='[]',
                resume_skills_snapshot_json=json.dumps(_skill_snapshot(resume), separators=(',', ':')),
                resume_primary_role_snapshot=str((resume or {}).get('primary_role') or ''),
                milestones_reached_json=json.dumps(milestones, separators=(',', ':')),
            )
        )


def _finalize_application_shape() -> None:
    bind = op.get_bind()
    unique_constraints = _unique_constraints('applications')
    required = {
        'manual_recruiter_name': sa.String(length=255),
        'manual_recruiter_company': sa.String(length=255),
        'manual_recruiter_email': sa.String(length=255),
        'manual_recruiter_phone': sa.String(length=80),
        'manual_recruiter_linkedin_url': sa.Text(),
        'manual_job_title': sa.Text(),
        'manual_end_client': sa.Text(),
        'manual_jd_text': sa.Text(),
        'manual_source_note': sa.Text(),
        'resume_submission_status': sa.String(length=30),
        'submission_method': sa.String(length=20),
        'rejection_detail_tags_json': sa.Text(),
        'resume_skills_snapshot_json': sa.Text(),
        'resume_primary_role_snapshot': sa.String(length=255),
        'milestones_reached_json': sa.Text(),
    }
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('applications') as batch_op:
            batch_op.alter_column('recruiter_opportunity_id', existing_type=sa.Integer(), nullable=True)
            batch_op.alter_column('recruiter_contact_id', existing_type=sa.Integer(), nullable=True)
            for name, column_type in required.items():
                batch_op.alter_column(name, existing_type=column_type, nullable=False, server_default=None)
            if 'ux_applications_owner_resume_opportunity' in unique_constraints:
                batch_op.drop_constraint('ux_applications_owner_resume_opportunity', type_='unique')
            if 'ux_applications_owner_dedupe_key' not in unique_constraints:
                batch_op.create_unique_constraint('ux_applications_owner_dedupe_key', ['owner_id', 'dedupe_key'])
    else:
        op.alter_column('applications', 'recruiter_opportunity_id', existing_type=sa.Integer(), nullable=True)
        op.alter_column('applications', 'recruiter_contact_id', existing_type=sa.Integer(), nullable=True)
        for name, column_type in required.items():
            op.alter_column(
                'applications',
                name,
                existing_type=column_type,
                nullable=False,
                server_default=None,
            )
        if 'ux_applications_owner_resume_opportunity' in unique_constraints:
            op.drop_constraint('ux_applications_owner_resume_opportunity', 'applications', type_='unique')
        if 'ux_applications_owner_dedupe_key' not in unique_constraints:
            op.create_unique_constraint(
                'ux_applications_owner_dedupe_key',
                'applications',
                ['owner_id', 'dedupe_key'],
            )
    _create_index('ix_applications_resume_submission_status', 'applications', ['resume_submission_status'])


def upgrade() -> None:
    _create_side_tables()
    _add_model_columns()
    _backfill_applications()
    _finalize_application_shape()


def downgrade() -> None:
    bind = op.get_bind()
    if 'applications' in _table_names():
        unique_constraints = _unique_constraints('applications')
        new_columns = (
            'milestones_reached_json',
            'resume_primary_role_snapshot',
            'resume_skills_snapshot_json',
            'dedupe_key',
            'rejection_detail_tags_json',
            'submission_method',
            'resume_submitted_at',
            'resume_submission_status',
            'manual_source_note',
            'manual_jd_text',
            'manual_end_client',
            'manual_job_title',
            'manual_recruiter_linkedin_url',
            'manual_recruiter_phone',
            'manual_recruiter_email',
            'manual_recruiter_company',
            'manual_recruiter_name',
        )
        if bind.dialect.name == 'sqlite':
            with op.batch_alter_table('applications') as batch_op:
                if 'ix_applications_resume_submission_status' in _indexes('applications'):
                    batch_op.drop_index('ix_applications_resume_submission_status')
                if 'ux_applications_owner_dedupe_key' in unique_constraints:
                    batch_op.drop_constraint('ux_applications_owner_dedupe_key', type_='unique')
                batch_op.create_unique_constraint(
                    'ux_applications_owner_resume_opportunity',
                    ['owner_id', 'resume_asset_id', 'recruiter_opportunity_id'],
                )
                batch_op.alter_column('recruiter_opportunity_id', existing_type=sa.Integer(), nullable=False)
                batch_op.alter_column('recruiter_contact_id', existing_type=sa.Integer(), nullable=False)
                for name in new_columns:
                    if name in _columns('applications'):
                        batch_op.drop_column(name)
        else:
            if 'ix_applications_resume_submission_status' in _indexes('applications'):
                op.drop_index('ix_applications_resume_submission_status', table_name='applications')
            if 'ux_applications_owner_dedupe_key' in unique_constraints:
                op.drop_constraint('ux_applications_owner_dedupe_key', 'applications', type_='unique')
            op.create_unique_constraint(
                'ux_applications_owner_resume_opportunity',
                'applications',
                ['owner_id', 'resume_asset_id', 'recruiter_opportunity_id'],
            )
            op.alter_column('applications', 'recruiter_opportunity_id', existing_type=sa.Integer(), nullable=False)
            op.alter_column('applications', 'recruiter_contact_id', existing_type=sa.Integer(), nullable=False)
            for name in new_columns:
                if name in _columns('applications'):
                    op.drop_column('applications', name)

    for table_name, column_names in (
        ('user_settings', ('feature_resume_tracking_sweep_interval_minutes', 'feature_resume_tracking_enabled')),
        ('resume_assets', ('variant_label', 'structured_skills_json', 'primary_role')),
        ('application_suggestions', ('payload_json',)),
    ):
        if table_name not in _table_names():
            continue
        for name in column_names:
            if name in _columns(table_name):
                op.drop_column(table_name, name)
    for table_name in ('application_outreach_messages', 'application_skill_gap_snapshots'):
        if table_name in _table_names():
            op.drop_table(table_name)
