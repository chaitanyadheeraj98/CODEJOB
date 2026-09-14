"""Exactly one process does the background work, whatever the worker count.

`StartupService.startup()` runs in every process. It starts the auto-runner
thread, the Telegram poller and a Gmail label sync - so `uvicorn --workers 4`
would run four of each.

That is not a performance problem, it is a correctness one:

- The auto-runner drives Gmail sync and **draft generation and sending**. Four
  copies means four times the Gmail API calls against a per-minute quota that
  already returns 429s for a single account, and duplicate outward-facing
  actions against the same rows. Sending the same email twice is not
  recoverable by a retry.
- Two Telegram pollers on one bot token is a **409 Conflict** from Telegram,
  and updates are split unpredictably between them.

Leadership is claimed per tick rather than once at start-up. A process that
loses the race simply does nothing that tick and picks the work up
automatically when the holder stops renewing - no restart, no operator.

Failing closed, deliberately
----------------------------
With Redis unreachable, nobody is elected and the background work pauses. The
alternative - every process assuming it is the leader - produces duplicate
sends, and a paused auto-runner is a far better failure than an email a
recruiter receives twice. Redis is a hard dependency of this work anyway: the
auto-runner's main job is to enqueue, and the queue is Redis.
"""

from __future__ import annotations

import logging
import os
import uuid

from redis.exceptions import RedisError

from app.jobs.queues import get_redis_connection

logger = logging.getLogger(__name__)

KEY_PREFIX = "leader"

# Roles that must have exactly one holder.
AUTO_RUNNER = "auto_runner"
TELEGRAM_POLLER = "telegram_poller"

# Long enough that a slow tick does not drop the lease, short enough that a
# killed process is replaced promptly. The auto-runner renews every 5s.
DEFAULT_TTL_SECONDS = 30

# Identifies this process for the life of the process. The pid is included so a
# log line can be traced to a container; the uuid is what makes it unique,
# since pids repeat across containers.
INSTANCE_ID = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"

# Claim the lease if it is free, renew it if it is already ours, and report
# failure if someone else holds it - as one atomic step.
#
# GET-then-SET from Python would let two processes both see a free key and both
# claim it, which is the exact outcome this exists to prevent. `SET NX` alone
# cannot renew, and `SET` alone would steal the lease from a live holder.
_CLAIM_LUA = """
local key = KEYS[1]
local me = ARGV[1]
local ttl = tonumber(ARGV[2])
local holder = redis.call('GET', key)
if holder == false then
  redis.call('SET', key, me, 'EX', ttl)
  return 1
end
if holder == me then
  redis.call('EXPIRE', key, ttl)
  return 1
end
return 0
"""

# Only the holder may release. Releasing unconditionally would let a process
# whose lease had already expired and been taken delete the new holder's claim.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _key(role: str) -> str:
    return f"{KEY_PREFIX}:{role}"


def is_leader(
    role: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    instance_id: str | None = None,
    connection=None,
) -> bool:
    """True when this process holds `role` now, claiming or renewing it.

    Call it every tick. It is one round trip and it is what makes failover
    automatic.

    `instance_id` defaults to this process's. It is a parameter rather than a
    hidden global read so that "which process is asking" can be stated
    explicitly - a test simulating four workers in four threads cannot patch a
    module global safely, because the threads would overwrite each other's
    value.
    """
    try:
        redis = connection or get_redis_connection()
        return bool(redis.eval(_CLAIM_LUA, 1, _key(role), instance_id or INSTANCE_ID, ttl_seconds))
    except RedisError as exc:
        # Fail closed: see the module docstring. Logged as a type rather than a
        # message, because a connection error carries the URL.
        logger.warning("leader_election_unavailable role=%s error=%s", role, type(exc).__name__)
        return False


def release(role: str, *, instance_id: str | None = None, connection=None) -> None:
    """Give up the lease on a clean shutdown, so failover is immediate.

    Without this the next process waits out the TTL before taking over, which
    turns a rolling restart into a gap in automation.
    """
    try:
        redis = connection or get_redis_connection()
        redis.eval(_RELEASE_LUA, 1, _key(role), instance_id or INSTANCE_ID)
    except RedisError as exc:
        logger.warning("leader_release_failed role=%s error=%s", role, type(exc).__name__)


def current_holder(role: str, *, connection=None) -> str | None:
    """Who holds `role`, for diagnostics. Never used to make a decision."""
    try:
        redis = connection or get_redis_connection()
        value = (connection or redis).get(_key(role))
        return value.decode() if isinstance(value, bytes) else value
    except RedisError:
        return None
