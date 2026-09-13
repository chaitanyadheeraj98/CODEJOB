from __future__ import annotations

from redis import Redis
from rq import Queue
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry

from app.config import settings

GMAIL_SYNC_QUEUE = "gmail_sync"
NVOIDS_SYNC_QUEUE = "nvoids_sync"
AUTOMATION_RUN_QUEUE = "automation_run"
EMBEDDING_QUEUE = "embedding_generation"
SCHEDULED_TASK_QUEUE = "scheduled_task"
# Its own queue rather than a share of automation_run: _enqueue_background_job
# rejects a job with 409 while any other job is active on the same queue, and a
# sync running is exactly when a user is most likely to be pasting.
MANUAL_INTAKE_QUEUE = "manual_intake"
# Gmail push events. Separate from gmail_sync because the two have opposite
# shapes: a sync is one long job a user is waiting on, an event is a stream of
# short ones nobody is watching. Sharing a queue would leave notifications
# queued behind a sync that takes minutes, which is the delay this feature
# exists to remove.
GMAIL_EVENT_QUEUE = "gmail_event"
QUEUE_NAMES = frozenset(
    {
        GMAIL_SYNC_QUEUE,
        NVOIDS_SYNC_QUEUE,
        AUTOMATION_RUN_QUEUE,
        EMBEDDING_QUEUE,
        SCHEDULED_TASK_QUEUE,
        MANUAL_INTAKE_QUEUE,
        GMAIL_EVENT_QUEUE,
    }
)


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
