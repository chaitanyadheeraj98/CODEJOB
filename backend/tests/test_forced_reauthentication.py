"""§13's borrowed laptop, and why `prompt=consent` was not enough.

Deleting an account requires re-authentication. The sign-in flow already sent
`prompt=consent`, which guarantees a **click** - and an unlocked laptop
supplies clicks. `prompt=login` is what makes Google ask for credentials again.

Only the deletion flow asks for it. Ordinary sign-in must not, or everyone who
signed in a minute ago gets asked for their password again.
"""

import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ["DEBUG"] = "false"

from app.config import settings
from app.services import auth_service


def _prompt(url: str) -> list[str]:
    raw = parse_qs(urlparse(url).query).get("prompt", [""])[0]
    return raw.split()


class ForcedReauthenticationTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(settings, "google_client_id", "client-id"),
            patch.object(settings, "google_client_secret", "client-secret"),
            patch.object(settings, "google_auth_redirect_uri", "https://app.example/callback"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def test_ordinary_sign_in_does_not_force_a_password(self):
        """Otherwise every sign-in re-prompts someone who just signed in."""
        prompt = _prompt(auth_service.begin_login().authorization_url)
        self.assertIn("consent", prompt)
        self.assertNotIn("login", prompt)

    def test_forcing_reauthentication_asks_google_for_credentials(self):
        prompt = _prompt(auth_service.begin_login(force_reauth=True).authorization_url)
        self.assertIn("login", prompt)

    def test_it_still_asks_for_consent_so_the_refresh_token_survives(self):
        """`prompt` replaces rather than adds. Dropping `consent` here would
        cost the refresh token this flow also exists to obtain."""
        prompt = _prompt(auth_service.begin_login(force_reauth=True).authorization_url)
        self.assertIn("consent", prompt)

    def test_offline_access_is_unchanged_either_way(self):
        for force in (False, True):
            url = auth_service.begin_login(force_reauth=force).authorization_url
            self.assertEqual(parse_qs(urlparse(url).query).get("access_type"), ["offline"])


if __name__ == "__main__":
    unittest.main()
