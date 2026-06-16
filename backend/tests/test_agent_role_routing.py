from typing import Any

from app.services.agent_chat_service import AgentChatService
from app.services.agent_roles import get_role_config, get_tool_names_for_role


def test_role_tool_whitelist_keeps_nutrition_away_from_medication_writes() -> None:
    nutrition_tools = get_tool_names_for_role("nutrition")

    assert "analyze_diet" in nutrition_tools
    assert "query_user_memory" in nutrition_tools
    assert "search_guideline_knowledge" in nutrition_tools
    assert "parse_medication_plan" not in nutrition_tools
    assert "log_medication_status" not in nutrition_tools


def test_agent_chat_service_builds_tools_for_selected_role_only() -> None:
    service = AgentChatService(db=object())  # type: ignore[arg-type]

    nutrition_tool_names = {tool.name for tool in service._build_backend_tools("nutrition")}
    medication_tool_names = {tool.name for tool in service._build_backend_tools("medication")}

    assert "analyze_diet" in nutrition_tool_names
    assert "parse_medication_plan" not in nutrition_tool_names
    assert "parse_medication_plan" in medication_tool_names
    assert "analyze_diet" not in medication_tool_names


def test_role_prompt_identifies_specialty_without_removing_safety_rules() -> None:
    config = get_role_config("nutrition")

    assert config.agent_role == "nutrition"
    assert "营养" in config.prompt
    assert "医疗安全" in config.prompt or "医生" in config.prompt
