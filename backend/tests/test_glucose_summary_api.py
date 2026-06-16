from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.main import app, get_db
from app.models.glucose_model import GlucoseRecordModel


class GlucoseSummaryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        Base.metadata.create_all(bind=self.engine)

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_get_glucose_summary_returns_read_only_trend_summary(self) -> None:
        now = datetime.now()
        db = self.session_local()
        try:
            db.add_all(
                [
                    GlucoseRecordModel(
                        user_id=1,
                        value=5.8,
                        measure_time=(now - timedelta(days=2)).isoformat(),
                        measure_type="fasting",
                        source="manual",
                        created_at=now.isoformat(),
                    ),
                    GlucoseRecordModel(
                        user_id=1,
                        value=11.2,
                        measure_time=(now - timedelta(days=1)).isoformat(),
                        measure_type="post_meal",
                        source="device",
                        created_at=now.isoformat(),
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

        response = self.client.get("/api/glucose/summary?user_id=1&days=7")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "生成血糖总结成功")
        self.assertEqual(payload["data"]["count"], 2)
        self.assertEqual(payload["data"]["target"], {"min": 3.9, "max": 10.0})
        self.assertIn("近7日共记录 2 次血糖", payload["data"]["summary"])
        self.assertIn("高于目标范围 1 次", payload["data"]["summary"])

    def test_get_glucose_summary_handles_empty_records(self) -> None:
        response = self.client.get("/api/glucose/summary?user_id=1&days=7")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["data"]["count"], 0)
        self.assertEqual(payload["data"]["summary"], "近7日暂无血糖记录，暂时无法生成趋势总结。")

    def test_get_glucose_summary_rejects_non_demo_user(self) -> None:
        response = self.client.get("/api/glucose/summary?user_id=2&days=7")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "只能访问当前演示用户的血糖总结")


if __name__ == "__main__":
    unittest.main()
