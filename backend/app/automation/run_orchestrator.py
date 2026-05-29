from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.ai.resume_context_attribution import RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision

logger = logging.getLogger(__name__)


CandidateItem = Mapping[str, Any]


@dataclass(frozen=True)
class RunOrchestratorDependencies:
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    hard_filter_check: Callable[[dict[str, str | int | bool], UserSettings], tuple[bool, str]]
    compute_blended_ai_score: Callable[
        [str, str, dict[str, str | int | bool], UserSettings, RecruiterEmail | None, ResumeAsset | None],
        tuple[float, str, str, str | None, str | None, Any],
    ]
    policy_f2f_block: Callable[[dict[str, str | int | bool], Mapping[str, Any]], tuple[bool, str]]
    evaluate_routing_policy: Callable[[Session, str, str, str, str, bool], RoutingDecision]
    greeting_from_to_contact: Callable[[str | None, str], str]
    build_user_fallback_draft: Callable[
        [Session, UserSettings, str, str, dict[str, str | int | bool], str, str | None],
        str,
    ]
    generate_reply_with_ai_or_fallback: Callable[..., Any]
    apply_routing_decision: Callable[[RecruiterEmail, RoutingDecision], None]
    capture_premium_numbers: Callable[[Session, RecruiterEmail], None]
    record_productivity_event: Callable[..., Any]
    apply_gmail_label: Callable[[Session, RecruiterEmail, CandidateItem], None]
    mark_message_processed: Callable[[str], None]


@dataclass(frozen=True)
class RunOrchestratorRequest:
    db: Session
    owner_id: str
    items: list[CandidateItem]
    user_settings: UserSettings
    resume: ResumeAsset
    active_resume: ResumeAsset | None
    effective_policy: Mapping[str, Any]
    threshold: float
    dry_run: bool
    model_name: str
    deps: RunOrchestratorDependencies


@dataclass(frozen=True)
class RunOrchestratorResult:
    matched_count: int
    queued_count: int
    skipped_count: int
    failed_count: int
    queued_email_ids: list[int]
    last_email: RecruiterEmail | None
    ai_last_error: str | None
    ai_last_draft_source: str | None
    ai_last_started_at: datetime | None
    ai_last_finished_at: datetime | None
    ai_last_duration_ms: int | None


