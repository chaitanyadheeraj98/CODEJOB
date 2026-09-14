# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import base64
import html
import json
import logging
import mimetypes
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.ai.draft_formatting import draft_text_to_html
from app.config import settings
from app import tenancy
from app.db import session_scope
from app.parsing.document_extraction import clean_html_text
from app.services import gmail_credential_service, google_identity_service

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send"]
# Identity scopes, added for B2. They are what makes one consent serve both
# sign-in and mailbox access, and they are the only way to obtain the stable
# `sub` claim - which is why GmailCredential.google_subject was nullable until
# now. Adding them forces a one-time re-consent for anyone already connected.
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
for _scope in IDENTITY_SCOPES:
    if _scope not in SCOPES:
        SCOPES.append(_scope)

# Google does not echo the requested scope list verbatim once `openid` is in
# it: it reorders, and it adds the userinfo.profile scope that `openid` implies.
# oauthlib treats any difference as tampering and raises "Scope has changed",
# which would abort every consent with an error that looks nothing like its
# cause. Relaxing the check is the documented remedy and is safe here because
# the token's audience and signature are verified separately, in
# google_identity_service.verify_id_token.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
if SHEETS_SCOPE not in SCOPES:
    SCOPES.append(SHEETS_SCOPE)
_oauth_lock = threading.Lock()
_oauth_thread: threading.Thread | None = None
_oauth_last_error: str | None = None
_oauth_last_authorization_url: str | None = None
_oauth_prepared_flow: InstalledAppFlow | None = None
_oauth_prepared_state: str | None = None
_oauth_prepared_kwargs: dict[str, str] | None = None


class GmailMessageCandidate(TypedDict):
    external_message_id: str
    external_thread_id: str
    external_rfc_message_id: str
    in_reply_to_header: str
    references_header: str
    sender: str
    recipient_email: str
    subject: str
    body: str
    snippet: str
    gmail_received_at: datetime | None
    label_ids: list[str]
    to_header: str
    cc_header: str
    bcc_header: NotRequired[str]
    list_id: str
    list_post: str
    list_unsubscribe: str
    delivered_to: str
    mailing_list: str


@dataclass(frozen=True)
class MailAttachment:
    path: str
    display_name: str | None = None
    mime_type: str | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]


def is_gmail_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret and settings.google_redirect_uri)


# Gmail connect does not use `settings.google_redirect_uri`. That one belongs to
# app login (`/auth/google/callback`), and handing it to this flow sent the
# Gmail code to the login handler, which rejected a state it never issued and
# answered `?login_error=state_mismatch` while the listener below waited for a
# code that never arrived. `run_local_server` overwrites `redirect_uri` with
# this loopback regardless, so preparing the URL with anything else only means
# the URL the user opens is not the one the listener is waiting for.
OAUTH_LOOPBACK_HOST = "localhost"
OAUTH_LOOPBACK_PORT = 8080


def oauth_loopback_redirect_uri() -> str:
    return f"http://{OAUTH_LOOPBACK_HOST}:{OAUTH_LOOPBACK_PORT}/"


