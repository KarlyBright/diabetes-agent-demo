from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.exercise_model import ExerciseRecord

VALID_EXERCISE_TYPES = ("walking", "running", "swimming", "cycling", "yoga", "strength")
VALID_INTENSITIES = ("low", "medium", "high")

EXERCISE_TYPE_CN = {
    "walking": "步行",
    "running": "跑步",
    "swimming": "游泳",
    "cycling": "骑行",
    "yoga": "瑜伽",
    "strength": "力量训练",
}

MET_VALUES = {
    "walking": {"low": 2.5, "medium": 3.5, "high": 5.0},
    "running": {"low": 6.0, "medium": 8.0, "high": 11.0},
    "swimming": {"low": 4.0, "medium": 6.0, "high": 8.0},
    "cycling": {"low": 4.0, "medium": 6.5, "high": 10.0},
    "yoga": {"low": 2.5, "medium": 3.0, "high": 4.0},
    "strength": {"low": 3.0, "medium": 5.0, "high": 8.0},
}


def estimate_calories(
    *, weight_kg: float, exercise_type: str, intensity: str, duration_min: int
) -> float:
    met = MET_VALUES[exercise_type][intensity]
    return round(met * weight_kg * (duration_min / 60), 1)


def create_exercise_record(
    db: Session,
    *,
    user_id: int,
    exercise_type: str,
    intensity: str,
    duration_minutes: int,
    weight_kg: float = 70.0,
    exercise_time: str | None = None,
    notes: str | None = None,
    pre_glucose_id: int | None = None,
    post_glucose_id: int | None = None,
) -> dict[str, Any]:
    if exercise_type not in VALID_EXERCISE_TYPES:
        raise ValueError(f"exercise_type 必须是 {VALID_EXERCISE_TYPES} 之一")
    if intensity not in VALID_INTENSITIES:
        raise ValueError(f"intensity 必须是 {VALID_INTENSITIES} 之一")

    calories = estimate_calories(
        weight_kg=weight_kg,
        exercise_type=exercise_type,
        intensity=intensity,
        duration_min=duration_minutes,
    )

    record = ExerciseRecord(
        user_id=user_id,
        exercise_type=exercise_type,
        intensity=intensity,
        duration_minutes=duration_minutes,
        calories_burned=calories,
        exercise_time=exercise_time or datetime.now().isoformat(timespec="seconds"),
        notes=notes,
        pre_glucose_id=pre_glucose_id,
        post_glucose_id=post_glucose_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _to_dict(record)


def get_exercise_history(
    db: Session, *, user_id: int, days: int = 7
) -> list[dict[str, Any]]:
    start_time = (datetime.now() - timedelta(days=days)).isoformat()
    records = (
        db.query(ExerciseRecord)
        .filter(
            ExerciseRecord.user_id == user_id,
            ExerciseRecord.exercise_time >= start_time,
        )
        .order_by(ExerciseRecord.exercise_time.desc())
        .all()
    )
    return [_to_dict(r) for r in records]


def get_exercise_summary(
    db: Session, *, user_id: int, days: int = 7
) -> dict[str, Any]:
    records = get_exercise_history(db, user_id=user_id, days=days)
    total_minutes = sum(r["duration_minutes"] for r in records)
    total_calories = sum(r["calories_burned"] or 0 for r in records)
    return {
        "session_count": len(records),
        "total_minutes": total_minutes,
        "total_calories": round(total_calories, 1),
        "days": days,
    }


def _to_dict(record: ExerciseRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "exercise_type": record.exercise_type,
        "intensity": record.intensity,
        "duration_minutes": record.duration_minutes,
        "calories_burned": record.calories_burned,
        "pre_glucose_id": record.pre_glucose_id,
        "post_glucose_id": record.post_glucose_id,
        "exercise_time": record.exercise_time,
        "notes": record.notes,
        "created_at": record.created_at,
    }
