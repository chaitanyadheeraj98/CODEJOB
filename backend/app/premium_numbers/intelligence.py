from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import RecruiterEmail
from app.services.phone_intelligence_workflow_service import PhoneIntelligenceWorkflowService

OPPORTUNITY_STATUS_VALUES = {"New", "Called", "Applied", "Follow Up", "Closed", "Not Interested"}


def process_email_number_intelligence(db: Session, email: RecruiterEmail) -> None:
    workflow = PhoneIntelligenceWorkflowService(manage_transaction=False)
    workflow.classify_only(db, email, source="legacy_intelligence")