def _credentials_payload() -> dict[str, Any]:
    return {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [oauth_loopback_redirect_uri(), settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _ensure_token_parent() -> None:
    token_path = Path(settings.google_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)


class GmailReconnectRequired(RuntimeError):
    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        super().__init__("Gmail connection needs reconnecting.")


def _load_credentials(owner_id: str | None = None) -> Credentials:
    owner_id = owner_id or tenancy.owner_id()
    if not is_gmail_configured():
        raise RuntimeError("Gmail OAuth is not configured")

    token_path = Path(settings.google_token_path)
    creds: Credentials | None = None
    if settings.feature_db_credentials_enabled:
        with session_scope() as db:
            creds = gmail_credential_service.get_credentials(db, owner_id)
            if (
                creds is None
                # settings.owner_id, deliberately, not tenancy.owner_id():
                # the shared token file belongs to the legacy single-tenant
                # owner, and a signed-in user must never be able to adopt it.
                and owner_id == settings.owner_id
                and gmail_credential_service.import_legacy_token_file(db, owner_id)
            ):
                creds = gmail_credential_service.get_credentials(db, owner_id)
    elif token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        _backfill_profile_email(owner_id, creds)
        return creds

    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request

        try:
            creds.refresh(Request())
        except RefreshError as exc:
            if not settings.feature_db_credentials_enabled:
                raise
            with session_scope() as db:
                gmail_credential_service.mark_revoked(db, owner_id, "refresh_failed")
            logger.warning("gmail_token_refresh_failed owner_id=%s error=refresh_failed", owner_id)
            raise GmailReconnectRequired(owner_id) from exc
        if settings.feature_db_credentials_enabled:
            with session_scope() as db:
                gmail_credential_service.mark_refreshed(db, owner_id, creds)
            _backfill_profile_email(owner_id, creds)
        else:
            _ensure_token_parent()
            token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError(
            "Gmail OAuth would require an interactive browser flow here, which hangs "
            "under pytest (no cached/refreshable token). The calling function needs "
            "to be mocked in this test instead of reaching _load_credentials()."
        )
    if settings.feature_db_credentials_enabled:
        raise GmailReconnectRequired(owner_id)

    global _oauth_last_authorization_url
    flow = InstalledAppFlow.from_client_config(_credentials_payload(), SCOPES)
    flow.redirect_uri = oauth_loopback_redirect_uri()
    # In Docker, there is no local browser in-container; user opens the printed URL manually.
    extra_auth_kwargs: dict[str, str] = {"prompt": "select_account"}
    if settings.google_login_hint:
        extra_auth_kwargs["login_hint"] = settings.google_login_hint

    auth_url, auth_state = flow.authorization_url(**extra_auth_kwargs)
    _oauth_last_authorization_url = auth_url
    logger.info("Gmail OAuth authorization URL: %s", auth_url)

    flow_any: Any = flow
    creds = cast(
        Credentials,
        flow_any.run_local_server(
            host=OAUTH_LOOPBACK_HOST,
            bind_addr="0.0.0.0",
            port=OAUTH_LOOPBACK_PORT,
            open_browser=False,
            authorization_prompt_message="Please visit this URL to authorize this application: {url}",
            state=auth_state,
            **extra_auth_kwargs,
        ),
    )
    _ensure_token_parent()
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _prepare_oauth_flow() -> tuple[InstalledAppFlow, str, str, dict[str, str]]:
    flow = InstalledAppFlow.from_client_config(_credentials_payload(), SCOPES)
    flow.redirect_uri = oauth_loopback_redirect_uri()
    extra_auth_kwargs: dict[str, str] = {"prompt": "select_account"}
    if settings.google_login_hint:
        extra_auth_kwargs["login_hint"] = settings.google_login_hint
    auth_url, auth_state = flow.authorization_url(**extra_auth_kwargs)
    return flow, auth_url, auth_state, extra_auth_kwargs


def _run_prepared_oauth_flow(flow: InstalledAppFlow, auth_state: str, extra_auth_kwargs: dict[str, str]) -> Credentials:
    flow_any: Any = flow
    creds = cast(
        Credentials,
        flow_any.run_local_server(
            host=OAUTH_LOOPBACK_HOST,
            bind_addr="0.0.0.0",
            port=OAUTH_LOOPBACK_PORT,
            open_browser=False,
            authorization_prompt_message="Please visit this URL to authorize this application: {url}",
            state=auth_state,
            **extra_auth_kwargs,
        ),
    )
    _persist_new_credentials(creds)
    return creds


def _profile_email(creds: Credentials) -> str:
    """The address that just consented, via users.getProfile.

    Lives here rather than in gmail_credential_service because the import runs
    one way only - gmail_client imports the service, never the reverse - and
    this needs a Gmail API call. The service takes the result as an argument.

    Costs one quota unit, once per connect, and needs no new scope:
    gmail.modify already covers getProfile. Chosen over reading an ID token
    precisely so the scope set does not change, which would force every
    existing user to re-consent. `google_subject` stays null until B2 widens
    the scopes deliberately.

    Never fatal. A transient failure here must not cost the user a connection
    they just completed; the address is backfilled on the next successful save.
    """
    try:
        profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
        return str(_as_dict(profile).get("emailAddress") or "")
    except Exception as exc:
        # Type only, never the message. A provider error reaching this path can
        # carry response text, and A5 established that provider errors do not
        # go to the log.
        logger.warning("gmail_profile_lookup_failed error=%s", type(exc).__name__)
        return ""


def _backfill_profile_email(owner_id: str, creds: Credentials) -> None:
    """Fill in `google_email` for a row that arrived without one.

    Found by flipping the flag on live data: the legacy token file's `account`
    field is empty, so an imported credential has no address, and nothing would
    ever fill it in - `save_credentials` only runs on a fresh consent, which an
    imported owner never performs. That left the Settings card with no address
    to show and, more seriously, no value in the column Pub/Sub will route on.

    One getProfile call, once, only when the column is empty. A failure is
    ignored: this is cosmetic until Pub/Sub lands, and must never break a Gmail
    call that was about to succeed.
    """
    try:
        with session_scope() as db:
            row = gmail_credential_service.get_row(db, owner_id)
            if row is None or row.google_email:
                return
        email = _profile_email(creds)
        if not email:
            return
        with session_scope() as db:
            row = gmail_credential_service.get_row(db, owner_id)
            if row is not None and not row.google_email:
                row.google_email = email[:320]
        logger.info("gmail_credentials_email_backfilled owner_id=%s", owner_id)
    except Exception as exc:
        logger.warning(
            "gmail_credentials_email_backfill_failed owner_id=%s error=%s", owner_id, type(exc).__name__
        )


def verified_identity(creds: Credentials):
    """The verified Google identity behind a freshly issued credential.

    Returns None when the credential carries no ID token, which is the case for
    anything issued before the identity scopes were added. That is deliberate:
    an existing connection must keep working until its owner happens to
    reconnect, rather than being invalidated by a deploy.
    """
    raw = getattr(creds, "id_token", None)
    if not raw:
        return None
    return google_identity_service.verify_id_token(str(raw))


def store_credentials_for_owner(owner_id: str, creds: Credentials, *, identity=None) -> None:
    """Persist a credential for a specific owner, from the web sign-in flow.

    Distinct from `_persist_new_credentials` because the caller has already
    verified the identity and matched it against the mailbox - repeating either
    would mean a second getProfile call and a second verification of the same
    token. The owner is passed explicitly, since sign-in is the one path where
    it is emphatically not the configured constant.
    """
    with session_scope() as db:
        gmail_credential_service.save_credentials(
            db,
            owner_id,
            creds,
            google_email=identity.email if identity is not None else _profile_email(creds),
            google_subject=identity.subject if identity is not None else None,
        )


def _persist_new_credentials(creds: Credentials, owner_id: str | None = None) -> None:
    owner_id = owner_id or tenancy.owner_id()
    if not settings.feature_db_credentials_enabled:
        _ensure_token_parent()
        Path(settings.google_token_path).write_text(creds.to_json(), encoding="utf-8")
        return

    google_email = _profile_email(creds)
    identity = verified_identity(creds)
    google_subject = None
    if identity is not None:
        # The check that stops somebody signing in as themselves and attaching
        # another account's mailbox. A mismatch raises, and nothing is stored.
        google_identity_service.assert_identity_matches_mailbox(identity, google_email)
        google_subject = identity.subject
        google_email = identity.email

    with session_scope() as db:
        gmail_credential_service.save_credentials(
            db, owner_id, creds, google_email=google_email, google_subject=google_subject
        )


def _gmail_service(owner_id: str | None = None) -> Any:
    creds = _load_credentials(owner_id)
    return build("gmail", "v1", credentials=creds)


def _sheets_service(owner_id: str | None = None) -> Any:
    creds = _load_credentials(owner_id)
    return build("sheets", "v4", credentials=creds)


def _oauth_worker() -> None:
    global _oauth_last_error, _oauth_prepared_flow, _oauth_prepared_state, _oauth_prepared_kwargs
    try:
        prepared_flow: InstalledAppFlow | None = None
        prepared_state: str | None = None
        prepared_kwargs: dict[str, str] | None = None
        with _oauth_lock:
            prepared_flow = _oauth_prepared_flow
            prepared_state = _oauth_prepared_state
            prepared_kwargs = _oauth_prepared_kwargs
        if prepared_flow and prepared_state and prepared_kwargs:
            _run_prepared_oauth_flow(prepared_flow, prepared_state, prepared_kwargs)
        else:
            _load_credentials()
        _oauth_last_error = None
    except Exception as exc:
        _oauth_last_error = str(exc)
    finally:
        with _oauth_lock:
            _oauth_prepared_flow = None
            _oauth_prepared_state = None
            _oauth_prepared_kwargs = None


def oauth_bootstrap_status() -> tuple[bool, str | None]:
    global _oauth_thread
    with _oauth_lock:
        in_progress = bool(_oauth_thread and _oauth_thread.is_alive())
        return in_progress, _oauth_last_error


def oauth_authorization_url() -> str | None:
    with _oauth_lock:
        return _oauth_last_authorization_url


def start_oauth_bootstrap() -> tuple[str, str, str | None]:
    global _oauth_thread, _oauth_last_error, _oauth_last_authorization_url, _oauth_prepared_flow, _oauth_prepared_state, _oauth_prepared_kwargs
    if not is_gmail_configured():
        return "oauth_not_configured", "Gmail OAuth is not configured.", None

    configured, authenticated, _ = gmail_auth_status()
    if configured and authenticated:
        return "ready", "Gmail already authenticated.", None

    with _oauth_lock:
        if _oauth_thread and _oauth_thread.is_alive():
            return (
                "oauth_in_progress",
                "OAuth is already in progress. Open the authorization URL below.",
                _oauth_last_authorization_url,
            )
        _oauth_last_error = None
        try:
            flow, auth_url, auth_state, extra_auth_kwargs = _prepare_oauth_flow()
            _oauth_last_authorization_url = auth_url
            _oauth_prepared_flow = flow
            _oauth_prepared_state = auth_state
            _oauth_prepared_kwargs = extra_auth_kwargs
            logger.info("Gmail OAuth authorization URL: %s", auth_url)
        except Exception as exc:
            _oauth_last_authorization_url = None
            _oauth_prepared_flow = None
            _oauth_prepared_state = None
            _oauth_prepared_kwargs = None
            _oauth_last_error = str(exc)
            return (
                "oauth_required",
                f"OAuth setup failed: {exc}",
                None,
            )
        _oauth_thread = threading.Thread(target=_oauth_worker, daemon=True, name="gmail-oauth-bootstrap")
        _oauth_thread.start()
    return (
        "oauth_in_progress",
        "OAuth started. Open the authorization URL below, complete sign-in, then retry Sync + Queue.",
        oauth_authorization_url(),
    )


def _decode_chunk(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_from_parts(parts: list[dict[str, Any]]) -> tuple[str, str]:
    text_plain = ""
    text_html = ""
    for part in parts:
        mime = (part.get("mimeType") or "").lower()
        body_data = _decode_chunk(part.get("body", {}).get("data"))
        if mime == "text/plain" and body_data and not text_plain:
            text_plain = body_data
        elif mime == "text/html" and body_data and not text_html:
            text_html = body_data

        nested_parts = _as_list_of_dicts(part.get("parts"))
        if nested_parts:
            nested_plain, nested_html = _extract_from_parts(nested_parts)
            if nested_plain and not text_plain:
                text_plain = nested_plain
            if nested_html and not text_html:
                text_html = nested_html
    return text_plain, text_html


def _strip_html(html: str) -> str:
    return clean_html_text(html)


def _decode_body(payload: dict[str, Any]) -> str:
    direct = _decode_chunk(payload.get("body", {}).get("data"))
    if direct:
        return _strip_html(direct) if (payload.get("mimeType") or "").lower() == "text/html" else direct

    plain, html = _extract_from_parts(_as_list_of_dicts(payload.get("parts")))
    if html:
        cleaned_html = _strip_html(html)
        if cleaned_html.strip():
            return cleaned_html
    if plain:
        return plain
    return ""


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    for item in headers:
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def _extract_email_address(from_header: str) -> str:
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).strip()
    return from_header.strip()


def _message_details_to_candidate(details: dict[str, Any]) -> GmailMessageCandidate | None:
    message_id = details.get("id")
    if not isinstance(message_id, str) or not message_id:
        return None
    payload = _as_dict(details.get("payload"))
    header_items = _as_list_of_dicts(payload.get("headers"))
    headers: list[dict[str, str]] = [
        {
            "name": str(item.get("name", "")),
            "value": str(item.get("value", "")),
        }
        for item in header_items
    ]
    from_header = _get_header(headers, "From")
    to_header = _get_header(headers, "To")
    cc_header = _get_header(headers, "Cc")
    subject = _get_header(headers, "Subject") or "(No Subject)"
    rfc_message_id = _get_header(headers, "Message-ID")
    in_reply_to_header = _get_header(headers, "In-Reply-To")
    references_header = _get_header(headers, "References")
    list_id = _get_header(headers, "List-Id")
    list_post = _get_header(headers, "List-Post")
    list_unsubscribe = _get_header(headers, "List-Unsubscribe")
    delivered_to = _get_header(headers, "Delivered-To")
    mailing_list = _get_header(headers, "Mailing-List")
    internal_date_ms = details.get("internalDate")
    gmail_received_at = None
    if internal_date_ms:
        try:
            gmail_received_at = datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC)
        except (TypeError, ValueError):
            gmail_received_at = None
    body = _decode_body(payload)
    snippet = (details.get("snippet") or "").strip()
    if not body.strip() and snippet:
        body = snippet
    return {
        "external_message_id": message_id,
        "external_thread_id": str(details.get("threadId", "")),
        "external_rfc_message_id": rfc_message_id,
        "in_reply_to_header": in_reply_to_header,
        "references_header": references_header,
        "sender": from_header,
        "recipient_email": _extract_email_address(from_header),
        "subject": subject,
        "body": body,
        "snippet": snippet,
        "gmail_received_at": gmail_received_at,
        "label_ids": [str(label) for label in details.get("labelIds", []) if isinstance(label, str)],
        "to_header": to_header,
        "cc_header": cc_header,
        "bcc_header": _get_header(headers, "Bcc"),
        "list_id": list_id,
        "list_post": list_post,
        "list_unsubscribe": list_unsubscribe,
        "delivered_to": delivered_to,
        "mailing_list": mailing_list,
    }


def list_unread_candidates_by_query(
    query: str, max_results_per_page: int = 100, max_total_results: int | None = None
) -> list[GmailMessageCandidate]:
    return _list_candidates(query=query, max_results_per_page=max_results_per_page, max_total_results=max_total_results)


def list_candidates_by_label_ids(label_ids: list[str], *, unread_only=False, max_total_results=None, skip_message_ids=None) -> list[GmailMessageCandidate]:
    return _list_candidates(query="is:unread" if unread_only else "", label_ids=label_ids,
        max_total_results=max_total_results, skip_message_ids=skip_message_ids)


def list_candidates_by_query(query: str, *, max_total_results=None, skip_message_ids=None) -> list[GmailMessageCandidate]:
    return _list_candidates(query=query, max_total_results=max_total_results, skip_message_ids=skip_message_ids)


def _list_candidates(*, query: str, label_ids=None, max_results_per_page=100, max_total_results=None, skip_message_ids=None) -> list[GmailMessageCandidate]:
    service = _gmail_service()
    page_token: str | None = None
    results: list[GmailMessageCandidate] = []
    skip_message_ids = skip_message_ids or set()
    while True:
        if max_total_results is not None and len(results) >= max_total_results:
            break
        req = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results_per_page,
            pageToken=page_token,
            **({"labelIds": label_ids} if label_ids is not None else {}),
        )
        response = _as_dict(req.execute())
        messages = _as_list_of_dicts(response.get("messages"))
        for message in messages:
            if max_total_results is not None and len(results) >= max_total_results:
                break
            message_id = message.get("id")
            if not isinstance(message_id, str) or not message_id:
                continue
            if message_id in skip_message_ids:
                continue
            try:
                details = _as_dict(
                    service.users().messages().get(userId="me", id=message_id, format="full").execute()
                )
            except HttpError:
                # A message can be deleted/moved between the list() call and this get() call
                # (e.g. another client archives it concurrently); skip it rather than aborting
                # the whole batch over one stale id.
                logger.exception("gmail_message_fetch_failed message_id=%s", message_id)
                continue
            candidate = _message_details_to_candidate(details)
            if candidate is not None:
                results.append(candidate)

        next_token_raw = response.get("nextPageToken")
        next_token = next_token_raw if isinstance(next_token_raw, str) and next_token_raw else None
        if not next_token:
            break
        page_token = next_token
    return results


