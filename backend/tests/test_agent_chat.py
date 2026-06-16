from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.main import MealAnalyzeInput, MealRecord
from app.services.agent_chat_service import (
    CHAT_IMAGE_MAX_BYTES,
    CHAT_IMAGE_MAX_COUNT,
    ChatImageInput,
)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent import router
from app.api.agent import AgentChatService as RouteAgentChatService
from app.models.agent_memory_model import AgentMemory
from app.models.glucose_model import GlucoseRecordModel
from app.models.patient_model import PatientProfile
from app.db.database import Base
from app.services.agent_chat_service import (
    AGENT_SYSTEM_PROMPT,
    IMAGE_ANALYSIS_FALLBACK_MESSAGE,
    MEAL_TEXT_MAX_LENGTH,
    AgentChatService,
    AnalyzeDietTool,
    BackendAgentTool,
    SearchGuidelineKnowledgeTool,
    ToolResultPayload,
    analyze_diet_with_gl,
    build_meal_analysis_payload,
    handle_medication_parse,
    handle_medication_take,
)
from app.services.knowledge_service import ingest_knowledge_document


class _FakeRunResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


class _FakeLoop:
    def __init__(self, bot: "_FakeBot") -> None:
        self.bot = bot
        self._extra_hooks: list[Any] = []
        self.tools = None
        self.workspace = Path.cwd()
        self.provider = type(
            "Provider",
            (),
            {"generation": type("Generation", (), {"max_tokens": 1024})()},
        )()
        self.model = "openai/gpt-4.1"
        self.sessions = object()
        self.context_window_tokens = 16000
        self.context = type(
            "Context", (), {"build_messages": staticmethod(lambda *args, **kwargs: [])}
        )()
        self.memory_consolidator = type("Consolidator", (), {})()
        self.processed_messages: list[Any] = []

    async def _connect_mcp(self) -> None:
        return None

    async def _process_message(
        self, msg: Any, session_key: str, **_: Any
    ) -> _FakeRunResult:
        self.processed_messages.append((msg, session_key))
        if self.bot.payload is not None:
            runtime_hook = self._extra_hooks[0]
            if hasattr(runtime_hook, "_owner"):
                runtime_hook._owner.last_tool_payload = self.bot.payload
        return _FakeRunResult(self.bot.content)


class _FakeBot:
    def __init__(
        self, payload: ToolResultPayload | None = None, content: str = ""
    ) -> None:
        self.payload = payload
        self.content = content
        self.calls: list[tuple[str, str]] = []
        self._loop: Any = _FakeLoop(self)

    async def run(self, message: str, session_key: str, hooks: list[Any]):
        self.calls.append((message, session_key))
        if self.payload is not None:
            runtime_hook = hooks[0]
            if hasattr(runtime_hook, "_owner"):
                runtime_hook._owner.last_tool_payload = self.payload
        return _FakeRunResult(self.content)


class _HookBase:
    async def after_iteration(self, context: Any) -> None:
        return None


