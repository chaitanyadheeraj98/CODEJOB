import hashlib
import uuid
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base, SessionLocal, engine, ensure_sqlite_phase0_columns
from app.gmail_client import (
    gmail_auth_status,
    is_gmail_configured,
    list_unread_candidates_by_query,
    mark_message_processed,
    send_reply_with_attachment,
)
from app.models import DraftEditFeedback, RecruiterEmail, ResumeAsset, SyncRun, UserSettings
from app.models import RecipientRoutingFeedback
from app.phase0 import (
    ai_assist_score,
    draft_reply,
    hard_filter_check,
    is_recruiter_like,
    parse_email,
    resolve_to_cc,
    should_block_f2f,
)
from app.schemas import (
    ApproveSendRequest,
    AutomationRunResponse,
    BulkRejectRequest,
    CandidateListResponse,
    EmailResponse,
    GmailStatusResponse,
    GmailSyncResponse,
    IngestEmailRequest,
    RejectRequest,
    ResolveRecipientsRequest,
    ResumeResponse,
    SettingsRequest,
    SettingsResponse,
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
    _ensure_default_settings()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_default_settings() -> None:
    db = SessionLocal()
    try:
        existing = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
        if existing:
            return
        default_settings = UserSettings(
            owner_id=settings.owner_id,
            enabled=True,
            gmail_query="is:unread in:inbox recruiter",
            qualification_threshold=settings.qualification_threshold,
            feature_auto_polling=settings.feature_auto_polling,
            feature_auto_send=settings.feature_auto_send,
            feature_retry_queue=settings.feature_retry_queue,
        )
        db.add(default_settings)
        db.commit()
    finally:
        db.close()


def _get_settings(db: Session) -> UserSettings:
    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
    if not user_settings:
        raise HTTPException(status_code=500, detail="Settings not initialized")
    return user_settings


def _to_csv(values: list[str]) -> str:
    return ",".join(v.strip() for v in values if v.strip())


def _active_resume(db: Session) -> ResumeAsset | None:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .order_by(ResumeAsset.version.desc())
        .first()
    )


def _is_terminal_state(email: RecruiterEmail) -> bool:
    return email.state in {"approved_sent", "rejected", "auto_rejected"}


def _email_domain(address: str) -> str:
    parts = address.split("@")
    return parts[1].lower() if len(parts) == 2 else ""


def _learned_recipient_override(db: Session, sender: str) -> tuple[str, str] | None:
    sender_domain = _email_domain(sender)
    if not sender_domain:
        return None
    feedback = (
        db.query(RecipientRoutingFeedback)
        .filter(
            RecipientRoutingFeedback.owner_id == settings.owner_id,
            RecipientRoutingFeedback.sender_domain == sender_domain,
        )
        .order_by(RecipientRoutingFeedback.id.desc())
        .first()
    )
    if not feedback:
        return None
    return feedback.corrected_to, feedback.corrected_cc


def _learned_greeting(db: Session) -> str:
    latest = (
        db.query(DraftEditFeedback)
        .filter(DraftEditFeedback.owner_id == settings.owner_id)
        .order_by(DraftEditFeedback.id.desc())
        .first()
    )
    if not latest:
        return "Hi,"
    for line in latest.edited_draft.splitlines():
        if line.strip():
            if line.strip().lower().startswith("hi"):
                return line.strip()
            break
    return "Hi,"


