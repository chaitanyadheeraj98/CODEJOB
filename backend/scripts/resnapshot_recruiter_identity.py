"""Re-record the recruiter on sends that named the person who forwarded the mail.

    PYTHONPATH=. uv run python -m scripts.resnapshot_recruiter_identity --dry-run
    PYTHONPATH=. uv run python -m scripts.resnapshot_recruiter_identity --apply

Run deliberately. Not scheduled, not reachable from a route, not called on boot,
and deliberately not an Alembic migration - `alembic upgrade head` runs when the
backend container starts, and a several-thousand-row data rewrite has no business
in a boot path.

**The defect.** Both auto-log paths snapshotted the sender::

    sender_name, sender_address = parseaddr(email.sender or '')

On a forwarded requirement the sender is whoever passed the posting along, not
the recruiter the resume went to. `stamp_recruiter_email_identity` had already
worked out the right person and stored it in `recruiter_emails.resolved_recruiter_email`
during the run, and nothing read it. Measured on the tracked rows, the two were
different people 1,770 times out of 3,386.

The same mistake filed the *company*: `recruiter_emails.company` is the firm that
sent the mail by the extractor's own definition, so copying it onto a recruiter on
another domain names a firm the mail never named for them.

**What it changes.** Two passes over one root cause, in one audit file:

1. **Applications and tracked applications.** For every row whose dedupe key names
   a source email, `recruiter_identity_service.recruiter_identity_for` is asked who
   the recruiter is, and only the fields that disagree are rewritten - the two
   snapshot columns, the three `manual_recruiter_*` columns, and
   `recruiter_contact_id`, which was never passed and is NULL on every existing row
   (so each card's live-vs-snapshot comparison had nothing live to compare).

2. **Recruiter contacts.** A contact created by the sourcing panel's fallback
   inherited the source email's company. Where that company is still exactly what
   the email said *and* the contact's own address is on a different domain than the
   sender's, it is set to "Unknown" - the store's existing word for not identified.

**What it refuses to touch.** Hand-logged rows: a dedupe key without a recognised
source-email prefix is the user's own typing and this script has no standing to
overrule it, the same rule `blank_vendor_as_end_client` already applies. Rows whose
recorded recruiter address matches neither the sender nor the resolved recruiter
are reported and left alone too: somebody typed that address deliberately.

**Say this before `--apply`.** Blanking a contact's company moves it into the
flagged set in the premium-numbers inventory - `_contact_is_flagged_expr` counts a
missing or "Unknown" company as needing review. That is the designed route for "we
do not know", but it is a visible increase in a review count.

**Idempotence.** Pass 1 writes what `recruiter_identity_for` returns, so a second
run finds every field already equal and writes nothing. A contact set to "Unknown"
no longer equals its email's company, so pass 2 skips it thereafter.

**Audit trail.** Every run - dry or applied - writes a JSON file to
`backend/var/recruiter_identity_remediation/` recording the timestamp, git commit,
mode, and every field it touched with its before and after values.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Application, AppTSApplication, PremiumContactEmail, PremiumNumberContact, RecruiterEmail
from app.phase0 import email_domain
from app.services import recruiter_identity_service

AUDIT_DIR = Path(__file__).resolve().parent.parent / "var" / "recruiter_identity_remediation"

# Which dedupe-key prefix marks an auto-logged row in each table. Anything else was
# typed by a human.
SOURCES: tuple[tuple[type, str], ...] = (
    (Application, "recruiter_email:"),
    (AppTSApplication, "appts_email:"),
)

UNKNOWN = "Unknown"


@dataclass(frozen=True)
class Change:
    table: str
    row_id: int
    source_email_id: int | None
    reason: str
    # field name -> [before, after]. Empty on a review-only entry.
    fields: dict[str, list[object]] = field(default_factory=dict)
    note: str = ""


def _git_commit() -> str:
    """The commit the run was made from, for the audit trail.

    `git` is not installed in the backend image, so a container run falls back to
    the `GIT_COMMIT` environment variable.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return (os.environ.get("GIT_COMMIT") or "").strip() or "unknown"


