from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.medication_model import MedicationPlan, MedicationTakenRecord
from app.models.questionnaire_model import AgentQuestionSession
from app.services.hypoglycemia_service import create_hypo_event
from app.services.reverse_inquiry_service import advance_inquiry, close_inquiry, start_inquiry


class TestReverseInquiryService:
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

    def test_consecutive_high_starts_with_first_question_and_closes_with_sleep_reason(self) -> None:
        db = self.session_local()
        try:
            started = start_inquiry(db, user_id=1, trigger_type="consecutive_high", context={})
            replied = advance_inquiry(db, session_id=started["session_id"], user_id=1, message="最近熬夜")
        finally:
            db.close()

        assert "熬夜" in started["message"]
        assert started["status"] == "active"
        assert replied["status"] == "closed"
        assert "作息" in replied["message"] or "睡眠" in replied["message"]
        assert replied["quick_replies"] == []

    def test_medication_missed_already_taken_suggests_logging_medication_status(self) -> None:
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
            started = start_inquiry(db, user_id=1, trigger_type="medication_missed", context={})
            replied = advance_inquiry(db, session_id=started["session_id"], user_id=1, message="已经吃了")
            records = db.query(MedicationTakenRecord).all()
        finally:
            db.close()

        assert "具体是哪一个用药计划" in replied["message"]
        assert replied["status"] == "closed"
        assert any("查看用药计划" in item for item in replied["quick_replies"])
        assert records == []

    def test_medication_missed_records_triggering_plan_when_available(self) -> None:
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
            started = start_inquiry(
                db,
                user_id=1,
                trigger_type="medication_missed",
                context={"plan_id": metformin_plan_id},
            )
            advance_inquiry(db, session_id=started["session_id"], user_id=1, message="已经吃了")
            records = db.query(MedicationTakenRecord).all()
        finally:
            db.close()

        assert len(records) == 1
        assert records[0].plan_id == metformin_plan_id
        assert records[0].drug_name == "二甲双胍"

    def test_close_inquiry_rejects_cross_user_session_id(self) -> None:
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

            try:
                close_inquiry(db, session_id=session_id, user_id=1, outcome={"reason": "test"})
            except ValueError as exc:
                error_message = str(exc)
            else:
                error_message = ""

            db.refresh(other_session)
            session_status = other_session.status
        finally:
            db.close()

        assert "not found" in error_message
        assert session_status == "active"

    def test_start_inquiry_reuses_existing_active_session_for_same_trigger(self) -> None:
        db = self.session_local()
        try:
            first = start_inquiry(db, user_id=1, trigger_type="consecutive_high", context={"a": 1})
            second = start_inquiry(db, user_id=1, trigger_type="consecutive_high", context={"a": 2})
            active_count = (
                db.query(AgentQuestionSession)
                .filter_by(user_id=1, trigger_type="consecutive_high", status="active")
                .count()
            )
        finally:
            db.close()

        assert second["session_id"] == first["session_id"]
        assert active_count == 1

    def test_medication_missed_starts_new_session_for_different_plan_context(self) -> None:
        db = self.session_local()
        try:
            first = start_inquiry(db, user_id=1, trigger_type="medication_missed", context={"plan_id": 1})
            second = start_inquiry(db, user_id=1, trigger_type="medication_missed", context={"plan_id": 2})
            active_sessions = (
                db.query(AgentQuestionSession)
                .filter_by(user_id=1, trigger_type="medication_missed", status="active")
                .order_by(AgentQuestionSession.id.asc())
                .all()
            )
        finally:
            db.close()

        assert second["session_id"] != first["session_id"]
        assert len(active_sessions) == 2

    def test_hypoglycemia_followup_does_not_parse_timer_as_glucose_value(self) -> None:
        db = self.session_local()
        try:
            create_hypo_event(db, user_id=1, initial_value=3.2, severity="mild")
            started = start_inquiry(db, user_id=1, trigger_type="hypoglycemia_followup", context={})
            replied = advance_inquiry(db, session_id=started["session_id"], user_id=1, message="15分钟后再测")
        finally:
            db.close()

        assert replied["status"] == "active"
        assert "当前血糖" in replied["message"] or "血糖数值" in replied["message"]

    def test_hypoglycemia_followup_keeps_15_15_when_still_low(self) -> None:
        db = self.session_local()
        try:
            create_hypo_event(db, user_id=1, initial_value=3.2, severity="mild")
            started = start_inquiry(db, user_id=1, trigger_type="hypoglycemia_followup", context={})
            replied = advance_inquiry(db, session_id=started["session_id"], user_id=1, message="还是3.5")
        finally:
            db.close()

        assert "恢复" in started["message"] or "血糖多少" in started["message"]
        assert replied["status"] == "active"
        assert "15" in replied["message"]
        assert any("15分钟后再测" in item for item in replied["quick_replies"])
