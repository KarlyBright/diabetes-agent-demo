from sqlalchemy import Column, Float, Integer, String

from app.db.database import Base


class HypoglycemiaEvent(Base):
    __tablename__ = "hypoglycemia_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    trigger_glucose_id = Column(Integer, nullable=True)
    initial_value = Column(Float, nullable=False)
    severity = Column(String, nullable=False)
    status = Column(String, default="active")
    resolved_value = Column(Float, nullable=True)
    resolved_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