def _apply_draft_learning(db: Session, draft: str) -> str:
    greeting = _learned_greeting(db)
    lines = draft.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("hi"):
            lines[i] = greeting
            return "\n".join(lines)
    if lines:
        return "\n".join([lines[0], "", greeting, *lines[1:]])
    return greeting


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    return SettingsResponse(
        enabled=s.enabled,
        gmail_query=s.gmail_query,
        min_salary=s.min_salary,
        accepted_locations=[v for v in s.accepted_locations.split(",") if v],
        visa_required_allowed=s.visa_required_allowed,
        remote_preference=s.remote_preference,
        role_keywords=[v for v in s.role_keywords.split(",") if v],
        must_have_skills=[v for v in s.must_have_skills.split(",") if v],
        free_text_guidance=s.free_text_guidance,
        qualification_threshold=s.qualification_threshold,
        feature_auto_polling=s.feature_auto_polling,
        feature_auto_send=s.feature_auto_send,
        feature_retry_queue=s.feature_retry_queue,
        owner_id=s.owner_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@app.put("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsRequest, db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    s.enabled = payload.enabled
    s.gmail_query = payload.gmail_query
    s.min_salary = payload.min_salary
    s.accepted_locations = _to_csv(payload.accepted_locations)
    s.visa_required_allowed = payload.visa_required_allowed
    s.remote_preference = payload.remote_preference
    s.role_keywords = _to_csv(payload.role_keywords)
    s.must_have_skills = _to_csv(payload.must_have_skills)
    s.free_text_guidance = payload.free_text_guidance
    s.qualification_threshold = payload.qualification_threshold
    s.feature_auto_polling = payload.feature_auto_polling
    s.feature_auto_send = payload.feature_auto_send
    s.feature_retry_queue = payload.feature_retry_queue
    db.commit()
    db.refresh(s)
    return get_settings(db)


@app.post("/settings/resume", response_model=ResumeResponse)
def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ResumeResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name required")
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file not allowed")

    sha256 = hashlib.sha256(content).hexdigest()
    Path(settings.resume_storage_dir).mkdir(parents=True, exist_ok=True)
    target_path = Path(settings.resume_storage_dir) / f"{sha256}_{file.filename}"
    target_path.write_bytes(content)

    current = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .all()
    )
    for item in current:
        item.is_current = False

    last_version = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id)
        .order_by(ResumeAsset.version.desc())
        .first()
    )
    next_version = 1 if not last_version else last_version.version + 1

    resume = ResumeAsset(
        owner_id=settings.owner_id,
        file_path=str(target_path),
        file_name=file.filename,
        mime_type=file.content_type or "application/pdf",
        sha256=sha256,
        version=next_version,
        is_current=True,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


@app.get("/settings/resumes", response_model=list[ResumeResponse])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeAsset]:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id)
        .order_by(ResumeAsset.version.desc())
        .all()
    )


@app.get("/gmail/status", response_model=GmailStatusResponse)
def gmail_status() -> GmailStatusResponse:
    configured, authenticated, detail = gmail_auth_status()
    return GmailStatusResponse(
        configured=configured,
        authenticated=authenticated,
        token_path=settings.google_token_path,
        last_sync_at=last_gmail_sync_at,
        detail=detail,
    )


@app.post("/gmail/sync", response_model=GmailSyncResponse)
def gmail_sync(db: Session = Depends(get_db)) -> GmailSyncResponse:
    global last_gmail_sync_at
    if not is_gmail_configured():
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")

    user_settings = _get_settings(db)
    if not user_settings.enabled:
        raise HTTPException(status_code=400, detail="Pipeline is disabled in settings")

    sync_batch_id = str(uuid.uuid4())
    sync_run = SyncRun(owner_id=settings.owner_id, sync_batch_id=sync_batch_id, started_at=datetime.utcnow())
    db.add(sync_run)
    db.commit()

    imported_count = 0
    skipped_count = 0
    error_count = 0
    try:
        candidates = list_unread_candidates_by_query(user_settings.gmail_query)
        for item in candidates:
            existing = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id)
                .filter(RecruiterEmail.external_message_id == item["external_message_id"])
                .first()
            )
            if existing:
                skipped_count += 1
                continue

            if not is_recruiter_like(item["sender"], item["subject"], item["body"]):
                skipped_count += 1
                continue

            parsed = parse_email(item["subject"], item["body"])
            hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
            ai_score, ai_summary = ai_assist_score(parsed, user_settings)
            threshold = user_settings.qualification_threshold

            state = "needs_review"
            decision = "Qualified"
            decision_reason = "Qualified by hard filters + AI score"
            auto_reject_reason = None
            draft = ""

            if not hard_pass:
                state = "auto_rejected"
                decision = "Reject"
                decision_reason = "Hard filters failed"
                auto_reject_reason = hard_reason
            elif ai_score < threshold:
                state = "auto_rejected"
                decision = "Reject"
                decision_reason = f"AI score below threshold ({threshold:.2f})"
                auto_reject_reason = "ai_score_too_low"
            else:
                blocked, block_reason = should_block_f2f(parsed)
                if blocked:
                    state = "auto_rejected"
                    decision = "Reject"
                    decision_reason = block_reason
                    auto_reject_reason = "f2f_non_texas"
                    draft = ""
                else:
                    draft = _apply_draft_learning(db, draft_reply(item["sender"], str(parsed["role"]), parsed))

            email = RecruiterEmail(
                owner_id=settings.owner_id,
                sender=item["sender"],
                subject=item["subject"],
                body=item["body"],
                role=str(parsed["role"]),
                location=str(parsed["location"]),
                salary_text=str(parsed["salary_text"]),
                skills_text=str(parsed["skills_text"]),
                score=int(ai_score * 100),
                decision=decision,
                state=state,
                decision_reason=decision_reason,
                hard_filter_result=hard_reason,
                auto_reject_reason=auto_reject_reason,
                ai_score=ai_score,
                ai_score_source="v1_rules_plus_ai",
                ai_summary=ai_summary,
                sync_batch_id=sync_batch_id,
                draft_reply=draft,
                approval_status="pending",
                sent_status="not_sent",
                source="gmail",
                external_message_id=item["external_message_id"],
                external_thread_id=item["external_thread_id"],
                recipient_email=item["recipient_email"],
            )
            db.add(email)
            imported_count += 1

        sync_run.imported_count = imported_count
        sync_run.skipped_count = skipped_count
        sync_run.error_count = error_count
        sync_run.ended_at = datetime.utcnow()
        db.commit()
    except Exception:
        error_count += 1
        sync_run.error_count = error_count
        sync_run.ended_at = datetime.utcnow()
        db.commit()
        raise

    last_gmail_sync_at = datetime.utcnow()
    return GmailSyncResponse(
        sync_batch_id=sync_batch_id,
        imported_count=imported_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )


