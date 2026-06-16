from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.hypoglycemia_model import HypoglycemiaEvent

HYPO_THRESHOLD = 3.9
SEVERE_HYPO_THRESHOLD = 3.0
RECHECK_INTERVAL_MIN = 15
CARB_DOSE_GRAMS = 15

CARB_OPTIONS = [
    {"item": "葡萄糖片", "amount": "3片（约15g）"},
    {"item": "果汁", "amount": "150ml"},
    {"item": "糖果", "amount": "3颗"},
]


def detect_hypoglycemia(glucose_value: float) -> dict[str, Any]:
    if glucose_value > HYPO_THRESHOLD:
        return {"is_hypo": False}

    severity = "severe" if glucose_value <= SEVERE_HYPO_THRESHOLD else "mild"
    return {
        "is_hypo": True,
        "severity": severity,
        "value": glucose_value,
        "protocol": {
            "carb_dose_grams": CARB_DOSE_GRAMS,
            "recheck_minutes": RECHECK_INTERVAL_MIN,
            "carb_options": CARB_OPTIONS,
            "escalate": severity == "severe",
        },
    }


def create_hypo_event(
    db: Session,
    *,
    user_id: int,
    initial_value: float,
    severity: str,
    trigger_glucose_id: int | None = None,
) -> dict[str, Any]:
    event = HypoglycemiaEvent(
        user_id=user_id,
        trigger_glucose_id=trigger_glucose_id,
        initial_value=initial_value,
        severity=severity,
        status="active",
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return _to_dict(event)


def resolve_hypo_event(
    db: Session, *, user_id: int, resolved_value: float
) -> dict[str, Any] | None:
    event = (
        db.query(HypoglycemiaEvent)
        .filter(
            HypoglycemiaEvent.user_id == user_id,
            HypoglycemiaEvent.status == "active",
        )
        .order_by(HypoglycemiaEvent.id.desc())
        .first()
    )
    if event is None:
        return None

    if resolved_value > HYPO_THRESHOLD:
        event.status = "resolved"
        event.resolved_value = resolved_value
        event.resolved_at = datetime.now().isoformat(timespec="seconds")
    else:
        event.resolved_value = resolved_value

    db.commit()
    db.refresh(event)
    return _to_dict(event)


def get_active_hypo_event(db: Session, *, user_id: int) -> dict[str, Any] | None:
    event = (
        db.query(HypoglycemiaEvent)
        .filter(
            HypoglycemiaEvent.user_id == user_id,
            HypoglycemiaEvent.status == "active",
        )
        .order_by(HypoglycemiaEvent.id.desc())
        .first()
    )
    return _to_dict(event) if event else None


def get_hypo_history(
    db: Session, *, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    events = (
        db.query(HypoglycemiaEvent)
        .filter(HypoglycemiaEvent.user_id == user_id)
        .order_by(HypoglycemiaEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [_to_dict(e) for e in events]


def _to_dict(event: HypoglycemiaEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "user_id": event.user_id,
        "trigger_glucose_id": event.trigger_glucose_id,
        "initial_value": event.initial_value,
        "severity": event.severity,
        "status": event.status,
        "resolved_value": event.resolved_value,
        "resolved_at": event.resolved_at,
        "created_at": event.created_at,
    }