def _source_email_id(dedupe_key: str | None, prefix: str) -> int | None:
    key = str(dedupe_key or "")
    if not key.startswith(prefix):
        return None
    try:
        return int(key[len(prefix):])
    except ValueError:
        return None


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _same(left: object, right: object) -> bool:
    return _text(left).casefold() == _text(right).casefold()


def _stated(value: object) -> bool:
    """A value that says something. "Unknown" is the store's word for not identified."""
    text = _text(value)
    return bool(text) and text.casefold() != UNKNOWN.casefold()


def _address(value: object) -> str:
    return _text(parseaddr(str(value or ""))[1]).casefold()


def scan_applications(db: Session, *, contacts_losing_company: set[int]) -> tuple[list[Change], list[Change], int]:
    """Recompute the recruiter on every auto-logged send. Reads nothing it writes.

    `contacts_losing_company` is what pass 2 is about to blank. Pass 1 has to know,
    or it would copy onto the card the very company pass 2 is removing from the
    contact - which is how email 8027 kept "RPATECHNOLOGY INC" in a first draft of
    this script even though the recruiter it named had changed.
    """
    to_change: list[Change] = []
    review: list[Change] = []
    seen = 0

    for model, prefix in SOURCES:
        rows = db.query(model).order_by(model.id).all()
        keyed = [(row, eid) for row in rows if (eid := _source_email_id(row.dedupe_key, prefix)) is not None]
        seen += len(keyed)
        emails: dict[int, RecruiterEmail] = {}
        if keyed:
            # One query for the lot: the dev database alone holds 3,400 sends.
            for email in db.query(RecruiterEmail).filter(RecruiterEmail.id.in_({eid for _, eid in keyed})).all():
                emails[int(email.id)] = email

        for row, email_id in keyed:
            email = emails.get(email_id)
            if email is None:
                review.append(Change(
                    table=model.__tablename__, row_id=row.id, source_email_id=email_id,
                    reason="source_email_missing", note="the source email is gone, so nothing can be recomputed",
                ))
                continue

            identity = recruiter_identity_service.recruiter_identity_for(db, email, owner_id=row.owner_id)
            if not identity.address:
                review.append(Change(
                    table=model.__tablename__, row_id=row.id, source_email_id=email_id,
                    reason="no_recruiter_address", note="neither the recipient nor the sender is a usable address",
                ))
                continue

            recorded = _address(row.manual_recruiter_email)
            sender = _address(parseaddr(email.sender or "")[1])
            if recorded and recorded not in {identity.address, sender}:
                review.append(Change(
                    table=model.__tablename__, row_id=row.id, source_email_id=email_id,
                    reason="recorded_address_is_neither",
                    note=f"row says {recorded!r}, mail says sender {sender!r} / recruiter {identity.address!r}",
                ))
                continue

            company = identity.company
            if identity.contact_id is not None and identity.contact_id in contacts_losing_company:
                company = recruiter_identity_service.sender_company_for(email, identity.address)
            wanted: dict[str, object] = {
                "recruiter_name_snapshot": identity.name or UNKNOWN,
                "manual_recruiter_name": identity.name or UNKNOWN,
                "recruiter_company_snapshot": company or UNKNOWN,
                "manual_recruiter_company": company or UNKNOWN,
                "manual_recruiter_email": identity.address,
                "recruiter_contact_id": identity.contact_id,
            }
            renaming = recorded != identity.address
            if not renaming:
                # The row already names the right person, so whatever is written on
                # it describes them - including `email.company`, which was the
                # sender's firm and the sender is the recruiter here. Only fill what
                # is missing. Overwriting a stated name would silently undo an edit
                # the user made on the card.
                wanted = {
                    name: value
                    for name, value in wanted.items()
                    if name == "recruiter_contact_id" or not _stated(getattr(row, name))
                }
            differing = {
                name: [getattr(row, name), value]
                for name, value in wanted.items()
                if getattr(row, name) != value and not (
                    name != "recruiter_contact_id" and _same(getattr(row, name), value)
                )
            }
            if not differing:
                continue
            to_change.append(Change(
                table=model.__tablename__, row_id=row.id, source_email_id=email_id,
                reason="renamed_the_recruiter" if renaming else "linked_the_contact_record",
                fields=differing,
            ))
    return to_change, review, seen


