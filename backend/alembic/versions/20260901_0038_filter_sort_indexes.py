"""Add filter and sort indexes.

Revision ID: 20260901_0038
Revises: 20260831_0037
"""
from alembic import op
revision="20260901_0038"
down_revision="20260831_0037"
branch_labels=None
depends_on=None
INDEXES=[
("ix_recruiter_email_owner_state_created","recruiter_emails",["owner_id","state","created_at"]),("ix_recruiter_email_owner_state_score","recruiter_emails",["owner_id","state","ats_score"]),("ix_recruiter_email_owner_state_sent_at","recruiter_emails",["owner_id","state","sent_at"]),("ix_recruiter_email_source","recruiter_emails",["source"]),("ix_recruiter_email_sendability_status","recruiter_emails",["sendability_status"]),("ix_recruiter_email_routing_status","recruiter_emails",["routing_status"]),("ix_email_conversation_owner_last_message","email_conversations",["owner_id","last_message_at"]),("ix_email_conversation_status","email_conversations",["status"]),("ix_email_reply_message_conversation_direction","email_reply_messages",["conversation_id","direction"]),("ix_number_review_queue_owner_state_updated","number_review_queue",["owner_id","state","updated_at"]),("ix_premium_number_contact_owner_deleted_updated","premium_number_contacts",["owner_id","deleted_at","updated_at"]),("ix_premium_number_contact_phone_is_valid","premium_number_contacts",["phone_is_valid"]),("ix_application_owner_deleted_created","applications",["owner_id","deleted_at","created_at"]),("ix_application_recruiter_opportunity_id","applications",["recruiter_opportunity_id"]),("ix_application_recruiter_contact_id","applications",["recruiter_contact_id"])]
def upgrade() -> None:
    for name,table,columns in INDEXES: op.create_index(name,table,columns)
def downgrade() -> None:
    for name,table,_ in reversed(INDEXES): op.drop_index(name,table_name=table)
