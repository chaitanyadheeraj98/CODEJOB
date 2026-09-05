"""Assert the worker consumes every queue the application can enqueue to.

The failure this catches is silent. `get_queue` accepts any name in
`QUEUE_NAMES`, `_enqueue_background_job` writes a RecentRun and returns 200, and
the job then sits in Redis forever because no worker is listening for it. There
is no error, no log line, and no unit test that can see it - a fake queue in a
test always succeeds.

So the check is a text comparison between two files that have to agree and have
no other link: the `command:` of the worker service in docker-compose.yml, and
QUEUE_NAMES in app/jobs/queues.py.

Run it in CI on any change to either file, and by hand before a deploy:

    python backend/scripts/check_worker_queues.py

Exit code 0 means they agree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
QUEUES_FILE = REPO_ROOT / "backend" / "app" / "jobs" / "queues.py"

# Everything between `rq worker` and the first flag is the queue list.
_WORKER_COMMAND_RE = re.compile(r"command:\s*rq worker\s+(?P<queues>[^\n]*?)\s+--", re.MULTILINE)
# Only the constants, not QUEUE_NAMES' own body, so a name present in one and
# absent from the other is still caught.
_QUEUE_CONSTANT_RE = re.compile(r'^[A-Z_]+_QUEUE\s*=\s*"(?P<name>[a-z_]+)"', re.MULTILINE)


def worker_queues(compose_text: str) -> list[str]:
    match = _WORKER_COMMAND_RE.search(compose_text)
    if match is None:
        raise SystemExit(
            "Could not find the worker `command: rq worker ...` line in docker-compose.yml. "
            "If the worker is started differently now, update this script - do not delete it."
        )
    return match.group("queues").split()


def declared_queues(queues_text: str) -> list[str]:
    return [match.group("name") for match in _QUEUE_CONSTANT_RE.finditer(queues_text)]


def main() -> int:
    declared = set(declared_queues(QUEUES_FILE.read_text(encoding="utf-8")))
    consumed = set(worker_queues(COMPOSE_FILE.read_text(encoding="utf-8")))

    if not declared:
        print("No *_QUEUE constants found in app/jobs/queues.py - the check would pass vacuously.")
        return 1

    unconsumed = sorted(declared - consumed)
    unknown = sorted(consumed - declared)

    if unconsumed:
        print("FAIL: queues the app can enqueue to but no worker consumes:")
        for name in unconsumed:
            print(f"  - {name}")
        print(
            "\nJobs on these sit queued forever with no error. Add them to the worker "
            f"command in {COMPOSE_FILE.name}."
        )
    if unknown:
        print("FAIL: queues the worker consumes that the app never declares:")
        for name in unknown:
            print(f"  - {name}")
        print(f"\nEither the constant was removed from {QUEUES_FILE.name} or the name is a typo.")

    if unconsumed or unknown:
        return 1

    print(f"OK: worker consumes all {len(declared)} declared queues: {', '.join(sorted(declared))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
