import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

os.environ["DEBUG"] = "false"

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


if __name__ == "__main__":
    unittest.main()
