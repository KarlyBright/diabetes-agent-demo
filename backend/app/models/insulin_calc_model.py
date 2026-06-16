from sqlalchemy import Column, Float, Integer, String

from app.db.database import Base


class InsulinCalculation(Base):
    __tablename__ = "insulin_calculations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    current_glucose = Column(Float, nullable=False)
    carbs_intake = Column(Float, nullable=False)
    iob = Column(Float, default=0)
    suggested_dose = Column(Float, nullable=False)
    correction_dose = Column(Float, nullable=False)
    carb_dose = Column(Float, nullable=False)
    accepted = Column(Integer, default=0)
    created_at = Column(String, nullable=False)
