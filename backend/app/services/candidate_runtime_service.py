from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.ai.resume_context_attribution import RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, UserSettings
from app.phase0 import DEFAULT_FALLBACK_DRAFT_TEMPLATE, DEFAULT_SIGNATURE_EMAIL, DEFAULT_SIGNATURE_NAME, DEFAULT_SIGNATURE_PHONE, draft_reply, greeting_from_to_contact, parse_email, render_fallback_draft_template, requested_details_block, skills_from_text
from app.routing import RoutingDecision
from app.premium_numbers import extract_and_store_premium_numbers
from app.premium_numbers.intelligence import process_email_number_intelligence

logger = logging.getLogger(__name__)


@dataclass
class CandidateRuntimeDeps:
    get_settings: Callable[[Session], UserSettings]
    evaluate_routing_policy: Callable[..., RoutingDecision]
    apply_routing_decision: Callable[[RecruiterEmail, RoutingDecision], None]


class CandidateRuntimeService:
    def __init__(self, deps: CandidateRuntimeDeps):
        self.deps = deps

    def capture_premium_numbers(self, db: Session, email: RecruiterEmail) -> None:
        try:
            extract_and_store_premium_numbers(db, email)
            process_email_number_intelligence(db, email)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Premium numbers extraction skipped for email_id=%s: %s", email.id, exc)

    def apply_draft_learning(self, db: Session, draft: str) -> str:
        _ = db
        return draft

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
            return self.apply_draft_learning(db, rendered)
        return self.apply_draft_learning(db, draft_reply(sender, role, parsed, greeting_line))

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
            email.role = role
            email.location = str(parsed["location"])
            email.salary_text = str(parsed["salary_text"])
            email.skills_text = str(parsed["skills_text"])
            if "Unknown Role" in email.draft_reply:
                greeting_line = greeting_from_to_contact(email.recipient_email, email.body)
                email.draft_reply = self.build_user_fallback_draft(
                    db,
                    user_settings,
                    sender=email.sender,
                    role=role,
                    parsed=parsed,
                    greeting_line=greeting_line,
                    resume_file_name=email.resume_file_name,
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
