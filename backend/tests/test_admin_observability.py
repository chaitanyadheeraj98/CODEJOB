"""F4 / §12.4: the observability page, and the line it must not cross.

Two kinds of test here. The first kind checks the numbers answer §12.2's
questions - *"the assistant is slow for me"* - per user and overall. The second
kind checks that answering them reads nothing it should not: five columns, a
response model that emits a fixed field set, and no account email.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import settings
from app.models import Base, ChatTurn, User
from app.schemas import ObservabilityResponse
from app.services import observability_service

ADMIN = "usr_admin"
ALICE = "usr_alice"
BOB = "usr_bob"


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.now = datetime.now(UTC)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _turn(self, owner_id: str, *, minutes_ago: int = 1, duration_ms: int = 100,
              ttft: int | None = 50, failure_code: str | None = None) -> None:
        self.db.add(ChatTurn(
            owner_id=owner_id, session_id=1, model="m", requested_model="auto",
            duration_ms=duration_ms, time_to_first_token_ms=ttft,
            failure_code=failure_code, prompt_sha256="abc",
            created_at=self.now - timedelta(minutes=minutes_ago),
        ))
        self.db.commit()

    def _summary(self, **kwargs):
        return observability_service.summarise(self.db, **kwargs)


class NumbersTests(_Base):
    def test_it_counts_turns_in_the_window_and_ignores_older_ones(self):
        self._turn(ALICE, minutes_ago=5)
        self._turn(ALICE, minutes_ago=60 * 30)  # 30 hours ago
        summary = self._summary(window_hours=24)
        self.assertEqual(summary["turns"], 1)

    def test_percentiles_are_by_index_not_interpolation(self):
        for value in (100, 200, 300, 400, 500):
            self._turn(ALICE, duration_ms=value)
        summary = self._summary()
        self.assertEqual(summary["duration_ms"]["p50"], 300)
        self.assertEqual(summary["duration_ms"]["p95"], 500)

    def test_a_missing_metric_is_null_rather_than_zero(self):
        """`time_to_first_token_ms` is null when a turn failed before the first
        token. Counting those as zero would report the assistant getting faster
        the more often it broke.

        Three rows, two of them null, so zero-filling moves the median. With
        one null and one value it does not: the median index of an even-length
        list lands on the real number either way, and the test passed against
        the bug it was written for.
        """
        self._turn(ALICE, ttft=None, failure_code="mcp_unavailable")
        self._turn(ALICE, ttft=None, failure_code="mcp_unavailable")
        self._turn(ALICE, ttft=400)
        summary = self._summary()
        self.assertEqual(summary["time_to_first_token_ms"]["p50"], 400)
        self.assertEqual(summary["turns"], 3, "the failed turns still count as turns")

    def test_no_turns_gives_nulls_not_an_error(self):
        summary = self._summary()
        self.assertEqual(summary["turns"], 0)
        self.assertIsNone(summary["duration_ms"]["p50"])

    def test_failure_codes_are_ordered_by_frequency(self):
        for _ in range(3):
            self._turn(ALICE, failure_code="mcp_unavailable")
        self._turn(ALICE, failure_code="admission_rejected")
        summary = self._summary()
        self.assertEqual(
            summary["failure_codes"],
            [{"code": "mcp_unavailable", "turns": 3}, {"code": "admission_rejected", "turns": 1}],
        )

    def test_admission_rejected_is_counted_on_its_own(self):
        """§12.2: it is the capacity signal, so it is read directly rather than
        hunted for in the failure-code list."""
        self._turn(ALICE, failure_code="admission_rejected")
        self._turn(ALICE, failure_code="mcp_unavailable")
        summary = self._summary()
        self.assertEqual(summary["admission_rejected"], 1)
        self.assertEqual(summary["failed"], 2)

    def test_turns_per_hour_fills_the_empty_hours(self):
        """A gap must read as a zero, not as a missing bucket a chart would
        smooth over."""
        self._turn(ALICE, minutes_ago=5)
        summary = self._summary(window_hours=6)
        self.assertGreaterEqual(len(summary["turns_per_hour"]), 6)
        self.assertEqual(sum(b["turns"] for b in summary["turns_per_hour"]), 1)
        self.assertTrue(any(b["turns"] == 0 for b in summary["turns_per_hour"]))

    def test_two_users_are_reported_separately(self):
        """The §12.2 question is "slow *for me*", which a global p95 cannot
        answer."""
        for value in (100, 100, 100):
            self._turn(ALICE, duration_ms=value)
        for value in (9000, 9000):
            self._turn(BOB, duration_ms=value)

        per_user = {entry["owner_id"]: entry for entry in self._summary()["per_user"]}
        self.assertEqual(per_user[ALICE]["turns"], 3)
        self.assertEqual(per_user[ALICE]["duration_ms"]["p50"], 100)
        self.assertEqual(per_user[BOB]["duration_ms"]["p50"], 9000)

    def test_the_busiest_users_come_first(self):
        self._turn(ALICE)
        for _ in range(3):
            self._turn(BOB)
        self.assertEqual([e["owner_id"] for e in self._summary()["per_user"]], [BOB, ALICE])

    def test_the_row_cap_is_reported_rather_than_hidden(self):
        """A partial percentile must not be mistaken for a complete one."""
        with patch.object(observability_service, "MAX_ROWS", 2):
            self._turn(ALICE)
            self._turn(ALICE)
            self._turn(ALICE)
            summary = self._summary()
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["turns"], 2)

    def test_a_short_window_is_not_marked_truncated(self):
        self._turn(ALICE)
        self.assertFalse(self._summary()["truncated"])

    def test_a_broken_query_returns_an_empty_summary_rather_than_raising(self):
        """A page reporting on an outage must not be part of it."""
        with patch.object(self.db, "query", side_effect=RuntimeError("boom")):
            summary = observability_service.summarise(self.db)
        self.assertEqual(summary["turns"], 0)


class PrivacyTests(_Base):
    """§12.1. The highest-value target in the product is an admin page that
    renders other people's mail, so these are the tests that matter most."""

    def test_only_the_five_agreed_columns_are_read(self):
        """An allowlist, asserted against what the service declares it reads.

        `ChatTurn` carries no message content today, but "the table happens to
        be safe" is not a control - naming the columns is.
        """
        self.assertEqual(
            set(observability_service.READS_COLUMNS),
            {"owner_id", "created_at", "duration_ms", "time_to_first_token_ms", "failure_code"},
        )
        for name in observability_service.READS_COLUMNS:
            self.assertIn(name, ChatTurn.__table__.columns, f"{name} is not a chat_turn column")

    def test_the_service_reads_no_column_outside_that_list(self):
        """Checked against the SQL the service actually emits.

        The first version of this test built the query itself and asserted
        against that, which proves only that the test can write a safe query.
        The statements are captured off the engine now, so adding a column to
        the service is what the assertion sees.
        """
        self._turn(ALICE)
        statements: list[str] = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        try:
            observability_service.summarise(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", _record)

        selected = " ".join(s for s in statements if "chat_turn" in s)
        self.assertTrue(selected, "the service issued no query against chat_turn")
        for column in ChatTurn.__table__.columns.keys():
            if column in observability_service.READS_COLUMNS:
                continue
            self.assertNotIn(
                f"chat_turn.{column}", selected,
                f"{column} is read but not on the §12.1 allowlist",
            )

    def test_the_response_model_emits_a_fixed_field_set(self):
        """The model is the control. A field added to the service's dict cannot
        reach the page without being declared here first, which is the moment
        to ask whether it is content."""
        self.assertEqual(
            set(ObservabilityResponse.model_fields),
            {
                "window_hours", "since", "until", "truncated", "turns", "failed",
                "admission_rejected", "duration_ms", "time_to_first_token_ms",
                "turns_per_hour", "failure_codes", "per_user",
            },
        )

    def test_a_user_entry_carries_an_owner_id_and_no_email(self):
        """An admin can map it through /admin/users. The fewer places an
        account's address is rendered, the fewer places it leaks from."""
        self._turn(ALICE)
        entry = self._summary()["per_user"][0]
        self.assertEqual(entry["owner_id"], ALICE)
        self.assertNotIn("email", entry)

    def test_nothing_in_the_payload_looks_like_an_address_or_a_subject(self):
        self._turn(ALICE, failure_code="mcp_unavailable")
        body = ObservabilityResponse(**self._summary()).model_dump_json()
        self.assertNotIn("@", body)
        self.assertNotIn("prompt", body)
        self.assertNotIn("content", body)


class RouteTests(unittest.TestCase):
    """The gate, which is the same `is_admin` database column as the rest of
    /admin - never a token claim, never a request body field."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        main.app.dependency_overrides[main.get_db] = self._session
        self.client = TestClient(main.app)

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        main.app.dependency_overrides.pop(main.get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _as(self, user):
        return patch.object(main.auth_service, "resolve_session", return_value=user)

    def _get(self, **params):
        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            return self.client.get("/admin/observability", params=params)
        finally:
            self.client.cookies.clear()

    def test_an_admin_gets_the_summary(self):
        admin = User(email="a@example.com", owner_id=ADMIN, is_admin=True)
        with patch.object(settings, "feature_auth_enabled", True), self._as(admin):
            response = self._get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("per_user", response.json())

    def test_a_signed_in_non_admin_is_refused(self):
        plain = User(email="b@example.com", owner_id=ALICE, is_admin=False)
        with patch.object(settings, "feature_auth_enabled", True), self._as(plain):
            response = self._get()
        self.assertEqual(response.status_code, 403)

    def test_an_anonymous_caller_is_refused(self):
        """End to end, though the refusal comes from the session middleware
        rather than `require_admin` - it runs first and 401s any non-public
        path without a session. The admin gate is what the other two tests
        reach; this one asserts the outer guarantee.
        """
        with patch.object(settings, "feature_auth_enabled", True), self._as(None):
            response = self._get()
        self.assertEqual(response.status_code, 401)

    def test_it_is_absent_when_sign_in_is_off(self):
        """404 rather than 403, matching the rest of /admin: a disabled feature
        should look absent."""
        with patch.object(settings, "feature_auth_enabled", False):
            response = self._get()
        self.assertEqual(response.status_code, 404)

    def test_an_out_of_range_window_is_rejected_rather_than_clamped(self):
        """A silent clamp answers a different question than the one asked."""
        admin = User(email="a@example.com", owner_id=ADMIN, is_admin=True)
        with patch.object(settings, "feature_auth_enabled", True), self._as(admin):
            too_long = self._get(window_hours=observability_service.MAX_WINDOW_HOURS + 1)
            zero = self._get(window_hours=0)
        self.assertEqual(too_long.status_code, 422)
        self.assertEqual(zero.status_code, 422)

    def test_the_route_is_registered_before_the_catch_all_mount(self):
        """`app.mount("/", chat_mcp_app)` matches every path beneath it, so a
        route registered after it is unreachable and returns the MCP app's
        plain `Not Found`."""
        paths = [getattr(route, "path", None) for route in main.app.router.routes]
        self.assertIn("/admin/observability", paths)
        mounts = [i for i, path in enumerate(paths) if path == "/"]
        if mounts:
            self.assertLess(paths.index("/admin/observability"), mounts[-1])


if __name__ == "__main__":
    unittest.main()
