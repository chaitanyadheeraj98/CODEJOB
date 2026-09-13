from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, cast

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.automation.queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    describe_f2f_block,
    describe_hard_filter_block,
    describe_score_threshold_block,
    prepare_candidate_for_queue,
)
from app.gates import EmailIntentDecision, llm_decided
from app.gates.sender_denylist import SENDER_DENYLIST
from app.ai.resume_context_attribution import RESUME_CONTEXT_MISSING, RESUME_CONTEXT_RULES_ONLY
from app.automation import RunOrchestrator, RunOrchestratorDependencies, RunOrchestratorRequest
from app.external_feeds.dedupe import build_email_content_hash
from app.external_feeds.models import ExternalOpportunity
from app.external_feeds.parser import parse_nvoids_detail
from app.job_intent_learning import approved_learning_signals_for_owner, record_pending_job_intent_learning
from app.services import application_intelligence_service, appts_service, opportunity_lineage_service, policy_service
from app.services.policy_service import EffectiveRunInputs
from app.gmail_client import GmailMessageCandidate, MailAttachment
from app.models import AttachmentAsset, DraftEditFeedback, EmailConversation, EmailReplyMessage, GmailRequirementGroup, OpportunityLineage, RecipientRoutingFeedback, RecentRun, RecentRunSkippedItem, RecruiterEmail, ResumeAsset, SyncRun, UserSettings
from app.parsing import build_skills_json_payload
from app.parsing.document_extraction import prepare_gmail_parse_body
from app.parsing.jd_requirements import has_job_description_content, requirements_from_payload
from app.phase0 import DEFAULT_SIGNATURE_EMAIL, RoutingResult, jd_entity_fields_from_parsed, parse_email_with_details
from app.recent_runs import (
    RUN_SOURCE_AUTOMATION,
    RUN_SOURCE_GMAIL_SYNC,
    SkippedItemRecord,
    automation_run_key,
    create_recent_run,
    gmail_sync_run_key,
    record_skipped_item,
    update_recent_run,
)
from app.routing import RoutingDecision
from app.schemas import ApproveSendRequest, AutomationRunRequest, AutomationRunResponse, ConversationDetailResponse, ConversationSummaryResponse, GmailSyncResponse, RegenerateCandidateRequest, RejectRequest, ResolveRecipientsRequest, resume_variant_token
from app.config import settings as app_settings
from app.services.candidate_runtime_service import resolve_resume_display_name
from app.services.candidate_screening_service import CandidateScreeningService, apply_screening_decision
from app.services.email_inbox_service import (
    capture_inbound_reply,
    conversation_detail,
    ensure_sent_conversation,
    generate_tracking_token,
    list_conversations,
    mark_conversation_read,
    send_conversation_reply,
    tracking_pixel_url,
)
from app.services.gmail_group_source_service import ConfiguredRequirementGroup, resolve_trusted_group_context
from app.services.requirement_expansion_service import RequirementExpansionService
from app.services.role_manifest_pipeline import extract_and_score_children
from app.services.role_provenance import (
    apply_role_assignment,
    apply_role_family,
    assign_role,
    role_family_fields,
)
from app.services.role_taxonomy import fill_entity_gaps, role_matcher_for
from app.services.role_manifest_service import RoleManifestResult, RoleManifestService
from app.services.sendability_service import apply_resume_sendability, resolve_sendability_status

logger = logging.getLogger(__name__)

REPLY_THREAD_SCAN_LOOKBACK_DAYS = 7
REPLY_UNREAD_SCAN_MAX_RESULTS = 100


@dataclass
class OrchestrationDeps:
    owner_id: str
    model_name: str
    get_settings: Callable[[Session], UserSettings]
    active_resume: Callable[[Session], ResumeAsset | None]
    enabled_resumes: Callable[[Session], list[ResumeAsset]]
    enabled_attachment_assets: Callable[[Session], list[AttachmentAsset]]
    effective_run_inputs: Callable[[UserSettings, str | None], EffectiveRunInputs]
    compute_blended_ai_score: Callable[..., tuple[float, str, str, str | None, str | None, Any]]
    select_best_resume_match: Callable[..., Any]
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
    policy_f2f_block: Callable[[dict[str, str | int | bool], Any, UserSettings], tuple[bool, str]]
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
    classify_email_intent: Callable[..., EmailIntentDecision]
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    parse_email_with_details: Callable[..., tuple[dict[str, str | int | bool], dict[str, Any]]]
    hard_filter_check: Callable[[dict[str, str | int | bool], UserSettings, Any], tuple[bool, str]]
    greeting_from_to_contact: Callable[[str | None, str], str]
    generate_reply_with_ai_or_fallback: Callable[..., Any]
    send_reply_with_attachment: Callable[..., str]
    send_new_email_with_attachment: Callable[..., str]
    mark_message_processed: Callable[[str], None]
    append_tracking_sheet_row: Callable[..., None]
    mark_reply_processed: Callable[[str, list[str] | None], None] | None = None
    get_message_thread_id: Callable[[str], str] | None = None
    get_message_rfc_message_id: Callable[[str], str] | None = None
    list_thread_messages: Callable[[str], list[GmailMessageCandidate]] | None = None
    list_unread_thread_ids: Callable[[], set[str]] | None = None
    list_gmail_labels: Callable | None = None
    list_candidates_by_label_ids: Callable | None = None
    list_thread_ids_by_label: Callable | None = None
    list_candidates_by_query: Callable | None = None


