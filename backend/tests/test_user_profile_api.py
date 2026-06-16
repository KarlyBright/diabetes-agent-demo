from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.main import app, get_db


class UserProfileApiTests(unittest.TestCase):
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

    def test_get_user_profile_returns_empty_profile_when_missing(self) -> None:
        response = self.client.get("/api/user/profile?user_id=1")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "获取用户资料成功")
        self.assertEqual(payload["data"]["user_id"], 1)
        self.assertEqual(payload["data"]["name"], None)
        self.assertEqual(payload["data"]["medications"], [])
        self.assertFalse(payload["data"]["profile_completed"])

    def test_post_user_profile_persists_and_reads_back_profile(self) -> None:
        save_response = self.client.post(
            "/api/user/profile",
            json={
                "user_id": 1,
                "name": "王三",
                "age": 35,
                "gender": "男",
                "diabetes_type": "2型糖尿病",
                "height": 175,
                "weight": 72,
                "medications": [" 二甲双胍 ", "阿卡波糖"],
            },
        )

        self.assertEqual(save_response.status_code, 200)
        save_payload = save_response.json()
        self.assertEqual(save_payload["message"], "用户资料保存成功")
        self.assertEqual(save_payload["data"]["name"], "王三")
        self.assertEqual(save_payload["data"]["age"], 35)
        self.assertEqual(save_payload["data"]["medications"], ["二甲双胍", "阿卡波糖"])
        self.assertTrue(save_payload["data"]["profile_completed"])

        get_response = self.client.get("/api/user/profile?user_id=1")
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        self.assertEqual(get_payload["data"]["name"], "王三")
        self.assertEqual(get_payload["data"]["diabetes_type"], "2型糖尿病")
        self.assertEqual(get_payload["data"]["medications"], ["二甲双胍", "阿卡波糖"])

    def test_post_user_profile_rejects_invalid_age(self) -> None:
        response = self.client.post(
            "/api/user/profile",
            json={
                "user_id": 1,
                "age": 0,
                "diabetes_type": "2型糖尿病",
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
