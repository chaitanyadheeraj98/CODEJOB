from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import re
from typing import cast

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.automation.queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)
from app.models import EmployerNumber, NumberReviewQueue, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, ResumeAsset, UserSettings
from app.parsing import build_skills_json_payload
from app.premium_numbers.phone_normalization import best_display_phone, canonicalize_phone
from app.phase0 import (
    extract_email_address,
    greeting_from_to_contact,
    hard_filter_check,
    parse_email,
    parse_email_with_details,
    should_block_f2f,
)
from app.routing import RoutingDecision
from app.semantic.embeddings_service import generate_embedding
from app.services import policy_service
from app.services.candidate_runtime_service import CandidateRuntimeDeps, CandidateRuntimeService
from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService

from .collector import NvoidsCollector
from .dedupe import build_dedupe_hash
from .models import ExternalFeedSource, ExternalOpportunity, ExternalScrapeRun
from .parser import parse_external_post, parse_job_detail_contacts, parse_listing_rows, parse_nvoids_detail
from .types import ExternalFeedSyncResult


logger = logging.getLogger(__name__)


class ExternalFeedService:
    def __init__(self) -> None:
        self.collector = NvoidsCollector()
        self.default_query = "(tx or texas) and java and spring* not(*js)"
        self.default_hotlist_mode = "Exclude Hotlists"
        self.scoring_runtime = ScoringRuntimeService(
            ScoringRuntimeDeps(generate_embedding_with_health=lambda text: generate_embedding(text))
        )
        self.candidate_runtime = CandidateRuntimeService(
            CandidateRuntimeDeps(
                get_settings=lambda _db: UserSettings(owner_id="default-owner"),
                evaluate_routing_policy=lambda *_args, **_kwargs: self._fallback_routing_decision(),
                apply_routing_decision=lambda *_args, **_kwargs: None,
            )
        )

    @staticmethod
    def _normalize_location_tokens(raw_locations: list[str] | tuple[str, ...] | None) -> list[str]:
        if not raw_locations:
            return []
        normalized: list[str] = []
        for value in raw_locations:
            token = str(value or "").strip().lower()
            if not token or token in normalized:
                continue
            normalized.append(token)
        return normalized

    def build_nvoids_query(self, raw_locations: list[str] | tuple[str, ...] | None) -> str:
        locations = self._normalize_location_tokens(raw_locations)
        if not locations:
            return self.default_query
        location_clause = " or ".join(locations)
        return f"({location_clause}) and java and spring* not(*js)"

    def row_matches_locations(self, row_location: str, raw_locations: list[str] | tuple[str, ...] | None) -> bool:
        locations = self._normalize_location_tokens(raw_locations)
        if not locations:
            return True
        normalized_row_location = str(row_location or "").strip().lower()
        if not normalized_row_location:
            return False
        for token in locations:
            if token == "remote":
                if re.search(r"\bremote\b", normalized_row_location):
                    return True
                continue
            if token in normalized_row_location:
                return True
        return False

    def ensure_nvoids_source(self, db: Session, *, owner_id: str) -> ExternalFeedSource:
        row = (
            db.query(ExternalFeedSource)
            .filter(ExternalFeedSource.owner_id == owner_id, ExternalFeedSource.source_type == "nvoids")
            .first()
        )
        if row:
            return row
        row = ExternalFeedSource(
            owner_id=owner_id,
            source_type="nvoids",
            base_url="https://www.nvoids.com",
            enabled=True,
            poll_interval_minutes=45,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def sync_nvoids(
        self,
        db: Session,
        *,
        owner_id: str,
        max_pages: int = 3,
        max_items: int = 10,
        duplicate_stop_threshold: int = 2,
    ) -> ExternalFeedSyncResult:
        source = self.ensure_nvoids_source(db, owner_id=owner_id)
        user_settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
            or UserSettings(owner_id=owner_id)
        )
        location_filters = self._normalize_location_tokens((user_settings.nvoids_locations or "").split(","))
        query = self.build_nvoids_query(location_filters)
        run = ExternalScrapeRun(owner_id=owner_id, source_type="nvoids", started_at=datetime.now(UTC), notes="")
        db.add(run)
        db.commit()
        db.refresh(run)

        fetched_count = 0
        created_count = 0
        deduped_count = 0
        failed_count = 0
        skipped_location_count = 0
        consecutive_duplicate_pages = 0
        enqueue_attempts = 0
        enqueue_successes = 0

        logger.info(
            "nvoids_sync_start owner_id=%r max_pages=%s max_items=%s query=%r locations=%s semantic_enabled=%s ai_enabled=%s threshold=%s",
            owner_id,
            max_pages,
            max_items,
            query,
            location_filters,
            getattr(user_settings, "feature_semantic_enabled", None),
            getattr(user_settings, "feature_ai_enabled", None),
            getattr(user_settings, "qualification_threshold", None),
        )

        try:
            for page in range(max_pages):
                collected = self.collector.fetch_search_page(
                    query=query,
                    hotlist_mode=self.default_hotlist_mode,
                    page=page,
                )
                rows = parse_listing_rows(collected.html, collected.url)
                logger.info("nvoids_sync_page_loaded page=%s rows=%s url=%r", page, len(rows), collected.url)
                if not rows:
                    break
                page_deduped = 0
                for row in rows:
                    if created_count >= max_items:
                        break
                    fetched_count += 1
                    if not self.row_matches_locations(row.location, location_filters):
                        skipped_location_count += 1
                        logger.info(
                            "nvoids_sync_skip_location page=%s title=%r location=%r allowed_locations=%s",
                            page,
                            row.title,
                            row.location,
                            location_filters,
                        )
                        continue
                    detail_html = ""
                    detail_url = row.href
                    recruiter_email = ""
                    recruiter_phone = ""
                    recruiter_name = ""
                    fallback_used = False
                    try:
                        detail_page = self.collector.fetch_detail_page(url=row.href)
                        detail_html = detail_page.html
                        detail_url = detail_page.url
                    except Exception:
                        # Keep ingestion resilient: listing row still ingests even if one detail page fails.
                        failed_count += 1
                        fallback_used = True
                        logger.warning(
                            "nvoids_sync_row_detail_fetch_failed page=%s title=%r href=%r",
                            page,
                            row.title,
                            row.href,
                        )
                    try:
                        if detail_html.strip():
                            recruiter_email, recruiter_phone, recruiter_name = parse_job_detail_contacts(detail_html)
                        else:
                            fallback_used = True
                        parsed = parse_external_post(
                            source_type="nvoids",
                            source_url=detail_url,
                            title=row.title,
                            location=row.location,
                            posted_text=row.posted_text,
                            raw_body=detail_html or "",
                            raw_html=detail_html or "",
                        )
                    except Exception:
                        failed_count += 1
                        logger.exception(
                            "nvoids_sync_row_parse_failed page=%s title=%r href=%r detail_html_present=%s",
                            page,
                            row.title,
                            row.href,
                            bool(detail_html.strip()),
                        )
                        continue
                    if recruiter_email:
                        parsed = parsed.__class__(
                            **{
                                **parsed.__dict__,
                                "recruiter_email": recruiter_email,
                                "recruiter_phone": recruiter_phone or parsed.recruiter_phone,
                                "recruiter_name": recruiter_name or parsed.recruiter_name,
                                "parse_confidence": max(parsed.parse_confidence, 0.8),
                            }
                        )
                    if fallback_used:
                        logger.info(
                            "nvoids_sync_row_fallback_used page=%s external_post_id=%r title=%r detail_html_present=%s recruiter_email=%r",
                            page,
                            parsed.external_post_id,
                            row.title,
                            bool(detail_html.strip()),
                            parsed.recruiter_email,
                        )
                    logger.info(
                        "nvoids_sync_row_parsed page=%s external_post_id=%r role=%r recruiter_email=%r recruiter_phone=%r confidence=%.2f",
                        page,
                        parsed.external_post_id,
                        parsed.role,
                        parsed.recruiter_email,
                        parsed.recruiter_phone,
                        parsed.parse_confidence,
                    )
                    dedupe_hash = build_dedupe_hash(
                        recruiter_phone=parsed.recruiter_phone,
                        recruiter_email=parsed.recruiter_email,
                        role=parsed.role,
                        location=parsed.location,
                        posted_at=parsed.posted_at,
                        raw_body=parsed.raw_body,
                    )
                    exists = (
                        db.query(ExternalOpportunity)
                        .filter(
                            ExternalOpportunity.owner_id == owner_id,
                            ExternalOpportunity.source_type == "nvoids",
                            or_(
                                ExternalOpportunity.dedupe_hash == dedupe_hash,
                                ExternalOpportunity.external_post_id == parsed.external_post_id,
                            ),
                        )
                        .first()
                    )
                    if exists:
                        deduped_count += 1
                        page_deduped += 1
                        logger.info(
                            "nvoids_sync_row_deduped page=%s external_post_id=%r dedupe_hash=%r",
                            page,
                            parsed.external_post_id,
                            dedupe_hash,
                        )
                        continue

                    record = ExternalOpportunity(
                        owner_id=owner_id,
                        feed_source_id=source.id,
                        source_type="nvoids",
                        external_post_id=parsed.external_post_id,
                        source_url=parsed.source_url,
                        posted_at=parsed.posted_at,
                        recruiter_email=parsed.recruiter_email,
                        recruiter_phone=parsed.recruiter_phone,
                        recruiter_name=parsed.recruiter_name,
                        company=parsed.company,
                        role=parsed.role,
                        location=parsed.location,
                        work_mode=parsed.work_mode,
                        visa_hints=parsed.visa_hints,
                        duration=parsed.duration,
                        rate=parsed.rate,
                        skills_text=parsed.skills_text,
                        raw_body=parsed.raw_body,
                        raw_html=parsed.raw_html,
                        dedupe_hash=dedupe_hash,
                        parse_confidence=parsed.parse_confidence,
                        bridge_status="pending",
                    )
                    db.add(record)
                    db.flush()
                    if self._bridge_to_recruiter_opportunity(db, owner_id=owner_id, item=record):
                        record.bridge_status = "bridged"
                    enqueue_attempts += 1
                    enqueued = self._enqueue_needs_review_candidate(db, owner_id=owner_id, item=record)
                    if enqueued:
                        enqueue_successes += 1
                    created_count += 1

                db.commit()
                if created_count >= max_items:
                    break
                if page_deduped >= len(rows):
                    consecutive_duplicate_pages += 1
                else:
                    consecutive_duplicate_pages = 0
                if consecutive_duplicate_pages >= duplicate_stop_threshold:
                    break

            source.last_sync_at = datetime.now(UTC)
            run.ended_at = datetime.now(UTC)
            run.fetched_count = fetched_count
            run.created_count = created_count
            run.deduped_count = deduped_count
            run.failed_count = failed_count
            if skipped_location_count:
                run.notes = f"skipped_location_count={skipped_location_count}"
            db.commit()
            db.refresh(run)
            logger.info(
                "nvoids_sync_complete owner_id=%r fetched=%s created=%s deduped=%s skipped_location=%s failed=%s enqueue_attempts=%s enqueue_successes=%s run_id=%s",
                owner_id,
                fetched_count,
                created_count,
                deduped_count,
                skipped_location_count,
                failed_count,
                enqueue_attempts,
                enqueue_successes,
                run.id,
            )
            return ExternalFeedSyncResult(
                source_type="nvoids",
                fetched_count=fetched_count,
                created_count=created_count,
                deduped_count=deduped_count,
                failed_count=failed_count,
                skipped_location_count=skipped_location_count,
                run_id=run.id,
            )
        except Exception as exc:
            db.rollback()
            failed_count += 1
            run.ended_at = datetime.now(UTC)
            run.failed_count = failed_count
            run.notes = str(exc)
            db.add(run)
            db.commit()
            raise

    def backfill_nvoids_contact_phones(self, db: Session, *, owner_id: str, limit: int = 5000) -> dict[str, int]:
        rows = (
            db.query(ExternalOpportunity)
            .filter(ExternalOpportunity.owner_id == owner_id, ExternalOpportunity.source_type == "nvoids")
            .order_by(ExternalOpportunity.id.asc())
            .limit(max(1, min(int(limit), 50000)))
            .all()
        )
        scanned = 0
        corrected = 0
        unchanged = 0
        deleted_placeholder_opportunities = 0
        deleted_placeholder_recruiters = 0
        recruiter_numbers_reformatted = 0
        employer_numbers_reformatted = 0
        review_numbers_reformatted = 0
        errors = 0

        for row in rows:
            scanned += 1
            try:
                _, strict_phone, strict_name = parse_job_detail_contacts(row.raw_html or row.raw_body or "")
                strict_phone = strict_phone.strip()
                strict_name = strict_name.strip()
                old_phone = (row.recruiter_phone or "").strip()
                if strict_name and (not row.recruiter_name or row.recruiter_name.strip().lower() in {"", "unknown"}):
                    row.recruiter_name = strict_name
                if old_phone != strict_phone:
                    row.recruiter_phone = strict_phone
                    corrected += 1
                else:
                    unchanged += 1
            except Exception:
                errors += 1

        db.flush()

        recruiter_numbers = (
            db.query(RecruiterNumber)
            .filter(RecruiterNumber.owner_id == owner_id)
            .all()
        )
        for recruiter in recruiter_numbers:
            nvoids_opportunities = (
                db.query(RecruiterOpportunity, ExternalOpportunity)
                .outerjoin(
                    ExternalOpportunity,
                    ExternalOpportunity.id == RecruiterOpportunity.external_opportunity_id,
                )
                .filter(
                    RecruiterOpportunity.owner_id == owner_id,
                    RecruiterOpportunity.recruiter_number_id == recruiter.id,
                    RecruiterOpportunity.source_type == "nvoids",
                )
                .all()
            )
            if not nvoids_opportunities:
                continue
            has_any_phone = any((ext and (ext.recruiter_phone or "").strip()) for _, ext in nvoids_opportunities)
            if has_any_phone:
                continue
            if recruiter.first_detected_email_id is not None:
                continue

            for opportunity, _ext in nvoids_opportunities:
                db.delete(opportunity)
                deleted_placeholder_opportunities += 1
            db.flush()

            remaining_opportunities = (
                db.query(RecruiterOpportunity.id)
                .filter(
                    RecruiterOpportunity.owner_id == owner_id,
                    RecruiterOpportunity.recruiter_number_id == recruiter.id,
                )
                .first()
            )
            if remaining_opportunities is None:
                db.delete(recruiter)
                deleted_placeholder_recruiters += 1

        recruiter_numbers = (
            db.query(RecruiterNumber)
            .filter(RecruiterNumber.owner_id == owner_id)
            .all()
        )
        for recruiter in recruiter_numbers:
            reformatted = self._standardize_stored_display(
                normalized=recruiter.normalized_phone_number,
                display=recruiter.display_phone_number,
            )
            if reformatted is not None and reformatted != recruiter.display_phone_number:
                recruiter.display_phone_number = reformatted
                recruiter_numbers_reformatted += 1

        employer_numbers = (
            db.query(EmployerNumber)
            .filter(EmployerNumber.owner_id == owner_id)
            .all()
        )
        for employer in employer_numbers:
            reformatted = self._standardize_stored_display(
                normalized=employer.normalized_phone_number,
                display=employer.display_phone_number,
            )
            if reformatted is not None and reformatted != employer.display_phone_number:
                employer.display_phone_number = reformatted
                employer_numbers_reformatted += 1

        review_numbers = (
            db.query(NumberReviewQueue)
            .filter(NumberReviewQueue.owner_id == owner_id)
            .all()
        )
        for review in review_numbers:
            reformatted = self._standardize_stored_display(
                normalized=review.normalized_phone_number,
                display=review.display_phone_number,
            )
            if reformatted is not None and reformatted != review.display_phone_number:
                review.display_phone_number = reformatted
                review_numbers_reformatted += 1

        db.commit()
        return {
            "scanned": scanned,
            "corrected": corrected,
            "unchanged": unchanged,
            "deleted_placeholder_opportunities": deleted_placeholder_opportunities,
            "deleted_placeholder_recruiters": deleted_placeholder_recruiters,
            "recruiter_numbers_reformatted": recruiter_numbers_reformatted,
            "employer_numbers_reformatted": employer_numbers_reformatted,
            "review_numbers_reformatted": review_numbers_reformatted,
            "errors": errors,
        }

    @staticmethod
    def _standardize_stored_display(*, normalized: str, display: str) -> str | None:
        raw_display = str(display or "").strip()
        if not raw_display or raw_display.lower() == "unknown":
            return None
        canonical = canonicalize_phone(str(normalized or "").strip())
        if not canonical:
            canonical = canonicalize_phone(raw_display)
        if not canonical:
            return None
        return best_display_phone(raw_display or canonical, fallback=raw_display)

    def _pick_cc_from_employer_pool(self, db: Session, *, owner_id: str, exclude: str) -> str | None:
        pool = (
            db.query(RecruiterEmail.sender)
            .join(EmployerNumber, EmployerNumber.source_email_id == RecruiterEmail.id)
            .filter(
                EmployerNumber.owner_id == owner_id,
                RecruiterEmail.owner_id == owner_id,
            )
            .order_by(EmployerNumber.updated_at.desc())
            .all()
        )
        exclude_l = exclude.strip().lower()
        for (email,) in pool:
            candidate = extract_email_address(str(email or ""))
            if candidate and candidate != exclude_l:
                return candidate
        return None

    @staticmethod
    def _fallback_routing_decision() -> RoutingDecision:
        return RoutingDecision(
            to_email=None,
            cc_email=None,
            status="missing",
            confidence=0.0,
            reason="Unavailable",
            evidence=[],
            candidates=[],
            recommended_state="failed",
            recommended_skip_reason="missing_to_or_cc",
            should_mark_failed=True,
            is_sendable_candidate=False,
            needs_manual_confirmation=False,
        )

    @staticmethod
    def _policy_f2f_block(parsed: dict[str, str | int | bool], policy: policy_service.PolicyConfig) -> tuple[bool, str]:
        normalized = policy_service.normalize_policy(policy)
        qualification = normalized["qualification"]
        strictness = policy_service.as_str(qualification.get("location_strictness", "balanced"), "balanced")
        if strictness == "lenient":
            return False, ""
        blocked, reason = should_block_f2f(parsed)
        if blocked:
            return True, reason or ""
        if strictness == "strict":
            location_text = str(parsed.get("job_location_text", "")).strip().lower()
            if not location_text or location_text == "unknown":
                return True, "Location is unclear under strict location policy"
        return False, ""

    def _active_resume(self, db: Session, *, owner_id: str) -> ResumeAsset | None:
        return (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.is_current.is_(True))
            .order_by(ResumeAsset.version.desc())
            .first()
        )

    def _enabled_resumes(self, db: Session, *, owner_id: str) -> list[ResumeAsset]:
        return (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.is_enabled.is_(True))
            .order_by(ResumeAsset.is_current.desc(), ResumeAsset.updated_at.desc(), ResumeAsset.version.desc(), ResumeAsset.id.desc())
            .all()
        )

    def _enqueue_needs_review_candidate(self, db: Session, *, owner_id: str, item: ExternalOpportunity) -> bool:
        recruiter_to = extract_email_address(item.recruiter_email or "")
        if not recruiter_to:
            logger.info(
                "nvoids_enqueue_skip reason=no_recruiter_email external_post_id=%r role=%r raw_recruiter_email=%r",
                item.external_post_id,
                item.role,
                item.recruiter_email,
            )
            return False
        external_message_id = f"nvoids:{item.external_post_id}"
        existing = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.external_message_id == external_message_id)
            .first()
        )
        if existing:
            logger.info(
                "nvoids_enqueue_skip reason=duplicate_candidate external_post_id=%r candidate_id=%s",
                item.external_post_id,
                existing.id,
            )
            return False
        cc_email = self._pick_cc_from_employer_pool(db, owner_id=owner_id, exclude=recruiter_to)
        if not cc_email:
            logger.info(
                "nvoids_enqueue_skip reason=no_cc_pool_match external_post_id=%r recruiter_to=%r",
                item.external_post_id,
                recruiter_to,
            )
            return False
        body = item.raw_body or item.role or ""
        subject = item.role or "Nvoids Opportunity"
        ai_parse_body = body
        ai_input_source = ""
        if item.source_type == "nvoids" and item.raw_html:
            detail = parse_nvoids_detail(item.raw_html, item.role or subject, item.location or "")
            ai_parse_body = detail.jd_body or ""
            ai_input_source = detail.jd_body_source or ""
        settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
            or UserSettings(owner_id=owner_id)
        )
        active_resume = self._active_resume(db, owner_id=owner_id)
        enabled_resumes = self._enabled_resumes(db, owner_id=owner_id)
        effective_policy = policy_service.read_policy_from_settings(settings.policy_json)
        threshold = policy_service.policy_threshold(settings.qualification_threshold, effective_policy)
        routing_decision = RoutingDecision(
            to_email=recruiter_to,
            cc_email=cc_email,
            status="safe",
            confidence=0.85,
            reason="External feed recruiter import with employer pool cc.",
            evidence=[],
            candidates=[],
            recommended_state="needs_review",
            recommended_skip_reason=None,
            should_mark_failed=False,
            is_sendable_candidate=True,
            needs_manual_confirmation=False,
        )
        parsed, parser_details = parse_email_with_details(
            subject,
            body,
            source="nvoids",
            ai_extractor_enabled=settings.feature_ai_extractor_enabled,
            ai_body_override=ai_parse_body,
            source_hints={
                "canonical_title": item.role,
                "canonical_location": item.location,
                "company": item.company,
                "work_mode": item.work_mode,
                "visa_hints": item.visa_hints,
                "ai_input_source": ai_input_source,
                "ai_input_chars": len(ai_parse_body or ""),
            },
        )
        resume_selection = self.scoring_runtime.select_best_resume_match(
            subject=subject,
            body=body,
            parsed=parsed,
            user_settings=settings,
            email_row=existing,
            resumes=enabled_resumes,
            fallback_resume=active_resume,
            db=db,
            owner_id=owner_id,
            external_thread_id=item.source_url or external_message_id,
        )
        selected_resume = resume_selection.resume or active_resume
        ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
        ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
        ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
        ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
        preparation = prepare_candidate_for_queue(
            QueuePreparationRequest(
                db=db,
                owner_id=owner_id,
                sender=recruiter_to,
                subject=subject,
                body=body,
                snippet=body,
                user_settings=settings,
                effective_policy=effective_policy,
                threshold=threshold,
                model_name="deepseek-chat",
                scoring_resume=selected_resume,
                draft_resume=selected_resume,
                existing_email=existing,
                external_thread_id=item.source_url or external_message_id,
                routing_decision=routing_decision,
                parsed_overrides=dict(parsed),
            ),
            QueuePreparationDependencies(
                parse_email=parse_email,
                hard_filter_check=hard_filter_check,
                compute_blended_ai_score=lambda subject, body, parsed, user_settings, email_row, resume, db_ctx=None, owner_id_ctx=None, thread_id_ctx=None: self.scoring_runtime.compute_blended_ai_score(
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
                policy_f2f_block=self._policy_f2f_block,
                evaluate_routing_policy=lambda *_args, **_kwargs: routing_decision,
                greeting_from_to_contact=greeting_from_to_contact,
                build_user_fallback_draft=lambda db, user_settings, sender, role, parsed, greeting_line, resume_file_name: self.candidate_runtime.build_user_fallback_draft(
                    db,
                    user_settings,
                    sender=sender,
                    role=role,
                    parsed=parsed,
                    greeting_line=greeting_line,
                    resume_file_name=resume_file_name,
                ),
                generate_reply_with_ai_or_fallback=lambda **kwargs: generate_reply_with_ai_or_fallback(**kwargs),
            ),
        )
        if selected_resume and preparation.resume_embedding_json and selected_resume.semantic_embedding != preparation.resume_embedding_json:
            selected_resume.semantic_embedding = preparation.resume_embedding_json
        if preparation.outcome != "needs_review":
            logger.info(
                "nvoids_enqueue_skip reason=queue_preparation_outcome external_post_id=%r outcome=%r role=%r ai_score=%.3f threshold=%.3f hard_filter=%r skip_reason=%r auto_reject_reason=%r routing_status=%r draft_source=%r semantic_source=%r",
                item.external_post_id,
                preparation.outcome,
                parsed.get("role"),
                preparation.ai_score,
                threshold,
                preparation.hard_filter_reason,
                preparation.skip_reason,
                preparation.auto_reject_reason,
                getattr(preparation.routing_decision, "status", None),
                preparation.draft_source,
                getattr(preparation.semantic_diag, "input_source", None),
            )
            return False
        email = RecruiterEmail(
            owner_id=owner_id,
            sender=recruiter_to,
            subject=subject,
            body=body,
            role=str(item.role or preparation.parsed.get("role", subject)),
            location=str(preparation.parsed.get("location", item.location or "")),
            salary_text=str(preparation.parsed.get("salary_text", item.rate or "")),
            skills_text=str(preparation.parsed.get("skills_text", item.skills_text or "")),
            skills_json=json.dumps(
                build_skills_json_payload(
                    parser_details,
                    fallback_skills_text=str(preparation.parsed.get("skills_text", item.skills_text or "")),
                ),
                separators=(",", ":"),
            ),
            score=int(preparation.ai_score * 100),
            decision="Qualified",
            state="needs_review",
            decision_reason=preparation.decision_reason,
            hard_filter_result=preparation.hard_filter_reason,
            auto_reject_reason=None,
            ai_score=preparation.ai_score,
            ai_score_source=preparation.ai_score_source,
            ai_summary=preparation.ai_summary,
            ats_score=ats_score,
            ats_score_source=ats_score_source,
            ats_summary=ats_summary,
            ats_breakdown_json=ats_breakdown_json,
            semantic_input_source=getattr(preparation.semantic_diag, "input_source", None),
            semantic_input_chars=getattr(preparation.semantic_diag, "input_chars", None),
            semantic_chunks=getattr(preparation.semantic_diag, "chunks", None),
            semantic_fallback_reason=getattr(preparation.semantic_diag, "fallback_reason", None),
            keyword_source=getattr(preparation.semantic_diag, "keyword_source", None),
            thread_snapshot_used=getattr(preparation.semantic_diag, "thread_snapshot_used", None),
            thread_snapshot_email_id=getattr(preparation.semantic_diag, "thread_snapshot_email_id", None),
            skip_reason=None,
            sync_batch_id=f"external-run-{item.id}",
            draft_reply=preparation.draft_reply or "",
            draft_source=preparation.draft_source,
            draft_model=preparation.draft_model,
            draft_ai_error=preparation.draft_ai_error,
            draft_resume_context_status=preparation.draft_resume_context_status,
            semantic_embedding=preparation.email_embedding_json,
            approval_status="pending",
            sent_status="not_sent",
            source="nvoids",
            external_message_id=external_message_id,
            external_thread_id=item.source_url or external_message_id,
            gmail_received_at=item.posted_at or datetime.now(UTC),
            recipient_email=recruiter_to,
            cc_email=cc_email,
            routing_status=routing_decision.status,
            routing_confidence=routing_decision.confidence,
            routing_reason=routing_decision.reason,
            routing_evidence="[]",
            routing_candidates="[]",
            routing_confirmed=False,
            resume_asset_id=selected_resume.id if selected_resume else None,
            resume_file_name=selected_resume.file_name if selected_resume else None,
            parser_details_json=json.dumps(parser_details, separators=(",", ":")),
        )
        logger.info(
            "nvoids_enqueue_success external_post_id=%r recruiter_to=%r cc_email=%r role=%r ai_score=%.3f resume_id=%r resume_name=%r draft_source=%r",
            item.external_post_id,
            recruiter_to,
            cc_email,
            email.role,
            preparation.ai_score,
            email.resume_asset_id,
            email.resume_file_name,
            email.draft_source,
        )
        db.add(email)
        return True

    def _bridge_to_recruiter_opportunity(self, db: Session, *, owner_id: str, item: ExternalOpportunity) -> bool:
        canonical = canonicalize_phone(item.recruiter_phone or "")
        if not canonical and not item.recruiter_email:
            item.bridge_status = "ignored"
            return False
        if not canonical:
            item.bridge_status = "ignored_no_phone"
            return False

        recruiter = None
        if canonical:
            recruiter = (
                db.query(RecruiterNumber)
                .filter(RecruiterNumber.owner_id == owner_id, RecruiterNumber.normalized_phone_number == canonical)
                .first()
            )
        if not recruiter:
            recruiter = RecruiterNumber(
                owner_id=owner_id,
                normalized_phone_number=canonical or f"nvoids-{item.id}",
                display_phone_number=best_display_phone(item.recruiter_phone or canonical, fallback="Unknown"),
                recruiter_name=item.recruiter_name or "Unknown",
                company=item.company or "Unknown",
                designation="Recruiter",
                recruiter_email=item.recruiter_email or "",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.flush()

        existing = (
            db.query(RecruiterOpportunity)
            .filter(
                RecruiterOpportunity.owner_id == owner_id,
                RecruiterOpportunity.recruiter_number_id == recruiter.id,
                RecruiterOpportunity.gmail_message_id == f"nvoids:{item.external_post_id}",
            )
            .first()
        )
        if existing:
            item.bridge_status = "duplicate"
            item.bridge_target_opportunity_id = existing.id
            return False

        opp = RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=recruiter.id,
            source_email_id=None,
            gmail_message_id=f"nvoids:{item.external_post_id}",
            email_subject=item.role,
            email_sender=item.recruiter_email or "nvoids",
            gmail_open_url=item.source_url,
            received_at=item.posted_at or datetime.now(UTC),
            job_title=item.role,
            client=item.company,
            location=item.location,
            work_mode=item.work_mode,
            visa_restrictions=item.visa_hints,
            extracted_skills=item.skills_text,
            evidence="External feed: nvoids",
            status="New",
            notes="",
            source_type="nvoids",
            source_url=item.source_url,
            external_opportunity_id=item.id,
        )
        db.add(opp)
        db.flush()
        item.bridge_target_opportunity_id = opp.id
        return True
