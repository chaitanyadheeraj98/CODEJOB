from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.ai.resume_context_attribution import RESUME_CONTEXT_MISSING, RESUME_CONTEXT_RULES_ONLY
from app.automation import RunOrchestrator, RunOrchestratorDependencies, RunOrchestratorRequest
from app.services.policy_service import EffectiveRunInputs
from app.models import DraftEditFeedback, RecipientRoutingFeedback, RecruiterEmail, ResumeAsset, SyncRun, UserSettings
from app.phase0 import RoutingResult
from app.gmail_client import GmailMessageCandidate
from app.routing import RoutingDecision
from app.schemas import ApproveSendRequest, AutomationRunRequest, AutomationRunResponse, GmailSyncResponse, RejectRequest, ResolveRecipientsRequest

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationDeps:
    owner_id: str
    model_name: str
    get_settings: Callable[[Session], UserSettings]
    active_resume: Callable[[Session], ResumeAsset | None]
    effective_run_inputs: Callable[[UserSettings, str | None], EffectiveRunInputs]
    compute_blended_ai_score: Callable[..., tuple[float, str, str, str | None, str | None]]
    analyze_email_routing: Callable[[Session, str, str, str, str], RoutingResult]
    build_user_fallback_draft: Callable[..., str]
    apply_routing_result: Callable[[RecruiterEmail, RoutingResult], None]
    apply_gmail_label_for_email: Callable[..., None]
    log_gmail_labeling_stats: Callable[[], None]
    build_run_response: Callable[..., AutomationRunResponse]
    record_productivity_event: Callable[..., Any]
    policy_threshold: Callable[[UserSettings, Any], float]
    policy_batch_limit: Callable[[Any, int], int]
    policy_dry_run: Callable[[Any], bool]
    policy_f2f_block: Callable[[dict[str, str | int | bool], Any], tuple[bool, str]]
    evaluate_routing_policy: Callable[..., Any]
    apply_routing_decision: Callable[[RecruiterEmail, Any], None]
    capture_premium_numbers: Callable[[Session, RecruiterEmail], None]
    percentile_ms: Callable[[list[float], float], float]
    begin_embedding_latency_capture: Callable[[], None]
    end_embedding_latency_capture: Callable[[], list[float]]
    embedding_latency_log_enabled: Callable[[], bool]
    embedding_provider: Callable[[], str]
    embedding_model: Callable[[], str]
    evaluate_routing_for_email: Callable[[RecruiterEmail], RoutingDecision]
    is_terminal_state: Callable[[RecruiterEmail], bool]
    email_domain: Callable[[str], str]
    telegram_notify: Callable[[str], None]
    build_telegram_digest: Callable[[str, AutomationRunResponse], str]
    set_last_gmail_sync_at: Callable[[datetime], None]
    set_ai_runtime: Callable[[dict[str, Any]], None]
    is_gmail_configured: Callable[[], bool]
    gmail_auth_status: Callable[[], tuple[bool, bool, str]]
    oauth_bootstrap_status: Callable[[], tuple[bool, str | None]]
    list_unread_candidates_by_query: Callable[..., list[GmailMessageCandidate]]
    is_recruiter_like: Callable[[str, str, str], bool]
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    hard_filter_check: Callable[[dict[str, str | int | bool], UserSettings], tuple[bool, str]]
    should_block_f2f: Callable[[dict[str, str | int | bool]], tuple[bool, str | None]]
    greeting_from_to_contact: Callable[[str | None, str], str]
    generate_reply_with_ai_or_fallback: Callable[..., Any]
    send_reply_with_attachment: Callable[..., str]
    send_new_email_with_attachment: Callable[..., str]
    mark_message_processed: Callable[[str], None]
    append_tracking_sheet_row: Callable[..., None]


