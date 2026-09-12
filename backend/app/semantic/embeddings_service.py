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


class EmbeddingNotAvailableHere(RuntimeError):
    """This process is not allowed to compute embeddings.

    Raised rather than quietly falling back to hash, because hash and SBERT
    give different answers and a scoring path that silently changed provider
    would be a correctness change wearing a memory optimisation's clothes.
    """


def _embeddings_allowed_here() -> None:
    """C4: keep torch out of the request-serving processes.

    `langchain_core` imports `transformers` for a GPT-2 tokenizer whenever it
    is installed, and that one optional import pulls in ~483 MB of torch at
    application start - before any embedding runs. Declining it takes
    `import app.main` from 746 MB to 204 MB, which is the difference between
    `--workers 4` costing 3 GB and costing 0.8 GB.

    Declining it is only safe if nothing in this process embeds, so that is
    enforced here rather than assumed.
    """
    if settings.process_role != "worker" and not settings.api_embeddings_enabled:
        raise EmbeddingNotAvailableHere(
            "Embeddings are disabled in this process. Enqueue the work to the "
            "worker, or set API_EMBEDDINGS_ENABLED=true."
        )


def _load_sbert_model(model_name: str, device: str):
    _embeddings_allowed_here()
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
        hf_token = (settings.hf_token or "").strip() or None
        model = SentenceTransformer(
            model_name,
            device=device,
            token=hf_token,
        )
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


def generate_embeddings(texts: list[str]) -> tuple[list[list[float]], str]:
    if not texts:
        return [], settings.effective_semantic_embedding_provider
    provider = settings.effective_semantic_embedding_provider
    dims = max(32, int(settings.semantic_embedding_dimension or 256))
    if provider != "sbert":
        return [_hash_embedding(text, dims) for text in texts], "hash"
    model_name = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
    device = (settings.semantic_embedding_sbert_device or "cpu").strip() or "cpu"
    try:
        model = _load_sbert_model(model_name, device)
        vectors = model.encode(texts, normalize_embeddings=True)
        return [[float(item) for item in vector.tolist()] for vector in vectors], "sbert"
    except Exception as exc:
        logger.warning(
            "embedding_batch_fallback primary_provider=sbert fallback_provider=hash failure_reason=%s items=%s",
            str(exc),
            len(texts),
        )
        return [_hash_embedding(text, dims) for text in texts], "hash"


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
