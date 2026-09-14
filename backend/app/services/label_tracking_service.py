import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses, parseaddr

from sqlalchemy.orm import Session

from app.models import RecruiterWatch, TrackedThread, EmailReplyMessage, EmailConversation, RecruiterEmail, AppTSApplication, UserSettings
from app.config import settings
from app.services import email_inbox_service, gmail_label_service

logger = logging.getLogger(__name__)
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "rocketmail.com", "outlook.com",
    "hotmail.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me", "mail.com", "gmx.com", "gmx.net", "gmx.de",
    "yahoo.co.uk", "yahoo.co.in", "yahoo.in", "yahoo.ca", "yahoo.fr", "yahoo.de",
    "hotmail.co.uk", "outlook.in", "live.co.uk", "google.com", "fastmail.com",
    "tuta.com", "tutanota.com", "tutanota.de", "yandex.com", "yandex.ru", "qq.com", "163.com",
}
# Same hazard as freemail, different reason: nobody's colleagues live here.
# These are list servers, mail-tracking pixels and ESP return paths, so a domain
# watch on one follows every unrelated sender that shares the infrastructure.
#
# googlegroups.com is the sharp one. This app is *fed* by Google Groups - see
# GmailRequirementGroup and gmail_group_source_service - so a labeled thread
# that arrived through a group would turn the bulk requirement firehose into a
# recruiter watch, which is the precise opposite of the curated relationship a
# label is supposed to mean. A live sync derived exactly that watch.
SHARED_INFRASTRUCTURE_DOMAINS = {
    "googlegroups.com", "groups.io", "freelists.org", "listserv.com",
    "mailsuite.com", "mailtrack.io", "yesware.com", "hubspot.com",
    "sendgrid.net", "mailgun.org", "amazonses.com", "mcsv.net", "mcdlv.net",
    "sparkpostmail.com", "mandrillapp.com", "bounces.google.com",
}
WATCH_QUERY_MAX_TERMS = 20
APPLICATION_WATCH_TERMINAL_STATUSES = {"rejected", "withdrawn", "no_response", "position_closed", "duplicate"}
_ADDRESS = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", re.I)


def _freemail(domain: str) -> bool:
    """True when `domain` is too widely shared to stand for one company.

    Named for the case that dominates it, but it gates every domain watch, so
    shared infrastructure belongs here too - the question it answers is "would
    following this domain follow strangers", not "is this a consumer mailbox".
    """
    blocked = FREEMAIL_DOMAINS | SHARED_INFRASTRUCTURE_DOMAINS | {d.strip().lower() for d in settings.label_watch_extra_freemail_domains.split(",") if d.strip()}
    return any(domain == d or domain.endswith("." + d) for d in blocked)


def _employer_domains(db: Session, owner_id: str) -> set[str]:
    """The owner's own employer domains, which a watch must never follow.

    A labeled thread routinely carries the employer on CC - the vendor the owner
    works through signs the RTR - so their address sits in `participants_json`
    next to the recruiter's. Watching it would turn every internal company mail
    into a "recruiter follow-up" and, worse, the domain watch would pull in
    colleagues who have nothing to do with the submission.

    This is the one exclusion the blocklists above cannot express, because the
    domain is legitimate and company-specific; it is only wrong *for this owner*.
    Sourced from Settings -> employer domains, the same list the premium-numbers
    recruiter/employer split already trusts.
    """
    from app.premium_numbers.domain_guard import employer_domains_for_owner

    return {d.lower() for d in employer_domains_for_owner(db, owner_id)}


def _is_employer(domain: str, employers: set[str]) -> bool:
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in employers)


