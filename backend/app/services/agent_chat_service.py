from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, cast

from croniter import croniter
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..db.database import SessionLocal
from ..models.medication_model import (
    MedicationPlan,
    MedicationTakenRecord,
    PendingMedicationPlan,
)
from .agent_intent_service import classify_agent_intent
from .agent_memory_service import (
    archive_memory_by_key,
    extract_and_persist_memory,
    list_memories,
    memory_to_dict,
    upsert_memory,
)
from .case_reference_service import (
    build_user_case_features,
    format_case_insight,
    search_similar_cases,
)
from .diet_knowledge import DIABETES_DIET_GUIDELINES, get_guidelines_for_complications
from .glucose_ingestion_service import GlucoseIngestionInput, ingest_glucose_reading
from .knowledge_service import build_cited_context, format_citations, search_knowledge
from .agent_roles import get_role_config, get_tool_names_for_role
from .medical_safety_service import (
    append_disclaimer,
    build_safety_response,
    classify_medical_risk,
)
from .patient_service import get_patient_profile, get_recent_glucose


DEFAULT_USER_ID = 1
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")
NANOBOT_SOURCE_ROOT = PROJECT_ROOT / "nanobot-main" / "nanobot-main"
BACKEND_RUNTIME_ROOT = PROJECT_ROOT / "backend" / ".nanobot_runtime"
CHAT_UPLOAD_ROOT = BACKEND_RUNTIME_ROOT / "chat_uploads"
NANOBOT_WORKSPACE = BACKEND_RUNTIME_ROOT / "workspace"
NANOBOT_CONFIG_PATH = BACKEND_RUNTIME_ROOT / "config.json"
WORKSPACE_BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md")
ALLOWED_CHAT_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
CHAT_IMAGE_MAX_COUNT = 3
CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_IMAGE_EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
IMAGE_ANALYSIS_FALLBACK_MESSAGE = (
    "当前模型暂时不能直接分析这张图片。你可以补充图片里的关键信息，我继续帮你分析。"
)
PEANUT_BUTTER_MEAL_TRIGGER = "我晚饭想吃米饭配花生酱"
PEANUT_ALLERGY_WARNING_RESPONSE = "你对花生过敏哦，不能吃花生酱"


def ensure_nanobot_source_on_path() -> None:
    source_root = str(NANOBOT_SOURCE_ROOT)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)


ensure_nanobot_source_on_path()

_nanobot_runtime_cache: (
    tuple[type[Any], type[Any], type[Any], type[Any], type[Any], Any] | None
) = None


def load_nanobot_runtime() -> (
    tuple[type[Any], type[Any], type[Any], type[Any], type[Any], Any]
):
    global _nanobot_runtime_cache
    if _nanobot_runtime_cache is not None:
        return _nanobot_runtime_cache

    package_name = "nano" + "bot"
    nanobot_module = __import__(package_name, fromlist=["Nanobot"])
    hook_module = __import__(
        f"{package_name}.agent.hook", fromlist=["AgentHook", "AgentHookContext"]
    )
    base_tool_module = __import__(f"{package_name}.agent.tools.base", fromlist=["Tool"])
    registry_module = __import__(
        f"{package_name}.agent.tools.registry", fromlist=["ToolRegistry"]
    )
    loader_module = __import__(
        f"{package_name}.config.loader", fromlist=["set_config_path"]
    )
    _nanobot_runtime_cache = (
        nanobot_module.Nanobot,
        hook_module.AgentHook,
        hook_module.AgentHookContext,
        base_tool_module.Tool,
        registry_module.ToolRegistry,
        loader_module.set_config_path,
    )
    return _nanobot_runtime_cache


def format_agent_response(
    content: str,
    refresh: list[str],
    meal_analysis: dict[str, Any] | None = None,
    safety_level: str | None = None,
    citations: list[dict[str, Any]] | None = None,
    quick_replies: list[str] | None = None,
    memory_updates: list[dict[str, Any]] | None = None,
    agent_role: str = "assistant",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "refresh": refresh,
        "agent_role": agent_role,
    }
    if meal_analysis is not None:
        data["meal_analysis"] = meal_analysis
    if safety_level is not None:
        data["safety_level"] = safety_level
    if citations is not None:
        data["citations"] = citations
    if quick_replies is not None:
        data["quick_replies"] = quick_replies
    if memory_updates is not None:
        data["memory_updates"] = memory_updates
    return {
        "code": 0,
        "data": data,
    }


def detect_chat_image_mime(content: bytes) -> str | None:
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_inbound_message_runtime() -> type[Any]:
    package_name = "nano" + "bot"
    events_module = __import__(
        f"{package_name}.bus.events", fromlist=["InboundMessage"]
    )
    return cast(type[Any], events_module.InboundMessage)


def build_workspace_bootstrap() -> dict[str, str]:
    tools_md = """# Tooling Rules

你只能使用当前运行时注入的糖尿病业务工具。

## 必须遵守

- 当用户要求执行动作时，优先调用对应工具，而不是口头声称“已记录/已设置”。
- 不要尝试访问 shell、文件系统、网页或 MCP。
- 若工具已经返回结构化业务结果，请基于该结果给出简短中文回复。
- 设置用药提醒时，由你在多轮对话中收集药名、剂量、提醒时间；信息不完整时先追问，不要把整句自然语言交给后端解析。
- 只有当药名、剂量、提醒时间都明确后，才调用 `parse_medication_plan` 提交结构化字段创建待确认计划。

## 工具选择

- `record_glucose`：记录血糖
- `analyze_diet`：分析饮食
- `parse_medication_plan`：在药名、剂量、提醒时间都明确后，提交结构化字段创建新的待确认用药提醒
- `confirm_medication_plan`：确认最近待确认提醒
- `reject_medication_plan`：取消最近待确认提醒
- `log_medication_status`：记录已服药或漏服
- `query_medication_plans`：查询正式用药计划
- `guidance_fallback`：返回能力说明
"""

    user_md = """# Backend Role

这是一个糖尿病管理演示系统的后端智能体。

- 默认用户 ID 固定为 1
- 主要任务：记录血糖、饮食分析、用药提醒解析/确认/取消、服药执行记录、查询用药计划
- 响应应保持简短、直接、中文输出
"""

    soul_md = """# Soul

你是一个谨慎、可靠的糖尿病管理后端助手。

- 准确优先于花哨表达
- 不编造已经执行过的数据库动作
- 对健康相关结论保持稳健和保守
"""

    return {
        "AGENTS.md": AGENT_SYSTEM_PROMPT,
        "SOUL.md": soul_md,
        "USER.md": user_md,
        "TOOLS.md": tools_md,
    }


def _schema_property_errors(name: str, value: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")

    if schema_type == "string":
        if not isinstance(value, str):
            errors.append(f"{name} should be string")
        else:
            min_length = schema.get("minLength")
            if isinstance(min_length, int) and len(value) < min_length:
                errors.append(f"{name} must be at least {min_length} chars")
            max_length = schema.get("maxLength")
            if isinstance(max_length, int) and len(value) > max_length:
                errors.append(f"{name} must be at most {max_length} chars")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{name} should be number")
        else:
            minimum = schema.get("minimum")
            if isinstance(minimum, (int, float)) and value < minimum:
                errors.append(f"{name} must be >= {minimum}")

    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        errors.append(f"{name} must be one of {allowed}")
    return errors


def get_latest_pending_plan(
    db: Session, user_id: int = DEFAULT_USER_ID
) -> PendingMedicationPlan | None:
    return (
        db.query(PendingMedicationPlan)
        .filter(
            PendingMedicationPlan.user_id == user_id,
            PendingMedicationPlan.confirm_status == "pending",
        )
        .order_by(PendingMedicationPlan.pending_id.desc())
        .first()
    )


def get_active_plans(
    db: Session, user_id: int = DEFAULT_USER_ID
) -> list[MedicationPlan]:
    plans = (
        db.query(MedicationPlan)
        .filter(
            MedicationPlan.user_id == user_id,
            MedicationPlan.status == "active",
        )
        .order_by(MedicationPlan.plan_id.desc())
        .all()
    )
    return cast(list[MedicationPlan], plans)


def detect_drug_name_from_message(message: str) -> str | None:
    known_drugs = ["二甲双胍", "阿卡波糖", "格列美脲", "胰岛素", "恩格列净", "瑞格列奈"]
    for drug in known_drugs:
        if drug in message:
            return drug
    return None


def find_target_plan(
    db: Session,
    message: str,
    user_id: int = DEFAULT_USER_ID,
) -> tuple[MedicationPlan | None, str | None]:
    active_plans = get_active_plans(db, user_id=user_id)
    if len(active_plans) == 0:
        return None, "当前还没有正式用药计划，请先告诉我要设置什么药物提醒。"

    drug_name = detect_drug_name_from_message(message)
    if drug_name is not None:
        for plan in active_plans:
            plan_drug_name = cast(str | None, cast(Any, plan.drug_name))
            if plan_drug_name is not None and plan_drug_name == drug_name:
                return plan, None
        return None, f"我没有找到 {drug_name} 的正式用药计划。"

    if len(active_plans) == 1:
        return active_plans[0], None

    return (
        None,
        "我识别到你有多个启用中的药物计划，请在消息里带上药名，比如：我已经吃了二甲双胍。",
    )


def build_plan_text(plans: list[MedicationPlan]) -> str:
    if not plans:
        return "你当前还没有正式用药计划。"

    lines: list[str] = []
    for idx, plan in enumerate(plans, start=1):
        lines.append(
            f"{idx}. {plan.drug_name} {plan.dosage}，时间：{plan.time_text}，提醒时间：{plan.remind_time}，频率：{plan.frequency}"
        )
    return "\n".join(lines)


FOOD_GI_DATA = {
    "米饭": {"carbs_per_100g": 28, "gi": 73, "default_portion_g": 150},
    "白米饭": {"carbs_per_100g": 28, "gi": 73, "default_portion_g": 150},
    "粥": {"carbs_per_100g": 13, "gi": 69, "default_portion_g": 250},
    "面条": {"carbs_per_100g": 25, "gi": 61, "default_portion_g": 200},
    "方便面": {"carbs_per_100g": 26, "gi": 70, "default_portion_g": 100},
    "包子": {"carbs_per_100g": 25, "gi": 88, "default_portion_g": 100},
    "馒头": {"carbs_per_100g": 25, "gi": 85, "default_portion_g": 100},
    "面包": {"carbs_per_100g": 26, "gi": 75, "default_portion_g": 100},
    "饺子": {"carbs_per_100g": 22, "gi": 80, "default_portion_g": 150},
    "炒饭": {"carbs_per_100g": 26, "gi": 78, "default_portion_g": 200},
    "炒面": {"carbs_per_100g": 24, "gi": 72, "default_portion_g": 200},
    "馄饨": {"carbs_per_100g": 18, "gi": 65, "default_portion_g": 300},
    "烧麦": {"carbs_per_100g": 23, "gi": 82, "default_portion_g": 100},
    "油条": {"carbs_per_100g": 21, "gi": 75, "default_portion_g": 100},
    "奶茶": {"carbs_per_100g": 10, "gi": 67, "default_portion_g": 500},
    "可乐": {"carbs_per_100g": 11, "gi": 60, "default_portion_g": 330},
    "雪碧": {"carbs_per_100g": 11, "gi": 59, "default_portion_g": 330},
    "果汁": {"carbs_per_100g": 12, "gi": 66, "default_portion_g": 250},
    "蛋糕": {"carbs_per_100g": 26, "gi": 82, "default_portion_g": 100},
    "千层蛋糕": {"carbs_per_100g": 28, "gi": 80, "default_portion_g": 100},
    "甜点": {"carbs_per_100g": 25, "gi": 80, "default_portion_g": 100},
    "冰淇淋": {"carbs_per_100g": 17, "gi": 51, "default_portion_g": 100},
    "巧克力": {"carbs_per_100g": 27, "gi": 49, "default_portion_g": 50},
    "炸鸡": {"carbs_per_100g": 9, "gi": 75, "default_portion_g": 150},
    "薯条": {"carbs_per_100g": 22, "gi": 75, "default_portion_g": 120},
    "汉堡": {"carbs_per_100g": 24, "gi": 66, "default_portion_g": 200},
    "鸡蛋": {"carbs_per_100g": 1, "gi": 0, "default_portion_g": 60},
    "牛奶": {"carbs_per_100g": 5, "gi": 39, "default_portion_g": 250},
    "豆浆": {"carbs_per_100g": 2, "gi": 34, "default_portion_g": 250},
    "豆腐": {"carbs_per_100g": 2, "gi": 44, "default_portion_g": 150},
    "鸡肉": {"carbs_per_100g": 0, "gi": 0, "default_portion_g": 150},
    "鸡胸肉": {"carbs_per_100g": 0, "gi": 0, "default_portion_g": 150},
    "鱼": {"carbs_per_100g": 0, "gi": 0, "default_portion_g": 150},
    "虾": {"carbs_per_100g": 1, "gi": 0, "default_portion_g": 100},
    "牛肉": {"carbs_per_100g": 0, "gi": 0, "default_portion_g": 150},
    "瘦肉": {"carbs_per_100g": 2, "gi": 0, "default_portion_g": 100},
    "肉丸": {"carbs_per_100g": 3, "gi": 55, "default_portion_g": 100},
    "青菜": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 100},
    "蔬菜": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 100},
    "西兰花": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 100},
    "菠菜": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 100},
    "白菜": {"carbs_per_100g": 2, "gi": 15, "default_portion_g": 100},
    "生菜": {"carbs_per_100g": 2, "gi": 15, "default_portion_g": 100},
    "黄瓜": {"carbs_per_100g": 2, "gi": 15, "default_portion_g": 100},
    "番茄": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 150},
    "西红柿": {"carbs_per_100g": 3, "gi": 15, "default_portion_g": 150},
    "芹菜": {"carbs_per_100g": 2, "gi": 15, "default_portion_g": 100},
    "茄子": {"carbs_per_100g": 4, "gi": 15, "default_portion_g": 100},
    "土豆": {"carbs_per_100g": 17, "gi": 62, "default_portion_g": 100},
    "苹果": {"carbs_per_100g": 14, "gi": 36, "default_portion_g": 200},
    "香蕉": {"carbs_per_100g": 23, "gi": 51, "default_portion_g": 120},
    "橙子": {"carbs_per_100g": 12, "gi": 43, "default_portion_g": 150},
    "葡萄": {"carbs_per_100g": 17, "gi": 59, "default_portion_g": 100},
    "西瓜": {"carbs_per_100g": 6, "gi": 72, "default_portion_g": 300},
    "炖菜": {"carbs_per_100g": 8, "gi": 40, "default_portion_g": 200},
    "炖肉": {"carbs_per_100g": 3, "gi": 30, "default_portion_g": 150},
}

