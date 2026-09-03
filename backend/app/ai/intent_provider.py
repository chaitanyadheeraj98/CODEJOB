"""Provider seam for the job-intent gate.

The gate used to call `groq_chat_json` directly. It now calls `intent_chat_json`,
which dispatches on `settings.intent_gate_provider` and adds three things the direct
call could not have:

1. **A rollback lever.** This gate decides what enters the queue at all, so switching
   providers has to be a setting, not a deploy.
2. **A bounded budget per email.** The old path was `groq_gate_timeout_seconds: 20` x
   `groq_gate_max_retries: 2`. Here `intent_gate_timeout_seconds` covers the *whole*
   ladder, so adding a rung cannot silently double the worst case.
3. **Client-side schema validation.** DeepSeek supports
   `response_format: {"type": "json_object"}` only - no `json_schema`, no `strict`.
   Moving off Groq's server-side enforcement without this would degrade output
   quality silently, so every response is validated against the same schema the
   gate would have handed the API.
4. **Failures told apart.** DeepSeek's documented statuses want four different
   responses, but the SDK collapses several onto one exception type. They are
   separated here so a spent balance or a dead key stops the ladder with its own
   code instead of retrying every rung and recording an outage.

The return shape `(payload, error, usage)` deliberately matches `groq_chat_json`'s
first two elements so the gate's branch structure is unchanged; `usage` is additive
and may be ignored except for logging.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Literal

from openai import APIError, APITimeoutError, BadRequestError, RateLimitError

from app.ai.deepseek_client import (
    DeepSeekJSONError,
    deepseek_json_completion_with_diagnostics,
)
from app.ai.groq_client import groq_chat_json
from app.ai.json_object import validate_schema_subset
from app.config import settings

logger = logging.getLogger(__name__)

# Rung tokens that mean "do not reason". Everything else is a reasoning-effort level
# and maps to thinking enabled; the raw token is kept as the telemetry label so a
# future "disabled,high" ladder reports which rung actually answered.
_NO_THINKING_RUNGS = frozenset({"disabled", "off", "none", "no", "false", "0"})


@dataclass(frozen=True)
class IntentUsage:
    provider: str
    rung: str = ""
    attempts: int = 0
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    duration_ms: int = 0
    escalated: bool = False


def effort_ladder() -> tuple[str, ...]:
    """The configured rungs, in order. Its length is the hard attempt cap."""
    rungs = tuple(item.strip().lower() for item in (settings.intent_gate_effort_ladder or "").split(",") if item.strip())
    return rungs or ("disabled",)


def _thinking_for_rung(rung: str) -> Literal["enabled", "disabled"]:
    return "disabled" if rung in _NO_THINKING_RUNGS else "enabled"


def _schema_example(schema: dict[str, object]) -> object:
    """A minimal instance of `schema`, for the prompt.

    DeepSeek's json_object mode documents two requirements: the prompt must contain
    the word "json", and it should show the shape being asked for. This builds that
    shape from the schema itself so the example can never drift from what is
    validated.
    """
    schema_type = schema.get("type")
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required = schema.get("required")
        keys = [key for key in required if isinstance(key, str)] if isinstance(required, list) else list(properties)
        return {
            key: _schema_example(properties[key])
            for key in keys
            if isinstance(properties.get(key), dict)
        }
    if schema_type == "array":
        item_schema = schema.get("items")
        return [_schema_example(item_schema)] if isinstance(item_schema, dict) else []
    if schema_type in ("number", "integer"):
        return 0
    if schema_type == "boolean":
        return False
    return ""


@lru_cache(maxsize=8)
def _json_instruction(schema_json: str) -> str:
    """Built once per schema, never per email - see the cache-prefix note below.

    DeepSeek's prompt cache keys on the literal prefix. The gate's system prompt is a
    static module-level string with no per-email interpolation; this appendix has to
    be constant for the same reason, or every email pays full price on the segment.
    """
    schema = json.loads(schema_json)
    example = json.dumps(_schema_example(schema), ensure_ascii=True)
    return (
        "Respond with exactly one json object, with no markdown fences, no prose, and no extra keys. "
        "The json must satisfy this schema:\n"
        f"{schema_json}\n"
        "Example of the required json shape (values are placeholders):\n"
        f"{example}"
    )


def _cached_system_prompt(system_prompt: str, schema: dict[str, object]) -> str:
    return f"{system_prompt}\n{_json_instruction(json.dumps(schema, ensure_ascii=True, sort_keys=True))}"


# DeepSeek documents seven failure statuses that want four different responses, and
# the openai SDK collapses four of them onto a bare APIStatusError. Mapping by status
# keeps them apart: without this, a 401 and a 402 both recorded `deepseek_unavailable`
# and read exactly like a transient 503 in the persisted `gate_error`.
_STATUS_ERROR_CODES: dict[int, str] = {
    400: "deepseek_bad_request",
    401: "deepseek_auth_failed",
    402: "deepseek_insufficient_balance",
    422: "deepseek_invalid_parameters",
    429: "deepseek_rate_limited",
}

# Failures no further rung can fix. 401 and 402 need a person - a key rotation, a
# top-up - and 400/422 mean the request body itself was rejected, which the next
# rung would send again unchanged (rungs vary only the thinking flag). Retrying any
# of these spends the whole ladder and the timeout budget to reach the same fallback,
# and the run that follows looks like a provider outage rather than a billing page.
_TERMINAL_ERRORS = frozenset(
    {
        "deepseek_auth_failed",
        "deepseek_insufficient_balance",
        "deepseek_bad_request",
        "deepseek_invalid_parameters",
    }
)

# A 429 is a concurrency rejection, so the documented handling is backoff rather than
# an immediate re-send. Doubled per attempt, and always clamped to leave budget for
# the rung it precedes - a backoff that consumed the budget would convert a rate
# limit into a timeout and lose the real cause.
_RATE_LIMIT_BACKOFF_SECONDS = 0.5


def _deepseek_error_code(exc: Exception) -> str:
    if isinstance(exc, APITimeoutError):
        return "deepseek_timeout"
    if isinstance(exc, DeepSeekJSONError):
        return "deepseek_empty_content" if not exc.raw_content.strip() else "deepseek_invalid_json"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _STATUS_ERROR_CODES:
        return _STATUS_ERROR_CODES[status]
    # Fallbacks for a transport that raised the typed error without a status.
    if isinstance(exc, RateLimitError):
        return "deepseek_rate_limited"
    if isinstance(exc, BadRequestError):
        return "deepseek_bad_request"
    if isinstance(exc, APIError):
        return "deepseek_unavailable"
    return f"deepseek_error:{exc.__class__.__name__}"


def intent_chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, object],
    model: str | None = None,
    max_tokens: int = 500,
    agrees_with_taxonomy: Callable[[dict[str, object]], bool] | None = None,
) -> tuple[dict[str, object] | None, str | None, IntentUsage | None]:
    """Answer the intent gate with the configured provider.

    `agrees_with_taxonomy` is how the caller opts into disagreement-driven escalation
    without this module having to know what a taxonomy verdict is: it is handed the
    parsed payload and answers whether the rules already agree. Omit it (or leave
    `intent_gate_escalate_on_disagreement` off) and only hard failures escalate.
    """
    provider = settings.intent_gate_provider
    if provider == "taxonomy":
        # No network at all: the rules taxonomy is the whole answer.
        return None, "provider_disabled", None
    if provider == "groq":
        payload, error = groq_chat_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            model=model or settings.groq_gate_model,
            strict=bool(settings.groq_gate_strict_json),
            max_tokens=max_tokens,
        )
        return payload, error, IntentUsage(provider="groq", attempts=1)

    return _deepseek_ladder(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=schema,
        model=model,
        max_tokens=max_tokens,
        agrees_with_taxonomy=agrees_with_taxonomy,
    )


def _deepseek_ladder(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, object],
    model: str | None,
    max_tokens: int,
    agrees_with_taxonomy: Callable[[dict[str, object]], bool] | None,
) -> tuple[dict[str, object] | None, str | None, IntentUsage | None]:
    if not settings.deepseek_api_key:
        return None, "missing_deepseek_api_key", None

    selected_model = model or settings.intent_gate_model or settings.deepseek_model_fast or "deepseek-v4-flash"
    cached_system_prompt = _cached_system_prompt(system_prompt, schema)
    rungs = effort_ladder()
    escalate_on_disagreement = bool(settings.intent_gate_escalate_on_disagreement)

    started = time.perf_counter()
    # One budget for the whole ladder, not one per rung: a second rung must cost
    # latency inside the same worst case, never a second worst case.
    budget = max(0.1, float(settings.intent_gate_timeout_seconds))
    last_error = "deepseek_unknown_error"
    last_usage: IntentUsage | None = None
    attempts = 0

    for index, rung in enumerate(rungs):
        remaining = budget - (time.perf_counter() - started)
        if remaining <= 0:
            last_error = "deepseek_timeout_budget_exhausted" if attempts else "deepseek_timeout"
            break
        attempts += 1
        try:
            result = deepseek_json_completion_with_diagnostics(
                cached_system_prompt,
                user_prompt,
                model_name=selected_model,
                timeout_seconds=remaining,
                max_tokens=max_tokens,
                thinking=_thinking_for_rung(rung),
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a stable code below
            last_error = _deepseek_error_code(exc)
            # Never let an exception body reach runtime_state or the persisted
            # gate_error; the codes above are the whole contract.
            logger.warning(
                "intent_gate_rung_failed provider=deepseek rung=%s attempt=%s error=%s",
                rung,
                attempts,
                last_error,
            )
            last_usage = IntentUsage(
                provider="deepseek",
                rung=rung,
                attempts=attempts,
                model=selected_model,
                duration_ms=int((time.perf_counter() - started) * 1000),
                escalated=index > 0,
            )
            if last_error in _TERMINAL_ERRORS:
                # Logged at error level because the remedy is outside this process:
                # nothing the ladder can do will change the answer.
                logger.error(
                    "intent_gate_terminal provider=deepseek rung=%s error=%s remaining_rungs=%s",
                    rung,
                    last_error,
                    len(rungs) - index - 1,
                )
                break
            if last_error == "deepseek_rate_limited":
                remaining_after = budget - (time.perf_counter() - started)
                # Half the remaining budget at most, so a rung always survives the wait.
                delay = min(
                    _RATE_LIMIT_BACKOFF_SECONDS * (2 ** (attempts - 1)),
                    max(0.0, remaining_after) / 2,
                )
                if delay > 0:
                    time.sleep(delay)
            continue

        usage = IntentUsage(
            provider="deepseek",
            rung=rung,
            attempts=attempts,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            prompt_cache_hit_tokens=result.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=result.prompt_cache_miss_tokens,
            duration_ms=int((time.perf_counter() - started) * 1000),
            escalated=index > 0,
        )
        last_usage = usage
        if not validate_schema_subset(result.payload, schema):
            last_error = "deepseek_invalid_shape"
            logger.warning(
                "intent_gate_rung_failed provider=deepseek rung=%s attempt=%s error=%s",
                rung,
                attempts,
                last_error,
            )
            continue

        if escalate_on_disagreement and agrees_with_taxonomy is not None and index + 1 < len(rungs):
            if not agrees_with_taxonomy(result.payload):
                last_error = "deepseek_disagreed_with_taxonomy"
                continue

        return result.payload, None, usage

    return None, last_error, last_usage
