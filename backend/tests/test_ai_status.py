import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.gates.job_description_gate import classify_email_intent
from app.models import UserSettings
from app.runtime_state import runtime_state


class AIStatusTests(unittest.TestCase):
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
        with self.SessionLocal() as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    gmail_query="is:unread",
                    default_gmail_query="is:unread",
                    feature_groq_job_parser_enabled=True,
                )
            )
            db.commit()
        self._orig_groq_api_key = main.settings.groq_api_key
        self._orig_groq_base_url = main.settings.groq_base_url
        self._orig_groq_model = main.settings.groq_gate_model
        self._orig_intent_gate_provider = main.settings.intent_gate_provider
        self._reset_groq_runtime()

    def tearDown(self) -> None:
        main.settings.groq_api_key = self._orig_groq_api_key
        main.settings.groq_base_url = self._orig_groq_base_url
        main.settings.groq_gate_model = self._orig_groq_model
        main.settings.intent_gate_provider = self._orig_intent_gate_provider
        self._reset_groq_runtime()
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _reset_groq_runtime(self) -> None:
        runtime_state.groq_last_error = None
        runtime_state.groq_last_attempted_at = None
        runtime_state.groq_last_success_at = None
        runtime_state.groq_last_duration_ms = None
        runtime_state.groq_last_provider_result = None
        runtime_state.groq_request_mode = ""
        runtime_state.intent_gate_provider = ""
        runtime_state.intent_gate_last_error = None
        runtime_state.intent_gate_last_attempted_at = None
        runtime_state.intent_gate_last_success_at = None
        runtime_state.intent_gate_last_duration_ms = None
        runtime_state.intent_gate_last_provider_result = None
        runtime_state.intent_gate_last_rung = ""

    def test_ai_status_reports_missing_groq_config(self) -> None:
        main.settings.groq_api_key = ""
        main.settings.groq_base_url = ""
        main.settings.groq_gate_model = ""

        response = self.client.get("/ai/status")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["groq_enabled_in_settings"])
        self.assertFalse(payload["groq_configured"])
        self.assertEqual(payload["groq_runtime_healthy"], None)
        self.assertEqual(payload["groq_request_mode"], "json_object")
        self.assertIn("missing API key, model, or base URL", payload["groq_detail"])

    @patch(
        "app.gates.job_description_gate.classify_job_description_taxonomy",
        return_value=SimpleNamespace(
            intent_type="unknown",
            action="needs_review",
            confidence=0.51,
            reason="uncertain",
            evidence=[],
            negative_evidence=[],
        ),
    )
    @patch(
        "app.gates.job_description_gate.intent_chat_json",
        return_value=(
            {
                "intent_type": "recruiter_job_requirement",
                "action": "process_for_queue",
                "confidence": 0.93,
                "reason": "real job",
                "evidence": ["job description"],
                "negative_evidence": [],
                "learning_signals": [],
            },
            None,
            None,
        ),
    )
    def test_ai_status_reports_groq_runtime_healthy_after_success(self, _mock_provider, _mock_taxonomy) -> None:
        main.settings.groq_api_key = "test-key"
        main.settings.groq_base_url = "https://api.groq.com/openai/v1"
        main.settings.groq_gate_model = "llama-3.1-8b-instant"
        main.settings.intent_gate_provider = "groq"

        classify_email_intent(
            sender="jobs@example.com",
            subject="Backend Engineer",
            body="Job Description: Python role",
            groq_enabled=True,
        )

        response = self.client.get("/ai/status")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["groq_configured"])
        self.assertTrue(payload["groq_runtime_healthy"])
        self.assertEqual(payload["groq_last_error"], None)
        self.assertEqual(payload["groq_model"], "llama-3.1-8b-instant")
        self.assertEqual(payload["groq_request_mode"], "json_object")

    @patch(
        "app.gates.job_description_gate.classify_job_description_taxonomy",
        return_value=SimpleNamespace(
            intent_type="unknown",
            action="needs_review",
            confidence=0.51,
            reason="uncertain",
            evidence=[],
            negative_evidence=[],
        ),
    )
    @patch(
        "app.gates.job_description_gate.intent_chat_json",
        return_value=(None, "missing_groq_api_key", None),
    )
    def test_ai_status_reports_groq_fallback_error(self, _mock_provider, _mock_taxonomy) -> None:
        main.settings.groq_api_key = ""
        main.settings.groq_base_url = "https://api.groq.com/openai/v1"
        main.settings.groq_gate_model = "llama-3.1-8b-instant"
        main.settings.intent_gate_provider = "groq"

        classify_email_intent(
            sender="jobs@example.com",
            subject="Backend Engineer",
            body="Job Description: Python role",
            groq_enabled=True,
        )

        response = self.client.get("/ai/status")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["groq_runtime_healthy"])
        self.assertFalse(payload["groq_configured"])
        self.assertEqual(payload["groq_last_error"], "missing_groq_api_key")
        self.assertEqual(payload["groq_request_mode"], "json_object")
        self.assertIn("missing api key", payload["groq_detail"].lower())

    @patch(
        "app.gates.job_description_gate.classify_job_description_taxonomy",
        return_value=SimpleNamespace(
            intent_type="unknown",
            action="needs_review",
            confidence=0.51,
            reason="uncertain",
            evidence=[],
            negative_evidence=[],
        ),
    )
    @patch(
        "app.gates.job_description_gate.intent_chat_json",
        return_value=(None, "deepseek_timeout", None),
    )
    def test_ai_status_reports_the_provider_that_actually_answered(
        self, _mock_provider, _mock_taxonomy
    ) -> None:
        """A DeepSeek failure must not be reported in a field labelled Groq.

        The AI Access card renders `groq_runtime_healthy` as "Groq Runtime". If the
        gate wrote another provider's health there, the card would show a Groq
        outage that never happened - and hide the one that did.
        """
        main.settings.groq_api_key = "test-key"
        main.settings.groq_base_url = "https://api.groq.com/openai/v1"
        main.settings.groq_gate_model = "llama-3.1-8b-instant"
        main.settings.intent_gate_provider = "deepseek"

        classify_email_intent(
            sender="jobs@example.com",
            subject="Backend Engineer",
            body="Job Description: Python role",
            groq_enabled=True,
        )

        payload = self.client.get("/ai/status").json()

        self.assertEqual(payload["intent_gate_provider"], "deepseek")
        self.assertFalse(payload["intent_gate_runtime_healthy"])
        self.assertEqual(payload["intent_gate_last_error"], "deepseek_timeout")
        self.assertIn("deepseek_timeout", payload["intent_gate_detail"])
        # Groq never ran, so its fields must stay untouched rather than borrow
        # DeepSeek's failure.
        self.assertIsNone(payload["groq_runtime_healthy"])
        self.assertIsNone(payload["groq_last_error"])
        self.assertIn("idle", payload["groq_detail"].lower())


if __name__ == "__main__":
    unittest.main()
