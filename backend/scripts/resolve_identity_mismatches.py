"""One-shot repair of the email-ownership damage that migration 20260910_0048 reports
but deliberately does not fix.

WHY THIS IS NOT A MIGRATION: 0048 stays mechanical and safe. Deciding *who* actually
owns a disputed address is a judgement call made by reading the data - every decision
below is written out explicitly so it can be reviewed before anything runs, rather than
derived from a rule that would get most of them wrong. A "child table wins" rule, the
obvious mechanical choice, would have deleted the correct email from the correct person
in 13 of the 14 mismatches.

THE PATTERN: one bad extraction pass attached addresses lifted from a posting/signature
block to whichever contact happened to be nearby. It left two kinds of wreckage:
  1. 14 headline-vs-child ownership mismatches (REASSIGN / CLEAR_HEADLINE / MERGES).
  2. 13 child rows storing a raw "Name <address>" string instead of a bare address
     (DELETE_MALFORMED) - these never surfaced as mismatches because the string never
     equals any headline value, but they become visible junk once the child table is
     authoritative and the email-list editor renders it.

Every action is idempotent: an item already in its target state is skipped, so this is
safe to re-run. Preflight aborts on anything it does not recognise rather than acting on
a stale judgement.

PRECONDITION: alembic revision 20260910_0048 must already be applied.

Usage:
    python scripts/resolve_identity_mismatches.py             # dry run, prints the plan
    python scripts/resolve_identity_mismatches.py --apply     # commit the changes
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ContactIdentityAction,
    PremiumContactEmail,
    PremiumNumberContact,
    utc_now,
)
from app.premium_numbers import contact_identity_service  # noqa: E402

SOURCE = "identity_mismatch_repair"

# --------------------------------------------------------------------------------------
# THE DECISIONS - this block is the part to review.
# --------------------------------------------------------------------------------------

# (email, currently_owned_by, should_belong_to, role_on_target, why)
# The child row is moved, not deleted: the address is real and belongs to someone, and
# moving it also correctly leaves the wrong holder with no email rather than a bad one.
REASSIGN: list[tuple[str, int, int, str, str]] = [
    ("alekya@rpatechnologyinc.com", 34, 799, "employer",
     "34=Rakesh Chauhan/Black Rock Solutions; 799=RPATECHNOLOGY INC (38 leads) - domain matches 799"),
    ("asiya@horizonsoftech.net", 22, 801, "employer",
     "22=Compunnel (soft-deleted); 801=Horizons of Tech (9 leads) - domain matches 801"),
    ("chiranjeevi@horizonsoftech.net", 28, 817, "employer",
     "28=Anil Kumar/Ampstek; 817=Horizon Softech Inc (26 leads) - domain matches 817"),
    ("hr@horizonsoftech.net", 47, 1102, "employer",
     "47=Adam/Dataquad; 1102=Horizons of Tech - domain matches 1102. NOTE: generic role address"),
    ("kprashanth@horizonsoftech.net", 35, 1031, "recruiter",
     "35=Satyam Gupta/KTEK; 1031=Prashanth Kinnera/Horizon Soft Tech - name AND domain match 1031"),
    ("madhavi@horizonsoftech.net", 441, 798, "employer",
     "441=Lokesh/Info Way; 798=Horizons Tech (3 leads) - domain matches 798"),
    ("vaishnavi@horizonsoftech.net", 416, 802, "employer",
     "416=Vamshi Krishna/IMR Soft; 802=Horizon Softech Inc (37 leads) - domain matches 802"),
    ("swathimandla@livemindz.com", 707, 773, "recruiter",
     "707='Technical Recruiter'/Unknown placeholder and ALREADY SOFT-DELETED - its claim is dead weight; "
     "773=swathimandla/livemindz is live and an exact name+domain match"),
    ("krishalpha05@gmail.com", 997, 999, "recruiter",
     "997 is ALREADY SOFT-DELETED but still holds the child row; 999 is the live twin. Releasing the dead "
     "claim to the live contact - NOT a merge, because merge_contacts requires both sides to be live"),
    ("harshitha@horizonsoftech.net", 31, 796, "employer",
     "31=Leo/Nascent Technologies; 796=Horizons of Tech, holds (248) 247-6165 and survives the merge below"),
]

# (contact_id, column, expected_value, why) - the HEADLINE is the error here, not the
# child row. Both of these name a person who is not the address's owner.
CLEAR_HEADLINE: list[tuple[int, str, str, str]] = [
    (7, "recruiter_email", "vaishnavi@horizonsoftech.net",
     "7=Arjun Porandla/CNET Global Solutions - neither the name nor the domain is his; belongs to 802"),
    (20, "recruiter_email", "chiranjeevi@horizonsoftech.net",
     "20=Arvind Sahani/IMCS Group - neither the name nor the domain is his; belongs to 817"),
]

# (canonical_id, loser_ids, rename_to, why)
MERGES: list[tuple[int, list[int], str | None, str]] = [
    (796, [1120, 1137], "Harshitha Voddepally",
     "One person split across three rows. 796 holds the phone (248) 247-6165 and a lead but is named "
     "'Unknown'; 1120 and 1137 carry the real name and nothing else - 1137 is the phoneless orphan the "
     "dismiss() bug created on 2026-08-31. Canonical is 796 so the phone and lead history survive; the "
     "name is copied across afterwards."),
]

# (child_email_row_id, contact_id, stored_value, address_it_really_holds)
# Unparseable "Name <address>" rows. Every one of these addresses already exists as a
# clean child row on its rightful owner, so deleting these loses nothing - VERIFIED by
# the clean-duplicate check in preflight, which refuses to delete without it.
DELETE_MALFORMED: list[tuple[int, int, str, str]] = [
    (32, 18, "alekya <alekya@rpatechnologyinc.com>", "alekya@rpatechnologyinc.com"),
    (78, 4, "sheshwika kukkala <sheshwika@horizonsoftech.net>", "sheshwika@horizonsoftech.net"),
    (92, 6, "alekya satarla <alekya@horizonsoftech.net>", "alekya@horizonsoftech.net"),
    (109, 19, "prashanth kinnera <kprashanth@rpatechnologyinc.com>", "kprashanth@rpatechnologyinc.com"),
    (155, 7, "vaishnavi boosa <vaishnavi@horizonsoftech.net>", "vaishnavi@horizonsoftech.net"),
    (158, 20, "chiranjeevi kunchala <chiranjeevi@horizonsoftech.net>", "chiranjeevi@horizonsoftech.net"),
    (162, 8, "samshritha gangula <samshritha@horizonsoftech.net>", "samshritha@horizonsoftech.net"),
    (163, 12, "shiva krishna <hr@horizonsoftech.net>", "hr@horizonsoftech.net"),
    (164, 13, "<shiva@rpatechnologyinc.com>", "shiva@rpatechnologyinc.com"),
    (165, 14, "kartheek battula <kartheek@horizonsoftech.net>", "kartheek@horizonsoftech.net"),
    (166, 21, "prashanth kinnera <kprashanth@horizonsoftech.net>", "kprashanth@horizonsoftech.net"),
]

# Not an address at all - nothing to preserve, so no clean-duplicate requirement.
DELETE_JUNK: list[tuple[int, int, str, str]] = [
    (20, 1031, "unknown", "literally the string 'unknown' - never a real address"),
]

# Reported, never acted on automatically.
NEEDS_HUMAN_DECISION = [
    "Child email row 146 on contact 2 (Ritika Pandey) stores 'naveen vasapothula  <naveen@horizonsoftech.net>'. "
    "It is malformed like the 11 deleted above, BUT naveen@horizonsoftech.net exists nowhere else - no clean "
    "child row, no live headline. Deleting it would lose the only record of that address, so it is left alone. "
    "Decide whether to normalise it onto the right Horizons of Tech contact or drop it.",
    "210 soft-deleted contacts still hold a phone slot (migration 0048 reports all of them). None currently "
    "blocks a live contact, so nothing is broken today, but each is a landmine for any future lead carrying "
    "that number. Note some hold garbage rather than phone numbers (e.g. contact 504 claims 'nvoids-728').",
]

# --------------------------------------------------------------------------------------


def _log(session, *, action_type: str, primary: int, payload: dict, secondary: int | None = None) -> None:
    session.add(ContactIdentityAction(
        owner_id=settings.owner_id, action_type=action_type, primary_contact_id=primary,
        secondary_contact_id=secondary, value=json.dumps(payload, sort_keys=True),
        source=SOURCE, created_at=utc_now(),
    ))


def _live(session, contact_id: int) -> PremiumNumberContact | None:
    return session.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_(None),
    ).first()


def _set_headline(contact: PremiumNumberContact, email: str, role: str) -> None:
    """Local copy of what Phase 1 will centralise as contact_identity_service.
    set_headline_email - this script has to stand alone and run before Phase 1."""
    domain = email.rpartition("@")[2]
    if role == "recruiter":
        contact.recruiter_email, contact.recruiter_email_domain = email, domain
    else:
        contact.employer_email, contact.employer_email_domain = email, domain


def plan(session) -> tuple[dict[str, list], list[str]]:
    """Classify every decision as todo / done / problem. Idempotent: re-running after a
    successful apply yields an all-done plan rather than a preflight failure."""
    todo: dict[str, list] = {"reassign": [], "clear": [], "merge": [], "delete": []}
    done: dict[str, int] = {"reassign": 0, "clear": 0, "merge": 0, "delete": 0}
    problems: list[str] = []

    if "role" not in {c["name"] for c in sa.inspect(session.get_bind()).get_columns("premium_contact_emails")}:
        problems.append("premium_contact_emails.role is missing - apply alembic revision 20260910_0048 first")

    for item in REASSIGN:
        email, from_id, to_id, _role, _why = item
        row = session.query(PremiumContactEmail).filter(
            PremiumContactEmail.owner_id == settings.owner_id,
            PremiumContactEmail.normalized_email == email,
        ).first()
        if row is None:
            problems.append(f"REASSIGN {email}: no child row found at all")
        elif row.premium_contact_id == to_id:
            done["reassign"] += 1
        elif row.premium_contact_id == from_id:
            if _live(session, to_id) is None:
                problems.append(f"REASSIGN {email}: target contact {to_id} is missing or deleted")
            else:
                todo["reassign"].append(item)
        else:
            problems.append(
                f"REASSIGN {email}: expected the child row on {from_id} (todo) or {to_id} (done), "
                f"found it on {row.premium_contact_id}"
            )

    for item in CLEAR_HEADLINE:
        contact_id, column, expected, _why = item
        contact = _live(session, contact_id)
        current = (getattr(contact, column) or "").strip().lower() if contact else None
        if contact is None:
            problems.append(f"CLEAR_HEADLINE {contact_id}: contact missing or deleted")
        elif current == "":
            done["clear"] += 1
        elif current == expected:
            todo["clear"].append(item)
        else:
            problems.append(f"CLEAR_HEADLINE {contact_id}.{column}: expected {expected!r} or blank, found {current!r}")

    for item in MERGES:
        canonical_id, loser_ids, _rename, _why = item
        if _live(session, canonical_id) is None:
            problems.append(f"MERGE {canonical_id}: canonical contact is missing or deleted")
        elif all(_live(session, loser_id) is None for loser_id in loser_ids):
            done["merge"] += 1
        elif all(_live(session, loser_id) is not None for loser_id in loser_ids):
            todo["merge"].append(item)
        else:
            problems.append(f"MERGE {canonical_id}: losers {loser_ids} are half-merged - resolve by hand")

    for item in (*DELETE_MALFORMED, *DELETE_JUNK):
        row_id, contact_id, stored, detail = item
        row = session.get(PremiumContactEmail, row_id)
        if row is None:
            done["delete"] += 1
        elif row.normalized_email != stored or row.premium_contact_id != contact_id:
            problems.append(
                f"DELETE row {row_id}: expected {stored!r} on contact {contact_id}, "
                f"found {row.normalized_email!r} on {row.premium_contact_id}"
            )
        elif item in DELETE_MALFORMED and not session.query(PremiumContactEmail).filter(
            PremiumContactEmail.normalized_email == detail, PremiumContactEmail.id != row_id,
        ).first():
            # Refuse to delete the only surviving record of a real address.
            problems.append(f"DELETE row {row_id}: {detail} has no clean duplicate - deleting would lose it")
        else:
            todo["delete"].append(item)

    for key, count in done.items():
        if count:
            print(f"  (already applied: {count} {key})")
    return todo, problems


def run(*, apply: bool) -> None:
    session = SessionLocal()
    try:
        print("preflight:")
        todo, problems = plan(session)
        if problems:
            print("\nPREFLIGHT FAILED - the database does not match these decisions. Nothing was changed.\n")
            for problem in problems:
                print(f"  ! {problem}")
            sys.exit(1)
        print("  OK\n")

        print(f"== reassign {len(todo['reassign'])} child email row(s) to the contact the domain points at ==")
        for email, from_id, to_id, role, why in todo["reassign"]:
            row = session.query(PremiumContactEmail).filter(
                PremiumContactEmail.owner_id == settings.owner_id,
                PremiumContactEmail.normalized_email == email,
            ).first()
            print(f"  {email}: contact {from_id} -> {to_id} ({role})\n      {why}")
            if apply:
                others = session.query(PremiumContactEmail).filter(
                    PremiumContactEmail.premium_contact_id == to_id, PremiumContactEmail.id != row.id,
                ).count()
                row.premium_contact_id, row.role, row.is_primary = to_id, role, others == 0
                # In every one of these cases the target's headline ALREADY names this
                # address - that disagreement is what made it a mismatch. Rewriting it is a
                # no-op on the address itself, but it does refresh a stale _domain column.
                _set_headline(_live(session, to_id), email, role)
                _log(session, action_type="reassign_email", primary=to_id, secondary=from_id,
                     payload={"email": email, "from_contact_id": from_id, "reason": why})

        print(f"\n== clear {len(todo['clear'])} bogus headline email(s) - the child row was right here ==")
        for contact_id, column, expected, why in todo["clear"]:
            print(f"  contact {contact_id}.{column} = {expected} -> ''\n      {why}")
            if apply:
                contact = _live(session, contact_id)
                _log(session, action_type="clear_headline_email", primary=contact_id,
                     payload={"column": column, "old_value": getattr(contact, column), "reason": why})
                setattr(contact, column, "")
                setattr(contact, f"{column}_domain", "")

        print(f"\n== merge {len(todo['merge'])} duplicate group(s) ==")
        for canonical_id, loser_ids, rename_to, why in todo["merge"]:
            print(f"  keep {canonical_id}, merge in {loser_ids}"
                  + (f", rename to {rename_to!r}" if rename_to else "") + f"\n      {why}")
            if apply:
                for loser_id in loser_ids:
                    contact_identity_service.merge_contacts(
                        session, owner_id=settings.owner_id, canonical_contact_id=canonical_id,
                        loser_contact_id=loser_id, source=SOURCE,
                    )
                if rename_to:
                    canonical = _live(session, canonical_id)
                    # _merge_contact_records moves identifiers and history but never names -
                    # the survivor here is the one holding the phone, not the one with the name.
                    canonical.recruiter_name = canonical.owner_name = rename_to

        print(f"\n== delete {len(todo['delete'])} malformed child email row(s) ==")
        for row_id, contact_id, stored, detail in todo["delete"]:
            print(f"  row {row_id} on contact {contact_id}: {stored!r}\n      -> holds {detail}")
            if apply:
                _log(session, action_type="delete_malformed_email", primary=contact_id,
                     payload={"row_id": row_id, "stored_value": stored, "address": detail})
                session.delete(session.get(PremiumContactEmail, row_id))

        print(f"\n== {len(NEEDS_HUMAN_DECISION)} item(s) left for you - NOT acted on ==")
        for note in NEEDS_HUMAN_DECISION:
            print(f"  ? {note}")

        if apply:
            session.commit()
            print("\nCOMMITTED.")
        else:
            session.rollback()
            print("\nDRY RUN - nothing was written. Re-run with --apply to commit.")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the changes (default is a dry run)")
    run(apply=parser.parse_args().apply)
