from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint

from app.db.database import Base


# =========================
# 待确认用药计划
# =========================
class PendingMedicationPlan(Base):
    __tablename__ = "pending_medication_plan"

    pending_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)

    drug_name = Column(String(100))
    dosage = Column(String(50))
    time_text = Column(String(100))
    remind_time = Column(String(50))
    frequency = Column(String(50))

    confirm_status = Column(String(20), default="pending")  # pending / confirmed / rejected
    is_valid = Column(Boolean, default=False)
    missing_fields = Column(String(200), default="")

    created_at = Column(DateTime, default=datetime.now)


# =========================
# 正式用药计划
# =========================
class MedicationPlan(Base):
    __tablename__ = "medication_plan"

    plan_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)

    drug_name = Column(String(100))
    dosage = Column(String(50))
    time_text = Column(String(100))
    remind_time = Column(String(50))
    frequency = Column(String(50))

    status = Column(String(20), default="active")  # active / inactive
    last_reminded_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.now)


# =========================
# 服药记录
# =========================
class MedicationTakenRecord(Base):
    __tablename__ = "medication_taken_record"

    record_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)

    plan_id = Column(Integer)
    drug_name = Column(String(100))
    dosage = Column(String(50))
    time_text = Column(String(100))
    remind_time = Column(String(50))

    status = Column(String(20))  # taken / missed

    created_at = Column(DateTime, default=datetime.now)


class MedicationReminderEvent(Base):
    __tablename__ = "medication_reminder_event"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "plan_id",
            "reminder_date",
            "scheduled_for",
            name="uq_medication_reminder_event_daily_schedule",
        ),
    )

    reminder_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    plan_id = Column(Integer, nullable=False)
    reminder_date = Column(String(10), nullable=False)
    scheduled_for = Column(String(5), nullable=False)
    scheduled_at = Column(DateTime, nullable=True)
    message_content = Column(String(500), nullable=False)
    delivery_status = Column(String(20), nullable=False, default="pending")
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
