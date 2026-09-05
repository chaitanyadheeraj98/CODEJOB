"""Turn a pasted requirement into a Needs Review card.

The third ingestion path, beside Gmail and Nvoids. A requirement that arrives on
WhatsApp - or in any channel the application cannot read - had no way in at all;
this is that way in.

**This is an assembler, not a pipeline of its own.** The expensive work is
already built and source-agnostic, and every bit of it is called here
unmodified: `parse_email_with_details` for extraction, `CandidateScreeningService`
for screening, `select_best_resume_match` for the resume, and
`prepare_candidate_for_queue` for the hard filter, the blended score, routing
and the draft - the same engine the Gmail run and the Nvoids sync call. What
this module owns is the assembly, which is exactly where the manual-specific
rules live.

Those rules, and why each exists:

* **Two copies of the text.** `raw_text` is stored verbatim and is what contact
  extraction reads; `jd_text` is footer-stripped and is all the JD parser sees.
  The recruiter's address sits *below* the sign-off, which is precisely where
  `strip_recruiter_footer` cuts - so reading contacts from parsed output would
  put every paste into Failed Mapping. Feeding the raw text to the parser is the
  opposite mistake: the signature would read as job content, and the recruiter's
  own company would look like an end client.
* **Nothing about a contact is inferred.** An address is used only if it is
  written in the text.
* **A requirement with no address is still fully processed** - parsed, screened,
  resume matched, draft written - and then filed to Failed Mapping, because the
  work is worth keeping even when the mail cannot be sent yet.
* **`source="manual"` is written once, here**, where the row is built.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, cast

from sqlalchemy.orm import Session

from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.automation.queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)
from app.external_feeds.dedupe import build_dedupe_hash
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.parsing import build_skills_json_payload
from app.parsing.manual_contacts import ManualContacts, extract_manual_contacts
from app.phase0 import (
    greeting_from_to_contact,
    hard_filter_check,
    jd_entity_fields_from_parsed,
    parse_email,
    parse_email_with_details,
    strip_forward_headers,
    strip_recruiter_footer,
)
from app.premium_numbers.domain_guard import employer_domains_for_owner
from app.routing import RoutingDecision
from app.semantic.embeddings_service import generate_embedding
from app.services import opportunity_lineage_service, policy_service, recruiter_identity_service
from app.services.candidate_runtime_service import CandidateRuntimeDeps, CandidateRuntimeService
from app.services.candidate_screening_service import CandidateScreeningService, apply_screening_decision
from app.services.role_provenance import assign_role
from app.services.role_taxonomy import fill_entity_gaps, role_matcher_for
from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService
from app.services.sendability_service import apply_resume_sendability

logger = logging.getLogger(__name__)

MISSING_EMAIL_SKIP_REASON = "manual_intake_missing_recruiter_email"
MISSING_EMAIL_DETAIL = (
    "No recruiter email found in the pasted requirement. Add one to enable outreach."
)


@dataclass(frozen=True)
class ManualIntakeDeps:
    """The few callables that live in main and cannot be imported from here."""

    owner_id: str
    model_name: str
    get_settings: Callable[[Session], UserSettings]
    active_resume: Callable[[Session], ResumeAsset | None]
    enabled_resumes: Callable[[Session], list[ResumeAsset]]
    evaluate_routing_policy: Callable[..., RoutingDecision]
    apply_routing_decision: Callable[[RecruiterEmail, RoutingDecision], None]
    capture_premium_numbers: Callable[[Session, RecruiterEmail], None]


@dataclass(frozen=True)
class ManualDuplicate:
    """An existing manual card with the same content fingerprint."""

    id: int
    role: str
    client: str
    created_at: datetime


@dataclass(frozen=True)
class ManualIntakeResult:
    email_id: int
    state: str
    detail: str
    recruiter_email: str
    duplicate_of: int | None = None


class ManualIntakeService:
    def __init__(self, deps: ManualIntakeDeps) -> None:
        self.deps = deps
        self.scoring_runtime = ScoringRuntimeService(
            ScoringRuntimeDeps(generate_embedding_with_health=lambda text: generate_embedding(text))
        )
        self.candidate_runtime = CandidateRuntimeService(
            CandidateRuntimeDeps(
                get_settings=deps.get_settings,
                evaluate_routing_policy=deps.evaluate_routing_policy,
                apply_routing_decision=deps.apply_routing_decision,
            )
        )

    # --- text handling ----------------------------------------------------

    @staticmethod
    def _jd_text(raw_text: str) -> str:
        """The parser's copy: forwarding headers and the signature removed.

        `prepare_gmail_parse_body` is deliberately not used - a paste is not HTML
        and carries no Gmail quoting.
        """
        return strip_recruiter_footer(strip_forward_headers(raw_text or ""))

    @staticmethod
    def _subject(jd_text: str, parsed_role: str) -> str:
        if parsed_role.strip():
            return parsed_role.strip()[:500]
        for line in jd_text.splitlines():
            if line.strip():
                return line.strip()[:500]
        return "Pasted requirement"

    def _contacts(self, db: Session, raw_text: str) -> ManualContacts:
        return extract_manual_contacts(
            raw_text,
            employer_domains=frozenset(employer_domains_for_owner(db, self.deps.owner_id)),
        )

    def _fingerprint(self, *, contacts: ManualContacts, role: str, location: str, raw_text: str) -> str:
        """The same hash the Nvoids sync uses, over the same field set.

        `posted_at` is the paste date: a paste has no posting date of its own, so
        the same text pasted next week is a new requirement rather than a
        duplicate. That is deliberate - reposts and renewed openings are real.
        """
        return build_dedupe_hash(
            recruiter_phone=contacts.phone,
            recruiter_email=contacts.recruiter_email,
            role=role,
            location=location,
            posted_at=datetime.now(UTC),
            raw_body=raw_text,
        )

    # --- duplicate preview ------------------------------------------------

    def preview(self, db: Session, *, text: str) -> ManualDuplicate | None:
        """Exact-identity duplicate check. No model calls - this runs on blur.

        Identity, not similarity: it answers "have you already pasted this exact
        requirement today", never "are these two postings the same job". The
        second question has no answer the data can support.
        """
        raw_text = text or ""
        if not raw_text.strip():
            return None
        contacts = self._contacts(db, raw_text)
        parsed = parse_email(self._subject(self._jd_text(raw_text), ""), self._jd_text(raw_text))
        fingerprint = self._fingerprint(
            contacts=contacts,
            role=str(parsed.get("role", "")),
            location=str(parsed.get("location", "")),
            raw_text=raw_text,
        )
        row = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == self.deps.owner_id,
                RecruiterEmail.manual_dedupe_hash == fingerprint,
            )
            .order_by(RecruiterEmail.id.desc())
            .first()
        )
        if row is None:
            return None
        return ManualDuplicate(
            id=row.id,
            role=row.role or "",
            client=row.end_client or "",
            created_at=row.created_at,
        )

    # --- ingestion --------------------------------------------------------

    def ingest(self, db: Session, *, text: str) -> ManualIntakeResult:
        raw_text = text or ""
        if not raw_text.strip():
            raise ValueError("Pasted requirement is empty")

        jd_text = self._jd_text(raw_text)
        contacts = self._contacts(db, raw_text)
        user_settings = self.deps.get_settings(db)
        effective_policy = policy_service.read_policy_from_settings(user_settings.policy_json)

        parsed, parser_details = parse_email_with_details(
            self._subject(jd_text, ""),
            jd_text,
            ai_extractor_enabled=user_settings.feature_ai_extractor_enabled,
        )
        subject = self._subject(jd_text, str(parsed.get("role", "")))
        location = str(parsed.get("location", ""))
        fingerprint = self._fingerprint(
            contacts=contacts, role=str(parsed.get("role", "")), location=location, raw_text=raw_text
        )
        assigned = assign_role(
            extracted=str(parsed.get("role") or ""),
            subject=subject,
            body=jd_text,
            matcher=role_matcher_for(db, self.deps.owner_id),
        )

        def build_row(**overrides: Any) -> RecruiterEmail:
            email = RecruiterEmail(
                owner_id=self.deps.owner_id,
                sender=contacts.sender_identity or "Pasted requirement",
                subject=subject,
                # Verbatim, signature and all. The stripped copy is a parser
                # input and is never what gets stored.
                body=raw_text,
                role=assigned.role,
                role_source=assigned.role_source,
                role_canonical=assigned.role_canonical,
                salary_text=str(parsed.get("salary_text", "")),
                skills_text=str(parsed.get("skills_text", "")),
                skills_json=json.dumps(
                    build_skills_json_payload(
                        parser_details, fallback_skills_text=str(parsed.get("skills_text", ""))
                    ),
                    separators=(",", ":"),
                ),
                **fill_entity_gaps(
                    jd_entity_fields_from_parsed(parsed),
                    db=db,
                    owner_id=self.deps.owner_id,
                    subject=subject,
                    location=location,
                    body=jd_text,
                ),
                approval_status="pending",
                sent_status="not_sent",
                # Written once, here. Nothing infers it from a shared assembler.
                source="manual",
                manual_dedupe_hash=fingerprint,
                recipient_email=contacts.recruiter_email or None,
                parser_details_json=json.dumps(parser_details, separators=(",", ":")),
                **overrides,
            )
            return email

        screening = CandidateScreeningService().evaluate_parser_details(parser_details, user_settings)
        if not screening.proceed_to_scoring:
            hard_pass, hard_reason = hard_filter_check(
                parsed, user_settings, effective_policy, parser_details
            )
            email = build_row(
                decision="Qualified",
                state="needs_review",
                decision_reason="strict_candidate_screening",
                hard_filter_result=hard_reason,
            )
            apply_screening_decision(email, screening)
            return self._persist(db, email, contacts)

        active_resume = self.deps.active_resume(db)
        enabled_resumes = self.deps.enabled_resumes(db)
        resume_selection = self.scoring_runtime.select_best_resume_match(
            subject=subject,
            body=jd_text,
            parsed=parsed,
            parser_details=parser_details,
            user_settings=user_settings,
            email_row=None,
            resumes=enabled_resumes,
            fallback_resume=active_resume,
            db=db,
            owner_id=self.deps.owner_id,
            external_thread_id="",
        )
        selected_resume = getattr(resume_selection, "resume", None) or active_resume

        preparation = prepare_candidate_for_queue(
            QueuePreparationRequest(
                db=db,
                owner_id=self.deps.owner_id,
                sender=contacts.sender_identity or "Pasted requirement",
                subject=subject,
                # The engine scores and drafts from job content, never from the
                # signature block.
                body=jd_text,
                snippet=jd_text,
                user_settings=user_settings,
                effective_policy=effective_policy,
                threshold=policy_service.policy_threshold(
                    user_settings.qualification_threshold, effective_policy
                ),
                model_name=self.deps.model_name,
                scoring_resume=selected_resume,
                draft_resume=selected_resume,
                existing_email=None,
                # No thread: a paste is not a reply to anything.
                external_thread_id=None,
                # Unlike the Nvoids path, routing is *not* precomputed. The
                # recruiter address came out of the text, so the real policy runs
                # and CC is resolved from settings rather than from the paste.
                routing_decision=None,
                parsed_overrides=dict(parsed),
                parser_details=parser_details,
                precomputed_ai_score=cast(float | None, getattr(resume_selection, "ai_score", None)),
                precomputed_ai_summary=cast(str | None, getattr(resume_selection, "ai_summary", None)),
                precomputed_ai_score_source=cast(
                    str | None, getattr(resume_selection, "ai_score_source", None)
                ),
                precomputed_email_embedding_json=cast(
                    str | None, getattr(resume_selection, "email_embedding_json", None)
                ),
                precomputed_resume_embedding_json=cast(
                    str | None, getattr(resume_selection, "resume_embedding_json", None)
                ),
                precomputed_semantic_diag=getattr(resume_selection, "semantic_diag", None),
            ),
            QueuePreparationDependencies(
                parse_email=parse_email,
                hard_filter_check=hard_filter_check,
                # These two are adapters, not passthroughs: the engine calls them
                # positionally with a different shape than the services expose.
                compute_blended_ai_score=lambda subject_arg, body_arg, parsed_arg, settings_arg, email_row, resume, db_ctx=None, owner_id_ctx=None, thread_id_ctx=None: self.scoring_runtime.compute_blended_ai_score(
                    subject=subject_arg,
                    body=body_arg,
                    parsed=parsed_arg,
                    user_settings=settings_arg,
                    email_row=email_row,
                    resume=resume,
                    db=db_ctx,
                    owner_id=owner_id_ctx,
                    external_thread_id=str(thread_id_ctx or ""),
                ),
                policy_f2f_block=self._policy_f2f_block,
                evaluate_routing_policy=self.deps.evaluate_routing_policy,
                greeting_from_to_contact=greeting_from_to_contact,
                build_user_fallback_draft=lambda db_arg, settings_arg, sender, role, parsed_arg, greeting_line, resume_file_name: self.candidate_runtime.build_user_fallback_draft(
                    db_arg,
                    settings_arg,
                    sender=sender,
                    role=role,
                    parsed=parsed_arg,
                    greeting_line=greeting_line,
                    resume_file_name=resume_file_name,
                ),
                generate_reply_with_ai_or_fallback=lambda **kwargs: generate_reply_with_ai_or_fallback(
                    **kwargs
                ),
            ),
        )

        if (
            selected_resume
            and preparation.resume_embedding_json
            and selected_resume.semantic_embedding != preparation.resume_embedding_json
        ):
            selected_resume.semantic_embedding = preparation.resume_embedding_json

        # Three outcomes, three states. `routing_failed` is a recipient problem,
        # not a quality one, so it belongs in Failed Mapping beside the
        # no-address case rather than being auto-rejected as unqualified.
        state = {
            "needs_review": "needs_review",
            "routing_failed": "failed",
            "not_qualified": "auto_rejected",
        }.get(preparation.outcome, "auto_rejected")
        email = build_row(
            score=int(preparation.ai_score * 100),
            decision="Qualified" if state == "needs_review" else "Reject",
            state=state,
            decision_reason=preparation.decision_reason,
            hard_filter_result=preparation.hard_filter_reason,
            auto_reject_reason=preparation.auto_reject_reason,
            skip_reason=preparation.skip_reason,
            ai_score=preparation.ai_score,
            ai_summary=preparation.ai_summary,
            ai_score_source=preparation.ai_score_source,
            ats_score=cast(float | None, getattr(resume_selection, "ats_score", None)),
            ats_summary=cast(str | None, getattr(resume_selection, "ats_summary", None)),
            ats_score_source=cast(str | None, getattr(resume_selection, "ats_score_source", None)),
            ats_breakdown_json=cast(str | None, getattr(resume_selection, "ats_breakdown_json", None)),
            resume_picker_score=cast(
                float | None, getattr(resume_selection, "final_resume_score", None)
            ),
            resume_picker_reason=cast(
                str | None, getattr(resume_selection, "selection_reason", None)
            ),
            resume_picker_candidates_json=cast(
                str | None, getattr(resume_selection, "candidate_rankings_json", None)
            ),
            resume_picker_breakdown_json=cast(
                str | None, getattr(resume_selection, "picker_breakdown_json", None)
            ),
            semantic_embedding=preparation.email_embedding_json,
            resume_asset_id=selected_resume.id if selected_resume else None,
            resume_file_name=selected_resume.file_name if selected_resume else None,
            draft_reply=preparation.draft_reply or "",
            draft_source=preparation.draft_source,
            draft_model=preparation.draft_model,
            draft_ai_error=preparation.draft_ai_error,
            draft_resume_context_status=preparation.draft_resume_context_status,
        )
        apply_screening_decision(email, screening)
        if preparation.routing_decision is not None:
            self.deps.apply_routing_decision(email, preparation.routing_decision)
        if email.state == "failed":
            # Routing could not settle on a recipient. Same shape Failed Mapping
            # uses everywhere else, so the existing repair flow picks it up.
            email.routing_confirmed = False
            email.routing_status = email.routing_status or "ambiguous"
        if email.state == "needs_review":
            apply_resume_sendability(email)
        return self._persist(db, email, contacts)

    # --- persistence ------------------------------------------------------

    def _persist(
        self, db: Session, email: RecruiterEmail, contacts: ManualContacts
    ) -> ManualIntakeResult:
        if not contacts.recruiter_email:
            self._mark_outreach_unavailable(email)

        db.add(email)
        record = opportunity_lineage_service.create_candidate_record(
            db, owner_id=self.deps.owner_id, origin_type="manual"
        )
        email.record_id = record.id
        recruiter_identity_service.stamp_recruiter_email_identity(db, email)
        db.commit()
        db.refresh(email)

        # After the commit on purpose: the generic capture reads a persisted row.
        try:
            self.deps.capture_premium_numbers(db, email)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("manual_intake_premium_capture_failed email_id=%s", email.id)

        return ManualIntakeResult(
            email_id=email.id,
            state=email.state,
            detail=self._detail(email, contacts),
            recruiter_email=contacts.recruiter_email,
        )

    @staticmethod
    def _mark_outreach_unavailable(email: RecruiterEmail) -> None:
        """Processed, kept, and filed where routing problems already live.

        The draft, the resume pick and the screening decision all survive: the
        work is worth keeping, and only the sending is blocked. Nothing is
        guessed to fill the gap.

        The recipient is cleared here rather than merely left unset. Routing runs
        before this point and can resolve an address from stored contacts by
        matching the sender - which for a paste with no address means matching a
        *name*. An address arrived at that way is exactly the "borrowed from a
        similarly-named record" case this feature forbids, and the cost of
        getting it wrong is a stranger receiving the user's resume and document
        numbers. An address is used only when the pasted text contains it.
        """
        email.recipient_email = None
        email.state = "failed"
        email.routing_confirmed = False
        email.routing_status = "ambiguous"
        email.skip_reason = MISSING_EMAIL_SKIP_REASON
        email.last_error = MISSING_EMAIL_DETAIL

    @staticmethod
    def _detail(email: RecruiterEmail, contacts: ManualContacts) -> str:
        if not contacts.recruiter_email:
            return f"Saved to Failed Mapping. {MISSING_EMAIL_DETAIL}"
        if email.state == "needs_review":
            return f"Requirement added to Needs Review for {contacts.recruiter_email}."
        return f"Requirement stored as {email.state}."

    @staticmethod
    def _policy_f2f_block(
        parsed: dict[str, str | int | bool],
        policy: policy_service.PolicyConfig,
        user_settings: UserSettings,
    ) -> tuple[bool, str]:
        """Same rule the Nvoids path applies, read from the effective policy."""
        from app.phase0 import should_block_f2f

        normalized = policy_service.normalize_policy(policy)
        qualification = normalized["qualification"]
        accepted_rule = qualification["draft_rules"]["accepted_location"]
        accepted_locations = [
            loc.strip().lower() for loc in accepted_rule.get("locations", []) if loc.strip()
        ] or [loc.strip().lower() for loc in user_settings.accepted_locations.split(",") if loc.strip()]
        return should_block_f2f(parsed, accepted_locations)