@app.post("/automation/run-once", response_model=AutomationRunResponse)
def automation_run_once(db: Session = Depends(get_db)) -> AutomationRunResponse:
    if not is_gmail_configured():
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
    user_settings = _get_settings(db)
    resume = _active_resume(db)
    if not resume:
        raise HTTPException(status_code=400, detail="No active resume uploaded")

    effective_query = f"{user_settings.gmail_query} is:unread tx"
    items = list_unread_candidates_by_query(effective_query, max_results_per_page=20)
    if not items:
        return AutomationRunResponse(status="idle", detail="No unread matching emails found")

    item = items[0]
    existing = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id)
        .filter(RecruiterEmail.external_message_id == item["external_message_id"])
        .first()
    )
    if existing and existing.state == "approved_sent":
        return AutomationRunResponse(status="skipped", detail="Email already processed and sent", email_id=existing.id)

    parsed = parse_email(item["subject"], item["body"])
    hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
    ai_score, ai_summary = ai_assist_score(parsed, user_settings)
    threshold = user_settings.qualification_threshold

    blocked, block_reason = should_block_f2f(parsed)
    if not hard_pass or ai_score < threshold or blocked:
        email = existing or RecruiterEmail(
            owner_id=settings.owner_id,
            sender=item["sender"],
            subject=item["subject"],
            body=item["body"],
            role=str(parsed["role"]),
            location=str(parsed["location"]),
            salary_text=str(parsed["salary_text"]),
            skills_text=str(parsed["skills_text"]),
            source="gmail",
            external_message_id=item["external_message_id"],
            external_thread_id=item["external_thread_id"],
            recipient_email=item["recipient_email"],
        )
        email.score = int(ai_score * 100)
        email.ai_score = ai_score
        email.ai_score_source = "v1_rules_plus_ai"
        email.ai_summary = ai_summary
        email.hard_filter_result = hard_reason
        email.state = "processed_skipped"
        email.decision = "Reject"
        if blocked:
            email.auto_reject_reason = "f2f_non_texas"
            email.decision_reason = block_reason
            email.skip_reason = "f2f_non_texas_blocked"
        else:
            email.auto_reject_reason = hard_reason if not hard_pass else "ai_score_too_low"
            email.decision_reason = "Not qualified for auto-reply"
            email.skip_reason = "not_qualified"
        email.last_error = None
        if not existing:
            db.add(email)
        db.commit()
        db.refresh(email)
        mark_message_processed(item["external_message_id"])
        return AutomationRunResponse(status="skipped", detail="Email not qualified; skipped", email_id=email.id)

    recruiter_email, employer_email = resolve_to_cc(
        item["sender"],
        item["subject"],
        item["body"],
        item.get("snippet", ""),
    )
    learned = _learned_recipient_override(db, item["sender"])
    if learned:
        recruiter_email, employer_email = learned
    if not recruiter_email or not employer_email:
        email = existing or RecruiterEmail(
            owner_id=settings.owner_id,
            sender=item["sender"],
            subject=item["subject"],
            body=item["body"],
            role=str(parsed["role"]),
            location=str(parsed["location"]),
            salary_text=str(parsed["salary_text"]),
            skills_text=str(parsed["skills_text"]),
            source="gmail",
            external_message_id=item["external_message_id"],
            external_thread_id=item["external_thread_id"],
            recipient_email=item["recipient_email"],
        )
        email.state = "failed"
        email.decision = "Reject"
        email.last_error = "Could not resolve recruiter To and employer CC"
        email.skip_reason = "missing_to_or_cc"
        email.decision_reason = "Recipient routing unresolved"
        email.recipient_email = recruiter_email
        email.cc_email = employer_email
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name
        if not existing:
            db.add(email)
        db.commit()
        db.refresh(email)
        mark_message_processed(item["external_message_id"])
        return AutomationRunResponse(status="failed", detail=email.last_error, email_id=email.id)

    # Manual approval gate: queue only, never auto-send from run-once.
    reply = _apply_draft_learning(db, draft_reply(item["sender"], str(parsed["role"]), parsed))
    email = existing or RecruiterEmail(
        owner_id=settings.owner_id,
        sender=item["sender"],
        subject=item["subject"],
        body=item["body"],
        role=str(parsed["role"]),
        location=str(parsed["location"]),
        salary_text=str(parsed["salary_text"]),
        skills_text=str(parsed["skills_text"]),
        source="gmail",
        external_message_id=item["external_message_id"],
        external_thread_id=item["external_thread_id"],
        recipient_email=item["recipient_email"],
    )
    email.score = int(ai_score * 100)
    email.ai_score = ai_score
    email.ai_score_source = "v1_rules_plus_ai"
    email.ai_summary = ai_summary
    email.hard_filter_result = hard_reason
    email.draft_reply = reply
    email.last_error = None
    email.state = "needs_review"
    email.decision = "Qualified"
    email.decision_reason = "Qualified and queued for manual approval"
    email.approval_status = "pending"
    email.sent_status = "not_sent"
    email.sent_at = None
    email.gmail_sent_id = None
    email.recipient_email = recruiter_email
    email.cc_email = employer_email
    email.resume_asset_id = resume.id
    email.resume_file_name = resume.file_name
    email.skip_reason = None
    if not existing:
        db.add(email)
    db.commit()
    db.refresh(email)
    mark_message_processed(item["external_message_id"])
    return AutomationRunResponse(status="queued", detail="Email qualified and queued for approval", email_id=email.id)


