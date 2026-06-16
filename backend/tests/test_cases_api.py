from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.cases import router
from app.db.database import Base
from app.models.glucose_model import GlucoseRecordModel
from app.models.patient_model import PatientProfile
from app.services.case_reference_service import seed_demo_case_references


class TestCasesApi:
    def setup_method(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_local = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def teardown_method(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def build_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router, prefix="/api")

        from app.api import cases as cases_module

        def override_get_db():
            db = self.session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[cases_module.get_db] = override_get_db
        return TestClient(app)

    def test_similar_cases_endpoint_returns_anonymous_insight(self) -> None:
        db = self.session_local()
        try:
            db.add(PatientProfile(user_id=1, name="张三", age=58, diabetes_type="T2DM", bmi=26.0, profile_completed=True))
            db.add_all(
                [
                    GlucoseRecordModel(user_id=1, value=11.2, measure_time="2026-05-22T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-22T19:00:00"),
                    GlucoseRecordModel(user_id=1, value=10.8, measure_time="2026-05-21T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-21T19:00:00"),
                ]
            )
            seed_demo_case_references(db)
            db.commit()
        finally:
            db.close()

        response = self.build_client().get("/api/cases/similar")

        assert response.status_code == 200
        payload = response.json()["data"]
        assert "features" not in payload
        assert payload["cases"]
        assert "匿名案例" in payload["insight"]
        assert "仅供参考，不替代医生建议" in payload["insight"]
        assert "张三" not in str(payload)

    def test_similar_cases_endpoint_rejects_invalid_limit(self) -> None:
        client = self.build_client()

        too_large = client.get("/api/cases/similar", params={"limit": 1000})
        negative = client.get("/api/cases/similar", params={"limit": -1})

        assert too_large.status_code == 422
        assert negative.status_code == 422