def get_candidates_by_message_ids(message_ids: list[str]) -> list[GmailMessageCandidate]:
    """Fetch specific messages by id, independent of the is:unread query/label state.

    Used to retry skipped items directly by their stored Gmail message id, so retry
    works regardless of whether the message is currently marked read or unread.
    """
    service = _gmail_service()
    results: list[GmailMessageCandidate] = []
    for message_id in message_ids:
        try:
            details = _as_dict(
                service.users().messages().get(userId="me", id=message_id, format="full").execute()
            )
        except HttpError:
            logger.exception("gmail_message_fetch_failed message_id=%s", message_id)
            continue
        candidate = _message_details_to_candidate(details)
        if candidate is not None:
            results.append(candidate)
    return results


# --- push delivery ------------------------------------------------------
#
# Three thin wrappers, deliberately holding no policy. When to renew a watch,
# what to do with a stale cursor and which messages matter all belong to
# `gmail_pubsub_service`; what belongs here is the shape of Gmail's replies,
# because that is what the rest of the application should not have to know.


class StaleHistoryId(RuntimeError):
    """Gmail no longer holds history from the cursor it was given.

    Its own 404, given a name. The caller cannot retry its way out of this -
    the record is gone - so it has to be distinguishable from the transient
    failures that are worth retrying, which is the entire reason this is not
    just an `HttpError` reaching the service layer.
    """