@app.post("/phase0/emails/ingest", response_model=EmailResponse)
def ingest_email(payload: IngestEmailRequest, db: Session = Depends(get_db)) -> RecruiterEmail:
    user_settings = _get_settings(db)
    parsed = parse_email(payload.subject, payload.body)
    hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
    ai_score, ai_summary = ai_assist_score(parsed, user_settings)
    threshold = user_settings.qualification_threshold
    state = "needs_review" if hard_pass and ai_score >= threshold else "auto_rejected"
    decision = "Qualified" if state == "needs_review" else "Reject"

    email = RecruiterEmail(
        owner_id=settings.owner_id,
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        role=str(parsed["role"]),
        location=str(parsed["location"]),
        salary_text=str(parsed["salary_text"]),
        skills_text=str(parsed["skills_text"]),
        score=int(ai_score * 100),
        decision=decision,
        state=state,
        decision_reason="manual_ingest",
        hard_filter_result=hard_reason,
        auto_reject_reason=None if state == "needs_review" else "manual_ingest_not_qualified",
        ai_score=ai_score,
        ai_score_source="v1_rules_plus_ai",
        ai_summary=ai_summary,
        draft_reply=_apply_draft_learning(db, draft_reply(payload.sender, str(parsed["role"]), parsed))
        if state == "needs_review"
        else "",
        approval_status="pending",
        sent_status="not_sent",
        source="manual",
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


@app.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    state: str = Query("needs_review"),
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("newest"),
    db: Session = Depends(get_db),
) -> CandidateListResponse:
    states = [s.strip() for s in state.split(",") if s.strip()]
    query = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id)
    if states:
        query = query.filter(or_(*[RecruiterEmail.state == s for s in states]))

    if sort == "highest_score":
        query = query.order_by(RecruiterEmail.score.desc(), RecruiterEmail.created_at.desc())
    else:
        query = query.order_by(RecruiterEmail.created_at.desc())

    items = query.offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    next_cursor = cursor + limit if has_next else None
    return CandidateListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.get("/candidates/{email_id}", response_model=EmailResponse)
