"""Read models for the Labels workspace.

Label tracking already captures two kinds of mail: the threads sitting in a
tracked Gmail label, and everything the watches derived from those threads
matched elsewhere in the mailbox. Both land in `email_conversations`, but they
land as *separate* conversations, because a recruiter answering from a new
subject line is a new Gmail thread and nothing about the storage pretends
otherwise.

That is correct storage and useless presentation. A person who labeled one RTR
thread wants one page per recruiter relationship - what they sent, what came
back, in order, whichever thread it arrived in. This module does that join and
nothing else: no Gmail calls, no writes, no capture logic. Anything that reaches
the network belongs in `label_tracking_service`.

The join key is the watch. `RecruiterWatch.source_thread_ids_json` records which
labeled thread caused a watch to exist, and `EmailReplyMessage.matched_watch_id`
records which watch pulled a message in. Following those two columns from a
labeled thread yields exactly the mail that thread is responsible for, and
releasing the label releases the watches, so the dossier empties itself back
down to the original thread. That is the "tracked until the thread leaves the
label" rule, expressed as a query instead of a cleanup job.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    AppTSApplication,
    EmailConversation,
    EmailReplyMessage,
    GmailLabel,
    RecruiterEmail,
    RecruiterWatch,
    TrackedThread,
)
from app.recent_runs import build_gmail_message_url
from app.schemas import (
    LabelOverviewItem,
    LabelOverviewResponse,
    ThreadDossierContact,
    ThreadDossierMessage,
    ThreadDossierResponse,
)
from app.services import label_tracking_service as tracking

# Ordered widest-to-narrowest for display: the recruiter is the reason the page
# exists, the employer is deliberately excluded from tracking, and `other` is
# everyone else on the CC line.
CONTACT_RECRUITER = "recruiter"
CONTACT_EMPLOYER = "employer"
CONTACT_SELF = "self"
CONTACT_OTHER = "other"


def _labels_by_id(db: Session, owner_id: str) -> dict[str, GmailLabel]:
    rows = db.query(GmailLabel).filter(
        GmailLabel.owner_id == owner_id,
        GmailLabel.deleted_at.is_(None),
    )
    return {row.external_label_id: row for row in rows}


def label_overview(db: Session, owner_id: str) -> LabelOverviewResponse:
    """Tracked labels with the counts the rail needs, in one pass.

    Counts come from `tracked_threads`, not from Gmail's own
    `message_count_snapshot`: the snapshot counts messages Gmail knows about,
    including ones this app has never captured, and a rail that promises 40 and
    lists 8 is a bug report waiting to happen.
    """
    labels = _labels_by_id(db, owner_id)
    threads = db.query(TrackedThread).filter(
        TrackedThread.owner_id == owner_id,
        TrackedThread.untracked_at.is_(None),
    ).all()
    unread_by_conversation = {
        row.id: row.unread_reply_count
        for row in db.query(EmailConversation).filter(EmailConversation.owner_id == owner_id)
    }

    threads_per_label: dict[str, int] = defaultdict(int)
    unread_per_label: dict[str, int] = defaultdict(int)
    latest_per_label: dict[str, datetime] = {}
    for thread in threads:
        unread = unread_by_conversation.get(thread.conversation_id or -1, 0)
        for label_id in json.loads(thread.label_external_ids_json):
            threads_per_label[label_id] += 1
            unread_per_label[label_id] += unread
            current = latest_per_label.get(label_id)
            if current is None or thread.last_message_at > current:
                latest_per_label[label_id] = thread.last_message_at

    items = [
        LabelOverviewItem(
            external_label_id=label.external_label_id,
            name=label.name,
            color_background=label.color_background,
            color_text=label.color_text,
            thread_count=threads_per_label.get(label.external_label_id, 0),
            unread_count=unread_per_label.get(label.external_label_id, 0),
            last_message_at=latest_per_label.get(label.external_label_id),
        )
        for label in labels.values()
        if label.is_tracked
    ]
    # Busiest first, then alphabetical so an empty rail is still predictable.
    items.sort(key=lambda item: (-item.thread_count, item.name.lower()))
    return LabelOverviewResponse(
        items=items,
        tracked_thread_total=len(threads),
        untracked_label_count=sum(1 for label in labels.values() if not label.is_tracked and label.label_type == "user"),
    )


def watches_for_thread(db: Session, owner_id: str, thread_id: str) -> list[RecruiterWatch]:
    """Active watches this labeled thread is responsible for.

    Filtered in Python because `source_thread_ids_json` is a JSON text column on
    both SQLite and Postgres; the watch table is bounded by
    `label_tracking_max_watches`, so the scan is small by construction.
    """
    rows = db.query(RecruiterWatch).filter(
        RecruiterWatch.owner_id == owner_id,
        RecruiterWatch.released_at.is_(None),
    ).order_by(RecruiterWatch.watch_type.asc(), RecruiterWatch.value.asc()).all()
    return [row for row in rows if thread_id in set(json.loads(row.source_thread_ids_json))]


def _classify(address: str, *, owner_email: str, employers: set[str], watched: set[str]) -> str:
    if not address:
        return CONTACT_OTHER
    if address == tracking.normalize_address(owner_email):
        return CONTACT_SELF
    domain = tracking.domain_of(address)
    if tracking._is_employer(domain, employers):
        return CONTACT_EMPLOYER
    if address in watched or domain in watched:
        return CONTACT_RECRUITER
    return CONTACT_OTHER


def thread_dossier(db: Session, owner_id: str, thread_id: str, *, owner_email: str = "") -> ThreadDossierResponse:
    thread = db.query(TrackedThread).filter(
        TrackedThread.owner_id == owner_id,
        TrackedThread.external_thread_id == thread_id,
    ).first()
    if thread is None or thread.untracked_at is not None:
        raise HTTPException(status_code=404, detail="That thread is not in a tracked label")

    watches = watches_for_thread(db, owner_id, thread_id)
    watch_ids = {watch.id for watch in watches}
    watched_values = {watch.value for watch in watches}
    employers = tracking._employer_domains(db, owner_id)

    # The thread's own conversation plus every conversation a watch pulled in.
    conversation_ids: set[int] = {thread.conversation_id} if thread.conversation_id else set()
    if watch_ids:
        conversation_ids |= {
            row[0]
            for row in db.query(EmailReplyMessage.conversation_id)
            .filter(
                EmailReplyMessage.owner_id == owner_id,
                EmailReplyMessage.matched_watch_id.in_(watch_ids),
            )
            .distinct()
        }
    if not conversation_ids:
        raise HTTPException(status_code=404, detail="That thread has no captured messages yet")

    conversations = {
        row.id: row
        for row in db.query(EmailConversation).filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.id.in_(conversation_ids),
        )
    }
    rows = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.conversation_id.in_(conversation_ids),
        )
        .order_by(EmailReplyMessage.received_at.asc(), EmailReplyMessage.id.asc())
        .all()
    )

    counts: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    messages: list[ThreadDossierMessage] = []
    for row in rows:
        conversation = conversations.get(row.conversation_id)
        participants = tracking.participants_for(
            {"sender": row.sender, "to_header": row.to_header, "cc_header": row.cc_header}
        )
        for participant in participants:
            address = tracking.normalize_address(participant["address"])
            if address:
                counts[address] += 1
        sender = tracking.normalize_address(row.sender)
        if sender and sender not in names:
            display = row.sender.split("<")[0].strip().strip('"')
            names[sender] = display or sender
        messages.append(
            ThreadDossierMessage(
                id=row.id,
                conversation_id=row.conversation_id,
                external_thread_id=conversation.external_thread_id if conversation else thread_id,
                direction=row.direction,
                sender=row.sender,
                sender_address=sender,
                to_header=row.to_header,
                cc_header=row.cc_header,
                subject=conversation.subject_snapshot if conversation else thread.subject_snapshot,
                snippet=row.snippet,
                body=row.body,
                occurred_at=row.received_at,
                read_at=row.read_at,
                # `origin` is the conversation's provenance, so a message that
                # arrived in a brand-new thread is visibly labeled as a
                # follow-up rather than silently mixed into the labeled one.
                origin="label" if row.conversation_id == thread.conversation_id else "watch",
                gmail_link=build_gmail_message_url(
                    external_message_id=row.external_message_id,
                    external_thread_id=conversation.external_thread_id if conversation else thread_id,
                    external_rfc_message_id=row.external_rfc_message_id,
                ),
            )
        )

    contacts = [
        ThreadDossierContact(
            address=address,
            name=names.get(address, address),
            domain=tracking.domain_of(address),
            kind=_classify(address, owner_email=owner_email, employers=employers, watched=watched_values),
            message_count=count,
            watched=address in watched_values or tracking.domain_of(address) in watched_values,
        )
        for address, count in counts.items()
    ]
    order = {CONTACT_RECRUITER: 0, CONTACT_OTHER: 1, CONTACT_EMPLOYER: 2, CONTACT_SELF: 3}
    contacts.sort(key=lambda c: (order.get(c.kind, 4), -c.message_count, c.address))

    application = db.query(AppTSApplication).filter(
        AppTSApplication.owner_id == owner_id,
        AppTSApplication.source_thread_id == thread_id,
        AppTSApplication.deleted_at.is_(None),
    ).first()
    root = None
    conversation = conversations.get(thread.conversation_id or -1)
    if conversation is not None and conversation.root_recruiter_email_id:
        root = db.query(RecruiterEmail).filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.id == conversation.root_recruiter_email_id,
        ).first()
    labels = _labels_by_id(db, owner_id)

    return ThreadDossierResponse(
        thread_id=thread_id,
        subject=(conversation.subject_snapshot if conversation else "") or thread.subject_snapshot,
        labels=[labels[label_id].name for label_id in json.loads(thread.label_external_ids_json) if label_id in labels],
        last_message_at=thread.last_message_at,
        conversation_id=thread.conversation_id,
        thread_count=len({message.external_thread_id for message in messages}),
        unread_count=sum(1 for message in messages if message.direction == "inbound" and message.read_at is None),
        gmail_thread_link=build_gmail_message_url(external_thread_id=thread_id),
        appts_application_id=application.id if application else None,
        record_id=root.record_id if root else None,
        watches=sorted(f"{watch.watch_type}:{watch.value}" for watch in watches),
        contacts=contacts,
        messages=messages,
    )
