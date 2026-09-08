"""Blank the end-client values that were copied from the recruiter's own company.

    PYTHONPATH=. uv run python -m scripts.blank_vendor_as_end_client --dry-run
    PYTHONPATH=. uv run python -m scripts.blank_vendor_as_end_client --apply

Run deliberately. Not scheduled, not reachable from a route, not called on boot,
and deliberately not an Alembic migration - `alembic upgrade head` runs when the
backend container starts, and a several-thousand-row data rewrite has no business
in a boot path.

**The defect.** `resume_tracking_service.create_manual_application` required a
non-blank end client, so `create_application_from_recruiter_email` satisfied it
the only way it could::

    manual_end_client=(email.end_client or '').strip() or recruiter_company,

Every send whose posting did not name a client therefore recorded the staffing
firm that sent the email - or, when that was unknown too, the literal "Unknown".
The column is a search filter and is interpolated into outgoing follow-up mail,
so the value is not merely decorative: a follow-up to Centillion reads "your
client Centillion". Blank degrades to "your client" there, which is true.

**Why a separate script from `clean_end_client_backfill`.** That one blanks what
`end_client_validation` refuses - markup fragments, sentences, prose. It cannot
help here, because "Centillion Infotech" is a perfectly well-formed company name.
It is wrong for a reason no syntactic check can see: it names the wrong company.

**What it changes.** Only `Application` rows where all three hold:

1. the row was auto-logged from a recruiter email (`dedupe_key` is
   ``recruiter_email:{id}``) - a hand-typed value is the user's own statement and
   this script has no standing to overrule it;
2. the source email's own extracted `end_client` is blank - so the stored value
   cannot have come from the posting; and
3. `end_client_snapshot` equals what the fallback would have written for that
   email - its `company`, or the literal "Unknown" when the email named none -
   case- and whitespace-insensitively.

Condition 3 used to compare against `recruiter_company_snapshot`, the column the
fallback copied *from*. Reading the substitution off the row itself is only sound
while nothing else rewrites that column, and something since did:
`resnapshot_recruiter_identity` set it to "Unknown" on 725 rows whose recorded
recruiter turned out to be whoever forwarded the requirement, and the signature
went with it. The source email still holds the value the fallback copied, so the
comparison is made against that.

Rows meeting 1 and 3 but *not* 2 are reported and left alone: the email named a
client and the snapshot disagrees with it, which is a different problem needing a
human. Both `end_client_snapshot` and `manual_end_client` are cleared, because
the fallback wrote the same manufactured value into both.

**Idempotence.** A cleared row is not selected at all - `scan` reads only rows
with a non-blank `end_client_snapshot` - so a second `--apply` finds nothing and
writes nothing.

**Audit trail.** Every run - dry or applied - writes a JSON file to
`backend/var/end_client_remediation/` recording the timestamp, git commit, mode,
and every row it touched with its before and after values. The applied file is
what proves later what was destroyed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Application, RecruiterEmail

AUDIT_DIR = Path(__file__).resolve().parent.parent / "var" / "end_client_remediation"

DEDUPE_PREFIX = "recruiter_email:"


@dataclass(frozen=True)
class Change:
    application_id: int
    source_email_id: int | None
    recruiter_company: str
    before: str
    after: str
    reason: str


def _git_commit() -> str:
    """The commit the run was made from, for the audit trail.

    `git` is not installed in the backend image, so a container run falls back to
    the `GIT_COMMIT` environment variable. An audit record that cannot name its
    own code version is weaker evidence than one that can.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return (os.environ.get("GIT_COMMIT") or "").strip() or "unknown"


def _source_email_id(dedupe_key: str | None) -> int | None:
    key = str(dedupe_key or "")
    if not key.startswith(DEDUPE_PREFIX):
        return None
    try:
        return int(key[len(DEDUPE_PREFIX):])
    except ValueError:
        return None