NEGATION_MARKERS = (
    "没吃",
    "未吃",
    "没喝",
    "未喝",
    "不吃",
    "不喝",
    "不要",
    "不加",
    "没要",
    "没有吃",
    "没有喝",
    "本来想吃但没吃",
    "本来想喝但没喝",
)

KNOWN_UNRECOGNIZED_FOOD_HINTS = (
    "煲仔饭",
    "叉烧包",
    "零食",
)

MEAL_TEXT_MAX_LENGTH = 500

UNRECOGNIZED_SPLIT_PATTERN = re.compile(r"和|以及|还有|跟|与|、|搭配|配")
UNRECOGNIZED_CLEANUP_PATTERN = re.compile(
    r"今天|早餐|早饭|午餐|午饭|晚餐|晚饭|早上|中午|晚上|刚才|我|只|又|也|还|然后|本来|想|改|"
    r"吃了|喝了|吃|喝|要了|点了|摄入|食用|加了|加|少量|一点|小份|正常|很多|大份"
)
UNRECOGNIZED_PORTION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:g|克|ml|毫升|碗|杯|个|份|两)|[一二两三四五六七八九十半]+\s*(?:碗|杯|个|份|两)"
)
UNRECOGNIZED_CANDIDATE_PATTERN = re.compile(r"^[一-鿿]{2,12}$")

UNIT_SOURCE_BY_TEXT = {
    "碗": "unit_mapping",
    "杯": "unit_mapping",
    "个": "unit_mapping",
    "份": "unit_mapping",
}

FUZZY_PORTION_FACTORS = {
    "一点": 0.3,
    "少量": 0.4,
    "小份": 0.7,
    "正常": 1.0,
    "很多": 1.5,
    "大份": 1.5,
}

CHINESE_NUMBER_VALUES = {
    "一": 1.0,
    "二": 2.0,
    "两": 2.0,
    "三": 3.0,
    "四": 4.0,
    "五": 5.0,
    "半": 0.5,
}


def _parse_chinese_integer(text: str) -> float | None:
    digit_values = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digit_values:
        return float(digit_values[text])
    if text == "十":
        return 10.0

    tens_match = re.fullmatch(r"([一二两三四五六七八九])?十([一二两三四五六七八九])?", text)
    if not tens_match:
        return None

    tens_text, ones_text = tens_match.groups()
    tens = digit_values.get(tens_text, 1) if tens_text else 1
    ones = digit_values.get(ones_text, 0) if ones_text else 0
    return float(tens * 10 + ones)



def _strip_quantity_unit(text: str) -> str:
    for unit_text in UNIT_SOURCE_BY_TEXT:
        if text.endswith(unit_text):
            return text[: -len(unit_text)]
    return text



def _parse_quantity_number(text: str) -> float | None:
    if not text:
        return None

    normalized = re.sub(r"\s+", "", text.strip())
    if not normalized:
        return None

    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return float(normalized)

    if normalized in CHINESE_NUMBER_VALUES:
        return CHINESE_NUMBER_VALUES[normalized]

    half_match = re.fullmatch(r"(.+?)(个|碗|杯|份)?半", normalized)
    if half_match:
        base_text = half_match.group(1)
        base_value = _parse_quantity_number(base_text)
        if base_value is not None:
            return base_value + 0.5

    stripped_unit = _strip_quantity_unit(normalized)
    if stripped_unit != normalized:
        unitless_value = _parse_quantity_number(stripped_unit)
        if unitless_value is not None:
            return unitless_value

    chinese_integer_value = _parse_chinese_integer(normalized)
    if chinese_integer_value is not None:
        return chinese_integer_value

    return None



def _resolve_portion(food_name: str, context: str) -> tuple[float, str, float]:
    data = FOOD_GI_DATA[food_name]
    default_portion = float(data["default_portion_g"])
    lowered_context = context.lower()
    lowered_food_name = food_name.lower()
    start_index = lowered_context.find(lowered_food_name)

    if start_index == -1:
        before_near = context[-12:]
        after_near = context[:12]
    else:
        end_index = start_index + len(food_name)
        before_near = context[max(0, start_index - 12) : start_index]
        after_near = context[end_index : min(len(context), end_index + 12)]

    def _match_before(pattern: str, flags: int = 0) -> re.Match[str] | None:
        return re.search(pattern, before_near, flags=flags)

    def _match_after(pattern: str, flags: int = 0) -> re.Match[str] | None:
        return re.search(pattern, after_near, flags=flags)

    explicit_gram_patterns = (
        (r"(\d+(?:\.\d+)?)\s*(?:g|克)\s*$", _match_before),
        (r"^\s*(\d+(?:\.\d+)?)\s*(?:g|克)", _match_after),
    )
    for pattern, matcher in explicit_gram_patterns:
        explicit_gram_match = matcher(pattern, flags=re.IGNORECASE)
        if explicit_gram_match:
            return float(explicit_gram_match.group(1)), "explicit_gram", 0.95

    explicit_ml_patterns = (
        (r"(\d+(?:\.\d+)?)\s*(?:ml|毫升)\s*$", _match_before),
        (r"^\s*(\d+(?:\.\d+)?)\s*(?:ml|毫升)", _match_after),
    )
    for pattern, matcher in explicit_ml_patterns:
        explicit_ml_match = matcher(pattern, flags=re.IGNORECASE)
        if explicit_ml_match:
            return float(explicit_ml_match.group(1)), "explicit_ml", 0.85

    unit_pattern_before = r"((?:\d+(?:\.\d+)?)|[一二两三四五六七八九十半]+)?\s*(碗|杯|个|份)(半)?\s*$"
    unit_pattern_after = r"^\s*((?:\d+(?:\.\d+)?)|[一二两三四五六七八九十半]+)?\s*(碗|杯|个|份)(半)?"
    for unit_match in (
        _match_before(unit_pattern_before),
        _match_after(unit_pattern_after),
    ):
        if unit_match:
            quantity_prefix = unit_match.group(1) or "一"
            unit_text = unit_match.group(2)
            quantity_suffix = unit_match.group(3) or ""
            quantity_text = (
                f"{quantity_prefix}{unit_text}{quantity_suffix}"
                if quantity_suffix
                else quantity_prefix
            )
            quantity = _parse_quantity_number(quantity_text) or 1.0
            source = "unit_mapping_scaled" if quantity != 1.0 else UNIT_SOURCE_BY_TEXT[unit_text]
            return round(default_portion * quantity, 1), source, 0.8

    nearby_text = before_near + after_near
    for fuzzy_text, factor in FUZZY_PORTION_FACTORS.items():
        if fuzzy_text in nearby_text:
            return round(default_portion * factor, 1), "fuzzy_estimated", 0.55

    return default_portion, "default", 0.6


def _calculate_gl(
    food_name: str,
    portion_g: float | None = None,
    portion_source: str = "default",
    portion_confidence: float = 0.6,
    raw_text: str | None = None,
) -> dict[str, Any] | None:
    food_name = food_name.strip()
    if food_name not in FOOD_GI_DATA:
        return None

    data = FOOD_GI_DATA[food_name]
    portion = float(data["default_portion_g"] if portion_g is None else portion_g)
    available_carbs = data["carbs_per_100g"] * portion / 100
    gi = data["gi"]
    gl = available_carbs * gi / 100
    confidence = round(0.9 * portion_confidence, 2)

    return {
        "food": food_name,
        "raw_text": raw_text or food_name,
        "portion_g": round(portion, 1),
        "portion_source": portion_source,
        "carbs_g": round(available_carbs, 1),
        "available_carbs_g": round(available_carbs, 1),
        "gi": gi,
        "gl": round(gl, 1),
        "confidence": confidence,
        "warnings": ["使用默认份量估算"] if portion_source == "default" else [],
    }


def _estimate_blood_glucose_impact(total_gl: float) -> dict[str, Any]:
    if total_gl < 10:
        level = "低"
        impact = "对血糖影响较小"
        color = "green"
    elif total_gl < 20:
        level = "中"
        impact = "对血糖有一定影响，需适量"
        color = "orange"
    else:
        level = "高"
        impact = "可能导致血糖明显升高，建议搭配蔬菜和蛋白"
        color = "red"

    return {
        "level": level,
        "impact": impact,
        "color": color,
        "estimated_gl": round(total_gl, 1),
    }


