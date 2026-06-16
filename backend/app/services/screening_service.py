from __future__ import annotations

from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.screening_model import ScreeningItem, ScreeningRecord

DEFAULT_SCREENING_ITEMS = [
    {"name": "眼底检查（视网膜病变）", "category": "eye", "interval_months": 12},
    {"name": "肾功能（尿微量白蛋白/eGFR）", "category": "kidney", "interval_months": 6},
    {"name": "足部检查（神经病变/血管）", "category": "foot", "interval_months": 6},
    {"name": "神经病变筛查", "category": "nerve", "interval_months": 12},
    {"name": "心血管风险评估", "category": "cardio", "interval_months": 12},
    {"name": "口腔检查", "category": "dental", "interval_months": 6},
]


def init_default_screening_items(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    existing = (
        db.query(ScreeningItem)
        .filter(ScreeningItem.user_id == user_id)
        .count()
    )
    if existing > 0:
        return get_screening_items(db, user_id=user_id)

    items = []
    for item_data in DEFAULT_SCREENING_ITEMS:
        item = ScreeningItem(
            user_id=user_id,
            name=item_data["name"],
            category=item_data["category"],
            interval_months=item_data["interval_months"],
            is_active=1,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        db.add(item)
        items.append(item)

    db.commit()
    return [_item_to_dict(i) for i in items]


def get_screening_items(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    items = (
        db.query(ScreeningItem)
        .filter(ScreeningItem.user_id == user_id, ScreeningItem.is_active == 1)
        .all()
    )
    return [_item_to_dict(i) for i in items]


def create_screening_item(
    db: Session,
    *,
    user_id: int,
    name: str,
    category: str,
    interval_months: int,
) -> dict[str, Any]:
    item = ScreeningItem(
        user_id=user_id,
        name=name,
        category=category,
        interval_months=interval_months,
        is_active=1,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _item_to_dict(item)


def record_screening(
    db: Session,
    *,
    user_id: int,
    screening_item_id: int,
    check_date: str,
    result: str = "normal",
    notes: str | None = None,
) -> dict[str, Any]:
    item = (
        db.query(ScreeningItem)
        .filter(ScreeningItem.id == screening_item_id, ScreeningItem.user_id == user_id)
        .first()
    )
    if item is None:
        raise ValueError("筛查项不存在")

    check_dt = datetime.strptime(check_date, "%Y-%m-%d")
    next_due = check_dt + relativedelta(months=item.interval_months)

    record = ScreeningRecord(
        user_id=user_id,
        screening_item_id=screening_item_id,
        check_date=check_date,
        result=result,
        notes=notes,
        next_due_date=next_due.strftime("%Y-%m-%d"),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _record_to_dict(record)


def get_screening_calendar(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    items = get_screening_items(db, user_id=user_id)
    calendar: list[dict[str, Any]] = []

    for item in items:
        latest_record = (
            db.query(ScreeningRecord)
            .filter(
                ScreeningRecord.user_id == user_id,
                ScreeningRecord.screening_item_id == item["id"],
            )
            .order_by(ScreeningRecord.check_date.desc())
            .first()
        )

        entry: dict[str, Any] = {
            "item": item,
            "last_check": _record_to_dict(latest_record) if latest_record else None,
            "next_due_date": None,
            "status": "never_checked",
        }

        if latest_record:
            entry["next_due_date"] = latest_record.next_due_date
            today = datetime.now().strftime("%Y-%m-%d")
            if latest_record.next_due_date < today:
                entry["status"] = "overdue"
            elif (
                datetime.strptime(latest_record.next_due_date, "%Y-%m-%d")
                - datetime.now()
            ).days <= 14:
                entry["status"] = "due_soon"
            else:
                entry["status"] = "ok"

        calendar.append(entry)

    return calendar


def get_overdue_screenings(db: Session, *, user_id: int) -> list[dict[str, Any]]:
    calendar = get_screening_calendar(db, user_id=user_id)
    return [
        entry for entry in calendar
        if entry["status"] in ("overdue", "never_checked")
    ]


def _item_to_dict(item: ScreeningItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "name": item.name,
        "category": item.category,
        "interval_months": item.interval_months,
        "is_active": item.is_active,
        "created_at": item.created_at,
    }


def _record_to_dict(record: ScreeningRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "screening_item_id": record.screening_item_id,
        "check_date": record.check_date,
        "result": record.result,
        "notes": record.notes,
        "next_due_date": record.next_due_date,
        "created_at": record.created_at,
    }
