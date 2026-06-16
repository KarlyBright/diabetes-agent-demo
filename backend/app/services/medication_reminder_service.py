from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from croniter import croniter
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.medication_model import (
    MedicationPlan,
    MedicationReminderEvent,
    MedicationTakenRecord,
)

logger = logging.getLogger(__name__)
Clock = Callable[[], datetime]
DAILY_FREQUENCY_ALIASES = frozenset({"daily", "每天", "每日"})
WEEKDAY_ALIASES = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}
INTERVAL_PATTERN = re.compile(r"^interval:(?P<amount>\d+)(?P<unit>[hd])$")
CRON_PREFIX = "cron:"


def get_now() -> datetime:
    return datetime.now()


def get_today_date_text(now: datetime) -> str:
    return now.date().isoformat()


def format_reminder_message(plan: MedicationPlan) -> str:
    return (
        f"💊 用药提醒\n\n"
        f"请记得按时服用 {plan.drug_name} {plan.dosage}。\n"
        f"时间：{plan.time_text}（{plan.remind_time}）"
    )


def parse_time_to_minutes(remind_time: str) -> int:
    hour_text, minute_text = remind_time.split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("remind_time must be within 00:00-23:59")
    return hour * 60 + minute


def is_plan_due(plan: MedicationPlan, now: datetime) -> bool:
    return parse_time_to_minutes(plan.remind_time) <= now.hour * 60 + now.minute


def is_daily_frequency(raw_frequency: str | None) -> bool:
    normalized_frequency = (raw_frequency or "").strip().lower()
    return normalized_frequency in DAILY_FREQUENCY_ALIASES


def normalize_frequency(raw_frequency: str | None) -> str:
    normalized_frequency = (raw_frequency or "daily").strip()
    if not normalized_frequency:
        return "daily"

    lowered_frequency = normalized_frequency.lower()
    if lowered_frequency in DAILY_FREQUENCY_ALIASES:
        return "daily"
    if lowered_frequency.startswith("weekly:"):
        return f"weekly:{normalized_frequency.split(':', maxsplit=1)[1].upper()}"
    if lowered_frequency.startswith("interval:"):
        return lowered_frequency
    if lowered_frequency.startswith(CRON_PREFIX):
        return f"cron:{normalized_frequency.split(':', maxsplit=1)[1].strip()}"

    logger.warning(
        "Unsupported medication frequency, falling back to daily",
        extra={"frequency": raw_frequency},
    )
    return "daily"


def build_occurrence(now: datetime, hour: int, minute: int) -> datetime:
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_weekly_days(frequency: str) -> set[int]:
    _, raw_days = frequency.split(":", maxsplit=1)
    days = {
        WEEKDAY_ALIASES[day.strip().upper()]
        for day in raw_days.split(",")
        if day.strip().upper() in WEEKDAY_ALIASES
    }
    if not days:
        logger.warning(
            "Invalid weekly frequency, falling back to daily",
            extra={"frequency": frequency},
        )
    return days


def should_skip_for_taken_today(plan: MedicationPlan) -> bool:
    frequency = normalize_frequency(plan.frequency)
    return frequency == "daily" or frequency.startswith("weekly:")


def get_due_occurrence(plan: MedicationPlan, now: datetime) -> datetime | None:
    frequency = normalize_frequency(plan.frequency)

    if frequency.startswith(CRON_PREFIX):
        expression = frequency.split(":", maxsplit=1)[1].strip()
        try:
            occurrence = (
                now.replace(second=0, microsecond=0)
                if croniter.match(expression, now)
                else croniter(expression, now).get_prev(datetime)
            )
        except (KeyError, ValueError):
            logger.warning(
                "Invalid cron frequency, falling back to remind_time",
                extra={"plan_id": plan.plan_id, "frequency": plan.frequency},
            )
            occurrence = None
        if occurrence is None or occurrence.date() != now.date():
            return None
        return occurrence

    hour_minute = parse_time_to_minutes(plan.remind_time)
    hour, minute = divmod(hour_minute, 60)
    base_occurrence = build_occurrence(now, hour, minute)

    if frequency == "daily":
        return base_occurrence if base_occurrence <= now else None

    if frequency.startswith("weekly:"):
        weekly_days = parse_weekly_days(frequency)
        if not weekly_days:
            return base_occurrence if base_occurrence <= now else None
        if now.weekday() in weekly_days and base_occurrence <= now:
            return base_occurrence
        return None

    interval_match = INTERVAL_PATTERN.match(frequency)
    if interval_match:
        amount = int(interval_match.group("amount"))
        unit = interval_match.group("unit")
        interval = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        if amount <= 0:
            return None
        if plan.last_reminded_at is not None:
            elapsed_intervals = int(
                (now - plan.last_reminded_at).total_seconds()
                // interval.total_seconds()
            )
            if elapsed_intervals <= 0:
                return None
            next_occurrence = plan.last_reminded_at + elapsed_intervals * interval
            return next_occurrence if next_occurrence <= now else None
        if base_occurrence > now:
            return None
        elapsed_intervals = int(
            (now - base_occurrence).total_seconds() // interval.total_seconds()
        )
        return base_occurrence + elapsed_intervals * interval

    return base_occurrence if base_occurrence <= now else None