def _extract_unique_food_names(text: str) -> list[str]:
    matched_foods: list[str] = []
    occupied_ranges: list[tuple[int, int]] = []

    for food_name in sorted(FOOD_GI_DATA.keys(), key=len, reverse=True):
        start_index = text.find(food_name.lower())
        if start_index == -1:
            continue
        end_index = start_index + len(food_name)
        if any(
            not (end_index <= occupied_start or start_index >= occupied_end)
            for occupied_start, occupied_end in occupied_ranges
        ):
            continue
        matched_foods.append(food_name)
        occupied_ranges.append((start_index, end_index))

    return matched_foods



def _has_negation_near(text: str, start_index: int) -> bool:
    clause_delimiters = "，。；,.!！?？"

    clause_start = start_index
    while clause_start > 0 and text[clause_start - 1] not in clause_delimiters:
        clause_start -= 1

    clause_end = start_index
    while clause_end < len(text) and text[clause_end] not in clause_delimiters:
        clause_end += 1

    clause_text = text[clause_start:clause_end]
    food_offset = start_index - clause_start
    prefix_text = clause_text[:food_offset]
    return any(marker in prefix_text for marker in NEGATION_MARKERS)



def _extract_food_occurrences(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    occurrences: list[dict[str, Any]] = []
    ignored_foods: list[dict[str, Any]] = []
    occupied_ranges: list[tuple[int, int]] = []

    for food_name in sorted(FOOD_GI_DATA.keys(), key=len, reverse=True):
        search_start = 0
        lowered_food_name = food_name.lower()
        while True:
            start_index = text.find(lowered_food_name, search_start)
            if start_index == -1:
                break
            end_index = start_index + len(lowered_food_name)
            search_start = end_index

            if any(
                not (end_index <= occupied_start or start_index >= occupied_end)
                for occupied_start, occupied_end in occupied_ranges
            ):
                continue

            occupied_ranges.append((start_index, end_index))
            raw_start = max(0, start_index - 8)
            raw_end = min(len(text), end_index + 8)
            context = text[raw_start:raw_end]

            if _has_negation_near(text, start_index):
                ignored_foods.append(
                    {
                        "raw_text": context,
                        "food_name": food_name,
                        "reason": "用户明确否定摄入",
                    }
                )
                continue

            portion_g, portion_source, portion_confidence = _resolve_portion(
                food_name, context
            )
            occurrences.append(
                {
                    "food_name": food_name,
                    "raw_text": context,
                    "portion_g": portion_g,
                    "portion_source": portion_source,
                    "portion_confidence": portion_confidence,
                    "start_index": start_index,
                }
            )

    occurrences.sort(key=lambda item: int(item["start_index"]))
    for occurrence in occurrences:
        occurrence.pop("start_index", None)
    return occurrences, ignored_foods



def _clean_unrecognized_candidate(text: str) -> str:
    without_known_foods = text
    for food_name in sorted(FOOD_GI_DATA.keys(), key=len, reverse=True):
        without_known_foods = without_known_foods.replace(food_name.lower(), "")

    without_portion = UNRECOGNIZED_PORTION_PATTERN.sub("", without_known_foods)
    without_noise = UNRECOGNIZED_CLEANUP_PATTERN.sub("", without_portion)
    return without_noise.strip(" \t\n\r，。；,.!！?？：:的了")


def _extract_unrecognized_items(
    text: str, recognized_foods: list[str]
) -> list[dict[str, str]]:
    recognized_food_set = {food.lower() for food in recognized_foods}
    unrecognized_names: list[str] = []

    def add_candidate(candidate: str, start_index: int) -> None:
        normalized_candidate = candidate.lower().strip()
        if not normalized_candidate:
            return
        if normalized_candidate in recognized_food_set:
            return
        if normalized_candidate in unrecognized_names:
            return
        if _has_negation_near(text, start_index):
            return
        unrecognized_names.append(normalized_candidate)

    for hint in KNOWN_UNRECOGNIZED_FOOD_HINTS:
        lowered_hint = hint.lower()
        search_start = 0
        while True:
            start_index = text.find(lowered_hint, search_start)
            if start_index == -1:
                break
            search_start = start_index + len(lowered_hint)
            add_candidate(lowered_hint, start_index)

    search_start = 0
    for clause in re.split(r"[，。；,.!！?？]", text):
        if not clause:
            continue
        clause_start = text.find(clause, search_start)
        search_start = clause_start + len(clause) if clause_start >= 0 else search_start
        if clause_start >= 0 and _has_negation_near(text, clause_start):
            continue

        part_start_offset = 0
        for part in UNRECOGNIZED_SPLIT_PATTERN.split(clause):
            if not part:
                part_start_offset += 1
                continue
            part_start = clause.find(part, part_start_offset)
            part_start_offset = part_start + len(part) if part_start >= 0 else part_start_offset
            absolute_start = clause_start + part_start if clause_start >= 0 and part_start >= 0 else 0
            if any(marker in part for marker in NEGATION_MARKERS):
                continue
            candidate = _clean_unrecognized_candidate(part)
            if UNRECOGNIZED_CANDIDATE_PATTERN.fullmatch(candidate):
                add_candidate(candidate, absolute_start)

    return [
        {
            "raw_text": name,
            "reason": "食物库暂未收录，未计入 GL",
        }
        for name in unrecognized_names
    ]


def _build_gl_warnings(
    identified_foods: list[dict[str, Any]],
    unrecognized_items: list[dict[str, str]],
) -> list[str]:
    warnings = [
        "GL 为估算值，实际结果会受份量、烹饪方式、进食顺序和个体差异影响"
    ]
    if any(food.get("portion_source") == "default" for food in identified_foods):
        warnings.append("部分食物使用默认份量估算，实际 GL 可能不同")
    if unrecognized_items:
        warnings.append("部分食物未识别，当前 GL 可能被低估")
    return warnings


def _estimate_meal_confidence(
    identified_foods: list[dict[str, Any]],
    unrecognized_items: list[dict[str, str]],
) -> float:
    if not identified_foods:
        return 0.3 if unrecognized_items else 0.5

    total_gl = sum(float(food.get("gl", 0)) for food in identified_foods)
    if total_gl <= 0:
        base_confidence = sum(float(food.get("confidence", 0.6)) for food in identified_foods) / len(
            identified_foods
        )
    else:
        base_confidence = sum(
            float(food.get("confidence", 0.6)) * float(food.get("gl", 0))
            for food in identified_foods
        ) / total_gl

    if unrecognized_items:
        base_confidence -= 0.15
    if any(food.get("portion_source") == "default" for food in identified_foods):
        base_confidence -= 0.1
    return round(max(0.1, min(base_confidence, 0.95)), 2)


def analyze_diet_with_gl(text: str) -> dict[str, Any]:
    lowered_text = text.lower()
    food_occurrences, ignored_foods = _extract_food_occurrences(lowered_text)

    high_carb_keywords = [
        "米饭",
        "面",
        "面条",
        "粥",
        "包子",
        "馒头",
        "面包",
        "粉",
        "炒饭",
        "炒面",
        "饺子",
    ]
    sugary_keywords = [
        "奶茶",
        "可乐",
        "雪碧",
        "果汁",
        "甜饮料",
        "糖水",
        "蛋糕",
        "甜点",
        "冰淇淋",
        "巧克力",
    ]
    fried_keywords = ["炸鸡", "油条", "薯条", "炸串", "汉堡", "油炸", "炸物"]
    protein_keywords = [
        "鸡蛋",
        "牛奶",
        "豆浆",
        "豆腐",
        "鸡肉",
        "鸡胸肉",
        "鱼",
        "虾",
        "牛肉",
        "瘦肉",
    ]
    vegetable_keywords = [
        "青菜",
        "蔬菜",
        "西兰花",
        "菠菜",
        "白菜",
        "生菜",
        "黄瓜",
        "番茄",
        "西红柿",
        "芹菜",
    ]

    identified_foods: list[dict[str, Any]] = []
    total_gl = 0.0

    for occurrence in food_occurrences:
        gl_data = _calculate_gl(
            str(occurrence["food_name"]),
            portion_g=float(occurrence["portion_g"]),
            portion_source=str(occurrence["portion_source"]),
            portion_confidence=float(occurrence["portion_confidence"]),
            raw_text=str(occurrence["raw_text"]),
        )
        if gl_data:
            identified_foods.append(gl_data)
            total_gl += float(gl_data["gl"])

    unrecognized_items = _extract_unrecognized_items(
        lowered_text, [str(item["food"]) for item in identified_foods]
    )

    risks: list[str] = []
    suggestions: list[str] = []
    highlights: list[str] = []
    score = 100

    recognized_food_text = " ".join(
        str(food["food"]).lower() for food in identified_foods
    )
    scoring_text = recognized_food_text

    carb_count = sum(1 for keyword in high_carb_keywords if keyword in scoring_text)
    fried_count = sum(1 for keyword in fried_keywords if keyword in scoring_text)
    protein_count = sum(1 for keyword in protein_keywords if keyword in scoring_text)
    vegetable_count = sum(1 for keyword in vegetable_keywords if keyword in scoring_text)
    sugary_count = sum(1 for keyword in sugary_keywords if keyword in scoring_text)

    if carb_count >= 2:
        risks.append("主食偏多，餐后血糖可能更容易升高")
        suggestions.append("下次可适当减少一部分精制主食，或替换成粗粮")
        score -= 15
    elif carb_count == 1:
        highlights.append("有主食摄入，但仍需注意分量")
        score -= 5

    if sugary_count >= 1:
        risks.append("甜食摄入较多，不利于控糖")
        score -= 20
        if (
            "奶茶" in scoring_text
            or "可乐" in scoring_text
            or "雪碧" in scoring_text
            or "果汁" in scoring_text
        ):
            suggestions.append("建议把含糖饮料替换成白水、无糖茶或无糖豆浆")
        elif (
            "蛋糕" in scoring_text
            or "甜点" in scoring_text
            or "冰淇淋" in scoring_text
            or "巧克力" in scoring_text
        ):
            suggestions.append("甜食可偶尔享用，但要注意控制量，搭配蔬菜一起吃更佳")
        else:
            suggestions.append("建议减少甜食摄入，用水果或坚果替代更健康")

    if fried_count >= 1:
        risks.append("存在油炸或高油食物，能量负担较大")
        suggestions.append("建议优先选择蒸、煮、炖，减少油炸食品")
        score -= 15

    if protein_count == 0:
        risks.append("优质蛋白不足，饱腹感和营养均衡性不够")
        suggestions.append("建议增加鸡蛋、豆腐、鱼虾、瘦肉等优质蛋白")
        score -= 10
    else:
        highlights.append("有蛋白质摄入")

    if vegetable_count == 0:
        risks.append("蔬菜不足，不利于延缓餐后血糖波动")
        suggestions.append("建议每餐增加一些绿叶菜或低糖蔬菜")
        score -= 10
    else:
        highlights.append("有蔬菜摄入")

    if score >= 85:
        summary = "这顿饭整体还不错，基本符合控糖饮食方向。"
    elif score >= 65:
        summary = "这顿饭总体还可以，但还有优化空间。"
    else:
        summary = "这顿饭对控糖不太友好，建议下次调整搭配。"

    glucose_impact = _estimate_blood_glucose_impact(total_gl)
    warnings = _build_gl_warnings(identified_foods, unrecognized_items)
    confidence = _estimate_meal_confidence(identified_foods, unrecognized_items)

    return {
        "summary": summary,
        "risks": risks,
        "suggestions": suggestions,
        "highlights": highlights,
        "score": max(score, 0),
        "identified_foods": identified_foods,
        "total_gl": round(total_gl, 1),
        "glucose_impact": glucose_impact,
        "confidence": confidence,
        "warnings": warnings,
        "ignored_foods": ignored_foods,
        "unrecognized_items": unrecognized_items,
    }

def build_diet_reply_with_context(
    message: str, db: Session, user_id: int = DEFAULT_USER_ID
) -> dict[str, Any]:
    result = analyze_diet_with_gl(message)

    profile = get_patient_profile(db, user_id)
    glucose_data = get_recent_glucose(db, user_id)

    gl_info = result.get("glucose_impact", {})
    gl_level = gl_info.get("level", "未知")
    total_gl = result.get("total_gl", 0)

    food_list = result.get("identified_foods", [])
    food_text = (
        "\n".join(
            [
                f"- {f['food']}: {f['portion_g']}g, 碳水{f['carbs_g']}g, GI={f['gi']}, GL={f['gl']}"
                for f in food_list
            ]
        )
        if food_list
        else "未识别到已知食物"
    )

    guidelines = "\n".join(
        [f"- {item}" for item in DIABETES_DIET_GUIDELINES["基本原则"]]
    )

    complications_guidelines = get_guidelines_for_complications(
        profile.get("complications") if profile else None
    )

    fasting_latest = (
        glucose_data.get("fasting", [{}])[0].get("value")
        if glucose_data.get("fasting")
        else "未记录"
    )
    postmeal_latest = (
        glucose_data.get("postmeal", [{}])[0].get("value")
        if glucose_data.get("postmeal")
        else "未记录"
    )

    p = profile or {}
    p_age = p.get("age")
    p_diabetes_type = p.get("diabetes_type")
    p_disease_duration = p.get("disease_duration")
    p_medications = p.get("medications", [])
    p_complications = p.get("complications", [])
    p_bmi = p.get("bmi")
    p_target_fasting_max = p.get("target_fasting_max")
    p_target_postmeal_max = p.get("target_postmeal_max")

    prompt = f"""你是一位专业的糖尿病饮食管理师。请根据以下信息给出个性化的饮食建议。

## 患者档案
- 年龄：{p_age if p_age else '未提供'}
- 糖尿病类型：{p_diabetes_type if p_diabetes_type else '未提供'}
- 病程：{p_disease_duration if p_disease_duration else '未提供'}
- 用药：{', '.join(p_medications) if p_medications else '未提供'}
- 并发症：{', '.join(p_complications) if p_complications else '无'}
- BMI：{round(p_bmi, 1) if p_bmi else '未计算'}

##最近血糖
- 最近空腹血糖：{fasting_latest} mmol/L
- 最近餐后血糖：{postmeal_latest} mmol/L
- 血糖控制目标：空腹 {p.get('target_fasting_min', 4.4)}-{p_target_fasting_max or 7.0} mmol/L，餐后 {p.get('target_postmeal_min', 4.4)}-{p_target_postmeal_max or 10.0} mmol/L（{'已设置' if p_target_fasting_max else '使用默认目标'}）

## 本次饮食
- 饮食描述：{message}
- 识别食物和升糖负荷(GL)：
{food_text}
- 总升糖负荷(GL)：{total_gl}（{gl_level}级别）

## 糖尿病饮食原则
{guidelines}

## 特殊情况饮食注意
{complications_guidelines if complications_guidelines else "无特殊并发症注意"}

请给出简洁、实用的个性化建议（2-4句话），包括：
1. 对本次饮食的升糖评估
2. 结合患者具体情况的建议
3. 下次进餐的注意事项"""

    return {
        "data": {
            "content": prompt,
            "refresh": ["meal"],
        },
        "analysis_result": result,
        "profile": profile,
    }


def build_meal_analysis_payload(analysis_result: dict[str, Any]) -> dict[str, Any]:
    total_gl = analysis_result.get("total_gl", 0)
    gl_info = analysis_result.get("glucose_impact", {})
    gl_level = gl_info.get("level", "未知")

    detected_foods = []
    for food in analysis_result.get("identified_foods", []):
        gi = food.get("gi", 0)
        if gi >= 70:
            category = "high_risk"
        elif gi >= 55:
            category = "medium_risk"
        else:
            category = "protective"
        detected_foods.append(
            {
                "name": food.get("food", ""),
                "food": food.get("food", ""),
                "category": category,
                "gi": gi,
                "gl": food.get("gl", 0),
                "portion_g": food.get("portion_g", 0),
                "portion_source": food.get("portion_source", "default"),
                "carbs_g": food.get("carbs_g", 0),
                "available_carbs_g": food.get(
                    "available_carbs_g", food.get("carbs_g", 0)
                ),
                "confidence": food.get("confidence", 0.6),
                "warnings": food.get("warnings", []),
            }
        )

    if gl_level == "高":
        risk_level = "high"
    elif gl_level == "中":
        risk_level = "medium"
    else:
        risk_level = "low"

    suggestions = analysis_result.get("suggestions") or []
    if not suggestions:
        if risk_level == "high":
            suggestions = [
                "建议减少高GI食物摄入",
                "主食适当减量",
                "增加蔬菜比例",
            ]
        elif risk_level == "medium":
            suggestions = [
                "注意控制主食总量",
                "餐后可适当活动",
                "关注餐后血糖变化",
            ]
        else:
            suggestions = ["这顿饭整体较均衡，可以继续保持"]

    summary = (
        analysis_result.get("summary") or f"升糖负荷(GL): {total_gl}（{gl_level}级别）"
    )

    return {
        "risk_level": risk_level,
        "total_gl": total_gl,
        "gl_level": gl_level,
        "score": analysis_result.get("score", 0),
        "detected_foods": detected_foods,
        "suggestion": suggestions,
        "summary": summary,
        "confidence": analysis_result.get("confidence", 0.5),
        "warnings": analysis_result.get("warnings", []),
        "ignored_foods": analysis_result.get("ignored_foods", []),
        "unrecognized_items": analysis_result.get("unrecognized_items", []),
        "calculation_version": "gl-v1.1-phase1",
    }


def build_diet_reply(message: str) -> dict[str, Any]:
    result = analyze_diet_with_gl(message)
    risk_text = (
        "\n".join(f"- {item}" for item in result["risks"])
        if result["risks"]
        else "无明显风险"
    )
    highlight_text = (
        "\n".join(f"- {item}" for item in result["highlights"])
        if result["highlights"]
        else "暂无明显优点"
    )
    suggestion_text = (
        "\n".join(f"- {item}" for item in result["suggestions"])
        if result["suggestions"]
        else "继续保持"
    )

    gl_info = result.get("glucose_impact", {})
    gl_level = gl_info.get("level", "未知")
    gl_impact = gl_info.get("impact", "")
    total_gl = result.get("total_gl", 0)

    level_emoji = {"低": "🟢", "中": "🟠", "高": "🔴"}.get(gl_level, "⚪")

    food_details = ""
    if result.get("identified_foods"):
        food_lines = []
        for food in result["identified_foods"]:
            food_lines.append(
                f"  • {food['food']}: {food['portion_g']}g, 碳水 {food['carbs_g']}g, GI={food['gi']}, GL={food['gl']}"
            )
        food_details = "\n🍔 识别食物：\n" + "\n".join(food_lines)

    content = (
        "🍽 饮食分析结果\n\n"
        f"👉 总结：{result['summary']}\n\n"
        f"⚠ 风险：\n{risk_text}\n\n"
        f"✅ 优点：\n{highlight_text}\n\n"
        f"💡 建议：\n{suggestion_text}\n\n"
        f"📊 评分：{result['score']}/100\n\n"
        f"📈 升糖负荷(GL)：{total_gl} ({level_emoji}{gl_level})\n"
        f"   {gl_impact}\n"
        f"{food_details}"
    )
    return format_agent_response(content=content, refresh=["advice"])


def record_glucose_reading(
    db: Session,
    value: float,
    *,
    user_id: int = DEFAULT_USER_ID,
    measure_type: str = "fasting",
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    ingest_glucose_reading(
        db,
        GlucoseIngestionInput(
            user_id=user_id,
            value=value,
            measure_time=now,
            measure_type=measure_type,
            source="agent",
        ),
    )
    return format_agent_response(
        content=f"已帮你记录血糖 {value} mmol/L",
        refresh=["glucose", "adherence", "advice"],
    )


def normalize_remind_time(remind_time: str) -> str:
    normalized = remind_time.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", normalized):
        hour_text, minute_text = normalized.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour > 23 or minute > 59:
            raise ValueError("remind_time must be a valid HH:MM time")
        return f"{hour:02d}:{minute:02d}"

    time_match = re.fullmatch(r"(\d{1,2})点(?:(\d{1,2})分?)?", normalized)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "0")
        if hour > 23 or minute > 59:
            raise ValueError("remind_time must be a valid HH:MM time")
        return f"{hour:02d}:{minute:02d}"

    raise ValueError("remind_time must use HH:MM or 中文几点格式")


def build_time_text(remind_time: str, time_text: str | None) -> str:
    raw_time_text = (time_text or "").strip()
    return raw_time_text if raw_time_text else remind_time


def normalize_frequency(frequency: str | None) -> str:
    normalized_frequency = (frequency or "daily").strip()
    if not normalized_frequency:
        return "daily"

    lowered_frequency = normalized_frequency.lower()
    if lowered_frequency in {"daily", "everyday"} or normalized_frequency in {
        "每天",
        "每日",
    }:
        return "daily"

    if lowered_frequency.startswith("interval:"):
        interval_match = re.fullmatch(r"interval:(\d+)([hd])", lowered_frequency)
        if interval_match and int(interval_match.group(1)) > 0:
            return lowered_frequency
        logger.warning(
            "Invalid interval medication frequency, falling back to daily",
            extra={"frequency": frequency},
        )
        return "daily"

    if lowered_frequency.startswith("cron:"):
        expression = normalized_frequency.split(":", maxsplit=1)[1].strip()
        if is_cron_frequency_too_frequent(expression):
            logger.warning(
                "Cron medication frequency is too frequent, falling back to daily",
                extra={"frequency": frequency},
            )
            return "daily"
        return f"cron:{expression}"

    return lowered_frequency


def is_cron_frequency_too_frequent(expression: str) -> bool:
    try:
        base_time = datetime(2026, 1, 1, 0, 0)
        iterator = croniter(expression, base_time)
        first_occurrence = iterator.get_next(datetime)
        second_occurrence = iterator.get_next(datetime)
    except (KeyError, ValueError):
        return True

    return second_occurrence - first_occurrence < timedelta(hours=1)


def build_medication_payload(
    *,
    drug_name: str,
    dosage: str,
    remind_time: str,
    time_text: str | None,
    frequency: str | None,
    user_id: int,
) -> dict[str, Any]:
    normalized_drug_name = drug_name.strip()
    normalized_dosage = dosage.strip()
    if not normalized_drug_name:
        raise ValueError("drug_name cannot be empty")
    if not normalized_dosage:
        raise ValueError("dosage cannot be empty")

    normalized_remind_time = normalize_remind_time(remind_time)
    normalized_frequency = normalize_frequency(frequency)
    normalized_time_text = build_time_text(normalized_remind_time, time_text)

    return {
        "user_id": user_id,
        "drug_name": normalized_drug_name,
        "dosage": normalized_dosage,
        "time_text": normalized_time_text,
        "remind_time": normalized_remind_time,
        "frequency": normalized_frequency,
        "confirm_status": "pending",
        "missing_fields": [],
        "is_valid": True,
    }


def handle_medication_parse(
    *,
    drug_name: str,
    dosage: str,
    remind_time: str,
    db: Session,
    user_id: int = DEFAULT_USER_ID,
    time_text: str | None = None,
    frequency: str | None = None,
) -> dict[str, Any]:
    result = build_medication_payload(
        drug_name=drug_name,
        dosage=dosage,
        remind_time=remind_time,
        time_text=time_text,
        frequency=frequency,
        user_id=user_id,
    )

    pending_record = PendingMedicationPlan(
        user_id=result["user_id"],
        drug_name=result["drug_name"],
        dosage=result["dosage"],
        time_text=result["time_text"],
        remind_time=result["remind_time"],
        frequency=result["frequency"],
        confirm_status=result["confirm_status"],
        is_valid=result["is_valid"],
        missing_fields="",
    )

    db.add(pending_record)
    db.commit()
    db.refresh(pending_record)

    content = (
        "我帮你创建了一条待确认的用药提醒：\n\n"
        f"药物：{pending_record.drug_name}\n"
        f"剂量：{pending_record.dosage}\n"
        f"时间：{pending_record.time_text}\n"
        f"提醒时间：{pending_record.remind_time}\n"
        f"频率：{pending_record.frequency}\n\n"
        "如果没问题，你可以回复：确认用药提醒"
    )

    return format_agent_response(content=content, refresh=["medication"])


def handle_medication_confirm(
    db: Session, user_id: int = DEFAULT_USER_ID
) -> dict[str, Any]:
    pending = get_latest_pending_plan(db, user_id=user_id)
    if pending is None:
        return format_agent_response(content="当前没有待确认的用药计划。", refresh=[])

    if not bool(cast(Any, pending.is_valid)):
        raw_missing_fields = cast(str | None, pending.missing_fields)
        missing = raw_missing_fields.split(",") if raw_missing_fields else []
        missing_text = "、".join(missing) if missing else "信息不完整"
        return format_agent_response(
            content=f"这条待确认计划还不完整，暂时不能创建正式提醒。缺少：{missing_text}",
            refresh=[],
        )

    new_plan = MedicationPlan(
        user_id=pending.user_id,
        drug_name=pending.drug_name,
        dosage=pending.dosage,
        time_text=pending.time_text,
        remind_time=pending.remind_time,
        frequency=pending.frequency,
        status="active",
    )

    db.add(new_plan)
    setattr(pending, "confirm_status", "confirmed")
    db.commit()
    db.refresh(new_plan)
    return format_agent_response(
        content=f"已为你创建用药提醒：{new_plan.drug_name} {new_plan.dosage}，{new_plan.time_text}。",
        refresh=["medication", "adherence", "advice"],
    )


def handle_medication_reject(
    db: Session, user_id: int = DEFAULT_USER_ID
) -> dict[str, Any]:
    pending = get_latest_pending_plan(db, user_id=user_id)
    if pending is None:
        return format_agent_response(content="当前没有待确认的用药计划。", refresh=[])

    setattr(pending, "confirm_status", "rejected")
    db.commit()
    return format_agent_response(
        content="好的，这条待确认的用药提醒我已经取消了。",
        refresh=["medication"],
    )


def handle_medication_take(
    message: str,
    db: Session,
    *,
    user_id: int = DEFAULT_USER_ID,
    status: str = "taken",
) -> dict[str, Any]:
    plan, error = find_target_plan(db, message, user_id=user_id)
    if error is not None:
        return format_agent_response(content=error, refresh=[])

    if plan is None:
        return format_agent_response(content="当前无法识别对应的用药计划。", refresh=[])
    new_record = MedicationTakenRecord(
        user_id=user_id,
        plan_id=plan.plan_id,
        drug_name=plan.drug_name,
        dosage=plan.dosage,
        time_text=plan.time_text,
        remind_time=plan.remind_time,
        status=status,
        created_at=datetime.now(),
    )

    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    if status == "taken":
        content = f"已帮你记录：{plan.drug_name} 已服药。"
    else:
        content = (
            f"已帮你记录：{plan.drug_name} 本次未服药。"
            "建议尽量按时服药，如需我也可以帮你查看当前用药计划。"
        )

    return format_agent_response(
        content=content,
        refresh=["medication", "adherence", "advice"],
    )


def handle_medication_query(
    db: Session, user_id: int = DEFAULT_USER_ID
) -> dict[str, Any]:
    plans = get_active_plans(db, user_id=user_id)
    content = build_plan_text(plans)
    return format_agent_response(
        content=f"💊 当前正式用药计划：\n\n{content}", refresh=[]
    )


def build_fallback_reply() -> dict[str, Any]:
    return format_agent_response(
        content=(
            "我可以帮你记录血糖、分析饮食、设置和确认用药提醒、记录已服药或漏服。"
            "你可以试着说：\n"
            "1. 我刚测血糖 7.2\n"
            "2. 早餐吃了两个包子一杯豆浆\n"
            "3. 我每天早晚吃二甲双胍1片\n"
            "4. 确认用药提醒\n"
            "5. 我已经吃了二甲双胍"
        ),
        refresh=[],
    )


ACTION_COMPLETION_PHRASES = (
    "已帮你",
    "已经帮你",
    "已为你",
    "创建成功",
    "设置成功",
    "记录成功",
)


def looks_like_unverified_action_completion(content: str) -> bool:
    normalized = content.strip()
    return any(phrase in normalized for phrase in ACTION_COMPLETION_PHRASES)


def looks_like_image_analysis_unavailable(content: str) -> bool:
    normalized = content.strip()
    unavailable_markers = (
        "无法直接查看图片",
        "无法直接分析图片",
        "不能直接查看图片",
        "不能直接分析图片",
        "看不到图片",
        "无法查看图像",
        "无法分析图像",
    )
    return any(marker in normalized for marker in unavailable_markers)


@dataclass(frozen=True, slots=True)
class ChatImageInput:
    filename: str
    content_type: str
    content: bytes


@dataclass(slots=True)
class ToolResultPayload:
    message: str
    refresh: list[str]
    meal_analysis: dict[str, Any] | None = None
    safety_level: str | None = None
    citations: list[dict[str, Any]] | None = None
    snippets: list[dict[str, Any]] | None = None
    quick_replies: list[str] | None = None
    memory_updates: list[dict[str, Any]] | None = None
    agent_role: str = "assistant"

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "message": self.message,
            "refresh": self.refresh,
            "agent_role": self.agent_role,
        }
        if self.meal_analysis is not None:
            payload["meal_analysis"] = self.meal_analysis
        if self.safety_level is not None:
            payload["safety_level"] = self.safety_level
        if self.citations is not None:
            payload["citations"] = self.citations
        if self.snippets is not None:
            payload["snippets"] = self.snippets
        if self.quick_replies is not None:
            payload["quick_replies"] = self.quick_replies
        if self.memory_updates is not None:
            payload["memory_updates"] = self.memory_updates
        return json.dumps(payload, ensure_ascii=False)


