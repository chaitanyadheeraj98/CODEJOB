import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import RecruiterEmail
from app.services.requirement_expansion_service import RequirementExpansionService
from app.services.role_manifest_service import RoleManifestService


DELOITTE_SOURCE = """Please share resumes along with your LinkedIn URL
VISA: USC/GC Only
1. AI Pod Product Owner
Job ID: DLTJP00057258
14+ years total experience and 10+ years US experience
2. Knowledge Engineer - AI Architect
Job ID: DLTJP00057259
14+ years total experience and 10+ years US experience"""


class RequirementExpansionServiceTests(unittest.TestCase):
    def test_reprocessing_multi_role_parent_does_not_duplicate_children(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        manifest_payload = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.96,
            "shared_constraints": [{"type": "work_authorization", "value": "USC/GC Only", "start_line": 2, "end_line": 2}],
            "roles": [
                {"index": 1, "title_hint": "AI Pod Product Owner", "requisition_id": "DLTJP00057258", "start_line": 3, "end_line": 5, "confidence": 0.98},
                {"index": 2, "title_hint": "Knowledge Engineer - AI Architect", "requisition_id": "DLTJP00057259", "start_line": 6, "end_line": 8, "confidence": 0.98},
            ],
        }
        manifest = RoleManifestService(provider=lambda system, user: manifest_payload).detect(DELOITTE_SOURCE)

        with Session(engine) as db:
            parent = RecruiterEmail(
                owner_id="default-owner",
                sender="recruiter@example.com",
                subject="Deloitte roles",
                body=DELOITTE_SOURCE,
                role="Ml Engineer",
                state="needs_review",
                decision="Qualified",
                external_message_id="gmail-message-4435",
                source="gmail",
            )
            db.add(parent)
            db.commit()
            service = RequirementExpansionService()

            first = service.expand(db, parent, manifest, materialize=True)
            second = service.expand(db, parent, manifest, materialize=True)

            self.assertEqual(first.child_ids, second.child_ids)
            self.assertEqual(db.query(RecruiterEmail).filter(RecruiterEmail.source_parent_email_id == parent.id).count(), 2)
            self.assertEqual(parent.sendability_status, "superseded_multi_role")

            sent_child = db.get(RecruiterEmail, first.child_ids[0])
            assert sent_child is not None
            sent_child.state = "approved_sent"
            sent_child.sendability_status = "sendable"
            sent_child.draft_reply = "Already sent"
            db.commit()

            service.expand(db, parent, manifest, materialize=True)
            db.refresh(sent_child)

            self.assertEqual(sent_child.state, "approved_sent")
            self.assertEqual(sent_child.sendability_status, "sendable")
            self.assertEqual(sent_child.draft_reply, "Already sent")


if __name__ == "__main__":
    unittest.main()