def _is_infrastructure(domain: str) -> bool:
    """True when nothing at this domain is ever a person worth following.

    The distinction this draws against `_freemail` is the whole reason it
    exists. A freemail *domain* is unwatchable but a freemail *address* is
    fine - plenty of recruiters correspond from a personal Gmail, and
    charanteja4267@gmail.com is a real contact on a live tracked thread.

    A list server or ESP return path is different in kind: `hstjava@
    googlegroups.com` is not a person, it is a firehose. Watching it as an
    address subscribes the dossier to every message the group ever relays,
    which is exactly the noise a hand-applied label is meant to cut through.
    So infrastructure is refused at both levels, freemail only at the domain.
    """
    blocked = SHARED_INFRASTRUCTURE_DOMAINS | {
        d.strip().lower() for d in settings.label_watch_extra_infrastructure_domains.split(",") if d.strip()
    }
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in blocked)


def _upsert_watch(
    db: Session,
    owner_id: str,
    *,
    kind: str,
    value: str,
    thread_id: str | None = None,
    application_id: int | None = None,
    label_external_id: str | None = None,
    active_count: list[int],
) -> RecruiterWatch | None:
    domain = value if kind == "domain" else domain_of(value)
    if (
        _is_employer(domain, _employer_domains(db, owner_id))
        or _is_infrastructure(domain)
        or (kind == "domain" and _freemail(value))
    ):
        return None
    watch = db.query(RecruiterWatch).filter(
        RecruiterWatch.owner_id == owner_id,
        RecruiterWatch.watch_type == kind,
        RecruiterWatch.value == value,
    ).first()
    if watch is None or watch.released_at is not None:
        if active_count[0] >= settings.label_tracking_max_watches:
            logger.warning(
                "label_tracking_watch_limit_reached %s_id=%s",
                "thread" if thread_id else "application",
                thread_id or application_id,
            )
            return None
        active_count[0] += 1
    if watch is None:
        watch = RecruiterWatch(
            owner_id=owner_id,
            watch_type=kind,
            value=value,
            source_thread_ids_json="[]",
            source_application_ids_json="[]",
            origin_label_external_id=label_external_id,
        )
        db.add(watch)
    if thread_id is not None:
        watch.source_thread_ids_json = json.dumps(sorted(set(json.loads(watch.source_thread_ids_json)) | {thread_id}))
    if application_id is not None:
        watch.source_application_ids_json = json.dumps(sorted(set(json.loads(watch.source_application_ids_json)) | {application_id}))
    watch.released_at = None
    db.flush()
    return watch


def derive_watches(db: Session, owner_id: str, *, thread: TrackedThread, owner_email: str) -> list[RecruiterWatch]:
    if thread.owner_id != owner_id or thread.untracked_at or not json.loads(thread.label_external_ids_json):
        return []
    watches = []
    db.flush()
    active_count = [db.query(RecruiterWatch).filter(RecruiterWatch.owner_id == owner_id, RecruiterWatch.released_at.is_(None)).count()]
    for address in sorted({normalize_address(p["address"]) for p in json.loads(thread.participants_json)}):
        if address == normalize_address(owner_email) or not _ADDRESS.fullmatch(address):
            continue
        domain = domain_of(address)
        # Employer participants are dropped whole, address included: the point of
        # the dossier is the recruiter side of the conversation. Group and relay
        # addresses go the same way, for the reason in `_is_infrastructure`.
        values = [("address", address)] + ([] if _freemail(domain) else [("domain", domain)])
        for kind, value in values:
            watch = _upsert_watch(
                db,
                owner_id,
                kind=kind,
                value=value,
                thread_id=thread.external_thread_id,
                label_external_id=json.loads(thread.label_external_ids_json)[0],
                active_count=active_count,
            )
            if watch is not None:
                watches.append(watch)
    return watches


def build_watch_query(watches) -> str:
    if len(watches) > WATCH_QUERY_MAX_TERMS:
        raise ValueError("Split watches into chunks of at most 20")
    terms = []
    for watch in watches:
        value = watch.value.lower()
        if watch.watch_type == "address":
            valid = _ADDRESS.fullmatch(value)
        else:
            valid = re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}", value) and not _freemail(value)
        # Last line of defence, and the one that matters most: this builds the
        # Gmail query. A stored watch that predates a blocklist change must not
        # reach the network even if reconcile has not run yet.
        if not valid or _is_infrastructure(domain_of(value) if watch.watch_type == "address" else value):
            continue
        terms.extend(f'{field}:"{value}"' for field in ("from", "to", "cc", "bcc"))
    return f'({" OR ".join(terms)}) newer_than:{settings.label_tracking_watch_lookback_days}d' if terms else ""


