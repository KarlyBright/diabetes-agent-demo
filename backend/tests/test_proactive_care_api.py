from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent_inquiry import router as inquiry_router
from app.api.proactive_care import router
from app.db.database import Base
from app.models.medication_model import MedicationPlan, MedicationTakenRecord
from app.models.proactive_care_model import ProactiveCareEvent
from app.models.questionnaire_model import AgentQuestionSession


class TestProactiveCareApi:
    def setup_method(self) -> None:
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

    def teardown_method(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def build_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.include_router(inquiry_router, prefix="/api/agent")

        from app.api import agent_inquiry as inquiry_module
        from app.api import proactive_care as care_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[care_module.get_db] = override_get_db
        app.dependency_overrides[inquiry_module.get_db] = override_get_db
        return TestClient(app)

    def seed_event(self, *, user_id: int, event_type: str, plan_id: int | None = None) -> int:
        db = self.session_local()
        try:
            event = ProactiveCareEvent(
                user_id=user_id,
                event_type=event_type,
                priority=1,
                message="测试主动关怀",
                status="pending",
                cooldown_until=None,
                created_at="2026-05-22T10:00:00",
                plan_id=plan_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return int(event.id)
        finally:
            db.close()

    def test_dismiss_ignores_client_user_id_and_blocks_cross_user_event(self) -> None:
        client = self.build_client()
        other_event_id = self.seed_event(user_id=2, event_type="medication_missed")

        response = client.post(
            f"/api/care/dismiss/{other_event_id}",
            params={"user_id": 2},
        )

        assert response.status_code == 404
        db = self.session_local()
        try:
            event = db.query(ProactiveCareEvent).filter_by(id=other_event_id).one()
            assert event.status == "pending"
        finally:
            db.close()

    def test_start_inquiry_maps_hypo_followup_event_to_supported_trigger(self) -> None:
        client = self.build_client()
        event_id = self.seed_event(user_id=1, event_type="hypo_followup")

        response = client.post(f"/api/care/{event_id}/start-inquiry")

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["trigger_type"] == "hypoglycemia_followup"
        db = self.session_local()
        try:
            session = db.query(AgentQuestionSession).filter_by(id=payload["session_id"]).one()
            assert session.trigger_type == "hypoglycemia_followup"
        finally:
            db.close()

    def test_start_inquiry_passes_medication_plan_id_to_reply_flow(self) -> None:
        db = self.session_local()
        try:
            db.add_all(
                [
                    MedicationPlan(
                        user_id=1,
                        drug_name="阿卡波糖",
                        dosage="1片",
                        time_text="午餐前",
                        remind_time="11:30",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationPlan(
                        user_id=1,
                        drug_name="二甲双胍",
                        dosage="1片",
                        time_text="早餐后",
                        remind_time="08:00",
                        frequency="daily",
                        status="active",
                    ),
                ]
            )
            db.commit()
            metformin_plan = db.query(MedicationPlan).filter_by(drug_name="二甲双胍").one()
            metformin_plan_id = metformin_plan.plan_id
        finally:
            db.close()

        client = self.build_client()
        event_id = self.seed_event(user_id=1, event_type="medication_missed", plan_id=metformin_plan_id)
        start_response = client.post(f"/api/care/{event_id}/start-inquiry")
        session_id = start_response.json()["data"]["session_id"]
        reply_response = client.post(
            f"/api/agent/inquiry/{session_id}/reply",
            json={"message": "已经吃了"},
        )

        assert start_response.status_code == 200
        assert reply_response.status_code == 200
        db = self.session_local()
        try:
            records = db.query(MedicationTakenRecord).all()
        finally:
            db.close()
        assert len(records) == 1
        assert records[0].plan_id == metformin_plan_id
        assert records[0].drug_name == "二甲双胍"

    def test_start_inquiry_rejects_unsupported_care_event_type(self) -> None:
        client = self.build_client()
        event_id = self.seed_event(user_id=1, event_type="exercise_encourage")

        response = client.post(f"/api/care/{event_id}/start-inquiry")

        assert response.status_code == 400
        assert "不支持" in response.json()["detail"] or "unsupported" in response.json()["detail"]
