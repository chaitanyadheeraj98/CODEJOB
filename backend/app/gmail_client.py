import base64
import json
import mimetypes
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send"]


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

    flow = InstalledAppFlow.from_client_config(_credentials_payload(), SCOPES)
    # In Docker, there is no local browser in-container; user opens the printed URL manually.
    extra_auth_kwargs: dict[str, str] = {"prompt": "select_account"}
    if settings.google_login_hint:
        extra_auth_kwargs["login_hint"] = settings.google_login_hint

    creds = flow.run_local_server(
        host="localhost",
        bind_addr="0.0.0.0",
        port=8080,
        open_browser=False,
        authorization_prompt_message="Please visit this URL to authorize this application: {url}",
        **extra_auth_kwargs,
    )
    _ensure_token_parent()
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _gmail_service():
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds)


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

        nested_parts = part.get("parts") or []
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

    plain, html = _extract_from_parts(payload.get("parts", []) or [])
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


def list_unread_candidates_by_query(query: str, max_results_per_page: int = 100) -> list[dict[str, str]]:
    service = _gmail_service()
    page_token: str | None = None
    results: list[dict[str, str]] = []

    while True:
        req = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results_per_page,
            pageToken=page_token,
        )
        response = req.execute()
        messages = response.get("messages", [])
        for message in messages:
            message_id = message.get("id")
            if not message_id:
                continue
            details = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            payload = details.get("payload", {})
            headers = payload.get("headers", [])
            from_header = _get_header(headers, "From")
            subject = _get_header(headers, "Subject") or "(No Subject)"
            body = _decode_body(payload)
            snippet = (details.get("snippet") or "").strip()
            if not body.strip() and snippet:
                body = snippet
            results.append(
                {
                    "external_message_id": message_id,
                    "external_thread_id": details.get("threadId", ""),
                    "sender": from_header,
                    "recipient_email": _extract_email_address(from_header),
                    "subject": subject,
                    "body": body,
                    "snippet": snippet,
                }
            )

        next_token = response.get("nextPageToken")
        if not next_token:
            break
        page_token = next_token
    return results


def send_reply_with_attachment(
    thread_id: str,
    to: str,
    cc: str | None,
    subject: str,
    body: str,
    attachment_path: str | None = None,
) -> str:
    service = _gmail_service()
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    message.set_content(body)

    if attachment_path:
        file_path = Path(attachment_path)
        if file_path.exists():
            content = file_path.read_bytes()
            mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            main_type, sub_type = mime_type.split("/", 1)
            message.add_attachment(content, maintype=main_type, subtype=sub_type, filename=file_path.name)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload = {"raw": raw, "threadId": thread_id}
    response = service.users().messages().send(userId="me", body=payload).execute()
    return response.get("id", "")


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
