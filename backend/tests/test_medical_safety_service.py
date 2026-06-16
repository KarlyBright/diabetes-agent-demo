from unittest.mock import MagicMock

from app.services.medical_safety_service import (
    append_disclaimer,
    build_safety_response,
    classify_medical_risk,
    log_safety_intervention,
    should_block_agent_run,
)


def test_classify_medical_risk_covers_critical_high_medium_and_normal() -> None:
    critical = classify_medical_risk("我现在血糖3.2，头晕手抖怎么办")
    high = classify_medical_risk("我能不能停二甲双胍")
    medium = classify_medical_risk("我是不是肾病了")
    insulin_high = classify_medical_risk("血糖12，晚饭80g碳水该打多少胰岛素")
    dose_high = classify_medical_risk("二甲双胍能不能加量")
    hospital_high = classify_medical_risk("疑似酮症我不去医院可以吗")
    diet_medium = classify_medical_risk("我不吃主食行不行")
    normal = classify_medical_risk("今天早餐吃了燕麦和鸡蛋")

    assert critical.risk_level == "critical"
    assert critical.category == "hypoglycemia"
    assert should_block_agent_run(critical) is True

    assert high.risk_level == "high"
    assert high.category == "medication"
    assert should_block_agent_run(high) is True

    assert medium.risk_level == "medium"
    assert medium.category == "diagnosis"
    assert should_block_agent_run(medium) is False

    assert insulin_high.risk_level == "high"
    assert insulin_high.category == "insulin"
    assert should_block_agent_run(insulin_high) is True

    assert dose_high.risk_level == "high"
    assert dose_high.category == "medication"
    assert hospital_high.risk_level == "high"
    assert hospital_high.category == "emergency"
    assert diet_medium.risk_level == "medium"
    assert diet_medium.category == "lifestyle"

    assert normal.risk_level == "normal"
    assert normal.category == "general"
    assert should_block_agent_run(normal) is False


def test_classify_medical_risk_blocks_common_red_line_variants() -> None:
    examples = {
        "现在该打几单位胰岛素？": "insulin",
        "我今晚胰岛素几 U 合适？": "insulin",
        "二甲双胍要不要停掉？": "medication",
        "这个药我先停掉行吗？": "medication",
    }

    for message, category in examples.items():
        result = classify_medical_risk(message)
        assert result.risk_level == "high", message
        assert result.category == category
        assert should_block_agent_run(result) is True


def test_symptoms_without_low_glucose_evidence_do_not_trigger_rescue_flow() -> None:
    result = classify_medical_risk("今天有点头晕手抖")

    assert result.risk_level == "medium"
    assert result.category == "hypoglycemia"
    assert should_block_agent_run(result) is False


def test_build_safety_response_for_high_risk_does_not_recommend_stopping_medication() -> None:
    result = classify_medical_risk("我能不能停二甲双胍")
    response = build_safety_response(result)

    assert response["safety_level"] == "high"
    assert "咨询" in response["content"]
    assert "停药" not in response["content"]
    assert response["quick_replies"]


def test_build_safety_response_for_critical_hypoglycemia_uses_rescue_flow() -> None:
    result = classify_medical_risk("我现在血糖3.2，头晕手抖怎么办")
    response = build_safety_response(result)

    assert response["safety_level"] == "critical"
    assert "15" in response["content"]
    assert "急救" in response["content"] or "立即" in response["content"]


def test_append_disclaimer_adds_medical_warning_without_overwriting_content() -> None:
    content = append_disclaimer("建议先记录晚餐碳水。", category="insulin")

    assert content.startswith("建议先记录晚餐碳水。")
    assert "不能替代医生" in content or "咨询医生" in content


def test_log_safety_intervention_is_best_effort_on_database_failure() -> None:
    db = MagicMock()
    db.commit.side_effect = RuntimeError("database unavailable")
    result = classify_medical_risk("我能不能停二甲双胍")

    record = log_safety_intervention(
        db,
        user_id=1,
        result=result,
        action="blocked",
    )

    assert record is None
    db.rollback.assert_called_once()
