from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any, Callable, Mapping, cast

from app.gates import EmailIntentDecision
from app.job_intent_learning import approved_learning_signals_for_owner, record_pending_job_intent_learning
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import settings as app_settings
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.parsing import build_skills_json_payload
from app.phase0 import jd_entity_fields_from_parsed
from app.parsing.document_extraction import prepare_gmail_parse_body
from app.recent_runs import SkippedItemRecord
from app.routing import RoutingDecision
from app.services import policy_service
from app.services.gmail_group_source_service import ConfiguredRequirementGroup, resolve_trusted_group_context
from app.services.candidate_screening_service import CandidateScreeningService, apply_screening_decision
from app.services.requirement_expansion_service import RequirementExpansionService
from app.services.role_manifest_pipeline import extract_and_score_children
from app.services.role_manifest_service import RoleManifestResult, RoleManifestService
from app.services.sendability_service import apply_resume_sendability
from .queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)

logger = logging.getLogger(__name__)


CandidateItem = Mapping[str, Any]


def _default_intent_decision(**_kwargs: object) -> EmailIntentDecision:
    return EmailIntentDecision(
        intent_type="recruiter_job_requirement",
        action="process_for_queue",
        confidence=1.0,
        reason="Legacy dependency set did not provide an intent classifier.",
        evidence=[],
        negative_evidence=[],
        provider="legacy",
    )


def _discard_skipped_item(_db: Session, _payload: SkippedItemRecord) -> None:
    return None