class OrchestrationService:
    def __init__(self, deps: OrchestrationDeps):
        self.deps = deps

    def _sync_label_tracking(self, db: Session, user_settings: UserSettings) -> tuple[int, int, int]:
        if not (app_settings.feature_label_tracking_enabled and user_settings.feature_label_tracking_enabled):
            return 0, 0, 0
        from types import SimpleNamespace
        from app import gmail_client
        from app.services import label_tracking_service
        deps = SimpleNamespace(
            list_candidates_by_label_ids=self.deps.list_candidates_by_label_ids or gmail_client.list_candidates_by_label_ids,
            list_thread_ids_by_label=self.deps.list_thread_ids_by_label or gmail_client.list_thread_ids_by_label,
            list_candidates_by_query=self.deps.list_candidates_by_query or gmail_client.list_candidates_by_query,
        )
        owner_email = user_settings.signature_email or DEFAULT_SIGNATURE_EMAIL
        messages = threads = 0
        errors = 0
        for index, step in enumerate((
            lambda: self.sync_gmail_labels(db),
            lambda: label_tracking_service.reconcile_untracked(db, self.deps.owner_id, deps=deps),
            lambda: label_tracking_service.sync_tracked_labels(db, self.deps.owner_id, deps=deps, owner_email=owner_email),
            lambda: label_tracking_service.sync_watch_matches(db, self.deps.owner_id, deps=deps, owner_email=owner_email, max_messages=max(0, app_settings.label_tracking_max_messages_per_sync - messages)),
        )):
            if index == 3 and errors:
                continue
            try:
                with db.begin_nested():
                    result = step()
                db.commit()
                messages += getattr(result, "messages", 0)
                threads += getattr(result, "threads", 0)
            except Exception:
                errors += 1
                logger.warning("label_tracking_sync_step_failed", exc_info=False)
        return threads, messages, errors

    def sync_gmail_labels(self, db: Session):
        from app.services import gmail_label_service
        from app import gmail_client
        return gmail_label_service.sync_labels(db, self.deps.owner_id, list_labels=self.deps.list_gmail_labels or gmail_client.list_gmail_labels)

    def list_label_threads(self, db: Session, *, owner_id: str | None = None, **filters):
        from app.services.label_tracking_service import list_label_threads
        return list_label_threads(db, owner_id or self.deps.owner_id, **filters)

    def set_tracked_labels(self, db: Session, external_label_ids: list[str]):
        from app.services import gmail_label_service
        return gmail_label_service.set_tracked(db, self.deps.owner_id, external_label_ids)

    @staticmethod
    def _extract_external_post_id(external_message_id: str | None) -> str | None:
        message_id = (external_message_id or "").strip()
        if not message_id.lower().startswith("nvoids:"):
            return None
        external_post_id = message_id.split(":", 1)[1].strip()
        return external_post_id or None

    def _load_external_opportunity(self, db: Session, email: RecruiterEmail) -> ExternalOpportunity | None:
        external_post_id = self._extract_external_post_id(email.external_message_id)
        if not external_post_id:
            return None
        return (
            db.query(ExternalOpportunity)
            .filter(
                ExternalOpportunity.owner_id == self.deps.owner_id,
                ExternalOpportunity.external_post_id == external_post_id,
            )
            .first()
        )

    @staticmethod
    def _has_manual_routing_edit(email: RecruiterEmail) -> bool:
        try:
            evidence = json.loads(email.routing_evidence or "[]")
        except (TypeError, ValueError):
            return False
        if not isinstance(evidence, list):
            return False
        return any(isinstance(item, dict) and item.get("source") == "manual_edit" for item in evidence)

    @staticmethod
    def _manual_routing_decision(email: RecruiterEmail) -> RoutingDecision:
        to_email = (email.recipient_email or "").strip() or None
        cc_email = (email.cc_email or "").strip() or None
        has_pair = bool(to_email and cc_email)
        return RoutingDecision(
            to_email=to_email,
            cc_email=cc_email,
            status="confirmed" if has_pair else "missing",
            confidence=1.0 if has_pair else 0.0,
            reason="Recipient routing manually confirmed; preserved during regenerate.",
            evidence=[],
            candidates=[],
            recommended_state="needs_review" if has_pair else "failed",
            recommended_skip_reason=None if has_pair else "missing_to_or_cc",
            should_mark_failed=not has_pair,
            is_sendable_candidate=has_pair,
            needs_manual_confirmation=False,
        )

    def _load_enabled_requirement_groups(self, db: Session) -> list[ConfiguredRequirementGroup]:
        rows = (
            db.query(GmailRequirementGroup)
            .filter(
                GmailRequirementGroup.owner_id == self.deps.owner_id,
                GmailRequirementGroup.enabled.is_(True),
            )
            .order_by(GmailRequirementGroup.id.asc())
            .all()
        )
        return [
            ConfiguredRequirementGroup(
                id=row.id,
                display_name=row.display_name,
                group_email=row.group_email,
                normalized_group_email=row.normalized_group_email,
                group_slug=row.group_slug,
                enabled=row.enabled,
            )
            for row in rows
        ]

    def _get_email_or_raise(self, db: Session, email_id: int) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return email

    def _sent_thread_ids(self, db: Session) -> set[str]:
        sent_thread_ids = {
            str(row[0])
            for row in db.query(RecruiterEmail.external_thread_id)
            .filter(
                RecruiterEmail.owner_id == self.deps.owner_id,
                RecruiterEmail.sent_status == "sent",
                RecruiterEmail.external_thread_id.is_not(None),
            )
            .all()
            if row[0]
        }
        sent_thread_ids.update(
            str(row[0])
            for row in db.query(EmailConversation.external_thread_id)
            .filter(EmailConversation.owner_id == self.deps.owner_id)
            .all()
            if row[0]
        )
        return sent_thread_ids

    def count_live_unread_replies(self, db: Session) -> int:
        """Cheap, accurate-ish live count: unread inbox threads that are also sent threads.

        Unlike _capture_inbound_replies, this only checks thread-id membership (no
        per-message header fetch), so it misses the rfc-message-id-in-headers path
        that catches a reply landing on a different thread id. That secondary path
        exists for an edge case; the full sync still runs it. This is meant as a
        cheap "worth checking" signal, not a replacement for the real scan.
        """
        if self.deps.list_unread_thread_ids is None:
            return 0
        sent_thread_ids = self._sent_thread_ids(db)
        if not sent_thread_ids:
            return 0
        return len(sent_thread_ids & self.deps.list_unread_thread_ids())

    def _capture_inbound_replies(self, db: Session, user_settings: UserSettings) -> tuple[int, int]:
        """Scan for and ingest inbound replies to previously-sent recruiter emails.

        Runs from both sync_gmail and run_once (Sync Now / automation / the
        auto-poller all end up in one of those two) so reply detection doesn't
        depend on which entry point triggered the run. Two sources feed the scan,
        each individually a full Gmail API content-fetch per message, so both are
        bounded rather than exhaustive:

        - An is:unread search, capped at REPLY_UNREAD_SCAN_MAX_RESULTS most-recent
          matches (Gmail returns newest-first, and this account's unread count in
          the low hundreds made an unbounded fetch take minutes on its own).
        - A direct per-thread fetch of EmailConversation rows updated in the last
          REPLY_THREAD_SCAN_LOOKBACK_DAYS days — this is what catches a reply that
          was already opened/read in Gmail before a sync ran (the unread search
          can never see it again once read), scoped to recent activity rather
          than every email ever sent, since that set only grows over time.

        A reply landing outside both bounds (old, already-read, thread untouched
        in a week) will still surface on a later run once send/reply activity on
        that thread brings its EmailConversation back into the lookback window,
        or if it happens to still be unread.

        Returns (matched_count, created_count).
        """
        if not user_settings.feature_reply_inbox_enabled:
            return 0, 0

        sent_thread_ids = self._sent_thread_ids(db)
        sent_rfc_message_ids = {
            str(row[0]).strip().lower()
            for row in db.query(EmailReplyMessage.external_rfc_message_id)
            .filter(
                EmailReplyMessage.owner_id == self.deps.owner_id,
                EmailReplyMessage.direction == "outbound",
                EmailReplyMessage.external_rfc_message_id.is_not(None),
            )
            .all()
            if row[0]
        }

        candidates: list[GmailMessageCandidate] = []
        seen_message_ids: set[str] = set()

        for item in self.deps.list_unread_candidates_by_query(
            "is:unread in:inbox", max_total_results=REPLY_UNREAD_SCAN_MAX_RESULTS
        ):
            if item["external_message_id"] in seen_message_ids:
                continue
            reply_headers = f"{item.get('in_reply_to_header') or ''} {item.get('references_header') or ''}".lower()
            if item.get("external_thread_id") in sent_thread_ids or any(
                message_id in reply_headers for message_id in sent_rfc_message_ids
            ):
                candidates.append(item)
                seen_message_ids.add(item["external_message_id"])

        if self.deps.list_thread_messages is not None:
            recency_cutoff = datetime.now(UTC) - timedelta(days=REPLY_THREAD_SCAN_LOOKBACK_DAYS)
            recent_thread_ids = {
                str(row[0])
                for row in db.query(EmailConversation.external_thread_id)
                .filter(
                    EmailConversation.owner_id == self.deps.owner_id,
                    EmailConversation.last_message_at >= recency_cutoff,
                    EmailConversation.root_recruiter_email_id.is_not(None),
                )
                .all()
                if row[0]
            }
            for thread_id in recent_thread_ids:
                try:
                    thread_items = self.deps.list_thread_messages(thread_id)
                except Exception:
                    logger.exception("gmail_thread_scan_failed thread_id=%s", thread_id)
                    continue
                for item in thread_items:
                    if item["external_message_id"] in seen_message_ids:
                        continue
                    candidates.append(item)
                    seen_message_ids.add(item["external_message_id"])

        owner_email = (user_settings.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL
        matched_count = 0
        created_count = 0
        for item in candidates:
            # No "already a RecruiterEmail candidate" skip here: a message that also matched
            # the JD-candidate scan (e.g. a reply whose subject happens to match the saved
            # search) must still be captured as a reply. capture_inbound_reply is already
            # idempotent on external_message_id, so this can't double-insert.
            matched_reply, created_reply = capture_inbound_reply(db, owner_id=self.deps.owner_id, item=item, owner_email=owner_email)
            if not matched_reply:
                continue
            matched_count += 1
            if created_reply:
                created_count += 1
            db.commit()
            if created_reply and user_settings.feature_application_automation_enabled:
                reply = (
                    db.query(EmailReplyMessage)
                    .filter(
                        EmailReplyMessage.owner_id == self.deps.owner_id,
                        EmailReplyMessage.external_message_id == item["external_message_id"],
                    )
                    .first()
                )
                if reply is not None:
                    try:
                        application_intelligence_service.correlate_reply_to_application(
                            db,
                            owner_id=self.deps.owner_id,
                            reply_message_id=reply.id,
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                        logger.exception("application_reply_correlation_failed reply_message_id=%s", reply.id)
            try:
                if self.deps.mark_reply_processed is not None:
                    self.deps.mark_reply_processed(item["external_message_id"], item.get("label_ids"))
                else:
                    self.deps.mark_message_processed(item["external_message_id"])
            except Exception:
                logger.exception("gmail_reply_label_failed external_message_id=%s", item["external_message_id"])

        return matched_count, created_count

    def reconcile_inbox_once(self, db: Session, user_settings: UserSettings) -> tuple[int, int, int]:
        """Run the bounded Inbox and label scans deliberately, once.

        The public door to `_capture_inbound_replies` and `_sync_label_tracking`
        for push delivery, which has exactly two reasons to want them: the first
        watch registration, and recovery from a history cursor Gmail has aged
        out. Both are the same situation - a gap that no notification will ever
        describe, because the notifications for it were either never sent or
        long since acknowledged.

        Named rather than reached through the private methods so that Phase D's
        "these no longer run on a schedule" is a statement about call sites that
        can be checked, instead of a convention.

        Returns (matched_replies, created_replies, label_errors).
        """
        matched, created = self._capture_inbound_replies(db, user_settings)
        _, _, errors = self._sync_label_tracking(db, user_settings)
        return matched, created, errors

    def _detect_role_manifest_if_enabled(self, user_settings: UserSettings, body: str) -> RoleManifestResult | None:
        if not user_settings.feature_role_manifest_enabled:
            return None
        return RoleManifestService(max_rung=2).detect(body)

    def _expand_and_extract_children(
        self, db: Session, email: RecruiterEmail, manifest_result: RoleManifestResult, user_settings: UserSettings
    ) -> None:
        expansion = RequirementExpansionService().expand(
            db,
            email,
            manifest_result,
            materialize=app_settings.role_manifest_child_creation_enabled,
        )
        if expansion.child_ids:
            extract_and_score_children(
                db,
                expansion.child_ids,
                user_settings=user_settings,
                get_candidate=self._get_email_or_raise,
                parse_email_with_details=self.deps.parse_email_with_details,
                regenerate_candidate=self.regenerate_candidate,
            )

    def sync_gmail(
        self,
        db: Session,
        *,
        sync_batch_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> GmailSyncResponse:
        if not self.deps.is_gmail_configured():
            raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")

        user_settings = self.deps.get_settings(db)
        if not user_settings.enabled:
            raise HTTPException(status_code=400, detail="Pipeline is disabled in settings")

        sync_batch_id = sync_batch_id or str(uuid.uuid4())
        sync_run = SyncRun(owner_id=self.deps.owner_id, sync_batch_id=sync_batch_id, started_at=datetime.now(UTC))
        db.add(sync_run)
        db.commit()
        run_key = gmail_sync_run_key(sync_batch_id)
        recent_run = db.query(RecentRun).filter(RecentRun.run_key == run_key).first()
        if recent_run is None:
            recent_run = create_recent_run(
                db,
                owner_id=self.deps.owner_id,
                run_source=RUN_SOURCE_GMAIL_SYNC,
                run_key=run_key,
                sync_batch_id=sync_batch_id,
                status="running",
                detail="Gmail sync started.",
            )
        else:
            recent_run.status = "running"
            recent_run.detail = "Gmail sync started."
            recent_run.sync_batch_id = sync_batch_id
        db.commit()

        imported_count = 0
        skipped_count = 0
        error_count = 0
        skipped_item_count = 0
        try:
            resolved = self.deps.effective_run_inputs(user_settings, None)
            effective_query = resolved.effective_query
            effective_policy = resolved.policy
            threshold = self.deps.policy_threshold(user_settings, effective_policy)
            trusted_groups = self._load_enabled_requirement_groups(db) if user_settings.feature_gmail_requirement_groups_enabled else []
            candidates = self.deps.list_unread_candidates_by_query(effective_query)
            reply_matched_count, reply_created_count = self._capture_inbound_replies(db, user_settings)
            self._sync_label_tracking(db, user_settings)
            imported_count += reply_created_count
            skipped_count += reply_matched_count - reply_created_count
            processed_items = 0
            total_items = len(candidates)

            def report_item() -> None:
                nonlocal processed_items
                processed_items += 1
                db.commit()
                if progress_callback is not None:
                    progress_callback(processed_items, total_items)

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
                    skipped_item_count += 1
                    record_skipped_item(
                        db,
                        SkippedItemRecord(
                            owner_id=self.deps.owner_id,
                            run_source=RUN_SOURCE_GMAIL_SYNC,
                            run_key=run_key,
                            source_type="gmail",
                            reason_code="duplicate_existing_email",
                            reason_detail="Skipped because this Gmail message already exists in the candidate database.",
                            external_message_id=item["external_message_id"],
                            external_thread_id=item["external_thread_id"],
                            candidate_email_id=existing.id,
                            title_or_subject=item["subject"],
                            sender=item["sender"],
                            gmail_message_url=existing.gmail_message_url,
                        ),
                    )
                    report_item()
                    continue

                content_hash = build_email_content_hash(sender=item["sender"], subject=item["subject"], body=item["body"])
                existing_by_content = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id)
                    .filter(RecruiterEmail.content_dedupe_hash == content_hash)
                    .first()
                )
                if existing_by_content:
                    self.deps.apply_gmail_label_for_email(email=existing_by_content, candidate_item=item)
                    skipped_count += 1
                    skipped_item_count += 1
                    record_skipped_item(
                        db,
                        SkippedItemRecord(
                            owner_id=self.deps.owner_id,
                            run_source=RUN_SOURCE_GMAIL_SYNC,
                            run_key=run_key,
                            source_type="gmail",
                            reason_code="duplicate_candidate_content_hash",
                            reason_detail="Skipped because a Gmail message with the same sender, subject, and body already exists in the candidate database, sent under a different message id.",
                            external_message_id=item["external_message_id"],
                            external_thread_id=item["external_thread_id"],
                            candidate_email_id=existing_by_content.id,
                            title_or_subject=item["subject"],
                            sender=item["sender"],
                            gmail_message_url=existing_by_content.gmail_message_url,
                        ),
                    )
                    report_item()
                    continue

                if user_settings.feature_reply_inbox_enabled:
                    matched_reply, created_reply = capture_inbound_reply(
                        db,
                        owner_id=self.deps.owner_id,
                        item=item,
                        owner_email=(user_settings.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL,
                    )
                    if matched_reply:
                        if created_reply:
                            imported_count += 1
                        else:
                            skipped_count += 1
                        report_item()
                        try:
                            if self.deps.mark_reply_processed is not None:
                                self.deps.mark_reply_processed(item["external_message_id"], item.get("label_ids"))
                            else:
                                self.deps.mark_message_processed(item["external_message_id"])
                        except Exception:
                            logger.exception(
                                "gmail_reply_label_failed external_message_id=%s",
                                item["external_message_id"],
                            )
                        continue

                recruiter_like_warning: str | None = None
                recruiter_like_mode = policy_service.recruiter_like_rule_mode(effective_policy)
                is_recruiter_like = self.deps.is_recruiter_like(item["sender"], item["subject"], item["body"])
                trusted_group_context = resolve_trusted_group_context(
                    groups=trusted_groups,
                    subject=item["subject"],
                    body=item["body"],
                    to_header=item.get("to_header"),
                    cc_header=item.get("cc_header"),
                    list_id=item.get("list_id"),
                    list_post=item.get("list_post"),
                    list_unsubscribe=item.get("list_unsubscribe"),
                    delivered_to=item.get("delivered_to"),
                    mailing_list=item.get("mailing_list"),
                )
                sender_domain = self.deps.email_domain(item["sender"])
                if sender_domain in SENDER_DENYLIST:
                    skipped_count += 1
                    skipped_item_count += 1
                    record_skipped_item(
                        db,
                        SkippedItemRecord(
                            owner_id=self.deps.owner_id,
                            run_source=RUN_SOURCE_GMAIL_SYNC,
                            run_key=run_key,
                            source_type="gmail",
                            reason_code="denylisted_sender_domain",
                            reason_detail=f"Skipped deterministic sender denylist match: {sender_domain}",
                            external_message_id=item["external_message_id"],
                            external_thread_id=item["external_thread_id"],
                            title_or_subject=item["subject"],
                            sender=item["sender"],
                            gate_action="skip",
                            gate_provider="sender_denylist",
                            gate_error=None,
                            source_group_name=trusted_group_context.group_name,
                            source_group_email=trusted_group_context.group_email,
                            source_group_match_method=trusted_group_context.match_method,
                            source_group_trusted=(
                                trusted_group_context.trusted if trusted_group_context.matched else False
                            ),
                        ),
                    )
                    report_item()
                    continue
                approved_learning_signals = approved_learning_signals_for_owner(db, self.deps.owner_id)
                intent_decision = self.deps.classify_email_intent(
                    sender=item["sender"],
                    subject=item["subject"],
                    body=item["body"],
                    snippet=item.get("snippet", ""),
                    recruiter_like=is_recruiter_like,
                    groq_enabled=bool(user_settings.feature_groq_job_parser_enabled),
                    trusted_group_context=trusted_group_context,
                    approved_learning_signals=approved_learning_signals,
                )
                if llm_decided(intent_decision.provider) and intent_decision.learned_signals:
                    record_pending_job_intent_learning(
                        db,
                        owner_id=self.deps.owner_id,
                        intent_type=intent_decision.intent_type,
                        confidence=intent_decision.confidence,
                        evidence=intent_decision.evidence,
                        negative_evidence=intent_decision.negative_evidence,
                        learned_signals=intent_decision.learned_signals,
                    )
                if intent_decision.action == "skip":
                    skipped_count += 1
                    skipped_item_count += 1
                    record_skipped_item(
                        db,
                        SkippedItemRecord(
                            owner_id=self.deps.owner_id,
                            run_source=RUN_SOURCE_GMAIL_SYNC,
                            run_key=run_key,
                            source_type="gmail",
                            reason_code=intent_decision.intent_type or "skip",
                            reason_detail=intent_decision.reason,
                            external_message_id=item["external_message_id"],
                            external_thread_id=item["external_thread_id"],
                            title_or_subject=item["subject"],
                            sender=item["sender"],
                            intent_type=intent_decision.intent_type,
                            intent_confidence=intent_decision.confidence,
                            intent_reason=intent_decision.reason,
                            intent_evidence=intent_decision.evidence,
                            intent_negative_evidence=intent_decision.negative_evidence,
                            gate_action=intent_decision.action,
                            gate_provider=intent_decision.provider,
                            gate_error=intent_decision.error,
                            source_group_name=trusted_group_context.group_name,
                            source_group_email=trusted_group_context.group_email,
                            source_group_match_method=trusted_group_context.match_method,
                            source_group_trusted=trusted_group_context.trusted if trusted_group_context.matched else False,
                        ),
                    )
                    report_item()
                    continue
                if recruiter_like_mode in {"block", "warn"} and not is_recruiter_like:
                    recruiter_like_warning = "non_recruiter_like_gmail"

                parse_body = prepare_gmail_parse_body(item["body"])
                manifest_result = self._detect_role_manifest_if_enabled(user_settings, parse_body)
                item_ai_extractor_enabled = user_settings.feature_ai_extractor_enabled and (
                    manifest_result is None or manifest_result.status != "multiple"
                )
                parsed, parser_details = self.deps.parse_email_with_details(
                    item["subject"],
                    parse_body,
                    source="gmail",
                    ai_extractor_enabled=item_ai_extractor_enabled,
                )
                hard_pass, hard_reason = self.deps.hard_filter_check(parsed, user_settings, effective_policy, parser_details)
                screening = CandidateScreeningService().evaluate_parser_details(parser_details, user_settings)
                if hard_pass and not screening.proceed_to_scoring:
                    assigned = assign_role(
                        extracted=str(parsed.get("role") or ""),
                        subject=str(item["subject"]),
                        body=str(item["body"]),
                        matcher=role_matcher_for(db, self.deps.owner_id),
                    )
                    email = RecruiterEmail(
                        owner_id=self.deps.owner_id,
                        sender=item["sender"],
                        subject=item["subject"],
                        body=item["body"],
                        role=assigned.role,
                        role_source=assigned.role_source,
                        role_canonical=assigned.role_canonical,
                        **role_family_fields(role=assigned.role, skills_text=str(parsed["skills_text"])),
                        salary_text=str(parsed["salary_text"]),
                        skills_text=str(parsed["skills_text"]),
                        skills_json=json.dumps(
                            build_skills_json_payload(
                                parser_details,
                                fallback_skills_text=str(parsed["skills_text"]),
                            ),
                            separators=(",", ":"),
                        ),
                        **fill_entity_gaps(jd_entity_fields_from_parsed(parsed), db=db, owner_id=self.deps.owner_id, subject=str(item["subject"]), location=str(parsed["location"]), body=str(item["body"])),
                        decision="Qualified",
                        state="needs_review",
                        decision_reason="strict_candidate_screening",
                        hard_filter_result=hard_reason,
                        approval_status="pending",
                        sent_status="not_sent",
                        source="gmail",
                        sync_batch_id=sync_batch_id,
                        content_dedupe_hash=content_hash,
                        external_message_id=item["external_message_id"],
                        external_thread_id=item["external_thread_id"],
                        external_rfc_message_id=item.get("external_rfc_message_id"),
                        gmail_received_at=item.get("gmail_received_at"),
                        recipient_email=item["recipient_email"],
                        parser_details_json=json.dumps(parser_details, separators=(",", ":")),
                    )
                    apply_screening_decision(email, screening)
                    self.deps.apply_gmail_label_for_email(email=email, candidate_item=item)
                    db.add(email)
                    if email.record_id is None:
                        candidate_record = opportunity_lineage_service.create_candidate_record(
                            db, owner_id=email.owner_id, origin_type="gmail"
                        )
                        email.record_id = candidate_record.id
                    imported_count += 1
                    report_item()
                    if manifest_result is not None:
                        self._expand_and_extract_children(db, email, manifest_result, user_settings)
                    continue

                content_insufficient = (
                    intent_decision.intent_type == "application_link_only"
                    or not has_job_description_content(
                        skills_text=str(parsed["skills_text"]),
                        structured_requirements=requirements_from_payload(parser_details.get("structured_requirements")),
                    )
                )
                if content_insufficient:
                    assigned = assign_role(
                        extracted=str(parsed.get("role") or ""),
                        subject=str(item["subject"]),
                        body=str(item["body"]),
                        matcher=role_matcher_for(db, self.deps.owner_id),
                    )
                    email = RecruiterEmail(
                        owner_id=self.deps.owner_id,
                        sender=item["sender"],
                        subject=item["subject"],
                        body=item["body"],
                        role=assigned.role,
                        role_source=assigned.role_source,
                        role_canonical=assigned.role_canonical,
                        **role_family_fields(role=assigned.role, skills_text=str(parsed["skills_text"])),
                        salary_text=str(parsed["salary_text"]),
                        skills_text=str(parsed["skills_text"]),
                        skills_json=json.dumps(
                            build_skills_json_payload(
                                parser_details,
                                fallback_skills_text=str(parsed["skills_text"]),
                            ),
                            separators=(",", ":"),
                        ),
                        **fill_entity_gaps(jd_entity_fields_from_parsed(parsed), db=db, owner_id=self.deps.owner_id, subject=str(item["subject"]), location=str(parsed["location"]), body=str(item["body"])),
                        decision="Qualified",
                        state="needs_review",
                        decision_reason="No job description content - only an application link/form was found.",
                        hard_filter_result=hard_reason,
                        qualification_result="qualified",
                        blocking_rule="no_job_description_content",
                        qualification_detail="The email points to an application portal/link with no described role content, so no draft was generated.",
                        sendability_status="content_insufficient",
                        approval_status="pending",
                        sent_status="not_sent",
                        source="gmail",
                        content_dedupe_hash=content_hash,
                        sync_batch_id=sync_batch_id,
                        external_message_id=item["external_message_id"],
                        external_thread_id=item["external_thread_id"],
                        external_rfc_message_id=item.get("external_rfc_message_id"),
                        gmail_received_at=item.get("gmail_received_at"),
                        recipient_email=item["recipient_email"],
                        intent_type=intent_decision.intent_type,
                        intent_confidence=intent_decision.confidence,
                        intent_reason=intent_decision.reason,
                        intent_evidence_json=json.dumps(intent_decision.evidence, separators=(",", ":")),
                        intent_negative_evidence_json=json.dumps(intent_decision.negative_evidence, separators=(",", ":")),
                        gate_action=intent_decision.action,
                        gate_provider=intent_decision.provider,
                        gate_error=intent_decision.error,
                        parser_details_json=json.dumps(parser_details, separators=(",", ":")),
                    )
                    self.deps.apply_gmail_label_for_email(email=email, candidate_item=item)
                    db.add(email)
                    if email.record_id is None:
                        candidate_record = opportunity_lineage_service.create_candidate_record(
                            db, owner_id=email.owner_id, origin_type="gmail"
                        )
                        email.record_id = candidate_record.id
                    imported_count += 1
                    report_item()
                    continue

                active_resume = self.deps.active_resume(db)
                resume_selection = self.deps.select_best_resume_match(
                    subject=item["subject"],
                    body=item["body"],
                    parsed=parsed,
                    parser_details=parser_details,
                    user_settings=user_settings,
                    email_row=None,
                    db=db,
                    owner_id=self.deps.owner_id,
                    external_thread_id=str(item.get("external_thread_id") or ""),
                )
                selected_resume = getattr(resume_selection, "resume", None) or active_resume
                ai_score = float(getattr(resume_selection, "ai_score", 0.0))
                ai_summary = str(getattr(resume_selection, "ai_summary", ""))
                ai_score_source = str(getattr(resume_selection, "ai_score_source", ""))
                ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
                ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
                ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
                ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
                resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
                resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
                resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
                resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
                email_embedding_json = cast(str | None, getattr(resume_selection, "email_embedding_json", None))
                resume_embedding_json = cast(str | None, getattr(resume_selection, "resume_embedding_json", None))
                semantic_diag = getattr(resume_selection, "semantic_diag", None)
                state = "needs_review"
                decision = "Qualified"
                decision_reason = "Qualified by hard filters + AI score"
                auto_reject_reason = None
                qualification_result = "qualified"
                blocking_rule: str | None = None
                qualification_detail = "Qualified for queue review."
                qualification_context: dict[str, object] | None = None
                draft = ""
                routed: RoutingResult | None = None
                warnings: list[str] = []
                if hard_pass and hard_reason.startswith("warnings: "):
                    warnings.append(hard_reason.removeprefix("warnings: ").strip())
                if recruiter_like_warning:
                    warnings.append(recruiter_like_warning)

                if not hard_pass:
                    state = "auto_rejected"
                    decision = "Reject"
                    blocking_rule, qualification_detail, qualification_context = describe_hard_filter_block(
                        parsed=parsed,
                        user_settings=user_settings,
                        effective_policy=effective_policy,
                        hard_reason=hard_reason,
                    )
                    decision_reason = qualification_detail
                    auto_reject_reason = hard_reason.removeprefix("blocked: ").strip()
                    qualification_result = "rejected"
                elif (
                    policy_service.draft_rule_mode(effective_policy, "score_threshold") == "block"
                    and ai_score < threshold
                ):
                    state = "auto_rejected"
                    decision = "Reject"
                    blocking_rule, qualification_detail, qualification_context = describe_score_threshold_block(
                        ai_score=ai_score,
                        threshold=threshold,
                    )
                    decision_reason = qualification_detail
                    auto_reject_reason = "ai_score_too_low"
                    qualification_result = "rejected"
                else:
                    if policy_service.draft_rule_mode(effective_policy, "score_threshold") == "warn" and ai_score < threshold:
                        warnings.append(f"score_below_threshold:{ai_score:.2f}<{threshold:.2f}")
                    blocked, block_reason = self.deps.policy_f2f_block(parsed, effective_policy, user_settings)
                    if blocked:
                        state = "auto_rejected"
                        decision = "Reject"
                        blocking_rule, qualification_detail, qualification_context = describe_f2f_block(block_reason=block_reason)
                        decision_reason = qualification_detail
                        auto_reject_reason = "f2f_non_texas"
                        qualification_result = "rejected"
                    elif block_reason:
                        warnings.append(block_reason)
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
                            resume_file_name=resolve_resume_display_name(
                                user_settings,
                                selected_resume.file_name if selected_resume else None,
                            ),
                        )
                        if warnings:
                            decision_reason = "Qualified by hard filters + AI score with warnings"
                if state == "needs_review":
                    qualification_result = "qualified"
                    blocking_rule = None
                    qualification_detail = "Qualified for queue review."
                    qualification_context = {"warnings": warnings}

                assigned = assign_role(
                    extracted=str(parsed.get("role") or ""),
                    subject=str(item["subject"]),
                    body=str(item["body"]),
                    matcher=role_matcher_for(db, self.deps.owner_id),
                )
                email = RecruiterEmail(
                    owner_id=self.deps.owner_id,
                    sender=item["sender"],
                    subject=item["subject"],
                    body=item["body"],
                    role=assigned.role,
                    role_source=assigned.role_source,
                    role_canonical=assigned.role_canonical,
                    **role_family_fields(role=assigned.role, skills_text=str(parsed["skills_text"])),
                    salary_text=str(parsed["salary_text"]),
                    skills_text=str(parsed["skills_text"]),
                    skills_json=json.dumps(
                        build_skills_json_payload(parser_details, fallback_skills_text=str(parsed["skills_text"])),
                        separators=(",", ":"),
                    ),
                    **fill_entity_gaps(jd_entity_fields_from_parsed(parsed), db=db, owner_id=self.deps.owner_id, subject=str(item["subject"]), location=str(parsed["location"]), body=str(item["body"])),
                    score=int(ai_score * 100),
                    decision=decision,
                    state=state,
                    decision_reason=decision_reason,
                    hard_filter_result=policy_service.combine_rule_messages(warnings),
                    auto_reject_reason=auto_reject_reason,
                    ai_score=ai_score,
                    ai_score_source=ai_score_source,
                    ai_summary=ai_summary,
                    ats_score=ats_score,
                    ats_score_source=ats_score_source,
                    ats_summary=ats_summary,
                    ats_breakdown_json=ats_breakdown_json,
                    resume_picker_score=resume_picker_score,
                    resume_picker_reason=resume_picker_reason,
                    resume_picker_candidates_json=resume_picker_candidates_json,
                    resume_picker_breakdown_json=resume_picker_breakdown_json,
                    semantic_input_source=getattr(semantic_diag, "input_source", None),
                    semantic_input_chars=getattr(semantic_diag, "input_chars", None),
                    semantic_chunks=getattr(semantic_diag, "chunks", None),
                    semantic_fallback_reason=getattr(semantic_diag, "fallback_reason", None),
                    keyword_source=getattr(semantic_diag, "keyword_source", None),
                    thread_snapshot_used=getattr(semantic_diag, "thread_snapshot_used", None),
                    thread_snapshot_email_id=getattr(semantic_diag, "thread_snapshot_email_id", None),
                    semantic_embedding=email_embedding_json,
                    intent_type=intent_decision.intent_type,
                    intent_confidence=intent_decision.confidence,
                    intent_reason=intent_decision.reason,
                    intent_evidence_json=json.dumps(intent_decision.evidence, separators=(",", ":")),
                    intent_negative_evidence_json=json.dumps(intent_decision.negative_evidence, separators=(",", ":")),
                    gate_action=intent_decision.action,
                    gate_provider=intent_decision.provider,
                    gate_error=intent_decision.error,
                    source_group_name=trusted_group_context.group_name,
                    source_group_email=trusted_group_context.group_email,
                    source_group_match_method=trusted_group_context.match_method,
                    source_group_trusted=trusted_group_context.trusted if trusted_group_context.matched else False,
                    qualification_result=qualification_result,
                    blocking_rule=blocking_rule,
                    qualification_detail=qualification_detail,
                    qualification_context_json=json.dumps(qualification_context or {}, separators=(",", ":")),
                    sync_batch_id=sync_batch_id,
                    draft_reply=draft,
                    draft_source="rules_only" if draft else None,
                    draft_model=self.deps.model_name if draft else None,
                    draft_ai_error=None,
                    draft_resume_context_status=RESUME_CONTEXT_RULES_ONLY if draft else None,
                    approval_status="pending",
                    sent_status="not_sent",
                    source="gmail",
                    content_dedupe_hash=content_hash,
                    external_message_id=item["external_message_id"],
                    external_thread_id=item["external_thread_id"],
                    external_rfc_message_id=item.get("external_rfc_message_id"),
                    gmail_received_at=item.get("gmail_received_at"),
                    recipient_email=item["recipient_email"],
                    resume_asset_id=selected_resume.id if selected_resume else None,
                    resume_file_name=selected_resume.file_name if selected_resume else None,
                    parser_details_json=json.dumps(parser_details, separators=(",", ":")),
                )
                if routed:
                    self.deps.apply_routing_result(email, routed)
                    email.routing_confirmed = False
                apply_screening_decision(email, screening)
                if email.state == "needs_review":
                    apply_resume_sendability(email)
                self.deps.apply_gmail_label_for_email(email=email, candidate_item=item)
                if selected_resume and resume_embedding_json and selected_resume.semantic_embedding != resume_embedding_json:
                    selected_resume.semantic_embedding = resume_embedding_json
                db.add(email)
                if email.record_id is None:
                    candidate_record = opportunity_lineage_service.create_candidate_record(
                        db, owner_id=email.owner_id, origin_type="gmail"
                    )
                    email.record_id = candidate_record.id
                imported_count += 1
                report_item()
                if manifest_result is not None:
                    self._expand_and_extract_children(db, email, manifest_result, user_settings)

            sync_run.imported_count = imported_count
            sync_run.skipped_count = skipped_count
            sync_run.error_count = error_count
            sync_run.ended_at = datetime.now(UTC)
            update_recent_run(
                recent_run,
                status="ok",
                detail=f"Imported: {imported_count} | Skipped: {skipped_count} | Errors: {error_count}",
                skipped_count=skipped_count,
                failed_count=error_count,
                skipped_item_count=skipped_item_count,
            )
            db.commit()
        except Exception:
            error_count += 1
            sync_run.error_count = error_count
            sync_run.ended_at = datetime.now(UTC)
            update_recent_run(
                recent_run,
                status="failed",
                detail="Gmail sync failed.",
                skipped_count=skipped_count,
                failed_count=error_count,
                skipped_item_count=skipped_item_count,
            )
            db.commit()
            raise

        self.deps.set_last_gmail_sync_at(datetime.now(UTC))
        self.deps.log_gmail_labeling_stats()
        response = GmailSyncResponse(
            sync_batch_id=sync_batch_id,
            run_key=run_key,
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

    def run_once(
        self,
        payload: AutomationRunRequest | None,
        db: Session,
        *,
        run_key_override: str | None = None,
        items_override: list[GmailMessageCandidate] | None = None,
    ) -> AutomationRunResponse:
        run_key = run_key_override or automation_run_key(str(uuid.uuid4()))
        recent_run = db.query(RecentRun).filter(RecentRun.run_key == run_key).first()
        if recent_run is None:
            recent_run = create_recent_run(
                db,
                owner_id=self.deps.owner_id,
                run_source=RUN_SOURCE_AUTOMATION,
                run_key=run_key,
                status="running",
                detail="Automation run started.",
            )
        else:
            recent_run.status = "running"
            recent_run.detail = "Automation run started."
        db.commit()
        if not self.deps.is_gmail_configured():
            raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
        configured, authenticated, detail = self.deps.gmail_auth_status()
        if configured and not authenticated:
            in_progress, last_error = self.deps.oauth_bootstrap_status()
            if in_progress:
                response = AutomationRunResponse(status="oauth_in_progress", detail="OAuth is in progress. Complete sign-in from backend logs, then retry Sync + Queue.", run_key=run_key)
                update_recent_run(recent_run, status=response.status, detail=response.detail)
                db.commit()
                self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
                self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
                return response
            if last_error:
                response = AutomationRunResponse(status="oauth_required", detail=f"OAuth required. Trigger Connect Gmail and complete sign-in. Last OAuth error: {last_error}", run_key=run_key)
                update_recent_run(recent_run, status=response.status, detail=response.detail)
                db.commit()
                self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
                self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
                return response
            response = AutomationRunResponse(status="oauth_required", detail=f"{detail} Click Connect Gmail, open the auth URL from backend logs, complete sign-in, then retry.", run_key=run_key)
            update_recent_run(recent_run, status=response.status, detail=response.detail)
            db.commit()
            self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status})
            self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
            self.deps.log_gmail_labeling_stats()
            return response

        user_settings = self.deps.get_settings(db)
        try:
            self._capture_inbound_replies(db, user_settings)
        except Exception:
            logger.exception("reply_capture_failed_during_automation_run")
        self._sync_label_tracking(db, user_settings)

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
        items = (
            items_override
            if items_override is not None
            else self.deps.list_unread_candidates_by_query(effective_query, max_results_per_page=batch_limit)[:batch_limit]
        )
        if not items:
            idle_detail = (
                "None of the selected messages could be retrieved from Gmail (they may have been deleted)."
                if items_override is not None
                else f"No unread matching emails found for query: {effective_query}"
            )
            response = self.deps.build_run_response(
                "idle",
                idle_detail,
                run_key=run_key,
                effective_query=effective_query,
                matched_count=0,
                queued_count=0,
                skipped_count=0,
                failed_count=0,
            )
            update_recent_run(
                recent_run,
                status=response.status,
                detail=response.detail,
                matched_count=0,
                queued_count=0,
                skipped_count=0,
                failed_count=0,
                skipped_item_count=0,
            )
            db.commit()
            self.deps.record_productivity_event(db, event_type="recent_run_recorded", event_source="run_once", metadata={"status": response.status, "matched_count": 0, "queued_count": 0, "skipped_count": 0, "failed_count": 0})
            self.deps.telegram_notify(self.deps.build_telegram_digest("Run Digest", response))
            self.deps.log_gmail_labeling_stats()
            return response

        if user_settings.feature_reply_inbox_enabled:
            # Candidates here come from the JD-scan query, which can also match a reply on
            # a thread we already have a conversation for (e.g. its subject still says "Java
            # Full Stack Developer"). Log it as a reply too rather than assuming thread
            # membership means it isn't also a genuine new requirement from that recruiter -
            # classification below still runs on every item regardless.
            owner_email = (user_settings.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL
            for item in items:
                try:
                    capture_inbound_reply(db, owner_id=self.deps.owner_id, item=item, owner_email=owner_email)
                except Exception:
                    logger.exception(
                        "reply_capture_failed_for_candidate_item external_message_id=%s",
                        item.get("external_message_id"),
                    )
            db.commit()

        capture_started = False
        if self.deps.embedding_latency_log_enabled():
            self.deps.begin_embedding_latency_capture()
            capture_started = True

        ai_state = {"ai_running": user_settings.feature_ai_enabled}
        self.deps.set_ai_runtime(ai_state)
        try:
            active_resume = self.deps.active_resume(db)
            enabled_resumes = self.deps.enabled_resumes(db)
            trusted_groups = self._load_enabled_requirement_groups(db) if user_settings.feature_gmail_requirement_groups_enabled else []
            run_orchestrator = RunOrchestrator()
            result = run_orchestrator.execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id=self.deps.owner_id,
                    items=items,
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=active_resume,
                    enabled_resumes=enabled_resumes,
                    effective_policy=effective_policy,
                    trusted_groups=trusted_groups,
                    threshold=threshold,
                    dry_run=dry_run,
                    model_name=self.deps.model_name,
                    run_source=RUN_SOURCE_AUTOMATION,
                    run_key=run_key,
                    deps=RunOrchestratorDependencies(
                        parse_email=self.deps.parse_email,
                        parse_email_with_details=self.deps.parse_email_with_details,
                        hard_filter_check=self.deps.hard_filter_check,
                        compute_blended_ai_score=lambda subject, body, parsed, user_settings, email_row, resume, db_ctx=None, owner_id_ctx=None, thread_id_ctx=None: self.deps.compute_blended_ai_score(
                            subject=subject,
                            body=body,
                            parsed=parsed,
                            user_settings=user_settings,
                            email_row=email_row,
                            resume=resume,
                            db=db_ctx if db_ctx is not None else db,
                            owner_id=owner_id_ctx if owner_id_ctx is not None else self.deps.owner_id,
                            external_thread_id=str(thread_id_ctx or getattr(email_row, "external_thread_id", "") or ""),
                        ),
                        policy_f2f_block=self.deps.policy_f2f_block,
                        evaluate_routing_policy=self.deps.evaluate_routing_policy,
                        greeting_from_to_contact=self.deps.greeting_from_to_contact,
                        build_user_fallback_draft=lambda db, user_settings, sender, role, parsed, greeting_line, resume_file_name: self.deps.build_user_fallback_draft(
                            db, user_settings, sender=sender, role=role, parsed=parsed, greeting_line=greeting_line, resume_file_name=resume_file_name
                        ),
                        generate_reply_with_ai_or_fallback=self.deps.generate_reply_with_ai_or_fallback,
                        apply_routing_decision=self.deps.apply_routing_decision,
                        select_best_resume_match=self.deps.select_best_resume_match,
                        capture_premium_numbers=self.deps.capture_premium_numbers,
                        record_productivity_event=self.deps.record_productivity_event,
                        apply_gmail_label=lambda _db, email, item: self.deps.apply_gmail_label_for_email(email=email, candidate_item=item),
                        mark_message_processed=self.deps.mark_message_processed,
                        is_recruiter_like=self.deps.is_recruiter_like,
                        classify_email_intent=self.deps.classify_email_intent,
                        record_skipped_item=lambda db_ctx, payload: record_skipped_item(db_ctx, payload),
                        regenerate_candidate=self.regenerate_candidate,
                        get_candidate=self._get_email_or_raise,
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
                    "semantic_input_source": getattr(result.last_email, "semantic_input_source", None) if result.last_email else None,
                    "semantic_input_chars": getattr(result.last_email, "semantic_input_chars", None) if result.last_email else None,
                    "semantic_chunks": getattr(result.last_email, "semantic_chunks", None) if result.last_email else None,
                    "semantic_fallback_reason": getattr(result.last_email, "semantic_fallback_reason", None) if result.last_email else None,
                    "keyword_source": getattr(result.last_email, "keyword_source", None) if result.last_email else None,
                    "thread_snapshot_used": getattr(result.last_email, "thread_snapshot_used", None) if result.last_email else None,
                    "thread_snapshot_email_id": getattr(result.last_email, "thread_snapshot_email_id", None) if result.last_email else None,
                }
            )

            retry_promoted_count = 0
            retry_skipped_count = 0
            if user_settings.feature_retry_queue and not dry_run:
                retry_promoted_count, retry_skipped_count = self._retry_failed_queue(db)

            auto_sent_count = 0
            auto_send_failed_count = 0
            if (
                user_settings.feature_auto_send
                and not user_settings.feature_role_manifest_enabled
                and not dry_run
                and result.queued_email_ids
            ):
                auto_sent_count, auto_send_failed_count = self._auto_send_newly_queued(
                    result.queued_email_ids, db
                )

            matched_label = "selected emails" if items_override is not None else "unread matching emails"
            if result.queued_count > 0:
                status = "ready"
                detail = f"Processed {result.matched_count} {matched_label}: queued={result.queued_count}, skipped={result.skipped_count}, failed={result.failed_count}."
            elif result.failed_count > 0:
                status = "failed"
                detail = f"Processed {result.matched_count} {matched_label}: queued=0, skipped={result.skipped_count}, failed={result.failed_count}."
            else:
                status = "skipped"
                detail = f"Processed {result.matched_count} {matched_label}: queued=0, skipped={result.skipped_count}, failed=0."
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
                run_key=run_key,
                effective_query=effective_query,
                matched_count=result.matched_count,
                queued_count=result.queued_count,
                skipped_count=result.skipped_count,
                failed_count=result.failed_count,
                auto_sent_count=auto_sent_count if user_settings.feature_auto_send else None,
                auto_send_failed_count=auto_send_failed_count if user_settings.feature_auto_send else None,
                retry_promoted_count=retry_promoted_count if user_settings.feature_retry_queue else None,
                retry_skipped_count=retry_skipped_count if user_settings.feature_retry_queue else None,
                queued_email_ids=result.queued_email_ids,
            )
            skipped_item_count = (
                db.query(func.count())
                .select_from(RecentRunSkippedItem)
                .filter(
                    RecentRunSkippedItem.owner_id == self.deps.owner_id,
                    RecentRunSkippedItem.run_key == run_key,
                )
                .scalar()
                or 0
            )
            update_recent_run(
                recent_run,
                status=response.status,
                detail=response.detail,
                matched_count=response.matched_count,
                queued_count=response.queued_count,
                skipped_count=response.skipped_count or 0,
                failed_count=response.failed_count or 0,
                skipped_item_count=int(skipped_item_count),
            )
            db.commit()
            self.deps.record_productivity_event(
                db,
                event_type="recent_run_recorded",
                event_source="run_once_retry" if items_override is not None else "run_once",
                entity_id=response.email_id,
                entity_type="RecruiterEmail" if response.email_id is not None else "",
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
        except Exception:
            skipped_item_count = (
                db.query(func.count())
                .select_from(RecentRunSkippedItem)
                .filter(
                    RecentRunSkippedItem.owner_id == self.deps.owner_id,
                    RecentRunSkippedItem.run_key == run_key,
                )
                .scalar()
                or 0
            )
            update_recent_run(
                recent_run,
                status="failed",
                detail="Automation run failed.",
                skipped_item_count=int(skipped_item_count),
            )
            db.commit()
            raise
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
                candidate = (
                    db.query(RecruiterEmail).filter(RecruiterEmail.id == email_id).first()
                    if hasattr(db, "query")
                    else None
                )
                if candidate is not None and candidate.is_multi_role_child:
                    raise HTTPException(status_code=400, detail="Multi-role children require manual approval")
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
                    entity_type="RecruiterEmail",
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
                    entity_type="RecruiterEmail",
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
                retry_status = resolve_sendability_status(email)
                retry_structural_block = retry_status in {
                    "source_parent",
                    "superseded_multi_role",
                    "manifest_review",
                    "extraction_review",
                }
                retry_strict_block = email.screening_mode == "strict" and retry_status != "sendable"
                retry_historical_safety_block = email.screening_mode is None and retry_status in {
                    "blocked_ineligible",
                    "eligibility_review",
                    "mandatory_resume_fail",
                    "mandatory_resume_review",
                }
                if retry_structural_block or retry_strict_block or retry_historical_safety_block:
                    retry_skipped_count += 1
                    continue
                try:
                    self.regenerate_candidate(email.id, RegenerateCandidateRequest(), db)
                    retry_promoted_count += 1
                    self.deps.record_productivity_event(
                        db,
                        event_type="needs_review_marked",
                        event_source="state",
                        entity_id=email.id,
                        entity_type="RecruiterEmail",
                        metadata={"source": "retry_queue"},
                    )
                except Exception:
                    db.rollback()
                    logger.exception("retry_failed_queue_regenerate_failed email_id=%s", email.id)
                    retry_skipped_count += 1
            else:
                retry_skipped_count += 1
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
        sendability_status = resolve_sendability_status(email)
        if email.sendability_status != sendability_status:
            email.sendability_status = sendability_status
            db.commit()
        structural_blocked = sendability_status in {
            "source_parent",
            "superseded_multi_role",
            "manifest_review",
            "extraction_review",
            "score_review",
        }
        strict_screening = email.screening_mode == "strict"
        persisted_safety_block = email.screening_mode is None and sendability_status in {
            "blocked_ineligible",
            "eligibility_review",
            "mandatory_resume_fail",
            "mandatory_resume_review",
        }
        if structural_blocked or persisted_safety_block or (strict_screening and sendability_status != "sendable"):
            raise HTTPException(status_code=400, detail=f"Candidate is not sendable: {sendability_status}")

        source_parent = None
        source_already_sent = False
        if email.source_parent_email_id:
            source_parent = db.query(RecruiterEmail).filter(RecruiterEmail.id == email.source_parent_email_id).first()
            sibling_sent = (
                db.query(RecruiterEmail.id)
                .filter(
                    RecruiterEmail.source_parent_email_id == email.source_parent_email_id,
                    RecruiterEmail.id != email.id,
                    RecruiterEmail.sent_status == "sent",
                )
                .first()
            )
            source_already_sent = bool(sibling_sent)
            if sibling_sent and not payload.confirm_same_source_additional_send:
                raise HTTPException(
                    status_code=409,
                    detail="Another role from this source was already sent; explicit confirmation is required.",
                )

        original_draft = email.draft_reply
        if payload.edited_reply:
            email.draft_reply = payload.edited_reply

        email.last_error = None
        sent_message_id = None
        sent_thread_id: str | None = None
        sent_rfc_message_id: str | None = None
        user_settings = self.deps.get_settings(db)
        tracking_token: str | None = None
        pixel_url: str | None = None
        if (
            user_settings.feature_email_tracking_enabled
            and app_settings.tracking_secret_key
            and app_settings.public_base_url
        ):
            candidate_token = generate_tracking_token(app_settings.tracking_secret_key, email.id)
            candidate_url = tracking_pixel_url(app_settings.public_base_url, candidate_token)
            if candidate_url:
                tracking_token = candidate_token
                pixel_url = candidate_url
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
        resume = None
        if email.resume_asset_id:
            resume = (
                db.query(ResumeAsset)
                .filter(ResumeAsset.owner_id == self.deps.owner_id, ResumeAsset.id == email.resume_asset_id)
                .first()
            )
        if resume is None:
            resume = self.deps.active_resume(db)
        if not resume:
            raise HTTPException(status_code=400, detail="No active resume uploaded")
        extra_attachments = self.deps.enabled_attachment_assets(db)
        if not resume.file_path or not os.path.exists(resume.file_path):
            raise HTTPException(status_code=400, detail=f"Resume file missing on disk: {resume.file_name}")
        missing_attachments = [item.file_name for item in extra_attachments if not item.file_path or not os.path.exists(item.file_path)]
        if missing_attachments:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment files missing on disk: {', '.join(missing_attachments)}",
            )
        attachments = [
            MailAttachment(
                path=resume.file_path,
                display_name=resolve_resume_display_name(user_settings, resume.file_name),
                mime_type=resume.mime_type,
            ),
            *[
                MailAttachment(path=item.file_path, display_name=item.file_name, mime_type=item.mime_type)
                for item in extra_attachments
            ],
        ]
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name
        # Stamped invisibly into the HTML part so a recruiter who phones about
        # "the resume you sent" can be traced back to an exact variant and send.
        variant_token = (
            resume_variant_token(resume.id, email.id)
            if user_settings.feature_resume_variant_marker_enabled
            else None
        )

        source_email = source_parent or email
        if email.source == "gmail":
            if not source_email.external_thread_id:
                raise HTTPException(status_code=400, detail="Missing Gmail metadata")
            try:
                sent_message_id = self.deps.send_reply_with_attachment(
                    source_email.external_thread_id,
                    email.recipient_email,
                    email.cc_email,
                    email.subject,
                    email.draft_reply,
                    draft_text_size=user_settings.draft_text_size,
                    attachments=attachments,
                    tracking_pixel_url=pixel_url,
                    variant_token=variant_token,
                )
                sent_thread_id = source_email.external_thread_id
                if source_email.external_message_id and not source_already_sent:
                    self.deps.mark_message_processed(source_email.external_message_id)
            except Exception as exc:
                email.last_error = str(exc)
                db.commit()
                db.refresh(email)
                raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc
        # A pasted requirement has no thread to reply to, so it sends the same
        # way an Nvoids listing does: a new message to the recruiter address the
        # extraction found.
        elif email.source in {"nvoids", "manual"}:
            try:
                sent_message_id = self.deps.send_new_email_with_attachment(
                    email.recipient_email,
                    email.cc_email,
                    email.subject,
                    email.draft_reply,
                    draft_text_size=user_settings.draft_text_size,
                    attachments=attachments,
                    tracking_pixel_url=pixel_url,
                    variant_token=variant_token,
                )
                if (
                    user_settings.feature_reply_inbox_enabled
                    and sent_message_id
                    and self.deps.get_message_thread_id is not None
                ):
                    try:
                        sent_thread_id = self.deps.get_message_thread_id(sent_message_id)
                    except Exception:
                        logger.exception("gmail_sent_thread_lookup_failed message_id=%s", sent_message_id)
                sent_thread_id = sent_thread_id or sent_message_id
            except Exception as exc:
                email.last_error = str(exc)
                db.commit()
                db.refresh(email)
                raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

        if (
            user_settings.feature_reply_inbox_enabled
            and sent_message_id
            and self.deps.get_message_rfc_message_id is not None
        ):
            try:
                sent_rfc_message_id = self.deps.get_message_rfc_message_id(sent_message_id)
            except Exception:
                logger.exception("gmail_sent_rfc_lookup_failed message_id=%s", sent_message_id)

        email.state = "approved_sent"
        email.decision = "Qualified"
        email.approval_status = "approved"
        email.sent_status = "sent"
        email.sent_at = datetime.now(UTC)
        email.gmail_sent_id = sent_message_id
        email.tracking_token = tracking_token
        email.sent_attachment_file_names_json = json.dumps(
            [item.file_name for item in extra_attachments],
            separators=(",", ":"),
        )
        if payload.edited_reply and payload.edited_reply.strip() != original_draft.strip():
            db.add(DraftEditFeedback(owner_id=self.deps.owner_id, recruiter_email_id=email.id, original_draft=original_draft, edited_draft=payload.edited_reply))
        if user_settings.feature_reply_inbox_enabled and sent_thread_id:
            ensure_sent_conversation(
                db,
                owner_id=self.deps.owner_id,
                root_email=email,
                thread_id=sent_thread_id,
                sent_rfc_message_id=sent_rfc_message_id,
            )
        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(db, event_type="approved_sent", event_source="action", entity_id=email.id, entity_type="RecruiterEmail", metadata={"state": email.state, "sent_status": email.sent_status})

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

        if email.marked_for_tracking:
            try:
                result = appts_service.create_tracked_application_from_email(
                    db, email, owner_id=self.deps.owner_id,
                )
                db.commit()
                if result and result[1]:
                    appts_service.enqueue_embedding_generation(result[0].id)
            except Exception as exc:
                db.rollback()
                logger.warning("Failed to create tracked AppTS application for email_id=%s: %s", email.id, exc)

        return email

    def list_inbox_conversations(self, db: Session, **filters: object) -> list[ConversationSummaryResponse]:
        return list_conversations(db, self.deps.owner_id, **filters)

    def refresh_inbox_replies(self, db: Session, **filters: object) -> list[ConversationSummaryResponse]:
        """Cheap reply-only refresh for the inbox refresh icon: no candidate import/scoring/queueing."""
        if not self.deps.is_gmail_configured():
            raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
        user_settings = self.deps.get_settings(db)
        if not user_settings.enabled:
            raise HTTPException(status_code=400, detail="Pipeline is disabled in settings")
        # Guarded the same way the automation run guards it, and for a sharper
        # reason: label tracking is a second, independent source of threads, and
        # letting the reply scan's failure return before it runs means one Gmail
        # rate limit silently costs the user every labeled thread too. Gmail
        # answers this account's reply scan with 403 rateLimitExceeded often
        # enough that the label half never ran at all.
        reply_error: Exception | None = None
        try:
            self._capture_inbound_replies(db, user_settings)
        except Exception as exc:
            db.rollback()
            reply_error = exc
            logger.exception("reply_capture_failed_during_inbox_refresh")
        self._sync_label_tracking(db, user_settings)
        # Raised after label tracking, never instead of it: the labeled threads
        # are captured and committed by the time this fires, so the retry the
        # user makes is only for the half that actually failed. Silence here is
        # what let a systematic Gmail failure look like an empty inbox.
        if reply_error is not None:
            raise HTTPException(status_code=502, detail="Gmail rejected the reply scan; labeled threads were still refreshed") from reply_error
        return list_conversations(db, self.deps.owner_id, **filters)

    def get_inbox_conversation(self, conversation_id: int, db: Session) -> ConversationDetailResponse:
        return conversation_detail(db, self.deps.owner_id, conversation_id)

    def mark_inbox_conversation_read(self, conversation_id: int, db: Session) -> ConversationDetailResponse:
        return mark_conversation_read(db, self.deps.owner_id, conversation_id)

    def reply_to_inbox_conversation(
        self,
        conversation_id: int,
        body: str,
        db: Session,
    ) -> ConversationDetailResponse:
        user_settings = self.deps.get_settings(db)
        detail = conversation_detail(db, self.deps.owner_id, conversation_id)
        root_email = self._get_email_or_raise(db, detail.root_recruiter_email_id) if detail.root_recruiter_email_id is not None else None
        pixel_url = None
        if user_settings.feature_email_tracking_enabled and root_email is not None:
            pixel_url = tracking_pixel_url(app_settings.public_base_url, root_email.tracking_token)
        return send_conversation_reply(
            db,
            owner_id=self.deps.owner_id,
            conversation_id=conversation_id,
            body=body,
            sender=user_settings.signature_email or "me",
            draft_text_size=user_settings.draft_text_size,
            tracking_url=pixel_url,
            send_reply=self.deps.send_reply_with_attachment,
        )

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
        email.marked_for_tracking = False
        record = opportunity_lineage_service.get_record(
            db,
            owner_id=self.deps.owner_id,
            record_id=email.record_id or "",
        )
        lineage = db.get(OpportunityLineage, record.internal_lineage_id) if record and record.internal_lineage_id else None
        if lineage is not None:
            opportunity_lineage_service.record_event(
                db,
                lineage_id=lineage.id,
                event_type="rejected",
                process_name="orchestration_service",
                related_record_type="RecruiterEmail",
                related_record_id=email.id,
            )
            lineage.current_status = "closed"
            lineage.closed_at = datetime.now(UTC)
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

        prior_status = email.routing_status
        email.state = "failed"
        email.routing_confirmed = False
        if prior_status not in {"safe", "confirmed"}:
            email.routing_status = "ambiguous"
            email.routing_confidence = min(float(email.routing_confidence or 0.0), 0.5)
        email.routing_reason = f"Manually moved to failed mapping for recipient remap (prior routing: {prior_status})."
        email.last_error = "Recipient mapping flagged for manual remap"
        email.skip_reason = "manual_failed_mapping"
        email.decision_reason = "Moved to failed mapping by user"
        email.approval_status = "pending"
        email.sent_status = "not_sent"
        email.marked_for_tracking = False
        record = opportunity_lineage_service.get_record(
            db,
            owner_id=self.deps.owner_id,
            record_id=email.record_id or "",
        )
        if record and record.internal_lineage_id:
            opportunity_lineage_service.record_event(
                db,
                lineage_id=record.internal_lineage_id,
                event_type="failed_mapping",
                process_name="orchestration_service",
                related_record_type="RecruiterEmail",
                related_record_id=email.id,
            )
        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(db, event_type="failed_mapping_marked", event_source="action", entity_id=email.id, entity_type="RecruiterEmail", metadata={"source": "manual_move_to_failed_mapping", "prior_routing_status": prior_status})
        return email

    def set_tracking(self, email_id: int, tracked: bool, db: Session) -> RecruiterEmail:
        email = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id).first()
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if email.state != "needs_review":
            raise HTTPException(status_code=400, detail="Only needs_review candidates can be tracked/untracked")
        email.marked_for_tracking = tracked
        db.commit()
        db.refresh(email)
        return email

    def regenerate_candidate(self, email_id: int, payload: RegenerateCandidateRequest, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if self.deps.is_terminal_state(email):
            raise HTTPException(status_code=400, detail="Candidate is in terminal state")
        if email.state not in {"needs_review", "failed"}:
            raise HTTPException(status_code=400, detail="Only needs_review or failed candidates can be regenerated")

        user_settings = self.deps.get_settings(db)
        resolved = self.deps.effective_run_inputs(user_settings, None)
        effective_policy = resolved.policy
        threshold = self.deps.policy_threshold(user_settings, effective_policy)
        active_resume = self.deps.active_resume(db)
        enabled_resumes = self.deps.enabled_resumes(db)

        parse_subject = email.subject
        parse_body = prepare_gmail_parse_body(email.body) if email.source == "gmail" else email.body
        parser_source = "gmail" if email.source == "gmail" else email.source
        parse_kwargs: dict[str, Any] = {
            "source": parser_source,
            "ai_extractor_enabled": user_settings.feature_ai_extractor_enabled,
        }
        external = None
        external_context_warning: str | None = None
        if email.source == "nvoids":
            external = self._load_external_opportunity(db, email)
            if external and external.raw_html:
                detail = parse_nvoids_detail(external.raw_html, external.role or parse_subject, external.location or "")
                parse_kwargs["ai_body_override"] = detail.jd_body or ""
                parse_kwargs["source_hints"] = {
                    "canonical_title": external.role,
                    "canonical_location": external.location,
                    "company": external.company,
                    "work_mode": external.work_mode,
                    "visa_hints": external.visa_hints,
                    "ai_input_source": detail.jd_body_source or "",
                    "ai_input_chars": len(detail.jd_body or ""),
                }
                parse_body = external.raw_body or email.body
                parse_subject = external.role or email.subject
            else:
                external_context_warning = "Nvoids source context unavailable during regenerate; used stored candidate content."

        parsed, parser_details = self.deps.parse_email_with_details(parse_subject, parse_body, **parse_kwargs)
        inherited_constraints: list[dict[str, object]] = []
        try:
            raw_inherited = json.loads(email.inherited_constraints_json or "[]")
            if isinstance(raw_inherited, list):
                inherited_constraints = [item for item in raw_inherited if isinstance(item, dict)]
        except (TypeError, ValueError):
            inherited_constraints = []
        screening = CandidateScreeningService().evaluate_parser_details(
            parser_details,
            user_settings,
            inherited_constraints=inherited_constraints,
        )
        if not screening.proceed_to_scoring:
            apply_role_assignment(
                email,
                extracted=parsed.get("role"),
                subject=parse_subject,
                body=email.body,
                matcher=role_matcher_for(db, self.deps.owner_id),
            )
            email.location = str(parsed.get("location") or email.location or "")
            email.salary_text = str(parsed.get("salary_text") or email.salary_text or "")
            email.skills_text = str(parsed.get("skills_text") or email.skills_text or "")
            apply_role_family(email, role=email.role, skills_text=email.skills_text)
            for jd_field, jd_value in jd_entity_fields_from_parsed(parsed).items():
                setattr(email, jd_field, jd_value if jd_value is not None else getattr(email, jd_field))
            email.parser_details_json = json.dumps(parser_details, separators=(",", ":"))
            email.skills_json = json.dumps(
                build_skills_json_payload(parser_details, fallback_skills_text=email.skills_text),
                separators=(",", ":"),
            )
            email.state = "needs_review"
            email.decision = "Qualified"
            email.decision_reason = "strict_candidate_screening"
            email.approval_status = "pending"
            email.sent_status = "not_sent"
            apply_screening_decision(email, screening)
            db.commit()
            db.refresh(email)
            return email
        resume_selection = self.deps.select_best_resume_match(
            subject=parse_subject,
            body=parse_body,
            parsed=parsed,
            parser_details=parser_details,
            user_settings=user_settings,
            email_row=email,
            resumes=enabled_resumes,
            fallback_resume=active_resume,
            db=db,
            owner_id=self.deps.owner_id,
            external_thread_id=email.external_thread_id,
        )
        selected_resume = getattr(resume_selection, "resume", None) or active_resume
        ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
        ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
        ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
        ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
        resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
        resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
        resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
        resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))

        existing_routing_usable = bool((email.recipient_email or "").strip() and (email.cc_email or "").strip())
        # Only ever preserve routing that a human actually set via the resolve-recipients endpoint
        # (routing_evidence source="manual_edit"). `routing_confirmed` alone isn't a safe signal for
        # that: it also gets set as a side effect of a prior successful auto-derived routing pass
        # (see below), which previously made system-derived CC values -- including stale/incorrect
        # ones -- stick forever across every subsequent regenerate.
        manually_confirmed_routing = self._has_manual_routing_edit(email)
        routing_decision = None
        if payload.preserve_manual_routing and email.routing_confirmed and manually_confirmed_routing:
            routing_decision = self._manual_routing_decision(email)
        elif email.source == "nvoids" and existing_routing_usable and manually_confirmed_routing:
            routing_decision = self._manual_routing_decision(email)

        preparation = prepare_candidate_for_queue(
            QueuePreparationRequest(
                db=db,
                owner_id=self.deps.owner_id,
                sender=email.sender,
                subject=parse_subject,
                body=parse_body,
                snippet=parse_body,
                user_settings=user_settings,
                effective_policy=effective_policy,
                threshold=threshold,
                model_name=self.deps.model_name,
                scoring_resume=selected_resume,
                draft_resume=selected_resume,
                existing_email=email,
                external_thread_id=email.external_thread_id,
                routing_decision=routing_decision,
                parsed_overrides=dict(parsed),
                parser_details=parser_details,
            ),
            QueuePreparationDependencies(
                parse_email=self.deps.parse_email,
                hard_filter_check=self.deps.hard_filter_check,
                compute_blended_ai_score=lambda subject, body, parsed, user_settings, email_row, resume, db_ctx=None, owner_id_ctx=None, thread_id_ctx=None: self.deps.compute_blended_ai_score(
                    subject=subject,
                    body=body,
                    parsed=parsed,
                    user_settings=user_settings,
                    email_row=email_row,
                    resume=resume,
                    db=db_ctx,
                    owner_id=owner_id_ctx,
                    external_thread_id=str(thread_id_ctx or ""),
                ),
                policy_f2f_block=self.deps.policy_f2f_block,
                evaluate_routing_policy=self.deps.evaluate_routing_policy,
                greeting_from_to_contact=self.deps.greeting_from_to_contact,
                build_user_fallback_draft=lambda db_ctx, settings_ctx, sender, role, parsed_ctx, greeting_line, resume_file_name: self.deps.build_user_fallback_draft(
                    db_ctx,
                    settings_ctx,
                    sender=sender,
                    role=role,
                    parsed=parsed_ctx,
                    greeting_line=greeting_line,
                    resume_file_name=resume_file_name,
                ),
                generate_reply_with_ai_or_fallback=lambda **kwargs: self.deps.generate_reply_with_ai_or_fallback(**kwargs),
            ),
        )

        draft_reply = preparation.draft_reply or ""
        draft_source = preparation.draft_source
        draft_model = preparation.draft_model
        draft_ai_error = preparation.draft_ai_error
        draft_resume_context_status = preparation.draft_resume_context_status
        score_review_draft_created = False
        if (
            email.source == "gmail"
            and preparation.outcome == "not_qualified"
            and preparation.blocking_rule == "score_threshold"
            and payload.preserve_review_visibility
            and not draft_reply
        ):
            greeting_line = self.deps.greeting_from_to_contact(email.recipient_email or "", parse_body)
            draft_reply = self.deps.build_user_fallback_draft(
                db,
                user_settings,
                sender=email.sender,
                role=str(preparation.parsed.get("role") or email.role or parse_subject),
                parsed=preparation.parsed,
                greeting_line=greeting_line,
                resume_file_name=selected_resume.file_name if selected_resume else None,
            )
            draft_source = "rules_only"
            draft_model = None
            draft_ai_error = None
            draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY
            score_review_draft_created = True

        apply_role_assignment(
            email,
            extracted=preparation.parsed.get("role"),
            subject=parse_subject,
            body=email.body,
            matcher=role_matcher_for(db, self.deps.owner_id),
        )
        email.location = str(preparation.parsed.get("location", email.location or ""))
        email.salary_text = str(preparation.parsed.get("salary_text", email.salary_text or ""))
        email.skills_text = str(preparation.parsed.get("skills_text", email.skills_text or ""))
        # After role and skills are both refreshed above, so a re-process reclassifies
        # against what the row now says rather than what it said when first ingested.
        apply_role_family(email, role=email.role, skills_text=email.skills_text)
        for jd_field, jd_value in jd_entity_fields_from_parsed(preparation.parsed).items():
            setattr(email, jd_field, jd_value if jd_value is not None else getattr(email, jd_field))
        email.skills_json = json.dumps(
            build_skills_json_payload(parser_details, fallback_skills_text=email.skills_text),
            separators=(",", ":"),
        )
        email.parser_details_json = json.dumps(parser_details, separators=(",", ":"))
        email.score = int(preparation.ai_score * 100)
        email.ai_score = preparation.ai_score
        email.ai_score_source = preparation.ai_score_source
        email.ai_summary = preparation.ai_summary
        email.ats_score = ats_score
        email.ats_score_source = ats_score_source
        email.ats_summary = ats_summary
        email.ats_breakdown_json = ats_breakdown_json
        email.resume_picker_score = resume_picker_score
        email.resume_picker_reason = resume_picker_reason
        email.resume_picker_candidates_json = resume_picker_candidates_json
        email.resume_picker_breakdown_json = resume_picker_breakdown_json
        email.semantic_embedding = preparation.email_embedding_json
        email.semantic_input_source = getattr(preparation.semantic_diag, "input_source", None)
        email.semantic_input_chars = getattr(preparation.semantic_diag, "input_chars", None)
        email.semantic_chunks = getattr(preparation.semantic_diag, "chunks", None)
        email.semantic_fallback_reason = getattr(preparation.semantic_diag, "fallback_reason", None)
        email.keyword_source = getattr(preparation.semantic_diag, "keyword_source", None)
        email.thread_snapshot_used = getattr(preparation.semantic_diag, "thread_snapshot_used", None)
        email.thread_snapshot_email_id = getattr(preparation.semantic_diag, "thread_snapshot_email_id", None)
        email.draft_reply = draft_reply
        email.draft_source = draft_source
        email.draft_model = draft_model
        email.draft_ai_error = draft_ai_error
        email.draft_resume_context_status = draft_resume_context_status
        email.resume_asset_id = selected_resume.id if selected_resume else None
        email.resume_file_name = selected_resume.file_name if selected_resume else None
        email.hard_filter_result = preparation.hard_filter_reason
        email.skip_reason = preparation.skip_reason
        email.auto_reject_reason = preparation.auto_reject_reason

        if selected_resume and preparation.resume_embedding_json and selected_resume.semantic_embedding != preparation.resume_embedding_json:
            selected_resume.semantic_embedding = preparation.resume_embedding_json

        if preparation.routing_decision is not None:
            self.deps.apply_routing_decision(email, preparation.routing_decision)
        email.routing_confirmed = bool(routing_decision is not None and payload.preserve_manual_routing and not routing_decision.should_mark_failed)

        email.qualification_result = preparation.qualification_result
        email.blocking_rule = preparation.blocking_rule
        email.qualification_detail = preparation.qualification_detail

        if preparation.outcome == "routing_failed":
            email.state = "failed"
            email.decision = "Qualified"
            email.decision_reason = preparation.decision_reason
            email.last_error = external_context_warning or "Recipient routing unresolved during regenerate."
        elif preparation.outcome == "not_qualified" and payload.preserve_review_visibility:
            email.state = "needs_review"
            email.decision = "Reject"
            email.decision_reason = preparation.decision_reason
            email.last_error = external_context_warning or "Regenerated candidate is no longer qualified for auto-reply."
        elif preparation.outcome == "not_qualified":
            email.state = "auto_rejected"
            email.decision = "Reject"
            email.decision_reason = preparation.decision_reason
            email.last_error = external_context_warning
        else:
            email.state = "needs_review"
            email.decision = "Qualified"
            email.decision_reason = preparation.decision_reason
            email.last_error = external_context_warning

        email.approval_status = "pending"
        email.sent_status = "not_sent"
        apply_screening_decision(email, screening)
        if score_review_draft_created:
            email.sendability_status = "score_review"
        apply_resume_sendability(email)

        db.commit()
        db.refresh(email)
        self.deps.record_productivity_event(
            db,
            event_type="needs_review_marked" if email.state == "needs_review" else "failed_mapping_marked",
            event_source="action",
            entity_id=email.id,
            entity_type="RecruiterEmail",
            metadata={"source": "regenerate_candidate", "outcome": preparation.outcome},
        )
        return email

    def dismiss_failed_candidate(self, email_id: int, db: Session) -> dict[str, int | bool | str]:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if email.state != "failed":
            raise HTTPException(status_code=400, detail="Only failed candidates can be dismissed")

        email.state = "dismissed"
        email.decision_reason = "Dismissed from failed mapping by user"
        email.skip_reason = "failed_mapping_dismissed"
        email.last_error = "Failed mapping card dismissed by user"
        db.commit()
        self.deps.record_productivity_event(
            db,
            event_type="failed_mapping_dismissed",
            event_source="action",
            entity_id=email.id,
            entity_type="RecruiterEmail",
            metadata={"source": "failed_mapping_delete_button"},
        )
        return {"id": email.id, "deleted": True, "state": email.state}

    def resolve_recipients(self, email_id: int, payload: ResolveRecipientsRequest, db: Session) -> RecruiterEmail:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if email.state != "failed":
            raise HTTPException(status_code=400, detail="Only failed candidates can have recipients resolved")

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
        parser_details = json.loads(email.parser_details_json or "{}")
        if not isinstance(parser_details, dict):
            parser_details = {}
        screening = CandidateScreeningService().evaluate_parser_details(parser_details, user_settings)
        apply_screening_decision(email, screening)
        if not screening.proceed_to_scoring:
            db.commit()
            db.refresh(email)
            return email
        resume_selection = self.deps.select_best_resume_match(
            subject=email.subject,
            body=email.body,
            parsed=parsed,
            parser_details=email.parser_details_json and json.loads(email.parser_details_json),
            user_settings=user_settings,
            email_row=email,
            db=db,
            owner_id=self.deps.owner_id,
            external_thread_id=email.external_thread_id,
        )
        resume = getattr(resume_selection, "resume", None) or self.deps.active_resume(db)
        email.ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
        email.ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
        email.ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
        email.ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
        email.resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
        email.resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
        email.resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
        email.resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
        fallback_reply = self.deps.build_user_fallback_draft(
            db,
            user_settings,
            sender=email.sender,
            role=role,
            parsed=parsed,
            greeting_line=greeting_line,
            resume_file_name=resolve_resume_display_name(
                user_settings,
                resume.file_name if resume else email.resume_file_name,
            ),
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

        apply_role_assignment(
            email,
            extracted=role,
            subject=email.subject,
            body=email.body,
            matcher=role_matcher_for(db, self.deps.owner_id),
        )
        email.location = str(parsed["location"])
        email.salary_text = str(parsed["salary_text"])
        email.skills_text = str(parsed["skills_text"])
        apply_role_family(email, role=email.role, skills_text=email.skills_text)
        email.draft_reply = reply
        email.draft_source = draft_source
        email.draft_model = draft_model
        email.draft_ai_error = draft_ai_error
        email.draft_resume_context_status = draft_resume_context_status
        apply_screening_decision(email, screening)
        apply_resume_sendability(email)

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
        self.deps.record_productivity_event(db, event_type="needs_review_marked", event_source="state", entity_id=email.id, entity_type="RecruiterEmail", metadata={"source": "resolve_recipients"})
        return email
