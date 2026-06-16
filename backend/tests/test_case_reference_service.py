from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.glucose_model import GlucoseRecordModel
from app.models.medication_model import MedicationPlan
from app.models.patient_model import PatientProfile
from app.services.case_reference_service import (
    build_user_case_features,
    format_case_insight,
    seed_demo_case_references,
    search_similar_cases,
)


class TestCaseReferenceService:
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

    def test_build_user_case_features_extracts_profile_and_glucose_patterns(self) -> None:
        db = self.session_local()
        try:
            db.add(
                PatientProfile(
                    user_id=1,
                    age=58,
                    diabetes_type="T2DM",
                    bmi=26.1,
                    medications=["二甲双胍"],
                    profile_completed=True,
                )
            )
            db.add_all(
                [
                    GlucoseRecordModel(user_id=1, value=11.2, measure_time="2026-05-22T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-22T19:00:00"),
                    GlucoseRecordModel(user_id=1, value=10.8, measure_time="2026-05-21T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-21T19:00:00"),
                    GlucoseRecordModel(user_id=1, value=6.8, measure_time="2026-05-20T07:00:00", measure_type="fasting", source="manual", created_at="2026-05-20T07:00:00"),
                ]
            )
            db.add(MedicationPlan(user_id=1, drug_name="二甲双胍", dosage="1片", time_text="早餐后", remind_time="08:00", frequency="daily", status="active"))
            db.commit()

            features = build_user_case_features(db, user_id=1)
        finally:
            db.close()

        assert features["diabetes_type"] == "t2dm"
        assert features["age_band"] == "middle_aged"
        assert features["bmi_category"] == "overweight"
        assert "post_meal_high" in features["glucose_patterns"]
        assert "metformin" in features["medication_tags"]

    def test_search_similar_cases_returns_explainable_anonymous_matches(self) -> None:
        db = self.session_local()
        try:
            seed_demo_case_references(db)
            features = {
                "diabetes_type": "t2dm",
                "age_band": "middle_aged",
                "bmi_category": "overweight",
                "glucose_patterns": ["post_meal_high"],
                "medication_tags": ["metformin"],
                "behavior_tags": ["sedentary"],
            }
            cases = search_similar_cases(db, features, limit=2)
            insight = format_case_insight(cases)
        finally:
            db.close()

        assert cases
        assert cases[0]["score"] > 0
        assert cases[0]["match_reasons"]
        assert "匿名案例" in insight
        assert "仅供参考，不替代医生建议" in insight
        assert "姓名" not in insight
        assert "电话" not in insight
