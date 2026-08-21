import unittest
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

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
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _run(self, settings: SimpleNamespace) -> Mock:
        db = Mock()
        reminder = Mock()
        service = AutoRunnerService(
            session_factory=lambda: db,
            get_settings=lambda _db: settings,
            run_once=Mock(),
            run_nvoids_once=Mock(),
            check_live_replies=Mock(),
            run_reminder_sweep=reminder,
            action_lock=Lock(),
            stop_event=_OneIterationStop(),
        )
        service.run_loop()
        db.close.assert_called_once()
        return reminder

    def test_reminder_sweep_runs_on_its_own_clamped_interval(self) -> None:
        settings = self._settings()
        self.assertEqual(AutoRunnerService.reminder_sweep_interval_minutes(settings), 30)
        self._run(settings).assert_called_once()

    def test_reminder_sweep_requires_both_application_flags(self) -> None:
        self._run(self._settings(feature_applications_enabled=False)).assert_not_called()
        self._run(self._settings(feature_application_automation_enabled=False)).assert_not_called()


if __name__ == "__main__":
    unittest.main()
