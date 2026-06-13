from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Callable, Mapping

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision
from .queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)

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
    select_best_resume_match: Callable[..., Any]
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
    enabled_resumes: list[ResumeAsset]
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
            parsed_for_selection = request.deps.parse_email(subject, body)
            resume_selection = request.deps.select_best_resume_match(
                subject=subject,
                body=body,
                parsed=parsed_for_selection,
                user_settings=request.user_settings,
                email_row=existing,
                resumes=request.enabled_resumes,
                fallback_resume=request.active_resume,
                db=request.db,
                owner_id=request.owner_id,
                external_thread_id=str(item.get("external_thread_id") or ""),
            )
            selected_resume = getattr(resume_selection, "resume", None) or request.active_resume or request.resume
            if request.user_settings.feature_ai_enabled:
                ai_last_error = None
                ai_last_started_at = datetime.now(UTC)
                ai_last_finished_at = None
                ai_last_duration_ms = None
                ai_last_draft_source = None
            try:
                preparation = prepare_candidate_for_queue(
                    QueuePreparationRequest(
                        db=request.db,
                        owner_id=request.owner_id,
                        sender=sender,
                        subject=subject,
                        body=body,
                        snippet=snippet,
                        user_settings=request.user_settings,
                        effective_policy=request.effective_policy,
                        threshold=request.threshold,
                        model_name=request.model_name,
                        scoring_resume=selected_resume,
                        draft_resume=selected_resume,
                        existing_email=existing,
                        external_thread_id=str(item.get("external_thread_id") or ""),
                    ),
                    QueuePreparationDependencies(
                        parse_email=request.deps.parse_email,
                        hard_filter_check=request.deps.hard_filter_check,
                        compute_blended_ai_score=request.deps.compute_blended_ai_score,
                        policy_f2f_block=request.deps.policy_f2f_block,
                        evaluate_routing_policy=request.deps.evaluate_routing_policy,
                        greeting_from_to_contact=request.deps.greeting_from_to_contact,
                        build_user_fallback_draft=request.deps.build_user_fallback_draft,
                        generate_reply_with_ai_or_fallback=request.deps.generate_reply_with_ai_or_fallback,
                    ),
                )
            finally:
                if request.user_settings.feature_ai_enabled and ai_last_started_at is not None:
                    ai_last_finished_at = datetime.now(UTC)
                    ai_last_duration_ms = int((ai_last_finished_at - ai_last_started_at).total_seconds() * 1000)
            parsed = preparation.parsed
            if (
                selected_resume
                and preparation.resume_embedding_json
                and selected_resume.semantic_embedding != preparation.resume_embedding_json
            ):
                selected_resume.semantic_embedding = preparation.resume_embedding_json

            if preparation.outcome == "not_qualified":
                if request.dry_run:
                    skipped_count += 1
                    continue
                email = self._email_row(existing, request, item, parsed)
                def apply_skipped_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.score = int(preparation.ai_score * 100)
                    target.ai_score = preparation.ai_score
                    target.ai_score_source = preparation.ai_score_source
                    target.ai_summary = preparation.ai_summary
                    target.semantic_input_source = getattr(preparation.semantic_diag, "input_source", None)
                    target.semantic_input_chars = getattr(preparation.semantic_diag, "input_chars", None)
                    target.semantic_chunks = getattr(preparation.semantic_diag, "chunks", None)
                    target.semantic_fallback_reason = getattr(preparation.semantic_diag, "fallback_reason", None)
                    target.keyword_source = getattr(preparation.semantic_diag, "keyword_source", None)
                    target.thread_snapshot_used = getattr(preparation.semantic_diag, "thread_snapshot_used", None)
                    target.thread_snapshot_email_id = getattr(preparation.semantic_diag, "thread_snapshot_email_id", None)
                    target.semantic_embedding = preparation.email_embedding_json or target.semantic_embedding
                    target.hard_filter_result = preparation.hard_filter_reason
                    target.state = "processed_skipped"
                    target.decision = "Reject"
                    target.auto_reject_reason = preparation.auto_reject_reason
                    target.decision_reason = preparation.decision_reason
                    target.skip_reason = preparation.skip_reason
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

            routing_decision = preparation.routing_decision
            if routing_decision is not None and preparation.outcome == "routing_failed":
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
                    target.skip_reason = preparation.skip_reason
                    target.decision_reason = preparation.decision_reason
                    request.deps.apply_routing_decision(target, routing_decision)
                    target.routing_confirmed = False
                    target.resume_asset_id = selected_resume.id if selected_resume else None
                    target.resume_file_name = selected_resume.file_name if selected_resume else None

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
            if request.user_settings.feature_ai_enabled:
                ai_last_error = preparation.draft_ai_error
                ai_last_draft_source = preparation.draft_source
            else:
                ai_last_draft_source = preparation.draft_source
            email = self._email_row(existing, request, item, parsed)
            def apply_queued_state(target: RecruiterEmail) -> None:
                target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                target.score = int(preparation.ai_score * 100)
                target.ai_score = preparation.ai_score
                target.ai_score_source = preparation.ai_score_source
                target.ai_summary = preparation.ai_summary
                target.semantic_input_source = getattr(preparation.semantic_diag, "input_source", None)
                target.semantic_input_chars = getattr(preparation.semantic_diag, "input_chars", None)
                target.semantic_chunks = getattr(preparation.semantic_diag, "chunks", None)
                target.semantic_fallback_reason = getattr(preparation.semantic_diag, "fallback_reason", None)
                target.keyword_source = getattr(preparation.semantic_diag, "keyword_source", None)
                target.thread_snapshot_used = getattr(preparation.semantic_diag, "thread_snapshot_used", None)
                target.thread_snapshot_email_id = getattr(preparation.semantic_diag, "thread_snapshot_email_id", None)
                target.semantic_embedding = preparation.email_embedding_json or target.semantic_embedding
                target.hard_filter_result = preparation.hard_filter_reason
                target.draft_reply = preparation.draft_reply or ""
                target.draft_source = preparation.draft_source
                target.draft_model = preparation.draft_model
                target.draft_ai_error = preparation.draft_ai_error
                target.draft_resume_context_status = preparation.draft_resume_context_status
                target.last_error = None
                target.state = "needs_review"
                target.decision = "Qualified"
                target.decision_reason = preparation.decision_reason
                target.approval_status = "pending"
                target.sent_status = "not_sent"
                target.sent_at = None
                target.gmail_sent_id = None
                request.deps.apply_routing_decision(target, routing_decision)
                target.routing_confirmed = False
                target.resume_asset_id = selected_resume.id if selected_resume else None
                target.resume_file_name = selected_resume.file_name if selected_resume else None
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