def _contact_address(db: Session, contact: PremiumNumberContact) -> str:
    """The contact's own recruiter address, headline column first."""
    headline = _text(contact.recruiter_email).casefold()
    if headline:
        return headline
    claimed = (
        db.query(PremiumContactEmail.normalized_email)
        .filter(
            PremiumContactEmail.owner_id == contact.owner_id,
            PremiumContactEmail.premium_contact_id == contact.id,
        )
        .order_by(PremiumContactEmail.is_primary.desc(), PremiumContactEmail.id)
        .first()
    )
    return _text(claimed[0]).casefold() if claimed else ""


def scan_contacts(db: Session) -> tuple[list[Change], list[Change], int]:
    """Find recruiter contacts wearing the company of the mail that introduced them."""
    contacts = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.first_detected_email_id.isnot(None),
        )
        .order_by(PremiumNumberContact.id)
        .all()
    )
    emails: dict[int, RecruiterEmail] = {}
    if contacts:
        wanted = {int(contact.first_detected_email_id) for contact in contacts}
        for email in db.query(RecruiterEmail).filter(RecruiterEmail.id.in_(wanted)).all():
            emails[int(email.id)] = email

    to_change: list[Change] = []
    review: list[Change] = []
    for contact in contacts:
        company = _text(contact.company)
        if not company or company.casefold() == UNKNOWN.casefold():
            continue
        email = emails.get(int(contact.first_detected_email_id))
        if email is None:
            continue
        if not _same(company, email.company):
            # The company came from somewhere else - a signature, a lead, a human.
            continue
        address = _contact_address(db, contact)
        if not address:
            review.append(Change(
                table=PremiumNumberContact.__tablename__, row_id=contact.id, source_email_id=int(email.id),
                reason="contact_has_no_address", note="cannot tell whose company this is without an address",
            ))
            continue
        if recruiter_identity_service.sender_company_for(email, address):
            # Same domain as the sender, so the mail did name this firm for them.
            continue
        to_change.append(Change(
            table=PremiumNumberContact.__tablename__, row_id=contact.id, source_email_id=int(email.id),
            reason="company_copied_across_a_domain_boundary",
            fields={"company": [contact.company, UNKNOWN]},
            note=f"{address} is not on {email_domain(_address(parseaddr(email.sender or '')[1])) or '?'}",
        ))
    return to_change, review, len(contacts)


def _apply(db: Session, applications: list[Change], contacts: list[Change]) -> None:
    by_table = {model.__tablename__: model for model, _ in SOURCES}
    for change in applications:
        model = by_table[change.table]
        row = db.query(model).filter(model.id == change.row_id).first()
        if row is None:
            continue
        for name, (_before, after) in change.fields.items():
            setattr(row, name, after)
    for change in contacts:
        row = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == change.row_id).first()
        if row is not None:
            row.company = UNKNOWN
    db.commit()


