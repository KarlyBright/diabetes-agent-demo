from sqlalchemy import Column, Integer, String, Text

from app.db.database import Base


class ScreeningItem(Base):
    __tablename__ = "screening_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    interval_months = Column(Integer, nullable=False)
    is_active = Column(Integer, default=1)
    created_at = Column(String, nullable=False)


class ScreeningRecord(Base):
    __tablename__ = "screening_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    screening_item_id = Column(Integer, nullable=False)
    check_date = Column(String, nullable=False)
    result = Column(String, default="normal")
    notes = Column(Text, nullable=True)
    next_due_date = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
