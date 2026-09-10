from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import gmail_client
from app.models import GmailLabel


@dataclass
class LabelSyncResult:
    created: int = 0
    updated: int = 0
    tombstoned: int = 0


def sync_labels(db: Session, owner_id: str, *, list_labels=gmail_client.list_gmail_labels) -> LabelSyncResult:
    from app.services.label_tracking_service import release_label

    items = list_labels()
    existing = {row.external_label_id: row for row in db.query(GmailLabel).filter(GmailLabel.owner_id == owner_id)}
    now = datetime.now(UTC)
    result = LabelSyncResult()
    seen = set()
    for item in items:
        label_id = item["id"]
        seen.add(label_id)
        row = existing.get(label_id)
        if row is None:
            row = GmailLabel(owner_id=owner_id, external_label_id=label_id, is_tracked=False)
            db.add(row)
            result.created += 1
        else:
            result.updated += 1
        row.name = item["name"]
        row.label_type = item.get("type") or ("user" if label_id.startswith("Label_") else "system")
        row.color_background = item.get("color", {}).get("backgroundColor")
        row.color_text = item.get("color", {}).get("textColor")
        row.message_count_snapshot = item.get("threadsTotal", row.message_count_snapshot or 0)
        row.last_synced_at = now
        row.deleted_at = None
    for label_id, row in existing.items():
        if label_id not in seen and row.deleted_at is None:
            row.deleted_at = now
            result.tombstoned += 1
            release_label(db, owner_id, label_id)
    db.flush()
    return result


def list_labels(db: Session, owner_id: str, *, tracked_only=False, include_deleted=False) -> list[GmailLabel]:
    query = db.query(GmailLabel).filter(GmailLabel.owner_id == owner_id)
    if tracked_only:
        query = query.filter(GmailLabel.is_tracked.is_(True))
    if not include_deleted:
        query = query.filter(GmailLabel.deleted_at.is_(None))
    return query.order_by(GmailLabel.name, GmailLabel.id).all()


def set_tracked(db: Session, owner_id: str, external_label_ids: list[str]) -> list[GmailLabel]:
    from app.services.label_tracking_service import release_label

    rows = list_labels(db, owner_id, include_deleted=True)
    selected = set(external_label_ids)
    if selected - {r.external_label_id for r in rows if r.deleted_at is None}:
        raise HTTPException(422, "Unknown or deleted Gmail label id")
    # The whole premise is that a label means the user deliberately filed this
    # thread. A system label means nothing of the kind, and tracking one is not
    # a slightly worse choice - SENT, INBOX or CATEGORY_PROMOTIONS would pull
    # the entire mailbox into email_conversations and mint a recruiter watch per
    # participant, which is the freemail-domain hazard several orders larger.
    system = sorted(r.name for r in rows if r.external_label_id in selected and r.label_type != "user")
    if system:
        raise HTTPException(422, f"Gmail system labels cannot be tracked: {', '.join(system)}")
    for row in rows:
        tracked = row.external_label_id in selected
        if row.is_tracked and not tracked:
            release_label(db, owner_id, row.external_label_id)
        row.is_tracked = tracked
    db.flush()
    return list_labels(db, owner_id)


def resolve_label_ids(db: Session, owner_id: str, names_or_ids: list[str]) -> list[str]:
    return [row.external_label_id for row in db.query(GmailLabel).filter(
        GmailLabel.owner_id == owner_id, GmailLabel.deleted_at.is_(None),
        or_(GmailLabel.external_label_id.in_(names_or_ids), GmailLabel.name.in_(names_or_ids)),
    )]