@dataclass(frozen=True)
class MailboxWatch:
    """What `users.watch` promises: where history starts, and when it lapses."""

    history_id: str
    expiration_at: datetime | None


@dataclass(frozen=True)
class HistoryPage:
    message_ids: tuple[str, ...]
    next_page_token: str | None
    history_id: str | None


def watch_mailbox(topic_name: str) -> MailboxWatch:
    """Ask Gmail to publish this mailbox's changes to `topic_name`.

    No `labelIds` filter. Restricting the watch to INBOX would be cheaper and
    would miss two things this application needs: replies sent from Gmail
    itself, and the label transitions that tracked conversations are built on.
    Filtering happens here, where the tracked-label catalog is known.
    """
    response = _as_dict(
        _gmail_service().users().watch(userId="me", body={"topicName": topic_name}).execute()
    )
    return MailboxWatch(
        history_id=str(response.get("historyId") or ""),
        expiration_at=_epoch_millis_to_utc(response.get("expiration")),
    )


def stop_mailbox_watch() -> None:
    """Stop push delivery for this mailbox. Idempotent at Gmail's end."""
    _gmail_service().users().stop(userId="me").execute()


def list_history(start_history_id: str, page_token: str | None = None) -> HistoryPage:
    """One page of changes since `start_history_id`.

    Returns message ids only, from `messagesAdded`, `labelsAdded` and
    `labelsRemoved` together. A removal is not a separate kind of event to the
    caller: every id here is refetched, and a refetched message carries its
    *current* labels, which is what deciding thread membership needs whether a
    label was just added or just taken away.

    `historyTypes` is deliberately not passed, so Gmail returns every type.
    Narrowing it would silently drop the label transitions above.

    Raises `StaleHistoryId` when Gmail answers 404, which is its documented way
    of saying the cursor has aged out.
    """
    try:
        response = _as_dict(
            _gmail_service()
            .users()
            .history()
            .list(userId="me", startHistoryId=start_history_id, pageToken=page_token)
            .execute()
        )
    except HttpError as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            raise StaleHistoryId(start_history_id) from exc
        raise

    message_ids: list[str] = []
    seen: set[str] = set()
    for record in _as_list_of_dicts(response.get("history")):
        for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
            for entry in _as_list_of_dicts(record.get(key)):
                message = _as_dict(entry.get("message"))
                message_id = str(message.get("id") or "")
                # Deduped here rather than by the caller: one history page
                # routinely carries the same message three times over - added,
                # then labelled, then unlabelled - and each duplicate would
                # otherwise cost a full message fetch.
                if message_id and message_id not in seen:
                    seen.add(message_id)
                    message_ids.append(message_id)

    return HistoryPage(
        message_ids=tuple(message_ids),
        next_page_token=str(response.get("nextPageToken") or "") or None,
        history_id=str(response.get("historyId") or "") or None,
    )