@dataclass
class LabelTrackingResult:
    threads: int = 0
    messages: int = 0
    watches: int = 0


def normalize_address(raw: str) -> str:
    return parseaddr(raw)[1].strip().lower()


def domain_of(address: str) -> str:
    return normalize_address(address).rsplit("@", 1)[-1] if "@" in normalize_address(address) else ""


def participants_for(item) -> list[dict[str, str]]:
    return [{"address": address.lower(), "role": role}
        for role, header in (("from", "sender"), ("to", "to_header"), ("cc", "cc_header"), ("bcc", "bcc_header"))
        for _, address in getaddresses([item.get(header) or ""]) if "@" in address]


def attach_labeled_message(db: Session, owner_id: str, *, item, label_external_id: str, owner_email: str) -> tuple[bool, bool]:
    """Fold one labelled message into its tracked thread and conversation.

    Extracted from `sync_tracked_labels` so push delivery can reach the same
    behaviour one message at a time. A scan finds messages by asking Gmail for
    a label's contents; a notification arrives already knowing which message
    changed. Everything after that point - the thread, the participants, the
    conversation, the derived watches - is identical, and two implementations
    of "identical" drift.

    The caller keeps its own limits and its own idea of what it has already
    seen. Those are properties of a scan, not of a message.

    Returns (thread_created, message_captured).
    """
    thread_id = item.get("external_thread_id")
    if not thread_id:
        return False, False
    thread = db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.external_thread_id == thread_id).first()
    created = thread is None
    if thread is None:
        thread = TrackedThread(owner_id=owner_id, external_thread_id=thread_id,
            label_external_ids_json="[]", participants_json="[]", subject_snapshot=item.get("subject", "")[:500])
        db.add(thread)
        db.flush()
    thread.label_external_ids_json = json.dumps(sorted(set(json.loads(thread.label_external_ids_json)) | {label_external_id}))
    participants = {(p["address"], p["role"]) for p in json.loads(thread.participants_json) + participants_for(item)}
    thread.participants_json = json.dumps([{"address": a, "role": r} for a, r in sorted(participants)])
    thread.last_seen_at = datetime.now(UTC)
    thread.untracked_at = None
    conversation = email_inbox_service.ensure_label_conversation(db, owner_id=owner_id,
        thread_id=thread_id, label_external_id=label_external_id, first_item=item)
    if conversation.root_recruiter_email_id is None and normalize_address(conversation.recruiter_email_snapshot) == normalize_address(owner_email):
        other = next((p["address"] for p in json.loads(thread.participants_json) if p["address"] != normalize_address(owner_email)), "")
        conversation.recruiter_snapshot = other
        conversation.recruiter_email_snapshot = other
    thread.conversation_id = conversation.id
    captured = email_inbox_service.capture_labeled_message(db, owner_id=owner_id, item=item, owner_email=owner_email, conversation=conversation)
    thread.last_message_at = conversation.last_message_at
    derive_watches(db, owner_id, thread=thread, owner_email=owner_email)
    db.flush()
    return created, bool(captured)


def active_watches(db: Session, owner_id: str) -> list[RecruiterWatch]:
    """Every watch still in force, in the order the scan applies them."""
    return db.query(RecruiterWatch).filter(
        RecruiterWatch.owner_id == owner_id, RecruiterWatch.released_at.is_(None),
    ).order_by(RecruiterWatch.id).all()