class OrchestrationService:
    def __init__(self, deps: OrchestrationDeps):
        self.deps = deps

    def sync_gmail(self, db: Session) -> GmailSyncResponse:
        if not self.deps.is_gmail_configured():
            raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")

        user_settings = self.deps.get_settings(db)
        if not user_settings.enabled:
            raise HTTPException(status_code=400, detail="Pipeline is disabled in settings")

        sync_batch_id = str(uuid.uuid4())
        sync_run = SyncRun(owner_id=self.deps.owner_id, sync_batch_id=sync_batch_id, started_at=datetime.now(UTC))
        db.add(sync_run)
        db.commit()

        imported_count = 0
        skipped_count = 0
        error_count = 0
        try:
            resolved = self.deps.effective_run_inputs(user_settings, None)
            effective_query = resolved.effective_query
            candidates = self.deps.list_unread_candidates_by_query(effective_query)
            for item in candidates:
                existing = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id)
                    .filter(RecruiterEmail.external_message_id == item["external_message_id"])
                    .first()
                )
                if existing:
                    self.deps.apply_gmail_label_for_email(email=existing, candidate_item=item)
                    skipped_count += 1
                    continue

                if not self.deps.is_recruiter_like(item["sender"], item["subject"], item["body"]):
                    skipped_count += 1
                    continue

                parsed = self.deps.parse_email(item["subject"], item["body"])
                hard_pass, hard_reason = self.deps.hard_filter_check(parsed, user_settings)
                active_resume = self.deps.active_resume(db)
                ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json = self.deps.compute_blended_ai_score(
                    subject=item["subject"],
                    body=item["body"],
                    parsed=parsed,
                    user_settings=user_settings,
                    email_row=None,
                    resume=active_resume,
                )
                threshold = user_settings.qualification_threshold

                state = "needs_review"
                decision = "Qualified"
                decision_reason = "Qualified by hard filters + AI score"
                auto_reject_reason = None
                draft = ""
                routed: RoutingResult | None = None

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
                    blocked, block_reason = self.deps.should_block_f2f(parsed)
                    if blocked:
                        state = "auto_rejected"
                        decision = "Reject"
                        decision_reason = block_reason
                        auto_reject_reason = "f2f_non_texas"
                    else:
                        routed = self.deps.analyze_email_routing(db, item["sender"], item["subject"], item["body"], item.get("snippet", ""))
                        greeting_line = self.deps.greeting_from_to_contact(routed.to_email, item["body"])
                        draft = self.deps.build_user_fallback_draft(
                            db,
                            user_settings,
                            sender=item["sender"],
                            role=str(parsed["role"]),
                            parsed=parsed,
                            greeting_line=greeting_line,
                            resume_file_name=None,
                        )

                email = RecruiterEmail(
                    owner_id=self.deps.owner_id,
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
                    ai_score_source=ai_score_source,
                    ai_summary=ai_summary,
                    semantic_embedding=email_embedding_json,
                    sync_batch_id=sync_batch_id,
                    draft_reply=draft,
                    draft_source="rules_only" if draft else None,
                    draft_model=self.deps.model_name if draft else None,
                    draft_ai_error=None,
                    draft_resume_context_status=RESUME_CONTEXT_RULES_ONLY if draft else None,
                    approval_status="pending",
                    sent_status="not_sent",
                    source="gmail",
                    external_message_id=item["external_message_id"],
                    external_thread_id=item["external_thread_id"],
                    external_rfc_message_id=item.get("external_rfc_message_id"),
                    gmail_received_at=item.get("gmail_received_at"),
                    recipient_email=item["recipient_email"],
                )
                if routed:
                    self.deps.apply_routing_result(email, routed)
                    email.routing_confirmed = False
                self.deps.apply_gmail_label_for_email(email=email, candidate_item=item)
                if active_resume and resume_embedding_json and active_resume.semantic_embedding != resume_embedding_json:
                    active_resume.semantic_embedding = resume_embedding_json
                db.add(email)
                imported_count += 1

            sync_run.imported_count = imported_count
            sync_run.skipped_count = skipped_count
            sync_run.error_count = error_count
            sync_run.ended_at = datetime.now(UTC)
            db.commit()
        except Exception:
            error_count += 1
            sync_run.error_count = error_count
            sync_run.ended_at = datetime.now(UTC)
            db.commit()
            raise

        self.deps.set_last_gmail_sync_at(datetime.now(UTC))
        self.deps.log_gmail_labeling_stats()
        response = GmailSyncResponse(
            sync_batch_id=sync_batch_id,
            imported_count=imported_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )
        self.deps.telegram_notify(
            "Sync Digest\n"
            f"Batch: {response.sync_batch_id}\n"
            f"Imported: {response.imported_count} | Skipped: {response.skipped_count} | Errors: {response.error_count}"
        )
        return response

    def run_once(self, payload: AutomationRunRequest | None, db: Session) -> AutomationRunResponse:
        if not self.deps.is_gmail_configured():
            raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
        configured, authenticated, detail = self.deps.gmail_auth_status()
        if configured and not authenticated:
            in_progress, last_error = self.deps.oauth_bootstrap_status()
            if in_progress:
                response = AutomationRunResponse(status="oauth_in_progress", detail="OAuth is in progress. Complete sign-in from backend logs, then retry Sync + Queue.")
                self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
                self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
                return response
            if last_error:
                response = AutomationRunResponse(status="oauth_required", detail=f"OAuth required. Trigger Connect Gmail and complete sign-in. Last OAuth error: {last_error}")
                self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
                self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
                return response
            response = AutomationRunResponse(status="oauth_required", detail=f"{detail} Click Connect Gmail, open the auth URL from backend logs, complete sign-in, then retry.")
            self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
            self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
            self.deps.log_gmail_labeling_stats()
            return response

        user_settings = self.deps.get_settings(db)
        resume = self.deps.active_resume(db)
        if not resume:
            raise HTTPException(status_code=400, detail="No active resume uploaded")

        requested_mail_date = payload.mail_date if payload else None
        resolved = self.deps.effective_run_inputs(user_settings, requested_mail_date)
        effective_policy = resolved.policy
        effective_query = resolved.effective_query
        threshold = self.deps.policy_threshold(user_settings, effective_policy)
        batch_limit = self.deps.policy_batch_limit(effective_policy, default_value=20)
        dry_run = self.deps.policy_dry_run(effective_policy)
        items = self.deps.list_unread_candidates_by_query(effective_query, max_results_per_page=batch_limit)[:batch_limit]
        if not items:
            response = self.deps.build_run_response(
                "idle",
                f"No unread matching emails found for query: {effective_query}",
                effective_query=effective_query,
                matched_count=0,
                queued_count=0,
                skipped_count=0,
                failed_count=0,
            )
            self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status, "matched_count": 0, "queued_count": 0, "skipped_count": 0, "failed_count": 0})
            self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
            self.deps.log_gmail_labeling_stats()
            return response

        capture_started = False
        if self.deps.embedding_latency_log_enabled():
            self.deps.begin_embedding_latency_capture()
            capture_started = True

        ai_state = {"ai_running": user_settings.feature_ai_enabled}
        self.deps.set_ai_runtime(ai_state)
        try:
            active_resume = self.deps.active_resume(db)
            run_orchestrator = RunOrchestrator()
            result = run_orchestrator.execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id=self.deps.owner_id,
                    items=items,
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=active_resume,
                    effective_policy=effective_policy,
                    threshold=threshold,
                    dry_run=dry_run,
                    model_name=self.deps.model_name,
                    deps=RunOrchestratorDependencies(
                        parse_email=self.deps.parse_email,
                        hard_filter_check=self.deps.hard_filter_check,
                        compute_blended_ai_score=lambda subject, body, parsed, user_settings, email_row, resume: self.deps.compute_blended_ai_score(
                            subject=subject, body=body, parsed=parsed, user_settings=user_settings, email_row=email_row, resume=resume
                        ),
                        policy_f2f_block=self.deps.policy_f2f_block,
                        evaluate_routing_policy=self.deps.evaluate_routing_policy,
                        greeting_from_to_contact=self.deps.greeting_from_to_contact,
                        build_user_fallback_draft=lambda db, user_settings, sender, role, parsed, greeting_line, resume_file_name: self.deps.build_user_fallback_draft(
                            db, user_settings, sender=sender, role=role, parsed=parsed, greeting_line=greeting_line, resume_file_name=resume_file_name
                        ),
                        generate_reply_with_ai_or_fallback=self.deps.generate_reply_with_ai_or_fallback,
                        apply_routing_decision=self.deps.apply_routing_decision,
                        capture_premium_numbers=self.deps.capture_premium_numbers,
                        record_productivity_event=self.deps.record_productivity_event,
                        apply_gmail_label=lambda _db, email, item: self.deps.apply_gmail_label_for_email(email=email, candidate_item=item),
                        mark_message_processed=self.deps.mark_message_processed,
                    ),
                )
            )
            self.deps.set_ai_runtime(
                {
                    "ai_running": False,
                    "ai_last_error": result.ai_last_error,
                    "ai_last_draft_source": result.ai_last_draft_source,
                    "ai_last_started_at": result.ai_last_started_at,
                    "ai_last_finished_at": result.ai_last_finished_at,
                    "ai_last_duration_ms": result.ai_last_duration_ms,
                }
            )

            retry_promoted_count = 0
            retry_skipped_count = 0
            if user_settings.feature_retry_queue and not dry_run:
                retry_promoted_count, retry_skipped_count = self._retry_failed_queue(db)

            auto_sent_count = 0
            auto_send_failed_count = 0
            if user_settings.feature_auto_send and not dry_run and result.queued_email_ids:
                auto_sent_count, auto_send_failed_count = self._auto_send_newly_queued(
                    result.queued_email_ids, db
                )

            if result.queued_count > 0:
                status = "ready"
                detail = f"Processed {result.matched_count} unread matching emails: queued={result.queued_count}, skipped={result.skipped_count}, failed={result.failed_count}."
            elif result.failed_count > 0:
                status = "failed"
                detail = f"Processed {result.matched_count} unread matching emails: queued=0, skipped={result.skipped_count}, failed={result.failed_count}."
            else:
                status = "skipped"
                detail = f"Processed {result.matched_count} unread matching emails: queued=0, skipped={result.skipped_count}, failed=0."
            if dry_run:
                detail = f"[Dry run] {detail} No database or Gmail label changes were made. (batch_limit={batch_limit}, threshold={threshold:.2f})"
            else:
                automation_notes: list[str] = []
                if user_settings.feature_retry_queue:
                    automation_notes.append(
                        f"retry_promoted={retry_promoted_count}, retry_skipped={retry_skipped_count}"
                    )
                if user_settings.feature_auto_send:
                    automation_notes.append(
                        f"auto_sent={auto_sent_count}, auto_send_failed={auto_send_failed_count}"
                    )
                if automation_notes:
                    detail = f"{detail} Automation: " + "; ".join(automation_notes) + "."

            response = self.deps.build_run_response(
                status,
                detail,
                result.last_email,
                effective_query=effective_query,
                matched_count=result.matched_count,
                queued_count=result.queued_count,
                skipped_count=result.skipped_count,
                failed_count=result.failed_count,
                auto_sent_count=auto_sent_count if user_settings.feature_auto_send else None,
                auto_send_failed_count=auto_send_failed_count if user_settings.feature_auto_send else None,
                retry_promoted_count=retry_promoted_count if user_settings.feature_retry_queue else None,
                retry_skipped_count=retry_skipped_count if user_settings.feature_retry_queue else None,
            )
            self.deps.record_productivity_event(
                db,
                event_type="recent_run_recorded",
                event_source="run_once",
                entity_id=response.email_id,
                metadata={
                    "status": response.status,
                    "matched_count": response.matched_count or 0,
                    "queued_count": response.queued_count or 0,
                    "skipped_count": response.skipped_count or 0,
                    "failed_count": response.failed_count or 0,
                    "retry_promoted_count": retry_promoted_count,
                    "retry_skipped_count": retry_skipped_count,
                    "auto_sent_count": auto_sent_count,
                    "auto_send_failed_count": auto_send_failed_count,
                },
            )
            self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
            return response
        finally:
            if capture_started:
                latency_samples = self.deps.end_embedding_latency_capture()
                if latency_samples:
                    p50_ms = self.deps.percentile_ms(latency_samples, 50.0)
                    p95_ms = self.deps.percentile_ms(latency_samples, 95.0)
                    max_ms = max(latency_samples)
                    provider = self.deps.embedding_provider()
                    model = self.deps.embedding_model()
                    logger.warning(
                        "embedding_latency_summary count=%s p50_ms=%.2f p95_ms=%.2f max_ms=%.2f provider=%s model=%s",
                        len(latency_samples), p50_ms, p95_ms, max_ms, provider, model
                    )

    def _auto_send_newly_queued(self, queued_email_ids: list[int], db: Session) -> tuple[int, int]:
        auto_sent_count = 0
        auto_send_failed_count = 0
        for email_id in queued_email_ids:
            try:
                self.approve_send(email_id, ApproveSendRequest(), db)
                auto_sent_count += 1
            except HTTPException as exc:
                auto_send_failed_count += 1
                logger.warning(
                    "auto_send_skipped email_id=%s status=%s detail=%s",
                    email_id,
                    exc.status_code,
                    exc.detail,
                )
                self.deps.record_productivity_event(
                    db,
                    event_type="auto_send_failed",
                    event_source="automation",
                    entity_id=email_id,
                    metadata={"detail": str(exc.detail)},
                )
            except Exception:
                auto_send_failed_count += 1
                logger.exception("auto_send_crash email_id=%s", email_id)
                self.deps.record_productivity_event(
                    db,
                    event_type="auto_send_failed",
                    event_source="automation",
                    entity_id=email_id,
                    metadata={"detail": "unexpected_auto_send_error"},
                )
        return auto_sent_count, auto_send_failed_count

    def _retry_failed_queue(self, db: Session) -> tuple[int, int]:
        retry_promoted_count = 0
        retry_skipped_count = 0
        failed_items = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id)
            .filter(RecruiterEmail.source == "gmail")
            .filter(RecruiterEmail.state == "failed")
            .filter(RecruiterEmail.routing_confirmed.is_(False))
            .all()
        )
        for email in failed_items:
            routing_decision = self.deps.evaluate_routing_policy(
                db,
                email.sender,
                email.subject,
                email.body,
                "",
                email.routing_confirmed,
            )
            self.deps.apply_routing_decision(email, routing_decision)
            if routing_decision.is_sendable_candidate and not routing_decision.should_mark_failed:
                email.state = "needs_review"
                email.last_error = None
                email.skip_reason = None
                email.decision = "Qualified"
                email.decision_reason = "Recovered by retry queue routing refresh"
                email.approval_status = "pending"
                email.sent_status = "not_sent"
                retry_promoted_count += 1
                self.deps.record_productivity_event(
                    db,
                    event_type="needs_review_marked",
                    event_source="state",
                    entity_id=email.id,
                    metadata={"source": "retry_queue"},
                )
            else:
                retry_skipped_count += 1
        if failed_items:
            db.commit()
        return retry_promoted_count, retry_skipped_count

    def approve_send(self, email_id: int, payload: ApproveSendRequest, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if self.deps.is_terminal_state(email):
            raise HTTPException(status_code=400, detail="Candidate is in terminal state")
        if email.state != "needs_review":
            raise HTTPException(status_code=400, detail="Only needs_review candidates can be approved")

        original_draft = email.draft_reply
        if payload.edited_reply:
            email.draft_reply = payload.edited_reply

        email.last_error = None
        sent_message_id = None
        if not email.recipient_email:
            raise HTTPException(status_code=400, detail="Recipient email is required before sending")
        if not email.cc_email:
            raise HTTPException(status_code=400, detail="CC email is required before sending")
        routing_decision = self.deps.evaluate_routing_for_email(email)
        if not routing_decision.is_sendable_candidate:
            detail = (
                f"{routing_decision.reason} "
                f"(status={routing_decision.status}, confidence={routing_decision.confidence:.2f})"
            ).strip()
            raise HTTPException(status_code=400, detail=f"Recipient routing is not safe to send: {detail}")
        if not email.draft_reply.strip():
            raise HTTPException(status_code=400, detail="Draft email body is required before sending")
        resume = self.deps.active_resume(db)
        if not resume:
            raise HTTPException(status_code=400, detail="No active resume uploaded")
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name

        if email.source == "gmail":
            if not email.external_thread_id:
                raise HTTPException(status_code=400, detail="Missing Gmail metadata")
            try:
                sent_message_id = self.deps.send_reply_with_attachment(
                    email.external_thread_id,
                    email.recipient_email,
                    email.cc_email,
                    email.subject,
                    email.draft_reply,
                    resume.file_path,
                    resume.file_name,
                )
                if email.external_message_id:
                    self.deps.mark_message_processed(email.external_message_id)
            except Exception as exc:
                email.last_error = str(exc)
                db.commit()
                db.refresh(email)
                raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc
        elif email.source == "nvoids":
            try:
                sent_message_id = self.deps.send_new_email_with_attachment(
                    email.recipient_email,
                    email.cc_email,
                    email.subject,
                    email.draft_reply,
                    resume.file_path,
                    resume.file_name,
                )
            except Exception as exc:
                email.last_error = str(exc)
                db.commit()
                db.refresh(email)
                raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

        email.state = "approved_sent"
        email.decision = "Qualified"
        email.approval_status = "approved"
        email.sent_status = "sent"
        email.sent_at = datetime.now(UTC)
        email.gmail_sent_id = sent_message_id
        if payload.edited_reply and payload.edited_reply.strip() != original_draft.strip():
            db.add(DraftEditFeedback(owner_id=self.deps.owner_id, recruiter_email_id=email.id, original_draft=original_draft, edited_draft=payload.edited_reply))
        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(db, event_type="approved_sent", event_source="action", entity_id=email.id, metadata={"state": email.state, "sent_status": email.sent_status})

        try:
            logger.info("Appending Google Sheets tracking row for approved email_id=%s", email.id)
            self.deps.append_tracking_sheet_row(
                role=email.role,
                sender=email.sender,
                subject=email.subject,
                body=email.body,
                to_email=email.recipient_email,
                cc_email=email.cc_email,
            )
            logger.info("Google Sheets tracking row appended for email_id=%s", email.id)
        except Exception as exc:
            warning = f"Sheets tracking warning: {exc}"
            logger.warning("Failed to append Google Sheets tracking row for email_id=%s: %s", email.id, exc)
            email.last_error = warning[:2000]
            db.commit()
            db.refresh(email)

        return email

    def reject_candidate(self, email_id: int, payload: RejectRequest, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if self.deps.is_terminal_state(email):
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

    def send_to_failed_mapping(self, email_id: int, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if self.deps.is_terminal_state(email):
            raise HTTPException(status_code=400, detail="Candidate is in terminal state")
        if email.state != "needs_review":
            raise HTTPException(status_code=400, detail="Only needs_review candidates can be moved to failed mapping")

        email.state = "failed"
        email.routing_confirmed = False
        email.routing_status = "ambiguous"
        email.routing_confidence = min(float(email.routing_confidence or 0.0), 0.5)
        email.routing_reason = "Manually moved to failed mapping for recipient remap."
        email.last_error = "Recipient mapping flagged for manual remap"
        email.skip_reason = "manual_failed_mapping"
        email.decision_reason = "Moved to failed mapping by user"
        email.approval_status = "pending"
        email.sent_status = "not_sent"
        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(db, event_type="failed_mapping_marked", event_source="action", entity_id=email.id, metadata={"source": "manual_move_to_failed_mapping"})
        return email

    def resolve_recipients(self, email_id: int, payload: ResolveRecipientsRequest, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")

        to_email = payload.to_email.strip()
        cc_email = payload.cc_email.strip()
        email.recipient_email = to_email
        email.cc_email = cc_email
        email.routing_status = "confirmed"
        email.routing_confidence = 1.0
        email.routing_reason = "Recipient routing manually confirmed."
        email.routing_evidence = json.dumps([
            {"role": "to", "email": to_email, "source": "manual_edit", "detail": "Confirmed by user"},
            {"role": "cc", "email": cc_email, "source": "manual_edit", "detail": "Confirmed by user"},
        ])
        email.routing_candidates = email.routing_evidence
        email.routing_confirmed = True
        email.state = "needs_review"
        email.last_error = None
        email.skip_reason = None
        email.decision_reason = "Recipient routing corrected by user"

        parsed = self.deps.parse_email(email.subject, email.body)
        role = str(parsed["role"])
        greeting_line = self.deps.greeting_from_to_contact(to_email, email.body)
        user_settings = self.deps.get_settings(db)
        resume = self.deps.active_resume(db)
        fallback_reply = self.deps.build_user_fallback_draft(
            db,
            user_settings,
            sender=email.sender,
            role=role,
            parsed=parsed,
            greeting_line=greeting_line,
            resume_file_name=resume.file_name if resume else email.resume_file_name,
        )
        reply = fallback_reply
        draft_source = "rules_only"
        draft_model = None
        draft_ai_error = None
        draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY
        if resume:
            email.resume_asset_id = resume.id
            email.resume_file_name = resume.file_name

        if user_settings.feature_ai_enabled:
            if resume:
                ai_reply = self.deps.generate_reply_with_ai_or_fallback(
                    sender=email.sender,
                    recruiter_to_email=to_email,
                    greeting_line=greeting_line,
                    subject=email.subject,
                    body=email.body,
                    role=role,
                    location=str(parsed["location"]),
                    salary_text=str(parsed["salary_text"]),
                    skills_text=str(parsed["skills_text"]),
                    resume_path=resume.file_path,
                    resume_file_name=resume.file_name,
                    fallback_draft=fallback_reply,
                    model_name=self.deps.model_name,
                )
                reply = ai_reply.draft_text
                draft_source = ai_reply.source
                draft_model = ai_reply.ai_model
                draft_ai_error = ai_reply.ai_error
                draft_resume_context_status = ai_reply.resume_context_status
            else:
                draft_ai_error = "AI enabled but no active resume uploaded; generated rules-only fallback draft."
                draft_resume_context_status = RESUME_CONTEXT_MISSING

        email.role = role
        email.location = str(parsed["location"])
        email.salary_text = str(parsed["salary_text"])
        email.skills_text = str(parsed["skills_text"])
        email.draft_reply = reply
        email.draft_source = draft_source
        email.draft_model = draft_model
        email.draft_ai_error = draft_ai_error
        email.draft_resume_context_status = draft_resume_context_status

        sender_domain = self.deps.email_domain(email.sender)
        body_lower = (email.body or "").lower()
        if sender_domain:
            db.add(
                RecipientRoutingFeedback(
                    owner_id=self.deps.owner_id,
                    sender_domain=sender_domain,
                    corrected_to=to_email,
                    corrected_cc=cc_email,
                    sample_sender=email.sender,
                    evidence_to_present=to_email.lower() in body_lower,
                    evidence_cc_present=cc_email.lower() in body_lower or cc_email.lower() in email.sender.lower(),
                    sample_body=email.body[:5000] if email.body else None,
                )
            )

        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(db, event_type="needs_review_marked", event_source="state", entity_id=email.id, metadata={"source": "resolve_recipients"})
        return email
