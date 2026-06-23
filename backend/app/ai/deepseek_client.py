from __future__ import annotations

import json
import re

from openai import OpenAI

from app.config import settings

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _build_client(*, timeout_seconds: float | None = None) -> OpenAI:
    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=timeout_seconds if timeout_seconds is not None else settings.deepseek_timeout_seconds,
    )


def _parse_json_object(content: str) -> dict[str, object]:
    text = (content or "").strip()
    if not text:
        raise RuntimeError("DeepSeek returned empty content")
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

    raise RuntimeError("DeepSeek returned malformed JSON content")


def deepseek_chat_completion(system_prompt: str, user_prompt: str) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is missing")

    client = _build_client()
    response = client.chat.completions.create(
        model=settings.deepseek_model_fast or "deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.35,
        max_tokens=550,
    )
    content = response.choices[0].message.content if response.choices else ""
    if not content:
        raise RuntimeError("DeepSeek returned empty content")
    return content.strip()


def deepseek_json_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    model_name: str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, object]:
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is missing")

    client = _build_client(timeout_seconds=timeout_seconds)
    response = client.chat.completions.create(
        model=model_name or settings.deepseek_model_fast or "deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=900,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content if response.choices else ""
    return _parse_json_object(content or "")