class ToolCaptureHook:
    def __init__(self, hook_base: type[Any]) -> None:
        self._hook_base = hook_base
        self.last_tool_payload: ToolResultPayload | None = None

    async def after_iteration(self, context: Any) -> None:
        for result in context.tool_results:
            payload = _parse_tool_payload(result)
            if payload is not None:
                self.last_tool_payload = payload

    def to_runtime_hook(self) -> Any:
        hook_base = self._hook_base
        owner = self

        class _RuntimeToolCaptureHook(hook_base):
            async def after_iteration(self, context: Any) -> None:
                await owner.after_iteration(context)

        runtime_hook = _RuntimeToolCaptureHook()
        setattr(runtime_hook, "_owner", owner)
        return runtime_hook


def _parse_tool_payload(result: Any) -> ToolResultPayload | None:
    if not isinstance(result, str):
        return None
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    message = data.get("message")
    refresh = data.get("refresh")
    meal_analysis = data.get("meal_analysis")
    safety_level = data.get("safety_level")
    citations = data.get("citations")
    snippets = data.get("snippets")
    quick_replies = data.get("quick_replies")
    memory_updates = data.get("memory_updates")
    agent_role = data.get("agent_role", "assistant")
    if (
        not isinstance(message, str)
        or not isinstance(refresh, list)
        or not all(isinstance(item, str) for item in refresh)
    ):
        return None
    if meal_analysis is not None and not isinstance(meal_analysis, dict):
        return None
    if safety_level is not None and not isinstance(safety_level, str):
        return None
    if citations is not None and not isinstance(citations, list):
        return None
    if snippets is not None and not isinstance(snippets, list):
        return None
    if quick_replies is not None and (
        not isinstance(quick_replies, list)
        or not all(isinstance(item, str) for item in quick_replies)
    ):
        return None
    if memory_updates is not None and not isinstance(memory_updates, list):
        return None
    if not isinstance(agent_role, str):
        return None
    return ToolResultPayload(
        message=message,
        refresh=refresh,
        meal_analysis=meal_analysis,
        safety_level=safety_level,
        citations=citations,
        snippets=snippets,
        quick_replies=quick_replies,
        memory_updates=memory_updates,
        agent_role=agent_role,
    )


