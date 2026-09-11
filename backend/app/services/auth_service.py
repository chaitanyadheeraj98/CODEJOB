"""Sign-in: the Google web flow, users, and browser sessions.

Two things distinguish this from the Phase A OAuth code in `gmail_client`, and
both matter.

**A web flow, not a loopback flow.** Phase A uses
`InstalledAppFlow.run_local_server`, which opens a listener inside the
container and blocks until a browser reaches it. That cannot serve two people
at once and has no way to attribute a callback to whoever started it. This uses
`Flow` with a registered redirect URI, so the callback is an ordinary request
and carries its own `state`.

**Sessions are rows.** A stateless token cannot be revoked before it expires,
and "log out everywhere" and "disable this account now" are both requirements
at 100 accounts. The cost is one indexed lookup per request.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UserSession
from app.services import google_identity_service

logger = logging.getLogger(__name__)

STATE_COOKIE = "codejob_oauth_state"


class LoginError(RuntimeError):
    """Sign-in could not complete. The message is safe to show a user."""


@dataclass(frozen=True)
class StartedLogin:
    authorization_url: str
    state: str


def hash_session_token(token: str) -> str:
    """Sessions are stored hashed so a leaked database yields no live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def _client_config() -> dict[str, object]:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_auth_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _flow(state: str | None = None) -> Flow:
    from app.gmail_client import SCOPES

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = settings.google_auth_redirect_uri
    return flow


def begin_login() -> StartedLogin:
    if not (settings.google_client_id and settings.google_client_secret):
        raise LoginError("Google sign-in is not configured.")
    authorization_url, state = _flow().authorization_url(
        # offline + consent so the exchange returns a refresh token. Without
        # `prompt=consent` Google omits it on every sign-in after the first,
        # and this flow is also how the mailbox gets connected.
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return StartedLogin(authorization_url=authorization_url, state=state)


def exchange_code(*, code: str, state: str):
    """Swap the authorization code for credentials.

    Kept here rather than in the router so the router holds no OAuth mechanics,
    and so this is unit-testable without a request.
    """
    flow = _flow(state=state)
    flow.fetch_token(code=code)
    return flow.credentials


def _new_owner_id() -> str:
    """Opaque, generated, never derived from an email.

    `owner_id` is already stamped across 82 columns; putting an address in it
    would leak identity into every row and break when somebody changes it.
    """
    return f"usr_{uuid.uuid4().hex}"


def _claims_the_legacy_install(db: Session, email: str) -> bool:
    """True when this sign-in should inherit the existing single-tenant data.

    The alternative was rewriting `owner_id` across 84 columns to point at a
    freshly generated id. That is a long, irreversible data migration with a
    partial-failure mode, run against a live database, to achieve exactly what
    reusing the existing string achieves for free.

    So the first sign-in by the configured address **adopts**
    `settings.owner_id` rather than being given a new one. Every row, credential
    and label already in the database is theirs immediately, and nothing is
    written.

    Guarded three ways, because this hands over an entire install:

    - the address must be configured explicitly in `bootstrap_owner_email`;
    - the address must match, and it has already been verified by Google
      (`email_verified`) and admitted by the test-user list;
    - nobody may already hold that `owner_id` - a second claimant gets a
      generated id and no special treatment.
    """
    configured = (settings.bootstrap_owner_email or "").strip().lower()
    if not configured or email.strip().lower() != configured:
        return False
    taken = db.query(User).filter(User.owner_id == settings.owner_id).first()
    if taken is not None:
        logger.warning("bootstrap_owner_already_claimed by=%s", taken.email)
        return False
    return True


def upsert_user(db: Session, identity: google_identity_service.VerifiedIdentity) -> User:
    """Find or create the account for a verified Google identity.

    Matched on `google_subject` first: it is stable across an email change,
    which `email` is not. Falling back to email covers a row created before the
    identity scopes existed, and backfills the subject onto it.
    """
    user = db.query(User).filter(User.google_subject == identity.subject).first()
    if user is None:
        user = db.query(User).filter(User.email == identity.email).first()
        if user is not None and not user.google_subject:
            user.google_subject = identity.subject
    if user is None:
        claims_legacy = _claims_the_legacy_install(db, identity.email)
        user = User(
            owner_id=settings.owner_id if claims_legacy else _new_owner_id(),
            email=identity.email,
            google_subject=identity.subject,
            display_name=identity.name[:120],
            # Admin comes with the legacy claim and from nowhere else at
            # sign-in. Every other route to it is an existing admin promoting
            # somebody through /admin/users.
            is_admin=claims_legacy,
        )
        db.add(user)
        db.flush()
        logger.info(
            "user_created owner_id=%s legacy_claim=%s admin=%s",
            user.owner_id, claims_legacy, user.is_admin,
        )
    else:
        # The address can change on Google's side; the subject cannot.
        user.email = identity.email
        if identity.name:
            user.display_name = identity.name[:120]

    if user.disabled_at is not None:
        # Removing somebody from the GCP test-user list stops new sign-ins, but
        # an account disabled here must be refused even if Google still allows
        # it through.
        raise LoginError("That account has been disabled.")

    user.last_login_at = datetime.now(UTC)
    db.flush()
    return user


def create_session(db: Session, user: User, *, user_agent: str = "", remote_ip: str = "") -> str:
    """Return the raw token to put in the cookie. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
            user_agent=(user_agent or "")[:400],
            remote_ip=(remote_ip or "")[:64],
        )
    )
    db.flush()
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    """The user behind a cookie, or None. Read on every authenticated request.

    Every reason to reject returns None rather than raising: a stale cookie is
    an ordinary condition, not an error, and the caller's job is simply to fall
    back to unauthenticated.
    """
    if not token:
        return None
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == hash_session_token(token))
        .first()
    )
    if row is None or row.revoked_at is not None:
        return None
    if _as_utc(row.expires_at) <= datetime.now(UTC):
        return None
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or user.disabled_at is not None:
        return None
    row.last_seen_at = datetime.now(UTC)
    return user


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == hash_session_token(token))
        .first()
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.flush()


def revoke_all_sessions(db: Session, user_id: int) -> int:
    """Used by "log out everywhere" and by disabling an account."""
    now = datetime.now(UTC)
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        row.revoked_at = now
    db.flush()
    return len(rows)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
