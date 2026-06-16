from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.safety_model import SafetyIntervention


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MedicalRiskResult:
    risk_level: str
    category: str
    matched_rule: str


_CRITICAL_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("severe_hypoglycemia", ("血糖3.", "血糖 3.", "3.2", "3.1", "3.0"), "hypoglycemia"),
    ("emergency_hypoglycemia", ("低血糖", "昏迷", "抽搐"), "hypoglycemia"),
)
_HIGH_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "insulin_dose_question",
        ("打多少胰岛素", "胰岛素剂量", "该打多少", "胰岛素打多少"),
        "insulin",
    ),
    (
        "stop_or_change_medication",
        ("停二甲双胍", "停药", "不吃药", "换药", "自行调药", "自行调剂量", "加量", "减量"),
        "medication",
    ),
    ("avoid_emergency_care", ("不去医院", "不看医生", "不用急救"), "emergency"),
)
_MEDIUM_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("hypoglycemia_symptom_followup", ("头晕", "手抖"), "hypoglycemia"),
    ("diagnosis_question", ("是不是肾病", "是不是并发症", "是不是糖尿病"), "diagnosis"),
    ("extreme_lifestyle", ("不吃主食", "完全不吃碳水", "空腹剧烈运动"), "lifestyle"),
)
_HIGH_REGEX_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "insulin_dose_variant",
        r"(胰岛素.*(几\s*(u|U|单位)|多少|剂量|合适)|该打.*(胰岛素|几\s*(u|U|单位))|打几\s*(u|U|单位).*胰岛素)",
        "insulin",
    ),
    (
        "medication_stop_variant",
        r"((二甲双胍|这个药|药).*(停掉|停了|停用|要不要停|能不能停)|停掉.*(二甲双胍|这个药|药))",
        "medication",
    ),
)


def _matches_regex(pattern: str, message: str) -> bool:
    return re.search(pattern, message, flags=re.IGNORECASE) is not None


def classify_medical_risk(message: str) -> MedicalRiskResult:
    normalized = message.strip().replace("？", "?")

    for rule_name, keywords, category in _CRITICAL_RULES:
        if any(keyword in normalized for keyword in keywords):
            return MedicalRiskResult("critical", category, rule_name)

    for rule_name, keywords, category in _HIGH_RULES:
        if any(keyword in normalized for keyword in keywords):
            return MedicalRiskResult("high", category, rule_name)

    for rule_name, pattern, category in _HIGH_REGEX_RULES:
        if _matches_regex(pattern, normalized):
            return MedicalRiskResult("high", category, rule_name)

    for rule_name, keywords, category in _MEDIUM_RULES:
        if any(keyword in normalized for keyword in keywords):
            return MedicalRiskResult("medium", category, rule_name)

    return MedicalRiskResult("normal", "general", "none")


def build_safety_response(result: MedicalRiskResult) -> dict[str, Any]:
    if result.risk_level == "critical" and result.category == "hypoglycemia":
        return {
            "content": (
                "检测到低血糖风险，请立即按 15-15 原则处理：立即摄入 15g 快速糖，"
                "15 分钟后复测血糖；若仍低或症状加重，请尽快呼叫家人协助或联系急救。"
            ),
            "safety_level": "critical",
            "quick_replies": ["我已经补糖了", "需要低血糖处理步骤"],
        }

    if result.risk_level == "high":
        content_by_category = {
            "insulin": "胰岛素剂量属于高风险医疗决策，请按医生预设方案或经医生确认的计算器参数执行；当前不能直接给出自行注射剂量。",
            "emergency": "这类情况可能需要及时医疗评估，不建议自行决定不就医；如症状明显或持续，请尽快联系医生或急救。",
            "medication": "这类用药调整需要结合病情和化验结果判断，请先咨询医生，不建议自行调整当前方案。",
        }
        return {
            "content": content_by_category.get(result.category, "这类医疗决策需要医生评估，请先咨询医生。"),
            "safety_level": "high",
            "quick_replies": ["联系医生前我该准备什么", "告诉我当前风险"],
        }

    if result.risk_level == "medium":
        if result.category == "hypoglycemia":
            return {
                "content": append_disclaimer(
                    "头晕、手抖可能和低血糖有关。请先尽快测一次血糖；如果无法立即测量且症状明显，"
                    "可先按低血糖风险处理，补充约15克快速糖，并在15分钟后复测。",
                    result.category,
                ),
                "safety_level": "medium",
                "quick_replies": ["我去测血糖", "需要低血糖处理步骤"],
            }
        return {
            "content": append_disclaimer("这类问题可以先做一般性说明，但具体方案仍需结合医生建议。", result.category),
            "safety_level": "medium",
            "quick_replies": ["继续说明一般原则", "我想先了解注意事项"],
        }

    return {
        "content": "",
        "safety_level": "normal",
        "quick_replies": [],
    }


def append_disclaimer(content: str, category: str) -> str:
    disclaimer_by_category = {
        "insulin": "以上仅供一般信息参考，胰岛素剂量调整不能替代医生面诊，请按医嘱执行。",
        "medication": "以上仅供一般信息参考，用药调整请咨询医生或药师。",
        "diagnosis": "以上仅供一般信息参考，不能替代医生诊断。",
        "hypoglycemia": "如症状持续、意识不清或无法进食，请立即就医。",
    }
    disclaimer = disclaimer_by_category.get(
        category,
        "以上仅供一般信息参考，不能替代医生建议。",
    )
    return f"{content}\n\n{disclaimer}"


def log_safety_intervention(
    db: Session,
    user_id: int,
    result: MedicalRiskResult,
    action: str,
) -> SafetyIntervention | None:
    record = SafetyIntervention(
        user_id=user_id,
        risk_level=result.risk_level,
        category=result.category,
        matched_rule=result.matched_rule,
        action=action,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to log medical safety intervention",
            extra={
                "user_id": user_id,
                "risk_level": result.risk_level,
                "category": result.category,
                "action": action,
            },
        )
        return None
    return record


def should_block_agent_run(result: MedicalRiskResult) -> bool:
    return result.risk_level in {"critical", "high"}
