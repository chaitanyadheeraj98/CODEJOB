import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.job_intent_learning import approved_learning_signals_for_owner, record_pending_job_intent_learning
from app.models import JobIntentTaxonomyEntry


class JobIntentLearningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_pending_learning_is_created_and_not_used_until_approved(self) -> None:
        with self.SessionLocal() as db:
            record_pending_job_intent_learning(
                db,
                owner_id=main.settings.owner_id,
                intent_type="recruiter_job_requirement",
                confidence=0.91,
                evidence=["google groups footer + share resume", "job description below"],
                negative_evidence=["weak_footer:unsubscribe"],
                learned_signals=[],
            )
            db.commit()

            approved = approved_learning_signals_for_owner(db, main.settings.owner_id)
            self.assertEqual(approved, [])

        pending = self.client.get("/settings/job-intent-learning/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        payload = pending.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["status"], "pending")

        approve = self.client.post(
            "/settings/job-intent-learning/approve",
            json={"phrase": payload[0]["phrase"], "polarity": payload[0]["polarity"]},
        )
        self.assertEqual(approve.status_code, 200, approve.text)
        self.assertEqual(approve.json()["status"], "approved")

        with self.SessionLocal() as db:
            approved = approved_learning_signals_for_owner(db, main.settings.owner_id)
            self.assertEqual(len(approved), 1)
            self.assertEqual(approved[0].phrase, payload[0]["phrase"])

    def test_dismissed_learning_signal_stays_suppressed_from_pending(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                JobIntentTaxonomyEntry(
                    owner_id=main.settings.owner_id,
                    phrase="consultant hotlist",
                    normalized_phrase="consultant hotlist",
                    polarity="negative_candidate_hotlist",
                    source_examples_count=3,
                    sample_evidence_json='["consultant hotlist","please share relevant requirements"]',
                    confidence_aggregate=0.86,
                    last_intent_type="candidate_marketing_or_hotlist",
                    status="pending",
                )
            )
            db.commit()

        dismissed = self.client.post(
            "/settings/job-intent-learning/dismiss",
            json={"phrase": "consultant hotlist", "polarity": "negative_candidate_hotlist"},
        )
        self.assertEqual(dismissed.status_code, 200, dismissed.text)
        self.assertEqual(dismissed.json()["status"], "dismissed")

        pending = self.client.get("/settings/job-intent-learning/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json(), [])

        approved = self.client.get("/settings/job-intent-learning/approved")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json(), [])


if __name__ == "__main__":
    unittest.main()
