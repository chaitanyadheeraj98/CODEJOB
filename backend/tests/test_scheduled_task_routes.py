"""The nine v4 routes.

Three things they must get right. Every one 404s when the feature is off, so an
unbuilt-out surface is not reachable on a normal deployment. Every non-deleted
task appears in the management list, whatever its kind - the completeness
invariant. And the approval endpoint refuses an expired run *twice*: once on the
stored outcome, and once on a fresh clock comparison, because this codebase has
shipped an expires_at that nothing compares.
"""

import json
import os
import unittest
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    SCHEDULED_TASK_KINDS,
    Application,
    ScheduledTask,
    ScheduledTaskItem,
    ScheduledTaskRun,
    utc_now,
)

OWNER = "owner-under-test"


class SchedulingRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.previous_flag = main.settings.feature_scheduling_enabled
        self.previous_owner = main.settings.owner_id
        main.settings.feature_scheduling_enabled = True
        main.settings.owner_id = OWNER

    def tearDown(self) -> None:
        main.settings.feature_scheduling_enabled = self.previous_flag
        main.settings.owner_id = self.previous_owner
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create(self, **overrides) -> dict:
        body = {"title": "Morning reminder", "kind": "reminder", "when": "every weekday at 9am"}
        body.update(overrides)
        response = self.client.post("/scheduled-tasks", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def application(self, db, application_id: int = 1) -> Application:
        now = utc_now() - timedelta(days=40)
        row = Application(
            id=application_id,
            owner_id=OWNER,
            resume_asset_id=application_id,
            resume_version_snapshot=1,
            resume_file_name_snapshot="r.pdf",
            resume_sha256_snapshot="a" * 64,
            recruiter_opportunity_id=application_id,
            recruiter_contact_id=1,
            job_title_snapshot="Java Developer",
            end_client_snapshot="Bank X",
            status="contacted",
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return row


class FeatureGateTests(SchedulingRouteTests):
    def test_every_route_is_not_found_when_the_feature_is_off(self) -> None:
        main.settings.feature_scheduling_enabled = False

        for method, path, body in (
            ("get", "/scheduled-tasks", None),
            ("get", "/scheduled-tasks/pending-work", None),
            ("post", "/scheduled-tasks", {"title": "x", "kind": "reminder", "when": "in 2 hours"}),
            ("patch", "/scheduled-tasks/1", {"operation": "pause"}),
            ("delete", "/scheduled-tasks/1", None),
            ("get", "/scheduled-tasks/1/runs", None),
            ("post", "/scheduled-tasks/runs/1/approve", {}),
            ("post", "/scheduled-tasks/runs/1/discard", None),
            ("patch", "/scheduled-tasks/1/items/1", {"done": True}),
        ):
            with self.subTest(path=path):
                call = getattr(self.client, method)
                response = call(path, json=body) if body is not None else call(path)
                self.assertEqual(response.status_code, 404, f"{method} {path}")


class CreationTests(SchedulingRouteTests):
    def test_a_reminder_is_created_with_its_trigger_and_next_run(self) -> None:
        payload = self.create()

        self.assertEqual(payload["kind"], "reminder")
        self.assertEqual(payload["trigger"], "Every weekday at 9:00 AM (UTC)")
        self.assertIsNotNone(payload["next_run_at"])
        self.assertIn("Changes nothing", payload["permitted_actions"])

    def test_an_unparseable_schedule_is_refused_and_creates_nothing(self) -> None:
        response = self.client.post(
            "/scheduled-tasks",
            json={"title": "Vague", "kind": "reminder", "when": "whenever you feel like it"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Phrasings that work", response.json()["detail"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ScheduledTask).count(), 0)

    def test_a_monitor_without_a_condition_is_refused(self) -> None:
        response = self.client.post(
            "/scheduled-tasks", json={"title": "Watch", "kind": "monitor"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("predicate", response.json()["detail"])

    def test_an_unknown_kind_is_refused(self) -> None:
        response = self.client.post(
            "/scheduled-tasks", json={"title": "x", "kind": "telepathy", "when": "in 2 hours"}
        )

        self.assertEqual(response.status_code, 400)

    def test_a_checklist_needs_no_schedule_and_keeps_its_items(self) -> None:
        payload = self.create(
            title="Interview prep", kind="checklist", when="", items=["Read the JD", "Prep STAR answers"]
        )

        self.assertIsNone(payload["next_run_at"])
        runs = self.client.get(f"/scheduled-tasks/{payload['id']}/runs").json()
        self.assertEqual([item["text"] for item in runs["items"]], ["Read the JD", "Prep STAR answers"])


class CompletenessTests(SchedulingRouteTests):
    # temp157 §8.1: a scheduled task the user cannot see is one they cannot stop.
    def test_every_kind_appears_in_the_management_list(self) -> None:
        self.create(title="Reminder", kind="reminder", when="in 2 hours")
        self.create(title="Digest", kind="digest", when="every day at 9am")
        self.create(title="Checklist", kind="checklist", when="")
        self.create(
            title="Monitor",
            kind="monitor",
            condition={"predicate": "status_unchanged_for_days", "subject_type": "application"},
        )
        self.create(
            title="Workflow",
            kind="workflow",
            when="every day at 7am",
            condition={"predicate": "status_unchanged_for_days", "subject_type": "application"},
        )

        listed = self.client.get("/scheduled-tasks").json()["tasks"]

        self.assertEqual(len(listed), 5)
        self.assertEqual({task["kind"] for task in listed}, set(SCHEDULED_TASK_KINDS))
        for task in listed:
            self.assertTrue(task["trigger"])
            self.assertTrue(task["permitted_actions"])

    def test_a_deleted_task_stops_running_but_keeps_its_history(self) -> None:
        created = self.create()
        task_id = created["id"]

        deleted = self.client.delete(f"/scheduled-tasks/{task_id}")
        listed = self.client.get("/scheduled-tasks").json()["tasks"]

        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(listed, [])
        with self.SessionLocal() as db:
            self.assertIsNone(db.get(ScheduledTask, task_id).next_run_at)


class LifecycleTests(SchedulingRouteTests):
    def test_pausing_clears_the_next_run_and_resuming_recomputes_it(self) -> None:
        created = self.create()
        task_id = created["id"]

        paused = self.client.patch(f"/scheduled-tasks/{task_id}", json={"operation": "pause"}).json()
        resumed = self.client.patch(f"/scheduled-tasks/{task_id}", json={"operation": "resume"}).json()

        self.assertEqual(paused["status"], "paused")
        # Cleared, not kept: a stale next_run_at would fire immediately for
        # every tick missed while paused.
        self.assertIsNone(paused["next_run_at"])
        self.assertEqual(resumed["status"], "active")
        self.assertIsNotNone(resumed["next_run_at"])
        self.assertGreater(datetime.fromisoformat(resumed["next_run_at"]), datetime.now(UTC))

    def test_editing_the_schedule_takes_effect_before_the_next_run(self) -> None:
        created = self.create()

        edited = self.client.patch(
            f"/scheduled-tasks/{created['id']}",
            json={"operation": "edit", "when": "every Monday at 8am", "title": "Weekly nudge"},
        ).json()

        self.assertEqual(edited["title"], "Weekly nudge")
        self.assertEqual(edited["trigger"], "Every Monday at 8:00 AM (UTC)")

    def test_a_suspended_task_shows_its_error(self) -> None:
        created = self.create()
        with self.SessionLocal() as db:
            task = db.get(ScheduledTask, created["id"])
            task.status = "suspended"
            task.last_error = "Redis unreachable"
            db.commit()

        listed = self.client.get("/scheduled-tasks?status=suspended").json()["tasks"]

        self.assertEqual(listed[0]["status"], "suspended")
        self.assertEqual(listed[0]["last_error"], "Redis unreachable")

    def test_another_owners_task_and_a_missing_one_answer_identically(self) -> None:
        created = self.create()
        with self.SessionLocal() as db:
            db.query(ScheduledTask).update({ScheduledTask.owner_id: "someone-else"})
            db.commit()

        scoped = self.client.patch(f"/scheduled-tasks/{created['id']}", json={"operation": "pause"})
        missing = self.client.patch("/scheduled-tasks/999999", json={"operation": "pause"})

        self.assertEqual(scoped.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(scoped.json()["detail"], missing.json()["detail"])


class ApprovalTests(SchedulingRouteTests):
    def prepared_run(self, *, expires_in_hours: float = 24.0) -> tuple[int, str, int]:
        with self.SessionLocal() as db:
            application = self.application(db)
            task = ScheduledTask(
                owner_id=OWNER,
                title="Chase stale",
                kind="workflow",
                schedule_kind="recurring",
                cron_expression="0 7 * * *",
                timezone="UTC",
                status="active",
                action_json="{}",
            )
            db.add(task)
            db.flush()
            item_id = "11111111-1111-1111-1111-111111111111"
            run = ScheduledTaskRun(
                owner_id=OWNER,
                task_id=task.id,
                started_at=utc_now(),
                outcome="pending",
                item_count=1,
                expires_at=utc_now() + timedelta(hours=expires_in_hours),
                prepared_json=json.dumps(
                    {
                        "kind": "workflow",
                        "items": [
                            {
                                "item_id": item_id,
                                "action": "propose_record_update",
                                "record_kind": "application",
                                "record_id": application.id,
                                "summary": "Set next action",
                                "payload": {
                                    "next_action_type": "Follow up with recruiter",
                                    "next_action_at": utc_now().isoformat(),
                                },
                                "editable_fields": ["next_action_type"],
                            }
                        ],
                    }
                ),
            )
            db.add(run)
            db.commit()
            return run.id, item_id, application.id

    def test_approving_executes_through_the_v2_path(self) -> None:
        run_id, item_id, application_id = self.prepared_run()

        response = self.client.post(f"/scheduled-tasks/runs/{run_id}/approve", json={})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["approved"], 1)
        self.assertEqual(response.json()["outcome"], "approved")
        with self.SessionLocal() as db:
            application = db.get(Application, application_id)
            self.assertEqual(application.next_action_type, "Follow up with recruiter")
            self.assertIsNotNone(application.next_action_at)

    def test_editing_before_approving_sends_the_edited_payload(self) -> None:
        run_id, item_id, application_id = self.prepared_run()

        self.client.post(
            f"/scheduled-tasks/runs/{run_id}/approve",
            json={"item_ids": [item_id], "edits": {item_id: {"next_action_type": "Call instead"}}},
        )

        with self.SessionLocal() as db:
            self.assertEqual(db.get(Application, application_id).next_action_type, "Call instead")

    def test_an_item_id_outside_the_run_is_refused(self) -> None:
        run_id, _, _ = self.prepared_run()

        response = self.client.post(
            f"/scheduled-tasks/runs/{run_id}/approve", json={"item_ids": ["not-in-this-run"]}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not part of this run", response.json()["detail"])

    # The check that matters. F7: two shipped expires_at columns are never
    # compared, so the endpoint checks the clock itself rather than trusting a
    # sweep to have run.
    def test_approving_an_expired_run_is_refused_before_the_sweep_marks_it(self) -> None:
        run_id, _, application_id = self.prepared_run(expires_in_hours=-1)

        with self.SessionLocal() as db:
            self.assertEqual(db.get(ScheduledTaskRun, run_id).outcome, "pending")

        response = self.client.post(f"/scheduled-tasks/runs/{run_id}/approve", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["detail"])
        with self.SessionLocal() as db:
            self.assertIsNone(db.get(Application, application_id).next_action_at)

    def test_approving_an_expired_run_is_refused_after_the_sweep_marks_it(self) -> None:
        run_id, _, _ = self.prepared_run(expires_in_hours=-1)
        with self.SessionLocal() as db:
            run = db.get(ScheduledTaskRun, run_id)
            run.outcome = "expired"
            run.expiry_reason = "not_reviewed"
            db.commit()

        response = self.client.post(f"/scheduled-tasks/runs/{run_id}/approve", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("already expired", response.json()["detail"])

    def test_discard_leaves_no_side_effects(self) -> None:
        run_id, _, application_id = self.prepared_run()

        response = self.client.post(f"/scheduled-tasks/runs/{run_id}/discard")

        self.assertEqual(response.json()["outcome"], "discarded")
        with self.SessionLocal() as db:
            self.assertIsNone(db.get(Application, application_id).next_action_at)

    def test_an_expired_run_shows_its_reason_in_history(self) -> None:
        run_id, _, _ = self.prepared_run(expires_in_hours=-1)
        with self.SessionLocal() as db:
            run = db.get(ScheduledTaskRun, run_id)
            run.outcome = "expired"
            run.expiry_reason = "not_reviewed"
            task_id = run.task_id
            db.commit()

        history = self.client.get(f"/scheduled-tasks/{task_id}/runs").json()["runs"]

        self.assertEqual(history[0]["outcome"], "expired")
        self.assertEqual(history[0]["expiry_reason"], "Not reviewed before expiration")


class PendingWorkTests(SchedulingRouteTests):
    def test_both_sources_appear_in_one_list(self) -> None:
        from app.models import ApplicationSuggestion

        with self.SessionLocal() as db:
            application = self.application(db)
            task = ScheduledTask(
                owner_id=OWNER, title="Chase", kind="workflow", schedule_kind="recurring",
                cron_expression="0 7 * * *", timezone="UTC", status="active", action_json="{}",
            )
            db.add(task)
            db.flush()
            db.add(
                ScheduledTaskRun(
                    owner_id=OWNER, task_id=task.id, started_at=utc_now(),
                    outcome="pending", item_count=2, prepared_json="{}",
                )
            )
            db.add(
                ApplicationSuggestion(
                    owner_id=OWNER, application_id=application.id, suggestion_type="next_action",
                    status="pending", reason="No reply in 3 business days", created_at=utc_now(),
                )
            )
            db.commit()

        items = self.client.get("/scheduled-tasks/pending-work").json()["items"]

        self.assertEqual(len(items), 2)
        self.assertEqual(
            {item["source"] for item in items}, {"scheduled_run", "application_suggestion"}
        )
        # A key into the client's endpoint table, never a URL from the server.
        for item in items:
            self.assertNotIn("/", item["approve_endpoint"])


class ChecklistItemTests(SchedulingRouteTests):
    def test_an_item_toggles_and_persists(self) -> None:
        created = self.create(title="Prep", kind="checklist", when="", items=["Read the JD"])
        with self.SessionLocal() as db:
            item_id = db.query(ScheduledTaskItem).one().id

        toggled = self.client.patch(
            f"/scheduled-tasks/{created['id']}/items/{item_id}", json={"done": True}
        ).json()

        self.assertTrue(toggled["done"])
        self.assertIsNotNone(toggled["done_at"])

    def test_an_unknown_item_is_not_found(self) -> None:
        created = self.create(title="Prep", kind="checklist", when="")

        response = self.client.patch(
            f"/scheduled-tasks/{created['id']}/items/999", json={"done": True}
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