@dataclass(frozen=True)
class RunOrchestratorDependencies:
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    parse_email_with_details: Callable[..., tuple[dict[str, str | int | bool], dict[str, Any]]]
    hard_filter_check: Callable[
        [dict[str, str | int | bool], UserSettings, Mapping[str, Any], Mapping[str, Any] | None],
        tuple[bool, str],
    ]
    compute_blended_ai_score: Callable[
        [str, str, dict[str, str | int | bool], UserSettings, RecruiterEmail | None, ResumeAsset | None],
        tuple[float, str, str, str | None, str | None, Any],
    ]
    policy_f2f_block: Callable[[dict[str, str | int | bool], Mapping[str, Any], UserSettings], tuple[bool, str]]
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
    is_recruiter_like: Callable[[str, str, str], bool]
    classify_email_intent: Callable[..., EmailIntentDecision] = _default_intent_decision
    record_skipped_item: Callable[[Session, SkippedItemRecord], None] = _discard_skipped_item
    regenerate_candidate: Callable[..., RecruiterEmail] | None = None
    get_candidate: Callable[[Session, int], RecruiterEmail] | None = None


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
    run_source: str = "automation_run"
    run_key: str = ""
    trusted_groups: list[ConfiguredRequirementGroup] = field(default_factory=list)


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
                self._record_skipped_item(
                    request=request,
                    item=item,
                    reason_code="approved_sent_duplicate",
                    reason_detail="Skipped because this Gmail message was already approved and sent.",
                    candidate_email_id=existing.id,
                    gmail_message_url=existing.gmail_message_url,
                )
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
            recruiter_like_warning: str | None = None
            recruiter_like_mode = policy_service.recruiter_like_rule_mode(request.effective_policy)
            recruiter_like = request.deps.is_recruiter_like(sender, subject, body)
            trusted_group_context = resolve_trusted_group_context(
                groups=request.trusted_groups,
                subject=subject,
                body=body,
                to_header=str(item.get("to_header") or ""),
                cc_header=str(item.get("cc_header") or ""),
                list_id=str(item.get("list_id") or ""),
                list_post=str(item.get("list_post") or ""),
                list_unsubscribe=str(item.get("list_unsubscribe") or ""),
                delivered_to=str(item.get("delivered_to") or ""),
                mailing_list=str(item.get("mailing_list") or ""),
            )
            approved_learning_signals = approved_learning_signals_for_owner(request.db, request.owner_id)
            intent_decision = request.deps.classify_email_intent(
                sender=sender,
                subject=subject,
                body=body,
                snippet=snippet,
                recruiter_like=recruiter_like,
                groq_enabled=bool(request.user_settings.feature_groq_job_parser_enabled),
                trusted_group_context=trusted_group_context,
                approved_learning_signals=approved_learning_signals,
            )
            if intent_decision.provider == "groq" and intent_decision.learned_signals:
                record_pending_job_intent_learning(
                    request.db,
                    owner_id=request.owner_id,
                    intent_type=intent_decision.intent_type,
                    confidence=intent_decision.confidence,
                    evidence=intent_decision.evidence,
                    negative_evidence=intent_decision.negative_evidence,
                    learned_signals=intent_decision.learned_signals,
                )
            if intent_decision.action == "skip":
                skipped_count += 1
                self._record_skipped_item(
                    request=request,
                    item=item,
                    reason_code=intent_decision.intent_type or "skip",
                    reason_detail=intent_decision.reason,
                    intent_decision=intent_decision,
                    trusted_group_context=trusted_group_context,
                )
                continue
            if recruiter_like_mode in {"block", "warn"} and not recruiter_like:
                recruiter_like_warning = "non_recruiter_like_gmail"
            parse_body = prepare_gmail_parse_body(body)
            manifest_result: RoleManifestResult | None = None
            if request.user_settings.feature_role_manifest_enabled:
                manifest_result = RoleManifestService(max_rung=2).detect(parse_body)
            item_ai_extractor_enabled = request.user_settings.feature_ai_extractor_enabled and (
                manifest_result is None or manifest_result.status != "multiple"
            )
            parsed_for_selection, parser_details = request.deps.parse_email_with_details(
                subject,
                parse_body,
                source="gmail",
                ai_extractor_enabled=item_ai_extractor_enabled,
            )
            parser_details_json = json.dumps(parser_details, separators=(",", ":"))
            skills_json = json.dumps(
                build_skills_json_payload(
                    parser_details,
                    fallback_skills_text=str(parsed_for_selection.get("skills_text", "")),
                ),
                separators=(",", ":"),
            )
            screening = CandidateScreeningService().evaluate_parser_details(
                parser_details,
                request.user_settings,
            )
            if not screening.proceed_to_scoring:
                if request.dry_run:
                    queued_count += 1
                    continue
                email = self._email_row(existing, request, item, parsed_for_selection, parser_details_json, skills_json)

                def apply_screened_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.parser_details_json = parser_details_json
                    target.skills_json = skills_json
                    target.state = "needs_review"
                    target.decision = "Qualified"
                    target.decision_reason = "strict_candidate_screening"
                    target.approval_status = "pending"
                    target.sent_status = "not_sent"
                    apply_screening_decision(target, screening)

                apply_screened_state(email)
                email = self._commit_email_phase(
                    request=request,
                    email=email,
                    existing=existing,
                    external_message_id=external_message_id,
                    branch_name="strict_candidate_screening",
                    reapply_state=apply_screened_state,
                )
                request.db.refresh(email)
                request.deps.apply_gmail_label(request.db, email, item)
                request.db.commit()
                request.db.refresh(email)
                request.deps.mark_message_processed(external_message_id)
                queued_count += 1
                queued_email_ids.append(email.id)
                last_email = email
                if manifest_result is not None:
                    self._expand_and_extract_children(request, email, manifest_result)
                continue
            resume_selection = request.deps.select_best_resume_match(
                subject=subject,
                body=body,
                parsed=parsed_for_selection,
                parser_details=parser_details,
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
                        parsed_overrides=parsed_for_selection,
                        parser_details=parser_details,
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
                    self._record_skipped_item(
                        request=request,
                        item=item,
                        reason_code=preparation.skip_reason or "not_qualified",
                        reason_detail=preparation.decision_reason or "Skipped because the candidate was not qualified for queueing.",
                        intent_decision=intent_decision,
                        trusted_group_context=trusted_group_context,
                        qualification_result=preparation.qualification_result,
                        blocking_rule=preparation.blocking_rule,
                        qualification_detail=preparation.qualification_detail,
                        qualification_context=preparation.qualification_context,
                    )
                    continue
                email = self._email_row(existing, request, item, parsed, parser_details_json, skills_json)
                def apply_skipped_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.parser_details_json = parser_details_json
                    target.skills_json = skills_json
                    target.score = int(preparation.ai_score * 100)
                    target.ai_score = preparation.ai_score
                    target.ai_score_source = preparation.ai_score_source
                    target.ai_summary = preparation.ai_summary
                    target.resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
                    target.resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
                    target.resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
                    target.resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
                    target.semantic_input_source = getattr(preparation.semantic_diag, "input_source", None)
                    target.semantic_input_chars = getattr(preparation.semantic_diag, "input_chars", None)
                    target.semantic_chunks = getattr(preparation.semantic_diag, "chunks", None)
                    target.semantic_fallback_reason = getattr(preparation.semantic_diag, "fallback_reason", None)
                    target.keyword_source = getattr(preparation.semantic_diag, "keyword_source", None)
                    target.thread_snapshot_used = getattr(preparation.semantic_diag, "thread_snapshot_used", None)
                    target.thread_snapshot_email_id = getattr(preparation.semantic_diag, "thread_snapshot_email_id", None)
                    target.semantic_embedding = preparation.email_embedding_json or target.semantic_embedding
                    warnings: list[str] = []
                    if preparation.hard_filter_reason not in {"", "hard_filters_passed"}:
                        warnings.append(preparation.hard_filter_reason.removeprefix("warnings: ").strip())
                    if recruiter_like_warning:
                        warnings.append(recruiter_like_warning)
                    target.hard_filter_result = policy_service.combine_rule_messages(warnings)
                    target.state = "processed_skipped"
                    target.decision = "Reject"
                    target.auto_reject_reason = preparation.auto_reject_reason
                    target.decision_reason = preparation.decision_reason
                    target.skip_reason = preparation.skip_reason
                    target.intent_type = intent_decision.intent_type
                    target.intent_confidence = intent_decision.confidence
                    target.intent_reason = intent_decision.reason
                    target.intent_evidence_json = json.dumps(intent_decision.evidence, separators=(",", ":"))
                    target.intent_negative_evidence_json = json.dumps(intent_decision.negative_evidence, separators=(",", ":"))
                    target.gate_action = intent_decision.action
                    target.gate_provider = intent_decision.provider
                    target.source_group_name = trusted_group_context.group_name
                    target.source_group_email = trusted_group_context.group_email
                    target.source_group_match_method = trusted_group_context.match_method
                    target.source_group_trusted = trusted_group_context.trusted if trusted_group_context.matched else False
                    target.qualification_result = preparation.qualification_result
                    target.blocking_rule = preparation.blocking_rule
                    target.qualification_detail = preparation.qualification_detail
                    target.qualification_context_json = json.dumps(preparation.qualification_context or {}, separators=(",", ":"))
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
                self._record_skipped_item(
                    request=request,
                    item=item,
                    reason_code=email.skip_reason or "not_qualified",
                    reason_detail=email.decision_reason or "Skipped because the candidate was not qualified for queueing.",
                    candidate_email_id=email.id,
                    gmail_message_url=email.gmail_message_url,
                    intent_decision=intent_decision,
                    trusted_group_context=trusted_group_context,
                    qualification_result=email.qualification_result,
                    blocking_rule=email.blocking_rule,
                    qualification_detail=email.qualification_detail,
                    qualification_context=json.loads(email.qualification_context_json or "{}") if email.qualification_context_json else None,
                )
                last_email = email
                continue

            routing_decision = preparation.routing_decision
            if routing_decision is not None and preparation.outcome == "routing_failed":
                if request.dry_run:
                    failed_count += 1
                    continue
                email = self._email_row(existing, request, item, parsed, parser_details_json, skills_json)
                def apply_failed_state(target: RecruiterEmail) -> None:
                    target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                    target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                    target.state = routing_decision.recommended_state
                    target.decision = "Reject"
                    target.last_error = "Could not resolve recruiter To and employer CC"
                    target.skip_reason = preparation.skip_reason
                    target.decision_reason = preparation.decision_reason
                    target.intent_type = intent_decision.intent_type
                    target.intent_confidence = intent_decision.confidence
                    target.intent_reason = intent_decision.reason
                    target.intent_evidence_json = json.dumps(intent_decision.evidence, separators=(",", ":"))
                    target.intent_negative_evidence_json = json.dumps(intent_decision.negative_evidence, separators=(",", ":"))
                    target.gate_action = intent_decision.action
                    target.gate_provider = intent_decision.provider
                    target.source_group_name = trusted_group_context.group_name
                    target.source_group_email = trusted_group_context.group_email
                    target.source_group_match_method = trusted_group_context.match_method
                    target.source_group_trusted = trusted_group_context.trusted if trusted_group_context.matched else False
                    warnings: list[str] = []
                    if recruiter_like_warning:
                        warnings.append(recruiter_like_warning)
                    target.hard_filter_result = policy_service.combine_rule_messages(warnings)
                    target.qualification_result = preparation.qualification_result
                    target.blocking_rule = preparation.blocking_rule
                    target.qualification_detail = preparation.qualification_detail
                    target.qualification_context_json = json.dumps(preparation.qualification_context or {}, separators=(",", ":"))
                    request.deps.apply_routing_decision(target, routing_decision)
                    target.routing_confirmed = False
                    target.resume_asset_id = selected_resume.id if selected_resume else None
                    target.resume_file_name = selected_resume.file_name if selected_resume else None
                    target.resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
                    target.resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
                    target.resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
                    target.resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
                    target.parser_details_json = parser_details_json
                    target.skills_json = skills_json

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
            email = self._email_row(existing, request, item, parsed, parser_details_json, skills_json)
            def apply_queued_state(target: RecruiterEmail) -> None:
                target.external_rfc_message_id = target.external_rfc_message_id or item.get("external_rfc_message_id")
                target.gmail_received_at = target.gmail_received_at or item.get("gmail_received_at")
                target.score = int(preparation.ai_score * 100)
                target.ai_score = preparation.ai_score
                target.ai_score_source = preparation.ai_score_source
                target.ai_summary = preparation.ai_summary
                target.ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
                target.ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
                target.ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
                target.ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
                target.resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
                target.resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
                target.resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
                target.resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
                target.semantic_input_source = getattr(preparation.semantic_diag, "input_source", None)
                target.semantic_input_chars = getattr(preparation.semantic_diag, "input_chars", None)
                target.semantic_chunks = getattr(preparation.semantic_diag, "chunks", None)
                target.semantic_fallback_reason = getattr(preparation.semantic_diag, "fallback_reason", None)
                target.keyword_source = getattr(preparation.semantic_diag, "keyword_source", None)
                target.thread_snapshot_used = getattr(preparation.semantic_diag, "thread_snapshot_used", None)
                target.thread_snapshot_email_id = getattr(preparation.semantic_diag, "thread_snapshot_email_id", None)
                target.semantic_embedding = preparation.email_embedding_json or target.semantic_embedding
                target.intent_type = intent_decision.intent_type
                target.intent_confidence = intent_decision.confidence
                target.intent_reason = intent_decision.reason
                target.intent_evidence_json = json.dumps(intent_decision.evidence, separators=(",", ":"))
                target.intent_negative_evidence_json = json.dumps(intent_decision.negative_evidence, separators=(",", ":"))
                target.gate_action = intent_decision.action
                target.gate_provider = intent_decision.provider
                target.source_group_name = trusted_group_context.group_name
                target.source_group_email = trusted_group_context.group_email
                target.source_group_match_method = trusted_group_context.match_method
                target.source_group_trusted = trusted_group_context.trusted if trusted_group_context.matched else False
                warnings: list[str] = []
                if preparation.hard_filter_reason not in {"", "hard_filters_passed"}:
                    warnings.append(preparation.hard_filter_reason.removeprefix("warnings: ").strip())
                if recruiter_like_warning:
                    warnings.append(recruiter_like_warning)
                target.hard_filter_result = policy_service.combine_rule_messages(warnings)
                target.qualification_result = preparation.qualification_result
                target.blocking_rule = preparation.blocking_rule
                target.qualification_detail = preparation.qualification_detail
                target.qualification_context_json = json.dumps(preparation.qualification_context or {}, separators=(",", ":"))
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
                target.parser_details_json = parser_details_json
                target.skills_json = skills_json
                request.deps.apply_routing_decision(target, routing_decision)
                target.routing_confirmed = False
                target.resume_asset_id = selected_resume.id if selected_resume else None
                target.resume_file_name = selected_resume.file_name if selected_resume else None
                apply_screening_decision(target, screening)
                apply_resume_sendability(target)
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
            if manifest_result is not None:
                self._expand_and_extract_children(request, email, manifest_result)

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

    def _expand_and_extract_children(
        self,
        request: RunOrchestratorRequest,
        email: RecruiterEmail,
        manifest_result: RoleManifestResult,
    ) -> None:
        if request.deps.regenerate_candidate is None or request.deps.get_candidate is None:
            return
        expansion = RequirementExpansionService().expand(
            request.db,
            email,
            manifest_result,
            materialize=app_settings.role_manifest_child_creation_enabled,
        )
        if expansion.child_ids:
            extract_and_score_children(
                request.db,
                expansion.child_ids,
                user_settings=request.user_settings,
                get_candidate=request.deps.get_candidate,
                parse_email_with_details=request.deps.parse_email_with_details,
                regenerate_candidate=request.deps.regenerate_candidate,
            )

    def _email_row(
        self,
        existing: RecruiterEmail | None,
        request: RunOrchestratorRequest,
        item: CandidateItem,
        parsed: dict[str, str | int | bool],
        parser_details_json: str,
        skills_json: str,
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
            skills_json=skills_json,
            **jd_entity_fields_from_parsed(parsed),
            source="gmail",
            external_message_id=str(item["external_message_id"]),
            external_thread_id=item.get("external_thread_id"),
            external_rfc_message_id=item.get("external_rfc_message_id"),
            gmail_received_at=item.get("gmail_received_at"),
            recipient_email=item.get("recipient_email"),
            parser_details_json=parser_details_json,
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

    def _record_skipped_item(
        self,
        *,
        request: RunOrchestratorRequest,
        item: CandidateItem,
        reason_code: str,
        reason_detail: str,
        candidate_email_id: int | None = None,
        gmail_message_url: str | None = None,
        intent_decision: EmailIntentDecision | None = None,
        trusted_group_context: Any = None,
        qualification_result: str | None = None,
        blocking_rule: str | None = None,
        qualification_detail: str | None = None,
        qualification_context: dict[str, Any] | None = None,
    ) -> None:
        request.deps.record_skipped_item(
            request.db,
            SkippedItemRecord(
                owner_id=request.owner_id,
                run_source=request.run_source,
                run_key=request.run_key,
                source_type="gmail",
                reason_code=reason_code,
                reason_detail=reason_detail,
                external_message_id=str(item.get("external_message_id") or ""),
                external_thread_id=str(item.get("external_thread_id") or "") or None,
                candidate_email_id=candidate_email_id,
                title_or_subject=str(item.get("subject") or ""),
                sender=str(item.get("sender") or ""),
                source_url=None,
                gmail_message_url=gmail_message_url,
                intent_type=intent_decision.intent_type if intent_decision else None,
                intent_confidence=intent_decision.confidence if intent_decision else None,
                intent_reason=intent_decision.reason if intent_decision else None,
                intent_evidence=intent_decision.evidence if intent_decision else None,
                intent_negative_evidence=intent_decision.negative_evidence if intent_decision else None,
                gate_action=intent_decision.action if intent_decision else None,
                gate_provider=intent_decision.provider if intent_decision else None,
                source_group_name=getattr(trusted_group_context, "group_name", None),
                source_group_email=getattr(trusted_group_context, "group_email", None),
                source_group_match_method=getattr(trusted_group_context, "match_method", None),
                source_group_trusted=getattr(trusted_group_context, "trusted", None),
                qualification_result=qualification_result,
                blocking_rule=blocking_rule,
                qualification_detail=qualification_detail,
                qualification_context=qualification_context,
            ),
        )
