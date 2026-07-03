from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import APIError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

from app.config import settings

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
STRUCTURED_OUTPUT_MODELS = frozenset(
    {
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    }
)


def _build_client() -> OpenAI:
    return OpenAI(
        api_key=settings.groq_api_key,
        base_url=settings.groq_base_url,
        timeout=settings.groq_gate_timeout_seconds,
    )


def groq_request_mode_for_model(model: str | None) -> str:
    normalized = (model or "").strip().lower()
    return "json_schema" if normalized in STRUCTURED_OUTPUT_MODELS else "json_object"


def _parse_json_object(content: str) -> dict[str, object]:
    text = (content or "").strip()
    if not text:
        raise ValueError("Groq returned empty content")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = JSON_FENCE_RE.search(text)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    match = JSON_OBJECT_RE.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("Groq returned malformed JSON content", text, 0)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_schema_subset(value: object, schema: dict[str, object]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    return False
        if schema.get("additionalProperties") is False:
            allowed_keys = {key for key in properties if isinstance(key, str)}
            if any(key not in allowed_keys for key in value):
                return False
        for key, item_schema in properties.items():
            if key not in value:
                continue
            if isinstance(item_schema, dict) and not _validate_schema_subset(value[key], item_schema):
                return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return all(_validate_schema_subset(item, item_schema) for item in value)
        return True
    if schema_type == "string":
        if not isinstance(value, str):
            return False
    elif schema_type == "number":
        if not _is_number(value):
            return False
    elif schema_type is not None:
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    return True


def _classify_bad_request(exc: BadRequestError, *, request_mode: str) -> str:
    message = str(exc)
    if request_mode == "json_schema" and "does not support response format `json_schema`" in message:
        return "groq_unsupported_response_format"
    return f"groq_api_error:{exc.__class__.__name__}"


def groq_chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, object],
    model: str | None = None,
    strict: bool = True,
) -> tuple[dict[str, object] | None, str | None]:
    if not settings.groq_api_key:
        return None, "missing_groq_api_key"

    client = _build_client()
    attempts = max(1, int(settings.groq_gate_max_retries or 1))
    last_error = "groq_unknown_error"

    for attempt in range(attempts):
        try:
            selected_model = model or settings.groq_gate_model or "llama-3.1-8b-instant"
            request_mode = groq_request_mode_for_model(selected_model)
            request_kwargs: dict[str, Any] = {
                "model": selected_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            system_prompt
                            if request_mode == "json_schema"
                            else (
                                f"{system_prompt}\n"
                                "Return exactly one JSON object with no markdown fences, no prose, and no extra keys. "
                                "The JSON must satisfy the provided schema."
                            )
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            user_prompt
                            if request_mode == "json_schema"
                            else f"{user_prompt}\nRequired JSON schema:\n{json.dumps(schema, ensure_ascii=True)}"
                        ),
                    },
                ],
                "temperature": 0.0,
                "max_tokens": 500,
            }
            if request_mode == "json_schema":
                request_kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gmail_intent_gate",
                        "schema": schema,
                        "strict": strict,
                    },
                }
            else:
                request_kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(
                **request_kwargs,
            )
            content = response.choices[0].message.content if response.choices else ""
            if not content:
                return None, "groq_empty_content"
            payload = _parse_json_object(content)
            if not _validate_schema_subset(payload, schema):
                return None, "groq_invalid_shape"
            return payload, None
        except BadRequestError as exc:
            last_error = _classify_bad_request(exc, request_mode=request_mode)
        except RateLimitError:
            last_error = "groq_rate_limited"
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 4))
                continue
        except APITimeoutError:
            last_error = "groq_timeout"
        except json.JSONDecodeError:
            last_error = "groq_invalid_json"
        except APIError as exc:
            last_error = f"groq_api_error:{exc.__class__.__name__}"
        except Exception as exc:
            last_error = f"groq_error:{exc.__class__.__name__}"
        break

    return None, last_error
