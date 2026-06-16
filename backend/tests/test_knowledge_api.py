from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.knowledge import router
from app.db.database import Base


class TestKnowledgeApi:
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

    def build_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router, prefix="/api")

        from app.api import knowledge as knowledge_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[knowledge_module.get_db] = override_get_db
        return TestClient(app)

    def test_import_document_and_search_knowledge(self) -> None:
        client = self.build_client()

        import_response = client.post(
            "/api/knowledge/documents",
            json={
                "title": "糖尿病管理知识库",
                "source_name": "糖尿病管理知识库",
                "source_version": "demo-v1",
                "content": "topic: hypo\n低血糖时按15-15规则处理。",
            },
        )
        assert import_response.status_code == 200
        assert import_response.json()["data"]["source_name"] == "糖尿病管理知识库"

        search_response = client.get(
            "/api/knowledge/search",
            params={"query": "低血糖怎么办", "topic": "hypo"},
        )
        assert search_response.status_code == 200
        payload = search_response.json()["data"]
        assert payload["snippets"]
        assert "15-15" in payload["snippets"][0]["content"]
        assert payload["citations"][0]["source_version"] == "demo-v1"
