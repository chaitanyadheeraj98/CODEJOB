"""One structured line per request: who, what route, what came back, how long.

F3 / §12. Paired with `app.correlation`: the id in each line is the id the
response returned, so a user quoting it lands on the exact request.

**§12.1 governs every field here.** Identifiers, timings, counts and error
codes only. Two consequences that are easy to get wrong:

- The **route template** is logged, never the request path. `/candidates/4821`
  is an identifier; a path is not guaranteed to be one, and an *unmatched* path
  is a string the caller chose, so echoing it into a log file is letting a
  stranger write to it.
- The **query string is never logged**, at all. Nine endpoints take a free-text
  `q` parameter, and people paste recruiter addresses and subject lines into
  search boxes - which is precisely the content §12.1 forbids recording.

The logger keeps its own handler and does not propagate. The root handler in
this process is a `RichHandler` installed as a side effect of a third-party
import, and it *wraps long lines*, which would split one record across several
output lines and make it unparseable. A log format that depends on which
library won an import race is not a format.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Mapping

LOGGER_NAME = "app.request"

#: What is logged in place of a path that matched no route. The path itself is
#: caller-controlled, so it is never written out.
UNMATCHED_ROUTE = "<unmatched>"

logger = logging.getLogger(LOGGER_NAME)


def configure(stream: Any = None) -> logging.Logger:
    """Give the request logger its own handler, once.

    Idempotent: uvicorn imports the app in each worker, and a second handler
    would mean every request logged twice.
    """
    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # See the module docstring: not the root's to reformat or wrap.
    logger.propagate = False
    return logger


def route_template(scope: Mapping[str, Any]) -> str:
    """The matched route's template, or `UNMATCHED_ROUTE`.

    Starlette puts the route on the scope once it has matched, and the scope is
    shared, so a middleware outside the router can read it after the response
    comes back. Nothing matched means nothing safe to log.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


def log_request(
    *,
    correlation_id: str,
    owner_id: str | None,
    method: str,
    route: str,
    status: int,
    duration_ms: int,
) -> None:
    """Emit the record. Never raises: logging must not break a served request."""
    try:
        logger.info(json.dumps({
            "event": "request",
            "id": correlation_id,
            "owner": owner_id,
            "method": method,
            "route": route,
            "status": status,
            "duration_ms": duration_ms,
        }, separators=(",", ":")))
    except Exception:  # pragma: no cover - defensive
        pass
