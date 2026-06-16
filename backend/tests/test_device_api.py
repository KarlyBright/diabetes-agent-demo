from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.device import router
from app.db.database import Base
from app.models.glucose_model import GlucoseRecordModel
from app.services.device_demo_service import reset_device_demo_state


class DeviceApiTests(unittest.TestCase):
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
        reset_device_demo_state()

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        reset_device_demo_state()

    def build_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router, prefix="/api/device")

        from app.api import device as device_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[device_module.get_db] = override_get_db
        return TestClient(app)

    def test_status_endpoint_returns_glp_protocol_metadata(self) -> None:
        client = self.build_client()

        response = client.get("/api/device/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["protocol"]["service_uuid"], "0x1808")
        self.assertFalse(payload["data"]["connected"])
        self.assertGreaterEqual(len(payload["data"]["features"]), 1)

    def test_mock_sync_requires_connection(self) -> None:
        client = self.build_client()

        response = client.post("/api/device/mock-sync")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "请先连接 GLP 演示血糖仪"})

    def test_connect_and_mock_sync_persist_device_records(self) -> None:
        client = self.build_client()

        connect_response = client.post("/api/device/connect")
        self.assertEqual(connect_response.status_code, 200)
        self.assertTrue(connect_response.json()["data"]["connected"])

        sync_response = client.post("/api/device/mock-sync")
        self.assertEqual(sync_response.status_code, 200)
        sync_payload = sync_response.json()
        self.assertEqual(sync_payload["code"], 0)
        self.assertGreaterEqual(sync_payload["data"]["created_count"], 1)
        self.assertEqual(sync_payload["data"]["refresh"], ["glucose", "adherence", "advice"])

        db = self.session_local()
        try:
            stored_records = db.query(GlucoseRecordModel).all()
            self.assertGreaterEqual(len(stored_records), 1)
            self.assertTrue(all(record.source == "device" for record in stored_records))
        finally:
            db.close()

        status_response = client.get("/api/device/status")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.json()
        self.assertEqual(status_payload["data"]["state"], "synced")
        self.assertGreaterEqual(status_payload["data"]["last_sync_count"], 1)

    def test_device_readings_roll_back_entire_batch_on_invalid_payload(self) -> None:
        client = self.build_client()
        client.post("/api/device/connect")

        response = client.post(
            "/api/device/readings",
            json={
                "user_id": 1,
                "readings": [
                    {"measurement_hex": "170100ea07040b081e00000048c011", "context_hex": "02010002"},
                    {"measurement_hex": "170100ea07", "context_hex": None},
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        db = self.session_local()
        try:
            self.assertEqual(db.query(GlucoseRecordModel).count(), 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
