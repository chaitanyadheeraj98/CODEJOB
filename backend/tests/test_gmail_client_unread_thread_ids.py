import unittest

from app import gmail_client


class _FakeListCall:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def execute(self) -> dict[str, object]:
        return self._response


class _FakeMessages:
    def __init__(self, response: dict[str, object], store: dict[str, object]) -> None:
        self._response = response
        self._store = store

    def list(self, **kwargs: object) -> _FakeListCall:
        self._store["kwargs"] = kwargs
        return _FakeListCall(self._response)


class _FakeUsers:
    def __init__(self, response: dict[str, object], store: dict[str, object]) -> None:
        self._response = response
        self._store = store

    def messages(self) -> _FakeMessages:
        return _FakeMessages(self._response, self._store)


class _FakeService:
    def __init__(self, response: dict[str, object], store: dict[str, object]) -> None:
        self._response = response
        self._store = store

    def users(self) -> _FakeUsers:
        return _FakeUsers(self._response, self._store)


class GmailClientUnreadThreadIdsTests(unittest.TestCase):
    def test_list_unread_thread_ids_returns_dedup_thread_ids_and_queries_unread(self) -> None:
        store: dict[str, object] = {}
        response = {
            "messages": [
                {"id": "m1", "threadId": "t1"},
                {"id": "m2", "threadId": "t2"},
                {"id": "m3", "threadId": "t1"},
            ]
        }
        original_gmail_service = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService(response, store)
            thread_ids = gmail_client.list_unread_thread_ids()
        finally:
            gmail_client._gmail_service = original_gmail_service

        self.assertEqual(thread_ids, {"t1", "t2"})
        self.assertEqual(store["kwargs"]["q"], "is:unread in:inbox")
        self.assertEqual(store["kwargs"]["maxResults"], 500)

    def test_list_unread_thread_ids_empty_when_no_messages(self) -> None:
        store: dict[str, object] = {}
        original_gmail_service = gmail_client._gmail_service
        try:
            gmail_client._gmail_service = lambda: _FakeService({}, store)
            thread_ids = gmail_client.list_unread_thread_ids()
        finally:
            gmail_client._gmail_service = original_gmail_service

        self.assertEqual(thread_ids, set())


if __name__ == "__main__":
    unittest.main()
