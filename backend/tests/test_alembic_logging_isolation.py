"""Running a migration must not switch the application's logging off.

`alembic/env.py` calls `logging.config.fileConfig`, whose
`disable_existing_loggers` defaults to **True** - it sets `.disabled = True` on
every logger already configured, which is the entire `app.*` tree.

That is invisible on boot, where `alembic upgrade head` is its own process. In
the test suite, where migrations run in-process, it means every application
logger is dead from the first migration test onwards.

The failure that matters is not the noisy one. A test asserting a log
*contains* something fails and gets investigated. A test asserting a secret is
*absent* from the log passes - because there is no log at all. The suite's
credential guards are all of the latter kind.

Found by a C1 test that asserted a Redis URL never reaches a log line: it
passed alone and failed in the full suite, having run after the migration
tests.

Nothing here calls `fileConfig` with the damaging default. Doing so would
disable the loggers for the rest of the run, and this file sorts near the top
of the suite - which is exactly how the first version of it broke three
unrelated tests downstream.
"""

import inspect
import logging
import logging.config
import os
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

BACKEND = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND / "alembic.ini"


class AlembicLoggingIsolationTests(unittest.TestCase):
    def setUp(self):
        self.probe = logging.getLogger("app.test.alembic_logging_probe")
        self.probe.disabled = False
        # fileConfig is global. Snapshot every logger's disabled flag so this
        # file cannot leave the rest of the suite worse than it found it.
        self._previous = {
            name: obj.disabled
            for name, obj in logging.Logger.manager.loggerDict.items()
            if isinstance(obj, logging.Logger)
        }

    def tearDown(self):
        for name, was_disabled in self._previous.items():
            existing = logging.Logger.manager.loggerDict.get(name)
            if isinstance(existing, logging.Logger):
                existing.disabled = was_disabled
        self.probe.disabled = False

    def test_application_loggers_survive_alembics_logging_setup(self):
        """Exactly the call env.py makes."""
        self.assertFalse(self.probe.disabled, "precondition")
        logging.config.fileConfig(str(ALEMBIC_INI), disable_existing_loggers=False)
        self.assertFalse(
            self.probe.disabled,
            "alembic's logging config must not disable the application's loggers",
        )

    def test_the_default_is_the_one_that_would_break_it(self):
        """Characterised from the signature, not by triggering it.

        If this default ever changes, the explicit argument in env.py stops
        being load-bearing and the comment there should be revisited.
        """
        default = inspect.signature(logging.config.fileConfig).parameters[
            "disable_existing_loggers"
        ].default
        self.assertTrue(default)

    def test_env_py_passes_the_argument(self):
        # The behavioural test above cannot see env.py, and env.py is only ever
        # executed by Alembic itself.
        source = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
        self.assertIn("disable_existing_loggers=False", source)


if __name__ == "__main__":
    unittest.main()
