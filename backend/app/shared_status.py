"""The status fields every process must agree on.

`runtime_state` is a module-level object, so each process has its own. With one
API process that is invisible. With `uvicorn --workers 4`, `/gmail/status`,
`/ai/status` and `/gmail/live-replies` each answer from whichever worker took
the request, and the Settings cards flicker between four versions of the truth.

Leader election made it sharper rather than better: the auto-runner now runs in
exactly one process, so **only that process ever has live-reply data** and the
other three would report zero.

What is shared and what is not
------------------------------
Only telemetry - timestamps, counters, last-error strings, the live-reply
counts. Threads, locks, stop events and service objects stay process-local
because they *are* the process: a `threading.Event` in Redis would be a
description of an event, not one.

Reads are snapshotted
---------------------
A status card reads a dozen fields, and the dashboard polls several cards every
few seconds. One round trip per attribute would turn one page into hundreds of
Redis calls a second for no benefit, so a read fetches the whole hash at once
and reuses it briefly. A write goes straight through *and* updates the local
snapshot, so a process always sees its own writes immediately - the staleness
window only ever applies to another process's.

Fail open, unlike the locks
---------------------------
If Redis is unreachable this falls back to a process-local dictionary: exactly
today's behaviour. A status card is a readout, not a guard - showing slightly
stale numbers is fine, and refusing to render the Settings page because Redis
blinked is not.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from redis.exceptions import RedisError

from app.jobs.queues import get_redis_connection

logger = logging.getLogger(__name__)

KEY = "runtime:status"

# How long a fetched snapshot may be reused. Status is polled every few
# seconds; a second of staleness is imperceptible and collapses a card's dozen
# field reads into one round trip.
SNAPSHOT_TTL_SECONDS = 1.0

# Telemetry only. Anything not named here stays an ordinary attribute on
# `runtime_state` and therefore stays process-local - which is correct for
# threads, locks, events and service handles.
SHARED_FIELDS = frozenset({
    "last_gmail_sync_at",
    "ai_running",
    "ai_last_error",
    "ai_last_started_at",
    "ai_last_finished_at",
    "ai_last_duration_ms",
    "ai_last_draft_source",
    "embedding_last_error",
    "embedding_last_attempted_at",
    "embedding_last_success_at",
    "embedding_last_duration_ms",
    "groq_last_error",
    "groq_last_attempted_at",
    "groq_last_success_at",
    "groq_last_duration_ms",
    "groq_last_provider_result",
    "groq_request_mode",
    "intent_gate_provider",
    "intent_gate_last_error",
    "intent_gate_last_attempted_at",
    "intent_gate_last_success_at",
    "intent_gate_last_duration_ms",
    "intent_gate_last_provider_result",
    "intent_gate_last_rung",
    "intent_gate_last_escalated",
    "ollama_last_error",
    "ollama_last_attempted_at",
    "ollama_last_success_at",
    "ollama_last_duration_ms",
    "chat_last_error",
    "chat_last_failure_code",
    "chat_last_attempted_at",
    "chat_last_success_at",
    "chat_last_duration_ms",
    "chat_mcp_status",
    "chat_active_model",
    "live_replies",
})


def _encode(value: Any) -> str:
    """JSON, with datetimes tagged so they survive the round trip.

    Tagged rather than guessed at on the way back: a bare ISO string is
    indistinguishable from a status message that happens to look like a date,
    and `chat_active_model` is a free-form string.
    """
    if isinstance(value, datetime):
        return json.dumps({"__dt__": value.isoformat()})
    if isinstance(value, dict):
        # live_replies: {owner: (count, checked_at)}
        return json.dumps({
            "__map__": {
                str(k): [v[0], v[1].isoformat() if isinstance(v[1], datetime) else v[1]]
                for k, v in value.items()
            }
        })
    return json.dumps(value)


def _decode(raw: str | bytes) -> Any:
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__map__" in value:
            return {
                key: (pair[0], datetime.fromisoformat(pair[1]) if pair[1] else None)
                for key, pair in value["__map__"].items()
            }
    return value


class SharedStatus:
    """A Redis-backed hash behind an ordinary attribute API."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {}
        self._fetched_at = 0.0
        # Used when Redis is unreachable, and as the store under tests that
        # have no Redis. Never authoritative while Redis answers.
        self._fallback: dict[str, Any] = {}
        self._degraded = False

    def _refresh(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if now - self._fetched_at < SNAPSHOT_TTL_SECONDS:
                return self._snapshot
        try:
            raw = get_redis_connection().hgetall(KEY)
        except RedisError as exc:
            if not self._degraded:
                # Once per outage, not once per read: this is on a polled path.
                logger.warning("shared_status_unavailable error=%s", type(exc).__name__)
                self._degraded = True
            return self._fallback
        self._degraded = False
        snapshot = {
            (k.decode() if isinstance(k, bytes) else k): _decode(v) for k, v in raw.items()
        }
        with self._lock:
            self._snapshot = snapshot
            self._fetched_at = time.monotonic()
        return snapshot

    def get(self, name: str, default: Any = None) -> Any:
        snapshot = self._refresh()
        if name in snapshot:
            return snapshot[name]
        return self._fallback.get(name, default)

    def set(self, name: str, value: Any) -> None:
        # The local copy is updated first and unconditionally, so a process
        # always observes its own write even if Redis is unreachable or the
        # snapshot is a moment from expiring.
        with self._lock:
            self._snapshot[name] = value
        self._fallback[name] = value
        try:
            get_redis_connection().hset(KEY, name, _encode(value))
        except RedisError as exc:
            if not self._degraded:
                logger.warning("shared_status_write_failed error=%s", type(exc).__name__)
                self._degraded = True

    def clear(self) -> None:
        """Drop everything. For tests; never call this in a request."""
        with self._lock:
            self._snapshot = {}
            self._fetched_at = 0.0
        self._fallback = {}
        try:
            get_redis_connection().delete(KEY)
        except RedisError:
            pass

    def invalidate(self) -> None:
        """Force the next read to fetch. For tests that write from elsewhere."""
        with self._lock:
            self._fetched_at = 0.0


shared_status = SharedStatus()