def _epoch_millis_to_utc(value: Any) -> datetime | None:
    """Gmail sends watch expiry as epoch milliseconds in a string."""
    try:
        millis = int(str(value))
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def list_thread_ids_by_label(label_id: str, max_results: int = 500) -> set[str]:
    response = _as_dict(_gmail_service().users().messages().list(
        userId="me", labelIds=[label_id], maxResults=min(max_results, 500),
    ).execute())
    if response.get("nextPageToken"):
        raise RuntimeError("Label membership scan incomplete; retaining existing tracking until a complete scan succeeds")
    return {str(m["threadId"]) for m in _as_list_of_dicts(response.get("messages")) if m.get("threadId")}


def list_unread_thread_ids(max_results: int = 500) -> set[str]:
    """Cheap: one list() call, no per-message get(). Thread ids of unread inbox mail.

    A message's threadId comes back from list() for free, no format="full" get()
    needed, so callers can intersect against known sent-thread ids for an accurate
    "unread replies to threads I sent" count without the per-message fetch cost
    that makes the full sync (list_unread_candidates_by_query) slow.

    ponytail: bounded to max_results (Gmail's own list() page-size ceiling is 500),
    not exhaustive for accounts with more unread inbox mail than that. Good enough
    for a live poll; the full sync's per-thread scan has no such bound.
    """
    service = _gmail_service()
    response = _as_dict(
        service.users().messages().list(userId="me", q="is:unread in:inbox", maxResults=max_results).execute()
    )
    messages = _as_list_of_dicts(response.get("messages"))
    return {
        str(message["threadId"])
        for message in messages
        if isinstance(message.get("threadId"), str) and message["threadId"]
    }


