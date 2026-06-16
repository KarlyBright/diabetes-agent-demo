from sqlalchemy import Column, Float, Integer, String, Text

from app.db.database import Base


class ExerciseRecord(Base):
    __tablename__ = "exercise_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    exercise_type = Column(String, nullable=False)
    intensity = Column(String, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    calories_burned = Column(Float, nullable=True)
    pre_glucose_id = Column(Integer, nullable=True)
    post_glucose_id = Column(Integer, nullable=True)
    exercise_time = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(String, nullable=False)
