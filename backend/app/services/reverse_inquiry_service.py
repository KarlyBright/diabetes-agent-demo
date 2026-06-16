from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.medication_model import MedicationPlan, MedicationTakenRecord
from app.models.questionnaire_model import AgentQuestionSession
from app.services.hypoglycemia_service import resolve_hypo_event

INQUIRY_TEMPLATES = {
    "consecutive_high": {
        "first_step": "ask_reason",
        "first_message": "最近血糖偏高，最近有熬夜、感冒或饮食变化吗？",
        "quick_replies": ["最近熬夜", "有点感冒", "晚餐吃多了"],
    },
    "medication_missed": {
        "first_step": "ask_status",
        "first_message": "今天的药还没打卡，是已经吃了、忘了，还是医生调整了？",
        "quick_replies": ["已经吃了", "忘了", "医生调整了"],
    },
    "hypoglycemia_followup": {
        "first_step": "ask_recovery",
        "first_message": "刚才低血糖恢复了吗？现在血糖多少？",
        "quick_replies": ["现在4.2", "还是3.5", "还没测"],
    },
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _serialize_context(context: dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False)


def _deserialize_context(raw_context: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_context)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_session(db: Session, *, session_id: int, user_id: int) -> AgentQuestionSession:
    session = (
        db.query(AgentQuestionSession)
        .filter(
            AgentQuestionSession.id == session_id,
            AgentQuestionSession.user_id == user_id,
        )
        .first()
    )
    if session is None:
        raise ValueError("inquiry session not found")
    return session


def _build_start_response(session: AgentQuestionSession) -> dict[str, Any]:
    template = INQUIRY_TEMPLATES[session.trigger_type]
    return {
        "session_id": session.id,
        "trigger_type": session.trigger_type,
        "status": session.status,
        "message": template["first_message"],
        "quick_replies": list(template["quick_replies"]),
    }


def _can_reuse_session(
    existing_session: AgentQuestionSession,
    *,
    trigger_type: str,
    incoming_context: dict[str, Any],
) -> bool:
    if trigger_type != "medication_missed":
        return True
    existing_context = _deserialize_context(existing_session.context_json)
    return existing_context.get("plan_id") == incoming_context.get("plan_id")


def _extract_explicit_glucose_value(message: str) -> float | None:
    normalized = message.strip()
    patterns = (
        r"(?:血糖|糖值|现在)\s*(?:是|为|=|：|:)?\s*(\d+(?:\.\d+)?)",
        r"(?:还是|仍然|仍是)\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*(?:mmol\s*/\s*l|mmol/l|毫摩尔)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _record_medication_taken(
    db: Session,
    *,
    user_id: int,
    plan_id: int | None = None,
) -> bool:
    query = db.query(MedicationPlan).filter(
        MedicationPlan.user_id == user_id,
        MedicationPlan.status == "active",
    )
    if plan_id is not None:
        query = query.filter(MedicationPlan.plan_id == plan_id)
    plan = query.order_by(MedicationPlan.plan_id.asc()).first()
    if plan is None:
        return False
    db.add(
        MedicationTakenRecord(
            user_id=user_id,
            plan_id=plan.plan_id,
            drug_name=plan.drug_name,
            dosage=plan.dosage,
            time_text=plan.time_text,
            remind_time=plan.remind_time,
            status="taken",
        )
    )
    return True


def start_inquiry(
    db: Session,
    *,
    user_id: int,
    trigger_type: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    template = INQUIRY_TEMPLATES.get(trigger_type)
    if template is None:
        raise ValueError("unsupported inquiry trigger type")

    incoming_context = context or {}
    existing_sessions = (
        db.query(AgentQuestionSession)
        .filter(
            AgentQuestionSession.user_id == user_id,
            AgentQuestionSession.trigger_type == trigger_type,
            AgentQuestionSession.status == "active",
        )
        .order_by(AgentQuestionSession.id.desc())
        .all()
    )
    for existing_session in existing_sessions:
        if _can_reuse_session(existing_session, trigger_type=trigger_type, incoming_context=incoming_context):
            return _build_start_response(existing_session)

    session = AgentQuestionSession(
        user_id=user_id,
        trigger_type=trigger_type,
        status="active",
        current_step=str(template["first_step"]),
        context_json=_serialize_context(incoming_context),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _build_start_response(session)


def classify_inquiry_answer(message: str, session: AgentQuestionSession) -> dict[str, Any]:
    normalized = message.strip()
    if session.trigger_type == "consecutive_high":
        if "熬夜" in normalized or "晚睡" in normalized:
            return {"intent": "sleep_issue"}
        if "感冒" in normalized or "发烧" in normalized:
            return {"intent": "illness"}
        if "吃" in normalized or "夜宵" in normalized or "饮食" in normalized:
            return {"intent": "diet_change"}
        return {"intent": "other"}

    if session.trigger_type == "medication_missed":
        if "已经吃" in normalized or "已吃" in normalized:
            return {"intent": "already_taken"}
        if "忘" in normalized:
            return {"intent": "forgotten"}
        if "医生" in normalized or "调整" in normalized:
            return {"intent": "doctor_changed"}
        return {"intent": "other"}

    if session.trigger_type == "hypoglycemia_followup":
        value = _extract_explicit_glucose_value(normalized)
        if value is not None:
            if value < 3.9:
                return {"intent": "still_low", "glucose_value": value}
            return {"intent": "recovered", "glucose_value": value}
        return {"intent": "unknown"}

    return {"intent": "other"}


def advance_inquiry(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    message: str,
) -> dict[str, Any]:
    session = _load_session(db, session_id=session_id, user_id=user_id)
    classification = classify_inquiry_answer(message, session)
    context = _deserialize_context(session.context_json)
    context = {**context, "last_user_message": message, "last_classification": classification}

    if session.trigger_type == "consecutive_high":
        session.current_step = "completed"
        session.status = "closed"
        session.context_json = _serialize_context(context)
        session.updated_at = _now_iso()
        db.commit()
        advice_map = {
            "sleep_issue": "熬夜容易让血糖波动，建议先调整作息和睡眠，并留意接下来1-2次血糖变化。",
            "illness": "感冒或感染时血糖容易升高，建议加强监测，多喝水，必要时及时就医。",
            "diet_change": "最近饮食变化可能是原因，建议回顾晚餐主食和夜宵，并优先减少高糖高油食物。",
            "other": "如果高糖持续，建议继续监测并回顾饮食、作息和用药情况，必要时联系医生。",
        }
        return {
            "session_id": session.id,
            "status": session.status,
            "message": advice_map.get(classification["intent"], advice_map["other"]),
            "quick_replies": [],
        }

    if session.trigger_type == "medication_missed":
        session.current_step = "completed"
        session.status = "closed"
        if classification["intent"] == "already_taken":
            raw_plan_id = context.get("plan_id")
            plan_id = raw_plan_id if isinstance(raw_plan_id, int) else None
            recorded = False
            if plan_id is not None:
                recorded = _record_medication_taken(db, user_id=session.user_id, plan_id=plan_id)
            context = {**context, "medication_taken_recorded": recorded}
        session.context_json = _serialize_context(context)
        session.updated_at = _now_iso()
        db.commit()
        if classification["intent"] == "already_taken":
            if context.get("medication_taken_recorded") is True:
                message = "已为你补记本次服药状态，避免系统继续提醒。"
            else:
                message = "我还不能确认具体是哪一个用药计划，请先查看当前计划后再补记。"
            return {
                "session_id": session.id,
                "status": session.status,
                "message": message,
                "quick_replies": ["查看用药计划"],
            }
        if classification["intent"] == "forgotten":
            return {
                "session_id": session.id,
                "status": session.status,
                "message": "如果刚想起来，先确认是否适合补服；不确定时请联系医生或药师。",
                "quick_replies": ["我去确认一下", "帮我查看当前计划"],
            }
        return {
            "session_id": session.id,
            "status": session.status,
            "message": "如果医生已调整方案，建议尽快更新用药计划，避免后续提醒不准确。",
            "quick_replies": ["帮我查看当前计划"],
        }

    if session.trigger_type == "hypoglycemia_followup":
        session.context_json = _serialize_context(context)
        session.updated_at = _now_iso()
        if classification["intent"] == "still_low":
            session.current_step = "repeat_15_15"
            db.commit()
            return {
                "session_id": session.id,
                "status": session.status,
                "message": "血糖仍低于3.9，请再次补充15克快速糖，15分钟后再复测。若症状加重请及时求助。",
                "quick_replies": ["15分钟后再测", "需要低血糖处理步骤"],
            }
        if classification["intent"] == "recovered":
            session.current_step = "completed"
            session.status = "closed"
            glucose_value = float(classification["glucose_value"])
            resolve_hypo_event(db, user_id=session.user_id, resolved_value=glucose_value)
            db.commit()
            return {
                "session_id": session.id,
                "status": session.status,
                "message": "看起来已经恢复，后续可以补充少量主食并继续观察，避免再次低血糖。",
                "quick_replies": [],
            }
        db.commit()
        return {
            "session_id": session.id,
            "status": session.status,
            "message": "如果方便的话，请告诉我当前血糖数值，我好继续判断。",
            "quick_replies": ["还是3.5", "现在4.2"],
        }

    raise ValueError("unsupported inquiry trigger type")


def close_inquiry(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = _load_session(db, session_id=session_id, user_id=user_id)
    context = _deserialize_context(session.context_json)
    merged_context = {**context, "outcome": outcome or {}}
    session.status = "closed"
    session.current_step = "completed"
    session.context_json = _serialize_context(merged_context)
    session.updated_at = _now_iso()
    db.commit()
    return {
        "session_id": session.id,
        "status": session.status,
        "context": merged_context,
    }
