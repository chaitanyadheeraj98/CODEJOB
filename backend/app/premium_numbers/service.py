from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import RecruiterEmail
from app.services.phone_intelligence_workflow_service import PhoneIntelligenceWorkflowService


def extract_and_store_premium_numbers(db: Session, email: RecruiterEmail) -> int:
    workflow = PhoneIntelligenceWorkflowService(manage_transaction=False)
    return workflow.extract_only(db, email, source="legacy_extract")
