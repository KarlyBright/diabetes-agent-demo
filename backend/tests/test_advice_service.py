from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.main import get_daily_advice, meal_analysis_records
from app.services.advice_service import generate_daily_advice_logic
from app.services.patient_service import save_patient_profile


class AdviceServiceTests(unittest.TestCase):
    def test_generate_daily_advice_omits_profile_summary_when_profile_incomplete(self) -> None:
        result = generate_daily_advice_logic(
            profile={
                "user_id": 1,
                "name": None,
                "age": None,
                "gender": None,
                "diabetes_type": None,
                "medication": None,
                "profile_completed": False,
            },
            recent_glucose=[],
            recent_meal_analysis=None,
        )

        self.assertIsNone(result["profile_summary"])
        self.assertEqual(result["risk_level"], "low")
        self.assertTrue(
            any("建议先记录今天的血糖数据" in item for item in result["daily_advice"])
        )

    def test_generate_daily_advice_builds_profile_summary_from_medications_list(self) -> None:
        result = generate_daily_advice_logic(
            profile={
                "user_id": 1,
                "name": "张三",
                "age": 56,
                "gender": "男",
                "diabetes_type": "2型糖尿病",
                "medications": ["二甲双胍", "阿卡波糖"],
                "profile_completed": True,
            },
            recent_glucose=[],
            recent_meal_analysis=None,
        )

        self.assertEqual(
            result["profile_summary"],
            "56岁，2型糖尿病，当前服用二甲双胍、阿卡波糖",
        )

    def test_generate_daily_advice_builds_profile_summary_without_medication_text(self) -> None:
        result = generate_daily_advice_logic(
            profile={
                "user_id": 1,
                "name": "张三",
                "age": 56,
                "gender": "男",
                "diabetes_type": "2型糖尿病",
                "medications": [],
                "profile_completed": True,
            },
            recent_glucose=[],
            recent_meal_analysis=None,
        )

        self.assertEqual(result["profile_summary"], "56岁，2型糖尿病")

    def test_generate_daily_advice_keeps_legacy_medication_field_compatible(self) -> None:
        result = generate_daily_advice_logic(
            profile={
                "user_id": 1,
                "name": "张三",
                "age": 56,
                "gender": "男",
                "diabetes_type": "2型糖尿病",
                "medication": "二甲双胍",
                "profile_completed": True,
            },
            recent_glucose=[],
            recent_meal_analysis=None,
        )

        self.assertEqual(
            result["profile_summary"],
            "56岁，2型糖尿病，当前服用二甲双胍",
        )


class DailyAdviceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def tearDown(self) -> None:
        meal_analysis_records.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_get_daily_advice_reads_profile_summary_from_database(self) -> None:
        db = self.session_local()
        try:
            save_patient_profile(
                db,
                user_id=1,
                name="张三",
                age=56,
                gender="男",
                diabetes_type="2型糖尿病",
                medications=["二甲双胍"],
            )

            payload = get_daily_advice(user_id=1, db=db)

            self.assertEqual(payload["message"], "今日建议生成成功")
            self.assertEqual(
                payload["data"]["profile_summary"],
                "56岁，2型糖尿病，当前服用二甲双胍",
            )
            self.assertFalse(payload["data"].get("profile_completed", False))
        finally:
            db.close()

    def test_get_daily_advice_uses_fallback_meal_summary_when_no_meal_analysis_exists(self) -> None:
        db = self.session_local()
        try:
            payload = get_daily_advice(user_id=1, db=db)

            self.assertEqual(payload["message"], "今日建议生成成功")
            self.assertEqual(
                payload["data"]["meal_summary"],
                "暂无近期饮食分析记录，建议补充饮食信息。",
            )
        finally:
            db.close()

    def test_get_daily_advice_reads_latest_meal_analysis_for_current_user(self) -> None:
        db = self.session_local()
        try:
            meal_analysis_records.extend(
                [
                    {
                        "id": 1,
                        "user_id": 2,
                        "meal_text": "他人早餐",
                        "risk_level": "low",
                        "total_gl": 12,
                        "gl_level": "低",
                        "score": 88,
                        "detected_foods": [],
                        "suggestion": ["继续保持"],
                        "summary": "升糖负荷(GL): 12（低级别）",
                        "created_at": "2026-04-14T08:00:00",
                    },
                    {
                        "id": 2,
                        "user_id": 1,
                        "meal_text": "较早午餐",
                        "risk_level": "low",
                        "total_gl": 20,
                        "gl_level": "低",
                        "score": 90,
                        "detected_foods": [],
                        "suggestion": ["继续保持"],
                        "summary": "升糖负荷(GL): 20（低级别）",
                        "created_at": "2026-04-14T11:30:00",
                    },
                    {
                        "id": 3,
                        "user_id": 1,
                        "meal_text": "最新晚餐",
                        "risk_level": "high",
                        "total_gl": 48,
                        "gl_level": "高",
                        "score": 42,
                        "detected_foods": [],
                        "suggestion": ["建议减少高GI食物摄入"],
                        "summary": "升糖负荷(GL): 48（高级别）",
                        "created_at": "2026-04-14T18:30:00",
                    },
                ]
            )

            payload = get_daily_advice(user_id=1, db=db)

            self.assertEqual(
                payload["data"]["meal_summary"],
                "最近一次饮食风险等级为 high，综合评分为 42。",
            )
            self.assertEqual(payload["data"]["risk_level"], "high")
            self.assertIn("今晚建议减少主食摄入，避免含糖饮料和油炸食品。", payload["data"]["daily_advice"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
