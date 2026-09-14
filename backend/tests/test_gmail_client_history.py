"""The three Gmail calls push delivery adds, and the shape of their replies.

Thin wrappers, so these tests are about Gmail's contract rather than ours: a
watch expiry arrives as a string of epoch milliseconds, one history page
routinely names the same message three times, and a cursor that has aged out
comes back as a 404 that no amount of retrying will fix.
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from googleapiclient.errors import HttpError

from app import gmail_client

TOPIC = "projects/codejob-prod/topics/gmail-mailbox-events"


class _Call:
    """Stands in for the fluent googleapiclient chain, recording what it got."""

    def __init__(self, result):
        self._result = result
        self.kwargs: dict = {}
        self.body: dict = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        self.body = kwargs.get("body", {})
        return self

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Users:
    def __init__(self, **calls):
        for name, call in calls.items():
            setattr(self, f"_{name}", call)

    def watch(self, **kwargs):
        return self._watch(**kwargs)

    def stop(self, **kwargs):
        return self._stop(**kwargs)

    def history(self):
        return self

    def list(self, **kwargs):
        return self._history(**kwargs)


def _service(**calls):
    users = _Users(**calls)
    return type("Service", (), {"users": staticmethod(lambda: users)})()


def _http_error(status: int) -> HttpError:
    response = type("Response", (), {"status": status, "reason": "gone"})()
    return HttpError(response, b"{}")


class WatchTests(unittest.TestCase):
    def test_it_registers_against_the_topic_it_is_given(self):
        call = _Call({"historyId": "12345", "expiration": "1789123456000"})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(watch=call)):
            gmail_client.watch_mailbox(TOPIC)

        self.assertEqual(call.body, {"topicName": TOPIC})
        self.assertEqual(call.kwargs["userId"], "me")

    def test_it_does_not_filter_by_label(self):
        """Watching INBOX only would be cheaper and would miss replies sent
        from Gmail itself and the label transitions tracked threads need."""
        call = _Call({"historyId": "12345", "expiration": "1789123456000"})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(watch=call)):
            gmail_client.watch_mailbox(TOPIC)

        self.assertNotIn("labelIds", call.body)
        self.assertNotIn("labelFilterAction", call.body)

    def test_the_expiry_arrives_as_epoch_milliseconds(self):
        call = _Call({"historyId": "12345", "expiration": "1789123456000"})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(watch=call)):
            watch = gmail_client.watch_mailbox(TOPIC)

        self.assertEqual(watch.history_id, "12345")
        self.assertEqual(
            watch.expiration_at, datetime.fromtimestamp(1789123456, tz=UTC)
        )

    def test_a_missing_expiry_is_none_rather_than_1970(self):
        """`datetime.fromtimestamp(0)` would read as an expiry half a century
        ago, which every "is this watch alive" check would believe."""
        call = _Call({"historyId": "12345"})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(watch=call)):
            watch = gmail_client.watch_mailbox(TOPIC)

        self.assertIsNone(watch.expiration_at)

    def test_the_history_id_stays_a_string(self):
        """Gmail documents it as opaque. Parsing it to an int here is where a
        dependency on its current format would start."""
        call = _Call({"historyId": 12345, "expiration": "1789123456000"})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(watch=call)):
            watch = gmail_client.watch_mailbox(TOPIC)

        self.assertEqual(watch.history_id, "12345")

    def test_stopping_asks_for_this_mailbox(self):
        call = _Call({})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(stop=call)):
            gmail_client.stop_mailbox_watch()

        self.assertEqual(call.kwargs["userId"], "me")


class HistoryTests(unittest.TestCase):
    def _page(self, payload):
        call = _Call(payload)
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(history=call)):
            return gmail_client.list_history("100"), call

    def test_it_collects_ids_from_all_three_change_types(self):
        page, _ = self._page({
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}]},
                {"labelsAdded": [{"message": {"id": "m2"}}]},
                {"labelsRemoved": [{"message": {"id": "m3"}}]},
            ],
            "historyId": "140",
        })

        self.assertEqual(set(page.message_ids), {"m1", "m2", "m3"})

    def test_one_message_touched_three_times_is_fetched_once(self):
        """Added, then labelled, then unlabelled - all in one page. Each
        duplicate would otherwise cost a full message fetch."""
        page, _ = self._page({
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}]},
                {"labelsAdded": [{"message": {"id": "m1"}}]},
                {"labelsRemoved": [{"message": {"id": "m1"}}]},
            ],
        })

        self.assertEqual(page.message_ids, ("m1",))

    def test_it_asks_gmail_for_every_history_type(self):
        """Passing `historyTypes` would quietly drop label transitions."""
        _, call = self._page({"history": []})

        self.assertNotIn("historyTypes", call.kwargs)
        self.assertEqual(call.kwargs["startHistoryId"], "100")

    def test_it_reports_the_next_page_and_the_terminal_cursor(self):
        page, _ = self._page({"history": [], "nextPageToken": "tok", "historyId": "140"})

        self.assertEqual(page.next_page_token, "tok")
        self.assertEqual(page.history_id, "140")

    def test_an_absent_page_token_is_none_not_an_empty_string(self):
        """The caller loops `while token`; "" would be falsy by luck rather
        than by contract."""
        page, _ = self._page({"history": []})

        self.assertIsNone(page.next_page_token)

    def test_an_empty_mailbox_change_yields_nothing_and_does_not_raise(self):
        page, _ = self._page({"historyId": "140"})

        self.assertEqual(page.message_ids, ())

    def test_a_page_token_is_passed_through(self):
        call = _Call({"history": []})
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(history=call)):
            gmail_client.list_history("100", "tok")

        self.assertEqual(call.kwargs["pageToken"], "tok")

    def test_a_dead_cursor_raises_something_the_caller_can_recognise(self):
        """404 is Gmail saying the record is gone. Retrying cannot fix it, so
        it must not look like the transient failures that retrying does fix."""
        call = _Call(_http_error(404))
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(history=call)):
            with self.assertRaises(gmail_client.StaleHistoryId):
                gmail_client.list_history("100")

    def test_a_transient_failure_is_not_mistaken_for_a_dead_cursor(self):
        """A 503 recovers on its own. Treating it as a stale cursor would
        trigger a full recovery scan and re-baseline the watch for nothing."""
        call = _Call(_http_error(503))
        with patch.object(gmail_client, "_gmail_service", lambda *a, **k: _service(history=call)):
            with self.assertRaises(HttpError):
                gmail_client.list_history("100")


if __name__ == "__main__":
    unittest.main()
