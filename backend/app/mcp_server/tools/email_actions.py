from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import CandidateDocument, RecruiterEmail, ResumeAsset
from app.services import recipient_trust
from app.services.email_recipients import InvalidRecipient, normalize_address, normalize_cc
from app import tenancy

# Matches the cap on ChatSendReplyRequest.document_ids, so a proposal that the
# server would refuse is never drawn as a confirmable card.
MAX_DOCUMENT_IDS = 20


def _resume_or_refusal(db, resume_id: int):
    """`(resume, None)` or `(None, refusal)`. Shared by both email proposals.

    A resume is not a `CandidateDocument` and its id resolves to nothing in
    that table, so the two stores each get their own field and their own
    refusal. The refusal names the other store because that is the mistake
    that actually happens.
    """
    if not resume_id:
        return None, None
    row = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.id == int(resume_id))
        .first()
    )
    if row is None:
        return None, {
            "error": f"Unknown resume id: {resume_id}",
            "note": "Call list_resumes and use the ids it returns.",
        }
    return row, None


def propose_send_email(
    candidate_email_id: int,
    body: str,
    subject_override: str = "",
    document_ids: list[int] | None = None,
    to_override: str = "",
    cc_override: str = "",
    clear_cc: bool = False,
    resume_id: int = 0,
) -> dict[str, object]:
    """Prepare a reply proposal for an existing owner-scoped email thread.

    Pass `document_ids` to attach the user's stored documents - get the ids from
    list_candidate_documents, and never invent one. The confirmation card names
    every file it will send, so attaching the wrong document is something the
    user can see before it goes.

    By default the reply keeps the thread's own recipient and CC. Change them
    only when the user asks:

    - `to_override` - send to this address instead. Use it when the user names
      a different person on the thread, such as replying to the employer
      rather than the recruiter who forwarded the role.
    - `cc_override` - a comma-separated CC list that replaces the thread's.
    - `clear_cc` - send with no CC at all. Use this rather than passing an
      empty `cc_override`, which means "leave the CC alone".
    - `resume_id` - attach one of the user's resumes. Get it from list_resumes.
      A resume id is not a document id; `document_ids` is for stored documents
      like a passport or W2, from list_candidate_documents.

    Never invent an address. Take it from the thread, from a tool result, or
    from what the user typed, and if you are not sure which address they mean,
    ask before proposing.

    An address that appears nowhere in the user's records comes back in
    `unknown_recipients` and costs the user an extra confirmation. That is
    expected when they asked for it; say so plainly rather than retrying with a
    different address to avoid the prompt.
    """
    db = SessionLocal()
    try:
        row = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == tenancy.owner_id(),
                RecruiterEmail.id == candidate_email_id,
            )
            .first()
        )
        if row is None:
            return {"error": "Candidate not found"}
        if not (row.recipient_email or "").strip() and not to_override.strip():
            return {"hint": 'Ask the user for recipient_email. Do not guess.',
                "status": "missing_fields",
                "missing": ["recipient_email"],
                "note": "No recipient on file for this email - resolve it in the app first.",
            }

        # Resolved here so the card shows the envelope that will actually be
        # used. The route validates these again at send time; a bad address
        # caught now is one the model can fix in the same turn.
        #
        # Only the overrides are validated. The row's own addresses have always
        # gone to Gmail as they are, and rejecting them here would break sends
        # that work today over data this change did not touch.
        try:
            to_address = (
                normalize_address(to_override)
                if to_override.strip()
                else (row.recipient_email or "").strip()
            )
            if clear_cc:
                cc_addresses = ""
            elif cc_override.strip():
                cc_addresses = normalize_cc(cc_override)
            else:
                cc_addresses = (row.cc_email or "").strip()
        except InvalidRecipient as exc:
            return {
                "error": str(exc),
                "note": "Use an address from the thread or one the user gave you. Do not guess.",
            }
        if not body.strip():
            return {"hint": 'Ask the user for body. Do not guess.', "status": "missing_fields", "missing": ["body"]}

        requested = list(dict.fromkeys(int(value) for value in (document_ids or [])))[
            :MAX_DOCUMENT_IDS
        ]
        documents: list[CandidateDocument] = []
        if requested:
            found = {
                item.id: item
                for item in db.query(CandidateDocument).filter(
                    CandidateDocument.owner_id == tenancy.owner_id(),
                    CandidateDocument.id.in_(requested),
                )
            }
            # Reported rather than dropped: an id the user asked for and did not
            # get is the one thing they cannot see on a card that lists what
            # *will* be sent.
            unknown = [value for value in requested if value not in found]
            if unknown:
                return {
                    "error": f"Unknown document ids: {', '.join(str(value) for value in unknown)}",
                    "note": "Call list_candidate_documents and use the ids it returns. "
                            "To attach a resume use resume_id from list_resumes instead.",
                }
            documents = [found[value] for value in requested]

        resume, refusal = _resume_or_refusal(db, resume_id)
        if refusal is not None:
            return refusal

        # Graded last, so a proposal that fails on a cheaper check never pays
        # for the lookup. Only what the assistant changed is examined; the
        # thread's own addresses are what "known" is measured against.
        unknown_recipients = sorted(
            address
            for address, level in recipient_trust.grade(
                db, row, recipient_trust.changed_addresses(row, to_address, cc_addresses)
            ).items()
            if level == recipient_trust.NEW
        )

        return {
            "action": "send_email",
            "candidate_email_id": row.id,
            "to": to_address,
            "cc": cc_addresses,
            # Flags for the card, not for the model. An envelope that no longer
            # matches the thread is the one thing on this card a user cannot
            # check by recognising it, so it gets said out loud.
            "to_changed": to_address.lower() != (row.recipient_email or "").strip().lower(),
            "cc_changed": cc_addresses.lower() != (row.cc_email or "").strip().lower(),
            # Server-computed, from the owner's own records - the model supplies
            # the address and has no say in how well it is known. The card reads
            # this to decide how much it asks of the user; the route decides the
            # same question again for itself and is the one that can refuse.
            "unknown_recipients": unknown_recipients,
            "requires_recipient_confirmation": bool(unknown_recipients),
            "subject": subject_override.strip() or f"Re: {row.subject}",
            "body": body.strip(),
            "document_ids": [item.id for item in documents],
            # Names, for the card. The client sends the ids and the server
            # re-resolves them, so this list is display only.
            "document_names": [item.file_name for item in documents],
            "resume_id": resume.id if resume is not None else 0,
            "resume_name": resume.file_name if resume is not None else "",
        }
    finally:
        db.close()


