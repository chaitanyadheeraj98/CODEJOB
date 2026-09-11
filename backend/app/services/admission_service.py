"""Shared admission control, so a concurrency cap survives more than one process.

The caps this replaces were `threading.BoundedSemaphore` objects living in
module globals: `turns.slots` for chat turns and `_RENDER_SLOTS` for LibreOffice
rendering. Both are correct in exactly one situation - a single API process -
and silently become `N x cap` the moment a second one starts.

That is why **C1 gates C3**. Running `uvicorn --workers 4` before this exists
does not raise the limit from 3 to 3; it raises it to 12, quietly, with no
error and no log line.

Two caps rather than one
------------------------
A single global semaphore lets one user hold every slot and starve the other
ninety-nine. Admission here needs both a per-user cap and a global one, and the
per-user cap is checked first so a heavy user is told *they* are at their
limit, not that the service is busy.

Leases expire
-------------
A process-local semaphore is released by `finally`, and a killed process
releases everything by dying. A distributed one does not: a worker killed
mid-turn leaves its slot held forever. So every lease carries an expiry
slightly beyond the turn budget, and admission clears expired leases before it
counts. The cap is therefore self-healing: worst case, a crashed worker's slot
comes back a minute late.

Fail open
---------
If Redis is unreachable the assistant degrades rather than stops. The fallback
is a process-local semaphore sized to exactly today's cap, which is the
behaviour this module replaces - so losing Redis loses multi-process
coordination and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock

from redis import Redis
from redis.exceptions import RedisError

from app.jobs.queues import get_redis_connection

logger = logging.getLogger(__name__)

KEY_PREFIX = "admission"

# Pool names live here rather than at the call sites: they are Redis key
# components shared across processes, and a typo in one of two places would
# silently create a second, empty pool with no cap worth the name.
CHAT_TURN_POOL = "chat_turn"
RESUME_RENDER_POOL = "resume_render"

# Purge expired leases, check the per-user cap, then the global cap, then
# admit - as one atomic step.
#
# Done in Lua because the read and the write have to be indivisible. Checking
# ZCARD from Python and then ZADDing is a race that admits over the cap under
# exactly the load the cap exists for, and the window is wide enough to matter:
# two requests arriving in the same millisecond both see room and both take it.
#
# Returns {granted, scope}: scope names which cap refused, for the caller's
# message and for the log.
_ADMIT_LUA = """
local global_key = KEYS[1]
local user_key = KEYS[2]
local now = tonumber(ARGV[1])
local expires_at = tonumber(ARGV[2])
local token = ARGV[3]
local global_limit = tonumber(ARGV[4])
local user_limit = tonumber(ARGV[5])
local ttl = tonumber(ARGV[6])

redis.call('ZREMRANGEBYSCORE', global_key, '-inf', now)
redis.call('ZREMRANGEBYSCORE', user_key, '-inf', now)

if redis.call('ZCARD', user_key) >= user_limit then
  return {0, 'user'}
end
if redis.call('ZCARD', global_key) >= global_limit then
  return {0, 'global'}
end

