import hashlib

from sqlalchemy import func, or_

from app.config import settings
from app.db import SessionLocal
from app.models import AppTSApplication, EmailConversation, EmailReplyMessage, RecruiterEmail, ResumeAsset, TrackedThread
from app.mcp_server.tools import needs, refused, untrusted
from app.services.email_inbox_service import conversation_label_names
from app import tenancy


def lookup_record(db, owner_id: str, message_id: str, *, recruiter_email_id: int | None = None) -> dict[str, object] | None:
    value = message_id.strip()
    if not value and recruiter_email_id is None:
        return None
    emails = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id)
    email = emails.filter(RecruiterEmail.id == recruiter_email_id).first() if recruiter_email_id else emails.filter(RecruiterEmail.external_message_id == value).order_by(RecruiterEmail.id).first()
    if email is None:
        email = emails.filter(func.lower(RecruiterEmail.external_rfc_message_id) == value.lower()).order_by(RecruiterEmail.id).first()
    conversations = db.query(EmailConversation).filter(EmailConversation.owner_id == owner_id)
    conversation = None
    thread = None
    if email is not None:
        conversation = conversations.filter(EmailConversation.external_thread_id == email.external_thread_id).first()
    else:
        reply = db.query(EmailReplyMessage).filter(EmailReplyMessage.owner_id == owner_id,
            or_(EmailReplyMessage.external_message_id == value, func.lower(EmailReplyMessage.external_rfc_message_id) == value.lower())).first()
        if reply:
            conversation = conversations.filter(EmailConversation.id == reply.conversation_id).first()
        if conversation is None:
            conversation = conversations.filter(EmailConversation.external_thread_id == value).first()
        if conversation is None:
            thread = db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.external_thread_id == value).first()
            if thread and thread.conversation_id:
                conversation = conversations.filter(EmailConversation.id == thread.conversation_id).first()
        if conversation and conversation.root_recruiter_email_id:
            email = emails.filter(RecruiterEmail.id == conversation.root_recruiter_email_id).first()
    if email is None and conversation is None and thread is None:
        return None
    thread_id = conversation.external_thread_id if conversation else email.external_thread_id if email else thread.external_thread_id
    if thread is None and thread_id:
        thread = db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.external_thread_id == thread_id).first()
    applications = db.query(AppTSApplication).filter(AppTSApplication.owner_id == owner_id, AppTSApplication.deleted_at.is_(None))
    conditions = []
    if thread_id:
        conditions.append(AppTSApplication.source_thread_id == thread_id)
    if email:
        conditions.append(AppTSApplication.source_recruiter_email_id == email.id)
    application = applications.filter(or_(*conditions)).order_by(AppTSApplication.id).first() if conditions else None
    return {
        "record_id": email.record_id if email else None,
        "origin": conversation.origin if conversation else "label" if thread else "email",
        "recruiter_email_id": email.id if email else None,
        "conversation_id": conversation.id if conversation else None,
        "thread_id": thread_id,
        "subject": email.subject if email else conversation.subject_snapshot if conversation else thread.subject_snapshot,
        "recruiter": email.sender if email else conversation.recruiter_snapshot if conversation else "",
        "labels": conversation_label_names(db, conversation) if conversation else [],
        "tracked": bool(application or (thread and thread.untracked_at is None)),
        "appts_application_id": application.id if application else None,
        "label_thread": bool(thread and thread.untracked_at is None),
    }


def _safe(result: dict[str, object]) -> dict[str, object]:
    return {**result, "subject": untrusted("email", str(result["subject"])),
        "recruiter": untrusted("email", str(result["recruiter"])),
        "labels": [untrusted("email", str(label)) for label in result["labels"]]}


def resolve_record_by_message_id(message_id: str) -> dict[str, object]:
    """Find the record behind a Gmail message id, RFC Message-ID or thread id. Read-only."""
    with SessionLocal() as db:
        result = lookup_record(db, tenancy.owner_id(), message_id)
        return _safe(result) if result else refused("no record for that message id", hint="Sync the tracked Gmail label, or provide a stored message or thread id.")


def propose_track_record(record_id: str = "", message_id: str = "", resume_asset_id: int = 0) -> dict[str, object]:
    """Prepare application tracking with an explicitly chosen resume. Never performs it."""
    with SessionLocal() as db:
        if record_id:
            email = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == tenancy.owner_id(), RecruiterEmail.record_id == record_id).order_by(RecruiterEmail.id).first()
            result = lookup_record(db, tenancy.owner_id(), "", recruiter_email_id=email.id) if email else None
        else:
            result = lookup_record(db, tenancy.owner_id(), message_id)
        if not result:
            return refused("no record for that message id", hint="Resolve a stored Gmail message or provide its Record ID first.")
        if result["appts_application_id"]:
            return {"status": "already_tracked", "appts_application_id": result["appts_application_id"], "record_id": result["record_id"]}
        if resume_asset_id <= 0:
            return needs(["resume_asset_id"], hint="Choose a stored resume version to lock into the application; use list_resumes to see the options.")
        resume = db.query(ResumeAsset).filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.id == resume_asset_id).first()
        if resume is None:
            return refused("resume not found", hint="Select a resume returned by list_resumes.")
        if not result["label_thread"] and result["recruiter_email_id"] is None:
            return refused("thread is no longer label tracked", hint="Apply a tracked Gmail label and sync again.")
        label_thread = bool(result["label_thread"])
        payload = {"resume_asset_id": resume.id} if label_thread else {
            "resume_asset_id": resume.id, "recruiter_email_id": result["recruiter_email_id"],
            "dedupe_key": hashlib.sha256(f'record:{tenancy.owner_id()}:{result["recruiter_email_id"]}'.encode()).hexdigest(),
        }
        return {
            "action": "propose_track_record", "record_kind": "label_thread" if label_thread else "requirement",
            "record_id": result["record_id"], "thread_id": result["thread_id"],
            "record_label": untrusted("email", str(result["subject"])),
            "recruiter": untrusted("email", str(result["recruiter"])),
            "labels": [untrusted("email", str(label)) for label in result["labels"]],
            "resume": untrusted("resume", f"{resume.file_name} (v{resume.version})"),
            "endpoint": f'/appts/label-threads/{result["thread_id"]}/promote' if label_thread else "/appts/applications",
            "fields": payload, "changes": [{"field": "tracked", "from": False, "to": True}], "count": 1,
        }