def get_candidate(email_id: int, db: Session = Depends(get_db)) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return email


@app.post("/candidates/{email_id}/approve-send", response_model=EmailResponse)
def approve_and_send(
    email_id: int,
    payload: ApproveSendRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if _is_terminal_state(email):
        raise HTTPException(status_code=400, detail="Candidate is in terminal state")
    if email.state != "needs_review":
        raise HTTPException(status_code=400, detail="Only needs_review candidates can be approved")

    original_draft = email.draft_reply
    if payload.edited_reply:
        email.draft_reply = payload.edited_reply

    email.last_error = None
    sent_message_id = None
    if email.source == "gmail":
        if not email.external_thread_id or not email.recipient_email:
            raise HTTPException(status_code=400, detail="Missing Gmail metadata")
        if not email.cc_email:
            raise HTTPException(status_code=400, detail="CC email is required before sending")
        if not email.draft_reply.strip():
            raise HTTPException(status_code=400, detail="Draft email body is required before sending")
        resume = _active_resume(db)
        if not resume:
            raise HTTPException(status_code=400, detail="No active resume uploaded")
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name
        try:
            sent_message_id = send_reply_with_attachment(
                email.external_thread_id,
                email.recipient_email,
                email.cc_email,
                email.subject,
                email.draft_reply,
                resume.file_path,
            )
            if email.external_message_id:
                mark_message_processed(email.external_message_id)
        except Exception as exc:
            email.last_error = str(exc)
            db.commit()
            db.refresh(email)
            raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    email.state = "approved_sent"
    email.decision = "Qualified"
    email.approval_status = "approved"
    email.sent_status = "sent"
    email.sent_at = datetime.utcnow()
    email.gmail_sent_id = sent_message_id
    if payload.edited_reply and payload.edited_reply.strip() != original_draft.strip():
        db.add(
            DraftEditFeedback(
                owner_id=settings.owner_id,
                recruiter_email_id=email.id,
                original_draft=original_draft,
                edited_draft=payload.edited_reply,
            )
        )
    db.commit()
    db.refresh(email)
    return email


@app.post("/candidates/{email_id}/reject", response_model=EmailResponse)
def reject_candidate(
    email_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if _is_terminal_state(email):
        raise HTTPException(status_code=400, detail="Candidate is in terminal state")
    if email.state != "needs_review":
        raise HTTPException(status_code=400, detail="Only needs_review candidates can be rejected")

    email.state = "rejected"
    email.decision = "Reject"
    email.decision_reason = payload.reason or "Rejected by user"
    email.approval_status = "rejected"
    email.sent_status = "not_sent"
    db.commit()
    db.refresh(email)
    return email


@app.post("/candidates/reject-bulk")
def reject_bulk(payload: BulkRejectRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    if not payload.ids:
        return {"rejected_count": 0}

    rows = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id)
        .filter(RecruiterEmail.id.in_(payload.ids))
        .all()
    )
    rejected = 0
    for row in rows:
        if row.state == "needs_review":
            row.state = "rejected"
            row.decision = "Reject"
            row.decision_reason = payload.reason or "Bulk rejected by user"
            row.approval_status = "rejected"
            row.sent_status = "not_sent"
            rejected += 1
    db.commit()
    return {"rejected_count": rejected}


@app.post("/candidates/{email_id}/resolve-recipients", response_model=EmailResponse)
def resolve_recipients(
    email_id: int,
    payload: ResolveRecipientsRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")

    email.recipient_email = payload.to_email.strip()
    email.cc_email = payload.cc_email.strip()
    email.state = "needs_review"
    email.last_error = None
    email.skip_reason = None
    email.decision_reason = "Recipient routing corrected by user"

    sender_domain = _email_domain(email.sender)
    if sender_domain:
        db.add(
            RecipientRoutingFeedback(
                owner_id=settings.owner_id,
                sender_domain=sender_domain,
                corrected_to=email.recipient_email,
                corrected_cc=email.cc_email,
                sample_body=email.body[:5000] if email.body else None,
            )
        )

    db.commit()
    db.refresh(email)
    return email
