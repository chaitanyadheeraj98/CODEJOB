"""The id that ties a response, its log lines and its chat turn together.

F2 / §12: *"when a user says 'it hung at 3pm', that is how you find it."* The
id is minted once per request, returned in a response header so the user can
quote it, and stored on the `ChatTurn` the request produced. Those three are
the same string or the id is useless.

Mirrors `app.tenancy` deliberately - same ContextVar shape, same reset
discipline - because both answer "what is this request?" and there is no value
in two idioms for that.

**Never derived from the client.** Nothing sits upstream of this app that mints
a request id, so honouring an inbound `X-Request-ID` would buy no tracing and
cost a value that a caller controls landing in the logs and a database column.
Two requests could then claim the same id, which is precisely the confusion the
id exists to remove.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator
from uuid import uuid4

#: The response header the id is returned in. Also the name the dashboard must
#: be allowed to read cross-origin - see `expose_headers` on the CORS
#: middleware, without which the browser hides it and the user has nothing to
#: quote.
HEADER = "X-Request-ID"

_current_correlation_id: ContextVar[str | None] = ContextVar(
    "current_correlation_id", default=None
)


def new_correlation_id() -> str:
    """A fresh id. Hex, so it is safe in a header, a log line and a URL alike."""
    return uuid4().hex


def correlation_id() -> str | None:
    """The id for this request, or None when there is no request.

    None rather than a fabricated value, unlike `tenancy.owner_id`'s fallback.
    An owner always exists; a request does not. A background job stamped with
    an id that was never in a response header or a log line points at nothing,
    and "there was no request" is the true answer.
    """
    return _current_correlation_id.get()


def set_correlation_id(value: str | None):
    """Set the id for this context. Returns the token needed to reset it."""
    return _current_correlation_id.set(value)


def reset_correlation_id(token) -> None:
    _current_correlation_id.reset(token)


@contextmanager
def correlation_scope(value: str | None = None) -> Generator[str | None, None, None]:
    """Run a block under one id, minting one if not given.

    For work that wants to be traceable without a request behind it - a job
    whose log lines should hang together.
    """
    token = _current_correlation_id.set(value or new_correlation_id())
    try:
        yield correlation_id()
    finally:
        _current_correlation_id.reset(token)
