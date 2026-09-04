"""Blank the invalid `end_client` values the mapping defect wrote, and nothing else.

    PYTHONPATH=. uv run python -m scripts.clean_end_client_backfill --dry-run
    PYTHONPATH=. uv run python -m scripts.clean_end_client_backfill --apply

Run deliberately. Not scheduled, not reachable from a route, not called on boot.

**What it changes.** Only rows whose stored value `end_client_validation` refuses.
A refused value becomes `""`, which means *not identified* - never "no end
client", and never a substituted company. Rows holding a valid name are read and
left alone, so a re-run after the writers were fixed is a no-op.

**Why blanking and not repair.** `facing skills<br />...` is not a damaged
rendering of a real client name. It is a fragment of a sentence about something
else, produced when a label regex matched the "client" inside "client-facing".
There is nothing in it to recover, and trimming it to `facing` would yield a
value that passes every length and markup check while still being wrong.

**Idempotence.** `clean_end_client` is a pure function of the stored string and
its own output is a fixed point (asserted by `test_cleaning_is_idempotent`). A
second `--apply` finds nothing to change and writes nothing.

**Audit trail.** Every run - dry or applied - writes a JSON file to
`backend/var/end_client_remediation/` recording the timestamp, git commit, mode,
and every row it touched with its before value, after value, and the rule that
fired. A dry run and the apply that follows it are separately reconstructable,
and the applied file is what proves later what was destroyed.

`AppTSApplication.manual_end_client` and `end_client_snapshot` are read and
reported but never written: the audit found no invalid values there, and those
columns can hold a value the user typed, which this script has no standing to
overrule. If the report shows any, decide on them by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import AppTSApplication, RecruiterOpportunity
from app.services import end_client_validation

AUDIT_DIR = Path(__file__).resolve().parent.parent / "var" / "end_client_remediation"


@dataclass(frozen=True)
class Change:
    table: str
    row_id: int
    column: str
    source_type: str
    before: str
    after: str
    reason: str
    length_before: int


def _git_commit() -> str:
    """The commit the run was made from, for the audit trail.

    `git` is not installed in the backend image, so a container run falls back
    to the `GIT_COMMIT` environment variable. Pass it explicitly when running
    anywhere the repository is not on disk - an audit record that cannot name
    its own code version is weaker evidence than one that can.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return (os.environ.get("GIT_COMMIT") or "").strip() or "unknown"


def _scan_opportunities(db: Session) -> tuple[list[Change], int, int]:
    """Return (changes, populated, kept) for recruiter_opportunities."""
    changes: list[Change] = []
    populated = 0
    kept = 0
    rows = (
        db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.end_client.isnot(None))
        .filter(RecruiterOpportunity.end_client != "")
        .order_by(RecruiterOpportunity.id)
        .all()
    )
    for row in rows:
        populated += 1
        before = row.end_client or ""
        reason = end_client_validation.rejection_reason(before)
        if not reason:
            kept += 1
            continue
        changes.append(
            Change(
                table="recruiter_opportunities",
                row_id=row.id,
                column="end_client",
                source_type=row.source_type or "",
                before=before,
                after="",
                reason=reason,
                length_before=len(before),
            )
        )
    return changes, populated, kept


def _scan_tracked_applications(db: Session) -> list[Change]:
    """Report-only. Never written - see the module docstring."""
    found: list[Change] = []
    for row in db.query(AppTSApplication).order_by(AppTSApplication.id).all():
        for column in ("manual_end_client", "end_client_snapshot"):
            before = getattr(row, column, "") or ""
            if not before:
                continue
            reason = end_client_validation.rejection_reason(before)
            if reason:
                found.append(
                    Change(
                        table="appts_applications",
                        row_id=row.id,
                        column=column,
                        source_type="",
                        before=before,
                        after="(unchanged - review by hand)",
                        reason=reason,
                        length_before=len(before),
                    )
                )
    return found


def _print_report(
    changes: list[Change],
    populated: int,
    kept: int,
    review: list[Change],
    *,
    applied: bool,
) -> None:
    verb = "Cleared" if applied else "Would clear"
    print()
    print("=" * 78)
    print(f"  end_client remediation - {'APPLY' if applied else 'DRY RUN'}")
    print("=" * 78)

    if changes:
        print(f"\n  {verb} {len(changes)} row(s) in recruiter_opportunities:\n")
        width = max(len(str(c.row_id)) for c in changes)
        for change in changes:
            snippet = change.before if len(change.before) <= 66 else change.before[:63] + "..."
            print(f"    id {str(change.row_id).rjust(width)}  [{change.source_type or '-'}]  {change.reason}")
            print(f"      before ({change.length_before} ch): {snippet!r}")
            print(f"      after            : ''  (not identified)")
    else:
        print("\n  No invalid values found in recruiter_opportunities.")

    by_reason: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for change in changes:
        by_reason[change.reason] = by_reason.get(change.reason, 0) + 1
        key = change.source_type or "(none)"
        by_source[key] = by_source.get(key, 0) + 1

    print("\n  " + "-" * 74)
    print(f"  Populated end_client rows : {populated}")
    print(f"  {'Cleared' if applied else 'To clear':<25}: {len(changes)}")
    print(f"  Preserved unchanged       : {kept}")
    if by_reason:
        print("\n  By rule:")
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>4}  {reason}")
    if by_source:
        print("\n  By source_type:")
        for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
            print(f"    {count:>4}  {source}")

    if review:
        print(f"\n  {len(review)} tracked-application value(s) need a human decision")
        print("  (never written by this script):")
        for change in review:
            print(f"    appts id {change.row_id}.{change.column}: {change.before!r}  [{change.reason}]")
    else:
        print("\n  Tracked applications: no invalid values.")


def _write_audit(
    changes: list[Change],
    populated: int,
    kept: int,
    review: list[Change],
    *,
    applied: bool,
) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = AUDIT_DIR / f"{stamp}-{'apply' if applied else 'dryrun'}.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "git_commit": _git_commit(),
                "mode": "apply" if applied else "dry-run",
                "totals": {
                    "populated": populated,
                    "changed" if applied else "would_change": len(changes),
                    "preserved": kept,
                    "flagged_for_review": len(review),
                },
                "changes": [asdict(change) for change in changes],
                "review_only": [asdict(change) for change in review],
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
    mode.add_argument("--apply", action="store_true", help="Blank the invalid values reported by --dry-run.")
    args = parser.parse_args()
    applied = bool(args.apply)

    db = SessionLocal()
    try:
        changes, populated, kept = _scan_opportunities(db)
        review = _scan_tracked_applications(db)

        if applied and changes:
            ids = {change.row_id for change in changes}
            for row in db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id.in_(ids)).all():
                row.end_client = ""
            db.commit()

        _print_report(changes, populated, kept, review, applied=applied)
        audit_path = _write_audit(changes, populated, kept, review, applied=applied)
        print(f"\n  Audit trail: {audit_path}")
        if not applied and changes:
            print("  Re-run with --apply to write these changes.")
        print()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
