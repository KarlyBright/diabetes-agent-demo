from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.knowledge_model import KnowledgeChunk
from app.services.knowledge_service import (
    build_cited_context,
    format_citations,
    ingest_knowledge_document,
    search_knowledge,
)


class TestKnowledgeService:
    def setup_method(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        Base.metadata.create_all(bind=self.engine)

    def teardown_method(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_ingest_document_creates_topic_chunks_and_hypo_search_hits(self) -> None:
        db = self.session_local()
        try:
            ingest_knowledge_document(
                db,
                {
                    "title": "糖尿病管理知识库",
                    "source_name": "糖尿病管理知识库",
                    "source_version": "demo-v1",
                    "source_url": "https://example.test/knowledge",
                    "license_note": "demo",
                    "content": (
                        "topic: hypo\n"
                        "低血糖怎么办：按15-15规则处理，立即摄入15克快速糖，15分钟后复测血糖。\n\n"
                        "topic: medication\n"
                        "不要自行停药或改药，用药调整要咨询医生。"
                    ),
                },
            )
            chunks = db.query(KnowledgeChunk).order_by(KnowledgeChunk.chunk_index.asc()).all()
            results = search_knowledge(db, "低血糖怎么办", topics=["hypo"])
        finally:
            db.close()

        assert len(chunks) >= 2
        assert chunks[0].topic == "hypo"
        assert results
        assert results[0]["topic"] == "hypo"
        assert "15-15" in results[0]["content"]

    def test_format_citations_and_build_context_include_source_metadata(self) -> None:
        db = self.session_local()
        try:
            ingest_knowledge_document(
                db,
                {
                    "title": "低血糖处理",
                    "source_name": "糖尿病管理知识库",
                    "source_version": "demo-v1",
                    "content": "topic: hypo\n低血糖时按15-15规则处理。",
                },
            )
            chunks = search_knowledge(db, "低血糖", topics=["hypo"])
            citations = format_citations(chunks)
            context = build_cited_context(chunks)
        finally:
            db.close()

        assert citations
        assert citations[0]["source_name"] == "糖尿病管理知识库"
        assert citations[0]["source_version"] == "demo-v1"
        assert "source_name=糖尿病管理知识库" in context
        assert "source_version=demo-v1" in context

    def test_empty_search_does_not_fabricate_citations(self) -> None:
        db = self.session_local()
        try:
            results = search_knowledge(db, "完全不存在的问题", topics=["hypo"])
            citations = format_citations(results)
        finally:
            db.close()

        assert results == []
        assert citations == []
