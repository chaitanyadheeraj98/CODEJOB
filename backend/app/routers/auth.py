"""Sign-in routes.

There is no `/auth/register`, no `/auth/login` and no `/auth/password`, because
there is no local credential to create, present or rotate. Access is decided by
the Google OAuth consent screen's test-user list, and a `users` row is created
by the first successful sign-in.

Every route here 404s while `feature_auth_enabled` is off, so the flag is a
real switch rather than a half-exposed surface.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.schemas import AuthUserResponse, LoginStartResponse
from app.services import auth_service, google_identity_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def require_auth_enabled() -> None:
    if not settings.feature_auth_enabled:
        raise HTTPException(status_code=404, detail="Sign-in is not enabled.")


def _set_cookie(response: Response, name: str, value: str, *, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        # httpOnly so script cannot read it; Lax rather than Strict so the
        # Google callback still arrives with the cookie attached - Strict would
        # drop it on the cross-site redirect and break every sign-in.
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/google/start", response_model=LoginStartResponse, dependencies=[Depends(require_auth_enabled)])
def start_google_login(response: Response) -> LoginStartResponse:
    try:
        started = auth_service.begin_login()
    except auth_service.LoginError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # The state is echoed back by Google and compared in the callback. Stored
    # in an httpOnly cookie rather than server-side because it is single-use,
    # short-lived, and needs no coordination between workers.
    _set_cookie(response, auth_service.STATE_COOKIE, started.state, max_age=600)
    # The PKCE verifier travels with the state, for the same 10 minutes. Both
    # are httpOnly: the point of PKCE is that only the browser that began the
    # flow can complete it.
    _set_cookie(response, auth_service.VERIFIER_COOKIE, started.code_verifier, max_age=600)
    return LoginStartResponse(authorization_url=started.authorization_url, state=started.state)


@router.get("/google/callback", dependencies=[Depends(require_auth_enabled)])
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    """Exchange the code, prove who it is, and start a session.

    Returns a redirect in every case, including failure: this URL is reached by
    a browser, and rendering JSON at a person is not an answer. The failure
    reason travels as a query parameter for the dashboard to display.
    """
    from app import gmail_client

    def failed(reason: str) -> Response:
        logger.warning("google_login_failed reason=%s", reason)
        return RedirectResponse(f"{settings.dashboard_base_url}/?login_error={reason}", status_code=303)

    if error:
        return failed("declined")
    if not code:
        return failed("no_code")

    expected_state = request.cookies.get(auth_service.STATE_COOKIE)
    # CSRF: an attacker who can make the browser hit this URL cannot also set
    # the cookie, so a callback that does not carry the matching state is not
    # one this browser started.
    if not expected_state or not state or state != expected_state:
        return failed("state_mismatch")

    try:
        credentials = auth_service.exchange_code(
            code=code, state=state, code_verifier=request.cookies.get(auth_service.VERIFIER_COOKIE, "")
        )
        identity = google_identity_service.verify_id_token(str(getattr(credentials, "id_token", "") or ""))
        mailbox = gmail_client._profile_email(credentials)
        google_identity_service.assert_identity_matches_mailbox(identity, mailbox)
        user = auth_service.upsert_user(db, identity)
        gmail_client.store_credentials_for_owner(user.owner_id, credentials, identity=identity)
        token = auth_service.create_session(
            db,
            user,
            user_agent=request.headers.get("user-agent", ""),
            remote_ip=request.client.host if request.client else "",
        )
        db.commit()
    except google_identity_service.IdentityVerificationError:
        db.rollback()
        return failed("identity_unverified")
    except auth_service.LoginError:
        db.rollback()
        return failed("not_permitted")
    except Exception:
        db.rollback()
        logger.exception("google_login_unexpected_failure")
        return failed("unexpected")

    redirect = RedirectResponse(settings.dashboard_base_url, status_code=303)
    _set_cookie(redirect, settings.session_cookie_name, token, max_age=settings.session_ttl_hours * 3600)
    redirect.delete_cookie(auth_service.STATE_COOKIE, path="/")
    redirect.delete_cookie(auth_service.VERIFIER_COOKIE, path="/")
    return redirect


@router.get("/me", response_model=AuthUserResponse, dependencies=[Depends(require_auth_enabled)])
def current_user(request: Request, db: Session = Depends(get_db)) -> AuthUserResponse:
    user = auth_service.resolve_session(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return AuthUserResponse(
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        owner_id=user.owner_id,
    )


@router.post("/logout", dependencies=[Depends(require_auth_enabled)])
def logout(request: Request, db: Session = Depends(get_db)) -> Response:
    auth_service.revoke_session(db, request.cookies.get(settings.session_cookie_name))
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response
