from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.ai.resume_context_attribution import RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, UserSettings
from app.phase0 import DEFAULT_FALLBACK_DRAFT_TEMPLATE, DEFAULT_SIGNATURE_EMAIL, DEFAULT_SIGNATURE_NAME, DEFAULT_SIGNATURE_PHONE, draft_reply, greeting_from_to_contact, parse_email, render_fallback_draft_template, requested_details_block, skills_from_text
from app.routing import RoutingDecision
from app.services.role_provenance import RoleSource, apply_role_family, normalize_role
from app.services.phone_intelligence_workflow_service import PhoneIntelligenceWorkflowResult, PhoneIntelligenceWorkflowService

logger = logging.getLogger(__name__)


def resolve_resume_display_name(user_settings: UserSettings, resume_file_name: str | None) -> str | None:
    raw_name = (resume_file_name or "").strip()
    if not raw_name:
        return None
    configured_name = str(getattr(user_settings, "resume_display_name", "") or "").strip()
    if not configured_name:
        return raw_name
    original_suffix = Path(raw_name).suffix
    configured_suffix = Path(configured_name).suffix
    base_name = configured_name[: -len(configured_suffix)] if configured_suffix else configured_name
    base_name = base_name.rstrip(" .")
    if not base_name:
        return raw_name
    return f"{base_name}{original_suffix}" if original_suffix else base_name


@dataclass
class CandidateRuntimeDeps:
    get_settings: Callable[[Session], UserSettings]
    evaluate_routing_policy: Callable[..., RoutingDecision]
    apply_routing_decision: Callable[[RecruiterEmail, RoutingDecision], None]


class CandidateRuntimeService:
    def __init__(self, deps: CandidateRuntimeDeps):
        self.deps = deps
        self.phone_intelligence_workflow = PhoneIntelligenceWorkflowService(manage_transaction=True)

    def capture_premium_numbers(self, db: Session, email: RecruiterEmail) -> PhoneIntelligenceWorkflowResult | None:
        try:
            return self.phone_intelligence_workflow.process_email(db, email, source="candidate_runtime")
        except Exception as exc:
            db.rollback()
            logger.warning("Premium numbers extraction skipped for email_id=%s: %s", email.id, exc)
            return None

    def build_user_fallback_draft(
        self,
        db: Session,
        user_settings: UserSettings,
        *,
        sender: str,
        role: str,
        parsed: dict[str, str | int | bool],
        greeting_line: str,
        resume_file_name: str | None,
    ) -> str:
        _ = db
        template = (user_settings.fallback_draft_template or DEFAULT_FALLBACK_DRAFT_TEMPLATE).strip()
        signature_name = (user_settings.signature_name or "").strip() or DEFAULT_SIGNATURE_NAME
        signature_phone = (user_settings.signature_phone or "").strip() or DEFAULT_SIGNATURE_PHONE
        signature_email = (user_settings.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL
        context = {
            "greeting": greeting_line,
            "role": role,
            "sender": sender,
            "location": str(parsed.get("location", "")),
            "salary_text": str(parsed.get("salary_text", "")),
            "skills_list": "\n".join(f"- {skill}" for skill in skills_from_text(str(parsed.get("skills_text", "")))),
            "skills_inline": ", ".join(skills_from_text(str(parsed.get("skills_text", "")))),
            "resume_file_name": resume_file_name or "",
            "signature_name": signature_name,
            "signature_phone": signature_phone,
            "signature_email": signature_email,
            "requested_details_block": requested_details_block(bool(parsed.get("asks_contact_fields", False))),
        }
        rendered = render_fallback_draft_template(template, context)
        if rendered.strip():
            return rendered
        return draft_reply(sender, role, parsed, greeting_line)

    def repair_unknown_role_drafts(self, db: Session, emails: list[RecruiterEmail]) -> None:
        changed = False
        user_settings = self.deps.get_settings(db)
        for email in emails:
            if email.state != "needs_review":
                continue
            if email.role != "Unknown Role" and "Unknown Role" not in email.draft_reply:
                continue
            parsed = parse_email(email.subject, email.body)
            role = str(parsed["role"])
            if role == "Unknown Role":
                continue
            # Repair path: this exists to replace the "Unknown Role" sentinel, so it
            # overwrites deliberately rather than preserving the existing value.
            email.role = normalize_role(role)
            email.role_source = RoleSource.EXTRACTED
            email.location = str(parsed["location"])
            email.salary_text = str(parsed["salary_text"])
            email.skills_text = str(parsed["skills_text"])
            # The role just stopped being "Unknown Role", so the family classified
            # from the old sentinel is now wrong.
            apply_role_family(email, role=email.role, skills_text=email.skills_text)
            if "Unknown Role" in email.draft_reply:
                greeting_line = greeting_from_to_contact(email.recipient_email, email.body)
                email.draft_reply = self.build_user_fallback_draft(
                    db,
                    user_settings,
                    sender=email.sender,
                    role=role,
                    parsed=parsed,
                    greeting_line=greeting_line,
                    resume_file_name=resolve_resume_display_name(user_settings, email.resume_file_name),
                )
                email.draft_source = "rules_only"
                email.draft_model = None
                email.draft_ai_error = None
                email.draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY
            changed = True
        if changed:
            db.commit()

    def refresh_unconfirmed_routing(self, db: Session, emails: list[RecruiterEmail]) -> None:
        changed = False
        for email in emails:
            if email.source != "gmail" or email.routing_confirmed:
                continue
            routing = self.deps.evaluate_routing_policy(
                db,
                email.sender,
                email.subject,
                email.body,
                "",
                email.routing_confirmed,
            )
            if (
                email.recipient_email == routing.to_email
                and email.cc_email == routing.cc_email
                and email.routing_status == routing.status
                and float(email.routing_confidence or 0.0) == routing.confidence
            ):
                continue
            self.deps.apply_routing_decision(email, routing)
            if routing.should_mark_failed:
                email.state = routing.recommended_state
                email.last_error = "Could not resolve recruiter To and employer CC"
                email.skip_reason = routing.recommended_skip_reason
            changed = True
        if changed:
            db.commit()
