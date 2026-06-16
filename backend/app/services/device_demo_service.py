from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.glp_parser_service import (
    normalize_glp_measurement,
    parse_glucose_context_payload,
    parse_glucose_measurement_payload,
)
from app.services.glucose_ingestion_service import GlucoseIngestionInput, ingest_glucose_reading

DEMO_DEVICE_NAME = "GLP 演示血糖仪"
DEMO_PROTOCOL = {
    "profile": "GLP",
    "service_uuid": "0x1808",
    "characteristics": ["0x2A18", "0x2A34", "0x2A51", "0x2A52"],
}
DEMO_FEATURES = [
    {
        "uuid": "0x2A18",
        "name": "Glucose Measurement",
        "properties": ["notify"],
    },
    {
        "uuid": "0x2A34",
        "name": "Glucose Measurement Context",
        "properties": ["notify"],
    },
    {
        "uuid": "0x2A51",
        "name": "Glucose Feature",
        "properties": ["read"],
    },
    {
        "uuid": "0x2A52",
        "name": "Record Access Control Point",
        "properties": ["write", "indicate"],
    },
]
DEMO_GLP_FIXTURES = (
    {"measurement_hex": "170100ea07040b081e00000048c011", "context_hex": "02010002"},
    {"measurement_hex": "070200ea07040b160f0000004cc011", "context_hex": None},
)


@dataclass(frozen=True)
class DeviceDemoState:
    connected: bool
    state: str
    last_sync_at: str | None
    last_sync_count: int
    last_error: str | None


_device_demo_state = DeviceDemoState(
    connected=False,
    state="disconnected",
    last_sync_at=None,
    last_sync_count=0,
    last_error=None,
)


def reset_device_demo_state() -> None:
    global _device_demo_state
    _device_demo_state = DeviceDemoState(
        connected=False,
        state="disconnected",
        last_sync_at=None,
        last_sync_count=0,
        last_error=None,
    )


def _update_state(**changes: Any) -> DeviceDemoState:
    global _device_demo_state
    _device_demo_state = replace(_device_demo_state, **changes)
    return _device_demo_state


def get_device_demo_status() -> dict[str, Any]:
    return {
        "connected": _device_demo_state.connected,
        "state": _device_demo_state.state,
        "device_name": DEMO_DEVICE_NAME,
        "supports_context": True,
        "supports_mock_sync": True,
        "protocol": DEMO_PROTOCOL,
        "features": DEMO_FEATURES,
        "last_sync_at": _device_demo_state.last_sync_at,
        "last_sync_count": _device_demo_state.last_sync_count,
        "last_error": _device_demo_state.last_error,
    }


def connect_demo_device() -> dict[str, Any]:
    _update_state(connected=True, state="connected", last_error=None)
    return get_device_demo_status()


def disconnect_demo_device() -> dict[str, Any]:
    _update_state(connected=False, state="disconnected")
    return get_device_demo_status()


def ingest_demo_glp_fixtures(db: Session, *, user_id: int) -> dict[str, Any]:
    if not _device_demo_state.connected:
        raise ValueError("请先连接 GLP 演示血糖仪")

    created_records: list[dict[str, Any]] = []
    existing_records: list[dict[str, Any]] = []
    prepared_records: list[GlucoseIngestionInput] = []
    for fixture in DEMO_GLP_FIXTURES:
        measurement = parse_glucose_measurement_payload(bytes.fromhex(fixture["measurement_hex"]))
        context = (
            parse_glucose_context_payload(bytes.fromhex(fixture["context_hex"]))
            if fixture["context_hex"]
            else None
        )
        normalized = normalize_glp_measurement(measurement, context=context, user_id=user_id)
        prepared_records.append(
            GlucoseIngestionInput(
                user_id=normalized.user_id,
                value=normalized.value,
                measure_time=normalized.measure_time,
                measure_type=normalized.measure_type,
                source=normalized.source,
            )
        )

    try:
        for prepared_record in prepared_records:
            result = ingest_glucose_reading(db, prepared_record, auto_commit=False)
            if result.created:
                created_records.append(result.record)
            else:
                existing_records.append(result.record)
        db.commit()
    except Exception:
        db.rollback()
        raise

    synced_at = datetime.now().isoformat(timespec="seconds")
    _update_state(
        state="synced",
        last_sync_at=synced_at,
        last_sync_count=len(created_records),
        last_error=None,
    )
    return {
        "device_name": DEMO_DEVICE_NAME,
        "protocol": DEMO_PROTOCOL,
        "created_count": len(created_records),
        "existing_count": len(existing_records),
        "total_count": len(DEMO_GLP_FIXTURES),
        "last_sync_at": synced_at,
        "records": created_records + existing_records,
        "refresh": ["glucose", "adherence", "advice"],
        "message": f"已从 BLE GATT + GLP 演示血糖仪同步 {len(created_records)} 条记录",
    }


def ingest_glp_payload_batch(
    db: Session,
    *,
    user_id: int,
    payloads: list[dict[str, str | None]],
) -> dict[str, Any]:
    prepared_records: list[GlucoseIngestionInput] = []
    for payload in payloads:
        measurement = parse_glucose_measurement_payload(bytes.fromhex(payload["measurement_hex"]))
        context_hex = payload.get("context_hex")
        context = parse_glucose_context_payload(bytes.fromhex(context_hex)) if context_hex else None
        normalized = normalize_glp_measurement(measurement, context=context, user_id=user_id)
        prepared_records.append(
            GlucoseIngestionInput(
                user_id=normalized.user_id,
                value=normalized.value,
                measure_time=normalized.measure_time,
                measure_type=normalized.measure_type,
                source=normalized.source,
            )
        )

    created_records: list[dict[str, Any]] = []
    existing_records: list[dict[str, Any]] = []
    try:
        for prepared_record in prepared_records:
            result = ingest_glucose_reading(db, prepared_record, auto_commit=False)
            if result.created:
                created_records.append(result.record)
            else:
                existing_records.append(result.record)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "created_count": len(created_records),
        "existing_count": len(existing_records),
        "total_count": len(payloads),
        "records": created_records + existing_records,
        "refresh": ["glucose", "adherence", "advice"],
    }
