"""F2 / §12: one id per request, returned to the user and stored on the turn.

*"When a user says 'it hung at 3pm', that is how you find it."* That only works
if the string in the response header, the string in the ContextVar and the
string on the `ChatTurn` row are the same one - so the test that matters here
is the one that compares them, not the one that checks a header exists.

Driven through the real middleware stack rather than by calling the module,
because what is being asserted is a property of requests.
"""

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import correlation, main
from app.config import settings
from app.models import Base, ChatTurn
from app.services.chat_service import ChatService

HEADER = correlation.HEADER
PROBE = "/__correlation_probe__"
STREAM_PROBE = "/__correlation_stream_probe__"


class CorrelationIdTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

        # A probe route, added for the duration of the test and removed after.
        # Declared `def`, not `async def`, on purpose: 261 of this app's 262
        # endpoints are sync and run in the anyio threadpool, so the threadpool
        # is the context-propagation path that actually carries the product. An
        # async probe would pass while every real endpoint recorded None.
        db_factory = self.Session

        @main.app.get(PROBE)
        def _probe():
            db = db_factory()
            try:
                turn = ChatService._record_turn(
                    db, session_id=1, message_id=None, requested_model="auto",
                    prompt_sha256="abc",
                )
                return {"stored": turn.correlation_id if turn else None,
                        "seen": correlation.correlation_id()}
            finally:
                db.close()

        # A streaming probe, shaped like the one path that really writes turns.
        # `call_next` returns as soon as the response *starts*, so the
        # middleware has already reset its own ContextVar by the time this
        # generator runs. It still sees the id because the downstream task was
        # spawned with a copy of the context - and a copy is not reset by the
        # parent. Asserting it here rather than reasoning about it, because
        # every real chat turn is recorded from inside a generator like this.
        @main.app.get(STREAM_PROBE)
        def _stream_probe():
            def body():
                db = db_factory()
                try:
                    ChatService._record_turn(
                        db, session_id=1, message_id=None, requested_model="auto",
                        prompt_sha256="abc",
                    )
                    yield b"done"
                finally:
                    db.close()

            return StreamingResponse(body(), media_type="text/event-stream")

        # The MCP server is mounted at "/", and a Mount matches every path
        # under it, so a route appended after it is unreachable. Move the probes
        # in front of the mount rather than trusting append order.
        self._probe_routes = [main.app.router.routes.pop(), main.app.router.routes.pop()]
        for route in self._probe_routes:
            main.app.router.routes.insert(0, route)

        self.client = TestClient(main.app)

    def tearDown(self):
        for route in self._probe_routes:
            main.app.router.routes.remove(route)
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    # --- the tie ---------------------------------------------------------

    def test_the_id_in_the_header_is_the_id_on_the_turn(self):
        """The whole point. A header and a column that do not match are two
        useless facts instead of one useful one."""
        response = self.client.get(PROBE)
        returned = response.headers[HEADER]

        self.assertEqual(response.json()["stored"], returned)
        self.assertEqual(
            self.db.query(ChatTurn).one().correlation_id, returned,
            "the row a user would be looked up by must carry the id they were given",
        )

    def test_the_route_sees_the_id_across_the_threadpool_hop(self):
        response = self.client.get(PROBE)
        self.assertEqual(response.json()["seen"], response.headers[HEADER])

    def test_a_turn_recorded_mid_stream_gets_the_id_too(self):
        """Every real chat turn is written from inside a streaming response,
        after the middleware has reset its own copy of the ContextVar."""
        response = self.client.get(STREAM_PROBE)
        self.assertEqual(response.text, "done")
        self.assertEqual(
            self.db.query(ChatTurn).one().correlation_id, response.headers[HEADER]
        )

    # --- the id itself ---------------------------------------------------

    def test_every_request_gets_one(self):
        self.assertIn(HEADER, self.client.get("/health").headers)

    def test_each_request_gets_its_own(self):
        first = self.client.get("/health").headers[HEADER]
        second = self.client.get("/health").headers[HEADER]
        self.assertNotEqual(first, second, "a shared id cannot distinguish two requests")

    def test_a_client_supplied_id_is_ignored(self):
        """Nothing upstream mints one, so honouring the header would only let a
        caller choose what lands in the logs and collide two requests onto one
        id - the exact confusion the id exists to remove."""
        forged = "../../etc/passwd\nfake-log-line"
        response = self.client.get("/health", headers={HEADER: forged})
        self.assertNotEqual(response.headers[HEADER], forged)
        self.assertRegex(response.headers[HEADER], r"^[0-9a-f]{32}$")

    # --- the requests people actually ask about --------------------------

    def test_a_refused_request_still_carries_an_id(self):
        """This middleware is registered outside the two that refuse requests.

        A 200 is rarely the one someone asks about; "it failed at 3pm" is. An
        id that only appears on success is missing from every case it exists
        for.
        """
        with patch.object(settings, "feature_auth_enabled", True):
            unauthenticated = self.client.get("/settings/bootstrap")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertIn(HEADER, unauthenticated.headers)

        with patch.object(settings, "feature_user_taxonomy_enabled", False):
            hidden = self.client.get("/settings/entities/role/pending")
        self.assertEqual(hidden.status_code, 404)
        self.assertIn(HEADER, hidden.headers)

    # --- the browser has to be able to read it ---------------------------

    def test_the_header_is_exposed_to_the_dashboard_origin(self):
        """The dashboard is a different origin. A response header is invisible
        to cross-origin JavaScript unless CORS names it, so without this the id
        is returned and the browser hides it."""
        origin = settings.effective_cors_allowed_origins[0]
        response = self.client.get("/health", headers={"Origin": origin})
        exposed = response.headers.get("access-control-expose-headers", "")
        self.assertIn(HEADER.lower(), exposed.lower())

    # --- no request, no id -----------------------------------------------

    def test_outside_a_request_there_is_no_id_and_the_turn_says_so(self):
        """A background job stamped with an invented id points at nothing, and
        looks like an answer. None is the true value."""
        self.assertIsNone(correlation.correlation_id())
        ChatService._record_turn(
            self.db, session_id=1, message_id=None, requested_model="auto",
            prompt_sha256="abc",
        )
        self.assertIsNone(self.db.query(ChatTurn).one().correlation_id)

    def test_an_explicit_id_is_not_overwritten(self):
        """`setdefault`, matching owner_id: a caller that knows better wins."""
        with correlation.correlation_scope("from-the-request"):
            ChatService._record_turn(
                self.db, session_id=1, message_id=None, requested_model="auto",
                prompt_sha256="abc", correlation_id="explicit",
            )
        self.assertEqual(self.db.query(ChatTurn).one().correlation_id, "explicit")

    def test_the_scope_restores_what_it_replaced(self):
        with correlation.correlation_scope("outer") as outer:
            with correlation.correlation_scope("inner"):
                self.assertEqual(correlation.correlation_id(), "inner")
            self.assertEqual(correlation.correlation_id(), outer)
        self.assertIsNone(correlation.correlation_id())


if __name__ == "__main__":
    unittest.main()