def list_thread_messages(thread_id: str) -> list[GmailMessageCandidate]:
    """Fetch every message in a known Gmail thread, regardless of read/unread state.

    Unlike list_unread_candidates_by_query, this doesn't depend on the UNREAD label,
    so it can still find a reply after it's been opened/read in Gmail (e.g. because
    the user viewed the thread directly) before a sync ran.
    """
    service = _gmail_service()
    try:
        thread = _as_dict(service.users().threads().get(userId="me", id=thread_id, format="full").execute())
    except HttpError as exc:
        if getattr(exc.resp, "status", None) == 404:
            return []
        raise
    messages = _as_list_of_dicts(thread.get("messages"))
    results: list[GmailMessageCandidate] = []
    for message in messages:
        candidate = _message_details_to_candidate(message)
        if candidate is not None:
            results.append(candidate)
    return results


def get_message_rfc_message_id(message_id: str) -> str:
    service = _gmail_service()
    details_raw = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Message-ID"],
        )
        .execute()
    )
    details = _as_dict(details_raw)
    payload = _as_dict(details.get("payload"))
    header_items = _as_list_of_dicts(payload.get("headers"))
    headers: list[dict[str, str]] = [
        {
            "name": str(item.get("name", "")),
            "value": str(item.get("value", "")),
        }
        for item in header_items
    ]
    return _get_header(headers, "Message-ID")


def get_message_thread_id(message_id: str) -> str:
    service = _gmail_service()
    details = _as_dict(service.users().messages().get(userId="me", id=message_id, format="minimal").execute())
    thread_id = details.get("threadId")
    return thread_id if isinstance(thread_id, str) else ""


def _resolve_mail_attachments(
    attachments: list[MailAttachment] | None,
    attachment_path: str | None,
    attachment_display_name: str | None,
) -> list[MailAttachment]:
    resolved = list(attachments or [])
    if attachment_path:
        resolved.append(MailAttachment(path=attachment_path, display_name=attachment_display_name))
    return resolved


def _add_mail_attachments(message: EmailMessage, attachments: list[MailAttachment]) -> None:
    for attachment in attachments:
        file_path = Path(attachment.path)
        safe_name = (attachment.display_name or "").strip() or file_path.name
        if not file_path.exists():
            raise FileNotFoundError(f"Attachment file missing on disk: {safe_name}")
        content = file_path.read_bytes()
        mime_type = attachment.mime_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        main_type, sub_type = mime_type.split("/", 1)
        message.add_attachment(content, maintype=main_type, subtype=sub_type, filename=safe_name)


def _append_tracking_pixel(html_body: str, tracking_pixel_url: str | None) -> str:
    if not tracking_pixel_url:
        return html_body
    url = html.escape(tracking_pixel_url, quote=True)
    return (
        f'{html_body}<img src="{url}" width="1" height="1" alt="" '
        'style="display:none;border:0;outline:none" />'
    )


def _append_variant_token(html_body: str, variant_token: str | None) -> str:
    """Stamp the resume variant marker into the HTML part, hidden from the reader.

    Which resume went to which recruiter is already recorded server-side, but that
    record cannot be reached from the outside: when a recruiter phones about "the
    resume you sent", the only shared handle is the message itself. The marker
    gives that message a handle the user can search for or quote back.

    Hidden rather than removed: it rides along with quotes and forwards, so it
    survives the reply chain. It goes in the HTML alternative only - the plain text
    part stays clean.
    """
    if not variant_token:
        return html_body
    token = html.escape(variant_token, quote=True)
    return (
        f'{html_body}<span style="display:none;font-size:0;line-height:0;'
        f'max-height:0;overflow:hidden;opacity:0" aria-hidden="true">{token}</span>'
    )


