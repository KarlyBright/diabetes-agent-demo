from sqlalchemy import Column, Integer, String, Text, Float, Boolean, JSON
from app.db.database import Base


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)

    name = Column(String(100), nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String(20), nullable=True)
    diabetes_type = Column(String(50), nullable=True)
    disease_duration = Column(Integer, nullable=True)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    bmi = Column(Float, nullable=True)

    medications = Column(JSON, nullable=True)
    complications = Column(JSON, nullable=True)

    target_fasting_min = Column(Float, nullable=True)
    target_fasting_max = Column(Float, nullable=True)
    target_postmeal_min = Column(Float, nullable=True)
    target_postmeal_max = Column(Float, nullable=True)

    icr = Column(Float, nullable=True)
    isf = Column(Float, nullable=True)
    target_glucose = Column(Float, nullable=True, default=5.5)
    max_bolus = Column(Float, nullable=True, default=15.0)

    profile_completed = Column(Boolean, default=False)

    created_at = Column(String(50), nullable=True)
    updated_at = Column(String(50), nullable=True)
