"""Admin routes: see and disable accounts.

Not *create* accounts. Provisioning is implicit — Google's test-user list
decides who may enter, and a `users` row appears on first successful sign-in.
There is deliberately no invite endpoint here to drift out of step with the GCP
console.

Everything is gated on the `is_admin` **database column**, checked server-side
on every request. It is never read from a token claim and never settable
through a request body, because a claim is whatever the client last persuaded
somebody to sign.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.schemas import (
    AdminUserResponse,
    AdminUserUpdateRequest,
    ObservabilityResponse,
    TaxonomyPublishResponse,
)
from app.services import auth_service, gmail_credential_service, observability_service
from scripts import export_base_taxonomy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    if not settings.feature_auth_enabled:
        raise HTTPException(status_code=404, detail="Sign-in is not enabled.")
    user = auth_service.resolve_session(db, request.cookies.get(settings.session_cookie_name))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    if not user.is_admin:
        # 403 rather than 404: the caller is authenticated, and pretending the
        # route does not exist would only make a real admin's life harder.
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


@router.post("/taxonomy/publish", response_model=TaxonomyPublishResponse)
def publish_taxonomy(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TaxonomyPublishResponse:
    """§14 H2: export the calling admin's overlay as the base artefact, for review.

    **It writes to nobody.** Not to the repo, not to the database, not to
    another account. It returns the files a developer would commit, and the
    commit is what ships them - deliberate, reviewable, revertible, the same
    argument §11.1 makes for the base taxonomy generally. An endpoint that
    changed what 100 accounts see would be none of those three.

    The calling admin's own overlay, resolved from the session like everywhere
    else - never an `owner_id` parameter, which would make this a way to read
    another account's vocabulary.
    """
    artefact = export_base_taxonomy.build_base_taxonomy(db, owner_id=admin.owner_id)
    return TaxonomyPublishResponse(
        owner_id=admin.owner_id,
        generated_at=datetime.now(UTC),
        counts=export_base_taxonomy.counts_for(artefact),
        files=artefact,
    )


@router.get("/observability", response_model=ObservabilityResponse)
def observability(
    window_hours: int = Query(
        default=observability_service.DEFAULT_WINDOW_HOURS,
        ge=1,
        le=observability_service.MAX_WINDOW_HOURS,
    ),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ObservabilityResponse:
    """§12.4: latency, errors and who is stuck - without reading anyone's mail.

    Admin-gated like everything else here, on the `is_admin` database column.
    The window is capped in the signature rather than in the service so that an
    out-of-range value is a 422 the caller can see, not a silent clamp that
    answers a different question than the one asked.
    """
    return ObservabilityResponse(**observability_service.summarise(db, window_hours=window_hours))


def _to_response(db: Session, user: User) -> AdminUserResponse:
    status = gmail_credential_service.connection_status(db, user.owner_id)
    return AdminUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        owner_id=user.owner_id,
        is_admin=user.is_admin,
        disabled=user.disabled_at is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        gmail_connected=status.connected,
        gmail_email=status.google_email,
    )


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[AdminUserResponse]:
    return [_to_response(db, user) for user in db.query(User).order_by(User.email).all()]


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserResponse:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")

    if payload.disabled is not None:
        if payload.disabled and user.id == admin.id:
            # Locking the last admin out of the admin panel is a support call
            # nobody can answer from inside the product.
            raise HTTPException(status_code=422, detail="You cannot disable your own account.")
        if payload.disabled:
            user.disabled_at = user.disabled_at or _now()
            # Disabling has to end live sessions, not only refuse new sign-ins.
            # Removing somebody from the GCP test-user list does neither.
            revoked = auth_service.revoke_all_sessions(db, user.id)
            logger.info("admin_disabled_user target=%s sessions_revoked=%s", user.owner_id, revoked)
        else:
            user.disabled_at = None
            user.deletion_requested_at = None

    if payload.is_admin is not None:
        if not payload.is_admin and user.id == admin.id:
            raise HTTPException(status_code=422, detail="You cannot remove your own administrator access.")
        user.is_admin = payload.is_admin

    db.commit()
    db.refresh(user)
    return _to_response(db, user)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
