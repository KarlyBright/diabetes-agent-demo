from sqlalchemy import Column, Float, Integer, String, UniqueConstraint

from app.db.database import Base


class HbA1cRecord(Base):
    __tablename__ = "hba1c_records"
    __table_args__ = (
        UniqueConstraint("user_id", "test_date", name="idx_hba1c_user_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    value = Column(Float, nullable=False)
    test_date = Column(String, nullable=False)
    source = Column(String, default="manual")
    created_at = Column(String, nullable=False)
