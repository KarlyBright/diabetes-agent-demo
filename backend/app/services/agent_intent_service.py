from __future__ import annotations

from dataclasses import dataclass

from app.services.medical_safety_service import classify_medical_risk


@dataclass(frozen=True, slots=True)
class AgentIntentResult:
    intent: str
    agent_role: str
    risk_level: str
    matched_rule: str


_CASE_REFERENCE_STRONG_KEYWORDS = ("类似", "病例", "案例", "对照", "相似")
_CASE_REFERENCE_PATTERN_KEYWORDS = ("晚饭后高", "餐后高", "晚餐后高", "饭后高")

_INTENT_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("medication", ("药", "二甲双胍", "服药", "吃了", "漏服", "提醒", "打卡"), "medication"),
    ("exercise", ("运动", "散步", "跑步", "骑车", "游泳", "锻炼"), "exercise"),
    ("glucose", ("血糖", "空腹", "餐后", "测", "tir", "hba1c", "糖化"), "glucose"),
    ("diet", ("吃", "晚餐", "午餐", "早餐", "饮食", "火锅", "主食", "碳水", "控糖", "餐"), "nutrition"),
)


def classify_agent_intent(message: str) -> AgentIntentResult:
    normalized = message.strip().lower()
    safety_result = classify_medical_risk(message)
    if safety_result.risk_level in {"critical", "high"}:
        return AgentIntentResult(
            intent="safety",
            agent_role="safety",
            risk_level=safety_result.risk_level,
            matched_rule=safety_result.matched_rule,
        )

    if any(keyword in normalized for keyword in _CASE_REFERENCE_STRONG_KEYWORDS) and any(
        keyword in normalized for keyword in _CASE_REFERENCE_PATTERN_KEYWORDS
    ):
        return AgentIntentResult(
            intent="case_reference",
            agent_role="case_reference",
            risk_level=safety_result.risk_level,
            matched_rule=safety_result.matched_rule,
        )
    if "为什么" in normalized and any(keyword in normalized for keyword in _CASE_REFERENCE_PATTERN_KEYWORDS):
        return AgentIntentResult(
            intent="case_reference",
            agent_role="case_reference",
            risk_level=safety_result.risk_level,
            matched_rule=safety_result.matched_rule,
        )

    for intent, keywords, agent_role in _INTENT_RULES:
        if any(keyword.lower() in normalized for keyword in keywords):
            return AgentIntentResult(
                intent=intent,
                agent_role=agent_role,
                risk_level=safety_result.risk_level,
                matched_rule=safety_result.matched_rule,
            )

    return AgentIntentResult(
        intent="general",
        agent_role="coach",
        risk_level=safety_result.risk_level,
        matched_rule=safety_result.matched_rule,
    )
