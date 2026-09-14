"""The Gmail consent URL must point at the listener that is waiting for it.

The reported failure: Connect Gmail opened a consent screen, the account was
chosen, and the browser came back to `/?login_error=state_mismatch` with Gmail
still unconnected. The cause was one line - the Gmail flow prepared its
authorization URL with `settings.google_redirect_uri`, which is the *app login*
callback. Google delivered the Gmail code there, the login handler rejected a
state it had never issued, and the local server on the loopback port waited for
a code that had already been spent.

`run_local_server` overwrites `redirect_uri` with the loopback when it runs, so
the mismatch was invisible in the flow object and visible only in the URL the
user actually opened.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from app import gmail_client
from app.config import settings


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")
    monkeypatch.setattr(settings, "google_redirect_uri", "http://localhost:8000/auth/google/callback")
    monkeypatch.setattr(settings, "google_login_hint", "")
    yield


def _redirect_uri(url: str) -> str:
    return parse_qs(urlparse(url).query)["redirect_uri"][0]


def test_the_prepared_url_points_at_the_loopback_listener(configured) -> None:
    flow, auth_url, _state, _kwargs = gmail_client._prepare_oauth_flow()
    loopback = gmail_client.oauth_loopback_redirect_uri()

    assert _redirect_uri(auth_url) == loopback
    # The flow agrees with the URL, so `run_local_server` regenerating it
    # cannot change where Google sends the code.
    assert flow.redirect_uri == loopback


def test_the_login_callback_never_appears_in_the_gmail_consent_url(configured) -> None:
    # The regression guard. This exact value is what sent the Gmail code to the
    # login handler and produced `?login_error=state_mismatch`.
    _flow, auth_url, _state, _kwargs = gmail_client._prepare_oauth_flow()

    assert settings.google_redirect_uri not in auth_url


def test_the_loopback_uri_matches_the_port_the_server_binds(configured) -> None:
    # One constant, two users: the URL builder and `run_local_server`. Drift
    # between them is the whole bug, so it is asserted rather than trusted.
    assert gmail_client.oauth_loopback_redirect_uri() == (
        f"http://{gmail_client.OAUTH_LOOPBACK_HOST}:{gmail_client.OAUTH_LOOPBACK_PORT}/"
    )


def test_app_login_keeps_its_own_redirect_uri(configured) -> None:
    # Gmail connect moving off `google_redirect_uri` must not move login with
    # it: that callback is still where a sign-in code belongs.
    from app.services import auth_service

    assert auth_service.begin_login().authorization_url.count("redirect_uri") == 1
    assert _redirect_uri(auth_service.begin_login().authorization_url) == settings.google_redirect_uri
