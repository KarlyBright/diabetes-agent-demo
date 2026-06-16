from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.glucose_model import GlucoseRecordModel

TARGET_LOW = 3.9
TARGET_HIGH = 10.0
MIN_READINGS = 10


def calculate_tir(
    db: Session, *, user_id: int, days: int = 14
) -> dict[str, Any]:
    start_time = (datetime.now() - timedelta(days=days)).isoformat()
    records = (
        db.query(GlucoseRecordModel)
        .filter(
            GlucoseRecordModel.user_id == user_id,
            GlucoseRecordModel.measure_time >= start_time,
        )
        .all()
    )

    total = len(records)
    if total < MIN_READINGS:
        return {"status": "insufficient_data", "count": total}

    in_range = sum(1 for r in records if TARGET_LOW <= r.value <= TARGET_HIGH)
    below_range = sum(1 for r in records if r.value < TARGET_LOW)
    above_range = sum(1 for r in records if r.value > TARGET_HIGH)

    tir_pct = round(in_range / total * 100, 1)

    return {
        "status": "ok",
        "tir": tir_pct,
        "tbr": round(below_range / total * 100, 1),
        "tar": round(above_range / total * 100, 1),
        "total_readings": total,
        "days": days,
        "assessment": _assess_tir(tir_pct),
    }


def get_tir_trend(
    db: Session, *, user_id: int, weeks: int = 4
) -> list[dict[str, Any]]:
    trend: list[dict[str, Any]] = []
    now = datetime.now()

    for week_offset in range(weeks):
        week_end = now - timedelta(weeks=week_offset)
        week_start = week_end - timedelta(days=7)

        records = (
            db.query(GlucoseRecordModel)
            .filter(
                GlucoseRecordModel.user_id == user_id,
                GlucoseRecordModel.measure_time >= week_start.isoformat(),
                GlucoseRecordModel.measure_time < week_end.isoformat(),
            )
            .all()
        )

        total = len(records)
        if total < MIN_READINGS:
            continue

        in_range = sum(1 for r in records if TARGET_LOW <= r.value <= TARGET_HIGH)
        trend.append({
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": week_end.strftime("%Y-%m-%d"),
            "tir": round(in_range / total * 100, 1),
            "readings": total,
        })

    return list(reversed(trend))


def _assess_tir(tir_pct: float) -> str:
    if tir_pct >= 70:
        return "excellent"
    if tir_pct >= 50:
        return "good"
    if tir_pct >= 30:
        return "needs_improvement"
    return "poor"
