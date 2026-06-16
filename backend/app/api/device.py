from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.device_demo_service import (
    connect_demo_device,
    disconnect_demo_device,
    get_device_demo_status,
    ingest_demo_glp_fixtures,
    ingest_glp_payload_batch,
)

DEMO_USER_ID = 1
router = APIRouter()


class DeviceReadingPayload(BaseModel):
    measurement_hex: Annotated[str, Field(min_length=2)]
    context_hex: str | None = None


class DeviceReadingsRequest(BaseModel):
    readings: Annotated[list[DeviceReadingPayload], Field(min_length=1, max_length=20)]
    user_id: int = DEMO_USER_ID


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/status")
def get_device_status() -> dict[str, object]:
    return {"code": 0, "data": get_device_demo_status()}


@router.post("/connect")
def connect_device() -> dict[str, object]:
    return {"code": 0, "data": connect_demo_device()}


@router.post("/disconnect")
def disconnect_device() -> dict[str, object]:
    return {"code": 0, "data": disconnect_demo_device()}


@router.post("/mock-sync")
def mock_sync_device(db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        result = ingest_demo_glp_fixtures(db, user_id=DEMO_USER_ID)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": result}


@router.post("/readings")
def ingest_device_readings(req: DeviceReadingsRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        result = ingest_glp_payload_batch(
            db,
            user_id=req.user_id,
            payloads=[payload.model_dump() for payload in req.readings],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": result}
