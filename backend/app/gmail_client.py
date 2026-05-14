# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import base64
import json
import logging
import mimetypes
import re
import threading
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, TypedDict, cast

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.ai.draft_formatting import draft_text_to_html
from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send"]
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
    sender: str
    recipient_email: str
    subject: str
    body: str
    snippet: str
    gmail_received_at: datetime | None


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


def _credentials_payload() -> dict[str, Any]:
    return {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _ensure_token_parent() -> None:
    token_path = Path(settings.google_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)


def _load_credentials() -> Credentials:
    if not is_gmail_configured():
        raise RuntimeError("Gmail OAuth is not configured")

    token_path = Path(settings.google_token_path)
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request

        creds.refresh(Request())
        _ensure_token_parent()
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    global _oauth_last_authorization_url
    flow = InstalledAppFlow.from_client_config(_credentials_payload(), SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
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
            host="localhost",
            bind_addr="0.0.0.0",
            port=8080,
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
    flow.redirect_uri = settings.google_redirect_uri
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
            host="localhost",
            bind_addr="0.0.0.0",
            port=8080,
            open_browser=False,
            authorization_prompt_message="Please visit this URL to authorize this application: {url}",
            state=auth_state,
            **extra_auth_kwargs,
        ),
    )
    _ensure_token_parent()
    token_path = Path(settings.google_token_path)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _gmail_service() -> Any:
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds)


def _sheets_service() -> Any:
    creds = _load_credentials()
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
    no_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    no_tags = re.sub(r"(?is)<[^>]+>", " ", no_scripts)
    compact = re.sub(r"[ \t]+", " ", no_tags)
    return re.sub(r"\n\s*\n+", "\n\n", compact).strip()


def _decode_body(payload: dict[str, Any]) -> str:
    direct = _decode_chunk(payload.get("body", {}).get("data"))
    if direct:
        return direct

    plain, html = _extract_from_parts(_as_list_of_dicts(payload.get("parts")))
    if plain:
        return plain
    if html:
        return _strip_html(html)
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


def list_unread_candidates_by_query(query: str, max_results_per_page: int = 100) -> list[GmailMessageCandidate]:
    service = _gmail_service()
    page_token: str | None = None
    results: list[GmailMessageCandidate] = []

    while True:
        req = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results_per_page,
            pageToken=page_token,
        )
        response = _as_dict(req.execute())
        messages = _as_list_of_dicts(response.get("messages"))
        for message in messages:
            message_id = message.get("id")
            if not isinstance(message_id, str) or not message_id:
                continue
            details = _as_dict(service.users().messages().get(userId="me", id=message_id, format="full").execute())
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
            subject = _get_header(headers, "Subject") or "(No Subject)"
            rfc_message_id = _get_header(headers, "Message-ID")
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
            results.append(
                {
                    "external_message_id": message_id,
                    "external_thread_id": str(details.get("threadId", "")),
                    "external_rfc_message_id": rfc_message_id,
                    "sender": from_header,
                    "recipient_email": _extract_email_address(from_header),
                    "subject": subject,
                    "body": body,
                    "snippet": snippet,
                    "gmail_received_at": gmail_received_at,
                }
            )

        next_token_raw = response.get("nextPageToken")
        next_token = next_token_raw if isinstance(next_token_raw, str) and next_token_raw else None
        if not next_token:
            break
        page_token = next_token
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


def send_reply_with_attachment(
    thread_id: str,
    to: str,
    cc: str | None,
    subject: str,
    body: str,
    attachment_path: str | None = None,
    attachment_display_name: str | None = None,
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
        html_body = draft_text_to_html(plain_body)
        message.add_alternative(html_body, subtype="html")
    except Exception:
        # Fallback to plain text if HTML rendering fails.
        pass

    if attachment_path:
        file_path = Path(attachment_path)
        if file_path.exists():
            content = file_path.read_bytes()
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            main_type, sub_type = mime_type.split("/", 1)
            safe_name = (attachment_display_name or "").strip() or file_path.name
            message.add_attachment(content, maintype=main_type, subtype=sub_type, filename=safe_name)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload = {"raw": raw, "threadId": thread_id}
    response = _as_dict(service.users().messages().send(userId="me", body=payload).execute())
    message_id = response.get("id")
    return message_id if isinstance(message_id, str) else ""


def mark_message_processed(message_id: str) -> None:
    service = _gmail_service()
    body: dict[str, Any] = {"removeLabelIds": ["UNREAD"]}
    if settings.gmail_label_filter:
        body["addLabelIds"] = [settings.gmail_label_filter]
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def gmail_auth_status() -> tuple[bool, bool, str]:
    if not is_gmail_configured():
        return False, False, "Missing Gmail OAuth configuration"
    token_path = Path(settings.google_token_path)
    if not token_path.exists():
        return True, False, "Token file missing. First sync will trigger OAuth."
    try:
        _ = json.loads(token_path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.valid:
            return True, True, "Ready"
        return True, False, "Token exists but is not valid yet"
    except (ValueError, OSError, HttpError) as exc:
        return True, False, f"Token read error: {exc}"


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


