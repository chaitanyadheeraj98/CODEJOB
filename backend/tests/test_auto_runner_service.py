import unittest
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.auto_runner_service import AutoRunnerService


class _OneIterationStop:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _seconds: float) -> bool:
        self.calls += 1
        return self.calls > 1


class AutoRunnerApplicationReminderTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides: object) -> SimpleNamespace:
        values = {
            "enabled": True,
            "feature_auto_polling": False,
            "feature_auto_poll_interval_minutes": 10,
            "feature_nvoids_enabled": False,
            "feature_nvoids_auto_sync": False,
            "feature_nvoids_poll_interval_minutes": 30,
            "nvoids_batch_limit": 10,
            "feature_applications_enabled": True,
            "feature_application_automation_enabled": True,
            "feature_reminder_sweep_interval_minutes": 1,
            "feature_resume_tracking_enabled": False,
            "feature_resume_tracking_sweep_interval_minutes": 240,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _run(self, settings: SimpleNamespace, telegram_retention: Mock | None = None) -> Mock:
        db = Mock()
        opened = []
        reminder = Mock()

        def session_factory():
            opened.append(db)
            return db

        service = AutoRunnerService(
            session_factory=session_factory,
            get_settings=lambda _db: settings,
            run_once=Mock(),
            run_nvoids_once=Mock(),
            check_live_replies=Mock(),
            run_reminder_sweep=reminder,
            run_resume_tracking_sweep=Mock(),
            run_telegram_retention_sweep=telegram_retention or Mock(),
            action_lock=Lock(),
            stop_event=_OneIterationStop(),
        )
        service.run_loop()
        # Opened equals closed, rather than "exactly one". The property this
        # guards is that the loop leaks no session; the count was incidental,
        # and G3's hourly purge sweep legitimately opens a second one because
        # it is global work rather than part of any owner's tick.
        self.assertEqual(db.close.call_count, len(opened))
        return reminder

    def test_reminder_sweep_runs_on_its_own_clamped_interval(self) -> None:
        settings = self._settings()
        self.assertEqual(AutoRunnerService.reminder_sweep_interval_minutes(settings), 30)
        self._run(settings).assert_called_once()

    def test_reminder_sweep_requires_both_application_flags(self) -> None:
        self._run(self._settings(feature_applications_enabled=False)).assert_not_called()
        self._run(self._settings(feature_application_automation_enabled=False)).assert_not_called()

    def test_telegram_retention_sweep_runs_when_the_master_switch_is_on(self) -> None:
        retention = Mock()
        with patch("app.services.auto_runner_service.settings.feature_telegram_chat_enabled", True):
            self._run(self._settings(), retention)
        retention.assert_called_once()


if __name__ == "__main__":
    unittest.main()
