from sqlalchemy import Column, Float, Integer, String, UniqueConstraint

from app.db.database import Base


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "key", name="uq_agent_memory_user_category_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    category = Column(String(50), nullable=False)
    key = Column(String(100), nullable=False)
    value = Column(String(255), nullable=False)
    confidence = Column(Float, nullable=False, default=0.8)
    source = Column(String(50), nullable=False, default="chat")
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)
