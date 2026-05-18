from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import logging
from time import perf_counter

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)
_latency_capture_ms: ContextVar[list[float] | None] = ContextVar("semantic_latency_capture_ms", default=None)


def embedding_to_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"))


def embedding_from_json(payload: str | None) -> list[float]:
    if not payload:
        return []
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    values: list[float] = []
    for item in raw:
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            return []
    return values


def _hash_embedding(text: str, dims: int) -> list[float]:
    if dims <= 0:
        return []
    vector = [0.0] * dims
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return vector
    for token in normalized.split(" "):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        magnitude = 0.5 + (digest[5] / 255.0)
        vector[index] += sign * magnitude
    return vector


def begin_embedding_latency_capture() -> None:
    _latency_capture_ms.set([])


def end_embedding_latency_capture() -> list[float]:
    samples = _latency_capture_ms.get()
    _latency_capture_ms.set(None)
    if not samples:
        return []
    return list(samples)


def generate_embedding(text: str) -> tuple[list[float], str]:
    provider = (settings.semantic_embedding_provider or "hash").strip().lower()
    dims = max(32, int(settings.semantic_embedding_dimension or 256))
    model_name = settings.semantic_embedding_model or "text-embedding-3-small"
    started_at = perf_counter()
    result_provider = provider

    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is missing for semantic embedding provider=openai")
        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=float(settings.semantic_embedding_timeout_seconds or 20.0),
        )
        response = client.embeddings.create(model=model_name, input=text or "")
        if not response.data:
            raise RuntimeError("OpenAI embedding response is empty")
        vector = list(response.data[0].embedding)
        result_provider = "openai"
    elif provider == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is missing for semantic embedding provider=openrouter")
        client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=(settings.openrouter_base_url or "https://openrouter.ai/api/v1").rstrip("/"),
            timeout=float(settings.semantic_embedding_timeout_seconds or 20.0),
        )
        response = client.embeddings.create(model=model_name, input=text or "")
        if not response.data:
            raise RuntimeError("OpenRouter embedding response is empty")
        vector = list(response.data[0].embedding)
        result_provider = "openrouter"
    else:
        vector = _hash_embedding(text, dims)
        result_provider = "hash"

    elapsed_ms = (perf_counter() - started_at) * 1000.0
    if settings.semantic_embedding_latency_log_enabled:
        logger.warning(
            "Embedding latency provider=%s model=%s chars=%s dims=%s latency_ms=%.2f",
            result_provider,
            model_name if result_provider != "hash" else f"hash:{dims}",
            len(text or ""),
            len(vector),
            elapsed_ms,
        )
        samples = _latency_capture_ms.get()
        if isinstance(samples, list):
            samples.append(elapsed_ms)

    return vector, result_provider
