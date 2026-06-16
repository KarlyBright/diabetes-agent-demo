from sqlalchemy import Column, Integer, String, Text

from app.db.database import Base


class CaseReference(Base):
    __tablename__ = "case_references"

    id = Column(Integer, primary_key=True, index=True)
    case_type = Column(String(50), nullable=False)
    tags = Column(Text, nullable=False)
    profile_summary = Column(Text, nullable=False)
    pattern_summary = Column(Text, nullable=False)
    intervention_summary = Column(Text, nullable=False)
    outcome_summary = Column(Text, nullable=True)
    evidence_level = Column(String(50), nullable=False, default="demo")
    created_at = Column(String(50), nullable=False)
