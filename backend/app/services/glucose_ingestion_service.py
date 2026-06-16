from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.glucose_model import GlucoseRecordModel

ALLOWED_MEASURE_TYPES = frozenset({"fasting", "post_meal", "before_sleep"})
ALLOWED_SOURCES = frozenset({"manual", "device", "agent"})


@dataclass(frozen=True)
class GlucoseIngestionInput:
    user_id: int
    value: float
    measure_time: str
    measure_type: str
    source: str


@dataclass(frozen=True)
class GlucoseIngestionResult:
    created: bool
    record: dict[str, Any]


def normalize_measure_time(measure_time: str) -> str:
    try:
        normalized = datetime.fromisoformat(measure_time)
    except ValueError as exc:
        raise ValueError("measure_time must be a valid ISO datetime") from exc
    return normalized.isoformat(timespec="seconds")


def validate_glucose_input(payload: GlucoseIngestionInput) -> GlucoseIngestionInput:
    if payload.user_id <= 0:
        raise ValueError("user_id must be positive")
    if payload.measure_type not in ALLOWED_MEASURE_TYPES:
        raise ValueError(f"measure_type must be one of {sorted(ALLOWED_MEASURE_TYPES)}")
    if payload.source not in ALLOWED_SOURCES:
        raise ValueError(f"source must be one of {sorted(ALLOWED_SOURCES)}")
    if payload.value <= 0:
        raise ValueError("value must be positive")
    if payload.value > 100:
        raise ValueError("value must be <= 100 mmol/L")

    return GlucoseIngestionInput(
        user_id=payload.user_id,
        value=round(float(payload.value), 1),
        measure_time=normalize_measure_time(payload.measure_time),
        measure_type=payload.measure_type,
        source=payload.source,
    )


def serialize_glucose_record(record: GlucoseRecordModel) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "value": record.value,
        "measure_time": record.measure_time,
        "measure_type": record.measure_type,
        "source": record.source,
        "created_at": record.created_at,
    }


def find_existing_record(db: Session, payload: GlucoseIngestionInput) -> GlucoseRecordModel | None:
    return (
        db.query(GlucoseRecordModel)
        .filter(
            GlucoseRecordModel.user_id == payload.user_id,
            GlucoseRecordModel.value == payload.value,
            GlucoseRecordModel.measure_time == payload.measure_time,
            GlucoseRecordModel.measure_type == payload.measure_type,
            GlucoseRecordModel.source == payload.source,
        )
        .order_by(GlucoseRecordModel.id.asc())
        .first()
    )


def ingest_glucose_reading(
    db: Session,
    payload: GlucoseIngestionInput,
    *,
    auto_commit: bool = True,
) -> GlucoseIngestionResult:
    normalized_payload = validate_glucose_input(payload)
    existing_record = find_existing_record(db, normalized_payload)
    if existing_record is not None:
        return GlucoseIngestionResult(created=False, record=serialize_glucose_record(existing_record))

    created_at = datetime.now().isoformat(timespec="seconds")
    new_record = GlucoseRecordModel(
        user_id=normalized_payload.user_id,
        value=normalized_payload.value,
        measure_time=normalized_payload.measure_time,
        measure_type=normalized_payload.measure_type,
        source=normalized_payload.source,
        created_at=created_at,
    )
    db.add(new_record)
    if auto_commit:
        db.commit()
        db.refresh(new_record)
    else:
        db.flush()
    return GlucoseIngestionResult(created=True, record=serialize_glucose_record(new_record))
