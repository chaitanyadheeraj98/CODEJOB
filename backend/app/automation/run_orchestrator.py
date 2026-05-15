from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session

from app.ai.resume_context_attribution import RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision


CandidateItem = Mapping[str, Any]


@dataclass(frozen=True)
class RunOrchestratorDependencies:
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    hard_filter_check: Callable[[dict[str, str | int | bool], UserSettings], tuple[bool, str]]
    compute_blended_ai_score: Callable[
        [str, str, dict[str, str | int | bool], UserSettings, RecruiterEmail | None, ResumeAsset | None],
        tuple[float, str, str, str | None, str | None],
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
            ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json = request.deps.compute_blended_ai_score(
                subject,
                body,
                parsed,
                request.user_settings,
                existing,
                request.active_resume,
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
                email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
                email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
                email.score = int(ai_score * 100)
                email.ai_score = ai_score
                email.ai_score_source = ai_score_source
                email.ai_summary = ai_summary
                email.semantic_embedding = email_embedding_json or email.semantic_embedding
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
                email.draft_source = None
                email.draft_model = None
                email.draft_ai_error = None
                email.draft_resume_context_status = None
                if not existing:
                    request.db.add(email)
                request.db.commit()
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
                email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
                email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
                email.state = routing_decision.recommended_state
                email.decision = "Reject"
                email.last_error = "Could not resolve recruiter To and employer CC"
                email.skip_reason = routing_decision.recommended_skip_reason
                email.decision_reason = "Recipient routing unresolved"
                request.deps.apply_routing_decision(email, routing_decision)
                email.routing_confirmed = False
                email.resume_asset_id = request.resume.id
                email.resume_file_name = request.resume.file_name
                if not existing:
                    request.db.add(email)
                request.db.commit()
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
            email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
            email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
            email.score = int(ai_score * 100)
            email.ai_score = ai_score
            email.ai_score_source = ai_score_source
            email.ai_summary = ai_summary
            email.semantic_embedding = email_embedding_json or email.semantic_embedding
            email.hard_filter_result = hard_reason
            email.draft_reply = reply
            email.draft_source = draft_source
            email.draft_model = draft_model
            email.draft_ai_error = draft_ai_error
            email.draft_resume_context_status = draft_resume_context_status
            email.last_error = None
            email.state = "needs_review"
            email.decision = "Qualified"
            email.decision_reason = "Qualified and queued for manual approval"
            email.approval_status = "pending"
            email.sent_status = "not_sent"
            email.sent_at = None
            email.gmail_sent_id = None
            request.deps.apply_routing_decision(email, routing_decision)
            email.routing_confirmed = False
            email.resume_asset_id = request.resume.id
            email.resume_file_name = request.resume.file_name
            email.skip_reason = None
            if not existing:
                request.db.add(email)
            request.db.commit()
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
            last_email = email

        return RunOrchestratorResult(
            matched_count=matched_count,
            queued_count=queued_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
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
