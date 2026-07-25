import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import BadRequestError

from app.ai.groq_client import _parse_json_object, groq_chat_json, groq_request_mode_for_model
from app.config import settings


def _fake_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


class GroqClientTests(unittest.TestCase):
    def test_parser_accepts_wrapped_objects_and_retains_error_types(self) -> None:
        self.assertEqual(_parse_json_object('Result: {"action":"needs_review"} done.'), {"action": "needs_review"})
        with self.assertRaises(json.JSONDecodeError):
            _parse_json_object('["not", "an", "object"]')
        with self.assertRaisesRegex(ValueError, "empty content"):
            _parse_json_object("")

    def setUp(self) -> None:
        self._orig_api_key = settings.groq_api_key
        self._orig_model = settings.groq_gate_model
        settings.groq_api_key = "test-key"

    def tearDown(self) -> None:
        settings.groq_api_key = self._orig_api_key
        settings.groq_gate_model = self._orig_model

    def test_request_mode_uses_json_schema_for_supported_model(self) -> None:
        self.assertEqual(groq_request_mode_for_model("openai/gpt-oss-20b"), "json_schema")

    def test_request_mode_uses_json_object_for_llama_model(self) -> None:
        self.assertEqual(groq_request_mode_for_model("llama-3.1-8b-instant"), "json_object")

    @patch("app.ai.groq_client._build_client")
    def test_json_object_mode_is_used_for_unsupported_models(self, mock_build_client) -> None:
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _fake_response(
            '{"intent_type":"unknown","action":"needs_review","confidence":0.5,"reason":"uncertain","evidence":[],"negative_evidence":[]}'
        )

        payload, error = groq_chat_json(
            system_prompt="sys",
            user_prompt="user",
            schema={
                "type": "object",
                "properties": {
                    "intent_type": {"type": "string"},
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "negative_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence"],
                "additionalProperties": False,
            },
            model="llama-3.1-8b-instant",
        )

        self.assertIsNone(error)
        self.assertEqual(payload["intent_type"], "unknown")
        request_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["response_format"], {"type": "json_object"})

    @patch("app.ai.groq_client._build_client")
    def test_json_schema_mode_is_used_for_supported_models(self, mock_build_client) -> None:
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _fake_response(
            '{"intent_type":"unknown","action":"needs_review","confidence":0.5,"reason":"uncertain","evidence":[],"negative_evidence":[]}'
        )

        payload, error = groq_chat_json(
            system_prompt="sys",
            user_prompt="user",
            schema={
                "type": "object",
                "properties": {
                    "intent_type": {"type": "string"},
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "negative_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence"],
                "additionalProperties": False,
            },
            model="openai/gpt-oss-20b",
        )

        self.assertIsNone(error)
        self.assertEqual(payload["intent_type"], "unknown")
        request_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["response_format"]["type"], "json_schema")

    @patch("app.ai.groq_client._build_client")
    def test_invalid_shape_in_json_object_mode_fails_safely(self, mock_build_client) -> None:
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.return_value = _fake_response(
            '{"intent_type":"unknown","reason":"missing fields"}'
        )

        payload, error = groq_chat_json(
            system_prompt="sys",
            user_prompt="user",
            schema={
                "type": "object",
                "properties": {
                    "intent_type": {"type": "string"},
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "negative_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence"],
                "additionalProperties": False,
            },
            model="llama-3.1-8b-instant",
        )

        self.assertIsNone(payload)
        self.assertEqual(error, "groq_invalid_shape")

    @patch("app.ai.groq_client._build_client")
    def test_unsupported_json_schema_error_maps_to_stable_code(self, mock_build_client) -> None:
        mock_client = mock_build_client.return_value
        mock_client.chat.completions.create.side_effect = BadRequestError(
            message="This model does not support response format `json_schema`. See supported models.",
            response=httpx.Response(status_code=400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")),
            body={
                "error": {
                    "message": "This model does not support response format `json_schema`.",
                    "param": "response_format",
                }
            },
        )

        payload, error = groq_chat_json(
            system_prompt="sys",
            user_prompt="user",
            schema={
                "type": "object",
                "properties": {
                    "intent_type": {"type": "string"},
                    "action": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "negative_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence"],
                "additionalProperties": False,
            },
            model="openai/gpt-oss-20b",
        )

        self.assertIsNone(payload)
        self.assertEqual(error, "groq_unsupported_response_format")


if __name__ == "__main__":
    unittest.main()
