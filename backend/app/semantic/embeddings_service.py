from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import logging
import threading
from time import perf_counter

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
    sbert_model = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
    started_at = perf_counter()
    result_provider = provider

    if provider == "sbert":
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
            sbert_model if result_provider == "sbert" else f"hash:{dims}",
            len(text or ""),
            len(vector),
            elapsed_ms,
        )
        samples = _latency_capture_ms.get()
        if isinstance(samples, list):
            samples.append(elapsed_ms)

    return vector, result_provider
