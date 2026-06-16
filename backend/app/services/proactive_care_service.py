from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.proactive_care_model import ProactiveCareEvent
from app.models.glucose_model import GlucoseRecordModel
from app.models.medication_model import MedicationPlan, MedicationTakenRecord
from app.models.exercise_model import ExerciseRecord

COOLDOWN_RULES = {
    "glucose_missing": 8 * 3600,
    "consecutive_high": 12 * 3600,
    "medication_missed": 4 * 3600,
    "meal_missing": 6 * 3600,
    "exercise_encourage": 24 * 3600,
    "hypo_followup": 0,
    "screening_due": 72 * 3600,
}

MAX_DAILY_PROACTIVE_MESSAGES = 5

CARE_MESSAGES = {
    "glucose_missing": "今天还没测血糖哦，方便测一下吗？",
    "consecutive_high": "最近血糖偏高，需要聊聊可能的原因吗？",
    "medication_missed": "今天的药还没打卡，是忘了还是有其他情况？",
    "meal_missing": "这顿饭吃了什么呀？记录一下帮你分析",
    "exercise_encourage": "好几天没运动了，今天散步 20 分钟怎么样？",
    "hypo_followup": "刚才低血糖恢复了吗？现在感觉怎么样？",
    "screening_due": "有筛查项目快到期了，要不要预约一下？",
}

PRIORITY_MAP = {
    "hypo_followup": 1,
    "consecutive_high": 2,
    "medication_missed": 3,
    "glucose_missing": 4,
    "meal_missing": 5,
    "exercise_encourage": 6,
    "screening_due": 7,
}


def scan_and_generate_care_events(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []

    today_count = _get_today_event_count(db, user_id=user_id)
    if today_count >= MAX_DAILY_PROACTIVE_MESSAGES:
        return generated

    if _check_glucose_missing(db, user_id=user_id):
        event = _try_create_event(db, user_id=user_id, event_type="glucose_missing")
        if event:
            generated.append(event)

    if _check_consecutive_high(db, user_id=user_id):
        event = _try_create_event(db, user_id=user_id, event_type="consecutive_high")
        if event:
            generated.append(event)

    missed_plan = _get_missed_medication_plan(db, user_id=user_id)
    if missed_plan is not None:
        event = _try_create_event(
            db,
            user_id=user_id,
            event_type="medication_missed",
            plan_id=missed_plan.plan_id,
        )
        if event:
            generated.append(event)

    if _check_exercise_missing(db, user_id=user_id):
        event = _try_create_event(db, user_id=user_id, event_type="exercise_encourage")
        if event:
            generated.append(event)

    return generated


def get_pending_care_events(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    events = (
        db.query(ProactiveCareEvent)
        .filter(
            ProactiveCareEvent.user_id == user_id,
            ProactiveCareEvent.status == "pending",
        )
        .order_by(ProactiveCareEvent.priority.asc())
        .all()
    )
    return [_to_dict(e) for e in events]


def dismiss_care_event(db: Session, *, event_id: int, user_id: int) -> bool:
    event = (
        db.query(ProactiveCareEvent)
        .filter(
            ProactiveCareEvent.id == event_id,
            ProactiveCareEvent.user_id == user_id,
        )
        .first()
    )
    if event is None:
        return False
    event.status = "dismissed"
    db.commit()
    return True


def deliver_care_event(db: Session, *, event_id: int, user_id: int) -> bool:
    event = (
        db.query(ProactiveCareEvent)
        .filter(
            ProactiveCareEvent.id == event_id,
            ProactiveCareEvent.user_id == user_id,
        )
        .first()
    )
    if event is None:
        return False
    event.status = "delivered"
    event.delivered_at = datetime.now().isoformat(timespec="seconds")
    db.commit()
    return True


def _try_create_event(
    db: Session,
    *,
    user_id: int,
    event_type: str,
    plan_id: int | None = None,
) -> dict[str, Any] | None:
    if _is_in_cooldown(db, user_id=user_id, event_type=event_type):
        return None

    cooldown_seconds = COOLDOWN_RULES.get(event_type, 8 * 3600)
    cooldown_until = (
        datetime.now() + timedelta(seconds=cooldown_seconds)
    ).isoformat(timespec="seconds")

    event = ProactiveCareEvent(
        user_id=user_id,
        event_type=event_type,
        priority=PRIORITY_MAP.get(event_type, 5),
        plan_id=plan_id,
        message=CARE_MESSAGES.get(event_type, ""),
        status="pending",
        cooldown_until=cooldown_until,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _to_dict(event)


def _is_in_cooldown(db: Session, *, user_id: int, event_type: str) -> bool:
    latest = (
        db.query(ProactiveCareEvent)
        .filter(
            ProactiveCareEvent.user_id == user_id,
            ProactiveCareEvent.event_type == event_type,
        )
        .order_by(ProactiveCareEvent.id.desc())
        .first()
    )
    if latest is None:
        return False
    if latest.cooldown_until is None:
        return False
    return datetime.now().isoformat() < latest.cooldown_until


def _get_today_event_count(db: Session, *, user_id: int) -> int:
    today_start = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    return (
        db.query(ProactiveCareEvent)
        .filter(
            ProactiveCareEvent.user_id == user_id,
            ProactiveCareEvent.created_at >= today_start,
        )
        .count()
    )


def _check_glucose_missing(db: Session, *, user_id: int) -> bool:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    count = (
        db.query(GlucoseRecordModel)
        .filter(
            GlucoseRecordModel.user_id == user_id,
            GlucoseRecordModel.created_at >= cutoff,
        )
        .count()
    )
    return count == 0


def _check_consecutive_high(db: Session, *, user_id: int) -> bool:
    records = (
        db.query(GlucoseRecordModel)
        .filter(GlucoseRecordModel.user_id == user_id)
        .order_by(GlucoseRecordModel.id.desc())
        .limit(3)
        .all()
    )
    if len(records) < 3:
        return False
    return all(r.value > 10.0 for r in records)


def _get_missed_medication_plan(db: Session, *, user_id: int) -> MedicationPlan | None:
    active_plans = (
        db.query(MedicationPlan)
        .filter(MedicationPlan.user_id == user_id, MedicationPlan.status == "active")
        .order_by(MedicationPlan.plan_id.asc())
        .all()
    )
    if not active_plans:
        return None

    today_start = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    taken_plan_ids = {
        record.plan_id
        for record in db.query(MedicationTakenRecord)
        .filter(
            MedicationTakenRecord.user_id == user_id,
            MedicationTakenRecord.created_at >= today_start,
            MedicationTakenRecord.status == "taken",
        )
        .all()
    }
    return next((plan for plan in active_plans if plan.plan_id not in taken_plan_ids), None)


def _check_exercise_missing(db: Session, *, user_id: int) -> bool:
    cutoff = (datetime.now() - timedelta(days=3)).isoformat()
    count = (
        db.query(ExerciseRecord)
        .filter(
            ExerciseRecord.user_id == user_id,
            ExerciseRecord.exercise_time >= cutoff,
        )
        .count()
    )
    return count == 0


def _to_dict(event: ProactiveCareEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "user_id": event.user_id,
        "event_type": event.event_type,
        "priority": event.priority,
        "plan_id": event.plan_id,
        "message": event.message,
        "status": event.status,
        "cooldown_until": event.cooldown_until,
        "created_at": event.created_at,
        "delivered_at": event.delivered_at,
    }
