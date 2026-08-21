from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import gmail_client
from app.ai.application_outreach_prompting import MessageKind, build_application_outreach_prompts
from app.ai.deepseek_client import deepseek_chat_completion
from app.ai.draft_text_utils import extract_subject_line, sanitize_plain_text
from app.ai.resume_context import extract_resume_context
from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_EXTRACT_FAILED,
    classify_extracted_resume_context,
)
from app.ai.truthfulness import enforce_truthfulness
from app.models import (
    AttachmentAsset,
    Application,
    ApplicationEvent,
    PremiumNumberContact,
    RecruiterEmail,
    ResumeAsset,
    UserSettings,
    utc_now,
)
from app.services.application_service import (
    ApplicationReferenceNotFoundError,
    ApplicationValidationError,
    append_event,
)


@dataclass(frozen=True)
class OutreachRecipient:
    to: str
    cc: str | None
    thread_id: str | None
    source_recruiter_email_id: int | None


@dataclass(frozen=True)
class ApplicationDraftResult:
    to: str
    cc: str | None
    thread_id: str | None
    subject: str
    body: str
    source: str
    ai_model: str | None
    ai_error: str | None
    resume_context_status: str
    resume_file_name: str
    message_kind: MessageKind


def _latest_linked_email(db: Session, application: Application) -> RecruiterEmail | None:
    latest_link = (
        db.query(ApplicationEvent)
        .filter(
            ApplicationEvent.owner_id == application.owner_id,
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.linked_recruiter_email_id.isnot(None),
        )
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc())
        .first()
    )
    if latest_link is None:
        return None
    return (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == application.owner_id,
            RecruiterEmail.id == latest_link.linked_recruiter_email_id,
        )
        .first()
    )


def resolve_recipient(db: Session, application: Application) -> OutreachRecipient:
    """Use the latest linked Gmail thread, then fall back to the recruiter contact."""
    email = _latest_linked_email(db, application)
    if email is not None and (email.recipient_email or "").strip():
        return OutreachRecipient(
            to=email.recipient_email.strip(),
            cc=(email.cc_email or "").strip() or None,
            thread_id=((email.external_thread_id or "").strip() or None) if email.source == "gmail" else None,
            source_recruiter_email_id=email.id,
        )

    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == application.owner_id,
            PremiumNumberContact.id == application.recruiter_contact_id,
        )
        .first()
    )
    if recruiter is not None and (recruiter.recruiter_email or "").strip():
        return OutreachRecipient(
            to=recruiter.recruiter_email.strip(),
            cc=None,
            thread_id=None,
            source_recruiter_email_id=None,
        )
    raise ApplicationValidationError("No recipient email available for this application")


def _recent_timeline_text(db: Session, application: Application, *, limit: int = 6) -> str:
    events = (
        db.query(ApplicationEvent)
        .filter(
            ApplicationEvent.owner_id == application.owner_id,
            ApplicationEvent.application_id == application.id,
        )
        .order_by(ApplicationEvent.occurred_at.desc(), ApplicationEvent.id.desc())
        .limit(limit)
        .all()
    )
    lines = [
        f"{event.occurred_at.date().isoformat()} - {event.event_type}: {event.note}".strip(" :")
        for event in reversed(events)
    ]
    return "\n".join(line for line in lines if line)


def _split_generated_draft(text: str, fallback_subject: str) -> tuple[str, str]:
    subject_line = extract_subject_line(text)
    subject = subject_line.partition(":")[2].strip() if subject_line else fallback_subject
    body = "\n".join(line for line in text.splitlines() if line.strip() != subject_line).strip()
    return subject or fallback_subject, body


def build_application_draft(
    db: Session,
    application: Application,
    *,
    message_kind: MessageKind,
    user_settings: UserSettings,
    model_name: str,
) -> ApplicationDraftResult:
    recipient = resolve_recipient(db, application)
    resume = (
        db.query(ResumeAsset)
        .filter(
            ResumeAsset.owner_id == application.owner_id,
            ResumeAsset.id == application.resume_asset_id,
        )
        .first()
    )
    if resume is None:
        raise ApplicationReferenceNotFoundError("Resume for this application no longer exists")

    resume_text = ""
    context_status = RESUME_CONTEXT_EXTRACT_FAILED
    try:
        resume_text = extract_resume_context(resume.file_path, resume.file_name)
        context_status = classify_extracted_resume_context(resume_text).status
    except Exception:
        context_status = RESUME_CONTEXT_EXTRACT_FAILED

    if not user_settings.feature_application_outreach_drafts_enabled:
        return ApplicationDraftResult(
            to=recipient.to,
            cc=recipient.cc,
            thread_id=recipient.thread_id,
            subject=_fallback_subject(application, message_kind),
            body=_fallback_draft(application, message_kind, user_settings),
            source="ai_disabled",
            ai_model=None,
            ai_error=None,
            resume_context_status=context_status,
            resume_file_name=resume.file_name,
            message_kind=message_kind,
        )

    system_prompt, user_prompt = build_application_outreach_prompts(
        message_kind=message_kind,
        job_title=application.job_title_snapshot,
        end_client=application.end_client_snapshot,
        recruiter_name=application.recruiter_name_snapshot,
        status=application.status,
        timeline_text=_recent_timeline_text(db, application),
        resume_text=resume_text,
        signature_name=user_settings.signature_name,
        signature_phone=user_settings.signature_phone,
        signature_email=user_settings.signature_email,
    )
    try:
        generated = deepseek_chat_completion(system_prompt, user_prompt, model_name=model_name)
        sanitized = enforce_truthfulness(sanitize_plain_text(generated), resume_text)
        subject, body = _split_generated_draft(
            sanitized,
            _fallback_subject(application, message_kind),
        )
        if not body:
            raise RuntimeError("AI returned an empty draft")
        return ApplicationDraftResult(
            to=recipient.to,
            cc=recipient.cc,
            thread_id=recipient.thread_id,
            subject=subject,
            body=body,
            source="deepseek",
            ai_model=model_name,
            ai_error=None,
            resume_context_status=context_status,
            resume_file_name=resume.file_name,
            message_kind=message_kind,
        )
    except Exception as exc:
        return ApplicationDraftResult(
            to=recipient.to,
            cc=recipient.cc,
            thread_id=recipient.thread_id,
            subject=_fallback_subject(application, message_kind),
            body=_fallback_draft(application, message_kind, user_settings),
            source="rules_only",
            ai_model=model_name,
            ai_error=str(exc),
            resume_context_status=context_status,
            resume_file_name=resume.file_name,
            message_kind=message_kind,
        )


