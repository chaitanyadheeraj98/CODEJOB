import unittest
import uuid
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db import SessionLocal
from app.main import productivity_trend
from app.models import ProductivityEvent


class ProductivityTrendTimezoneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = SessionLocal()
        self.original_owner_id = settings.owner_id
        self.test_owner_id = f"trend-test-{uuid.uuid4()}"
        settings.owner_id = self.test_owner_id

    def tearDown(self) -> None:
        self.db.query(ProductivityEvent).filter(ProductivityEvent.owner_id == self.test_owner_id).delete()
        self.db.commit()
        settings.owner_id = self.original_owner_id
        self.db.close()

    def _insert_sent_event(self, occurred_at: datetime, source: str) -> None:
        self.db.add(
            ProductivityEvent(
                owner_id=self.test_owner_id,
                event_type="approved_sent",
                event_source=source,
                entity_id=None,
                weight=4.0,
                metadata_json="{}",
                occurred_at=occurred_at,
            )
        )
        self.db.commit()

    def test_trend_counts_naive_current_day_event(self) -> None:
        event_time_naive = (datetime.now(UTC) - timedelta(minutes=20)).replace(tzinfo=None, second=0, microsecond=0)
        self._insert_sent_event(event_time_naive, "test_naive")

        trend = productivity_trend(range="current_day", db=self.db)

        self.assertGreaterEqual(trend.kpi_total_sent, 1)
        self.assertTrue(any(point.sent_count > 0 for point in trend.bars))

    def test_trend_counts_aware_current_day_event(self) -> None:
        event_time_aware = (datetime.now(UTC) - timedelta(minutes=10)).replace(second=0, microsecond=0)
        self._insert_sent_event(event_time_aware, "test_aware")

        trend = productivity_trend(range="current_day", db=self.db)

        self.assertGreaterEqual(trend.kpi_total_sent, 1)
        self.assertTrue(any(point.sent_count > 0 for point in trend.bars))

    def test_naive_and_aware_events_match_total(self) -> None:
        base_time = datetime.now(UTC).replace(second=0, microsecond=0)
        self._insert_sent_event((base_time - timedelta(minutes=35)).replace(tzinfo=None), "test_naive_compare")
        self._insert_sent_event(base_time - timedelta(minutes=25), "test_aware_compare")

        trend = productivity_trend(range="current_day", db=self.db)

        self.assertGreaterEqual(trend.kpi_total_sent, 2)
        self.assertEqual(sum(point.sent_count for point in trend.bars), trend.kpi_total_sent)


if __name__ == "__main__":
    unittest.main()
