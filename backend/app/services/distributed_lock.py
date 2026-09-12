"""Mutual exclusion that survives more than one process.

`taxonomy_embedding_lock`, `taxonomy_bulk_review_lock` and the Telegram
`action_lock` were `threading.Lock` objects in `runtime_state`. They exclude
within one process and nowhere else, so with `--workers` two requests landing
on different workers could both start a taxonomy embedding batch over the same
rows, each believing it held the lock.

Keyed by owner as well as by name
---------------------------------
The locks they replace were global, which was invisible with one tenant and
wrong with two: one account's bulk-review apply blocked every other account's.
Each of these critical sections already operates on one tenant's rows -
`embed_pending_skills(db, owner_id=...)` takes the owner explicitly - so the
exclusion belongs per tenant.

Why the lease is renewed
------------------------
A process-local lock is released by `finally`, and a process that dies releases
everything by dying. A Redis key does neither. A TTL long enough to cover a
slow taxonomy batch would leave a crashed holder blocking that work for as long
as it lasted; a TTL short enough to recover quickly would expire mid-batch and
let a second holder in - which is the exact thing the lock exists to prevent.

So the TTL is short and a watchdog thread extends it while the work runs. A
crashed holder is cleared within one TTL, and a slow one keeps its lock for as
long as it genuinely needs it.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from redis.exceptions import RedisError

from app.jobs.queues import get_redis_connection

logger = logging.getLogger(__name__)

KEY_PREFIX = "lock"

# Named critical sections.
TAXONOMY_EMBEDDING = "taxonomy_embedding"
TAXONOMY_BULK_REVIEW = "taxonomy_bulk_review"
AUTOMATION_ACTION = "automation_action"

# Short, because the watchdog renews. This is the window a crashed holder
# blocks for, not the time the work is allowed to take.
DEFAULT_TTL_SECONDS = 60

# Only the holder may release. An unconditional DEL would let a holder whose
# lease had already expired - and been taken by someone else - delete the new
# holder's lock on its way out.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Same ownership check for renewal: a holder that has already lost its lease
# must not extend somebody else's.
_RENEW_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class LockBusy(Exception):
    """Someone else holds this lock."""


def _key(name: str, owner_id: str) -> str:
    return f"{KEY_PREFIX}:{name}:{owner_id}"


@contextmanager
def hold(
    name: str,
    *,
    owner_id: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    connection=None,
) -> Iterator[None]:
    """Hold `name` for `owner_id`, or raise `LockBusy` immediately.

    Non-blocking, because every call site this replaces was
    `acquire(blocking=False)` answering 409. Waiting would turn a clear "one of
    these is already running" into a request that hangs.
    """
    key = _key(name, owner_id)
    token = uuid.uuid4().hex
    try:
        # Inside the try: resolving the connection can fail too, not just using
        # it - a bad URL raises here rather than at the first command.
        redis = connection or get_redis_connection()
        acquired = bool(redis.set(key, token, nx=True, ex=ttl_seconds))
    except RedisError as exc:
        # Fail closed. These guard writes - a taxonomy batch and a bulk apply -
        # and running two of them concurrently is worse than running neither.
        # Logged as a type: a connection error carries the URL.
        logger.warning("lock_redis_unavailable name=%s error=%s", name, type(exc).__name__)
        raise LockBusy(name) from exc

    if not acquired:
        raise LockBusy(name)

    stop = threading.Event()

    def renew() -> None:
        # A third of the TTL, so two consecutive failures still leave a margin.
        while not stop.wait(ttl_seconds / 3):
            try:
                redis.eval(_RENEW_LUA, 1, key, token, ttl_seconds)
            except RedisError as exc:
                logger.warning("lock_renew_failed name=%s error=%s", name, type(exc).__name__)

    watchdog = threading.Thread(target=renew, name=f"lock-renew-{name}", daemon=True)
    watchdog.start()
    try:
        yield
    finally:
        stop.set()
        watchdog.join(timeout=1.0)
        try:
            redis.eval(_RELEASE_LUA, 1, key, token)
        except RedisError as exc:
            # The lease expires on its own, so this is late rather than lost.
            logger.warning("lock_release_failed name=%s error=%s", name, type(exc).__name__)


def is_held(name: str, *, owner_id: str, connection=None) -> bool:
    """For diagnostics and tests. Never used to decide whether to proceed."""
    try:
        redis = connection or get_redis_connection()
        return bool(redis.exists(_key(name, owner_id)))
    except RedisError:
        return False