def has_taken_today(
    db: Session,
    *,
    user_id: int,
    plan_id: int,
    today_text: str,
) -> bool:
    record = (
        db.query(MedicationTakenRecord)
        .filter(
            MedicationTakenRecord.user_id == user_id,
            MedicationTakenRecord.plan_id == plan_id,
            MedicationTakenRecord.status == "taken",
            func.date(MedicationTakenRecord.created_at) == today_text,
        )
        .first()
    )
    return record is not None


def reminder_exists(
    db: Session,
    *,
    user_id: int,
    plan_id: int,
    reminder_date: str,
    scheduled_for: str,
    scheduled_at: datetime | None = None,
) -> bool:
    query = db.query(MedicationReminderEvent).filter(
        MedicationReminderEvent.user_id == user_id,
        MedicationReminderEvent.plan_id == plan_id,
    )
    if scheduled_at is not None:
        query = query.filter(MedicationReminderEvent.scheduled_at == scheduled_at)
    else:
        query = query.filter(
            MedicationReminderEvent.reminder_date == reminder_date,
            MedicationReminderEvent.scheduled_for == scheduled_for,
        )

    return query.first() is not None


def get_active_plan(
    db: Session, *, user_id: int, plan_id: int
) -> MedicationPlan | None:
    return (
        db.query(MedicationPlan)
        .filter(
            MedicationPlan.user_id == user_id,
            MedicationPlan.plan_id == plan_id,
            MedicationPlan.status == "active",
        )
        .first()
    )


def is_plan_still_active(db: Session, *, user_id: int, plan_id: int) -> bool:
    return get_active_plan(db, user_id=user_id, plan_id=plan_id) is not None


def create_reminder_event_if_needed(
    db: Session,
    *,
    plan: MedicationPlan,
    reminder_date: str,
    scheduled_at: datetime,
) -> MedicationReminderEvent | None:
    scheduled_for = scheduled_at.strftime("%H:%M")
    reminder_event = MedicationReminderEvent(
        user_id=plan.user_id,
        plan_id=plan.plan_id,
        reminder_date=reminder_date,
        scheduled_for=scheduled_for,
        scheduled_at=scheduled_at,
        message_content=format_reminder_message(plan),
        delivery_status="pending",
    )
    db.add(reminder_event)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None

    db.refresh(reminder_event)
    return reminder_event


def sync_due_reminders(
    db: Session,
    user_id: int | None = None,
    *,
    now_provider: Clock = get_now,
) -> list[MedicationReminderEvent]:
    now = now_provider()
    today_text = get_today_date_text(now)

    query = db.query(MedicationPlan).filter(MedicationPlan.status == "active")
    if user_id is not None:
        query = query.filter(MedicationPlan.user_id == user_id)

    created_events: list[MedicationReminderEvent] = []

    for plan in query.all():
        try:
            scheduled_at = get_due_occurrence(plan, now)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping medication plan with invalid remind_time",
                extra={"plan_id": plan.plan_id, "remind_time": plan.remind_time},
            )
            continue

        if scheduled_at is None:
            continue

        reminder_date = scheduled_at.date().isoformat()
        scheduled_for = scheduled_at.strftime("%H:%M")

        if should_skip_for_taken_today(plan) and has_taken_today(
            db, user_id=plan.user_id, plan_id=plan.plan_id, today_text=today_text
        ):
            continue
        if reminder_exists(
            db,
            user_id=plan.user_id,
            plan_id=plan.plan_id,
            reminder_date=reminder_date,
            scheduled_for=scheduled_for,
            scheduled_at=scheduled_at,
        ):
            continue

        reminder_event = create_reminder_event_if_needed(
            db,
            plan=plan,
            reminder_date=reminder_date,
            scheduled_at=scheduled_at,
        )
        if reminder_event is not None:
            plan.last_reminded_at = scheduled_at
            db.commit()
            created_events.append(reminder_event)

    return created_events


