from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.gmail_client import GmailMessageCandidate
from app.models import EmailConversation, EmailOpenEvent, EmailReplyMessage, RecruiterEmail
from app.parsing.document_extraction import extract_gmail_reply_body
from app.recent_runs import build_gmail_message_url
from app.schemas import ConversationDetailResponse, ConversationMessageResponse, ConversationSummaryResponse


TRANSPARENT_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def generate_tracking_token(secret: str, recruiter_email_id: int) -> str:
    payload = f"{recruiter_email_id}:{uuid.uuid4()}".encode()
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:22]


def tracking_pixel_url(public_base_url: str, token: str | None) -> str | None:
    base_url = (public_base_url or "").strip().rstrip("/")
    if not token or not base_url.lower().startswith("https://"):
        return None
    return f"{base_url}/track/open/{token}.png"


def _sent_thread_root(db: Session, owner_id: str, thread_id: str) -> RecruiterEmail | None:
    return (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.external_thread_id == thread_id,
            RecruiterEmail.sent_status == "sent",
        )
        .order_by(RecruiterEmail.sent_at.desc(), RecruiterEmail.id.desc())
        .first()
    )


def ensure_sent_conversation(
    db: Session,
    *,
    owner_id: str,
    root_email: RecruiterEmail,
    thread_id: str,
    sent_rfc_message_id: str | None = None,
) -> EmailConversation:
    conversation = (
        db.query(EmailConversation)
        .filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.external_thread_id == thread_id,
        )
        .first()
    )
    sent_at = root_email.sent_at or datetime.now(UTC)
    if conversation is None:
        conversation = EmailConversation(
            owner_id=owner_id,
            root_recruiter_email_id=root_email.id,
            external_thread_id=thread_id,
            status="sent",
            last_message_at=sent_at,
            unread_reply_count=0,
        )
        db.add(conversation)
        db.flush()

    sent_message_id = (root_email.gmail_sent_id or "").strip() or f"sent:{root_email.id}"
    existing_sent = (
        db.query(EmailReplyMessage.id)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.external_message_id == sent_message_id,
        )
        .first()
    )
    if not existing_sent:
        db.add(
            EmailReplyMessage(
                owner_id=owner_id,
                conversation_id=conversation.id,
                direction="outbound",
                external_message_id=sent_message_id,
                external_rfc_message_id=sent_rfc_message_id,
                sender="me",
                body=root_email.draft_reply or "",
                snippet=(root_email.draft_reply or "")[:240],
                received_at=sent_at,
                read_at=sent_at,
            )
        )
    return conversation


def capture_inbound_reply(
    db: Session,
    *,
    owner_id: str,
    item: GmailMessageCandidate,
    owner_email: str,
) -> tuple[bool, bool]:
    thread_id = (item.get("external_thread_id") or "").strip()
    if not thread_id:
        return False, False
    # Thread scans (list_thread_messages) return every message in the thread,
    # including ones we sent ourselves. Without this check they'd get stored as
    # a second "inbound" row under our own name instead of being recognized as
    # already covered by the outbound row written at send time.
    _, sender_email = parseaddr(item.get("sender") or "")
    if sender_email.strip().lower() == owner_email.strip().lower():
        return True, False
    conversation = (
        db.query(EmailConversation)
        .filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.external_thread_id == thread_id,
        )
        .first()
    )
    root_email = None
    if conversation is not None:
        root_email = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == owner_id,
                RecruiterEmail.id == conversation.root_recruiter_email_id,
                RecruiterEmail.sent_status == "sent",
            )
            .first()
        )
    if root_email is None:
        root_email = _sent_thread_root(db, owner_id, thread_id)
    if root_email is None:
        header_text = f"{item.get('in_reply_to_header') or ''} {item.get('references_header') or ''}".lower()
        outbound = (
            db.query(EmailReplyMessage, EmailConversation, RecruiterEmail)
            .join(EmailConversation, EmailConversation.id == EmailReplyMessage.conversation_id)
            .join(RecruiterEmail, RecruiterEmail.id == EmailConversation.root_recruiter_email_id)
            .filter(
                EmailReplyMessage.owner_id == owner_id,
                EmailReplyMessage.direction == "outbound",
                EmailReplyMessage.external_rfc_message_id.is_not(None),
                RecruiterEmail.sent_status == "sent",
            )
            .all()
        )
        match = next(
            (
                (candidate_conversation, candidate_email)
                for message, candidate_conversation, candidate_email in outbound
                if message.external_rfc_message_id
                and message.external_rfc_message_id.strip().lower() in header_text
            ),
            None,
        )
        if match is not None:
            conversation, root_email = match
            conversation.external_thread_id = thread_id
    if root_email is None:
        return False, False

    message_id = item["external_message_id"]
    # The JD-scan query re-returns this same message every run as long as it stays
    # unread in Gmail (we never mark source messages read). Once a sent conversation
    # exists for its thread, that's indistinguishable from a genuine reply unless we
    # recognize it as the original message the candidate/reply was seeded from.
    if root_email.external_message_id and message_id == root_email.external_message_id:
        return True, False

    if conversation is None:
        conversation = ensure_sent_conversation(
            db,
            owner_id=owner_id,
            root_email=root_email,
            thread_id=thread_id,
        )
    existing = (
        db.query(EmailReplyMessage.id)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.external_message_id == message_id,
        )
        .first()
    )
    if existing:
        return True, False

    received_at = item.get("gmail_received_at") or datetime.now(UTC)
    body = extract_gmail_reply_body(item.get("body", ""))
    db.add(
        EmailReplyMessage(
            owner_id=owner_id,
            conversation_id=conversation.id,
            direction="inbound",
            external_message_id=message_id,
            external_rfc_message_id=item.get("external_rfc_message_id"),
            in_reply_to_header=item.get("in_reply_to_header"),
            references_header=item.get("references_header"),
            sender=item.get("sender", ""),
            body=body,
            snippet=(item.get("snippet") or body)[:240],
            received_at=received_at,
        )
    )
    conversation.status = "replied"
    conversation.last_message_at = received_at
    conversation.unread_reply_count += 1
    return True, True


