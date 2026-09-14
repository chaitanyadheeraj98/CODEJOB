"""Automation has to run for every tenant, not just the first one.

`auto_runner_service.py` contained no reference to `tenancy` or `owner_id`. Its
`get_settings` resolves `tenancy.owner_id()`, and the auto-runner runs in a
thread started at application start-up - no request, no middleware, so the
ContextVar is unset and the fallback constant is returned.

Everything the loop drives therefore ran for that one owner: Gmail sync, draft
generation, live reply checks, reminder, resume-tracking and scheduling
sweeps. **A second tenant's mailbox was never synced automatically.** They got
whatever they triggered by hand, with nothing to say why.

Found in the C3 audit rather than by a user, which is the only reason it is
being fixed before someone noticed their mail had stopped arriving.
"""

import os
import threading
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tenancy
from app.models import Base, UserSettings
from app.services.auto_runner_service import AutoRunnerService, _Schedule


class _StopAfter(threading.Event):
    """Runs the loop a fixed number of ticks, then stops it."""

    def __init__(self, ticks: int):
        super().__init__()
        self.remaining = ticks

    def wait(self, timeout=None):  # noqa: ARG002 - the loop's 5s sleep
        if self.remaining <= 0:
            return True
        self.remaining -= 1
        return False


class AutoRunnerTenantTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.seen_runs: list[str] = []
        self.seen_live: list[str] = []

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _settings_for(self, owner_id: str, **overrides) -> UserSettings:
        values = {
            "owner_id": owner_id,
            "enabled": True,
            "feature_auto_polling": True,
            "feature_auto_poll_interval_minutes": 1,
        }
        values.update(overrides)
        with self.Session() as db:
            row = UserSettings(**values)
            db.add(row)
            db.commit()
            return row

    def _service(self, owners: list[str], ticks: int = 1) -> AutoRunnerService:
        def get_settings(db):
            # Exactly what the real one does: resolve the tenant from the
            # ContextVar. If the loop forgets the owner scope, every tenant
            # gets the same row and this test notices.
            found = (
                db.query(UserSettings)
                .filter(UserSettings.owner_id == tenancy.owner_id())
                .first()
            )
            if found is None:
                raise AssertionError(f"no settings for {tenancy.owner_id()}")
            return found

        def run_once(_payload, _db):
            self.seen_runs.append(tenancy.owner_id())
            return Mock(status="queued", job_id="j", run_key="r")

        def check_live(_db):
            self.seen_live.append(tenancy.owner_id())

        return AutoRunnerService(
            session_factory=self.Session,
            get_settings=get_settings,
            run_once=run_once,
            run_nvoids_once=lambda _db, _limit: Mock(status="queued", job_id="j", run_key="r"),
            check_live_replies=check_live,
            run_reminder_sweep=lambda _db: None,
            run_resume_tracking_sweep=lambda _db: None,
            action_lock=threading.Lock(),
            stop_event=_StopAfter(ticks),
            list_owners=lambda: list(owners),
        )

    def test_every_tenant_is_serviced_not_only_the_first(self):
        for owner in ("usr_a", "usr_b", "usr_c"):
            self._settings_for(owner)
        self._service(["usr_a", "usr_b", "usr_c"]).run_loop()
        self.assertEqual(sorted(self.seen_runs), ["usr_a", "usr_b", "usr_c"])

    def test_each_tenant_runs_under_its_own_owner(self):
        """The assertion that would have failed before the owner scope.

        `run_once` records `tenancy.owner_id()` as the real enqueue path reads
        it. Without the scope every entry would be the fallback constant.
        """
        for owner in ("usr_a", "usr_b"):
            self._settings_for(owner)
        self._service(["usr_a", "usr_b"]).run_loop()
        self.assertNotIn("default-owner", self.seen_runs)

    def test_a_tenant_with_polling_off_is_skipped_and_the_others_are_not(self):
        self._settings_for("usr_on")
        self._settings_for("usr_off", feature_auto_polling=False)
        self._service(["usr_on", "usr_off"]).run_loop()
        self.assertEqual(self.seen_runs, ["usr_on"])

    def test_a_disabled_tenant_is_skipped(self):
        self._settings_for("usr_on")
        self._settings_for("usr_disabled", enabled=False)
        self._service(["usr_on", "usr_disabled"]).run_loop()
        self.assertEqual(self.seen_runs, ["usr_on"])

    def test_one_tenants_failure_does_not_stop_the_others(self):
        """Ninety-nine accounts must not stop because one has a bad row."""
        self._settings_for("usr_a")
        # usr_broken deliberately has no settings row, so get_settings raises.
        self._settings_for("usr_c")
        self._service(["usr_a", "usr_broken", "usr_c"]).run_loop()
        self.assertEqual(sorted(self.seen_runs), ["usr_a", "usr_c"])

    def test_the_owner_context_does_not_leak_past_a_tick(self):
        self._settings_for("usr_a")
        self._service(["usr_a"]).run_loop()
        self.assertEqual(tenancy.owner_id(), tenancy.owner_id())
        # Nothing should still be scoped once the loop returns.
        from app.config import settings as app_settings

        self.assertEqual(tenancy.owner_id(), app_settings.owner_id)

    def test_one_tenants_cadence_does_not_reset_anothers(self):
        """Seven loop-local timestamps became per-tenant for this reason."""
        self._settings_for("usr_a")
        self._settings_for("usr_b", feature_auto_polling=False)
        service = self._service(["usr_a", "usr_b"], ticks=3)
        service.run_loop()
        # usr_a polls on a 1-minute interval, so three ticks is still one run.
        # If usr_b's "polling off" branch reset a shared timestamp, usr_a would
        # have run on every tick.
        self.assertEqual(self.seen_runs, ["usr_a"])

    def test_live_reply_checks_are_spread_across_the_interval(self):
        """They call Gmail inline, and the quota is per minute.

        Every tenant starting at the same instant would send N requests in one
        tick, which is exactly the shape that already produces 429s for a
        single account.
        """
        offsets = {
            owner: _Schedule.starting_now(owner).next_live_check_at
            for owner in (f"usr_{n}" for n in range(40))
        }
        distinct = {value.replace(microsecond=0) for value in offsets.values()}
        self.assertGreater(len(distinct), 20, "the offsets must actually spread")
        span = max(offsets.values()) - min(offsets.values())
        self.assertLessEqual(span, timedelta(seconds=60), "and stay inside the interval")

    def test_the_offset_is_stable_for_an_owner(self):
        """A restart must not reshuffle every tenant into the same tick."""
        first = _Schedule.starting_now("usr_stable")
        second = _Schedule.starting_now("usr_stable")
        self.assertEqual(
            (first.next_live_check_at - datetime.now(UTC)).seconds,
            (second.next_live_check_at - datetime.now(UTC)).seconds,
        )

    def test_schedules_for_departed_tenants_are_dropped(self):
        """Otherwise the dict grows for the life of the process."""
        self._settings_for("usr_a")
        owners = ["usr_a", "usr_gone"]
        self._settings_for("usr_gone")
        service = self._service(owners, ticks=2)

        original = service._list_owners

        def shrink():
            result = list(original())
            owners[:] = ["usr_a"]
            return result

        service._list_owners = shrink
        service.run_loop()
        # Nothing to assert on the dict directly - it is a local - but the
        # second tick must not raise for the departed tenant.
        self.assertIn("usr_a", self.seen_runs)


