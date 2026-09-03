"""Proactively surface recruiter replies that need the user's attention.

Runs after a Gmail sync: for each inbound reply not yet reviewed, asks the
chat LLM whether it needs attention and, if so, posts a notification message
into the user's chat so it shows up next to the assistant - no new push
channel, just an assistant-role ChatMessage the widget already renders.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.ai.chat.history import message_text
from app.ai.chat.llm import build_chat_llm
from app.config import settings
from app.models import ChatMessage, ChatSession, EmailConversation, EmailReplyMessage, RecruiterEmail, utc_now

logger = logging.getLogger(__name__)

_PROMPT = """A recruiter reply just arrived that has not been reviewed yet. Decide whether the
job seeker should be proactively notified now, or whether it's routine and can wait
until they check the inbox themselves (e.g. a plain acknowledgment, an auto-reply,
or a reply with no action implied).

<untrusted_reply_data>
Recruiter: {sender}
Original role/subject: {subject}
Reply excerpt: {body}
</untrusted_reply_data>

Reply with exactly one of:
NO_ACTION_NEEDED
NOTIFY: <one or two sentence explanation of what happened, plus a concrete suggested next
step, ending by asking if they want you to draft it>

Never invent details not present in the excerpt above."""


async def _classify_reply(*, sender: str, subject: str, body: str) -> str | None:
    llm = build_chat_llm()
    prompt = _PROMPT.format(sender=sender, subject=subject, body=body[:2000])
    try:
        response = await llm.ainvoke(prompt)
    except Exception:
        logger.exception("proactive_notification_llm_call_failed")
        return None
    text = message_text(response.content).strip()
    if text.upper().startswith("NOTIFY:"):
        return text.split(":", 1)[1].strip()
    return None


def _target_session(db: Session) -> ChatSession:
    session = (
        db.query(ChatSession)
        .filter(ChatSession.owner_id == settings.owner_id)
        .order_by(ChatSession.updated_at.desc())
        .first()
    )
    if session is None:
        session = ChatSession(owner_id=settings.owner_id, title="Notifications")
        db.add(session)
        db.flush()
    return session


NOTIFICATIONS_SESSION_TITLE = "Notifications"


def _notifications_session(db: Session) -> ChatSession:
    """The dedicated Notifications session, created if absent.

    Deliberately not `_target_session`, which returns the most recently updated
    session. `chat_history_max_messages` is 20, so a daily digest landing in the
    conversation the user is actually having would evict a fifth of the model's
    usable history every morning.
    """
    session = (
        db.query(ChatSession)
        .filter(
            ChatSession.owner_id == settings.owner_id,
            ChatSession.title == NOTIFICATIONS_SESSION_TITLE,
        )
        .order_by(ChatSession.id.asc())
        .first()
    )
    if session is None:
        session = ChatSession(owner_id=settings.owner_id, title=NOTIFICATIONS_SESSION_TITLE)
        db.add(session)
        db.flush()
    return session


def notify_scheduled(db: Session, *, title: str, body: str, kind: str = "reminder") -> bool:
    """Post a scheduling notification. Best-effort: never raises.

    A notification failure must not fail the run that produced it - the work is
    already prepared and held, and losing the notice is better than losing the
    batch.
    """
    try:
        session = _notifications_session(db)
        db.add(
            ChatMessage(
                session_id=session.id,
                role="assistant",
                content=f"**{title}**\n\n{body}",
            )
        )
        session.updated_at = utc_now()
        db.commit()
    except Exception:
        logger.exception("scheduled_notification_failed kind=%s", kind)
        db.rollback()
        return False
    _notify_telegram(f"{title}\n{body}")
    return True


def _notify_telegram(text: str) -> None:
    """Telegram notifies; it never approves.

    A run approval can send email or mutate records, and a chat button cannot
    show ten drafted emails for review. Approving what you cannot see is not
    approval, so no callback control is attached here.
    """
    try:
        from app.runtime_state import runtime_state

        service = runtime_state.telegram_service
        if service is not None:
            service.notify(text)
    except Exception:
        logger.warning("scheduled_notification_telegram_failed", exc_info=True)


def generate_reply_notifications(db: Session, *, limit: int = 20) -> int:
    """Best-effort: never raises: a failure here must not fail the sync job."""
    pending = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == settings.owner_id,
            EmailReplyMessage.direction == "inbound",
            EmailReplyMessage.notified_at.is_(None),
        )
        .order_by(EmailReplyMessage.received_at.asc())
        .limit(max(1, limit))
        .all()
    )
    if not pending:
        return 0

    session = _target_session(db)
    created = 0
    for message in pending:
        conversation = db.get(EmailConversation, message.conversation_id)
        root = db.get(RecruiterEmail, conversation.root_recruiter_email_id) if conversation else None
        notification = asyncio.run(
            _classify_reply(sender=message.sender, subject=root.subject if root else "", body=message.body)
        )
        message.notified_at = utc_now()
        if notification:
            db.add(ChatMessage(session_id=session.id, role="assistant", content=notification))
            session.updated_at = utc_now()
            created += 1
    db.commit()
    return created