def record_open(
    db: Session,
    *,
    token: str,
    user_agent: str,
    remote_ip: str,
) -> None:
    email = db.query(RecruiterEmail).filter(RecruiterEmail.tracking_token == token).first()
    if email is None:
        return
    now = datetime.now(UTC)
    email.open_count = int(email.open_count or 0) + 1
    if email.opened_at is None:
        email.opened_at = now
    db.add(
        EmailOpenEvent(
            owner_id=email.owner_id,
            recruiter_email_id=email.id,
            occurred_at=now,
            user_agent=user_agent[:2000],
            remote_ip=remote_ip[:100],
            is_likely_proxy="googleimageproxy" in user_agent.lower(),
        )
    )
    conversation = (
        db.query(EmailConversation)
        .filter(
            EmailConversation.owner_id == email.owner_id,
            EmailConversation.root_recruiter_email_id == email.id,
        )
        .first()
    )
    if conversation is not None and conversation.status == "sent":
        conversation.status = "opened"
    db.commit()


def _conversation_or_404(db: Session, owner_id: str, conversation_id: int) -> tuple[EmailConversation, RecruiterEmail]:
    conversation = (
        db.query(EmailConversation)
        .filter(EmailConversation.owner_id == owner_id, EmailConversation.id == conversation_id)
        .first()
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    root_email = (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.id == conversation.root_recruiter_email_id,
        )
        .first()
    )
    if root_email is None:
        raise HTTPException(status_code=404, detail="Conversation source email not found")
    return conversation, root_email


def _summary(db: Session, conversation: EmailConversation, root_email: RecruiterEmail) -> ConversationSummaryResponse:
    latest = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == conversation.owner_id,
            EmailReplyMessage.conversation_id == conversation.id,
        )
        .order_by(EmailReplyMessage.received_at.desc(), EmailReplyMessage.id.desc())
        .first()
    )
    recruiter_name, _ = parseaddr(root_email.sender or "")
    return ConversationSummaryResponse(
        id=conversation.id,
        root_recruiter_email_id=root_email.id,
        recruiter=recruiter_name or (root_email.recipient_email or root_email.sender or "Unknown"),
        recruiter_email=root_email.recipient_email,
        subject=root_email.subject,
        status=conversation.status,
        last_message_preview=((latest.snippet or latest.body)[:240] if latest else (root_email.draft_reply or "")[:240]),
        last_message_at=conversation.last_message_at,
        unread_reply_count=conversation.unread_reply_count,
        gmail_thread_link=build_gmail_message_url(
            external_message_id=root_email.external_message_id,
            external_thread_id=conversation.external_thread_id,
            external_rfc_message_id=root_email.external_rfc_message_id,
        ),
    )