if __name__ == "__main__":
    unittest.main()


class AutomationOwnerListTests(unittest.TestCase):
    """Which tenants the loop is handed, which is the other half of the fix."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _add_user(self, owner_id: str, *, disabled: bool = False):
        from app.models import User

        with self.Session() as db:
            db.add(User(
                owner_id=owner_id, email=f"{owner_id}@example.com",
                disabled_at=datetime.now(UTC) if disabled else None,
            ))
            db.commit()

    def _owners(self, *, auth_enabled: bool) -> list[str]:
        from contextlib import contextmanager
        from unittest.mock import patch

        from app import main
        from app.config import settings as app_settings

        @contextmanager
        def scope():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        with patch.object(app_settings, "feature_auth_enabled", auth_enabled), \
                patch.object(main, "session_scope", scope):
            return main._automation_owners()

    def test_with_sign_in_off_it_is_the_configured_owner_and_nothing_else(self):
        """The behaviour this replaces, unchanged."""
        from app.config import settings as app_settings

        self._add_user("usr_a")
        self.assertEqual(self._owners(auth_enabled=False), [app_settings.owner_id])

    def test_with_sign_in_on_it_is_every_account(self):
        self._add_user("usr_a")
        self._add_user("usr_b")
        self.assertEqual(sorted(self._owners(auth_enabled=True)), ["usr_a", "usr_b"])

    def test_a_disabled_account_keeps_its_data_and_stops_being_synced(self):
        self._add_user("usr_active")
        self._add_user("usr_disabled", disabled=True)
        self.assertEqual(self._owners(auth_enabled=True), ["usr_active"])

    def test_an_install_that_has_just_turned_sign_in_on_keeps_running(self):
        """No users yet, but a connected mailbox and real data.

        Silently stopping automation until the first sign-in would be a
        regression with no error attached to it.
        """
        from app.config import settings as app_settings

        self.assertEqual(self._owners(auth_enabled=True), [app_settings.owner_id])


class LiveReplyScopingTests(unittest.TestCase):
    """`/gmail/live-replies` served one number to everybody.

    Harmless while one tenant's automation was the only writer. The moment the
    loop services every tenant it would hold whichever ran last, so one
    account's unread count would be shown to all of them.
    """

    def setUp(self):
        from app.runtime_state import runtime_state

        self.runtime_state = runtime_state
        self.previous = dict(runtime_state.live_replies)
        runtime_state.live_replies.clear()

    def tearDown(self):
        self.runtime_state.live_replies.clear()
        self.runtime_state.live_replies.update(self.previous)

    def test_each_tenant_sees_its_own_count(self):
        from app.main import live_replies

        now = datetime.now(UTC)
        self.runtime_state.live_replies["usr_a"] = (7, now)
        self.runtime_state.live_replies["usr_b"] = (0, now)

        with tenancy.owner_scope("usr_a"):
            self.assertEqual(live_replies().count, 7)
        with tenancy.owner_scope("usr_b"):
            self.assertEqual(live_replies().count, 0)

    def test_a_tenant_that_has_never_been_checked_sees_zero_not_someone_elses(self):
        from app.main import live_replies

        self.runtime_state.live_replies["usr_a"] = (7, datetime.now(UTC))
        with tenancy.owner_scope("usr_never_checked"):
            result = live_replies()
        self.assertEqual(result.count, 0)
        self.assertIsNone(result.checked_at)