def _normalize_topic(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    topic_aliases = {
        "低血糖": "hypo",
        "hypo": "hypo",
        "血糖": "glucose",
        "glucose": "glucose",
        "饮食": "diet",
        "diet": "diet",
        "运动": "exercise",
        "exercise": "exercise",
        "用药": "medication",
        "药": "medication",
        "medication": "medication",
        "筛查": "screening",
        "screening": "screening",
        "胰岛素": "insulin",
        "insulin": "insulin",
    }
    return topic_aliases.get(normalized, normalized)


def infer_knowledge_topic(message: str) -> str | None:
    rules = [
        (("低血糖", "hypo"), "hypo"),
        (("胰岛素",), "insulin"),
        (("用药", "停药", "药"), "medication"),
        (("饮食", "吃什么", "主食", "碳水"), "diet"),
        (("运动",), "exercise"),
        (("筛查", "并发症"), "screening"),
        (("血糖",), "glucose"),
    ]
    for keywords, topic in rules:
        if any(keyword in message for keyword in keywords):
            return topic
    return None


def build_hypo_knowledge_response(
    db: Session,
    message: str,
) -> dict[str, Any] | None:
    if "低血糖怎么办" not in message and "低血糖" not in message:
        return None
    snippets = search_knowledge(db, message, topics=["hypo"], limit=3)
    if not snippets:
        return None
    citations = format_citations(snippets)
    first_snippet = snippets[0]["content"]
    content = f"{first_snippet}\n\n依据：{build_cited_context(snippets)}"
    return format_agent_response(
        content=content,
        refresh=[],
        citations=citations,
    )


class BackendAgentTool:
    read_only = False
    exclusive = False

    def __init__(self, service: AgentChatService):
        self.service = service

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def parameters(self) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def concurrency_safe(self) -> bool:
        return False

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {}

        casted = dict(params)
        properties = self.parameters.get("properties", {})
        for key, schema in properties.items():
            if key not in casted:
                continue
            value = casted[key]
            if schema.get("type") == "number" and isinstance(value, str):
                try:
                    casted[key] = float(value)
                except ValueError:
                    casted[key] = value
        return casted

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]

        errors: list[str] = []
        schema = self.parameters
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for key in required:
            if key not in params:
                errors.append(f"missing required {key}")

        for key, value in params.items():
            prop_schema = properties.get(key)
            if isinstance(prop_schema, dict):
                errors.extend(_schema_property_errors(key, value, prop_schema))
        return errors

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        result = await run_in_threadpool(self.run_with_worker_session, **kwargs)
        return result.to_json()

    def run_with_worker_session(self, **kwargs: Any) -> ToolResultPayload:
        db = self.service.session_factory()
        try:
            return self.run(db=db, **kwargs)
        finally:
            db.close()

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        raise NotImplementedError


class RecordGlucoseTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "record_glucose"

    @property
    def description(self) -> str:
        return "Record a blood glucose reading for the demo user when the message contains a numeric measurement."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "value": {"type": "number", "minimum": 0},
                "measure_type": {
                    "type": "string",
                    "enum": ["fasting", "post_meal", "before_sleep"],
                },
            },
            "required": ["value"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        value = float(kwargs["value"])
        measure_type = str(kwargs.get("measure_type") or "fasting")
        active_db = db or self.service.db
        result = record_glucose_reading(active_db, value, measure_type=measure_type)
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class AnalyzeDietTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "analyze_diet"

    @property
    def description(self) -> str:
        return "Analyze a described meal and return a diabetes-focused dietary assessment. Returns structured data with identified foods, GL calculation, and personalized suggestions based on patient profile and diabetes guidelines."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "meal_text": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MEAL_TEXT_MAX_LENGTH,
                },
            },
            "required": ["meal_text"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        result = build_diet_reply_with_context(
            str(kwargs["meal_text"]),
            db=active_db,
            user_id=DEFAULT_USER_ID,
        )
        analysis = result.get("analysis_result", {})

        def _string_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [item for item in value if isinstance(item, str)]

        risks_list = _string_list(analysis.get("risks", []))
        risks_text = "- " + "\n- ".join(risks_list) if risks_list else "无明显风险"

        suggestions_list = _string_list(analysis.get("suggestions", []))
        suggestions_text = (
            "- " + "\n- ".join(suggestions_list) if suggestions_list else "继续保持"
        )

        score_val = analysis.get("score", "?")

        total_gl = analysis.get("total_gl", 0)
        gl_info = analysis.get("glucose_impact", {})
        gl_level = gl_info.get("level", "未知")
        gl_impact_text = gl_info.get("impact", "")

        food_list = analysis.get("identified_foods", [])
        formatted_foods: list[str] = []
        if isinstance(food_list, list):
            for food in food_list:
                if not isinstance(food, dict):
                    continue
                formatted_foods.append(
                    f"- {food.get('food', '')}: {food.get('portion_g', 0)}g（{food.get('portion_source', 'default')}）, 碳水{food.get('carbs_g', 0)}g, GI={food.get('gi', 0)}, GL={food.get('gl', 0)}"
                )
        food_text = "\n".join(formatted_foods) if formatted_foods else "未识别到已知食物"

        warnings_list = _string_list(analysis.get("warnings", []))
        warnings_text = (
            "\n".join([f"- {warning}" for warning in warnings_list])
            if warnings_list
            else "无"
        )

        level_emoji = {"低": "🟢", "中": "🟡", "高": "🔴"}.get(gl_level, "⚪")

        note = (
            "（以上为初步分析，系统将根据您的个人档案和最新血糖数据生成更精准的建议）"
        )

        summary = f"""🍽 饮食分析结果

📈 升糖负荷(GL)：{total_gl}（{level_emoji}{gl_level}）
   {gl_impact_text}

🍰 识别食物：
{food_text}

⚠ 风险提示：
{risks_text}

💡 建议：
{suggestions_text}

📊 评分：{score_val}/100

⚠ 估算提示：
{warnings_text}

---
{note}"""

        return ToolResultPayload(
            message=summary,
            refresh=result["data"]["refresh"],
            meal_analysis=build_meal_analysis_payload(analysis),
        )


class ParseMedicationPlanTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "parse_medication_plan"

    @property
    def description(self) -> str:
        return (
            "Create a pending medication reminder from structured fields that you have already collected in dialogue. "
            "Only call this tool after the drug name, dosage, and reminder time are all clear."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "drug_name": {"type": "string", "minLength": 1},
                "dosage": {"type": "string", "minLength": 1},
                "remind_time": {"type": "string", "minLength": 1},
                "time_text": {"type": "string", "minLength": 1},
                "frequency": {"type": "string", "minLength": 1},
            },
            "required": ["drug_name", "dosage", "remind_time"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        try:
            result = handle_medication_parse(
                drug_name=str(kwargs["drug_name"]),
                dosage=str(kwargs["dosage"]),
                remind_time=str(kwargs["remind_time"]),
                time_text=(
                    str(kwargs["time_text"])
                    if kwargs.get("time_text") is not None
                    else None
                ),
                frequency=(
                    str(kwargs["frequency"])
                    if kwargs.get("frequency") is not None
                    else None
                ),
                db=active_db,
            )
        except ValueError:
            return ToolResultPayload(
                message="要创建用药提醒，我还需要完整且有效的药名、剂量和提醒时间。请继续补充。",
                refresh=[],
            )
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class ConfirmMedicationPlanTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "confirm_medication_plan"

    @property
    def description(self) -> str:
        return "Confirm the latest pending medication plan and activate it."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        result = handle_medication_confirm(active_db)
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class RejectMedicationPlanTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "reject_medication_plan"

    @property
    def description(self) -> str:
        return "Reject and cancel the latest pending medication plan."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        result = handle_medication_reject(active_db)
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class LogMedicationStatusTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "log_medication_status"

    @property
    def description(self) -> str:
        return "Record whether the user took or missed a medication dose, optionally using the medication name from the message."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message_text": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": ["taken", "missed"]},
            },
            "required": ["message_text", "status"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        result = handle_medication_take(
            str(kwargs["message_text"]),
            active_db,
            status=str(kwargs["status"]),
        )
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class QueryMedicationPlansTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_medication_plans"

    @property
    def description(self) -> str:
        return "Show the user's current active medication plans."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        result = handle_medication_query(active_db)
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class GuidanceFallbackTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "guidance_fallback"

    @property
    def description(self) -> str:
        return "Return the supported capability guidance when no medical action fits the message."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        result = build_fallback_reply()
        return ToolResultPayload(
            message=result["data"]["content"],
            refresh=result["data"]["refresh"],
        )


class SearchGuidelineKnowledgeTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "search_guideline_knowledge"

    @property
    def description(self) -> str:
        return "Search the diabetes guideline knowledge base and return cited snippets."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "topic": {"type": "string", "minLength": 1},
                "limit": {"type": "number", "minimum": 1},
            },
            "required": ["query"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        topic_value = kwargs.get("topic")
        topic = _normalize_topic(str(topic_value)) if topic_value is not None else None
        snippets = search_knowledge(
            active_db,
            str(kwargs["query"]),
            topics=[topic] if topic else None,
            limit=int(kwargs.get("limit", 3)),
        )
        if not snippets:
            return ToolResultPayload(
                message="当前知识库未检索到相关内容。",
                refresh=[],
                citations=[],
            )
        citations = format_citations(snippets)
        structured_snippets = [
            {
                "content": item["content"],
                "source_name": item["source_name"],
                "source_version": item["source_version"],
                "topic": item["topic"],
            }
            for item in snippets
        ]
        snippet_lines = [f"- {item['content']}" for item in structured_snippets]
        return ToolResultPayload(
            message="知识库检索结果：\n" + "\n".join(snippet_lines),
            refresh=[],
            citations=citations,
            snippets=structured_snippets,
        )


class QuerySimilarCasesTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_similar_cases"

    @property
    def description(self) -> str:
        return "Find anonymous demo case references similar to the user's current diabetes profile and glucose patterns."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "minimum": 1},
            },
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        limit = int(kwargs.get("limit", 3))
        features = build_user_case_features(active_db, DEFAULT_USER_ID)
        cases = search_similar_cases(active_db, features, limit=limit)
        return ToolResultPayload(
            message=format_case_insight(cases),
            refresh=[],
            safety_level="normal",
            agent_role="case_reference",
        )