def list_conversations(db: Session, owner_id: str, *, recruiter: str | None = None, subject: str | None = None, status: str | None = None, unread_only: bool | None = None, sort: str = "newest", date_from: datetime | None = None, date_to: datetime | None = None) -> list[ConversationSummaryResponse]:
    if sort not in {"newest", "oldest", "unread_first"}:
        raise HTTPException(status_code=422, detail="Invalid sort. Must be one of: newest, oldest, unread_first")

    # last_message_at also moves on outbound replies (see send_conversation_reply), so it
    # can't be trusted as "when they replied" — compute that straight from inbound messages.
    last_inbound = (
        db.query(
            EmailReplyMessage.conversation_id.label("conversation_id"),
            func.max(EmailReplyMessage.received_at).label("last_inbound_at"),
        )
        .filter(EmailReplyMessage.owner_id == owner_id, EmailReplyMessage.direction == "inbound")
        .group_by(EmailReplyMessage.conversation_id)
        .subquery()
    )
    query = (
        db.query(EmailConversation, RecruiterEmail, last_inbound.c.last_inbound_at)
        .join(RecruiterEmail, RecruiterEmail.id == EmailConversation.root_recruiter_email_id)
        .outerjoin(last_inbound, last_inbound.c.conversation_id == EmailConversation.id)
        .filter(EmailConversation.owner_id == owner_id, RecruiterEmail.owner_id == owner_id)
    )
    if recruiter and recruiter.strip(): query = query.filter(RecruiterEmail.sender.ilike(f"%{recruiter.strip()}%"))
    if subject and subject.strip(): query = query.filter(RecruiterEmail.subject.ilike(f"%{subject.strip()}%"))
    if status:
        values = [value.strip() for value in status.split(",") if value.strip()]
        if values: query = query.filter(EmailConversation.status.in_(values))
    if unread_only is not None: query = query.filter(EmailConversation.unread_reply_count > 0 if unread_only else EmailConversation.unread_reply_count == 0)
    if date_from is not None: query = query.filter(EmailConversation.last_message_at >= date_from)
    if date_to is not None: query = query.filter(EmailConversation.last_message_at < date_to)
    if sort == "oldest": query = query.order_by(EmailConversation.last_message_at.asc(), EmailConversation.id.asc())
    elif sort == "unread_first": query = query.order_by((EmailConversation.unread_reply_count > 0).desc(), func.coalesce(last_inbound.c.last_inbound_at, EmailConversation.last_message_at).desc(), EmailConversation.id.desc())
    else: query = query.order_by(EmailConversation.last_message_at.desc(), EmailConversation.id.desc())
    rows = query.all()
    return [
        _summary(db, conversation, root_email).model_copy(update={"last_inbound_reply_at": last_inbound_at})
        for conversation, root_email, last_inbound_at in rows
    ]


def conversation_detail(db: Session, owner_id: str, conversation_id: int) -> ConversationDetailResponse:
    conversation, root_email = _conversation_or_404(db, owner_id, conversation_id)
    summary = _summary(db, conversation, root_email)
    rows = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.conversation_id == conversation_id,
        )
        .order_by(EmailReplyMessage.received_at.asc(), EmailReplyMessage.id.asc())
        .all()
    )
    return ConversationDetailResponse(
        **summary.model_dump(),
        to_email=root_email.recipient_email,
        cc_email=root_email.cc_email,
        messages=[
            ConversationMessageResponse(
                id=row.id,
                direction=row.direction,
                sender=row.sender,
                body=row.body,
                snippet=row.snippet,
                occurred_at=row.received_at,
                read_at=row.read_at,
            )
            for row in rows
        ],
    )


def mark_conversation_read(db: Session, owner_id: str, conversation_id: int) -> ConversationDetailResponse:
    conversation, _ = _conversation_or_404(db, owner_id, conversation_id)
    now = datetime.now(UTC)
    (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.conversation_id == conversation_id,
            EmailReplyMessage.direction == "inbound",
            EmailReplyMessage.read_at.is_(None),
        )
        .update({EmailReplyMessage.read_at: now}, synchronize_session=False)
    )
    conversation.unread_reply_count = 0
    db.commit()
    return conversation_detail(db, owner_id, conversation_id)


def send_conversation_reply(
    db: Session,
    *,
    owner_id: str,
    conversation_id: int,
    body: str,
    sender: str,
    draft_text_size: str,
    tracking_url: str | None,
    send_reply: Callable[..., str],
) -> ConversationDetailResponse:
    conversation, root_email = _conversation_or_404(db, owner_id, conversation_id)
    recipient = (root_email.recipient_email or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="Conversation recipient is missing")
    try:
        message_id = send_reply(
            conversation.external_thread_id,
            recipient,
            root_email.cc_email,
            root_email.subject,
            body.strip(),
            draft_text_size=draft_text_size,
            tracking_pixel_url=tracking_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc
    now = datetime.now(UTC)
    db.add(
        EmailReplyMessage(
            owner_id=owner_id,
            conversation_id=conversation.id,
            direction="outbound",
            external_message_id=message_id or f"inbox:{uuid.uuid4()}",
            sender=sender or "me",
            body=body.strip(),
            snippet=body.strip()[:240],
            received_at=now,
            read_at=now,
        )
    )
    conversation.last_message_at = now
    db.commit()
    return conversation_detail(db, owner_id, conversation_id)


def reply_count_for_email(db: Session, owner_id: str, recruiter_email_id: int) -> int:
    count = (
        db.query(func.count(EmailReplyMessage.id))
        .join(EmailConversation, EmailConversation.id == EmailReplyMessage.conversation_id)
        .filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.root_recruiter_email_id == recruiter_email_id,
            EmailReplyMessage.direction == "inbound",
        )
        .scalar()
    )
    return int(count or 0)
