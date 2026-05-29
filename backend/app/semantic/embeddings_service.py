from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import logging
import threading
from time import perf_counter
from urllib import request
from urllib.error import HTTPError, URLError

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)
_latency_capture_ms: ContextVar[list[float] | None] = ContextVar("semantic_latency_capture_ms", default=None)
_sbert_model_lock = threading.Lock()
_sbert_model_instance = None
_sbert_model_name = ""
_sbert_model_device = ""


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


def _openrouter_embedding(text: str, model_name: str) -> tuple[list[float], str]:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is missing for semantic embedding provider=openrouter")
    client = OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=(settings.openrouter_base_url or "https://openrouter.ai/api/v1").rstrip("/"),
        timeout=float(settings.semantic_embedding_timeout_seconds or 20.0),
    )
    response = client.embeddings.create(model=model_name, input=text or "")
    if not response.data:
        raise ValueError("No embedding data received")
    vector = list(response.data[0].embedding)
    if not vector:
        raise ValueError("No embedding data received")
    return vector, "openrouter"


def _gemini_embedding(text: str, model_name: str) -> tuple[list[float], str]:
    api_key = (settings.google_embedding_api_key or "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_EMBEDDING_API_KEY (or GoogleEmbedding_API_KEY) is missing for provider=gemini")
    base_url = (settings.google_embedding_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    payload = {
        "model": f"models/{model_name}",
        "content": {"parts": [{"text": text or ""}]},
    }
    url = f"{base_url}/models/{model_name}:embedContent?key={api_key}"
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=float(settings.semantic_embedding_timeout_seconds or 20.0)) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Gemini HTTP {exc.code}: {error_body[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini connection error: {exc}") from exc

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini embedding response is not valid JSON") from exc
    embedding = parsed.get("embedding") if isinstance(parsed, dict) else None
    values = embedding.get("values") if isinstance(embedding, dict) else None
    if not isinstance(values, list) or not values:
        raise ValueError("No embedding data received")
    vector = [float(item) for item in values]
    return vector, "gemini"


def _load_sbert_model(model_name: str, device: str):
    global _sbert_model_instance, _sbert_model_name, _sbert_model_device
    if (
        _sbert_model_instance is not None
        and _sbert_model_name == model_name
        and _sbert_model_device == device
    ):
        return _sbert_model_instance
    with _sbert_model_lock:
        if (
            _sbert_model_instance is not None
            and _sbert_model_name == model_name
            and _sbert_model_device == device
        ):
            return _sbert_model_instance
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise RuntimeError("SBERT dependency missing: install sentence-transformers") from exc
        model = SentenceTransformer(model_name, device=device)
        _sbert_model_instance = model
        _sbert_model_name = model_name
        _sbert_model_device = device
        return _sbert_model_instance


def _sbert_embedding(text: str, model_name: str) -> tuple[list[float], str]:
    sbert_device = (settings.semantic_embedding_sbert_device or "cpu").strip() or "cpu"
    model = _load_sbert_model(model_name, sbert_device)
    vector = model.encode(text or "", normalize_embeddings=True)
    values = [float(item) for item in vector.tolist()]
    if not values:
        raise ValueError("No embedding data received")
    return values, "sbert"


def _log_fallback(primary_provider: str, fallback_provider: str, failure_reason: str, text: str, latency_ms: float) -> None:
    logger.warning(
        "embedding_fallback primary_provider=%s fallback_provider=%s failure_reason=%s chars=%s latency_ms=%.2f",
        primary_provider,
        fallback_provider,
        failure_reason,
        len(text or ""),
        latency_ms,
    )


def generate_embedding(text: str) -> tuple[list[float], str]:
    provider = settings.effective_semantic_embedding_provider
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
    elif provider == "gemini":
        gemini_model = settings.google_embedding_model or model_name or "gemini-embedding-2"
        fallback_provider = (settings.semantic_embedding_fallback_provider or "openrouter").strip().lower()
        fallback_model = settings.semantic_embedding_fallback_model or "openai/text-embedding-3-small"
        sbert_model = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
        try:
            vector, result_provider = _gemini_embedding(text or "", gemini_model)
        except Exception as gemini_exc:
            _log_fallback(
                "gemini",
                fallback_provider,
                str(gemini_exc),
                text or "",
                (perf_counter() - started_at) * 1000.0,
            )
            if fallback_provider == "openrouter":
                try:
                    vector, result_provider = _openrouter_embedding(text or "", fallback_model)
                except Exception as openrouter_exc:
                    _log_fallback(
                        "openrouter",
                        "sbert",
                        str(openrouter_exc),
                        text or "",
                        (perf_counter() - started_at) * 1000.0,
                    )
                    try:
                        vector, result_provider = _sbert_embedding(text or "", sbert_model)
                    except Exception as sbert_exc:
                        _log_fallback(
                            "sbert",
                            "hash",
                            str(sbert_exc),
                            text or "",
                            (perf_counter() - started_at) * 1000.0,
                        )
                        vector = _hash_embedding(text, dims)
                        result_provider = "hash"
            elif fallback_provider == "hash":
                vector = _hash_embedding(text, dims)
                result_provider = "hash"
            elif fallback_provider == "sbert":
                try:
                    vector, result_provider = _sbert_embedding(text or "", sbert_model)
                except Exception as sbert_exc:
                    _log_fallback(
                        "sbert",
                        "hash",
                        str(sbert_exc),
                        text or "",
                        (perf_counter() - started_at) * 1000.0,
                    )
                    vector = _hash_embedding(text, dims)
                    result_provider = "hash"
            else:
                logger.warning(
                    "embedding_fallback unsupported_fallback_provider=%s; using hash",
                    fallback_provider,
                )
                vector = _hash_embedding(text, dims)
                result_provider = "hash"
    elif provider == "openrouter":
        sbert_model = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
        try:
            vector, result_provider = _openrouter_embedding(text or "", model_name)
        except Exception as openrouter_exc:
            _log_fallback(
                "openrouter",
                "sbert",
                str(openrouter_exc),
                text or "",
                (perf_counter() - started_at) * 1000.0,
            )
            try:
                vector, result_provider = _sbert_embedding(text or "", sbert_model)
            except Exception as sbert_exc:
                _log_fallback(
                    "sbert",
                    "hash",
                    str(sbert_exc),
                    text or "",
                    (perf_counter() - started_at) * 1000.0,
                )
                vector = _hash_embedding(text, dims)
                result_provider = "hash"
    elif provider == "sbert":
        sbert_model = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
        try:
            vector, result_provider = _sbert_embedding(text or "", sbert_model)
        except Exception as sbert_exc:
            _log_fallback(
                "sbert",
                "hash",
                str(sbert_exc),
                text or "",
                (perf_counter() - started_at) * 1000.0,
            )
            vector = _hash_embedding(text, dims)
            result_provider = "hash"
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
