from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent_inquiry import router
from app.db.database import Base
from app.models.medication_model import MedicationPlan
from app.models.questionnaire_model import AgentQuestionSession


class TestAgentInquiryApi:
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
        app.include_router(router, prefix="/api/agent")

        from app.api import agent_inquiry as inquiry_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[inquiry_module.get_db] = override_get_db
        return TestClient(app)

    def test_start_and_reply_inquiry(self) -> None:
        client = self.build_client()
        db = self.session_local()
        try:
            db.add(
                MedicationPlan(
                    user_id=1,
                    drug_name="二甲双胍",
                    dosage="1片",
                    time_text="早餐后",
                    remind_time="08:00",
                    frequency="daily",
                    status="active",
                )
            )
            db.commit()
        finally:
            db.close()

        start_response = client.post(
            "/api/agent/inquiry/start",
            json={"trigger_type": "medication_missed", "context": {}},
        )
        assert start_response.status_code == 200
        started = start_response.json()["data"]
        assert started["status"] == "active"

        reply_response = client.post(
            f"/api/agent/inquiry/{started['session_id']}/reply",
            json={"message": "已经吃了"},
        )
        assert reply_response.status_code == 200
        replied = reply_response.json()["data"]
        assert replied["status"] == "closed"
        assert replied["quick_replies"]

    def test_reply_rejects_cross_user_session_id(self) -> None:
        client = self.build_client()
        db = self.session_local()
        try:
            other_session = AgentQuestionSession(
                user_id=2,
                trigger_type="consecutive_high",
                status="active",
                current_step="ask_reason",
                context_json="{}",
                created_at="2026-05-22T10:00:00",
                updated_at="2026-05-22T10:00:00",
            )
            db.add(other_session)
            db.commit()
            db.refresh(other_session)
            session_id = int(other_session.id)
        finally:
            db.close()

        response = client.post(
            f"/api/agent/inquiry/{session_id}/reply",
            json={"message": "最近熬夜"},
        )

        assert response.status_code == 404
        db = self.session_local()
        try:
            session = db.query(AgentQuestionSession).filter_by(id=session_id).one()
            assert session.status == "active"
            assert session.user_id == 2
        finally:
            db.close()
