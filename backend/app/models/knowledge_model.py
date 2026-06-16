from sqlalchemy import Column, Integer, String, Text

from app.db.database import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    source_name = Column(String(255), nullable=False)
    source_version = Column(String(100), nullable=True)
    source_url = Column(String(500), nullable=True)
    license_note = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(String(50), nullable=False)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    topic = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=True)
    created_at = Column(String(50), nullable=False)
