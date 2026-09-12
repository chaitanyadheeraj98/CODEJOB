from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.schemas import AccountDeactivationResponse
from app.services import account_export_service, account_service, auth_service

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