def match_watch(item, watches, owner_email: str):
    """Which active watch, if any, this message answers to.

    Pure, and pure on purpose. `sync_watch_matches` reaches messages by asking
    Gmail a query built from these same watches; a notification hands over a
    message with no query involved. Keeping the decision separate from the
    search is what lets push delivery apply recruiter watches without building
    a Gmail query at all - which is the point of replacing the scans.

    Infrastructure and freemail domains are excluded here rather than at watch
    creation, matching the scan's behaviour exactly: a watch already stored can
    still be on a domain that should never match by domain.
    """
    addresses = {p["address"] for p in participants_for(item)} - {normalize_address(owner_email)}
    return next((w for w in watches if not _is_infrastructure(
        domain_of(w.value) if w.watch_type == "address" else w.value,
    ) and (w.value in addresses if w.watch_type == "address" else
        not _freemail(w.value) and any(domain_of(a) == w.value or domain_of(a).endswith("." + w.value) for a in addresses))), None)


def attach_watch_message(db: Session, owner_id: str, *, item, watch, owner_email: str) -> bool:
    """Fold one watch-matched message into its conversation. Returns captured."""
    thread_id = item.get("external_thread_id")
    if not thread_id:
        return False
    addresses = {p["address"] for p in participants_for(item)} - {normalize_address(owner_email)}
    conversation = email_inbox_service.ensure_watch_conversation(db, owner_id=owner_id, thread_id=thread_id, watch=watch, first_item=item)
    if normalize_address(conversation.recruiter_email_snapshot) == normalize_address(owner_email):
        conversation.recruiter_email_snapshot = sorted(addresses)[0] if addresses else ""
        conversation.recruiter_snapshot = conversation.recruiter_email_snapshot
    captured = email_inbox_service.capture_labeled_message(db, owner_id=owner_id, item=item, owner_email=owner_email, conversation=conversation, matched_watch_id=watch.id)
    if captured:
        watch.match_count += 1
        watch.last_matched_at = datetime.now(UTC)
    db.flush()
    return bool(captured)


def sync_tracked_labels(db: Session, owner_id: str, *, deps, owner_email: str = "") -> LabelTrackingResult:
    result = LabelTrackingResult()
    known = {r[0] for r in db.query(EmailReplyMessage.external_message_id).filter(EmailReplyMessage.owner_id == owner_id)}
    seen_threads = set()
    for label in gmail_label_service.list_labels(db, owner_id, tracked_only=True):
        if result.messages >= settings.label_tracking_max_messages_per_sync:
            break
        items = deps.list_candidates_by_label_ids([label.external_label_id],
            max_total_results=settings.label_tracking_max_messages_per_sync - result.messages, skip_message_ids=known)
        for item in items:
            thread_id = item.get("external_thread_id")
            if not thread_id:
                continue
            if thread_id not in seen_threads and len(seen_threads) >= settings.label_tracking_max_threads_per_sync:
                continue
            seen_threads.add(thread_id)
            created, captured = attach_labeled_message(
                db, owner_id, item=item, label_external_id=label.external_label_id, owner_email=owner_email,
            )
            result.threads += int(created)
            result.messages += int(captured)
            known.add(item["external_message_id"])
    return result


def reconcile_untracked(db: Session, owner_id: str, *, deps) -> int:
    labels = gmail_label_service.list_labels(db, owner_id, tracked_only=True)
    live = {label.external_label_id: deps.list_thread_ids_by_label(label.external_label_id) for label in labels}
    now = datetime.now(UTC)
    threads = {t.external_thread_id: t for t in db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id)}
    live_ids = set().union(*live.values()) if live else set()
    for conversation in db.query(EmailConversation).filter(EmailConversation.owner_id == owner_id, EmailConversation.external_thread_id.in_(live_ids)):
        if conversation.origin == "watch":
            conversation.origin = "label"
        if conversation.external_thread_id not in threads:
            root = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.id == conversation.root_recruiter_email_id).first() if conversation.root_recruiter_email_id else None
            participants = []
            for message in db.query(EmailReplyMessage).filter(EmailReplyMessage.owner_id == owner_id, EmailReplyMessage.conversation_id == conversation.id):
                participants.extend(participants_for({"sender": message.sender, "to_header": message.to_header, "cc_header": message.cc_header}))
            if root:
                participants.extend(participants_for({"sender": root.sender, "to_header": root.recipient_email, "cc_header": root.cc_email}))
            thread = TrackedThread(owner_id=owner_id, external_thread_id=conversation.external_thread_id,
                conversation_id=conversation.id, subject_snapshot=root.subject if root else conversation.subject_snapshot,
                participants_json=json.dumps(participants), label_external_ids_json="[]", last_message_at=conversation.last_message_at)
            db.add(thread)
            threads[conversation.external_thread_id] = thread
    released = 0
    for thread_id, thread in threads.items():
        current = sorted(label_id for label_id, ids in live.items() if thread_id in ids)
        thread.label_external_ids_json = json.dumps(current)
        if current:
            thread.untracked_at = None
            thread.last_seen_at = now
        elif thread.untracked_at is None:
            thread.untracked_at = now
            released += 1
    db.flush()
    reconcile_watches(db, owner_id)
    return released


