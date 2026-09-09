import json

import httpx
from langgraph.errors import GraphRecursionError


FALLBACK_MESSAGE = "Chat is temporarily unavailable. Check that Ollama is running, then try again."
FAILURE_MESSAGES = {
    "ollama_unavailable": FALLBACK_MESSAGE,
    "ollama_timeout": "The model did not respond in time. Try again or choose another model.",
    "ollama_rate_limited": "The model provider is rate-limiting us. Try again in a moment.",
    "ollama_model_not_found": "That model is not available. Choose an installed model.",
    "ollama_auth_error": "The model provider rejected authentication. Check its credentials.",
    "ollama_bad_request": "The model provider rejected this request. Check the model's tool support.",
    "ollama_payment_required": "The model provider has no remaining credit. Check its billing.",
    "mcp_unavailable": "The assistant's tools are not reachable, so it cannot look anything up.",
    "tool_budget_exhausted": "That needed more steps than one turn allows. Ask for one part at a time.",
    "budget_exhausted": "That took longer than a turn is allowed. Try a narrower question.",
}
_TERMINAL = frozenset({"ollama_auth_error", "ollama_payment_required", "ollama_bad_request",
                       "mcp_unavailable", "tool_budget_exhausted", "budget_exhausted"})
_RETRYABLE = frozenset({"ollama_timeout", "ollama_rate_limited", "ollama_unavailable"})


def classify_ollama_error(exc: Exception) -> str:
    if isinstance(exc, GraphRecursionError):
        return "tool_budget_exhausted"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "ollama_timeout"
    if isinstance(exc, (ConnectionError, httpx.TransportError)):
        return "ollama_unavailable"
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    codes = {400: "ollama_bad_request", 401: "ollama_auth_error", 402: "ollama_payment_required",
             403: "ollama_auth_error", 404: "ollama_model_not_found", 429: "ollama_rate_limited"}
    if status in codes:
        return codes[status]
    if isinstance(status, int) and status >= 500:
        return "ollama_unavailable"
    return f"ollama_error:{type(exc).__name__}"


def tool_error(exc: Exception) -> str:
    return json.dumps({"status": "refused", "reason": type(exc).__name__,
                       "hint": "The tool could not complete. Check its required arguments before trying again."})
