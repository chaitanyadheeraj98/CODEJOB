"""F3 / §12: one structured line per request, and nothing in it that is content.

The line exists so "it hung at 3pm" can be answered: which owner, which route,
what status, how long, and the id they were given. The tests that matter most
here are the negative ones - that a free-text query string and an unmatched,
caller-chosen path never reach the log file.
"""

import json
import logging
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import correlation, main, request_log
from app.config import settings
from app.models import Base

SECRET = "recruiter@acme.example urgent contract"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class RequestLoggingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        main.app.dependency_overrides[main.get_db] = self._session

        self.capture = _Capture()
        request_log.logger.addHandler(self.capture)
        self.client = TestClient(main.app)

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        request_log.logger.removeHandler(self.capture)
        main.app.dependency_overrides.pop(main.get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _records(self) -> list[dict]:
        return [json.loads(line) for line in self.capture.lines]

    # --- the line is there and says what it should ------------------------

    def test_one_line_per_request(self):
        self.client.get("/health")
        self.client.get("/health")
        self.assertEqual(len(self._records()), 2)

    def test_the_line_carries_the_id_the_response_returned(self):
        """The join between a user's complaint and the server's record."""
        response = self.client.get("/health")
        self.assertEqual(self._records()[0]["id"], response.headers[correlation.HEADER])

    def test_the_line_reports_route_status_method_and_duration(self):
        self.client.get("/health")
        record = self._records()[0]
        self.assertEqual(record["route"], "/health")
        self.assertEqual(record["status"], 200)
        self.assertEqual(record["method"], "GET")
        self.assertIsInstance(record["duration_ms"], int)
        self.assertGreaterEqual(record["duration_ms"], 0)

    def test_a_refused_request_is_logged_with_its_status(self):
        """Registered outside the session middleware, so the 401 is recorded.

        A log that only contains successes is missing every request anyone
        would ask about.
        """
        with patch.object(settings, "feature_auth_enabled", True):
            self.client.get("/settings/bootstrap")
        self.assertEqual(self._records()[0]["status"], 401)

    def test_the_owner_is_recorded(self):
        self.client.get("/health")
        self.assertEqual(self._records()[0]["owner"], settings.owner_id)

    def test_a_signed_in_request_is_logged_as_that_user(self):
        """The multi-tenant version of the question: not "was it slow" but
        "was it slow *for them*".

        Worth its own test because the flag is pinned off for the suite, so
        every other assertion here reads the owner from the single-tenant
        branch. Deleting the line that stashes the resolved owner broke nothing
        until this existed.
        """
        signed_in = SimpleNamespace(owner_id="usr_signed_in")
        with patch.object(settings, "feature_auth_enabled", True),                 patch.object(main.auth_service, "resolve_session", return_value=signed_in):
            self.client.cookies.set(settings.session_cookie_name, "a-session-token")
            try:
                response = self.client.get("/health")
            finally:
                self.client.cookies.clear()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._records()[0]["owner"], "usr_signed_in")

    # --- §12.1: what must never appear -----------------------------------

    def test_the_query_string_is_never_logged(self):
        """Nine endpoints take a free-text `q`, and people paste recruiter
        addresses and subject lines into search boxes. uvicorn's own access log
        writes the full request line, which is why it is switched off."""
        self.client.get("/health", params={"q": SECRET})
        blob = "".join(self.capture.lines)
        self.assertNotIn("recruiter@acme.example", blob)
        self.assertNotIn("urgent", blob)
        self.assertNotIn("q=", blob)

    def test_an_unmatched_path_is_not_echoed_back_into_the_log(self):
        """A path that matched no route is a string the caller chose. Writing
        it to the log file is letting a stranger write to the log file."""
        self.client.get("/no/such/route/" + SECRET.replace(" ", "-"))
        record = self._records()[0]
        self.assertEqual(record["route"], request_log.UNMATCHED_ROUTE)
        self.assertNotIn("acme", "".join(self.capture.lines))

    def test_a_path_parameter_is_logged_as_its_template(self):
        """`/candidates/{email_id}` rather than the id itself, and certainly
        rather than whatever else a path segment might carry.

        The substitution is by pattern rather than by parameter name. Spelling
        the name out meant the request went to the literal `{email_id}`, which
        FastAPI happily matched - so the path and the template were the same
        string and the test could not tell them apart. It passed while the
        middleware logged raw paths.
        """
        routes = {getattr(route, "path", "") for route in main.app.router.routes}
        template = next(p for p in sorted(routes) if p.startswith("/candidates/{"))
        url = re.sub(r"\{[^}]+\}", "4821", template)
        self.assertNotEqual(url, template, "the URL must be a real path, not the template")

        self.client.get(url)
        logged = self._records()[0]["route"]
        self.assertEqual(logged, template)
        self.assertNotIn("4821", logged)

    def test_the_record_carries_only_the_agreed_fields(self):
        """An allowlist, for the reason `test_chat_turn_owner` has one: a field
        added later should have to answer "is this content?" out loud."""
        self.client.get("/health")
        self.assertEqual(
            set(self._records()[0]),
            {"event", "id", "owner", "method", "route", "status", "duration_ms"},
        )

    # --- the format has to survive the process it runs in -----------------

    def test_the_logger_does_not_propagate_to_the_root(self):
        """The root handler here is a RichHandler installed as a side effect of
        a third-party import, and it wraps long lines - which would split one
        record across several and make it unparseable."""
        self.assertFalse(request_log.logger.propagate)
        self.assertTrue(request_log.logger.handlers)

    def test_configure_is_idempotent(self):
        """Called at import, once per uvicorn worker. A second handler would
        log every request twice."""
        before = len(request_log.logger.handlers)
        request_log.configure()
        request_log.configure()
        self.assertEqual(len(request_log.logger.handlers), before)

    def test_logging_never_breaks_the_request(self):
        with patch.object(request_log.logger, "info", side_effect=RuntimeError("boom")):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)


class UvicornAccessLogTests(unittest.TestCase):
    """The leak this phase actually closes is a deployment setting.

    uvicorn's access log writes `GET /gmail/live-replies?q=<whatever the user
    typed> HTTP/1.1`. Verified against the running container before the change.
    Asserted here because it is one flag away from coming back and nothing else
    in the suite would notice.
    """

    def test_the_backend_runs_uvicorn_with_the_access_log_off(self):
        """Parsed, not grepped.

        The first version read the file as text, and the comment above the
        command explains *why* the flag is there - so it contained the flag,
        and the assertion passed with the flag deleted from the command itself.
        YAML drops comments, which is the point.
        """
        from pathlib import Path

        import yaml

        compose = yaml.safe_load(
            (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
        )
        command = compose["services"]["backend"]["command"]
        self.assertIn("uvicorn app.main:app", command)
        self.assertIn("--no-access-log", command)


if __name__ == "__main__":
    unittest.main()