def sync_watch_matches(db: Session, owner_id: str, *, deps, owner_email: str = "", max_messages: int | None = None) -> LabelTrackingResult:
    result = LabelTrackingResult()
    limit = settings.label_tracking_max_messages_per_sync if max_messages is None else max_messages
    for thread in db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.untracked_at.is_(None)):
        derive_watches(db, owner_id, thread=thread, owner_email=owner_email)
    watches = active_watches(db, owner_id)
    known = {r[0] for r in db.query(EmailReplyMessage.external_message_id).filter(EmailReplyMessage.owner_id == owner_id)}
    threads = set()
    for start in range(0, len(watches), WATCH_QUERY_MAX_TERMS):
        if result.messages >= limit:
            break
        chunk = watches[start:start + WATCH_QUERY_MAX_TERMS]
        query = build_watch_query(chunk)
        if not query:
            continue
        for item in deps.list_candidates_by_query(query, max_total_results=limit - result.messages, skip_message_ids=known):
            if result.messages >= limit:
                break
            watch = match_watch(item, chunk, owner_email)
            thread_id = item.get("external_thread_id")
            if watch is None or not thread_id:
                continue
            if thread_id not in threads and len(threads) >= settings.label_tracking_max_threads_per_sync:
                continue
            threads.add(thread_id)
            if attach_watch_message(db, owner_id, item=item, watch=watch, owner_email=owner_email):
                result.messages += 1
            known.add(item["external_message_id"])
    result.threads = len(threads)
    return result


def reconcile_watches(db: Session, owner_id: str) -> int:
    db.flush()
    active_threads = {row.external_thread_id for row in db.query(TrackedThread).filter(
        TrackedThread.owner_id == owner_id, TrackedThread.untracked_at.is_(None),
    ) if json.loads(row.label_external_ids_json)}
    application_watches_enabled = bool(db.query(UserSettings.feature_application_watches_enabled).filter(
        UserSettings.owner_id == owner_id,
    ).scalar())
    live_applications = (
        {row.id for row in db.query(AppTSApplication.id).filter(
            AppTSApplication.owner_id == owner_id,
            AppTSApplication.deleted_at.is_(None),
            AppTSApplication.status.not_in(APPLICATION_WATCH_TERMINAL_STATUSES),
        )}
        if application_watches_enabled
        else set()
    )
    released = 0
    employers = _employer_domains(db, owner_id)
    for watch in db.query(RecruiterWatch).filter(RecruiterWatch.owner_id == owner_id, RecruiterWatch.released_at.is_(None)):
        thread_sources = set(json.loads(watch.source_thread_ids_json)) & active_threads
        application_sources = set(json.loads(watch.source_application_ids_json)) & live_applications
        watch.source_thread_ids_json = json.dumps(sorted(thread_sources))
        watch.source_application_ids_json = json.dumps(sorted(application_sources))
        # Blocklist changes have to reach watches already stored, or a domain
        # only ever gets refused at creation and the one derived before the
        # entry was added keeps following strangers forever. googlegroups.com
        # was derived on this account before it was blocked.
        #
        # Employer domains run through the same release path for a second
        # reason: the setting is editable, so adding a domain in Settings has to
        # retire the watches that domain already produced.
        #
        # Infrastructure is checked here at both watch types, matching
        # derivation: hstjava@googlegroups.com was stored as an *address* watch
        # while the blocklist still only gated domains, and survived every
        # earlier reconcile because of it.
        watch_domain = watch.value if watch.watch_type == "domain" else domain_of(watch.value)
        employer_hit = _is_employer(watch_domain, employers)
        if (
            not (thread_sources or application_sources)
            or employer_hit
            or _is_infrastructure(watch_domain)
            or (watch.watch_type == "domain" and _freemail(watch.value))
        ):
            watch.released_at = datetime.now(UTC)
            released += 1
    return released


