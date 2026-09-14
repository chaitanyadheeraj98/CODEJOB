"""How well does this owner already know the address we are about to mail?

Letting the assistant change the envelope opened a path that did not exist
before: the model reads recruiter email, recruiter email is untrusted content,
and untrusted content that can choose a recipient can ask for the user's
details to be sent somewhere the user never chose.

The answer is not to forbid new addresses. "CC the other recruiter at the same
firm" is an ordinary request, and a rule that refuses it sends the user to
their own mail client, where the app can neither help nor observe. So the
address is graded instead, and the grade decides how much friction it costs:

- **thread** - the address is already in this owner's record of this thread, or
  in their configured employer CC list. Nothing new is happening.
- **domain** - a new mailbox at a domain already on the thread. This is the
  colleague case. It is safe against the threat that motivates the check: an
  injected instruction cannot name a domain the user is not already
  corresponding with without falling into `new`.
- **new** - neither. Allowed, but only with a human saying so explicitly, which
  the server enforces rather than the card.

Only addresses the assistant *changed* are graded. The thread's own recipient
has always gone out as it is, and grading it here would fail sends that work
today over data this module had no part in creating.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app import tenancy
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    PremiumNumberContact,
    RecruiterEmail,
    UserSettings,
)

THREAD = "thread"
DOMAIN = "domain"
NEW = "new"

# Addresses arrive here from headers as often as from columns, so "Name
# <a@b.com>, c@d.com" has to yield two addresses rather than nothing.
_ADDRESS_IN_TEXT = re.compile(r"[^\s,;<>\"]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _addresses_in(value: str | None) -> set[str]:
    return {match.lower() for match in _ADDRESS_IN_TEXT.findall(value or "")}


def known_addresses(db: Session, email: RecruiterEmail) -> set[str]:
    """Every address this owner already has on this thread, plus their own CCs."""
    owner_id = tenancy.owner_id()
    known: set[str] = set()
    for value in (email.sender, email.recipient_email, email.cc_email):
        known |= _addresses_in(value)

    thread_id = (email.external_thread_id or "").strip()
    if thread_id:
        # Siblings first: one Gmail thread can hold several RecruiterEmail rows,
        # and an address on any of them is an address the user has seen.
        siblings = (
            db.query(RecruiterEmail.sender, RecruiterEmail.recipient_email, RecruiterEmail.cc_email)
            .filter(
                RecruiterEmail.owner_id == owner_id,
                RecruiterEmail.external_thread_id == thread_id,
            )
            .all()
        )
        for sender, recipient, cc in siblings:
            known |= _addresses_in(sender) | _addresses_in(recipient) | _addresses_in(cc)

        conversation = (
            db.query(EmailConversation.id, EmailConversation.recruiter_email_snapshot)
            .filter(
                EmailConversation.owner_id == owner_id,
                EmailConversation.external_thread_id == thread_id,
            )
            .first()
        )
        if conversation is not None:
            known |= _addresses_in(conversation[1])
            replies = (
                db.query(
                    EmailReplyMessage.sender,
                    EmailReplyMessage.to_header,
                    EmailReplyMessage.cc_header,
                )
                .filter(
                    EmailReplyMessage.owner_id == owner_id,
                    EmailReplyMessage.conversation_id == conversation[0],
                )
                .all()
            )
            for sender, to_header, cc_header in replies:
                known |= _addresses_in(sender) | _addresses_in(to_header) | _addresses_in(cc_header)

    settings_row = (
        db.query(UserSettings.preferred_employer_cc_emails, UserSettings.default_employer_cc_emails)
        .filter(UserSettings.owner_id == owner_id)
        .first()
    )
    if settings_row is not None:
        # The user configured these themselves, which is the strongest signal
        # available that an address is one they meant to write to.
        known |= _addresses_in(settings_row[0]) | _addresses_in(settings_row[1])
    return known


def domains_of(addresses: set[str]) -> set[str]:
    return {address.split("@", 1)[1] for address in addresses if "@" in address}


def classify(address: str, known: set[str], known_domains: set[str]) -> str:
    normalized = address.strip().lower()
    if not normalized:
        return NEW
    if normalized in known:
        return THREAD
    domain = normalized.split("@", 1)[1] if "@" in normalized else ""
    if domain and domain in known_domains:
        return DOMAIN
    return NEW


def changed_addresses(email: RecruiterEmail, to_address: str, cc_addresses: str) -> list[str]:
    """Only what this proposal moved, de-duplicated, order preserved.

    An address already on the row is not a decision the assistant made, so it is
    not a decision a user should be asked to underwrite. Shared by the tool and
    the route so the card and the server can never disagree about which
    addresses are up for review.
    """
    original = {
        value.strip().lower()
        for value in [email.recipient_email or ""] + (email.cc_email or "").replace(";", ",").split(",")
        if value.strip()
    }
    candidates = [to_address] + [part.strip() for part in cc_addresses.split(",")]
    changed: list[str] = []
    seen: set[str] = set()
    for address in candidates:
        key = address.strip().lower()
        if not key or key in original or key in seen:
            continue
        seen.add(key)
        changed.append(address.strip())
    return changed


def grade(db: Session, email: RecruiterEmail, addresses: list[str]) -> dict[str, str]:
    """`{address: level}` for each supplied address, in one pass over the data."""
    if not addresses:
        return {}
    known = known_addresses(db, email)
    known_domains = domains_of(known)
    return {address: classify(address, known, known_domains) for address in addresses}


def grade_without_thread(db: Session, addresses: list[str]) -> dict[str, str]:
    """The same grades for a message that starts its own thread.

    A reply inherits trust from the thread it is answering. A new message has
    no thread, so the basis is what the owner has deliberately recorded: the
    employer CC lists they configured, and the contacts they saved.

    Mail history is deliberately *not* searched. The addresses live in
    unbounded Text columns, so every check would be a full-table LIKE scan, and
    the index that would fix it is the shape that took production down once
    already. The cost of leaving it out is one extra tick when writing afresh
    to someone already in the mailbox but not in Contacts - which is a fair
    price for a first message to an address the user has never chosen before.
    """
    if not addresses:
        return {}
    owner_id = tenancy.owner_id()
    known: set[str] = set()
    known_domains: set[str] = set()

    settings_row = (
        db.query(UserSettings.preferred_employer_cc_emails, UserSettings.default_employer_cc_emails)
        .filter(UserSettings.owner_id == owner_id)
        .first()
    )
    if settings_row is not None:
        known |= _addresses_in(settings_row[0]) | _addresses_in(settings_row[1])

    contacts = (
        db.query(
            PremiumNumberContact.recruiter_email,
            PremiumNumberContact.employer_email,
            PremiumNumberContact.recruiter_email_domain,
            PremiumNumberContact.employer_email_domain,
        )
        .filter(PremiumNumberContact.owner_id == owner_id)
        .all()
    )
    for recruiter_email, employer_email, recruiter_domain, employer_domain in contacts:
        known |= _addresses_in(recruiter_email) | _addresses_in(employer_email)
        known_domains |= {
            value.strip().lower()
            for value in (recruiter_domain, employer_domain)
            if (value or "").strip()
        }

    known_domains |= domains_of(known)
    return {address: classify(address, known, known_domains) for address in addresses}
