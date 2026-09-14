from __future__ import annotations

import logging

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.schemas import AccountDeactivationResponse, AccountDeletionRequest
from app.services import account_export_service, account_service, auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])


def require_account_user(request: Request, db: Session = Depends(get_db)) -> User:
    if not settings.feature_auth_enabled:
        raise HTTPException(status_code=404, detail="Sign-in is not enabled.")
    user = auth_service.resolve_session(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


@router.post("/export")
def export_account(
    user: User = Depends(require_account_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """§13 G2: their applications, conversations and taxonomy overlay as JSON.

    Streamed rather than assembled: one real account holds ~19,000 rows across
    these tables, most of them recruiter mail carrying bodies, so building the
    document in memory would let one mailbox decide how much this process
    allocates.

    The owner comes from the verified session, never from a parameter - the
    same rule as everywhere else, and the one that keeps this from being an
    endpoint for reading other people's mail.
    """
    return StreamingResponse(
        account_export_service.stream_export(db, user.owner_id),
        media_type="application/json",
        headers={
            "Content-Disposition":
                f'attachment; filename="{account_export_service.filename_for(user.owner_id)}"',
        },
    )


@router.post("/deactivate", response_model=AccountDeactivationResponse)
def deactivate_account(
    response: Response,
    user: User = Depends(require_account_user),
    db: Session = Depends(get_db),
) -> AccountDeactivationResponse:
    result = account_service.deactivate(db, user)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    return AccountDeactivationResponse(
        deactivated_at=result.deactivated_at,
        purge_after=result.purge_after,
        sessions_revoked=result.sessions_revoked,
        jobs_stopped=result.jobs_stopped,
    )


#: How recently the caller must have proved they are this person to Google.
#: §13: "a live session is not authority to destroy an account, and neither is
#: a borrowed laptop." `last_login_at` moves only on a completed Google
#: sign-in, so a stolen cookie cannot refresh it.
REAUTH_WINDOW = timedelta(minutes=10)


@router.delete("", response_model=AccountDeactivationResponse)
def delete_account(
    payload: AccountDeletionRequest,
    response: Response,
    user: User = Depends(require_account_user),
    db: Session = Depends(get_db),
) -> AccountDeactivationResponse:
    """§13: ask to be deleted. The purge itself happens when the window closes.

    Nothing is destroyed here beyond what deactivation already destroys -
    sessions, credentials, running work. The rows go when
    `account_purge_service` finds this account past its recovery window, which
    is the promise G1 already makes to the user in as many words.

    Two gates, and they guard different things. The **typed confirmation** is
    against acting on the wrong account; the **re-authentication window** is
    against someone acting on an account that is not theirs at all.
    """
    if payload.confirm_email.strip().lower() != (user.email or "").strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Type the email address of this account to confirm deletion.",
        )

    last_login = user.last_login_at
    if last_login is not None and last_login.tzinfo is None:
        last_login = last_login.replace(tzinfo=UTC)
    if last_login is None or datetime.now(UTC) - last_login > REAUTH_WINDOW:
        # 401 with a distinguishable code: the dashboard sends them back
        # through Google rather than showing "something went wrong".
        raise HTTPException(status_code=401, detail="reauthentication_required")

    result = account_service.deactivate(db, user)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    logger.info("account_deletion_requested owner=%s purge_after=%s", user.owner_id, result.purge_after)
    return AccountDeactivationResponse(
        deactivated_at=result.deactivated_at,
        purge_after=result.purge_after,
        sessions_revoked=result.sessions_revoked,
        jobs_stopped=result.jobs_stopped,
    )

