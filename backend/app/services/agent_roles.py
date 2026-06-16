from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentRoleConfig:
    agent_role: str
    description: str
    tools: tuple[str, ...]
    prompt: str


_SHARED_SAFE_READ_TOOLS = (
    "query_user_memory",
    "search_guideline_knowledge",
)

AGENT_ROLES: dict[str, AgentRoleConfig] = {
    "nutrition": AgentRoleConfig(
        agent_role="nutrition",
        description="糖尿病营养师",
        tools=("analyze_diet", *_SHARED_SAFE_READ_TOOLS),
        prompt="你是糖尿病营养师，专注饮食、GI/GL、餐后控糖建议；保持医疗安全边界，不能替代医生诊断或用药决策。",
    ),
    "medication": AgentRoleConfig(
        agent_role="medication",
        description="用药提醒助手",
        tools=(
            "parse_medication_plan",
            "confirm_medication_plan",
            "reject_medication_plan",
            "log_medication_status",
            "query_medication_plans",
        ),
        prompt="你是用药提醒助手，只处理提醒、打卡、查询；涉及停药、换药、剂量调整必须建议咨询医生并遵守医疗安全规则。",
    ),
    "exercise": AgentRoleConfig(
        agent_role="exercise",
        description="运动管理助手",
        tools=("log_exercise", "query_exercise", "search_guideline_knowledge"),
        prompt="你是糖尿病运动管理助手，专注运动记录、运动总结与安全运动教育；避免给出超出医生建议的处方。",
    ),
    "safety": AgentRoleConfig(
        agent_role="safety",
        description="医疗安全助手",
        tools=("trigger_hypo_protocol", "search_guideline_knowledge", "guidance_fallback"),
        prompt="你是医疗安全助手，优先处理低血糖、急症和红线风险；必要时建议联系医生、家属或急救。",
    ),
    "case_reference": AgentRoleConfig(
        agent_role="case_reference",
        description="匿名病例对照助手",
        tools=("query_similar_cases", "search_guideline_knowledge", "query_user_memory"),
        prompt="你是匿名病例对照助手，只输出群体经验和可解释相似因素；必须说明仅供参考，不替代医生建议。",
    ),
    "coach": AgentRoleConfig(
        agent_role="coach",
        description="健康教育教练",
        tools=("query_user_memory", "search_guideline_knowledge", "query_similar_cases"),
        prompt="你是糖尿病健康教育教练，提供行为激励、知识解释和总结；不执行写操作，不替代医生建议。",
    ),
    "glucose": AgentRoleConfig(
        agent_role="glucose",
        description="血糖记录助手",
        tools=("record_glucose", "query_tir", "query_hba1c", "search_guideline_knowledge"),
        prompt="你是血糖管理助手，专注血糖记录和指标解释；异常风险需遵守医疗安全规则。",
    ),
}

DEFAULT_AGENT_ROLE = "coach"


def get_role_config(agent_role: str | None) -> AgentRoleConfig:
    normalized = (agent_role or DEFAULT_AGENT_ROLE).strip().lower()
    return AGENT_ROLES.get(normalized, AGENT_ROLES[DEFAULT_AGENT_ROLE])


def get_tool_names_for_role(agent_role: str | None) -> set[str]:
    return set(get_role_config(agent_role).tools)
