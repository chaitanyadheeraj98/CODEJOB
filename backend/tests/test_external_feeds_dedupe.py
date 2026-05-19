import unittest
from datetime import UTC, datetime

from app.external_feeds.dedupe import build_dedupe_hash


class ExternalFeedsDedupeTests(unittest.TestCase):
    def test_same_identity_and_content_hash_matches(self) -> None:
        ts = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
        a = build_dedupe_hash(
            recruiter_phone="+1 214 555 1212",
            recruiter_email="r@example.com",
            role="Java Developer",
            location="Dallas, TX",
            posted_at=ts,
            raw_body="Need Java Developer immediately",
        )
        b = build_dedupe_hash(
            recruiter_phone="12145551212",
            recruiter_email="R@example.com",
            role="  Java   Developer ",
            location="Dallas, TX",
            posted_at=ts,
            raw_body="Need Java Developer immediately",
        )
        self.assertEqual(a, b)

    def test_different_recruiter_changes_hash(self) -> None:
        ts = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
        a = build_dedupe_hash(
            recruiter_phone="+1 214 555 1212",
            recruiter_email="r1@example.com",
            role="Java Developer",
            location="Dallas, TX",
            posted_at=ts,
            raw_body="Need Java Developer immediately",
        )
        b = build_dedupe_hash(
            recruiter_phone="+1 972 555 1212",
            recruiter_email="r2@example.com",
            role="Java Developer",
            location="Dallas, TX",
            posted_at=ts,
            raw_body="Need Java Developer immediately",
        )
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
