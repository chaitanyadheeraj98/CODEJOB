from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.automation.queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)
from app.models import EmployerNumber, NumberReviewQueue, RecentRun, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, ResumeAsset, UserSettings
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
from app.recent_runs import (
    RUN_SOURCE_NVOIDS_SYNC,
    SkippedItemRecord,
    create_recent_run,
    nvoids_run_key,
    record_skipped_item,
    update_recent_run,
)
from app.routing import RoutingDecision
from app.semantic.embeddings_service import generate_embedding
from app.services import policy_service
from app.services.candidate_runtime_service import CandidateRuntimeDeps, CandidateRuntimeService
from app.services.candidate_screening_service import CandidateScreeningService, apply_screening_decision
from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService
from app.services.sendability_service import apply_resume_sendability

from .collector import NvoidsCollector
from .dedupe import build_dedupe_hash
from .models import ExternalFeedSource, ExternalOpportunity, ExternalScrapeRun
from .parser import (
    classify_nvoids_page_title,
    extract_nvoids_page_title,
    parse_external_post,
    parse_job_detail_contacts,
    parse_listing_rows,
    parse_nvoids_detail,
)
from .types import ExternalFeedSyncResult


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnqueueResult:
    enqueued: bool
    reason_code: str | None = None
    reason_detail: str | None = None
    candidate_email_id: int | None = None


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

    @staticmethod
    def normalize_nvoids_detail_title_mode(raw_mode: str | None) -> str:
        normalized = str(raw_mode or "").strip().lower()
        if normalized in {"job_details", "hotlist_details", "all"}:
            return normalized
        return "job_details"

    def nvoids_hotlist_mode_for_title_mode(self, raw_mode: str | None) -> str:
        mode = self.normalize_nvoids_detail_title_mode(raw_mode)
        if mode == "hotlist_details":
            return "Only Hotlists"
        if mode == "all":
            return "Include Hotlists"
        return "Exclude Hotlists"

    def nvoids_page_title_allowed(self, raw_mode: str | None, page_kind: str) -> bool:
        mode = self.normalize_nvoids_detail_title_mode(raw_mode)
        if page_kind == "unknown":
            return True
        if mode == "all":
            return page_kind in {"job_details", "hotlist_details"}
        if mode == "job_details":
            return page_kind == "job_details"
        if mode == "hotlist_details":
            return page_kind == "hotlist_details"
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
        run_key_override: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ExternalFeedSyncResult:
        source = self.ensure_nvoids_source(db, owner_id=owner_id)
        user_settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
            or UserSettings(owner_id=owner_id)
        )
        location_filters = self._normalize_location_tokens((user_settings.nvoids_locations or "").split(","))
        detail_title_mode = self.normalize_nvoids_detail_title_mode(getattr(user_settings, "nvoids_detail_title_mode", None))
        query = self.build_nvoids_query(location_filters)
        run = ExternalScrapeRun(owner_id=owner_id, source_type="nvoids", started_at=datetime.now(UTC), notes="")
        db.add(run)
        db.commit()
        db.refresh(run)
        run_key = run_key_override or nvoids_run_key(run.id)
        recent_run = db.query(RecentRun).filter(RecentRun.run_key == run_key).first()
        if recent_run is None:
            recent_run = create_recent_run(
                db,
                owner_id=owner_id,
                run_source=RUN_SOURCE_NVOIDS_SYNC,
                run_key=run_key,
                status="running",
                detail="Nvoids sync started.",
                skipped_count=0,
                failed_count=0,
                skipped_item_count=0,
                external_scrape_run_id=run.id,
            )
        else:
            recent_run.status = "running"
            recent_run.detail = "Nvoids sync started."
            recent_run.external_scrape_run_id = run.id
        db.commit()
        db.refresh(recent_run)

        fetched_count = 0
        created_count = 0
        deduped_count = 0
        failed_count = 0
        skipped_location_count = 0
        skipped_item_count = 0
        consecutive_duplicate_pages = 0
        enqueue_attempts = 0
        enqueue_successes = 0
        detail_fetch_fallback_rows = 0
        processed_items = 0

        def report_item() -> None:
            nonlocal processed_items
            processed_items += 1
            db.commit()
            if progress_callback is not None:
                progress_callback(processed_items, max(max_items, processed_items))

        if hasattr(self.collector, "reset_detail_fetch_metrics"):
            self.collector.reset_detail_fetch_metrics()

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
                    hotlist_mode=self.nvoids_hotlist_mode_for_title_mode(detail_title_mode),
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
                        skipped_item_count += 1
                        record_skipped_item(
                            db,
                            SkippedItemRecord(
                                owner_id=owner_id,
                                run_source=RUN_SOURCE_NVOIDS_SYNC,
                                run_key=run_key,
                                source_type="nvoids",
                                reason_code="skipped_location",
                                reason_detail=f"Skipped because listing location '{row.location}' did not match the saved Nvoids location filters.",
                                external_thread_id=row.href,
                                title_or_subject=row.title,
                                sender="Nvoids",
                                location=row.location,
                                source_url=row.href,
                            ),
                        )
                        logger.info(
                            "nvoids_sync_skip_location page=%s title=%r location=%r allowed_locations=%s",
                            page,
                            row.title,
                            row.location,
                            location_filters,
                        )
                        report_item()
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
                    except Exception as exc:
                        # Keep ingestion resilient: listing row still ingests even if one detail page fails.
                        failed_count += 1
                        fallback_used = True
                        detail_fetch_fallback_rows += 1
                        logger.warning(
                            "nvoids_sync_row_detail_fetch_failed page=%s title=%r href=%r error_type=%s error=%s",
                            page,
                            row.title,
                            row.href,
                            type(exc).__name__,
                            exc,
                        )
                    page_title = extract_nvoids_page_title(detail_html)
                    page_kind = classify_nvoids_page_title(page_title)
                    if detail_html.strip() and not self.nvoids_page_title_allowed(detail_title_mode, page_kind):
                        skipped_item_count += 1
                        record_skipped_item(
                            db,
                            SkippedItemRecord(
                                owner_id=owner_id,
                                run_source=RUN_SOURCE_NVOIDS_SYNC,
                                run_key=run_key,
                                source_type="nvoids",
                                reason_code="skipped_nvoids_page_title",
                                reason_detail=(
                                    f"Skipped because detail page title '{page_title or 'Unknown'}' "
                                    f"did not match the saved Nvoids title filter '{detail_title_mode}'."
                                ),
                                external_thread_id=detail_url,
                                title_or_subject=row.title,
                                sender="Nvoids",
                                location=row.location,
                                source_url=detail_url,
                            ),
                        )
                        logger.info(
                            "nvoids_sync_skip_page_title page=%s title=%r page_title=%r page_kind=%r mode=%r url=%r",
                            page,
                            row.title,
                            page_title,
                            page_kind,
                            detail_title_mode,
                            detail_url,
                        )
                        report_item()
                        continue
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
                        report_item()
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
                        report_item()
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
                    enqueue_result = self._enqueue_needs_review_candidate(db, owner_id=owner_id, item=record)
                    if enqueue_result.enqueued:
                        enqueue_successes += 1
                    else:
                        skipped_item_count += 1
                        record_skipped_item(
                            db,
                            SkippedItemRecord(
                                owner_id=owner_id,
                                run_source=RUN_SOURCE_NVOIDS_SYNC,
                                run_key=run_key,
                                source_type="nvoids",
                                reason_code=enqueue_result.reason_code or "nvoids_enqueue_skipped",
                                reason_detail=enqueue_result.reason_detail or "Skipped during Nvoids enqueue.",
                                external_message_id=f"nvoids:{record.external_post_id}",
                                external_thread_id=record.source_url,
                                candidate_email_id=enqueue_result.candidate_email_id,
                                external_opportunity_id=record.id,
                                title_or_subject=record.role or row.title,
                                sender=record.recruiter_email or record.recruiter_name or "Nvoids",
                                location=record.location,
                                source_url=record.source_url,
                            ),
                        )
                    created_count += 1
                    report_item()

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
            collector_metrics = (
                self.collector.get_detail_fetch_metrics()
                if hasattr(self.collector, "get_detail_fetch_metrics")
                else {"retry_count": 0, "failure_count": 0}
            )
            run.notes = ",".join(
                [
                    f"detail_fetch_failures={int(collector_metrics.get('failure_count', 0))}",
                    f"detail_fetch_retries={int(collector_metrics.get('retry_count', 0))}",
                    f"detail_fetch_fallback_rows={detail_fetch_fallback_rows}",
                    f"skipped_location_count={skipped_location_count}",
                ]
            )
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
            update_recent_run(
                recent_run,
                status="ok",
                detail=f"nvoids sync complete: fetched={fetched_count} created={created_count} deduped={deduped_count} skipped_location={skipped_location_count} failed={failed_count}",
                skipped_count=skipped_location_count,
                failed_count=failed_count,
                skipped_item_count=skipped_item_count,
            )
            db.commit()
            return ExternalFeedSyncResult(
                source_type="nvoids",
                run_key=run_key,
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
            update_recent_run(
                recent_run,
                status="failed",
                detail=f"nvoids_sync_failed: {exc}",
                skipped_count=skipped_location_count,
                failed_count=failed_count,
                skipped_item_count=skipped_item_count,
            )
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
        bridged = 0
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

                canonical_phone = canonicalize_phone(strict_phone)
                if canonical_phone:
                    existing_links = (
                        db.query(RecruiterOpportunity)
                        .filter(
                            RecruiterOpportunity.owner_id == owner_id,
                            RecruiterOpportunity.source_type == "nvoids",
                            RecruiterOpportunity.external_opportunity_id == row.id,
                        )
                        .all()
                    )
                    for existing_link in existing_links:
                        linked_recruiter = (
                            db.query(RecruiterNumber)
                            .filter(
                                RecruiterNumber.owner_id == owner_id,
                                RecruiterNumber.id == existing_link.recruiter_number_id,
                            )
                            .first()
                        )
                        if linked_recruiter is None:
                            continue
                        if linked_recruiter.normalized_phone_number == canonical_phone:
                            continue
                        if linked_recruiter.first_detected_email_id is not None:
                            continue
                        db.delete(existing_link)
                        deleted_placeholder_opportunities += 1
                        db.flush()
                        remaining_opportunities = (
                            db.query(RecruiterOpportunity.id)
                            .filter(
                                RecruiterOpportunity.owner_id == owner_id,
                                RecruiterOpportunity.recruiter_number_id == linked_recruiter.id,
                            )
                            .first()
                        )
                        if remaining_opportunities is None:
                            db.delete(linked_recruiter)
                            deleted_placeholder_recruiters += 1
                            db.flush()

                    if self._bridge_to_recruiter_opportunity(db, owner_id=owner_id, item=row):
                        row.bridge_status = "bridged"
                        bridged += 1
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
            "bridged": bridged,
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
    def _preferred_employer_cc(settings: UserSettings, *, recruiter_to: str) -> str | None:
        preferred = extract_email_address(getattr(settings, "preferred_employer_cc_email", "") or "")
        recruiter_normalized = extract_email_address(recruiter_to or "")
        if not preferred:
            return None
        if recruiter_normalized and preferred == recruiter_normalized:
            return None
        return preferred

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
        f2f_blocked, f2f_reason = should_block_f2f(parsed)
        if strictness == "lenient":
            f2f_blocked = False
            f2f_reason = ""
        blocked, reason = policy_service.should_block_non_texas_f2f(
            parsed,
            normalized,
            f2f_blocked=f2f_blocked,
            f2f_reason=f2f_reason or "",
        )
        if blocked:
            return True, reason
        return policy_service.should_block_unknown_location_under_strict(parsed, normalized)

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

    def _enqueue_needs_review_candidate(self, db: Session, *, owner_id: str, item: ExternalOpportunity) -> EnqueueResult:
        recruiter_to = extract_email_address(item.recruiter_email or "")
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
            return EnqueueResult(
                enqueued=False,
                reason_code="duplicate_candidate",
                reason_detail="Skipped because this Nvoids listing already exists as a candidate.",
                candidate_email_id=existing.id,
            )
        settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
            or UserSettings(owner_id=owner_id)
        )
        effective_policy = policy_service.read_policy_from_settings(settings.policy_json)
        recipient_mapping_mode = policy_service.recipient_mapping_rule_mode(effective_policy)
        if not recruiter_to and recipient_mapping_mode == "block":
            logger.info(
                "nvoids_enqueue_skip reason=no_recruiter_email external_post_id=%r role=%r raw_recruiter_email=%r",
                item.external_post_id,
                item.role,
                item.recruiter_email,
            )
            return EnqueueResult(
                enqueued=False,
                reason_code="no_recruiter_email",
                reason_detail="Skipped because no recruiter To email could be extracted from the Nvoids listing.",
            )
        preferred_cc_email = self._preferred_employer_cc(settings, recruiter_to=recruiter_to)
        cc_email = preferred_cc_email or self._pick_cc_from_employer_pool(db, owner_id=owner_id, exclude=recruiter_to)
        if not cc_email and recipient_mapping_mode == "block":
            logger.info(
                "nvoids_enqueue_skip reason=no_cc_pool_match external_post_id=%r recruiter_to=%r",
                item.external_post_id,
                recruiter_to,
            )
            return EnqueueResult(
                enqueued=False,
                reason_code="no_cc_pool_match",
                reason_detail="Skipped because no employer CC email could be resolved for the Nvoids listing.",
            )
        body = item.raw_body or item.role or ""
        subject = item.role or "Nvoids Opportunity"
        ai_parse_body = body
        ai_input_source = ""
        if item.source_type == "nvoids" and item.raw_html:
            detail = parse_nvoids_detail(item.raw_html, item.role or subject, item.location or "")
            ai_parse_body = detail.jd_body or ""
            ai_input_source = detail.jd_body_source or ""
        active_resume = self._active_resume(db, owner_id=owner_id)
        enabled_resumes = self._enabled_resumes(db, owner_id=owner_id)
        threshold = policy_service.policy_threshold(settings.qualification_threshold, effective_policy)
        routing_safe = bool(recruiter_to and cc_email)
        if routing_safe:
            routing_reason = (
                "External feed recruiter import with preferred employer CC from Execution Control."
                if preferred_cc_email
                else "External feed recruiter import with employer pool cc."
            )
        else:
            routing_reason = "Missing recruiter To or employer CC from external feed import."
        sender_identity = recruiter_to or extract_email_address(item.recruiter_email or "") or item.recruiter_name or "Nvoids Recruiter"
        routing_decision = RoutingDecision(
            to_email=recruiter_to or None,
            cc_email=cc_email,
            status="safe" if routing_safe else "missing",
            confidence=0.85 if routing_safe else 0.0,
            reason=routing_reason,
            evidence=[],
            candidates=[],
            recommended_state="failed" if not routing_safe else "needs_review",
            recommended_skip_reason="missing_to_or_cc" if not routing_safe else None,
            should_mark_failed=not routing_safe,
            is_sendable_candidate=routing_safe,
            needs_manual_confirmation=not routing_safe,
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
        screening = CandidateScreeningService().evaluate_parser_details(parser_details, settings)
        if not screening.proceed_to_scoring:
            email = RecruiterEmail(
                owner_id=owner_id,
                sender=sender_identity,
                subject=subject,
                body=body,
                role=str(item.role or parsed.get("role", subject)),
                location=str(parsed.get("location", item.location or "")),
                salary_text=str(parsed.get("salary_text", item.rate or "")),
                skills_text=str(parsed.get("skills_text", item.skills_text or "")),
                skills_json=json.dumps(
                    build_skills_json_payload(
                        parser_details,
                        fallback_skills_text=str(parsed.get("skills_text", item.skills_text or "")),
                    ),
                    separators=(",", ":"),
                ),
                decision="Qualified",
                state="needs_review",
                decision_reason="strict_candidate_screening",
                hard_filter_result="strict_candidate_screening",
                approval_status="pending",
                sent_status="not_sent",
                source="nvoids",
                external_message_id=external_message_id,
                external_thread_id=item.source_url or external_message_id,
                gmail_received_at=item.posted_at or datetime.now(UTC),
                recipient_email=recruiter_to or None,
                cc_email=cc_email,
                routing_status=routing_decision.status,
                routing_confidence=routing_decision.confidence,
                routing_reason=routing_decision.reason,
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=False,
                parser_details_json=json.dumps(parser_details, separators=(",", ":")),
            )
            apply_screening_decision(email, screening)
            db.add(email)
            db.flush()
            return EnqueueResult(enqueued=True, candidate_email_id=email.id)
        resume_selection = self.scoring_runtime.select_best_resume_match(
            subject=subject,
            body=body,
            parsed=parsed,
            parser_details=parser_details,
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
        resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
        resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
        resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
        resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
        preparation = prepare_candidate_for_queue(
            QueuePreparationRequest(
                db=db,
                owner_id=owner_id,
                sender=sender_identity,
                subject=subject,
                body=body,
                snippet=body,
                user_settings=settings,
                effective_policy=effective_policy,
                threshold=threshold,
                model_name="deepseek-v4-flash",
                scoring_resume=selected_resume,
                draft_resume=selected_resume,
                existing_email=existing,
                external_thread_id=item.source_url or external_message_id,
                routing_decision=routing_decision,
                parsed_overrides=dict(parsed),
                parser_details=parser_details,
                precomputed_ai_score=cast(float | None, getattr(resume_selection, "ai_score", None)),
                precomputed_ai_summary=cast(str | None, getattr(resume_selection, "ai_summary", None)),
                precomputed_ai_score_source=cast(str | None, getattr(resume_selection, "ai_score_source", None)),
                precomputed_email_embedding_json=cast(str | None, getattr(resume_selection, "email_embedding_json", None)),
                precomputed_resume_embedding_json=cast(str | None, getattr(resume_selection, "resume_embedding_json", None)),
                precomputed_semantic_diag=getattr(resume_selection, "semantic_diag", None),
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
            return EnqueueResult(
                enqueued=False,
                reason_code=preparation.skip_reason or "queue_preparation_outcome",
                reason_detail=preparation.decision_reason or f"Skipped because queue preparation ended with outcome '{preparation.outcome}'.",
            )
        email = RecruiterEmail(
            owner_id=owner_id,
            sender=sender_identity,
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
            resume_picker_score=resume_picker_score,
            resume_picker_reason=resume_picker_reason,
            resume_picker_candidates_json=resume_picker_candidates_json,
            resume_picker_breakdown_json=resume_picker_breakdown_json,
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
            recipient_email=recruiter_to or None,
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
        apply_screening_decision(email, screening)
        apply_resume_sendability(email)
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
        db.flush()
        return EnqueueResult(enqueued=True, candidate_email_id=email.id)

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