def send_reply_with_attachment(
    thread_id: str,
    to: str,
    cc: str | None,
    subject: str,
    body: str,
    attachment_path: str | None = None,
    attachment_display_name: str | None = None,
    draft_text_size: str = "normal",
    attachments: list[MailAttachment] | None = None,
    tracking_pixel_url: str | None = None,
    variant_token: str | None = None,
) -> str:
    service = _gmail_service()
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    plain_body = body or ""
    message.set_content(plain_body)
    try:
        html_body = _append_variant_token(
            _append_tracking_pixel(
                draft_text_to_html(plain_body, draft_text_size=draft_text_size),
                tracking_pixel_url,
            ),
            variant_token,
        )
        message.add_alternative(html_body, subtype="html")
    except Exception:
        # Fallback to plain text if HTML rendering fails.
        pass

    _add_mail_attachments(message, _resolve_mail_attachments(attachments, attachment_path, attachment_display_name))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload = {"raw": raw, "threadId": thread_id}
    response = _as_dict(service.users().messages().send(userId="me", body=payload).execute())
    message_id = response.get("id")
    return message_id if isinstance(message_id, str) else ""


def send_new_email_with_attachment(
    to: str,
    cc: str | None,
    subject: str,
    body: str,
    attachment_path: str | None = None,
    attachment_display_name: str | None = None,
    draft_text_size: str = "normal",
    attachments: list[MailAttachment] | None = None,
    tracking_pixel_url: str | None = None,
    variant_token: str | None = None,
) -> str:
    service = _gmail_service()
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    plain_body = body or ""
    message.set_content(plain_body)
    try:
        html_body = _append_variant_token(
            _append_tracking_pixel(
                draft_text_to_html(plain_body, draft_text_size=draft_text_size),
                tracking_pixel_url,
            ),
            variant_token,
        )
        message.add_alternative(html_body, subtype="html")
    except Exception:
        # Fallback to plain text if HTML rendering fails.
        pass

    _add_mail_attachments(message, _resolve_mail_attachments(attachments, attachment_path, attachment_display_name))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload = {"raw": raw}
    response = _as_dict(service.users().messages().send(userId="me", body=payload).execute())
    message_id = response.get("id")
    return message_id if isinstance(message_id, str) else ""


def mark_message_processed(message_id: str) -> None:
    service = _gmail_service()
    body: dict[str, Any] = {"removeLabelIds": ["UNREAD"]}
    if settings.gmail_label_filter:
        body["addLabelIds"] = [settings.gmail_label_filter]
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def mark_reply_processed(message_id: str, existing_label_ids: list[str] | None = None) -> None:
    label_id = ensure_gmail_labels(["CodeJob/Replied"]).get("CodeJob/Replied")
    if label_id:
        apply_gmail_label(message_id, label_id, existing_label_ids=existing_label_ids)
    mark_message_processed(message_id)


def list_gmail_labels() -> list[dict[str, str]]:
    service = _gmail_service()
    response = _as_dict(service.users().labels().list(userId="me").execute())
    labels = _as_list_of_dicts(response.get("labels"))
    results: list[dict[str, str]] = []
    for item in labels:
        label_id = item.get("id")
        name = item.get("name")
        if isinstance(label_id, str) and isinstance(name, str):
            results.append({"id": label_id, "name": name})
    return results


