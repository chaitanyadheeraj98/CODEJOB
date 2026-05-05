import base64
import json
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
    creds = flow.run_local_server(port=8080, open_browser=True)
    _ensure_token_parent()
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _gmail_service():
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict[str, Any]) -> str:
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []) or []:
        mime = part.get("mimeType", "")
        if mime in ("text/plain", "text/html"):
            chunk = part.get("body", {}).get("data")
            if chunk:
                return base64.urlsafe_b64decode(chunk).decode("utf-8", errors="ignore")
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


def list_unread_recruiter_candidates(max_results: int = 25) -> list[dict[str, str]]:
    service = _gmail_service()
    label_query = f" label:{settings.gmail_label_filter}" if settings.gmail_label_filter else ""
    query = (
        f"is:unread in:inbox ({label_query} "
        "recruiter OR recruiting OR talent OR hiring OR opportunity)"
    ).strip()
    query = " ".join(query.split())

    response = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = response.get("messages", [])
    results: list[dict[str, str]] = []

    for message in messages:
        message_id = message.get("id")
        if not message_id:
            continue
        details = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        payload = details.get("payload", {})
        headers = payload.get("headers", [])
        from_header = _get_header(headers, "From")
        subject = _get_header(headers, "Subject") or "(No Subject)"
        body = _decode_body(payload)
        results.append(
            {
                "external_message_id": message_id,
                "external_thread_id": details.get("threadId", ""),
                "sender": from_header,
                "recipient_email": _extract_email_address(from_header),
                "subject": subject,
                "body": body,
            }
        )
    return results


def send_reply(thread_id: str, to: str, subject: str, body: str) -> str:
    service = _gmail_service()
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    message.set_content(body)
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
