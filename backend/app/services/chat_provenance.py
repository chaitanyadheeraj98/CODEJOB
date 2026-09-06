"""What did the assistant just ask, and what has the user typed lately.

The evidence behind R2 (ask-first) and R3 (verbatim-substring). It exists
because a proposal tool cannot be trusted to report its own provenance: a model
claiming "the user told me this" is exactly the claim that has to be checked
against something the model does not control.

It works at all because of one fact about the turn: `ChatService.send_message`
commits the user's row *before* the agent runs, so a tool invoked during the
turn opens its own session and finds the user's current message already there,
with the assistant's previous message - the question - sitting just behind it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ChatAttachment, ChatMessage, ChatSession

# User messages searched on path A2. "Save that to my profile" usually refers to
# something said a turn or two ago; every row in the window is still one the
# user typed, so widening it widens *when* they said it and never *who said it*.
PROVENANCE_WINDOW = 6


@dataclass(frozen=True)
class TurnEvidence:
    user_text: str
    # Newest first, up to PROVENANCE_WINDOW, all role="user", all in one session.
    recent_user_texts: tuple[str, ...]
    # The assistant's *previous* message - the one that asked the question.
    assistant_text: str
    found: bool


def _normalise(text: str) -> str:
    """Case and runs of whitespace, and nothing else.

    Stripping punctuation is how `2 weeks` starts matching `two weeks`, and how
    a value the user never gave becomes one the server believes they did.
    """
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def contains_verbatim(haystack: str, needle: str) -> bool:
    """R3's test: does `needle` appear inside text the user themselves typed."""
    cleaned = _normalise(needle)
    if not cleaned:
        return False
    return cleaned in _normalise(haystack)


def latest_exchange(db: Session, owner_id: str) -> TurnEvidence:
    """The current turn's user message, its session's recent user messages, and
    the assistant message that preceded it.

    Owner-scoped through ChatSession, which is how every other owner-scoped chat
    query reaches a ChatMessage - the row itself carries no owner_id.
    """
    user_rows = (
        db.query(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(ChatSession.owner_id == owner_id, ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .limit(1)
        .all()
    )
    if not user_rows:
        return TurnEvidence("", (), "", found=False)
    newest = user_rows[0]

    # The window is fetched from the same session as the newest row and includes
    # it, so `user_text` and `recent_user_texts[0]` cannot disagree.
    window = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == newest.session_id,
            ChatMessage.role == "user",
            ChatMessage.id <= newest.id,
        )
        .order_by(ChatMessage.id.desc())
        .limit(PROVENANCE_WINDOW)
        .all()
    )
    recent = tuple(row.content or "" for row in window)

    assistant = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == newest.session_id,
            ChatMessage.role == "assistant",
            ChatMessage.id < newest.id,
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    return TurnEvidence(
        user_text=recent[0] if recent else "",
        recent_user_texts=recent,
        assistant_text=(assistant.content or "") if assistant is not None else "",
        found=True,
    )


# --- what the user put in front of the assistant --------------------------
#
# A different question from the one above, and the distinction is the point.
#
# `latest_exchange` answers *did the user write this*, which is what R3 needs
# before anything reaches `<user_profile>` - the one block the model is told to
# believe. It reads user message rows and nothing else, and it must stay that
# way: admitting an attachment row would let a recruiter's job description
# satisfy "the user told me this".
#
# `user_supplied_document` answers *which stored text are we about to process*,
# for a path where the content is untrusted by design - a pasted or uploaded job
# description, headed for ingestion and a Needs Review card a human then reads.
# It exists so the text comes from the database rather than from a tool
# argument. A model handed a 200-line JD and asked to pass it along will retype
# it, and a retyped JD is one with a changed rate, a dropped requirement id, or
# a recruiter address that was never in the original - which then gets filed as
# though a human had entered it.
#
# So: never use this for R2/R3 evidence, and never widen `latest_exchange` to
# cover this. Two questions, two functions, and neither one answers the other.


@dataclass(frozen=True)
class SourceDocument:
    """Stored text the user supplied, and where it was read from."""

    text: str
    # "chat_message" - typed or pasted into the composer.
    # "attachment"   - uploaded, and this is the text extracted at upload time.
    origin: str
    # For showing on a confirmation card: the file name, or a plain description
    # of the message. Whatever it says, the user can recognise what was read.
    label: str
    attachment_id: int | None = None


def user_supplied_document(
    db: Session,
    owner_id: str,
    *,
    attachment_id: int | None = None,
) -> SourceDocument | None:
    """The user's own pasted or uploaded text, read back from storage.

    With `attachment_id`, the extracted text of that attachment; without it, the
    newest user message. Returns None when there is nothing to read - no rows,
    an attachment belonging to someone else, or one whose extraction failed.

    The caller passes an id, never text. That is the whole contract: a proposal
    built on this shows the user the bytes that will actually be ingested, and
    the model's only influence is which stored document was chosen.
    """
    if attachment_id is not None:
        row = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == owner_id,
                ChatAttachment.id == attachment_id,
            )
            .first()
        )
        # Owner-scoped on the attachment row itself, which carries its own
        # owner_id - unlike ChatMessage, which is reached through its session.
        if row is None or row.content_markdown is None:
            return None
        return SourceDocument(
            text=row.content_markdown,
            origin="attachment",
            label=row.file_name or f"attachment {row.id}",
            attachment_id=row.id,
        )

    newest = (
        db.query(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .filter(ChatSession.owner_id == owner_id, ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .first()
    )
    if newest is None or not (newest.content or "").strip():
        return None
    return SourceDocument(
        text=newest.content or "",
        origin="chat_message",
        label="the message you sent",
        attachment_id=None,
    )
