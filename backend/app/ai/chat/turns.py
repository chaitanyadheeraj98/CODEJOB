import asyncio
from contextlib import aclosing, suppress
from dataclasses import dataclass
from threading import BoundedSemaphore

from app.config import settings


@dataclass
class ActiveTurn:
    session_id: int
    task: asyncio.Task
    queue: asyncio.Queue
    cancelled: bool = False


active_turns: dict[str, ActiveTurn] = {}
slots = BoundedSemaphore(settings.chat_max_concurrent_turns)


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


def cancel(session_id: int, turn_id: str) -> bool:
    turn = active_turns.get(turn_id)
    if turn is None or turn.session_id != session_id:
        return False
    turn.cancelled = True
    turn.task.cancel()
    return True
