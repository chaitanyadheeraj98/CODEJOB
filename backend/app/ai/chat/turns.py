"""Live chat turns, and the cancel that has to reach them.

`active_turns` holds an `asyncio.Task` and an `asyncio.Queue`, and neither can
live anywhere but the process running the turn. What *can* move is the
**request** to cancel one.

That matters from C3 onwards. With one API process, "stop" always lands on the
process streaming the turn and cancelling is a dictionary lookup. With four,
three times out of four it lands somewhere else, finds nothing in its own
`active_turns`, and returns 404 while the turn keeps running and keeps costing.

So a turn registers itself in Redis, "stop" writes a flag there, and a small
watcher on the streaming process notices and cancels locally. If Redis is
unreachable this falls back to exactly the single-process behaviour it
replaces: local cancel works, cross-process cancel does not.
"""

import asyncio
import logging
from contextlib import aclosing, suppress
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.config import settings
from app.jobs.queues import get_redis_connection

logger = logging.getLogger(__name__)

KEY_PREFIX = "chat:turn"
# How often a streaming process asks whether someone else cancelled its turn.
# A quarter second is imperceptible against a model that streams for seconds,
# and cheap against a cap of a couple of dozen concurrent turns.
CANCEL_POLL_SECONDS = 0.25


@dataclass
class ActiveTurn:
    session_id: int
    task: asyncio.Task
    queue: asyncio.Queue
    cancelled: bool = False
    watcher: asyncio.Task | None = None


active_turns: dict[str, ActiveTurn] = {}


def _key(turn_id: str) -> str:
    return f"{KEY_PREFIX}:{turn_id}"


def _ttl() -> int:
    # Outlives the budget so a turn is never unregistered while still running,
    # and expires on its own so a killed worker leaves no debris.
    return int(settings.chat_turn_budget_seconds) + 60


def register(turn_id: str, session_id: int) -> None:
    """Publish that this turn is running here, so another process can stop it."""
    try:
        redis = get_redis_connection()
        key = _key(turn_id)
        redis.hset(key, mapping={"session_id": session_id, "cancelled": 0})
        redis.expire(key, _ttl())
    except RedisError as exc:
        # Degraded to single-process behaviour, which is what this was before.
        logger.warning("turn_register_failed error=%s", type(exc).__name__)


def unregister(turn_id: str) -> None:
    try:
        get_redis_connection().delete(_key(turn_id))
    except RedisError as exc:
        logger.warning("turn_unregister_failed error=%s", type(exc).__name__)


def request_cancel(turn_id: str, session_id: int) -> bool:
    """Flag a turn running on some other process. False if it is not there.

    The session is checked against the stored one for the same reason the local
    path checks it: a turn id is not a permission to stop someone else's turn.
    """
    try:
        redis = get_redis_connection()
        stored = redis.hget(_key(turn_id), "session_id")
        if stored is None or int(stored) != session_id:
            return False
        redis.hset(_key(turn_id), "cancelled", 1)
        return True
    except RedisError as exc:
        logger.warning("turn_cancel_publish_failed error=%s", type(exc).__name__)
        return False


def cancel_requested(turn_id: str) -> bool:
    try:
        flag = get_redis_connection().hget(_key(turn_id), "cancelled")
        return flag is not None and int(flag) == 1
    except RedisError:
        return False


async def _watch_for_remote_cancel(turn_id: str) -> None:
    """Cancel this process's turn when another process asks for it."""
    while True:
        await asyncio.sleep(CANCEL_POLL_SECONDS)
        turn = active_turns.get(turn_id)
        if turn is None:
            return
        if await asyncio.to_thread(cancel_requested, turn_id):
            turn.cancelled = True
            turn.task.cancel()
            return


def start(session_id: int, turn_id: str, source) -> ActiveTurn:
    # ponytail: buffer at most one budgeted turn; bound the queue if token volume grows.
    queue = asyncio.Queue()

    async def produce():
        try:
            async with aclosing(source):
                async for event in source:
                    queue.put_nowait(event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            queue.put_nowait(exc)
        finally:
            queue.put_nowait(None)

    turn = ActiveTurn(session_id, asyncio.create_task(produce()), queue)
    active_turns[turn_id] = turn
    register(turn_id, session_id)
    turn.watcher = asyncio.create_task(_watch_for_remote_cancel(turn_id))
    return turn


async def events(turn: ActiveTurn):
    while True:
        event = await turn.queue.get()
        if event is None:
            return
        if isinstance(event, Exception):
            raise event
        yield event


async def close(turn_id: str) -> None:
    turn = active_turns.pop(turn_id, None)
    if turn:
        turn.task.cancel()
        with suppress(asyncio.CancelledError):
            await turn.task
        if turn.watcher is not None:
            turn.watcher.cancel()
            with suppress(asyncio.CancelledError):
                await turn.watcher
    # After the local pop, so the watcher cannot resurrect the entry.
    unregister(turn_id)


def cancel(session_id: int, turn_id: str) -> bool:
    """Stop a turn, wherever it is running.

    The local path stays first and stays synchronous: it is the common case and
    it cancels immediately rather than within a poll interval.
    """
    turn = active_turns.get(turn_id)
    if turn is not None:
        if turn.session_id != session_id:
            return False
        turn.cancelled = True
        turn.task.cancel()
        # Flagged as well, so a retry of the same request is a no-op rather
        # than a 404, and so nothing is left set if this process dies here.
        request_cancel(turn_id, session_id)
        return True
    return request_cancel(turn_id, session_id)
