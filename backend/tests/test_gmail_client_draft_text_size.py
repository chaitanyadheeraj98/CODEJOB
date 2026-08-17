import base64
import os
import tempfile
import unittest
from email import message_from_bytes

from app import gmail_client


class _FakeSendCall:
    def __init__(self, store: dict[str, object], body: dict[str, object]) -> None:
        self._store = store
        self._body = body

    def execute(self) -> dict[str, str]:
        self._store["body"] = self._body
        return {"id": "sent-123"}


class _FakeMessages:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def send(self, *, userId: str, body: dict[str, object]) -> _FakeSendCall:
        self._store["userId"] = userId
        return _FakeSendCall(self._store, body)


class _FakeUsers:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def messages(self) -> _FakeMessages:
        return _FakeMessages(self._store)


class _FakeService:
    def __init__(self, store: dict[str, object]) -> None:
        self._store = store

    def users(self) -> _FakeUsers:
        return _FakeUsers(self._store)


class GmailClientDraftTextSizeTests(unittest.TestCase):
    def test_send_reply_adds_sized_html_alternative(self) -> None:
        store: dict[str, object] = {}
        original_gmail_service = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService(store)
            sent_id = gmail_client.send_reply_with_attachment(
                "thread-123",
                "to@example.com",
                "cc@example.com",
                "Subject",
                "Hello\n\n- Java",
                draft_text_size="huge",
            )
        finally:
            gmail_client._gmail_service = original_gmail_service

        self.assertEqual(sent_id, "sent-123")
        payload = store["body"]
        assert isinstance(payload, dict)
        raw = payload["raw"]
        assert isinstance(raw, str)
        parsed = message_from_bytes(base64.urlsafe_b64decode(raw.encode("utf-8")))
        html_part = next(part for part in parsed.walk() if part.get_content_type() == "text/html")
        plain_part = next(part for part in parsed.walk() if part.get_content_type() == "text/plain")
        html_body = html_part.get_payload(decode=True).decode("utf-8")
        plain_body = plain_part.get_payload(decode=True).decode("utf-8")
        self.assertIn("font-size:28px;line-height:1.4;", html_body)
        self.assertIn("<ul><li>Java</li></ul>", html_body)
        self.assertIn("Hello", plain_body)

    def test_send_new_email_includes_multiple_attachments(self) -> None:
        store: dict[str, object] = {}
        original_gmail_service = gmail_client._gmail_service
        first_fd, first_path = tempfile.mkstemp(suffix=".txt")
        second_fd, second_path = tempfile.mkstemp(suffix=".pdf")
        os.close(first_fd)
        os.close(second_fd)
        try:
            with open(first_path, "wb") as handle:
                handle.write(b"first attachment")
            with open(second_path, "wb") as handle:
                handle.write(b"%PDF-1.4 second")
            gmail_client._gmail_service = lambda: _FakeService(store)
            sent_id = gmail_client.send_new_email_with_attachment(
                "to@example.com",
                None,
                "Subject",
                "Hello",
                draft_text_size="normal",
                attachments=[
                    gmail_client.MailAttachment(path=first_path, display_name="first.txt", mime_type="text/plain"),
                    gmail_client.MailAttachment(path=second_path, display_name="second.pdf", mime_type="application/pdf"),
                ],
            )
        finally:
            gmail_client._gmail_service = original_gmail_service
            if os.path.exists(first_path):
                os.unlink(first_path)
            if os.path.exists(second_path):
                os.unlink(second_path)

        self.assertEqual(sent_id, "sent-123")
        payload = store["body"]
        assert isinstance(payload, dict)
        parsed = message_from_bytes(base64.urlsafe_b64decode(str(payload["raw"]).encode("utf-8")))
        attachment_names = sorted(
            part.get_filename()
            for part in parsed.walk()
            if part.get_content_disposition() == "attachment"
        )
        self.assertEqual(attachment_names, ["first.txt", "second.pdf"])

    def test_send_reply_raises_clear_error_when_attachment_is_missing(self) -> None:
        store: dict[str, object] = {}
        original_gmail_service = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService(store)
            with self.assertRaises(FileNotFoundError) as cm:
                gmail_client.send_reply_with_attachment(
                    "thread-123",
                    "to@example.com",
                    None,
                    "Subject",
                    "Hello",
                    attachments=[gmail_client.MailAttachment(path="Z:/missing/file.pdf", display_name="missing.pdf")],
                )
        finally:
            gmail_client._gmail_service = original_gmail_service

        self.assertIn("Attachment file missing on disk", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