class RunOrchestrator:
    def execute(self, request: RunOrchestratorRequest) -> RunOrchestratorResult:
        matched_count = len(request.items)
        queued_count = 0
        skipped_count = 0
        failed_count = 0
        queued_email_ids: list[int] = []
        last_email: RecruiterEmail | None = None
        ai_last_error: str | None = None
        ai_last_draft_source: str | None = None
        ai_last_started_at: datetime | None = None
        ai_last_finished_at: datetime | None = None
        ai_last_duration_ms: int | None = None

        for item in request.items:
            external_message_id = str(item["external_message_id"])
            existing = (
                request.db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == request.owner_id)
                .filter(RecruiterEmail.external_message_id == external_message_id)
                .first()
            )
            if existing and existing.state == "approved_sent":
                if not request.dry_run:
                    request.deps.capture_premium_numbers(request.db, existing)
                skipped_count += 1
                last_email = existing
                if not request.dry_run:
                    request.deps.apply_gmail_label(request.db, existing, item)
                    request.db.commit()
                    request.db.refresh(existing)
                    request.deps.mark_message_processed(external_message_id)
                continue

            subject = str(item["subject"])
            body = str(item["body"])
            sender = str(item["sender"])
            snippet = str(item.get("snippet", ""))
            parsed = request.deps.parse_email(subject, body)
            hard_pass, hard_reason = request.deps.hard_filter_check(parsed, request.user_settings)
            ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json, semantic_diag = request.deps.compute_blended_ai_score(
                subject,
                body,
                parsed,
                request.user_settings,
                existing,
                request.active_resume,
                request.db,
                request.owner_id,
                str(item.get("external_thread_id") or ""),
            )
            if (
                request.active_resume
                and resume_embedding_json
                and request.active_resume.semantic_embedding != resume_embedding_json
            ):
                request.active_resume.semantic_embedding = resume_embedding_json

            blocked, block_reason = request.deps.policy_f2f_block(parsed, request.effective_policy)
            if not hard_pass or ai_score < request.threshold or blocked:
                if request.dry_run:
                    skipped_count += 1
                    continue
                email = self._email_row(existing, request, item, parsed)
                def apply_skipped_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.score = int(ai_score * 100)
                    target.ai_score = ai_score
                    target.ai_score_source = ai_score_source
                    target.ai_summary = ai_summary
                    target.semantic_input_source = getattr(semantic_diag, "input_source", None)
                    target.semantic_input_chars = getattr(semantic_diag, "input_chars", None)
                    target.semantic_chunks = getattr(semantic_diag, "chunks", None)
                    target.semantic_fallback_reason = getattr(semantic_diag, "fallback_reason", None)
                    target.keyword_source = getattr(semantic_diag, "keyword_source", None)
                    target.thread_snapshot_used = getattr(semantic_diag, "thread_snapshot_used", None)
                    target.thread_snapshot_email_id = getattr(semantic_diag, "thread_snapshot_email_id", None)
                    target.semantic_embedding = email_embedding_json or target.semantic_embedding
                    target.hard_filter_result = hard_reason
                    target.state = "processed_skipped"
                    target.decision = "Reject"
                    if blocked:
                        target.auto_reject_reason = "f2f_non_texas"
                        target.decision_reason = block_reason
                        target.skip_reason = "f2f_non_texas_blocked"
                    else:
                        target.auto_reject_reason = hard_reason if not hard_pass else "ai_score_too_low"
                        target.decision_reason = "Not qualified for auto-reply"
                        target.skip_reason = "not_qualified"
                    target.last_error = None
                    target.draft_source = None
                    target.draft_model = None
                    target.draft_ai_error = None
                    target.draft_resume_context_status = None

                apply_skipped_state(email)
                email = self._commit_email_phase(
                    request=request,
                    email=email,
                    existing=existing,
                    external_message_id=external_message_id,
                    branch_name="processed_skipped",
                    reapply_state=apply_skipped_state,
                )
                request.db.refresh(email)
                request.deps.capture_premium_numbers(request.db, email)
                request.deps.apply_gmail_label(request.db, email, item)
                request.db.commit()
                request.db.refresh(email)
                request.deps.mark_message_processed(external_message_id)
                skipped_count += 1
                last_email = email
                continue

            routing_decision = request.deps.evaluate_routing_policy(
                request.db,
                sender,
                subject,
                body,
                snippet,
                bool(existing.routing_confirmed) if existing else False,
            )
            if routing_decision.should_mark_failed:
                if request.dry_run:
                    failed_count += 1
                    continue
                email = self._email_row(existing, request, item, parsed)
                def apply_failed_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.state = routing_decision.recommended_state
                    target.decision = "Reject"
                    target.last_error = "Could not resolve recruiter To and employer CC"
                    target.skip_reason = routing_decision.recommended_skip_reason
                    target.decision_reason = "Recipient routing unresolved"
                    request.deps.apply_routing_decision(target, routing_decision)
                    target.routing_confirmed = False
                    target.resume_asset_id = request.resume.id
                    target.resume_file_name = request.resume.file_name

                apply_failed_state(email)
                email = self._commit_email_phase(
                    request=request,
                    email=email,
                    existing=existing,
                    external_message_id=external_message_id,
                    branch_name="failed_mapping",
                    reapply_state=apply_failed_state,
                )
                request.db.refresh(email)
                request.deps.capture_premium_numbers(request.db, email)
                request.deps.record_productivity_event(
                    request.db,
                    event_type="failed_mapping_marked",
                    event_source="state",
                    entity_id=email.id,
                    metadata={"reason": email.skip_reason or "missing_to_or_cc"},
                )
                request.deps.apply_gmail_label(request.db, email, item)
                request.db.commit()
                request.db.refresh(email)
                request.deps.mark_message_processed(external_message_id)
                failed_count += 1
                last_email = email
                continue

            if request.dry_run:
                queued_count += 1
                continue
            greeting_line = request.deps.greeting_from_to_contact(routing_decision.to_email, body)
            fallback_reply = request.deps.build_user_fallback_draft(
                request.db,
                request.user_settings,
                sender,
                str(parsed["role"]),
                parsed,
                greeting_line,
                request.resume.file_name,
            )
            if request.user_settings.feature_ai_enabled:
                ai_last_error = None
                ai_last_started_at = datetime.now(UTC)
                ai_last_finished_at = None
                ai_last_duration_ms = None
                ai_last_draft_source = None
                try:
                    ai_reply = request.deps.generate_reply_with_ai_or_fallback(
                        sender=sender,
                        recruiter_to_email=routing_decision.to_email,
                        greeting_line=greeting_line,
                        subject=subject,
                        body=body,
                        role=str(parsed["role"]),
                        location=str(parsed["location"]),
                        salary_text=str(parsed["salary_text"]),
                        skills_text=str(parsed["skills_text"]),
                        resume_path=request.resume.file_path,
                        resume_file_name=request.resume.file_name,
                        fallback_draft=fallback_reply,
                        model_name=request.model_name,
                    )
                finally:
                    ai_last_finished_at = datetime.now(UTC)
                    ai_last_duration_ms = int((ai_last_finished_at - ai_last_started_at).total_seconds() * 1000)
                reply = ai_reply.draft_text
                draft_source = ai_reply.source
                draft_model = ai_reply.ai_model
                draft_ai_error = ai_reply.ai_error
                draft_resume_context_status = ai_reply.resume_context_status
                ai_last_error = ai_reply.ai_error
                ai_last_draft_source = ai_reply.source
            else:
                reply = fallback_reply
                draft_source = "rules_only"
                draft_model = None
                draft_ai_error = None
                draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY
                ai_last_draft_source = "rules_only"
            email = self._email_row(existing, request, item, parsed)
            def apply_queued_state(target: RecruiterEmail) -> None:
                target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                target.score = int(ai_score * 100)
                target.ai_score = ai_score
                target.ai_score_source = ai_score_source
                target.ai_summary = ai_summary
                target.semantic_input_source = getattr(semantic_diag, "input_source", None)
                target.semantic_input_chars = getattr(semantic_diag, "input_chars", None)
                target.semantic_chunks = getattr(semantic_diag, "chunks", None)
                target.semantic_fallback_reason = getattr(semantic_diag, "fallback_reason", None)
                target.keyword_source = getattr(semantic_diag, "keyword_source", None)
                target.thread_snapshot_used = getattr(semantic_diag, "thread_snapshot_used", None)
                target.thread_snapshot_email_id = getattr(semantic_diag, "thread_snapshot_email_id", None)
                target.semantic_embedding = email_embedding_json or target.semantic_embedding
                target.hard_filter_result = hard_reason
                target.draft_reply = reply
                target.draft_source = draft_source
                target.draft_model = draft_model
                target.draft_ai_error = draft_ai_error
                target.draft_resume_context_status = draft_resume_context_status
                target.last_error = None
                target.state = "needs_review"
                target.decision = "Qualified"
                target.decision_reason = "Qualified and queued for manual approval"
                target.approval_status = "pending"
                target.sent_status = "not_sent"
                target.sent_at = None
                target.gmail_sent_id = None
                request.deps.apply_routing_decision(target, routing_decision)
                target.routing_confirmed = False
                target.resume_asset_id = request.resume.id
                target.resume_file_name = request.resume.file_name
                target.skip_reason = None

            apply_queued_state(email)
            email = self._commit_email_phase(
                request=request,
                email=email,
                existing=existing,
                external_message_id=external_message_id,
                branch_name="needs_review",
                reapply_state=apply_queued_state,
            )
            request.db.refresh(email)
            request.deps.capture_premium_numbers(request.db, email)
            request.deps.record_productivity_event(
                request.db,
                event_type="needs_review_marked",
                event_source="state",
                entity_id=email.id,
                metadata={"source": "automation_run"},
            )
            request.deps.apply_gmail_label(request.db, email, item)
            request.db.commit()
            request.db.refresh(email)
            request.deps.mark_message_processed(external_message_id)
            queued_count += 1
            queued_email_ids.append(email.id)
            last_email = email

        return RunOrchestratorResult(
            matched_count=matched_count,
            queued_count=queued_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            queued_email_ids=queued_email_ids,
            last_email=last_email,
            ai_last_error=ai_last_error,
            ai_last_draft_source=ai_last_draft_source,
            ai_last_started_at=ai_last_started_at,
            ai_last_finished_at=ai_last_finished_at,
            ai_last_duration_ms=ai_last_duration_ms,
        )

    def _email_row(
        self,
        existing: RecruiterEmail | None,
        request: RunOrchestratorRequest,
        item: CandidateItem,
        parsed: dict[str, str | int | bool],
    ) -> RecruiterEmail:
        if existing:
            return existing
        return RecruiterEmail(
            owner_id=request.owner_id,
            sender=str(item["sender"]),
            subject=str(item["subject"]),
            body=str(item["body"]),
            role=str(parsed["role"]),
            location=str(parsed["location"]),
            salary_text=str(parsed["salary_text"]),
            skills_text=str(parsed["skills_text"]),
            source="gmail",
            external_message_id=str(item["external_message_id"]),
            external_thread_id=item.get("external_thread_id"),
            external_rfc_message_id=item.get("external_rfc_message_id"),
            gmail_received_at=item.get("gmail_received_at"),
            recipient_email=item.get("recipient_email"),
        )

    def _commit_email_phase(
        self,
        *,
        request: RunOrchestratorRequest,
        email: RecruiterEmail,
        existing: RecruiterEmail | None,
        external_message_id: str,
        branch_name: str,
        reapply_state: Callable[[RecruiterEmail], None],
    ) -> RecruiterEmail:
        if not existing:
            request.db.add(email)
        try:
            request.db.commit()
            return email
        except IntegrityError as exc:
            if not self._is_external_message_unique_conflict(exc):
                raise
            request.db.rollback()
            recovered = (
                request.db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == request.owner_id)
                .filter(RecruiterEmail.external_message_id == external_message_id)
                .first()
            )
            if not recovered:
                raise
            logger.warning(
                "duplicate_external_message_recovered branch=%s owner_id=%s external_message_id=%s",
                branch_name,
                request.owner_id,
                external_message_id,
            )
            reapply_state(recovered)
            request.db.commit()
            return recovered

    @staticmethod
    def _is_external_message_unique_conflict(exc: IntegrityError) -> bool:
        text = str(exc).lower()
        return "unique constraint failed" in text and "recruiter_emails.external_message_id" in text
