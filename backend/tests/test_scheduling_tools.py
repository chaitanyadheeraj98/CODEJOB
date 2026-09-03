"""The two scheduling tools.

The claim that matters is that `propose_scheduled_task` writes nothing. It is
asserted by comparing every scheduling table before and after, not by reading
the function.
"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import ScheduledTask, ScheduledTaskItem, ScheduledTaskRun, UserSettings, utc_now
from app.services.scheduling import task_service

OWNER = "owner-under-test"


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patches = [
            patch("app.mcp_server.tools.scheduling.SessionLocal", self.SessionLocal),
            patch("app.config.settings.owner_id", OWNER),
        ]
        for item in self.patches:
            item.start()
        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=OWNER, timezone="America/New_York"))
            db.commit()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def seed_task(self, **overrides) -> int:
        with self.SessionLocal() as db:
            task = task_service.create_task(
                db,
                owner_id=OWNER,
                title=overrides.pop("title", "Morning digest"),
                kind=overrides.pop("kind", "digest"),
                when=overrides.pop("when", "every weekday at 9am"),
                **overrides,
            )
            return task.id

    def counts(self) -> tuple[int, int, int]:
        with self.SessionLocal() as db:
            return (
                db.query(ScheduledTask).count(),
                db.query(ScheduledTaskRun).count(),
                db.query(ScheduledTaskItem).count(),
            )


class ListToolTests(ToolTests):
    def test_it_reports_trigger_next_run_and_permitted_actions(self) -> None:
        from app.mcp_server.tools.scheduling import list_scheduled_tasks

        self.seed_task()

        payload = list_scheduled_tasks()

        self.assertEqual(payload["action"], "render_scheduled_tasks")
        task = payload["tasks"][0]
        self.assertEqual(task["trigger"], "Every weekday at 9:00 AM (America/New_York)")
        self.assertIn("Changes nothing", task["permitted_actions"])
        self.assertIsNotNone(task["next_run_at"])

    def test_it_carries_provenance_and_the_granularity_caveat(self) -> None:
        from app.mcp_server.tools.scheduling import list_scheduled_tasks

        payload = list_scheduled_tasks()

        self.assertEqual(payload["provenance"]["metric"], "Scheduled tasks")
        self.assertTrue(payload["provenance"]["assumptions"])
        # The user should read the granularity caveat, not discover it by
        # being five minutes late.
        self.assertIn("5", payload["granularity_note"])

    def test_an_unknown_status_names_the_valid_ones(self) -> None:
        from app.mcp_server.tools.scheduling import list_scheduled_tasks

        payload = list_scheduled_tasks(status="asleep")

        self.assertIn("statuses", payload)
        self.assertIn("active", payload["statuses"])


class ProposeToolTests(ToolTests):
    def test_it_writes_nothing(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        before = self.counts()

        propose_scheduled_task(
            "create", title="Daily digest", kind="digest", when="every day at 9am"
        )

        self.assertEqual(self.counts(), before)

    def test_the_preview_states_the_systems_interpretation(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        payload = propose_scheduled_task(
            "create", title="Weekday nudge", kind="reminder", when="every weekday at 9am"
        )

        self.assertEqual(payload["status"], "preview")
        # Parsed on the server and rendered back in English, in the user's zone.
        # An echo of what they typed would confirm nothing.
        self.assertEqual(payload["trigger"], "Every weekday at 9:00 AM (America/New_York)")
        self.assertEqual(payload["cron_expression"], "0 9 * * 1-5")
        self.assertEqual(payload["timezone"], "America/New_York")
        self.assertIsNotNone(payload["first_run"])
        self.assertIn("Changes nothing", payload["permitted_actions"])

    def test_an_unparseable_phrase_refuses_and_offers_what_works(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        payload = propose_scheduled_task(
            "create", title="Vague", kind="reminder", when="around lunchtime-ish"
        )

        self.assertEqual(payload["status"], "unparseable")
        self.assertTrue(payload["supported_phrasings"])
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_missing_title_is_reported_as_a_missing_field(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        payload = propose_scheduled_task("create", title="  ", when="in 2 hours")

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["title"])

    def test_an_unknown_operation_names_the_valid_ones(self) -> None:
        from app.mcp_server.tools.scheduling import OPERATIONS, propose_scheduled_task

        payload = propose_scheduled_task("obliterate", title="x")

        self.assertEqual(payload["operations"], list(OPERATIONS))

    def test_pausing_previews_the_existing_task(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        task_id = self.seed_task()

        payload = propose_scheduled_task("pause", task_id=task_id)

        self.assertEqual(payload["operation"], "pause")
        self.assertEqual(payload["title"], "Morning digest")
        self.assertTrue(payload["reversible"])

    def test_deleting_is_flagged_as_not_reversible(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        task_id = self.seed_task()

        payload = propose_scheduled_task("delete", task_id=task_id)

        self.assertFalse(payload["reversible"])

    def test_a_missing_task_is_reported_rather_than_previewed(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        payload = propose_scheduled_task("pause", task_id=999999)

        self.assertEqual(payload["status"], "not_found")

    def test_a_checklist_preview_needs_no_phrase(self) -> None:
        from app.mcp_server.tools.scheduling import propose_scheduled_task

        payload = propose_scheduled_task("create", title="Interview prep", kind="checklist")

        self.assertEqual(payload["schedule_kind"], "none")
        self.assertIn("No schedule", payload["trigger"])


class RegistrationTests(unittest.TestCase):
    def test_the_shipped_tool_count_is_unchanged_with_the_flag_off(self) -> None:
        from app.config import settings
        from app.mcp_server import server

        # v1's and v2's routing measurement is still owed at 35 tools. If v4's
        # pair registered by default, any regression would be unattributable
        # across two unmeasured additions at once.
        self.assertFalse(settings.feature_scheduling_enabled)
        self.assertEqual(len(server.SCHEDULING_TOOLS), 2)

    def test_no_scheduling_tool_executes_anything(self) -> None:
        from app.mcp_server import server

        # v4 registers no tool that approves, sends, or changes a record.
        # Approval is a user's click on a rendered control.
        names = {tool.__name__ for tool in server.SCHEDULING_TOOLS}
        self.assertEqual(names, {"list_scheduled_tasks", "propose_scheduled_task"})


if __name__ == "__main__":
    unittest.main()
