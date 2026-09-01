import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import UserSettings


class FilterVisibilitySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with Session(self.engine) as db:
            db.add(UserSettings(
                owner_id=main.settings.owner_id,
                gmail_query="keep-me",
                default_gmail_query="keep-me",
            ))
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_fresh_settings_and_full_put_round_trip_visibility(self) -> None:
        self.assertEqual(self.client.get("/settings").json()["visible_filters"], {})
        payload = {"visible_filters": {"needs_review": ["role", "location"]}}
        response = self.client.put("/settings", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/settings").json()["visible_filters"], payload["visible_filters"])
        self.assertEqual(self.client.get("/settings/bootstrap").json()["settings"]["visible_filters"], payload["visible_filters"])

    def test_omitted_full_put_field_preserves_saved_visibility(self) -> None:
        saved = {"needs_review": ["role"]}
        self.client.put("/settings/visible-filters", json={"visible_filters": saved})
        response = self.client.put("/settings", json={"gmail_query": "changed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["visible_filters"], saved)

    def test_narrow_put_preserves_other_settings_and_empty_array(self) -> None:
        response = self.client.put(
            "/settings/visible-filters",
            json={"visible_filters": {"needs_review": []}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["gmail_query"], "keep-me")
        self.assertEqual(response.json()["visible_filters"], {"needs_review": []})

    def test_visibility_payload_size_limits(self) -> None:
        too_many_dashboards = {f"page-{index}": [] for index in range(41)}
        too_many_fields = {"needs_review": [f"field-{index}" for index in range(61)]}
        for visible_filters in (too_many_dashboards, too_many_fields):
            response = self.client.put(
                "/settings/visible-filters",
                json={"visible_filters": visible_filters},
            )
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