def _key(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def scan(db: Session) -> tuple[list[Change], list[Change], int]:
    """Return (to_clear, needs_review, populated) over legacy applications."""
    rows = (
        db.query(Application)
        .filter(Application.end_client_snapshot.isnot(None))
        .filter(Application.end_client_snapshot != "")
        .order_by(Application.id)
        .all()
    )
    email_ids = {eid for row in rows if (eid := _source_email_id(row.dedupe_key)) is not None}
    extracted: dict[int, tuple[str, str]] = {}
    if email_ids:
        # One query rather than one per row: the dev database alone holds 3,400.
        for email_id, end_client, company in (
            db.query(RecruiterEmail.id, RecruiterEmail.end_client, RecruiterEmail.company)
            .filter(RecruiterEmail.id.in_(email_ids))
            .all()
        ):
            extracted[int(email_id)] = (
                str(end_client or "").strip(),
                str(company or "").strip(),
            )

    to_clear: list[Change] = []
    needs_review: list[Change] = []
    for row in rows:
        before = str(row.end_client_snapshot or "").strip()
        company = str(row.recruiter_company_snapshot or "").strip()
        source_email_id = _source_email_id(row.dedupe_key)
        if source_email_id is None:
            # No source email to compare against, so the row-local signature is
            # all there is. It still holds here: this script never rewrites what
            # the user typed, and nothing has rewritten these rows either.
            if _key(before) == _key(company):
                needs_review.append(
                    Change(
                        application_id=row.id,
                        source_email_id=None,
                        recruiter_company=company,
                        before=before,
                        after="(unchanged - hand-logged, the user typed this)",
                        reason="manual_entry",
                    )
                )
            continue
        source = extracted.get(source_email_id)
        if source is None:
            if _key(before) == _key(company):
                needs_review.append(
                    Change(
                        application_id=row.id,
                        source_email_id=source_email_id,
                        recruiter_company=company,
                        before=before,
                        after="(unchanged - source email is gone, cannot confirm)",
                        reason="source_email_missing",
                    )
                )
            continue
        posting_value, sender_company = source
        # What the fallback would have written for this email, whatever the row's
        # own recruiter columns say today.
        if _key(before) != _key(sender_company or "Unknown"):
            continue
        if posting_value:
            needs_review.append(
                Change(
                    application_id=row.id,
                    source_email_id=source_email_id,
                    recruiter_company=company,
                    before=before,
                    after=f"(unchanged - the posting says {posting_value!r})",
                    reason="posting_named_a_client",
                )
            )
            continue
        to_clear.append(
            Change(
                application_id=row.id,
                source_email_id=source_email_id,
                recruiter_company=company,
                before=before,
                after="",
                reason="recruiter_company_substituted",
            )
        )
    return to_clear, needs_review, len(rows)


def _print_report(
    to_clear: list[Change],
    needs_review: list[Change],
    populated: int,
    *,
    applied: bool,
) -> None:
    print()
    print("=" * 78)
    print(f"  vendor-as-end-client remediation - {'APPLY' if applied else 'DRY RUN'}")
    print("=" * 78)

    if to_clear:
        print(f"\n  {'Cleared' if applied else 'Would clear'} {len(to_clear)} application(s).")
        print("  Most frequent substituted values:\n")
        for value, count in Counter(change.before for change in to_clear).most_common(15):
            print(f"    {count:>6}  {value!r}")
    else:
        print("\n  Nothing to clear.")

    print("\n  " + "-" * 74)
    print(f"  Populated end_client_snapshot rows : {populated}")
    print(f"  {'Cleared' if applied else 'To clear':<35}: {len(to_clear)}")
    print(f"  Left for a human decision          : {len(needs_review)}")
    if needs_review:
        print("\n  By reason (never written by this script):")
        for reason, count in Counter(change.reason for change in needs_review).most_common():
            print(f"    {count:>6}  {reason}")
        for change in needs_review[:20]:
            print(f"      application {change.application_id}: {change.before!r} {change.after}")
        if len(needs_review) > 20:
            print(f"      ... and {len(needs_review) - 20} more, all in the audit file")


def _write_audit(
    to_clear: list[Change],
    needs_review: list[Change],
    populated: int,
    *,
    applied: bool,
) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = AUDIT_DIR / f"{stamp}-vendor-as-end-client-{'apply' if applied else 'dryrun'}.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "git_commit": _git_commit(),
                "mode": "apply" if applied else "dry-run",
                "totals": {
                    "populated": populated,
                    "changed" if applied else "would_change": len(to_clear),
                    "flagged_for_review": len(needs_review),
                },
                "changes": [asdict(change) for change in to_clear],
                "review_only": [asdict(change) for change in needs_review],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report only. Writes nothing to the database.")
    mode.add_argument("--apply", action="store_true", help="Blank the values reported by --dry-run.")
    args = parser.parse_args()
    applied = bool(args.apply)

    db = SessionLocal()
    try:
        to_clear, needs_review, populated = scan(db)

        if applied and to_clear:
            ids = {change.application_id for change in to_clear}
            for row in db.query(Application).filter(Application.id.in_(ids)).all():
                row.end_client_snapshot = ""
                row.manual_end_client = ""
            db.commit()

        _print_report(to_clear, needs_review, populated, applied=applied)
        audit_path = _write_audit(to_clear, needs_review, populated, applied=applied)
        print(f"\n  Audit trail: {audit_path}")
        if not applied and to_clear:
            print("  Re-run with --apply to write these changes.")
        print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
