from collections.abc import Generator
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base, SessionLocal, engine, ensure_sqlite_phase0_columns
from app.gmail_client import (
    gmail_auth_status,
    is_gmail_configured,
    list_unread_recruiter_candidates,
    mark_message_processed,
    send_reply,
)
from app.models import RecruiterEmail
from app.phase0 import draft_reply, parse_and_classify
from app.schemas import (
    ApproveSendRequest,
    EmailResponse,
    GmailStatusResponse,
    GmailSyncResponse,
    IngestEmailRequest,
)

app = FastAPI(title=settings.app_name)
last_gmail_sync_at: datetime | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_phase0_columns()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.post("/phase0/emails/ingest", response_model=EmailResponse)
def ingest_email(payload: IngestEmailRequest, db: Session = Depends(get_db)) -> RecruiterEmail:
    parsed = parse_and_classify(payload.subject, payload.body)
    reply = draft_reply(payload.sender, str(parsed["role"]), str(parsed["decision"]))

    email = RecruiterEmail(
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        role=str(parsed["role"]),
        location=str(parsed["location"]),
        salary_text=str(parsed["salary_text"]),
        skills_text=str(parsed["skills_text"]),
        score=int(parsed["score"]),
        decision=str(parsed["decision"]),
        draft_reply=reply,
        approval_status="pending",
        sent_status="not_sent",
        source="manual",
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


@app.get("/phase0/gmail/status", response_model=GmailStatusResponse)
def gmail_status() -> GmailStatusResponse:
    configured, authenticated, detail = gmail_auth_status()
    return GmailStatusResponse(
        configured=configured,
        authenticated=authenticated,
        token_path=settings.google_token_path,
        last_sync_at=last_gmail_sync_at,
        detail=detail,
    )


@app.post("/phase0/gmail/sync", response_model=GmailSyncResponse)
def gmail_sync(db: Session = Depends(get_db)) -> GmailSyncResponse:
    global last_gmail_sync_at
    if not is_gmail_configured():
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")

    candidates = list_unread_recruiter_candidates()
    imported_count = 0
    skipped_count = 0

    for item in candidates:
        existing = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.external_message_id == item["external_message_id"])
            .first()
        )
        if existing:
            skipped_count += 1
            continue

        parsed = parse_and_classify(item["subject"], item["body"])
        reply = draft_reply(item["sender"], str(parsed["role"]), str(parsed["decision"]))
        email = RecruiterEmail(
            sender=item["sender"],
            subject=item["subject"],
            body=item["body"],
            role=str(parsed["role"]),
            location=str(parsed["location"]),
            salary_text=str(parsed["salary_text"]),
            skills_text=str(parsed["skills_text"]),
            score=int(parsed["score"]),
            decision=str(parsed["decision"]),
            draft_reply=reply,
            approval_status="pending",
            sent_status="not_sent",
            source="gmail",
            external_message_id=item["external_message_id"],
            external_thread_id=item["external_thread_id"],
            recipient_email=item["recipient_email"],
        )
        db.add(email)
        imported_count += 1

    db.commit()
    last_gmail_sync_at = datetime.utcnow()
    return GmailSyncResponse(imported_count=imported_count, skipped_count=skipped_count)


@app.get("/phase0/emails", response_model=list[EmailResponse])
def list_emails(db: Session = Depends(get_db)) -> list[RecruiterEmail]:
    return db.query(RecruiterEmail).order_by(RecruiterEmail.created_at.desc()).all()


@app.get("/phase0/emails/{email_id}", response_model=EmailResponse)
def get_email(email_id: int, db: Session = Depends(get_db)) -> RecruiterEmail:
    email = db.query(RecruiterEmail).filter(RecruiterEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@app.post("/phase0/emails/{email_id}/approve-send", response_model=EmailResponse)
def approve_and_send(
    email_id: int,
    payload: ApproveSendRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    email = db.query(RecruiterEmail).filter(RecruiterEmail.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    if email.sent_status == "sent":
        raise HTTPException(status_code=400, detail="Email already sent")

    if payload.edited_reply:
        email.draft_reply = payload.edited_reply
    email.last_error = None

    if email.source == "gmail":
        if not email.external_thread_id or not email.recipient_email:
            raise HTTPException(status_code=400, detail="Missing Gmail thread or recipient metadata")
        try:
            send_reply(email.external_thread_id, email.recipient_email, email.subject, email.draft_reply)
            if email.external_message_id:
                mark_message_processed(email.external_message_id)
        except Exception as exc:
            email.last_error = str(exc)
            db.commit()
            db.refresh(email)
            raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    email.approval_status = "approved"
    email.sent_status = "sent"
    email.sent_at = datetime.utcnow()
    db.commit()
    db.refresh(email)
    return email
