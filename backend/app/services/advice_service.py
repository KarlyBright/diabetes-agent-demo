from __future__ import annotations

from typing import Any


GlucoseRecord = dict[str, Any]
MealAnalysis = dict[str, Any]


def _extract_medication_text(profile: dict[str, Any]) -> str | None:
    medications = profile.get("medications")
    if isinstance(medications, list):
        normalized_medications = [str(item).strip() for item in medications if str(item).strip()]
        if normalized_medications:
            return "、".join(normalized_medications)

    medication = profile.get("medication")
    if medication is None:
        return None

    medication_text = str(medication).strip()
    return medication_text or None


def generate_daily_advice_logic(
    profile: dict[str, Any] | None,
    recent_glucose: list[GlucoseRecord],
    recent_meal_analysis: MealAnalysis | None,
    latest_hba1c: dict[str, Any] | None = None,
) -> dict[str, Any]:
    advice_list = []
    risk_level = "low"
    normalized_profile = profile or {}
    medication_text = _extract_medication_text(normalized_profile)

    has_profile_summary_context = bool(
        normalized_profile.get("age") is not None
        and normalized_profile.get("diabetes_type")
    )

    if has_profile_summary_context:
        profile_summary = f"{normalized_profile['age']}岁，{normalized_profile['diabetes_type']}"
        if medication_text:
            profile_summary = f"{profile_summary}，当前服用{medication_text}"
    else:
        profile_summary = None

    if recent_glucose:
        latest_glucose = recent_glucose[0]
        glucose_value = latest_glucose["value"]
        measure_type = latest_glucose["measure_type"]

        if glucose_value >= 7.0 and measure_type == "fasting":
            glucose_summary = f"最近1次空腹血糖为{glucose_value} mmol/L，略高于理想范围。"
            advice_list.append("建议继续监测空腹血糖，并注意早餐前后的饮食控制。")
            risk_level = "medium"
        elif glucose_value >= 10.0 and measure_type == "post_meal":
            glucose_summary = f"最近1次餐后血糖为{glucose_value} mmol/L，偏高。"
            advice_list.append("建议减少高碳水和含糖饮料摄入，并关注餐后活动。")
            risk_level = "high"
        else:
            glucose_summary = f"最近1次血糖为{glucose_value} mmol/L，整体相对平稳。"
    else:
        glucose_summary = "暂无近期血糖记录，建议尽快补充监测数据。"
        advice_list.append("建议先记录今天的血糖数据，便于生成更准确的建议。")

    hba1c_summary = None
    if latest_hba1c:
        hba1c_value = latest_hba1c["value"]
        hba1c_summary = f"最近一次糖化血红蛋白(HbA1c)为 {hba1c_value}%"
        if hba1c_value >= 8.0:
            advice_list.append("HbA1c 偏高，建议与医生讨论是否需要调整治疗方案。")
            risk_level = "high"
        elif hba1c_value >= 7.0:
            advice_list.append("HbA1c 略高于目标，继续保持良好的饮食和运动习惯。")
            if risk_level != "high":
                risk_level = "medium"

    if recent_meal_analysis:
        meal_summary = (
            f"最近一次饮食风险等级为 {recent_meal_analysis['risk_level']}，"
            f"综合评分为 {recent_meal_analysis['score']}。"
        )

        if recent_meal_analysis["risk_level"] == "high":
            advice_list.append("今晚建议减少主食摄入，避免含糖饮料和油炸食品。")
            advice_list.append("饭后可适当活动，并关注餐后血糖变化。")
            risk_level = "high"
        elif recent_meal_analysis["risk_level"] == "medium":
            advice_list.append("建议控制本餐总碳水量，适当增加蔬菜比例。")
            if risk_level != "high":
                risk_level = "medium"
        else:
            advice_list.append("当前饮食整体较均衡，可以继续保持。")
    else:
        meal_summary = "暂无近期饮食分析记录，建议补充饮食信息。"
        advice_list.append("建议记录本餐饮食内容，帮助智能体生成更准确建议。")

    # 去重
    advice_list = list(dict.fromkeys(advice_list))

    if risk_level == "high":
        agent_prompt_hint = "用户近期风险偏高，请采用温和但明确的提醒语气。"
    elif risk_level == "medium":
        agent_prompt_hint = "用户存在一定风险，请采用鼓励式语气给出建议。"
    else:
        agent_prompt_hint = "用户整体状态相对平稳，请采用积极正向的语气反馈。"

    return {
        "profile_summary": profile_summary,
        "glucose_summary": glucose_summary,
        "hba1c_summary": hba1c_summary,
        "meal_summary": meal_summary,
        "risk_level": risk_level,
        "daily_advice": advice_list,
        "agent_prompt_hint": agent_prompt_hint
    }