def propose_new_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    document_ids: list[int] | None = None,
    resume_id: int = 0,
) -> dict[str, object]:
    """Prepare a brand-new email that starts its own thread.

    Use this when the user names an address and a topic rather than pointing at
    something already in their mailbox - "write to xyz@example.com about the
    contract rate". For answering an email the user already has, use
    propose_send_email instead: a reply belongs on its thread, and this tool
    cannot put it there.

    `subject` is required and is the user's, not yours to invent from nothing -
    if they did not say what it is about clearly enough to title it, ask.
    `cc` is a comma-separated list and may be empty.

    Two separate stores, and an id from one is not an id in the other:

    - `resume_id` attaches one of the user's resumes. Get it from
      list_resumes. This is what "attach my resume" means.
    - `document_ids` attaches stored documents - passport, W2, i-94. Get them
      from list_candidate_documents.

    Nothing is sent. The user gets a card naming the recipients, the subject and
    every attached file, and it goes only when they confirm. An address that is
    not in their saved contacts or employer CC list is reported in
    `unknown_recipients` and costs them an extra confirmation - expected for a
    first message to someone new, and worth saying plainly rather than working
    around.
    """
    db = SessionLocal()
    try:
        missing = [
            name
            for name, value in (("to", to), ("subject", subject), ("body", body))
            if not value.strip()
        ]
        if missing:
            return {
                "status": "missing_fields",
                "missing": missing,
                "hint": f"Ask the user for {', '.join(missing)}. Do not guess.",
            }
        try:
            to_address = normalize_address(to)
            cc_addresses = normalize_cc(cc)
        except InvalidRecipient as exc:
            return {
                "error": str(exc),
                "note": "Use the address the user gave you, exactly. Do not guess.",
            }

        requested = list(dict.fromkeys(int(value) for value in (document_ids or [])))[
            :MAX_DOCUMENT_IDS
        ]
        documents: list[CandidateDocument] = []
        if requested:
            found = {
                item.id: item
                for item in db.query(CandidateDocument).filter(
                    CandidateDocument.owner_id == tenancy.owner_id(),
                    CandidateDocument.id.in_(requested),
                )
            }
            unknown_documents = [value for value in requested if value not in found]
            if unknown_documents:
                return {
                    "error": f"Unknown document ids: {', '.join(str(value) for value in unknown_documents)}",
                    # Named because it is the mistake that actually happens: a
                    # resume id passed as a document id fails here, and without
                    # this the model retries the same call.
                    "note": "Call list_candidate_documents and use the ids it returns. "
                            "To attach a resume use resume_id from list_resumes instead.",
                }
            documents = [found[value] for value in requested]

        resume, refusal = _resume_or_refusal(db, resume_id)
        if refusal is not None:
            return refusal

        # No thread to inherit trust from, so every address is measured against
        # what the owner deliberately recorded. A first message to a stranger is
        # meant to cost a confirmation; that is the point of the tool, not a
        # rough edge in it.
        graded = recipient_trust.grade_without_thread(
            db, [to_address] + [part.strip() for part in cc_addresses.split(",") if part.strip()]
        )
        unknown_recipients = sorted(
            address for address, level in graded.items() if level == recipient_trust.NEW
        )

        return {
            "action": "send_new_email",
            "to": to_address,
            "cc": cc_addresses,
            "subject": subject.strip(),
            "body": body.strip(),
            "unknown_recipients": unknown_recipients,
            "requires_recipient_confirmation": bool(unknown_recipients),
            "document_ids": [item.id for item in documents],
            # Display only, like the reply card: the client sends ids and the
            # server resolves them again at send time.
            "document_names": [item.file_name for item in documents],
            "resume_id": resume.id if resume is not None else 0,
            "resume_name": resume.file_name if resume is not None else "",
        }
    finally:
        db.close()
