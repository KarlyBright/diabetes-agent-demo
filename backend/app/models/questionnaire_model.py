from sqlalchemy import Column, Integer, String, Text

from app.db.database import Base


class AgentQuestionSession(Base):
    __tablename__ = "agent_question_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    trigger_type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    current_step = Column(String(50), nullable=False)
    context_json = Column(Text, nullable=False)
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)