def send_application_message(
    db: Session,
    application: Application,
    *,
    to: str,
    cc: str | None,
    subject: str,
    body: str,
    thread_id: str | None,
    message_kind: MessageKind,
    include_resume: bool,
    attachment_asset_ids: list[int],
) -> tuple[Application, str]:
    to = to.strip()
    if not to:
        raise ApplicationValidationError("Recipient email is required")
    if not body.strip():
        raise ApplicationValidationError("Message body is required")

    mail_attachments: list[gmail_client.MailAttachment] = []
    attachment_names: list[str] = []
    if include_resume:
        resume = (
            db.query(ResumeAsset)
            .filter(
                ResumeAsset.owner_id == application.owner_id,
                ResumeAsset.id == application.resume_asset_id,
            )
            .first()
        )
        if resume is None:
            raise ApplicationReferenceNotFoundError("Resume for this application no longer exists")
        mail_attachments.append(
            gmail_client.MailAttachment(
                path=resume.file_path,
                display_name=resume.file_name,
                mime_type=resume.mime_type,
            )
        )
        attachment_names.append(resume.file_name)

    for attachment_id in dict.fromkeys(attachment_asset_ids):
        asset = (
            db.query(AttachmentAsset)
            .filter(
                AttachmentAsset.owner_id == application.owner_id,
                AttachmentAsset.id == attachment_id,
                AttachmentAsset.is_enabled.is_(True),
            )
            .first()
        )
        if asset is None:
            raise ApplicationReferenceNotFoundError(f"Attachment {attachment_id} not found")
        mail_attachments.append(
            gmail_client.MailAttachment(
                path=asset.file_path,
                display_name=asset.file_name,
                mime_type=asset.mime_type,
            )
        )
        attachment_names.append(asset.file_name)

    linked_email = _latest_linked_email(db, application)
    send_args = {
        "to": to,
        "cc": (cc or "").strip() or None,
        "subject": subject.strip(),
        "body": body,
        "attachments": mail_attachments,
    }
    if thread_id and thread_id.strip():
        gmail_message_id = gmail_client.send_reply_with_attachment(
            thread_id=thread_id.strip(),
            **send_args,
        )
    else:
        gmail_message_id = gmail_client.send_new_email_with_attachment(**send_args)

    append_event(
        db,
        application,
        event_type="outreach_sent",
        event_source="user",
        note=body[:500],
        linked_recruiter_email_id=linked_email.id if linked_email is not None else None,
        metadata={
            "gmail_message_id": gmail_message_id,
            "subject": subject.strip(),
            "to": to,
            "cc": (cc or "").strip() or None,
            "attachment_names": attachment_names,
            "message_kind": message_kind,
        },
    )
    application.last_contact_at = utc_now()
    application.follow_up_count = (application.follow_up_count or 0) + 1
    return application, gmail_message_id


def _fallback_subject(application: Application, message_kind: MessageKind) -> str:
    if message_kind == "submission_to_recruiter":
        return (
            f"Resume for {application.job_title_snapshot or 'the role'} at "
            f"{application.end_client_snapshot or 'your client'}"
        )
    return f"Following up - {application.job_title_snapshot or 'the role'}"


def _fallback_draft(
    application: Application,
    message_kind: MessageKind,
    user_settings: UserSettings,
) -> str:
    if message_kind == "submission_to_recruiter":
        body = (
            f"Hi {application.recruiter_name_snapshot or 'there'},\n\n"
            f"Please find my resume attached for the {application.job_title_snapshot or 'role'} "
            f"opportunity at {application.end_client_snapshot or 'your client'}. Happy to complete an RTR "
            "or answer any questions so we can move this forward.\n\n"
        )
    else:
        body = (
            f"Hi {application.recruiter_name_snapshot or 'there'},\n\n"
            f"Checking in on the {application.job_title_snapshot or 'role'} opportunity at "
            f"{application.end_client_snapshot or 'your client'} - is there any update on next steps?\n\n"
        )
    signature = ["Best regards,", (user_settings.signature_name or "").strip() or "[Your name]"]
    signature.extend(
        value for value in (
            (user_settings.signature_phone or "").strip(),
            (user_settings.signature_email or "").strip(),
        )
        if value
    )
    return body + "\n".join(signature)
