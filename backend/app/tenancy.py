"""Whose data a request or job is operating on.

`settings.owner_id` is referenced in 421 places under `backend/app`. Replacing
all of them in one change is not reviewable, and a single miss is a
cross-tenant data leak that no existing test would notice. So this module is a
bridge: the value is resolved once, per request or per job, and the sweep of
those 421 call sites happens afterwards in reviewable batches.

**The bridge is what makes sign-in landable. The sweep is what makes it
correct. Do not stop after the bridge** - until it is done, any code path still
reading `settings.owner_id` directly is operating as the single configured
owner regardless of who is signed in.

Two rules:

- The value comes from a **verified session**, never from a request parameter.
  An `owner_id`-shaped query parameter added "temporarily" is a full account
  takeover.
- A background job has no request, so it must set this explicitly from its
  payload. A job that forgets silently writes one user's data into another's
  account, with no error at all. Set it in the job decorator rather than
  trusting each job body to remember.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

from app.config import settings

_current_owner_id: ContextVar[str | None] = ContextVar("current_owner_id", default=None)


def owner_id() -> str:
    """The owner for this request or job.

    Falls back to the configured constant when nothing has been set, which is
    what keeps single-tenant behaviour byte-for-byte identical while
    `feature_auth_enabled` is off.
    """
    return _current_owner_id.get() or settings.owner_id


def current_owner_id_or_none() -> str | None:
    """The resolved owner without the fallback, for code that needs to know."""
    return _current_owner_id.get()


def set_owner_id(value: str | None):
    """Set the owner for this context. Returns the token needed to reset it."""
    return _current_owner_id.set(value)


def reset_owner_id(token) -> None:
    _current_owner_id.reset(token)


@contextmanager
def owner_scope(value: str | None) -> Generator[str, None, None]:
    """Run a block as one owner.

    The intended entry point for background jobs, which have no middleware to
    do it for them.
    """
    token = _current_owner_id.set(value)
    try:
        yield owner_id()
    finally:
        _current_owner_id.reset(token)


def owner_scoped(func):
    """Run a background job as the owner who enqueued it.

    A job has no request and therefore no middleware. Without this it runs
    under `owner_id()`'s fallback - the configured constant - which means one
    user's job quietly writes into another account and raises nothing at all.
    That silence is what makes it dangerous.

    Applied as a decorator rather than left to each job body for the same
    reason: a body that forgets looks exactly like one that remembered. A test
    enumerates the task module and fails if any job function is undecorated, so
    forgetting is caught at the suite rather than in production.

    `owner_id` is popped from the kwargs, so task signatures stay unchanged and
    RQ serialises one extra string.
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        value = kwargs.pop("owner_id", None)
        with owner_scope(value):
            if settings.feature_auth_enabled:
                from app.db import SessionLocal
                from app.services.account_service import is_owner_disabled

                with SessionLocal() as db:
                    if is_owner_disabled(db, owner_id()):
                        return {"status": "skipped", "reason": "account_disabled"}
            return func(*args, **kwargs)

    wrapper.__owner_scoped__ = True
    return wrapper