def _print_report(
    applications: list[Change],
    contacts: list[Change],
    review: list[Change],
    *,
    scanned_rows: int,
    scanned_contacts: int,
    applied: bool,
) -> None:
    verb = "Rewrote" if applied else "Would rewrite"
    print()
    print("=" * 78)
    print(f"  recruiter identity remediation - {'APPLY' if applied else 'DRY RUN'}")
    print("=" * 78)

    print(f"\n  Auto-logged sends scanned          : {scanned_rows}")
    print(f"  {verb + ' send(s)':<35}: {len(applications)}")
    for reason, count in Counter(change.reason for change in applications).most_common():
        print(f"    {count:>6}  {reason}")
    renamed = [change for change in applications if "manual_recruiter_email" in change.fields]
    if renamed:
        print(f"\n  Of those, {len(renamed)} change the recorded recruiter address. First few:\n")
        for change in renamed[:10]:
            before, after = change.fields["manual_recruiter_email"]
            name = change.fields.get("recruiter_name_snapshot", ["", ""])
            print(f"    {change.table} {change.row_id}: {before!r} -> {after!r}  ({name[0]!r} -> {name[1]!r})")

    print(f"\n  Recruiter contacts scanned         : {scanned_contacts}")
    print(f"  {verb + ' contact compan(ies)':<35}: {len(contacts)}")
    for value, count in Counter(_text(change.fields['company'][0]) for change in contacts).most_common(10):
        print(f"    {count:>6}  {value!r}")
    if contacts:
        print(f"\n  Note: {len(contacts)} contact(s) become company-less and will count as")
        print("  needing review in the premium-numbers inventory. That is the designed")
        print("  route for 'not identified', but it is a visible increase.")

    print(f"\n  Left for a human decision          : {len(review)}")
    for reason, count in Counter(change.reason for change in review).most_common():
        print(f"    {count:>6}  {reason}")
    for change in review[:10]:
        print(f"      {change.table} {change.row_id}: {change.note}")
    if len(review) > 10:
        print(f"      ... and {len(review) - 10} more, all in the audit file")


def _write_audit(
    applications: list[Change],
    contacts: list[Change],
    review: list[Change],
    *,
    scanned_rows: int,
    scanned_contacts: int,
    applied: bool,
) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = AUDIT_DIR / f"{stamp}-recruiter-identity-{'apply' if applied else 'dryrun'}.json"
    key = "changed" if applied else "would_change"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "git_commit": _git_commit(),
                "mode": "apply" if applied else "dry-run",
                "totals": {
                    "auto_logged_sends_scanned": scanned_rows,
                    "recruiter_contacts_scanned": scanned_contacts,
                    f"sends_{key}": len(applications),
                    f"contacts_{key}": len(contacts),
                    "flagged_for_review": len(review),
                },
                "applications": [asdict(change) for change in applications],
                "contacts": [asdict(change) for change in contacts],
                "review_only": [asdict(change) for change in review],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report only. Writes nothing to the database.")
    mode.add_argument("--apply", action="store_true", help="Write the changes reported by --dry-run.")
    args = parser.parse_args()
    applied = bool(args.apply)

    db = SessionLocal()
    try:
        # Contacts first: pass 1 reads contact records, so it has to be told which
        # of them pass 2 is about to strip.
        contacts, contact_review, scanned_contacts = scan_contacts(db)
        applications, app_review, scanned_rows = scan_applications(
            db, contacts_losing_company={change.row_id for change in contacts}
        )
        review = app_review + contact_review

        # `recruiter_identity_for` only reads, but the scan opened a session that
        # would otherwise carry a stray identity map into the write.
        db.expunge_all()
        if applied and (applications or contacts):
            _apply(db, applications, contacts)
        else:
            db.rollback()

        _print_report(
            applications, contacts, review,
            scanned_rows=scanned_rows, scanned_contacts=scanned_contacts, applied=applied,
        )
        audit_path = _write_audit(
            applications, contacts, review,
            scanned_rows=scanned_rows, scanned_contacts=scanned_contacts, applied=applied,
        )
        print(f"\n  Audit trail: {audit_path}")
        if not applied and (applications or contacts):
            print("  Re-run with --apply to write these changes.")
        print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
