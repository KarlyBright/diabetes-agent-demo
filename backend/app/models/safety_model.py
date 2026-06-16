from sqlalchemy import Column, Integer, String

from app.db.database import Base


class SafetyIntervention(Base):
    __tablename__ = "safety_interventions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    risk_level = Column(String(20), nullable=False)
    category = Column(String(50), nullable=False)
    matched_rule = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)
    created_at = Column(String(50), nullable=False)
