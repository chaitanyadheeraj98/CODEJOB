import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

os.environ["DEBUG"] = "false"

from app.config import settings
from app.main import lifespan


class StartupLifespanTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.main.StartupService")
    async def test_lifespan_uses_one_service_for_startup_and_shutdown(self, startup_service_type) -> None:
        service = startup_service_type.return_value

        async with lifespan(FastAPI()):
            service.startup.assert_called_once_with()
            service.shutdown.assert_not_called()

        startup_service_type.assert_called_once()
        service.shutdown.assert_called_once_with()

    @patch("app.main.StartupService")
    async def test_shutdown_still_runs_when_the_lifespan_is_cancelled(self, startup_service_type) -> None:
        """How every real shutdown arrives.

        Starlette throws the cancellation *at* the `yield`, so statements
        written after it never ran on SIGTERM. Nothing noticed for a long time
        because the process was dying anyway - until the leader lease stopped
        being handed back, and failover started waiting out the full TTL on
        every deploy.
        """
        service = startup_service_type.return_value
        # The chat MCP session manager may only be run once per process and the
        # test above already used it; this test is about the teardown path, not
        # about chat.
        with patch.object(settings, "feature_chat_enabled", False):
            context = lifespan(FastAPI())
            await context.__aenter__()
            service.shutdown.assert_not_called()

            # Exactly how Starlette ends a lifespan: the exception is thrown
            # at the `yield`, not raised after it. Whether it propagates is not
            # the point - whether teardown ran is.
            await context.__aexit__(GeneratorExit, GeneratorExit(), None)

        service.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
