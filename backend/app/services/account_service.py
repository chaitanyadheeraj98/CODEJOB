from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from rq.command import send_stop_job_command
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from sqlalchemy.orm import Session

from app.jobs.queues import get_redis_connection
from app.models import GmailCredential, ProviderCredential, RecentRun, User
from app.services import auth_service, gmail_credential_service

logger = logging.getLogger(__name__)

RECOVERY_DAYS = 30
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ACTIVE_JOB_STATUSES = {"queued", "running"}


@dataclass(frozen=True)
class DeactivationResult:
    deactivated_at: datetime
    purge_after: datetime
    sessions_revoked: int
    jobs_stopped: int


def is_owner_disabled(db: Session, owner_id: str) -> bool:
    return (
        db.query(User.id)
        .filter(User.owner_id == owner_id, User.disabled_at.isnot(None))
        .first()
        is not None
    )


def revoke_google_token(token: str) -> None:
    request = Request(
        GOOGLE_REVOKE_URL,
        data=urlencode({"token": token}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        if response.status >= 400:
            raise RuntimeError(f"Google returned HTTP {response.status}")


def _revoke_google_access(db: Session, owner_id: str) -> None:
    try:
        credentials = gmail_credential_service.get_credentials(db, owner_id)
        token = (credentials.refresh_token or credentials.token or "") if credentials else ""
        if token:
            revoke_google_token(token)
    except Exception:
        logger.exception("account_google_revoke_failed owner=%s", owner_id)


def _stop_push_watch(owner_id: str) -> None:
    """Best-effort, and never fatal to the deactivation it is part of.

    Refusing to deactivate an account because Google was slow would be the
    wrong trade in both directions: the user asked to leave, and the watch
    expires on its own within seven days regardless.
    """
    try:
        from app.services import gmail_pubsub_service

        gmail_pubsub_service.stop_watch(owner_id)
    except Exception:
        logger.exception("account_watch_stop_failed owner=%s", owner_id)


def _stop_jobs(db: Session, owner_id: str) -> int:
    rows = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == owner_id, RecentRun.status.in_(ACTIVE_JOB_STATUSES))
        .all()
    )
    if not rows:
        return 0
    try:
        connection = get_redis_connection()
    except Exception:
        connection = None
        logger.exception("account_job_stop_redis_unavailable owner=%s", owner_id)
    for row in rows:
        if connection is not None and row.job_backend_id:
            try:
                job = Job.fetch(row.job_backend_id, connection=connection)
                if job.get_status(refresh=True) == JobStatus.STARTED:
                    send_stop_job_command(connection, job.id)
                else:
                    job.cancel()
            except NoSuchJobError:
                pass
            except Exception:
                logger.exception("account_job_stop_failed owner=%s job=%s", owner_id, row.job_backend_id)
        row.status = "canceled"
        row.detail = "Account deactivated."
    return len(rows)


def deactivate(db: Session, user: User) -> DeactivationResult:
    now = datetime.now(UTC)
    user.disabled_at = user.disabled_at or now
    user.deletion_requested_at = user.deletion_requested_at or now
    sessions_revoked = auth_service.revoke_all_sessions(db, user.id)
    jobs_stopped = _stop_jobs(db, user.owner_id)
    # Before the revoke, not after. `users.stop` authenticates with the token
    # being revoked two lines down, so the order is the difference between
    # stopping delivery and leaving Gmail publishing to a topic for a mailbox
    # nobody here can read any more.
    _stop_push_watch(user.owner_id)
    _revoke_google_access(db, user.owner_id)
    db.query(GmailCredential).filter(GmailCredential.owner_id == user.owner_id).delete(
        synchronize_session=False
    )
    db.query(ProviderCredential).filter(ProviderCredential.owner_id == user.owner_id).delete(
        synchronize_session=False
    )
    db.flush()
    requested_at = user.deletion_requested_at
    return DeactivationResult(
        deactivated_at=user.disabled_at,
        purge_after=requested_at + timedelta(days=RECOVERY_DAYS),
        sessions_revoked=sessions_revoked,
        jobs_stopped=jobs_stopped,
    )