def mark_reminders_delivered(
    db: Session, reminder_ids: list[int], *, now_provider: Clock = get_now
) -> list[int]:
    if not reminder_ids:
        return []

    delivered_at = now_provider()
    (
        db.query(MedicationReminderEvent)
        .filter(MedicationReminderEvent.reminder_id.in_(reminder_ids))
        .update(
            {
                MedicationReminderEvent.delivery_status: "delivered",
                MedicationReminderEvent.delivered_at: delivered_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return reminder_ids


def mark_reminders_skipped(db: Session, reminder_ids: list[int]) -> list[int]:
    if not reminder_ids:
        return []

    (
        db.query(MedicationReminderEvent)
        .filter(MedicationReminderEvent.reminder_id.in_(reminder_ids))
        .update(
            {MedicationReminderEvent.delivery_status: "skipped"},
            synchronize_session=False,
        )
    )
    db.commit()
    return reminder_ids


def get_pending_chat_reminders(
    db: Session,
    *,
    user_id: int,
    now_provider: Clock = get_now,
) -> list[dict[str, Any]]:
    now = now_provider()

    events = (
        db.query(MedicationReminderEvent)
        .filter(
            MedicationReminderEvent.user_id == user_id,
            MedicationReminderEvent.delivery_status == "pending",
            or_(
                MedicationReminderEvent.scheduled_at.is_(None),
                MedicationReminderEvent.scheduled_at <= now,
            ),
        )
        .order_by(MedicationReminderEvent.reminder_id.asc())
        .all()
    )

    payload: list[dict[str, Any]] = []
    skipped_ids: list[int] = []

    for event in events:
        plan = get_active_plan(db, user_id=user_id, plan_id=event.plan_id)
        if plan is None:
            skipped_ids.append(event.reminder_id)
            continue
        if should_skip_for_taken_today(plan) and has_taken_today(
            db, user_id=user_id, plan_id=event.plan_id, today_text=event.reminder_date
        ):
            skipped_ids.append(event.reminder_id)
            continue

        payload.append(
            {
                "reminder_id": event.reminder_id,
                "role": "assistant",
                "content": event.message_content,
                "refresh": ["medication", "adherence", "advice"],
            }
        )

    mark_reminders_skipped(db, skipped_ids)
    return payload


def acknowledge_chat_reminders(
    db: Session,
    *,
    user_id: int,
    reminder_ids: list[int],
    now_provider: Clock = get_now,
) -> dict[str, list[int]]:
    if not reminder_ids:
        return {"acknowledged_ids": [], "skipped_ids": [], "ignored_ids": []}

    now = now_provider()
    valid_events = (
        db.query(MedicationReminderEvent)
        .filter(
            MedicationReminderEvent.user_id == user_id,
            MedicationReminderEvent.reminder_id.in_(reminder_ids),
            MedicationReminderEvent.delivery_status == "pending",
            or_(
                MedicationReminderEvent.scheduled_at.is_(None),
                MedicationReminderEvent.scheduled_at <= now,
            ),
        )
        .all()
    )

    found_ids = [event.reminder_id for event in valid_events]
    delivered_ids: list[int] = []
    skipped_ids: list[int] = []
    for event in valid_events:
        plan = get_active_plan(db, user_id=user_id, plan_id=event.plan_id)
        if plan is None:
            skipped_ids.append(event.reminder_id)
            continue
        if should_skip_for_taken_today(plan) and has_taken_today(
            db, user_id=user_id, plan_id=event.plan_id, today_text=event.reminder_date
        ):
            skipped_ids.append(event.reminder_id)
            continue
        delivered_ids.append(event.reminder_id)

    acknowledged_ids = mark_reminders_delivered(
        db, delivered_ids, now_provider=now_provider
    )
    skipped_result = mark_reminders_skipped(db, skipped_ids)
    ignored_ids = [
        reminder_id for reminder_id in reminder_ids if reminder_id not in found_ids
    ]
    return {
        "acknowledged_ids": acknowledged_ids,
        "skipped_ids": skipped_result,
        "ignored_ids": ignored_ids,
    }
