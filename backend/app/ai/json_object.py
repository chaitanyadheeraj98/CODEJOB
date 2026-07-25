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
