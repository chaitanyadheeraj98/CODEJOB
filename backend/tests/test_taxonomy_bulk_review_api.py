import json
import os
import unittest
from unittest import mock

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import CanonicalEntityTaxonomyEntry, CustomSkillTaxonomyEntry, RecruiterEmail
from app.skill_taxonomy import clear_skill_taxonomy_cache


LONG_BLOB = "Hands on experience in building and optimizing backend applications using"


class TaxonomyBulkReviewApiTests(unittest.TestCase):
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
        clear_skill_taxonomy_cache()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        clear_skill_taxonomy_cache()

    def _seed_email(self, *, sender: str, unknown_skills: list[str] | None = None, location: str | None = None) -> None:
        details: dict[str, object] = {}
        if unknown_skills is not None:
            details["unknown_skills"] = unknown_skills
        if location is not None:
            details["ai_extractor_result"] = {"primary_location": location}
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=main.settings.owner_id,
                    sender=sender,
                    subject="Role",
                    body="Body",
                    role="Engineer",
                    location="Remote",
                    salary_text="",
                    skills_text="Java",
                    score=0,
                    decision="qualified",
                    state="needs_review",
                    source="gmail",
                    parser_details_json=json.dumps(details),
                )
            )
            db.commit()

    def _seed_skill_queue(self) -> None:
        self._seed_email(sender="one@example.com", unknown_skills=["Temporal Workflow", LONG_BLOB])
        self._seed_email(sender="two@example.com", unknown_skills=["Temporal Workflow", "Agent Studio"])

    def _classify(self, scope: str) -> dict:
        response = self.client.post(
            "/settings/taxonomy/bulk-review/classify",
            json={"scope": scope, "use_model": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _skill_entry_count(self) -> int:
        with self.SessionLocal() as db:
            return db.query(CustomSkillTaxonomyEntry).count()

    def test_classify_buckets_skills_and_writes_nothing(self) -> None:
        self._seed_skill_queue()
        payload = self._classify("skill")

        self.assertEqual(payload["total_pending"], 3)
        self.assertEqual(sum(payload["counts"].values()), 3)
        by_key = {item["key"]: item for item in payload["recommendations"]}

        self.assertEqual(by_key["temporal workflow"]["bucket"], "approve")
        self.assertEqual(by_key["temporal workflow"]["occurrence_count"], 2)
        self.assertFalse(by_key["temporal workflow"]["locked"])

        blob_key = next(key for key in by_key if key.startswith("hands on experience"))
        self.assertEqual(by_key[blob_key]["bucket"], "dismiss")
        self.assertTrue(by_key[blob_key]["locked"])

        self.assertEqual(by_key["agent studio"]["bucket"], "review")

        self.assertEqual(self._skill_entry_count(), 0)

    def test_classify_without_a_key_reports_the_model_error_and_still_returns_rules(self) -> None:
        self._seed_skill_queue()
        with mock.patch.object(main.settings, "deepseek_api_key", ""):
            response = self.client.post(
                "/settings/taxonomy/bulk-review/classify",
                json={"scope": "skill", "use_model": True},
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("DeepSeek API key is missing", payload["model_error"])
        self.assertIsNone(payload["model_used"])
        self.assertEqual(payload["total_pending"], 3)

    def test_apply_with_a_wrong_expected_count_returns_409_and_writes_nothing(self) -> None:
        self._seed_skill_queue()
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={
                "scope": "skill",
                "action": "approve",
                "keys": ["temporal workflow", "agent studio"],
                "expected_count": 5,
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Confirmed 5", response.json()["detail"])
        self.assertEqual(self._skill_entry_count(), 0)

    def test_apply_approves_exactly_the_confirmed_keys(self) -> None:
        self._seed_skill_queue()
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={
                "scope": "skill",
                "action": "approve",
                "keys": ["temporal workflow"],
                "expected_count": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["applied_count"], 1)
        self.assertEqual(payload["applied_names"], ["Temporal Workflow"])
        self.assertEqual(payload["skipped"], [])

        with self.SessionLocal() as db:
            rows = db.query(CustomSkillTaxonomyEntry).all()
            self.assertEqual([(row.canonical_name, row.status) for row in rows], [("Temporal Workflow", "approved")])

        remaining = self._classify("skill")
        self.assertNotIn("temporal workflow", {item["key"] for item in remaining["recommendations"]})

    def test_apply_refuses_to_approve_a_locked_record(self) -> None:
        self._seed_skill_queue()
        blob_key = next(
            item["key"]
            for item in self._classify("skill")["recommendations"]
            if item["locked"]
        )
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={"scope": "skill", "action": "approve", "keys": [blob_key], "expected_count": 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["applied_count"], 0)
        self.assertEqual(payload["skipped"], [{"key": blob_key, "reason": "unsafe_for_approval"}])
        self.assertEqual(self._skill_entry_count(), 0)

    def test_apply_dismisses_a_locked_record(self) -> None:
        self._seed_skill_queue()
        blob_key = next(
            item["key"]
            for item in self._classify("skill")["recommendations"]
            if item["locked"]
        )
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={"scope": "skill", "action": "dismiss", "keys": [blob_key], "expected_count": 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied_count"], 1)
        with self.SessionLocal() as db:
            rows = db.query(CustomSkillTaxonomyEntry).all()
            self.assertEqual([row.status for row in rows], ["dismissed"])

    def test_apply_skips_a_key_that_is_no_longer_pending(self) -> None:
        self._seed_skill_queue()
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={
                "scope": "skill",
                "action": "approve",
                "keys": ["temporal workflow", "never seen"],
                "expected_count": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["applied_count"], 1)
        self.assertEqual(payload["skipped"], [{"key": "never seen", "reason": "no_longer_pending"}])

    def test_location_scope_classifies_and_applies(self) -> None:
        self._seed_email(sender="a@example.com", location="Omaha, NE")
        self._seed_email(sender="b@example.com", location="Omaha, NE")
        self._seed_email(sender="c@example.com", location="Trenton, NJ")

        payload = self._classify("location")
        by_key = {item["key"]: item for item in payload["recommendations"]}
        self.assertEqual(by_key["omaha ne"]["bucket"], "approve")
        self.assertEqual(by_key["trenton nj"]["bucket"], "review")

        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={"scope": "location", "action": "approve", "keys": ["omaha ne"], "expected_count": 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied_names"], ["Omaha, NE"])
        with self.SessionLocal() as db:
            rows = db.query(CanonicalEntityTaxonomyEntry).all()
            self.assertEqual([(row.entity_type, row.canonical_name, row.status) for row in rows], [("location", "Omaha, NE", "approved")])

    def test_apply_rejects_an_unknown_scope(self) -> None:
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={"scope": "planet", "action": "approve", "keys": [], "expected_count": 0},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_apply_rejects_an_oversized_key_list(self) -> None:
        response = self.client.post(
            "/settings/taxonomy/bulk-review/apply",
            json={
                "scope": "skill",
                "action": "approve",
                "keys": [f"key-{index}" for index in range(2001)],
                "expected_count": 2001,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self._skill_entry_count(), 0)


if __name__ == "__main__":
    unittest.main()
