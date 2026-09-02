from __future__ import annotations

import json
import re


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


class JSONObjectParseError(ValueError):
    def __init__(self, kind: str, content: str) -> None:
        super().__init__(kind)
        self.kind = kind
        self.content = content


def parse_json_object(content: str) -> dict[str, object]:
    text = (content or "").strip()
    if not text:
        raise JSONObjectParseError("empty", text)

    candidates = [text]
    fenced = JSON_FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    match = JSON_OBJECT_RE.search(text)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise JSONObjectParseError("malformed", text)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_schema_subset(value: object, schema: dict[str, object]) -> bool:
    """Client-side validation for providers with no server-side `json_schema` mode.

    Moved here verbatim from `groq_client` when the intent gate gained a second
    provider: DeepSeek supports `response_format: {"type": "json_object"}` only, so
    this is the *only* thing standing between a well-formed-but-wrong-shape response
    and a bad gate verdict. One validator, two callers - do not fork it.
    """
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
            if isinstance(item_schema, dict) and not validate_schema_subset(value[key], item_schema):
                return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return all(validate_schema_subset(item, item_schema) for item in value)
        return True
    if schema_type == "string":
        if not isinstance(value, str):
            return False
    elif schema_type in ("number", "integer"):
        if not _is_number(value):
            return False
    elif schema_type is not None:
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    return True