class AgentChatTests(unittest.TestCase):
    def test_system_prompt_blocks_peanut_butter_when_user_has_peanut_allergy(self) -> None:
        self.assertIn("我晚饭想吃米饭配花生酱", AGENT_SYSTEM_PROMPT)
        self.assertIn("你对花生过敏哦，不能吃花生酱", AGENT_SYSTEM_PROMPT)

    def build_test_client(self) -> TestClient:
        app = FastAPI()
        app.include_router(router, prefix="/api/agent")
        return TestClient(app)

    def build_test_client_with_db(self) -> tuple[TestClient, sessionmaker, object]:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        Base.metadata.create_all(bind=engine)

        app = FastAPI()
        app.include_router(router, prefix="/api/agent")

        from app.api import agent as agent_module

        def override_get_db():
            db = session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[agent_module.get_db] = override_get_db
        return TestClient(app), session_local, engine

    def test_chat_route_preserves_contract_with_nanobot_service(self) -> None:
        fake_response = {
            "code": 0,
            "data": {
                "role": "assistant",
                "content": "已帮你记录血糖 7.2 mmol/L",
                "refresh": ["glucose", "adherence", "advice"],
            },
        }
        client = self.build_test_client()

        with patch.object(
            RouteAgentChatService, "run_chat", return_value=fake_response
        ):
            response = client.post(
                "/api/agent/chat", json={"message": "我刚测血糖 7.2"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_response)

    def test_chat_route_blocks_insulin_dose_questions_before_nanobot(self) -> None:
        client, _, engine = self.build_test_client_with_db()
        try:
            with patch.object(RouteAgentChatService, "run_chat") as run_chat:
                response = client.post(
                    "/api/agent/chat",
                    json={"message": "血糖12，晚饭80g碳水该打多少胰岛素"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()["data"]
            self.assertEqual(payload["safety_level"], "high")
            self.assertIn("医生", payload["content"])
            run_chat.assert_not_called()
        finally:
            engine.dispose()

    def test_chat_route_preserves_optional_meal_analysis(self) -> None:
        fake_response = {
            "code": 0,
            "data": {
                "role": "assistant",
                "content": "这顿饭主食偏多",
                "refresh": ["meal"],
                "meal_analysis": {
                    "risk_level": "medium",
                    "total_gl": 27,
                    "gl_level": "中",
                    "score": 75,
                    "detected_foods": [
                        {
                            "name": "米饭",
                            "category": "high_risk",
                            "gi": 83,
                            "gl": 21,
                            "portion_g": 100,
                        }
                    ],
                    "suggestion": ["注意控制主食总量"],
                    "summary": "升糖负荷(GL): 27（中级别）",
                },
            },
        }
        client = self.build_test_client()

        with patch.object(
            RouteAgentChatService, "run_chat", return_value=fake_response
        ):
            response = client.post("/api/agent/chat", json={"message": "这是我的午餐"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_response)

    def test_chat_route_rejects_blank_message(self) -> None:
        client = self.build_test_client()
        response = client.post("/api/agent/chat", json={"message": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "message或图片不能为空"})

    def test_chat_route_rejects_overlong_json_message(self) -> None:
        client = self.build_test_client()
        response = client.post(
            "/api/agent/chat", json={"message": "米" * (MEAL_TEXT_MAX_LENGTH + 1)}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "请求参数不完整或格式不正确"})

    def test_chat_route_rejects_overlong_multipart_message(self) -> None:
        client = self.build_test_client()
        response = client.post(
            "/api/agent/chat",
            data={"message": "米" * (MEAL_TEXT_MAX_LENGTH + 1)},
            files={"images": ("meal.png", b"\x89PNG\r\n\x1a\nPNGDATA", "image/png")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "请求参数不完整或格式不正确"})

    def test_chat_route_accepts_trimmed_exact_limit_json_message(self) -> None:
        client = self.build_test_client()
        expected_message = "米" * MEAL_TEXT_MAX_LENGTH

        with patch.object(
            RouteAgentChatService,
            "run_chat",
            return_value={"code": 0, "data": {"role": "assistant", "content": "ok", "refresh": []}},
        ) as run_chat:
            response = client.post(
                "/api/agent/chat", json={"message": f"  {expected_message}  "}
            )

        self.assertEqual(response.status_code, 200)
        run_chat.assert_called_once()
        self.assertEqual(run_chat.call_args.args[0], expected_message)

    def test_chat_route_accepts_trimmed_exact_limit_multipart_message(self) -> None:
        client = self.build_test_client()
        expected_message = "米" * MEAL_TEXT_MAX_LENGTH

        with patch.object(
            RouteAgentChatService,
            "run_chat",
            return_value={"code": 0, "data": {"role": "assistant", "content": "ok", "refresh": []}},
        ) as run_chat:
            response = client.post(
                "/api/agent/chat",
                data={"message": f"  {expected_message}  "},
                files={"images": ("meal.png", b"\x89PNG\r\n\x1a\nPNGDATA", "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        run_chat.assert_called_once()
        self.assertEqual(run_chat.call_args.args[0], expected_message)

    def test_chat_route_accepts_multipart_image_only(self) -> None:
        fake_response = {
            "code": 0,
            "data": {
                "role": "assistant",
                "content": "我看到了你上传的图片。",
                "refresh": [],
            },
        }
        client = self.build_test_client()
        image_bytes = b"\x89PNG\r\n\x1a\nPNGDATA"

        with patch.object(
            RouteAgentChatService, "run_chat", return_value=fake_response
        ) as run_chat:
            response = client.post(
                "/api/agent/chat",
                data={"message": ""},
                files={"images": ("meal.png", image_bytes, "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_response)
        _, kwargs = run_chat.call_args
        self.assertEqual(kwargs["images"][0].content, image_bytes)
        self.assertEqual(kwargs["images"][0].content_type, "image/png")

    def test_chat_route_accepts_multipart_text_and_image(self) -> None:
        fake_response = {
            "code": 0,
            "data": {
                "role": "assistant",
                "content": "这顿饭主食偏多。",
                "refresh": [],
            },
        }
        client = self.build_test_client()
        image_bytes = b"\x89PNG\r\n\x1a\nPNGDATA"

        with patch.object(
            RouteAgentChatService, "run_chat", return_value=fake_response
        ) as run_chat:
            response = client.post(
                "/api/agent/chat",
                data={"message": "帮我看这顿饭"},
                files={"images": ("meal.png", image_bytes, "image/png")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_response)
        args, kwargs = run_chat.call_args
        self.assertEqual(args[0], "帮我看这顿饭")
        self.assertEqual(len(kwargs["images"]), 1)

    def test_chat_route_rejects_invalid_image_type(self) -> None:
        client = self.build_test_client()
        response = client.post(
            "/api/agent/chat",
            data={"message": "看看这个文件"},
            files={"images": ("note.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "仅支持 JPG、PNG、WEBP 图片"})

    def test_chat_route_rejects_too_many_images(self) -> None:
        client = self.build_test_client()
        files = [
            ("images", (f"img-{index}.png", b"\x89PNG\r\n\x1a\nX", "image/png"))
            for index in range(CHAT_IMAGE_MAX_COUNT + 1)
        ]
        response = client.post("/api/agent/chat", data={"message": ""}, files=files)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(), {"detail": f"最多只能上传{CHAT_IMAGE_MAX_COUNT}张图片"}
        )

    def test_chat_route_rejects_oversized_image(self) -> None:
        client = self.build_test_client()
        oversized = b"\x89PNG\r\n\x1a\n" + (b"0" * CHAT_IMAGE_MAX_BYTES)
        response = client.post(
            "/api/agent/chat",
            data={"message": ""},
            files={"images": ("huge.png", oversized, "image/png")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "图片大小不能超过5MB"})

    def test_chat_route_rejects_invalid_json_body(self) -> None:
        client = self.build_test_client()
        response = client.post(
            "/api/agent/chat",
            content="{bad json}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "请求体不是合法的 JSON"})

    def test_analyze_diet_with_gl_avoids_double_counting_overlapping_food_aliases(
        self,
    ) -> None:
        result = analyze_diet_with_gl("我早餐吃了白米饭和鸡胸肉")
        identified_food_names = [item["food"] for item in result["identified_foods"]]

        self.assertIn("白米饭", identified_food_names)
        self.assertIn("鸡胸肉", identified_food_names)
        self.assertNotIn("米饭", identified_food_names)
        self.assertNotIn("鸡肉", identified_food_names)

    def test_analyze_diet_with_gl_parses_explicit_grams(self) -> None:
        result = analyze_diet_with_gl("午餐吃了200g米饭")
        rice = next(item for item in result["identified_foods"] if item["food"] == "米饭")

        self.assertEqual(rice["portion_g"], 200)
        self.assertEqual(rice["portion_source"], "explicit_gram")
        self.assertEqual(rice["carbs_g"], 56.0)
        self.assertEqual(rice["gl"], 40.9)
        self.assertTrue(
            any("GL 为估算值" in warning for warning in result["warnings"])
        )

    def test_analyze_diet_with_gl_parses_half_bowl(self) -> None:
        result = analyze_diet_with_gl("晚餐吃了半碗面条")
        noodle = next(item for item in result["identified_foods"] if item["food"] == "面条")

        self.assertEqual(noodle["portion_g"], 100)
        self.assertEqual(noodle["portion_source"], "unit_mapping_scaled")
        self.assertEqual(noodle["gl"], 15.2)

    def test_analyze_diet_with_gl_parses_two_item_count(self) -> None:
        result = analyze_diet_with_gl("早餐吃了两个馒头")
        mantou = next(item for item in result["identified_foods"] if item["food"] == "馒头")

        self.assertEqual(mantou["portion_g"], 200)
        self.assertEqual(mantou["portion_source"], "unit_mapping_scaled")
        self.assertEqual(mantou["gl"], 42.5)

    def test_analyze_diet_with_gl_ignores_negated_foods(self) -> None:
        result = analyze_diet_with_gl("午餐没吃米饭，只吃了鸡蛋")
        identified_food_names = [item["food"] for item in result["identified_foods"]]
        ignored_rice = next(
            item for item in result["ignored_foods"] if item["food_name"] == "米饭"
        )

        self.assertNotIn("米饭", identified_food_names)
        self.assertIn("鸡蛋", identified_food_names)
        self.assertEqual(ignored_rice["reason"], "用户明确否定摄入")

    def test_analyze_diet_with_gl_counts_repeated_food_occurrences(self) -> None:
        result = analyze_diet_with_gl("早餐一碗米饭，晚餐又一碗米饭")
        rice_items = [item for item in result["identified_foods"] if item["food"] == "米饭"]

        self.assertEqual(len(rice_items), 2)
        self.assertEqual(result["total_gl"], 61.4)

    def test_analyze_diet_with_gl_reports_unrecognized_items(self) -> None:
        result = analyze_diet_with_gl("午餐吃了米饭和煲仔饭")
        unrecognized_claypot_rice = next(
            item
            for item in result["unrecognized_items"]
            if item["raw_text"] == "煲仔饭"
        )

        self.assertEqual(unrecognized_claypot_rice["raw_text"], "煲仔饭")
        self.assertTrue(
            any("部分食物未识别" in warning for warning in result["warnings"])
        )

    def test_analyze_diet_with_gl_reports_arbitrary_unrecognized_foods(self) -> None:
        result = analyze_diet_with_gl("午餐吃了米饭和寿司")
        unknown_sushi = next(
            item for item in result["unrecognized_items"] if item["raw_text"] == "寿司"
        )

        self.assertEqual(unknown_sushi["reason"], "食物库暂未收录，未计入 GL")
        self.assertTrue(
            any("部分食物未识别" in warning for warning in result["warnings"])
        )

    def test_analyze_diet_with_gl_ignores_negated_unrecognized_foods(self) -> None:
        result = analyze_diet_with_gl("午餐没吃寿司，只吃了米饭")

        self.assertEqual(result["unrecognized_items"], [])
        self.assertEqual([item["food"] for item in result["identified_foods"]], ["米饭"])

    def test_meal_analyze_input_rejects_overlong_text(self) -> None:
        with self.assertRaises(ValidationError):
            MealAnalyzeInput(user_id=1, meal_text="米" * (MEAL_TEXT_MAX_LENGTH + 1))

    def test_meal_record_rejects_overlong_text(self) -> None:
        with self.assertRaises(ValidationError):
            MealRecord(
                user_id=1,
                meal_text="米" * (MEAL_TEXT_MAX_LENGTH + 1),
                meal_time="2026-05-20T12:00:00",
            )

    def test_build_meal_analysis_payload_preserves_phase1_top_level_contract(self) -> None:
        analysis_result = {
            "summary": "fixture summary",
            "suggestions": ["fixture suggestion"],
            "score": 82,
            "identified_foods": [],
            "total_gl": 12.3,
            "glucose_impact": {"level": "中"},
            "confidence": 0.72,
            "warnings": ["fixture warning"],
            "ignored_foods": [{"food_name": "面条", "reason": "fixture ignored"}],
            "unrecognized_items": [{"raw_text": "煲仔饭", "reason": "fixture unknown"}],
        }
        payload = build_meal_analysis_payload(analysis_result)

        self.assertEqual(payload["risk_level"], "medium")
        self.assertEqual(payload["total_gl"], 12.3)
        self.assertEqual(payload["gl_level"], "中")
        self.assertEqual(payload["score"], 82)
        self.assertEqual(payload["detected_foods"], [])
        self.assertEqual(payload["suggestion"], ["fixture suggestion"])
        self.assertEqual(payload["summary"], "fixture summary")
        self.assertEqual(payload["confidence"], 0.72)
        self.assertEqual(payload["warnings"], ["fixture warning"])
        self.assertEqual(payload["ignored_foods"], analysis_result["ignored_foods"])
        self.assertEqual(payload["unrecognized_items"], analysis_result["unrecognized_items"])
        self.assertEqual(payload["calculation_version"], "gl-v1.1-phase1")

    def test_build_meal_analysis_payload_maps_detected_food_phase1_fields(self) -> None:
        analysis_result = {
            "suggestions": [],
            "identified_foods": [
                {
                    "food": "米饭",
                    "portion_g": 200,
                    "portion_source": "explicit_gram",
                    "carbs_g": 56.0,
                    "available_carbs_g": 56.0,
                    "gi": 73,
                    "gl": 40.9,
                    "confidence": 0.85,
                    "warnings": ["food warning"],
                }
            ],
            "total_gl": 40.9,
            "glucose_impact": {"level": "高"},
        }
        payload = build_meal_analysis_payload(analysis_result)
        rice = payload["detected_foods"][0]

        self.assertEqual(rice["name"], "米饭")
        self.assertEqual(rice["food"], "米饭")
        self.assertEqual(rice["category"], "high_risk")
        self.assertEqual(rice["portion_g"], 200)
        self.assertEqual(rice["portion_source"], "explicit_gram")
        self.assertEqual(rice["carbs_g"], 56.0)
        self.assertEqual(rice["available_carbs_g"], 56.0)
        self.assertEqual(rice["gi"], 73)
        self.assertEqual(rice["gl"], 40.9)
        self.assertEqual(rice["confidence"], 0.85)
        self.assertEqual(rice["warnings"], ["food warning"])

    def test_chat_route_rejects_incomplete_json_body(self) -> None:
        client = self.build_test_client()
        response = client.post(
            "/api/agent/chat",
            json={"text": "hello"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "请求参数不完整或格式不正确"})

    def test_chat_route_blocks_high_risk_medication_stop_question_before_agent_run(
        self,
    ) -> None:
        client, _, engine = self.build_test_client_with_db()
        try:
            with patch.object(RouteAgentChatService, "run_chat") as run_chat:
                response = client.post(
                    "/api/agent/chat",
                    json={"message": "我能不能停二甲双胍"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(payload["data"]["safety_level"], "high")
            self.assertIn("咨询医生", payload["data"]["content"])
            run_chat.assert_not_called()
        finally:
            engine.dispose()

    def test_agent_chat_service_replies_peanut_allergy_warning_for_peanut_butter_meal(
        self,
    ) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(content="这顿饭主食较多，请注意米饭份量。")

        with patch(
            f"{AgentChatService.__module__}.load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("我晚饭想吃米饭配花生酱"))

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["content"], "你对花生过敏哦，不能吃花生酱")

    def test_agent_chat_service_injects_structured_memory_into_normal_text_chat(
        self,
    ) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        fake_bot = _FakeBot(content="晚餐后可以喝温水。")
        try:
            seed_db = session_local()
            seed_db.add(
                AgentMemory(
                    user_id=1,
                    category="preference",
                    key="咖啡",
                    value="晚上不喝",
                    confidence=0.9,
                    source="user_edit",
                    status="active",
                    created_at="2026-05-22T10:00:00",
                    updated_at="2026-05-22T10:00:00",
                )
            )
            seed_db.commit()
            seed_db.close()
            service = AgentChatService(db=cast(Any, session_local()), session_factory=session_local)
            service._bot = fake_bot

            with patch(
                f"{AgentChatService.__module__}.load_nanobot_runtime",
                return_value=(object, _HookBase, object, object, object, object),
            ):
                response = asyncio.run(service.run_chat("晚餐后喝什么比较好"))

            self.assertEqual(response["code"], 0)
            sent_message, session_key = fake_bot.calls[0]
            self.assertEqual(session_key, "diabetes-demo-agent:user:1")
            self.assertIn("长期记忆", sent_message)
            self.assertIn("咖啡晚上不喝", sent_message)
            self.assertIn("用户本轮消息：晚餐后喝什么比较好", sent_message)
        finally:
            engine.dispose()

    def test_agent_chat_service_shortcuts_remember_message_and_persists_memory(
        self,
    ) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        Base.metadata.create_all(bind=engine)
        try:
            service = AgentChatService(
                db=session_local(),
                session_factory=session_local,
            )

            response = asyncio.run(service.run_chat("记住我花生过敏"))

            self.assertEqual(response["code"], 0)
            self.assertEqual(response["data"]["memory_updates"][0]["category"], "allergy")
            self.assertEqual(response["data"]["memory_updates"][0]["key"], "花生")
            self.assertIn("已记住", response["data"]["content"])

            verify_db = session_local()
            try:
                memories = verify_db.execute(
                    text("select category, key, value from agent_memories")
                ).fetchall()
            finally:
                verify_db.close()
            self.assertEqual(len(memories), 1)
            self.assertEqual(tuple(memories[0]), ("allergy", "花生", "过敏"))
        finally:
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

    def test_agent_chat_service_returns_case_reference_insight_without_nanobot(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        db = session_local()
        try:
            db.add(PatientProfile(user_id=1, age=58, diabetes_type="T2DM", bmi=26.0, profile_completed=True))
            db.add_all(
                [
                    GlucoseRecordModel(user_id=1, value=11.2, measure_time="2026-05-22T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-22T19:00:00"),
                    GlucoseRecordModel(user_id=1, value=10.8, measure_time="2026-05-21T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-21T19:00:00"),
                ]
            )
            db.commit()
            service = AgentChatService(db=db, session_factory=session_local)

            response = asyncio.run(service.run_chat("为什么我总是晚饭后高？"))
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["agent_role"], "case_reference")
        self.assertIn("匿名案例", response["data"]["content"])
        self.assertIn("仅供参考，不替代医生建议", response["data"]["content"])

    def test_case_reference_shortcut_preserves_medium_safety_disclaimer(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)
        db = session_local()
        try:
            db.add(PatientProfile(user_id=1, age=58, diabetes_type="T2DM", bmi=26.0, profile_completed=True))
            db.add_all(
                [
                    GlucoseRecordModel(user_id=1, value=11.2, measure_time="2026-05-22T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-22T19:00:00"),
                    GlucoseRecordModel(user_id=1, value=10.8, measure_time="2026-05-21T19:00:00", measure_type="post_meal", source="manual", created_at="2026-05-21T19:00:00"),
                ]
            )
            db.commit()
            service = AgentChatService(db=db, session_factory=session_local)

            response = asyncio.run(service.run_chat("为什么我总是晚饭后高，是不是并发症？"))
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

        self.assertEqual(response["data"]["agent_role"], "case_reference")
        self.assertEqual(response["data"]["safety_level"], "medium")
        self.assertIn("不能替代医生", response["data"]["content"])

    def test_agent_chat_service_prefers_tool_payload(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(
            payload=ToolResultPayload(
                message="💊 当前正式用药计划：\n\n1. 二甲双胍 1片，时间：早餐后，提醒时间：08:00，频率：daily",
                refresh=[],
            )
        )

        with patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("我吃什么药"))

        self.assertEqual(
            response,
            {
                "code": 0,
                "data": {
                    "role": "assistant",
                    "content": "💊 当前正式用药计划：\n\n1. 二甲双胍 1片，时间：早餐后，提醒时间：08:00，频率：daily",
                    "refresh": [],
                    "agent_role": "assistant",
                },
            },
        )

    def test_agent_chat_service_preserves_meal_analysis_from_tool_payload(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(
            payload=ToolResultPayload(
                message="🍽 饮食分析结果",
                refresh=["meal"],
                meal_analysis={
                    "risk_level": "medium",
                    "total_gl": 27,
                    "gl_level": "中",
                    "score": 75,
                    "detected_foods": [{"name": "米饭"}],
                    "suggestion": ["注意控制主食总量"],
                    "summary": "升糖负荷(GL): 27（中级别）",
                },
            )
        )

        with patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("这是我的午餐"))

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["content"], "🍽 饮食分析结果")
        self.assertEqual(response["data"]["refresh"], ["meal"])
        self.assertEqual(response["data"]["agent_role"], "assistant")
        self.assertEqual(response["data"]["meal_analysis"]["total_gl"], 27)
        self.assertEqual(
            response["data"]["meal_analysis"]["detected_foods"][0]["name"], "米饭"
        )

    def test_agent_chat_service_returns_direct_content_when_no_tool_payload(
        self,
    ) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(
            content="请告诉我药名和剂量，我再帮你设置早上9点的提醒。"
        )

        with patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("我每天早上9点要吃药"))

        self.assertEqual(
            response,
            {
                "code": 0,
                "data": {
                    "role": "assistant",
                    "content": "请告诉我药名和剂量，我再帮你设置早上9点的提醒。",
                    "refresh": [],
                    "agent_role": "assistant",
                },
            },
        )

    def test_backend_tool_execute_uses_worker_owned_session(self) -> None:
        request_db = object()
        worker_db = MagicMock()
        service = AgentChatService(
            db=cast(Any, request_db), session_factory=lambda: worker_db
        )

        class CapturingTool(BackendAgentTool):
            @property
            def name(self) -> str:
                return "capture_session"

            @property
            def description(self) -> str:
                return "Capture the database session used during execution."

            @property
            def parameters(self) -> dict[str, Any]:
                return {"type": "object", "properties": {}}

            def run(self, db: Any = None, **kwargs: Any) -> ToolResultPayload:
                self.seen_db = db
                return ToolResultPayload(message="ok", refresh=[])

        tool = CapturingTool(service)

        result = json.loads(asyncio.run(tool.execute()))

        self.assertEqual(result["message"], "ok")
        self.assertIs(tool.seen_db, worker_db)
        self.assertIsNot(tool.seen_db, request_db)
        worker_db.close.assert_called_once_with()

    def test_analyze_diet_tool_uses_nested_analysis_result_for_gl_and_foods(
        self,
    ) -> None:
        tool = AnalyzeDietTool(
            service=cast(Any, type("Service", (), {"db": object()})())
        )
        module = __import__(
            AgentChatService.__module__, fromlist=["build_diet_reply_with_context"]
        )
        fake_result = {
            "data": {
                "content": "prompt",
                "refresh": ["meal"],
            },
            "analysis_result": {
                "summary": "这顿饭总体还可以，但还有优化空间。",
                "risks": ["主食偏多，餐后血糖可能更容易升高"],
                "suggestions": ["注意控制主食总量"],
                "highlights": ["有蛋白质摄入"],
                "score": 75,
                "identified_foods": [
                    {
                        "food": "米饭",
                        "portion_g": 100,
                        "carbs_g": 25,
                        "gi": 83,
                        "gl": 21,
                    }
                ],
                "total_gl": 27,
                "glucose_impact": {
                    "level": "中",
                    "impact": "对血糖有一定影响，需适量",
                    "color": "orange",
                    "estimated_gl": 27,
                },
            },
            "profile": {},
        }

        with patch.object(
            module, "build_diet_reply_with_context", return_value=fake_result
        ):
            payload = tool.run(meal_text="午餐吃了米饭")

        self.assertIn("升糖负荷(GL)：27", payload.message)
        self.assertIn("对血糖有一定影响，需适量", payload.message)
        self.assertIn("米饭", payload.message)
        self.assertIn("GI=83", payload.message)
        self.assertIn("GL=21", payload.message)
        self.assertNotIn("升糖负荷(GL)：0", payload.message)
        self.assertNotIn("未识别到已知食物", payload.message)
        self.assertEqual(payload.refresh, ["meal"])
        self.assertEqual(payload.meal_analysis["total_gl"], 27)
        self.assertEqual(payload.meal_analysis["detected_foods"][0]["name"], "米饭")

    def test_diet_prompt_does_not_include_patient_name(self) -> None:
        module = __import__(
            AgentChatService.__module__,
            fromlist=["build_diet_reply_with_context"],
        )
        _, session_local, engine = self.build_test_client_with_db()
        db = session_local()
        try:
            db.add(
                PatientProfile(
                    user_id=1,
                    name="张三",
                    age=58,
                    diabetes_type="T2DM",
                    bmi=26.0,
                    medications=["二甲双胍"],
                    complications=[],
                    profile_completed=True,
                )
            )
            db.commit()

            result = module.build_diet_reply_with_context(
                "午餐吃了米饭",
                db,
                user_id=1,
            )
        finally:
            db.close()
            engine.dispose()

        content = result["data"]["content"]
        self.assertNotIn("张三", content)
        self.assertNotIn("姓名", content)
        self.assertIn("年龄", content)
        self.assertIn("糖尿病类型", content)

    def test_agent_chat_service_processes_images_via_media_paths(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        fake_bot = _FakeBot(content="我看到了餐盘照片。")
        service._bot = fake_bot
        image = ChatImageInput(
            filename="meal.png",
            content_type="image/png",
            content=b"\x89PNG\r\n\x1a\nIMG",
        )

        module = __import__(
            AgentChatService.__module__,
            fromlist=["load_nanobot_runtime", "load_inbound_message_runtime"],
        )

        class InboundMessageStub:
            def __init__(
                self,
                channel: str,
                sender_id: str,
                chat_id: str,
                content: str,
                media: list[str],
            ) -> None:
                self.channel = channel
                self.sender_id = sender_id
                self.chat_id = chat_id
                self.content = content
                self.media = media

        inbound_message_type = InboundMessageStub

        with patch.object(
            module,
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ), patch.object(
            module,
            "load_inbound_message_runtime",
            return_value=inbound_message_type,
        ):
            response = asyncio.run(service.run_chat("帮我看这顿饭", images=[image]))

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["content"], "我看到了餐盘照片。")
        self.assertEqual(response["data"]["agent_role"], "assistant")
        self.assertEqual(len(fake_bot.calls), 0)
        self.assertEqual(len(fake_bot._loop.processed_messages), 1)
        processed_message, session_key = fake_bot._loop.processed_messages[0]
        self.assertEqual(session_key, "diabetes-demo-agent:user:1")
        self.assertEqual(processed_message.content, "帮我看这顿饭")
        self.assertEqual(len(processed_message.media), 1)
        self.assertFalse(Path(processed_message.media[0]).exists())

    def test_agent_chat_service_returns_friendly_message_when_runtime_unavailable(
        self,
    ) -> None:
        service = AgentChatService(db=cast(Any, object()))
        module = __import__(
            AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
        )

        with patch.object(
            module,
            "load_nanobot_runtime",
            side_effect=ModuleNotFoundError("nanobot missing"),
        ):
            response = asyncio.run(service.run_chat("帮我分析这张图片"))

        self.assertEqual(response["code"], 0)
        self.assertEqual(
            response["data"]["content"],
            "智能体当前不可用，请先检查 nanobot 配置或依赖。",
        )
        self.assertEqual(response["data"]["agent_role"], "assistant")
        self.assertEqual(response["data"]["refresh"], [])

    def test_agent_chat_service_returns_friendly_message_when_image_processing_crashes(
        self,
    ) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot()
        image = ChatImageInput(
            filename="meal.png",
            content_type="image/png",
            content=b"\x89PNG\r\n\x1a\nIMG",
        )

        with patch.object(
            service, "_run_chat_with_images", side_effect=Exception("boom")
        ), patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("帮我看这张图", images=[image]))

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["content"], IMAGE_ANALYSIS_FALLBACK_MESSAGE)
        self.assertEqual(response["data"]["refresh"], [])
        self.assertEqual(response["data"]["agent_role"], "assistant")

    def test_agent_chat_service_normalizes_provider_image_unavailable_reply(
        self,
    ) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(
            content="抱歉，我当前无法直接查看图片内容，请补充文字描述。"
        )
        image = ChatImageInput(
            filename="meal.png",
            content_type="image/png",
            content=b"\x89PNG\r\n\x1a\nIMG",
        )

        with patch.object(
            service,
            "_run_chat_with_images",
            return_value=_FakeRunResult(service._bot.content),
        ), patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("帮我看这张图", images=[image]))

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["content"], IMAGE_ANALYSIS_FALLBACK_MESSAGE)
        self.assertEqual(response["data"]["refresh"], [])
        self.assertEqual(response["data"]["agent_role"], "assistant")

    def test_agent_chat_service_blocks_unverified_action_completion_text(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        service._bot = _FakeBot(content="已帮你设置每天早上9点的用药提醒。")

        with patch.object(
            AgentChatService.__module__
            and __import__(
                AgentChatService.__module__, fromlist=["load_nanobot_runtime"]
            ),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, object, object),
        ):
            response = asyncio.run(service.run_chat("我每天早上9点要吃药"))

        self.assertEqual(response["code"], 0)
        self.assertIn("还没有真正执行", response["data"]["content"])
        self.assertEqual(response["data"]["refresh"], [])

    def test_handle_medication_take_stores_created_at_as_datetime(self) -> None:
        db = MagicMock()
        plan = type(
            "Plan",
            (),
            {
                "plan_id": 1,
                "drug_name": "二甲双胍",
                "dosage": "1片",
                "time_text": "早餐后",
                "remind_time": "08:00",
            },
        )()
        module = __import__(AgentChatService.__module__, fromlist=["find_target_plan"])

        with patch.object(module, "find_target_plan", return_value=(plan, None)):
            response = handle_medication_take("二甲双胍已服药", db)

        record = db.add.call_args.args[0]
        self.assertEqual(response["code"], 0)
        self.assertIsInstance(record.created_at, datetime)

    def test_normalize_frequency_rejects_sub_hourly_cron(self) -> None:
        module = __import__(
            AgentChatService.__module__, fromlist=["normalize_frequency"]
        )

        self.assertEqual(module.normalize_frequency("cron:* * * * *"), "daily")
        self.assertEqual(
            module.normalize_frequency("cron:0 8,20 * * *"), "cron:0 8,20 * * *"
        )
        self.assertEqual(module.normalize_frequency("interval:0h"), "daily")
        self.assertEqual(module.normalize_frequency("interval:bad"), "daily")
        self.assertEqual(module.normalize_frequency("interval:8h"), "interval:8h")

    def test_handle_medication_parse_rejects_invalid_remind_time(self) -> None:
        db = MagicMock()

        with self.assertRaises(ValueError):
            handle_medication_parse(
                drug_name="头孢",
                dosage="10克",
                remind_time="99:99",
                db=db,
            )

        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()

    def test_handle_medication_parse_rejects_blank_structured_fields(self) -> None:
        db = MagicMock()

        with self.assertRaises(ValueError):
            handle_medication_parse(
                drug_name="   ",
                dosage="10克",
                remind_time="09:00",
                db=db,
            )

        with self.assertRaises(ValueError):
            handle_medication_parse(
                drug_name="头孢",
                dosage="   ",
                remind_time="09:00",
                db=db,
            )

        db.add.assert_not_called()
        db.commit.assert_not_called()
        db.refresh.assert_not_called()

    def test_handle_medication_parse_normalizes_chinese_time_and_persists_pending_plan(
        self,
    ) -> None:
        db = MagicMock()

        response = handle_medication_parse(
            drug_name="头孢",
            dosage="10克",
            remind_time="9点",
            time_text="每天早上9点",
            db=db,
        )

        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["refresh"], ["medication"])
        self.assertIn("待确认的用药提醒", response["data"]["content"])
        self.assertIn("头孢", response["data"]["content"])
        self.assertIn("10克", response["data"]["content"])
        self.assertIn("09:00", response["data"]["content"])
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()
        pending_record = db.add.call_args.args[0]
        self.assertEqual(pending_record.drug_name, "头孢")
        self.assertEqual(pending_record.dosage, "10克")
        self.assertEqual(pending_record.remind_time, "09:00")
        self.assertEqual(pending_record.time_text, "每天早上9点")

    def test_handle_medication_parse_defaults_time_text_and_frequency(self) -> None:
        db = MagicMock()

        response = handle_medication_parse(
            drug_name="二甲双胍",
            dosage="1片",
            remind_time="08:00",
            db=db,
        )

        self.assertEqual(response["code"], 0)
        pending_record = db.add.call_args.args[0]
        self.assertEqual(pending_record.time_text, "08:00")
        self.assertEqual(pending_record.frequency, "daily")
        self.assertIn("08:00", response["data"]["content"])

    def test_handle_medication_parse_normalizes_chinese_daily_frequency(self) -> None:
        db = MagicMock()

        response = handle_medication_parse(
            drug_name="二甲双胍",
            dosage="1片",
            remind_time="08:00",
            frequency="每天",
            db=db,
        )

        self.assertEqual(response["code"], 0)
        pending_record = db.add.call_args.args[0]
        self.assertEqual(pending_record.frequency, "daily")

    def test_agent_chat_service_restricts_nanobot_default_tools(self) -> None:
        service = AgentChatService(db=cast(Any, object()))

        class _FakeConsolidator:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

        class _FakeRestrictLoop:
            def __init__(self) -> None:
                self.tools = None
                self.workspace = Path.cwd()
                self.provider = type(
                    "Provider",
                    (),
                    {"generation": type("Generation", (), {"max_tokens": 1024})()},
                )()
                self.model = "openai/gpt-4.1"
                self.sessions = object()
                self.context_window_tokens = 16000
                self.context = type(
                    "Context",
                    (),
                    {"build_messages": staticmethod(lambda *args, **kwargs: [])},
                )()
                self.memory_consolidator = _FakeConsolidator()

        class _FakeRegistry:
            def __init__(self) -> None:
                self._tools: dict[str, Any] = {}

            def register(self, tool: Any) -> None:
                self._tools[tool.name] = tool

            @property
            def tool_names(self) -> list[str]:
                return list(self._tools.keys())

            def get_definitions(self) -> list[dict[str, Any]]:
                return []

        fake_bot = _FakeBot()
        fake_bot._loop = _FakeRestrictLoop()

        with patch.object(
            __import__(AgentChatService.__module__, fromlist=["load_nanobot_runtime"]),
            "load_nanobot_runtime",
            return_value=(object, _HookBase, object, object, _FakeRegistry, object),
        ):
            service._restrict_nanobot_tools(fake_bot)

        self.assertIsNotNone(fake_bot._loop)
        self.assertIsNotNone(fake_bot._loop.tools)
        self.assertEqual(
            set(fake_bot._loop.tools.tool_names),
            {
                "record_glucose",
                "analyze_diet",
                "parse_medication_plan",
                "confirm_medication_plan",
                "reject_medication_plan",
                "log_medication_status",
                "query_medication_plans",
                "guidance_fallback",
                "query_hba1c",
                "trigger_hypo_protocol",
                "query_tir",
                "log_exercise",
                "query_exercise",
                "query_screening",
                "calculate_insulin",
                "remember_user_fact",
                "query_user_memory",
                "forget_user_fact",
                "search_guideline_knowledge",
                "query_similar_cases",
            },
        )

        parse_tool = fake_bot._loop.tools._tools["parse_medication_plan"]
        self.assertEqual(
            parse_tool.parameters["required"],
            ["drug_name", "dosage", "remind_time"],
        )
        self.assertIn("drug_name", parse_tool.parameters["properties"])
        self.assertIn("dosage", parse_tool.parameters["properties"])
        self.assertIn("remind_time", parse_tool.parameters["properties"])

        forget_tool = fake_bot._loop.tools._tools["forget_user_fact"]
        self.assertEqual(forget_tool.parameters["required"], ["key"])
        self.assertIn("key", forget_tool.parameters["properties"])
        self.assertIn("category", forget_tool.parameters["properties"])
        self.assertNotIn("memory_id", forget_tool.parameters["properties"])

        for tool in fake_bot._loop.tools._tools.values():
            self.assertTrue(hasattr(tool, "cast_params"))
            self.assertTrue(hasattr(tool, "validate_params"))
            self.assertTrue(hasattr(tool, "to_schema"))
            self.assertTrue(hasattr(tool, "concurrency_safe"))

    def test_agent_chat_service_returns_hypo_knowledge_with_citations(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        Base.metadata.create_all(bind=engine)
        try:
            seed_db = session_local()
            try:
                ingest_knowledge_document(
                    seed_db,
                    {
                        "title": "低血糖处理",
                        "source_name": "糖尿病管理知识库",
                        "source_version": "demo-v1",
                        "source_url": "https://example.test/hypo",
                        "license_note": "demo",
                        "content": "topic: hypo\n低血糖时按15-15规则处理：立即摄入15克快速糖，15分钟后复测血糖。",
                    },
                )
            finally:
                seed_db.close()

            service = AgentChatService(db=session_local(), session_factory=session_local)
            response = asyncio.run(service.run_chat("低血糖怎么办"))

            self.assertEqual(response["code"], 0)
            self.assertIn("15-15", response["data"]["content"])
            self.assertIn("依据", response["data"]["content"])
            self.assertEqual(response["data"]["citations"][0]["source_name"], "糖尿病管理知识库")
            self.assertEqual(response["data"]["citations"][0]["source_version"], "demo-v1")
        finally:
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

    def test_search_guideline_knowledge_tool_returns_structured_snippets(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        Base.metadata.create_all(bind=engine)
        db = session_local()
        try:
            ingest_knowledge_document(
                db,
                {
                    "title": "低血糖处理",
                    "source_name": "糖尿病管理知识库",
                    "source_version": "demo-v1",
                    "source_url": "https://example.test/hypo",
                    "license_note": "demo",
                    "content": "topic: hypo\n低血糖时按15-15规则处理。",
                },
            )
            service = AgentChatService(db=db, session_factory=session_local)
            payload = SearchGuidelineKnowledgeTool(service).run(
                query="低血糖怎么办",
                topic="hypo",
            )
            serialized = json.loads(payload.to_json())
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

        assert serialized["snippets"]
        snippet = serialized["snippets"][0]
        assert snippet["content"] == "低血糖时按15-15规则处理。"
        assert snippet["source_name"] == "糖尿病管理知识库"
        assert snippet["source_version"] == "demo-v1"
        assert snippet["topic"] == "hypo"

    def test_nanobot_runtime_config_is_repo_local(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        temp_root = Path("backend") / ".tmp_test_nanobot_runtime"

        try:
            temp_root.mkdir(parents=True, exist_ok=True)
            (temp_root / "config.json").write_text(
                json.dumps({"providers": {"openai": {"apiBase": "http://example.test/v1"}}}),
                encoding="utf-8",
            )
            module = __import__(
                AgentChatService.__module__, fromlist=["BACKEND_RUNTIME_ROOT"]
            )
            with patch.object(module, "BACKEND_RUNTIME_ROOT", temp_root), patch.object(
                module, "NANOBOT_WORKSPACE", temp_root / "workspace"
            ), patch.object(module, "NANOBOT_CONFIG_PATH", temp_root / "config.json"), patch.dict(
                os.environ, {"OPENAI_API_KEY": "test-runtime-key"}, clear=False
            ):
                service._ensure_runtime_paths()
                config = json.loads(
                    (temp_root / "config.json").read_text(encoding="utf-8")
                )

            self.assertEqual(
                Path(config["agents"]["defaults"]["workspace"]), temp_root / "workspace"
            )
            self.assertTrue(config["tools"]["restrictToWorkspace"])
            self.assertFalse(config["tools"]["exec"]["enable"])
            self.assertEqual(config["tools"]["mcpServers"], {})
            self.assertEqual(config["providers"]["openai"]["apiKey"], "${OPENAI_API_KEY}")
            self.assertNotIn("test-runtime-key", json.dumps(config))
            self.assertEqual(
                config["providers"]["openai"].get("apiBase"),
                "http://example.test/v1",
            )
            self.assertTrue((temp_root / "workspace" / "AGENTS.md").exists())
            self.assertTrue((temp_root / "workspace" / "TOOLS.md").exists())
        finally:
            if temp_root.exists():
                for path in sorted(temp_root.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                temp_root.rmdir()

    def test_nanobot_runtime_config_uses_openai_base_url_from_env_when_missing(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        temp_root = Path("backend") / ".tmp_test_nanobot_runtime_env_base"
        module = __import__(AgentChatService.__module__, fromlist=["BACKEND_RUNTIME_ROOT"])

        try:
            temp_root.mkdir(parents=True, exist_ok=True)
            (temp_root / "config.json").write_text(json.dumps({}), encoding="utf-8")
            with patch.object(module, "BACKEND_RUNTIME_ROOT", temp_root), patch.object(
                module, "NANOBOT_WORKSPACE", temp_root / "workspace"
            ), patch.object(module, "NANOBOT_CONFIG_PATH", temp_root / "config.json"), patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-runtime-key",
                    "OPENAI_BASE_URL": "http://third-party.example/v1",
                },
                clear=False,
            ):
                service._ensure_runtime_paths()
                config = json.loads((temp_root / "config.json").read_text(encoding="utf-8"))

            self.assertEqual(
                config["providers"]["openai"].get("apiBase"),
                "http://third-party.example/v1",
            )
        finally:
            if temp_root.exists():
                for path in sorted(temp_root.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                temp_root.rmdir()

    def test_nanobot_runtime_config_requires_openai_api_key(self) -> None:
        service = AgentChatService(db=cast(Any, object()))
        temp_root = Path("backend") / ".tmp_test_nanobot_runtime_missing_key"
        module = __import__(AgentChatService.__module__, fromlist=["BACKEND_RUNTIME_ROOT"])

        try:
            with patch.object(module, "BACKEND_RUNTIME_ROOT", temp_root), patch.object(
                module, "NANOBOT_WORKSPACE", temp_root / "workspace"
            ), patch.object(module, "NANOBOT_CONFIG_PATH", temp_root / "config.json"), patch.dict(
                os.environ, {"OPENAI_API_KEY": ""}, clear=False
            ):
                with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                    service._ensure_runtime_paths()
        finally:
            if temp_root.exists():
                for path in sorted(temp_root.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        path.rmdir()
                temp_root.rmdir()


if __name__ == "__main__":
    unittest.main()
