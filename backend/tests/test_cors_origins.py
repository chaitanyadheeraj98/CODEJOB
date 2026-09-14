"""CORS must not hand the session cookie to any origin that asks.

`allow_origins=["*"]` with `allow_credentials=True` looks permissive and
harmless, and was harmless while there was nothing to steal. Once the session
moved into a cookie it became a data-theft hole: Starlette replaces the
wildcard with the *requesting* origin whenever a request carries a cookie, so
any page on the internet could call this API in the signed-in user's browser
and read the response.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.config import Settings, settings


class CorsOriginResolutionTests(unittest.TestCase):
    def _settings(self, **kwargs) -> Settings:
        values = {"cors_allowed_origins": "", "dashboard_base_url": "http://localhost:5173"}
        values.update(kwargs)
        return Settings.model_construct(**values)

    def test_default_covers_the_dashboard_and_the_dev_server(self):
        resolved = self._settings().effective_cors_allowed_origins
        self.assertEqual(resolved, ["http://localhost:5173", "http://localhost:5174"])

    def test_an_explicit_list_replaces_the_default(self):
        resolved = self._settings(
            cors_allowed_origins="https://app.example.com, https://staging.example.com"
        ).effective_cors_allowed_origins
        self.assertEqual(resolved, ["https://app.example.com", "https://staging.example.com"])

    def test_a_wildcard_is_dropped_rather_than_honoured(self):
        # Configuring "*" must not silently restore the hole this closes.
        resolved = self._settings(
            cors_allowed_origins="*, https://app.example.com"
        ).effective_cors_allowed_origins
        self.assertNotIn("*", resolved)
        self.assertEqual(resolved, ["https://app.example.com"])

    def test_a_trailing_slash_is_stripped(self):
        # Browsers send the bare origin; Starlette compares strings, so
        # "http://localhost:5173/" would never match anything.
        resolved = self._settings(
            cors_allowed_origins="http://localhost:5173/"
        ).effective_cors_allowed_origins
        self.assertEqual(resolved, ["http://localhost:5173"])

    def test_a_custom_dashboard_url_is_carried_into_the_default(self):
        resolved = self._settings(
            dashboard_base_url="https://app.example.com"
        ).effective_cors_allowed_origins
        self.assertIn("https://app.example.com", resolved)

    def test_the_live_app_is_not_configured_with_a_wildcard(self):
        self.assertNotIn("*", settings.effective_cors_allowed_origins)


class CorsBrowserBehaviourTests(unittest.TestCase):
    """What a browser actually sees, using the same middleware the app builds.

    Built standalone rather than against `app`: the real middleware reads
    settings once at import, so this is the only way to assert behaviour for a
    given configuration without reimporting the module.
    """

    def setUp(self):
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/candidates")
        def candidates():
            return {"total": 3567}

        self.client = TestClient(app)

    def test_the_dashboard_may_read_the_response(self):
        response = self.client.get(
            "/candidates",
            headers={"Origin": "http://localhost:5173", "Cookie": "codejob_session=x"},
        )
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")

    def test_a_hostile_origin_may_not(self):
        # The request still reaches the endpoint - CORS is enforced in the
        # browser, not the server - but with no allow-origin header the
        # browser refuses to give the attacker's script the body.
        response = self.client.get(
            "/candidates",
            headers={"Origin": "https://evil.example.com", "Cookie": "codejob_session=x"},
        )
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_a_wildcard_with_credentials_would_have_leaked_to_any_origin(self):
        """Characterises the bug, so the old configuration cannot come back."""
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/candidates")
        def candidates():
            return {"total": 3567}

        response = TestClient(app).get(
            "/candidates",
            headers={"Origin": "https://evil.example.com", "Cookie": "codejob_session=x"},
        )
        self.assertEqual(
            response.headers["access-control-allow-origin"], "https://evil.example.com"
        )
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")


if __name__ == "__main__":
    unittest.main()
