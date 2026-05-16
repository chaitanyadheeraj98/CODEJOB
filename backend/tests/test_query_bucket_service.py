import unittest

from app.query_bucket.service import sanitize_saved_queries


class QueryBucketServiceTests(unittest.TestCase):
    def test_sanitize_saved_queries_trims_and_dedupes(self) -> None:
        result = sanitize_saved_queries(["  is:unread ", "IS:UNREAD", " tx is:unread "])
        self.assertEqual(result, ["is:unread", "tx is:unread"])

    def test_sanitize_saved_queries_ignores_invalid_entries(self) -> None:
        result = sanitize_saved_queries(["", "   ", 42, None, "is:unread"])  # type: ignore[list-item]
        self.assertEqual(result, ["is:unread"])

    def test_sanitize_saved_queries_enforces_limit(self) -> None:
        values = [f"q{i}" for i in range(20)]
        result = sanitize_saved_queries(values)
        self.assertEqual(len(result), 10)


if __name__ == "__main__":
    unittest.main()