class AgentChatService:
    def __init__(self, db: Session, session_factory: SessionFactory | None = None):
        self.db = db
        self.session_factory = session_factory or SessionLocal
        self._bot: Any | None = None
        self._bot_agent_role: str | None = None

    async def run_chat(
        self,
        message: str,
        images: list[ChatImageInput] | None = None,
    ) -> dict[str, Any]:
        has_images = bool(images)
        if not has_images and message == PEANUT_BUTTER_MEAL_TRIGGER:
            return format_agent_response(
                content=PEANUT_ALLERGY_WARNING_RESPONSE,
                refresh=[],
            )
        safety_result = classify_medical_risk(message)
        memory_shortcut_response = await self._handle_memory_shortcut(message, has_images)
        if memory_shortcut_response is not None:
            return memory_shortcut_response

        knowledge_shortcut_response = await self._handle_knowledge_shortcut(message, has_images)
        if knowledge_shortcut_response is not None:
            return knowledge_shortcut_response

        if safety_result.risk_level == "medium" and safety_result.category == "hypoglycemia":
            safety_response = build_safety_response(safety_result)
            return format_agent_response(
                content=safety_response["content"],
                refresh=[],
                safety_level=safety_response["safety_level"],
                quick_replies=safety_response["quick_replies"],
            )

        intent_result = classify_agent_intent(message)
        if intent_result.intent == "case_reference" and not has_images:
            case_response = await run_in_threadpool(
                self._handle_case_reference_shortcut,
                safety_result.risk_level,
                safety_result.category,
            )
            if case_response is not None:
                return case_response

        try:
            bot = self.get_bot(intent_result.agent_role)
            _, agent_hook_base, _, _, _, _ = load_nanobot_runtime()
            hook = ToolCaptureHook(agent_hook_base)
            if has_images:
                message_for_agent = message
            else:
                message_for_agent = await self._build_message_with_memory_context(
                    message,
                    agent_role=intent_result.agent_role,
                )
            if has_images:
                result = await self._run_chat_with_images(
                    bot=bot,
                    hook=hook,
                    message=message_for_agent,
                    images=images or [],
                )
            else:
                result = await bot.run(
                    message_for_agent,
                    session_key="diabetes-demo-agent:user:1",
                    hooks=[hook.to_runtime_hook()],
                )
        except (
            ImportError,
            ModuleNotFoundError,
            AttributeError,
            ValueError,
            RuntimeError,
        ):
            return format_agent_response(
                content="智能体当前不可用，请先检查 nanobot 配置或依赖。",
                refresh=[],
            )
        except Exception:
            logger.exception("Agent chat failed", extra={"has_images": has_images})
            if has_images:
                return format_agent_response(
                    content=IMAGE_ANALYSIS_FALLBACK_MESSAGE,
                    refresh=[],
                )
            return format_agent_response(
                content="智能体当前不可用，请稍后再试。",
                refresh=[],
            )

        if hook.last_tool_payload is not None:
            content = hook.last_tool_payload.message
            response_safety_level = hook.last_tool_payload.safety_level
            if safety_result.risk_level == "medium":
                content = append_disclaimer(content, safety_result.category)
                response_safety_level = "medium"
            return format_agent_response(
                content=content,
                refresh=hook.last_tool_payload.refresh,
                meal_analysis=hook.last_tool_payload.meal_analysis,
                safety_level=response_safety_level,
                citations=hook.last_tool_payload.citations,
                quick_replies=hook.last_tool_payload.quick_replies,
                memory_updates=hook.last_tool_payload.memory_updates,
                agent_role=hook.last_tool_payload.agent_role or intent_result.agent_role,
            )

        final_content = result.content.strip() if result and result.content else ""
        if final_content:
            if has_images and looks_like_image_analysis_unavailable(final_content):
                return format_agent_response(
                    content=IMAGE_ANALYSIS_FALLBACK_MESSAGE,
                    refresh=[],
                )
            if looks_like_unverified_action_completion(final_content):
                return format_agent_response(
                    content="我还没有真正执行这项操作。请继续补充必要信息，或等我调用对应工具后再确认结果。",
                    refresh=[],
                )
            if safety_result.risk_level == "medium":
                return format_agent_response(
                    content=append_disclaimer(final_content, safety_result.category),
                    refresh=[],
                    safety_level="medium",
                )
            return format_agent_response(content=final_content, refresh=[])
        if has_images:
            return format_agent_response(
                content=IMAGE_ANALYSIS_FALLBACK_MESSAGE, refresh=[]
            )
        return build_fallback_reply()

    async def _run_chat_with_images(
        self,
        *,
        bot: Any,
        hook: ToolCaptureHook,
        message: str,
        images: list[ChatImageInput],
    ) -> Any:
        saved_paths = self._save_chat_images(images)
        try:
            return await self._run_bot_with_media(
                bot=bot,
                hook=hook,
                message=message,
                media_paths=saved_paths,
            )
        finally:
            self._cleanup_chat_images(saved_paths)

    async def _run_bot_with_media(
        self,
        *,
        bot: Any,
        hook: ToolCaptureHook,
        message: str,
        media_paths: list[Path],
    ) -> Any:
        inbound_message_type = load_inbound_message_runtime()
        runtime_hook = hook.to_runtime_hook()
        previous_hooks = bot._loop._extra_hooks
        bot._loop._extra_hooks = [runtime_hook]
        try:
            await bot._loop._connect_mcp()
            inbound_message = inbound_message_type(
                channel="api",
                sender_id="user",
                chat_id="agent",
                content=message,
                media=[str(path) for path in media_paths],
            )
            return await bot._loop._process_message(
                inbound_message,
                session_key="diabetes-demo-agent:user:1",
            )
        finally:
            bot._loop._extra_hooks = previous_hooks

    async def _build_message_with_memory_context(
        self,
        message: str,
        *,
        agent_role: str = "coach",
    ) -> str:
        memory_summary = await run_in_threadpool(self._build_memory_summary)
        role_config = get_role_config(agent_role)
        role_context = f"当前专业角色：{role_config.description}\n角色要求：{role_config.prompt}"
        if not memory_summary:
            return f"{role_context}\n\n用户本轮消息：{message}"
        return f"{role_context}\n\n长期记忆：\n{memory_summary}\n\n用户本轮消息：{message}"

    def _handle_case_reference_shortcut(
        self,
        safety_level: str = "normal",
        safety_category: str = "general",
    ) -> dict[str, Any] | None:
        try:
            features = build_user_case_features(self.db, DEFAULT_USER_ID)
            cases = search_similar_cases(self.db, features, limit=3)
        except Exception:
            self.db.rollback()
            logger.exception("Failed to build case reference insight")
            return None
        content = format_case_insight(cases)
        response_safety_level = "normal"
        if safety_level == "medium":
            content = append_disclaimer(content, safety_category)
            response_safety_level = "medium"
        return format_agent_response(
            content=content,
            refresh=[],
            safety_level=response_safety_level,
            agent_role="case_reference",
        )

    def _build_memory_summary(self) -> str:
        db = self.session_factory()
        try:
            memories = list_memories(db, user_id=DEFAULT_USER_ID)
        except Exception:
            db.rollback()
            logger.exception("Failed to load structured agent memories")
            return ""
        finally:
            db.close()
        if not memories:
            return ""
        return "\n".join(
            f"- {memory.category}: {memory.key}{memory.value}"
            for memory in memories
        )

    async def _handle_memory_shortcut(
        self,
        message: str,
        has_images: bool,
    ) -> dict[str, Any] | None:
        if has_images:
            return None

        persisted = await run_in_threadpool(
            self._persist_memory_shortcut,
            message,
        )
        if persisted is None:
            return None

        return format_agent_response(
            content=f"已记住：{persisted['key']}{persisted['value']}。",
            refresh=[],
            memory_updates=[persisted],
        )

    def _persist_memory_shortcut(self, message: str) -> dict[str, Any] | None:
        db = self.session_factory()
        try:
            return extract_and_persist_memory(
                db,
                user_id=DEFAULT_USER_ID,
                message=message,
            )
        finally:
            db.close()

    async def _handle_knowledge_shortcut(
        self,
        message: str,
        has_images: bool,
    ) -> dict[str, Any] | None:
        if has_images:
            return None
        return await run_in_threadpool(self._build_knowledge_shortcut_response, message)

    def _build_knowledge_shortcut_response(self, message: str) -> dict[str, Any] | None:
        db = self.session_factory()
        try:
            return build_hypo_knowledge_response(db, message)
        finally:
            db.close()

    def _save_chat_images(self, images: list[ChatImageInput]) -> list[Path]:
        request_dir = CHAT_UPLOAD_ROOT / uuid.uuid4().hex
        request_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        try:
            for index, image in enumerate(images, start=1):
                extension = CHAT_IMAGE_EXTENSION_BY_MIME.get(image.content_type, ".img")
                file_path = request_dir / f"{index}-{uuid.uuid4().hex}{extension}"
                file_path.write_bytes(image.content)
                saved_paths.append(file_path)
        except OSError:
            self._cleanup_chat_images(saved_paths)
            try:
                request_dir.rmdir()
            except OSError:
                pass
            raise
        return saved_paths

    def _cleanup_chat_images(self, saved_paths: list[Path]) -> None:
        parent_dirs = sorted(
            {path.parent for path in saved_paths},
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for file_path in saved_paths:
            if file_path.exists():
                file_path.unlink()
        for directory in parent_dirs:
            try:
                directory.rmdir()
            except OSError:
                continue
        try:
            CHAT_UPLOAD_ROOT.rmdir()
        except OSError:
            pass

    def get_bot(self, agent_role: str | None = None) -> Any:
        if self._bot is None:
            self._bot = self._create_bot(agent_role)
        elif (
            self._bot_agent_role is not None
            and self._bot_agent_role != agent_role
            and hasattr(self._bot, "_loop")
        ):
            self._restrict_nanobot_tools(self._bot, agent_role=agent_role)
            self._bot_agent_role = agent_role
        return self._bot

    def _create_bot(self, agent_role: str | None = None) -> Any:
        nanobot_runtime, _, _, _, _, set_config_path_fn = load_nanobot_runtime()
        self._ensure_runtime_paths()
        set_config_path_fn(NANOBOT_CONFIG_PATH)
        bot = nanobot_runtime.from_config(
            NANOBOT_CONFIG_PATH, workspace=NANOBOT_WORKSPACE
        )
        self._restrict_nanobot_tools(bot, agent_role=agent_role)
        self._bot_agent_role = agent_role
        return bot

    def _ensure_runtime_paths(self) -> None:
        BACKEND_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        NANOBOT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        config: dict[str, Any] = {}
        if NANOBOT_CONFIG_PATH.exists():
            try:
                config = json.loads(NANOBOT_CONFIG_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                config = {}

        agents_cfg = config.setdefault("agents", {})
        defaults_cfg = agents_cfg.setdefault("defaults", {})
        defaults_cfg.update(
            {
                "workspace": str(NANOBOT_WORKSPACE),
                "model": defaults_cfg.get("model", "openai/gpt-5.4-mini"),
                "provider": defaults_cfg.get("provider", "openai"),
                "temperature": 0.1,
                "maxToolIterations": 8,
                "maxToolResultChars": 4000,
                "contextWindowTokens": 16000,
            }
        )

        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for nanobot runtime")

        providers_cfg = config.setdefault("providers", {})
        openai_cfg = providers_cfg.setdefault("openai", {})
        openai_cfg["apiKey"] = "sk-a0709b8c5b9740bb66ece3909b617c4b46f0aeb6821cbff52b010158709b73f9"

        api_base = "https://llmapi.02013839.xyz/v1"
        if api_base:
            openai_cfg["apiBase"] = api_base
        else:
            openai_cfg.pop("apiBase", None)

        tools_cfg = config.setdefault("tools", {})
        tools_cfg["restrictToWorkspace"] = True
        tools_cfg["exec"] = {"enable": False, "timeout": 5, "pathAppend": ""}
        tools_cfg["web"] = {
            "proxy": None,
            "search": {
                "provider": "brave",
                "apiKey": "",
                "baseUrl": "",
                "maxResults": 0,
            },
        }
        tools_cfg["mcpServers"] = {}
        NANOBOT_CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        for filename, content in build_workspace_bootstrap().items():
            file_path = NANOBOT_WORKSPACE / filename
            file_path.write_text(content, encoding="utf-8")

        memory_dir = NANOBOT_WORKSPACE / "memory"
        memory_dir.mkdir(exist_ok=True)
        for filename in ("MEMORY.md", "HISTORY.md"):
            file_path = memory_dir / filename
            if not file_path.exists():
                file_path.write_text("", encoding="utf-8")

    def _restrict_nanobot_tools(self, bot: Any, agent_role: str | None = None) -> None:
        _, _, _, _, tool_registry_runtime, _ = load_nanobot_runtime()
        registry = tool_registry_runtime()
        for tool in self._build_backend_tools(agent_role):
            registry.register(tool)
        bot._loop.tools = registry
        bot._loop.memory_consolidator = bot._loop.memory_consolidator.__class__(
            workspace=bot._loop.workspace,
            provider=bot._loop.provider,
            model=bot._loop.model,
            sessions=bot._loop.sessions,
            context_window_tokens=bot._loop.context_window_tokens,
            build_messages=bot._loop.context.build_messages,
            get_tool_definitions=bot._loop.tools.get_definitions,
            max_completion_tokens=bot._loop.provider.generation.max_tokens,
        )

    def _build_backend_tools(self, agent_role: str | None = None) -> list[Any]:
        all_tools: list[Any] = [
            RecordGlucoseTool(self),
            AnalyzeDietTool(self),
            ParseMedicationPlanTool(self),
            ConfirmMedicationPlanTool(self),
            RejectMedicationPlanTool(self),
            LogMedicationStatusTool(self),
            QueryMedicationPlansTool(self),
            GuidanceFallbackTool(self),
            SearchGuidelineKnowledgeTool(self),
            QueryHbA1cTool(self),
            TriggerHypoProtocolTool(self),
            QueryTIRTool(self),
            LogExerciseTool(self),
            QueryExerciseTool(self),
            QueryScreeningTool(self),
            CalculateInsulinTool(self),
            QuerySimilarCasesTool(self),
            RememberUserFactTool(self),
            QueryUserMemoryTool(self),
            ForgetUserFactTool(self),
        ]
        if agent_role is None:
            return all_tools
        allowed_tool_names = get_tool_names_for_role(agent_role)
        return [tool for tool in all_tools if tool.name in allowed_tool_names]


class RememberUserFactTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "remember_user_fact"

    @property
    def description(self) -> str:
        return "Save a stable user preference, allergy, lifestyle, goal, care context, or communication preference."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["preference", "allergy", "lifestyle", "goal", "care_context", "communication"]},
                "key": {"type": "string", "minLength": 1},
                "value": {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["category", "key", "value"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        memory = upsert_memory(
            active_db,
            user_id=DEFAULT_USER_ID,
            category=str(kwargs["category"]),
            key=str(kwargs["key"]),
            value=str(kwargs["value"]),
            confidence=float(kwargs.get("confidence", 0.8)),
            source="chat",
        )
        memory_payload = memory_to_dict(memory)
        return ToolResultPayload(
            message=f"已记住：{memory.key}{memory.value}。",
            refresh=[],
            memory_updates=[memory_payload],
        )


class QueryUserMemoryTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_user_memory"

    @property
    def description(self) -> str:
        return "Query the user's structured long-term memory, optionally filtered by category."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["preference", "allergy", "lifestyle", "goal", "care_context", "communication"]},
            },
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        category_value = kwargs.get("category")
        category = str(category_value) if category_value is not None else None
        memories = list_memories(active_db, user_id=DEFAULT_USER_ID, category=category)
        if not memories:
            return ToolResultPayload(message="当前没有已保存的长期记忆。", refresh=[])

        lines = [f"- {memory.category}: {memory.key}{memory.value}" for memory in memories]
        return ToolResultPayload(message="已保存的长期记忆：\n" + "\n".join(lines), refresh=[])


class ForgetUserFactTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "forget_user_fact"

    @property
    def description(self) -> str:
        return "Archive a saved user memory by key and optional category."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 1},
                "category": {"type": "string", "enum": ["preference", "allergy", "lifestyle", "goal", "care_context", "communication"]},
            },
            "required": ["key"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        active_db = db or self.service.db
        category_value = kwargs.get("category")
        archived = archive_memory_by_key(
            active_db,
            user_id=DEFAULT_USER_ID,
            key=str(kwargs["key"]),
            category=str(category_value) if category_value is not None else None,
        )
        if not archived:
            return ToolResultPayload(message="没有找到对应的长期记忆。", refresh=[])
        return ToolResultPayload(message="已删除对应长期记忆。", refresh=[])


class QueryHbA1cTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_hba1c"

    @property
    def description(self) -> str:
        return "Query the user's latest HbA1c value and whether a recheck reminder is needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.hba1c_service import get_latest_hba1c, check_hba1c_reminder_needed

        active_db = db or self.service.db
        latest = get_latest_hba1c(active_db, user_id=DEFAULT_USER_ID)
        reminder = check_hba1c_reminder_needed(active_db, user_id=DEFAULT_USER_ID)

        if latest is None:
            message = "暂无 HbA1c 记录。建议去医院检测糖化血红蛋白。"
        else:
            message = f"最近一次 HbA1c：{latest['value']}%（{latest['test_date']}）"
            if reminder:
                message += "\n距上次检测已超过 90 天，建议复查。"

        return ToolResultPayload(message=message, refresh=[])


class TriggerHypoProtocolTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "trigger_hypo_protocol"

    @property
    def description(self) -> str:
        return "Trigger the 15-15 hypoglycemia rescue protocol when low blood sugar is detected or user reports hypoglycemia symptoms."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "glucose_value": {"type": "number", "minimum": 0},
            },
            "required": ["glucose_value"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.hypoglycemia_service import detect_hypoglycemia, create_hypo_event

        active_db = db or self.service.db
        value = float(kwargs["glucose_value"])
        detection = detect_hypoglycemia(value)

        if not detection["is_hypo"]:
            return ToolResultPayload(
                message=f"血糖 {value} mmol/L 未达到低血糖标准（≤3.9），无需启动急救流程。",
                refresh=[],
            )

        create_hypo_event(
            active_db,
            user_id=DEFAULT_USER_ID,
            initial_value=value,
            severity=detection["severity"],
        )

        severity_text = "严重低血糖" if detection["severity"] == "severe" else "低血糖"
        protocol = detection["protocol"]
        options = "\n".join(
            f"- {opt['item']}：{opt['amount']}" for opt in protocol["carb_options"]
        )

        message = (
            f"⚠️ 检测到{severity_text}（{value} mmol/L）！\n\n"
            f"请立即摄入 {protocol['carb_dose_grams']}g 快速碳水：\n{options}\n\n"
            f"摄入后等待 {protocol['recheck_minutes']} 分钟，然后复测血糖。"
        )
        if detection["severity"] == "severe":
            message += "\n\n🚨 严重低血糖，建议联系紧急联系人或拨打急救电话。"

        return ToolResultPayload(message=message, refresh=["glucose"])


class QueryTIRTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_tir"

    @property
    def description(self) -> str:
        return "Query the user's Time in Range (TIR) statistics for the specified period."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {"type": "number", "minimum": 7},
            },
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.tir_service import calculate_tir

        active_db = db or self.service.db
        days = int(kwargs.get("days", 14))
        result = calculate_tir(active_db, user_id=DEFAULT_USER_ID, days=days)

        if result["status"] == "insufficient_data":
            message = f"过去 {days} 天血糖记录不足（仅 {result['count']} 条），需要至少 10 条才能计算 TIR。"
        else:
            assessment_text = {
                "excellent": "优秀", "good": "良好",
                "needs_improvement": "需改善", "poor": "较差",
            }.get(result["assessment"], "未知")
            message = (
                f"过去 {days} 天的血糖控制情况：\n"
                f"- TIR（达标时间）：{result['tir']}%（目标 >70%）\n"
                f"- TBR（低于范围）：{result['tbr']}%（目标 <4%）\n"
                f"- TAR（高于范围）：{result['tar']}%（目标 <25%）\n"
                f"- 评估：{assessment_text}\n"
                f"- 总记录数：{result['total_readings']} 条"
            )

        return ToolResultPayload(message=message, refresh=[])


class LogExerciseTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "log_exercise"

    @property
    def description(self) -> str:
        return "Record an exercise session for the user. Types: walking, running, swimming, cycling, yoga, strength. Intensities: low, medium, high."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "exercise_type": {"type": "string", "enum": ["walking", "running", "swimming", "cycling", "yoga", "strength"]},
                "intensity": {"type": "string", "enum": ["low", "medium", "high"]},
                "duration_minutes": {"type": "number", "minimum": 1},
            },
            "required": ["exercise_type", "intensity", "duration_minutes"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.exercise_service import create_exercise_record, EXERCISE_TYPE_CN

        active_db = db or self.service.db
        exercise_type = str(kwargs["exercise_type"])
        intensity = str(kwargs["intensity"])
        duration = int(kwargs["duration_minutes"])

        record = create_exercise_record(
            active_db,
            user_id=DEFAULT_USER_ID,
            exercise_type=exercise_type,
            intensity=intensity,
            duration_minutes=duration,
        )

        type_cn = EXERCISE_TYPE_CN.get(exercise_type, exercise_type)
        message = (
            f"已记录运动：{type_cn} {duration} 分钟（{intensity}强度），"
            f"预估消耗 {record['calories_burned']} 千卡。"
        )
        return ToolResultPayload(message=message, refresh=["adherence"])


class QueryExerciseTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_exercise"

    @property
    def description(self) -> str:
        return "Query the user's recent exercise records and summary."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days": {"type": "number", "minimum": 1},
            },
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.exercise_service import get_exercise_summary

        active_db = db or self.service.db
        days = int(kwargs.get("days", 7))
        summary = get_exercise_summary(active_db, user_id=DEFAULT_USER_ID, days=days)

        if summary["session_count"] == 0:
            message = f"过去 {days} 天没有运动记录。建议每天至少进行 30 分钟中等强度运动。"
        else:
            message = (
                f"过去 {days} 天运动统计：\n"
                f"- 运动次数：{summary['session_count']} 次\n"
                f"- 总时长：{summary['total_minutes']} 分钟\n"
                f"- 总消耗：{summary['total_calories']} 千卡"
            )

        return ToolResultPayload(message=message, refresh=[])


class QueryScreeningTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "query_screening"

    @property
    def description(self) -> str:
        return "Query the user's complication screening calendar and overdue items."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.screening_service import get_overdue_screenings, init_default_screening_items

        active_db = db or self.service.db
        init_default_screening_items(active_db, user_id=DEFAULT_USER_ID)
        overdue = get_overdue_screenings(active_db, user_id=DEFAULT_USER_ID)

        if not overdue:
            message = "所有并发症筛查项目均在有效期内，暂无需要复查的项目。"
        else:
            lines = []
            for entry in overdue:
                item = entry["item"]
                status_text = "从未检查" if entry["status"] == "never_checked" else "已过期"
                lines.append(f"- {item['name']}（{status_text}）")
            message = "以下筛查项目需要关注：\n" + "\n".join(lines)

        return ToolResultPayload(message=message, refresh=[])


class CalculateInsulinTool(BackendAgentTool):
    @property
    def name(self) -> str:
        return "calculate_insulin"

    @property
    def description(self) -> str:
        return "Calculate suggested insulin bolus dose based on current glucose and carb intake. ALWAYS show disclaimer."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "current_glucose": {"type": "number", "minimum": 1.0},
                "carbs_intake": {"type": "number", "minimum": 0},
                "iob": {"type": "number", "minimum": 0},
            },
            "required": ["current_glucose", "carbs_intake"],
        }

    def run(self, db: Session | None = None, **kwargs: Any) -> ToolResultPayload:
        from app.services.insulin_calc_service import calculate_insulin_dose, SAFETY_RULES
        from app.services.patient_service import get_patient_profile

        active_db = db or self.service.db
        profile = get_patient_profile(active_db, DEFAULT_USER_ID)

        if not profile:
            return ToolResultPayload(
                message="请先完善个人档案后再使用胰岛素计算器。", refresh=[]
            )

        icr = profile.get("icr")
        isf = profile.get("isf")
        if not icr or not isf:
            return ToolResultPayload(
                message="请先在个人档案中设置碳水比(ICR)和敏感系数(ISF)后再使用计算器。",
                refresh=[],
            )

        result = calculate_insulin_dose(
            current_glucose=float(kwargs["current_glucose"]),
            carbs_intake=float(kwargs["carbs_intake"]),
            icr=icr,
            isf=isf,
            target_glucose=profile.get("target_glucose", 5.5),
            iob=float(kwargs.get("iob", 0)),
            max_bolus=profile.get("max_bolus", 15.0),
        )

        message = (
            f"💉 胰岛素剂量建议：{result['suggested_dose']} U\n"
            f"- 校正剂量：{result['correction_dose']} U\n"
            f"- 碳水覆盖：{result['carb_dose']} U\n"
            f"- IOB 扣减：{result['iob_deducted']} U\n\n"
            f"{SAFETY_RULES['disclaimer_text']}"
        )

        return ToolResultPayload(message=message, refresh=[])


AGENT_SYSTEM_PROMPT = (
    "你是糖尿病管理演示系统的后端智能编排器。"
    "必须优先通过已提供的工具完成业务动作，而不是编造已记录/已设置的结果。"
    "不要调用任何未注册的工具，不要要求访问网页、外部系统、shell 或文件系统。"
    "当用户想让系统执行动作时，调用对应工具；当用户只是泛泛询问支持什么功能时，调用 guidance_fallback。"
    "设置用药提醒时，由你在多轮对话中负责收集药名、剂量、提醒时间；信息不完整时先追问。"
    "只有当药名、剂量、提醒时间都已明确时，才调用 parse_medication_plan。"
    "当用户血糖值 ≤ 3.9 时，必须调用 trigger_hypo_protocol 启动低血糖急救流程。"
    "当用户询问胰岛素剂量时，调用 calculate_insulin，结果必须附带免责声明。"
    "当用户说“我晚饭想吃米饭配花生酱”时，必须回复“你对花生过敏哦，不能吃花生酱”。"
    "最终回复应简短自然，与工具返回含义保持一致。"
)
