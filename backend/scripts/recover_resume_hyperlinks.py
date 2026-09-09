"""Recover the hyperlinks that extraction used to drop from stored resume variants.

`unstructured` always read the link targets out of a .docx and handed them over
in `metadata.links`; `elements_to_markdown` kept only `element.text`, so a
contact line arrived as the word "LinkedIn" with the address gone. That is fixed
going forward. This repairs the variants already in the database by re-reading
their files.

The safety rule is that this only ever *adds* links. For each variant it
re-extracts the stored file, strips every `[anchor](url)` back down to `anchor`,
and compares that against what is stored today. If they match, the only
difference is links, and the row is safe to update. If they differ at all - the
extractor changed, the file was replaced, someone edited the text by hand - the
variant is reported for review and left exactly as it is.

Every variant it would touch is written to a timestamped backup file first, and
that happens even on a dry run, so there is always something to restore from.

Usage:
    python scripts/recover_resume_hyperlinks.py                  # dry run, writes a backup
    python scripts/recover_resume_hyperlinks.py --apply          # writes the safe ones
    python scripts/recover_resume_hyperlinks.py --apply --id 15  # one variant
    python scripts/recover_resume_hyperlinks.py --restore FILE   # put a backup back
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import ResumeAsset  # noqa: E402
from app.parsing.document_extraction import extract_document_text  # noqa: E402

# Under the resume storage directory because that is the one path mounted as a
# persistent volume. A backup written anywhere else in the image is gone at the
# next `docker compose up --build`, which is exactly when it would be wanted.
BACKUP_DIR = Path(settings.resume_storage_dir).parent / "resume-link-recovery"
# Must match enrich_resume, or the comparison is against a truncated document.
ENRICHMENT_MAX_CHARS = 50000
_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://[^\s)]+|mailto:[^\s)]+)\)")


def unlinked(text: str) -> str:
    """`[LinkedIn](url)` -> `LinkedIn`, so two texts compare on their words alone."""
    return _LINK.sub(r"\1", text or "")


def _backup_path(stamp: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"resume-variants-{stamp}.json"


def examine(row: ResumeAsset) -> dict[str, object]:
    """Decide what should happen to one variant, without changing anything."""
    verdict: dict[str, object] = {
        "id": row.id,
        "file_name": row.file_name,
        "stored_characters": len(row.content_markdown or ""),
    }
    path = Path(row.file_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return {**verdict, "action": "skip", "reason": "file is no longer on disk"}
    if row.content_markdown is None:
        return {**verdict, "action": "skip", "reason": "no text was ever extracted"}

    try:
        # The same limit enrich_resume uses. The default is 7000, which would
        # clip a resume to a quarter of itself and read as a rewrite.
        extracted = extract_document_text(path, row.file_name, max_chars=ENRICHMENT_MAX_CHARS).markdown_text
    except Exception as exc:  # a file we cannot read is a report, not a crash
        return {**verdict, "action": "skip", "reason": f"{type(exc).__name__}: {exc}"}

    links = _LINK.findall(extracted)
    stored = row.content_markdown or ""
    # Both sides stripped, so this is idempotent: once the links are stored,
    # a second run compares words against words and reports "nothing" rather
    # than flagging every variant it just repaired.
    if unlinked(extracted).strip() != unlinked(stored).strip():
        # The words moved, not just the links. Could be a hand edit, a replaced
        # file, or a changed extractor - all of which a human should look at.
        return {
            **verdict,
            "action": "review",
            "reason": "re-extracted text differs by more than links",
            "new_characters": len(extracted),
            "links_found": links,
        }
    if not links or _LINK.findall(stored) == links:
        return {**verdict, "action": "nothing",
                "reason": "already carries its links" if links else "no hyperlinks in the file"}
    return {
        **verdict,
        "action": "update",
        "links_found": links,
        "new_text": extracted,
        "new_characters": len(extracted),
    }


def run(*, apply: bool, only_id: int | None) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    db = SessionLocal()
    try:
        query = db.query(ResumeAsset).filter(ResumeAsset.owner_id == settings.owner_id)
        if only_id is not None:
            query = query.filter(ResumeAsset.id == only_id)
        rows = query.order_by(ResumeAsset.id).all()

        verdicts = [examine(row) for row in rows]
        updatable = [item for item in verdicts if item["action"] == "update"]

        # Backed up before anything is written, and on a dry run too: the point
        # of a rehearsal is that the real thing has a net under it.
        backup = _backup_path(stamp)
        backup.write_text(
            json.dumps(
                [
                    {"id": row.id, "file_name": row.file_name, "file_path": row.file_path,
                     "content_markdown": row.content_markdown}
                    for row in rows
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Backed up {len(rows)} variants -> {backup}\n")

        for item in verdicts:
            code = f"R{item['id']:02d}"
            if item["action"] == "update":
                print(f"  {code} {item['file_name'][:52]:<54} + {len(item['links_found'])} link(s)")
                for anchor in item["links_found"]:
                    print(f"        {anchor}")
            elif item["action"] == "review":
                print(f"  {code} REVIEW  {item['file_name'][:44]:<46} {item['reason']}")
                print(f"        stored {item['stored_characters']} chars, re-extracted {item['new_characters']}")
            elif item["action"] == "skip":
                print(f"  {code} skip    {item['file_name'][:44]:<46} {item['reason']}")

        if apply:
            by_id = {row.id: row for row in rows}
            for item in updatable:
                by_id[item["id"]].content_markdown = item["new_text"]
            db.commit()

        print()
        print(f"{len(verdicts)} variants: {len(updatable)} with links to recover, "
              f"{sum(1 for i in verdicts if i['action'] == 'review')} for review, "
              f"{sum(1 for i in verdicts if i['action'] == 'skip')} skipped.")
        print("Applied." if apply else "Dry run - nothing written. Re-run with --apply.")
        return len(updatable)
    finally:
        db.close()


def restore(path: Path) -> int:
    """Put a backup back, exactly. Only touches rows the backup actually holds."""
    saved = json.loads(path.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        restored = 0
        for entry in saved:
            row = (
                db.query(ResumeAsset)
                .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == entry["id"])
                .first()
            )
            if row is None:
                print(f"  R{entry['id']:02d} is gone - not recreated")
                continue
            if row.content_markdown != entry["content_markdown"]:
                row.content_markdown = entry["content_markdown"]
                restored += 1
        db.commit()
        print(f"Restored {restored} variants from {path}")
        return restored
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the safe updates")
    parser.add_argument("--id", type=int, default=None, help="only this variant")
    parser.add_argument("--restore", type=Path, default=None, help="restore from a backup file")
    args = parser.parse_args()

    if args.restore is not None:
        restore(args.restore)
        return
    run(apply=args.apply, only_id=args.id)


if __name__ == "__main__":
    main()
