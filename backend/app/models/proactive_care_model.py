from sqlalchemy import Column, Integer, String, Text

from app.db.database import Base


class ProactiveCareEvent(Base):
    __tablename__ = "proactive_care_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    priority = Column(Integer, default=5)
    plan_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=False)
    status = Column(String, default="pending")
    cooldown_until = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    delivered_at = Column(String, nullable=True)
