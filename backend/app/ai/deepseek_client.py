from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings
from app.ai.json_object import JSONObjectParseError, parse_json_object


class DeepSeekJSONError(RuntimeError):
    def __init__(self, message: str, *, raw_content: str = "", finish_reason: str = "") -> None:
        super().__init__(message)
        self.raw_content = raw_content
        self.finish_reason = finish_reason


@dataclass(frozen=True)
class DeepSeekJSONResult:
    payload: dict[str, object]
    model: str
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int
    response_hash: str


def _build_client(*, timeout_seconds: float | None = None) -> OpenAI:
    return OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=timeout_seconds if timeout_seconds is not None else settings.deepseek_timeout_seconds,
    )


def _parse_json_object(content: str) -> dict[str, object]:
    try:
        return parse_json_object(content)
    except JSONObjectParseError as exc:
        detail = "empty" if exc.kind == "empty" else "malformed JSON"
        raise RuntimeError(f"DeepSeek returned {detail} content") from exc


def deepseek_chat_completion(system_prompt: str, user_prompt: str, *, model_name: str | None = None) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is missing")

    client = _build_client()
    response = client.chat.completions.create(
        model=model_name or settings.deepseek_model_fast or "deepseek-v4-flash",
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
    return deepseek_json_completion_with_diagnostics(
        system_prompt,
        user_prompt,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
    ).payload


def deepseek_json_completion_with_diagnostics(
    system_prompt: str,
    user_prompt: str,
    *,
    model_name: str | None = None,
    timeout_seconds: float | None = None,
) -> DeepSeekJSONResult:
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is missing")

    client = _build_client(timeout_seconds=timeout_seconds)
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=model_name or settings.deepseek_model_fast or "deepseek-v4-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=900,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content if response.choices else ""
    finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "") if response.choices else ""
    raw_content = content or ""
    try:
        payload = _parse_json_object(raw_content)
    except RuntimeError as exc:
        category = "truncated JSON content" if finish_reason == "length" else str(exc)
        raise DeepSeekJSONError(category, raw_content=raw_content, finish_reason=finish_reason) from exc
    usage = getattr(response, "usage", None)
    return DeepSeekJSONResult(
        payload=payload,
        model=str(getattr(response, "model", "") or model_name or settings.deepseek_model_fast or "deepseek-v4-flash"),
        finish_reason=finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        duration_ms=int((time.perf_counter() - started) * 1000),
        response_hash=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
    )
