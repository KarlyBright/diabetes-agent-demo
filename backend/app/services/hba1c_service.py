from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.hba1c_model import HbA1cRecord

HBAIC_MIN = 3.0
HBAIC_MAX = 20.0
REMINDER_THRESHOLD_DAYS = 90


def create_hba1c_record(
    db: Session, *, user_id: int, value: float, test_date: str, source: str = "manual"
) -> dict[str, Any]:
    if not (HBAIC_MIN <= value <= HBAIC_MAX):
        raise ValueError(f"HbA1c 值必须在 {HBAIC_MIN}% 到 {HBAIC_MAX}% 之间")

    existing = (
        db.query(HbA1cRecord)
        .filter(HbA1cRecord.user_id == user_id, HbA1cRecord.test_date == test_date)
        .first()
    )
    if existing:
        raise ValueError("该日期已有记录，不可重复录入")

    record = HbA1cRecord(
        user_id=user_id,
        value=value,
        test_date=test_date,
        source=source,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _to_dict(record)


def get_hba1c_history(
    db: Session, *, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    records = (
        db.query(HbA1cRecord)
        .filter(HbA1cRecord.user_id == user_id)
        .order_by(HbA1cRecord.test_date.desc())
        .limit(limit)
        .all()
    )
    return [_to_dict(r) for r in records]


def get_latest_hba1c(db: Session, *, user_id: int) -> dict[str, Any] | None:
    record = (
        db.query(HbA1cRecord)
        .filter(HbA1cRecord.user_id == user_id)
        .order_by(HbA1cRecord.test_date.desc())
        .first()
    )
    return _to_dict(record) if record else None


def check_hba1c_reminder_needed(db: Session, *, user_id: int) -> bool:
    latest = get_latest_hba1c(db, user_id=user_id)
    if latest is None:
        return True
    last_date = datetime.strptime(latest["test_date"], "%Y-%m-%d")
    return (datetime.now() - last_date) > timedelta(days=REMINDER_THRESHOLD_DAYS)


def _to_dict(record: HbA1cRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "value": record.value,
        "test_date": record.test_date,
        "source": record.source,
        "created_at": record.created_at,
    }
