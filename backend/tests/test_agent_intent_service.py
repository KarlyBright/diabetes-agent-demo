from app.services.agent_intent_service import classify_agent_intent


def test_classify_agent_intent_routes_common_business_domains() -> None:
    assert classify_agent_intent("晚餐吃火锅怎么控糖").intent == "diet"
    assert classify_agent_intent("二甲双胍今天已经吃了").intent == "medication"
    assert classify_agent_intent("今天散步30分钟").intent == "exercise"
    assert classify_agent_intent("我刚测血糖 7.2").intent == "glucose"
    assert classify_agent_intent("为什么我总是晚饭后高").intent == "case_reference"
    assert classify_agent_intent("低血糖3.5怎么办").intent == "safety"


def test_case_reference_does_not_steal_medication_or_glucose_why_questions() -> None:
    assert classify_agent_intent("为什么我总忘记吃药").intent == "medication"
    assert classify_agent_intent("我总是漏服怎么办").intent == "medication"
    assert classify_agent_intent("为什么今天血糖总是偏高").intent == "glucose"
    assert classify_agent_intent("有没有类似案例解释晚饭后高").intent == "case_reference"


def test_safety_intent_takes_priority_over_nutrition_or_medication_terms() -> None:
    result = classify_agent_intent("低血糖3.5，晚餐还要不要吃火锅")

    assert result.intent == "safety"
    assert result.agent_role == "safety"
    assert result.risk_level == "critical"