redis.call('ZADD', global_key, expires_at, token)
redis.call('ZADD', user_key, expires_at, token)
redis.call('EXPIRE', global_key, ttl)
redis.call('EXPIRE', user_key, ttl)
return {1, ''}
"""


class AdmissionRejected(Exception):
    """No slot was available. `scope` is 'user', 'global' or 'local'."""

    def __init__(self, scope: str, message: str) -> None:
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True)
class Lease:
    pool: str
    token: str
    owner_id: str
    # Set when Redis was unreachable and a process-local semaphore granted this
    # instead. Carries the limit as well as the fact, because semaphores are
    # keyed by (pool, limit) and releasing the wrong one would corrupt an
    # unrelated cap.
    local_limit: int | None = None

    @property
    def local(self) -> bool:
        return self.local_limit is not None


_local_semaphores: dict[tuple[str, int], BoundedSemaphore] = {}
_local_lock = Lock()


def _local_semaphore(pool: str, limit: int) -> BoundedSemaphore:
    """One semaphore per pool, keyed by limit so a changed cap is not ignored."""
    key = (pool, limit)
    with _local_lock:
        existing = _local_semaphores.get(key)
        if existing is None:
            existing = BoundedSemaphore(limit)
            _local_semaphores[key] = existing
        return existing


def reset_local_semaphores() -> None:
    """Drop the fallback semaphores. For tests; never call this in a request."""
    with _local_lock:
        _local_semaphores.clear()


def _keys(pool: str, owner_id: str) -> tuple[str, str]:
    return f"{KEY_PREFIX}:{pool}:global", f"{KEY_PREFIX}:{pool}:user:{owner_id}"


def acquire(
    pool: str,
    *,
    owner_id: str,
    global_limit: int,
    per_user_limit: int,
    ttl_seconds: float,
    local_limit: int,
    connection: Redis | None = None,
) -> Lease:
    """Take a slot in `pool`, or raise `AdmissionRejected`.

    `local_limit` is the cap to fall back to when Redis cannot be reached. Pass
    the limit this pool had before admission was shared, so a Redis outage
    restores the previous behaviour rather than inventing a new one.
    """
    global_key, user_key = _keys(pool, owner_id)
    token = uuid.uuid4().hex
    now = time.time()
    try:
        redis = connection or get_redis_connection()
        granted, scope = redis.eval(
            _ADMIT_LUA,
            2,
            global_key,
            user_key,
            now,
            now + ttl_seconds,
            token,
            global_limit,
            per_user_limit,
            # A whole-key expiry as a second line of defence: if every member
            # somehow leaks, the key still disappears rather than capping this
            # pool forever.
            int(ttl_seconds) + 60,
        )
    except RedisError as exc:
        # Degraded, not down. Logged as a type, not a message: connection
        # errors can carry the URL, and that may carry a password.
        logger.warning("admission_redis_unavailable pool=%s error=%s", pool, type(exc).__name__)
        if not _local_semaphore(pool, local_limit).acquire(blocking=False):
            raise AdmissionRejected("local", "The assistant is busy. Try again in a moment.") from exc
        return Lease(pool=pool, token=token, owner_id=owner_id, local_limit=local_limit)

    if not granted:
        refused = scope.decode() if isinstance(scope, bytes) else str(scope)
        raise AdmissionRejected(refused, _rejection_message(refused))
    return Lease(pool=pool, token=token, owner_id=owner_id)


def _rejection_message(scope: str) -> str:
    # The per-user case is a different fact from the global one and deserves a
    # different sentence: "you already have one running" is actionable, "we are
    # busy" is not.
    if scope == "user":
        return "You already have as many turns running as this account allows. Wait for one to finish."
    return "The assistant is handling other turns. Try again in a moment."


def release(lease: Lease, *, connection: Redis | None = None) -> None:
    """Give a slot back. Safe to call twice; safe if Redis went away meanwhile."""
    if lease.local_limit is not None:
        try:
            _local_semaphore(lease.pool, lease.local_limit).release()
        except ValueError:
            # BoundedSemaphore raises on over-release. A double release is a
            # bug, but a bug that must not take a request down with it.
            logger.warning("admission_double_release pool=%s", lease.pool)
        return

    global_key, user_key = _keys(lease.pool, lease.owner_id)
    try:
        redis = connection or get_redis_connection()
        pipe = redis.pipeline()
        pipe.zrem(global_key, lease.token)
        pipe.zrem(user_key, lease.token)
        pipe.execute()
    except RedisError as exc:
        # The lease expires on its own, so this is late rather than lost.
        logger.warning("admission_release_failed pool=%s error=%s", lease.pool, type(exc).__name__)


async def acquire_async(
    pool: str,
    *,
    owner_id: str,
    global_limit: int,
    per_user_limit: int,
    ttl_seconds: float,
    local_limit: int,
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.25,
    connection: Redis | None = None,
) -> Lease:
    """`acquire` for the event loop, optionally waiting for a slot (C2).

    At peak, "try again in a moment" for someone who would have waited two
    seconds is a worse answer than waiting. 503 stays, as the timeout, not as
    the first response.

    The Redis call runs in a thread: it is short, but this is the request path
    of an async endpoint and blocking the loop would slow every other turn.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last: AdmissionRejected | None = None
    while True:
        try:
            return await asyncio.to_thread(
                acquire,
                pool,
                owner_id=owner_id,
                global_limit=global_limit,
                per_user_limit=per_user_limit,
                ttl_seconds=ttl_seconds,
                local_limit=local_limit,
                connection=connection,
            )
        except AdmissionRejected as exc:
            last = exc
            # Waiting cannot help a per-user refusal: the caller's own turn has
            # to finish first, and holding the request open pretends otherwise.
            if exc.scope == "user" or time.monotonic() + poll_seconds >= deadline:
                raise
        await asyncio.sleep(poll_seconds)
        if time.monotonic() >= deadline:
            raise last


def active_count(pool: str, *, owner_id: str | None = None, connection: Redis | None = None) -> int:
    """Live lease count, expired ones excluded. For tests and observability."""
    global_key, user_key = _keys(pool, owner_id or "")
    key = user_key if owner_id else global_key
    try:
        redis = connection or get_redis_connection()
        redis.zremrangebyscore(key, "-inf", time.time())
        return int(redis.zcard(key))
    except RedisError:
        return 0
