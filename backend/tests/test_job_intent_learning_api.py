import os
import unittest
from unittest.mock import patch

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

        with patch("app.main.clear_job_intent_signal_embedding_cache") as clear_cache:
            approve = self.client.post(
                "/settings/job-intent-learning/approve",
                json={"phrase": payload[0]["phrase"], "polarity": payload[0]["polarity"]},
            )
            clear_cache.assert_called_once()
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

    def test_bulk_approve_pending_learning_signals_moves_all_to_approved(self) -> None:
        with self.SessionLocal() as db:
            db.add_all(
                [
                    JobIntentTaxonomyEntry(
                        owner_id=main.settings.owner_id,
                        phrase="share updated resume",
                        normalized_phrase="share updated resume",
                        polarity="positive_recruiter_jd",
                        source_examples_count=2,
                        sample_evidence_json='["share updated resume"]',
                        confidence_aggregate=0.77,
                        last_intent_type="recruiter_job_requirement",
                        status="pending",
                    ),
                    JobIntentTaxonomyEntry(
                        owner_id=main.settings.owner_id,
                        phrase="consultant hotlist",
                        normalized_phrase="consultant hotlist",
                        polarity="negative_candidate_hotlist",
                        source_examples_count=3,
                        sample_evidence_json='["consultant hotlist"]',
                        confidence_aggregate=0.86,
                        last_intent_type="candidate_marketing_or_hotlist",
                        status="pending",
                    ),
                ]
            )
            db.commit()

        approved = self.client.post("/settings/job-intent-learning/approve-all")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(
            approved.json(),
            {
                "processed_count": 2,
                "approved_count": 2,
                "skipped_count": 0,
                "approved_signals": [
                    {"phrase": "consultant hotlist", "polarity": "negative_candidate_hotlist"},
                    {"phrase": "share updated resume", "polarity": "positive_recruiter_jd"},
                ],
            },
        )

        pending = self.client.get("/settings/job-intent-learning/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json(), [])

        listed = self.client.get("/settings/job-intent-learning/approved")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [(item["phrase"], item["polarity"]) for item in listed.json()],
            [
                ("consultant hotlist", "negative_candidate_hotlist"),
                ("share updated resume", "positive_recruiter_jd"),
            ],
        )

    def test_bulk_approve_pending_learning_signals_returns_zero_counts_when_empty(self) -> None:
        approved = self.client.post("/settings/job-intent-learning/approve-all")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(
            approved.json(),
            {
                "processed_count": 0,
                "approved_count": 0,
                "skipped_count": 0,
                "approved_signals": [],
            },
        )

    def test_bulk_approve_does_not_reintroduce_dismissed_learning_signal(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                JobIntentTaxonomyEntry(
                    owner_id=main.settings.owner_id,
                    phrase="dismissed phrase",
                    normalized_phrase="dismissed phrase",
                    polarity="negative_newsletter",
                    source_examples_count=1,
                    sample_evidence_json='["dismissed phrase"]',
                    confidence_aggregate=0.5,
                    last_intent_type="newsletter",
                    status="dismissed",
                )
            )
            db.commit()

        approved = self.client.post("/settings/job-intent-learning/approve-all")
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["approved_signals"], [])

        pending = self.client.get("/settings/job-intent-learning/pending")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json(), [])

    def test_toggle_polarity_flips_an_approved_signal_in_place(self) -> None:
        with self.SessionLocal() as db:
            entry = JobIntentTaxonomyEntry(
                owner_id=main.settings.owner_id,
                phrase="Woodland Hills, CA or Mason, OH",
                normalized_phrase="woodland hills, ca or mason, oh",
                polarity="positive_recruiter_jd",
                source_examples_count=7,
                sample_evidence_json="[]",
                confidence_aggregate=0.81,
                last_intent_type="recruiter_job_requirement",
                status="approved",
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            entry_id = entry.id

        response = self.client.post(f"/settings/job-intent-learning/{entry_id}/toggle-polarity")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["polarity"], "negative_newsletter")
        self.assertEqual(response.json()["status"], "approved")

        response = self.client.post(f"/settings/job-intent-learning/{entry_id}/toggle-polarity")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["polarity"], "positive_recruiter_jd")

    def test_toggle_polarity_404s_for_unknown_entry(self) -> None:
        response = self.client.post("/settings/job-intent-learning/999999/toggle-polarity")
        self.assertEqual(response.status_code, 404)

    def test_embedded_signals_returns_top_ranked_approved_signals_with_provider_flag(self) -> None:
        with self.SessionLocal() as db:
            approved_entry = JobIntentTaxonomyEntry(
                owner_id=main.settings.owner_id,
                phrase="share updated resume",
                normalized_phrase="share updated resume",
                polarity="positive_recruiter_jd",
                source_examples_count=2,
                sample_evidence_json="[]",
                confidence_aggregate=0.77,
                last_intent_type="recruiter_job_requirement",
                status="approved",
            )
            db.add_all(
                [
                    approved_entry,
                    JobIntentTaxonomyEntry(
                        owner_id=main.settings.owner_id,
                        phrase="consultant hotlist",
                        normalized_phrase="consultant hotlist",
                        polarity="negative_candidate_hotlist",
                        source_examples_count=3,
                        sample_evidence_json="[]",
                        confidence_aggregate=0.86,
                        last_intent_type="candidate_marketing_or_hotlist",
                        status="pending",
                    ),
                ]
            )
            db.commit()
            db.refresh(approved_entry)
            approved_entry_id = approved_entry.id

        with patch("app.main.generate_embeddings", return_value=([[0.1, 0.2]], "sbert")) as mocked:
            response = self.client.get("/settings/job-intent-learning/embedded")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            [{"id": approved_entry_id, "phrase": "share updated resume", "polarity": "positive_recruiter_jd", "confidence": 0.77, "embedded": True}],
        )
        mocked.assert_called_once_with(["share updated resume"])

    def test_embedded_signal_id_can_be_used_to_toggle_its_polarity(self) -> None:
        with self.SessionLocal() as db:
            entry = JobIntentTaxonomyEntry(
                owner_id=main.settings.owner_id,
                phrase="50% off every evergreen Cultivated Culture product",
                normalized_phrase="50% off every evergreen cultivated culture product",
                polarity="positive_recruiter_jd",
                source_examples_count=4,
                sample_evidence_json="[]",
                confidence_aggregate=0.9,
                last_intent_type="recruiter_job_requirement",
                status="approved",
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            entry_id = entry.id

        with patch("app.main.generate_embeddings", return_value=([[0.1, 0.2]], "sbert")):
            listed = self.client.get("/settings/job-intent-learning/embedded")
        self.assertEqual(listed.status_code, 200, listed.text)
        embedded_id = listed.json()[0]["id"]

        toggled = self.client.post(f"/settings/job-intent-learning/{embedded_id}/toggle-polarity")
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertEqual(toggled.json()["polarity"], "negative_newsletter")
        self.assertEqual(embedded_id, entry_id)

    def test_embedded_signals_returns_empty_list_when_nothing_approved(self) -> None:
        response = self.client.get("/settings/job-intent-learning/embedded")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
