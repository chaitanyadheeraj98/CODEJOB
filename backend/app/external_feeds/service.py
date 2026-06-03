from __future__ import annotations

from datetime import UTC, datetime
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import EmployerNumber, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, UserSettings
from app.premium_numbers.phone_normalization import canonicalize_phone
from app.phase0 import extract_email_address, parse_email

from .collector import NvoidsCollector
from .dedupe import build_dedupe_hash
from .models import ExternalFeedSource, ExternalOpportunity, ExternalScrapeRun
from .parser import parse_external_post, parse_job_detail_contacts, parse_listing_rows
from .types import ExternalFeedSyncResult


class ExternalFeedService:
    def __init__(self) -> None:
        self.collector = NvoidsCollector()
        self.default_query = "(tx or texas) and java and spring* not(*js)"
        self.default_hotlist_mode = "Exclude Hotlists"

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

        try:
            for page in range(max_pages):
                collected = self.collector.fetch_search_page(
                    query=query,
                    hotlist_mode=self.default_hotlist_mode,
                    page=page,
                )
                rows = parse_listing_rows(collected.html, collected.url)
                if not rows:
                    break
                page_deduped = 0
                for row in rows:
                    if created_count >= max_items:
                        break
                    fetched_count += 1
                    if not self.row_matches_locations(row.location, location_filters):
                        skipped_location_count += 1
                        continue
                    detail_html = ""
                    detail_url = row.href
                    try:
                        detail_page = self.collector.fetch_detail_page(url=row.href)
                        detail_html = detail_page.html
                        detail_url = detail_page.url
                    except Exception:
                        # Keep ingestion resilient: listing row still ingests even if one detail page fails.
                        failed_count += 1
                    recruiter_email, recruiter_phone, recruiter_name = parse_job_detail_contacts(detail_html)
                    parsed = parse_external_post(
                        source_type="nvoids",
                        source_url=detail_url,
                        title=row.title,
                        location=row.location,
                        posted_text=row.posted_text,
                        raw_body=detail_html or row.title,
                        raw_html=detail_html or collected.html,
                    )
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
                    self._enqueue_needs_review_candidate(db, owner_id=owner_id, item=record)
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
        recruiter_numbers_normalized = 0
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
            opportunities = (
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
            if not opportunities:
                continue
            has_any_phone = any((ext and (ext.recruiter_phone or "").strip()) for _, ext in opportunities)
            if has_any_phone:
                continue
            if recruiter.first_detected_email_id is not None:
                continue
            recruiter.normalized_phone_number = f"nvoids-{recruiter.id}"
            recruiter.display_phone_number = "Unknown"
            recruiter_numbers_normalized += 1

        db.commit()
        return {
            "scanned": scanned,
            "corrected": corrected,
            "unchanged": unchanged,
            "recruiter_numbers_normalized": recruiter_numbers_normalized,
            "errors": errors,
        }

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

    def _enqueue_needs_review_candidate(self, db: Session, *, owner_id: str, item: ExternalOpportunity) -> bool:
        recruiter_to = extract_email_address(item.recruiter_email or "")
        if not recruiter_to:
            return False
        external_message_id = f"nvoids:{item.external_post_id}"
        existing = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.external_message_id == external_message_id)
            .first()
        )
        if existing:
            return False
        cc_email = self._pick_cc_from_employer_pool(db, owner_id=owner_id, exclude=recruiter_to)
        if not cc_email:
            return False
        body = item.raw_body or item.role or ""
        subject = item.role or "Nvoids Opportunity"
        parsed = parse_email(subject, body)
        settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
            or UserSettings(owner_id=owner_id)
        )
        draft_reply = (
            f"Hi {item.recruiter_name or 'Recruiter'},\n\n"
            f"Thanks for sharing this role ({subject}). I am interested and would like to discuss fit.\n"
            f"Please let me know a good time to connect.\n\nRegards"
        )
        email = RecruiterEmail(
            owner_id=owner_id,
            sender=recruiter_to,
            subject=subject,
            body=body,
            role=str(parsed.get("role", subject)),
            location=str(parsed.get("location", item.location or "")),
            salary_text=str(parsed.get("salary_text", item.rate or "")),
            skills_text=str(parsed.get("skills_text", item.skills_text or "")),
            score=80,
            decision="Qualified",
            state="needs_review",
            decision_reason="external_feed_nvoids",
            hard_filter_result="external_feed",
            auto_reject_reason=None,
            ai_score=0.8,
            ai_score_source="external_feed_rule",
            ai_summary="Imported from nvoids external feed.",
            skip_reason=None,
            sync_batch_id=f"external-run-{item.id}",
            draft_reply=draft_reply,
            draft_source="rules_only",
            draft_model=None,
            draft_ai_error=None,
            draft_resume_context_status="rules_only",
            approval_status="pending",
            sent_status="not_sent",
            source="nvoids",
            external_message_id=external_message_id,
            external_thread_id=item.source_url or external_message_id,
            gmail_received_at=item.posted_at or datetime.now(UTC),
            recipient_email=recruiter_to,
            cc_email=cc_email,
            routing_status="safe",
            routing_confidence=0.85,
            routing_reason="External feed recruiter import with employer pool cc.",
            routing_evidence="[]",
            routing_candidates="[]",
            routing_confirmed=False,
        )
        db.add(email)
        return True

    def _bridge_to_recruiter_opportunity(self, db: Session, *, owner_id: str, item: ExternalOpportunity) -> bool:
        canonical = canonicalize_phone(item.recruiter_phone or "")
        if not canonical and not item.recruiter_email:
            item.bridge_status = "ignored"
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
                display_phone_number=item.recruiter_phone or canonical or "Unknown",
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
