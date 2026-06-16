from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent_memory import router
from app.db.database import Base


class TestAgentMemoryApi:
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
        app.include_router(router, prefix="/api/agent")

        from app.api import agent_memory as agent_memory_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[agent_memory_module.get_db] = override_get_db
        return TestClient(app)

    def test_memory_crud_and_extract_endpoints(self) -> None:
        client = self.build_client()

        create_response = client.post(
            "/api/agent/memory",
            json={
                "category": "allergy",
                "key": "花生",
                "value": "过敏",
            },
        )
        assert create_response.status_code == 200
        created = create_response.json()["data"]
        assert created["category"] == "allergy"
        assert created["key"] == "花生"

        list_response = client.get("/api/agent/memory")
        assert list_response.status_code == 200
        assert len(list_response.json()["data"]) == 1

        extract_response = client.post(
            "/api/agent/memory/extract",
            json={"message": "记住我晚上不喝咖啡"},
        )
        assert extract_response.status_code == 200
        assert extract_response.json()["data"]["key"] == "咖啡"

        delete_response = client.delete(f"/api/agent/memory/{created['id']}")
        assert delete_response.status_code == 200
        assert delete_response.json()["data"]["archived"] is True

    def test_memory_api_ignores_client_supplied_user_id_in_demo_mode(self) -> None:
        client = self.build_client()

        create_response = client.post(
            "/api/agent/memory",
            json={
                "user_id": 999,
                "category": "preference",
                "key": "咖啡",
                "value": "晚上不喝",
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["data"]["user_id"] == 1

        other_user_list = client.get("/api/agent/memory", params={"user_id": 999})
        assert other_user_list.status_code == 200
        assert other_user_list.json()["data"][0]["user_id"] == 1