def ensure_gmail_labels(label_names: list[str]) -> dict[str, str]:
    service = _gmail_service()
    existing = list_gmail_labels()
    by_normalized: dict[str, dict[str, str]] = {row["name"].strip().lower(): row for row in existing}
    result: dict[str, str] = {}
    for display_name in label_names:
        normalized = display_name.strip().lower()
        if not normalized:
            continue
        existing_label = by_normalized.get(normalized)
        if existing_label:
            result[display_name] = existing_label["id"]
            continue
        created = _as_dict(
            service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": display_name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        created_id = created.get("id")
        if isinstance(created_id, str) and created_id:
            by_normalized[normalized] = {"id": created_id, "name": display_name}
            result[display_name] = created_id
    return result


def apply_gmail_label(
    message_id: str,
    label_id: str,
    *,
    existing_label_ids: list[str] | None = None,
) -> bool:
    if existing_label_ids and label_id in existing_label_ids:
        return False
    service = _gmail_service()
    service.users().messages().modify(userId="me", id=message_id, body={"addLabelIds": [label_id]}).execute()
    return True


GMAIL_STATE_NOT_CONFIGURED = "not_configured"
GMAIL_STATE_NOT_CONNECTED = "not_connected"
GMAIL_STATE_CONNECTED = "connected"
GMAIL_STATE_CONNECTED_REFRESHABLE = "connected_refreshable"
GMAIL_STATE_NEEDS_RECONNECT = "needs_reconnect"


def gmail_connection_state(owner_id: str | None = None) -> tuple[str, str]:
    """Five states, because two were not enough to be truthful.

    The old version reported `creds.valid`, which is False the moment the
    *access* token passes its hour - so Settings read "Not authenticated" for a
    perfectly healthy connection roughly 23 hours out of every 24, and the user
    reasonably read that as "reconnect me". A refreshable credential is
    connected; that is the whole point of holding a refresh token.

    `connected_refreshable` is kept distinct from `connected` rather than
    collapsed into it, because the two mean different things to an operator
    debugging a sync: one will work right now, the other will work after one
    extra round trip to Google.
    """
    if not is_gmail_configured():
        return GMAIL_STATE_NOT_CONFIGURED, "Missing Gmail OAuth configuration"

    if settings.feature_db_credentials_enabled:
        owner_id = owner_id or tenancy.owner_id()
        with session_scope() as db:
            # Adopt an existing token file here too, not only in
            # _load_credentials. Found by flipping the flag live: Settings said
            # "Not connected" on a perfectly working install until something
            # happened to make a Gmail call, which is the same class of lie A7
            # exists to remove. Idempotent, and a no-op once a row exists.
            if (
                # Same reasoning as the adoption guard in _load_credentials:
                # only the legacy owner may adopt the shared token file.
                owner_id == settings.owner_id
                and gmail_credential_service.get_row(db, owner_id) is None
            ):
                gmail_credential_service.import_legacy_token_file(db, owner_id)
            status = gmail_credential_service.connection_status(db, owner_id)
        if status.revoked:
            return GMAIL_STATE_NEEDS_RECONNECT, status.last_error or "Access was revoked. Reconnect Gmail."
        if not status.connected:
            return GMAIL_STATE_NOT_CONNECTED, "No Gmail account is connected yet."
        if status.expires_at and status.expires_at > datetime.now(UTC):
            return GMAIL_STATE_CONNECTED, "Ready"
        return GMAIL_STATE_CONNECTED_REFRESHABLE, "Ready - the access token refreshes on next use."

    token_path = Path(settings.google_token_path)
    if not token_path.exists():
        return GMAIL_STATE_NOT_CONNECTED, "Token file missing. First sync will trigger OAuth."
    try:
        _ = json.loads(token_path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.valid:
            return GMAIL_STATE_CONNECTED, "Ready"
        if creds.refresh_token:
            return GMAIL_STATE_CONNECTED_REFRESHABLE, "Ready - the access token refreshes on next use."
        return GMAIL_STATE_NEEDS_RECONNECT, "Stored token cannot be refreshed. Reconnect Gmail."
    except (ValueError, OSError, HttpError) as exc:
        return GMAIL_STATE_NEEDS_RECONNECT, f"Token read error: {exc}"


def gmail_auth_status(owner_id: str | None = None) -> tuple[bool, bool, str]:
    """Back-compatible shim over `gmail_connection_state`.

    `authenticated` now means "this app can act on the mailbox without asking
    the user for anything", which includes the refreshable case. Callers that
    only want a boolean keep working and stop lying.
    """
    state, detail = gmail_connection_state(owner_id)
    configured = state != GMAIL_STATE_NOT_CONFIGURED
    authenticated = state in (GMAIL_STATE_CONNECTED, GMAIL_STATE_CONNECTED_REFRESHABLE)
    return configured, authenticated, detail


def _extract_phone(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(?:\+?\d[\d\-\s()]{7,}\d)", text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _extract_name_from_sender(sender: str) -> str:
    if "<" in sender:
        return sender.split("<", 1)[0].strip().strip('"')
    return ""


def _extract_company_from_email(address: str) -> str:
    if "@" not in address:
        return ""
    domain = address.split("@", 1)[1].lower()
    for suffix in (".com", ".net", ".org", ".io", ".co", ".ai"):
        if domain.endswith(suffix):
            domain = domain[: -len(suffix)]
            break
    company = domain.split(".")[0].strip()
    return company.upper() if company else ""


def _infer_client_name(body: str) -> str:
    patterns = [
        r"\bclient\s*[:\-]\s*([A-Za-z0-9&., \-/]{2,80})",
        r"\bimplementation client\s*[:\-]\s*([A-Za-z0-9&., \-/]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, body, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,-")
    return ""


def build_tracking_sheet_row(
    *,
    role: str,
    sender: str,
    subject: str,
    body: str,
    to_email: str | None,
    cc_email: str | None,
) -> list[str]:
    recruiter_email = (to_email or "").strip()
    recruiter_name = _extract_name_from_sender(sender)
    recruiter_contact = _extract_phone(body)
    vendor = _extract_company_from_email(recruiter_email) if recruiter_email else ""
    prime_client = _extract_company_from_email(cc_email or "") if cc_email else ""
    client = _infer_client_name(body)

    position = role.strip() if role.strip() and role.strip() != "Unknown Role" else subject.strip()

    # Sheet columns:
    # A S.No, B Position, C Vendor, D Name, E Email, F Contact,
    # G Prime Vendor/Implementation Client, H Contact, I Email, J Client
    return [
        "",
        position,
        vendor,
        recruiter_name,
        recruiter_email,
        recruiter_contact,
        prime_client,
        "",
        (cc_email or "").strip(),
        client,
    ]


def append_tracking_sheet_row(
    *,
    role: str,
    sender: str,
    subject: str,
    body: str,
    to_email: str | None,
    cc_email: str | None,
) -> None:
    if not settings.google_sheets_tracking_enabled:
        return
    if not settings.google_sheets_tracking_spreadsheet_id:
        raise RuntimeError("Google Sheets tracking is enabled but spreadsheet id is missing")

    service = _sheets_service()
    tab_name = settings.google_sheets_tracking_tab_name or "Sheet1"
    row = build_tracking_sheet_row(
        role=role,
        sender=sender,
        subject=subject,
        body=body,
        to_email=to_email,
        cc_email=cc_email,
    )
    payload = {"values": [row]}
    service.spreadsheets().values().append(
        spreadsheetId=settings.google_sheets_tracking_spreadsheet_id,
        range=f"{tab_name}!A:J",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=payload,
    ).execute()