def release_label(db: Session, owner_id: str, label_external_id: str) -> None:
    for thread in db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.untracked_at.is_(None)):
        labels = set(json.loads(thread.label_external_ids_json))
        labels.discard(label_external_id)
        thread.label_external_ids_json = json.dumps(sorted(labels))
        if not labels:
            thread.untracked_at = datetime.now(UTC)
    reconcile_watches(db, owner_id)


def list_label_threads(db: Session, owner_id: str, *, label=None, q=None, status="all", sort="newest", page=1, limit=25, date_from=None, date_to=None):
    from fastapi import HTTPException
    from app.schemas import LabelThreadListResponse, LabelThreadResponse
    from sqlalchemy import func, or_

    if status not in {"all", "untracked", "promoted"} or sort not in {"newest", "oldest"}:
        raise HTTPException(422, "Invalid label thread status or sort")
    query = db.query(TrackedThread).filter(TrackedThread.owner_id == owner_id, TrackedThread.untracked_at.is_(None))
    if label:
        label_ids = set(gmail_label_service.resolve_label_ids(db, owner_id, [label]))
        ids = [t.id for t in query if label_ids.intersection(json.loads(t.label_external_ids_json))]
        query = query.filter(TrackedThread.id.in_(ids))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(TrackedThread.subject_snapshot.ilike(like), TrackedThread.participants_json.ilike(like)))
    applications = {a.source_thread_id: a for a in db.query(AppTSApplication).filter(
        AppTSApplication.owner_id == owner_id, AppTSApplication.source_thread_id.is_not(None), AppTSApplication.deleted_at.is_(None))}
    if status == "promoted":
        query = query.filter(TrackedThread.external_thread_id.in_(applications))
    elif status == "untracked":
        query = query.filter(TrackedThread.external_thread_id.notin_(applications))
    if date_from is not None:
        query = query.filter(TrackedThread.last_message_at >= date_from)
    if date_to is not None:
        query = query.filter(TrackedThread.last_message_at < date_to)
    total = query.count()
    query = query.order_by(TrackedThread.last_message_at.asc(), TrackedThread.id.asc()) if sort == "oldest" else query.order_by(TrackedThread.last_message_at.desc(), TrackedThread.id.desc())
    items = []
    for thread in query.offset((page - 1) * limit).limit(limit):
        if thread.conversation_id is None:
            continue
        detail = email_inbox_service.conversation_detail(db, owner_id, thread.conversation_id)
        root = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.id == detail.root_recruiter_email_id).first() if detail.root_recruiter_email_id else None
        application = applications.get(thread.external_thread_id)
        items.append(LabelThreadResponse(thread_id=thread.external_thread_id, subject=detail.subject,
            recruiter=detail.recruiter, recruiter_email=detail.recruiter_email, labels=detail.labels,
            last_message_at=detail.last_message_at, message_count=len(detail.messages), unread_count=detail.unread_reply_count,
            conversation_id=thread.conversation_id, gmail_thread_link=detail.gmail_thread_link,
            appts_application_id=application.id if application else None, record_id=root.record_id if root else None))
    return LabelThreadListResponse(items=items, total=total, has_next=page * limit < total, next_cursor=page + 1 if page * limit < total else None)
