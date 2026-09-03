import base64
import unittest

from googleapiclient.errors import HttpError

from app import gmail_client


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _message_details(message_id: str, subject: str) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1700000000000",
        "snippet": subject,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Recruiter <r@example.com>"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _b64(f"Body for {subject}")},
        },
    }


class _FakeGetCall:
    def __init__(self, response: dict[str, object] | None, error: Exception | None) -> None:
        self._response = response
        self._error = error

    def execute(self) -> dict[str, object]:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeMessages:
    def __init__(self, responses: dict[str, dict[str, object]], errors: dict[str, Exception], calls: list[str]) -> None:
        self._responses = responses
        self._errors = errors
        self._calls = calls

    def get(self, *, userId: str, id: str, format: str) -> _FakeGetCall:  # noqa: A002
        self._calls.append(id)
        return _FakeGetCall(self._responses.get(id), self._errors.get(id))


class _FakeUsers:
    def __init__(self, responses: dict[str, dict[str, object]], errors: dict[str, Exception], calls: list[str]) -> None:
        self._responses = responses
        self._errors = errors
        self._calls = calls

    def messages(self) -> _FakeMessages:
        return _FakeMessages(self._responses, self._errors, self._calls)


class _FakeService:
    def __init__(self, responses: dict[str, dict[str, object]], errors: dict[str, Exception], calls: list[str]) -> None:
        self._responses = responses
        self._errors = errors
        self._calls = calls

    def users(self) -> _FakeUsers:
        return _FakeUsers(self._responses, self._errors, self._calls)


class GetCandidatesByMessageIdsTests(unittest.TestCase):
    def test_fetches_each_message_by_id_regardless_of_unread_state(self) -> None:
        calls: list[str] = []
        responses = {
            "m1": _message_details("m1", "Java Developer - Texas"),
            "m2": _message_details("m2", "Python Developer - Remote"),
        }
        original = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService(responses, {}, calls)
            candidates = gmail_client.get_candidates_by_message_ids(["m1", "m2"])
        finally:
            gmail_client._gmail_service = original

        self.assertEqual(calls, ["m1", "m2"])
        self.assertEqual([c["external_message_id"] for c in candidates], ["m1", "m2"])
        self.assertEqual(candidates[0]["subject"], "Java Developer - Texas")

    def test_skips_message_that_fails_to_fetch_instead_of_raising(self) -> None:
        calls: list[str] = []
        responses = {"m1": _message_details("m1", "Java Developer - Texas")}
        errors = {"m2": HttpError(resp=type("R", (), {"status": 404, "reason": "Not Found"})(), content=b"not found")}
        original = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService(responses, errors, calls)
            candidates = gmail_client.get_candidates_by_message_ids(["m1", "m2"])
        finally:
            gmail_client._gmail_service = original

        self.assertEqual(calls, ["m1", "m2"])
        self.assertEqual([c["external_message_id"] for c in candidates], ["m1"])


if __name__ == "__main__":
    unittest.main()
