"""Suite-wide defaults that must not depend on the developer's `backend/.env`.

`app.config.Settings` reads `.env`, so anything switched on there leaks into
every test. That stayed invisible until sign-in shipped: turning
`FEATURE_AUTH_ENABLED=true` on a working install made **656 tests fail at
once**, because the middleware correctly began refusing the unauthenticated
requests that almost every test makes.

The tests were not wrong and neither was the middleware - the suite was simply
inheriting a runtime switch. Pinning it here makes the result the same on a
machine with the flag on, a machine with it off, and CI.

Tests that need sign-in on turn it on themselves; this fixture runs first, so
their `setUp` still wins.
"""

import pytest

from app.config import settings

# Runtime switches the suite must not inherit from .env. Add to this list when
# a new feature flag changes request handling rather than only adding routes.
_PINNED_OFF = ("feature_auth_enabled",)


@pytest.fixture(autouse=True)
def _pin_runtime_flags():
    previous = {name: getattr(settings, name) for name in _PINNED_OFF}
    for name in _PINNED_OFF:
        setattr(settings, name, False)
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)
