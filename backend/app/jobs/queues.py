from __future__ import annotations

from redis import Redis
from rq import Queue
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry

from app.config import settings

GMAIL_SYNC_QUEUE = "gmail_sync"
NVOIDS_SYNC_QUEUE = "nvoids_sync"
AUTOMATION_RUN_QUEUE = "automation_run"
EMBEDDING_QUEUE = "embedding_generation"
QUEUE_NAMES = frozenset({GMAIL_SYNC_QUEUE, NVOIDS_SYNC_QUEUE, AUTOMATION_RUN_QUEUE, EMBEDDING_QUEUE})


def get_redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url)


def get_queue(name: str, *, connection: Redis | None = None) -> Queue:
    if name not in QUEUE_NAMES:
        raise ValueError(f"Unknown queue: {name}")
    return Queue(name, connection=connection or get_redis_connection())


def active_job_id(name: str, *, connection: Redis | None = None) -> str | None:
    queue = get_queue(name, connection=connection)
    job_ids = list(queue.get_job_ids())
    for registry_type in (StartedJobRegistry, DeferredJobRegistry, ScheduledJobRegistry):
        registry = registry_type(name=queue.name, connection=queue.connection)
        job_ids.extend(registry.get_job_ids())
    return job_ids[0] if job_ids else None


def redis_is_ready(*, connection: Redis | None = None) -> bool:
    return bool((connection or get_redis_connection()).ping())
