from sqlalchemy import Column, Integer, String, Float
from app.db.database import Base


class GlucoseRecordModel(Base):
    __tablename__ = "glucose_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    value = Column(Float, nullable=False)
    measure_time = Column(String, nullable=False)
    measure_type = Column(String, nullable=False)
    source = Column(String, nullable=False)
    created_at = Column(String, nullable=False)