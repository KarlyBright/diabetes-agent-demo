from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.hypoglycemia_service import (
    detect_hypoglycemia,
    create_hypo_event,
    resolve_hypo_event,
    get_active_hypo_event,
    get_hypo_history,
)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class HypoCheckInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    glucose_value: float
    glucose_record_id: int | None = None


class HypoResolveInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    resolved_value: float


@router.post("/hypo/check")
def check_hypoglycemia(input_data: HypoCheckInput, db: Session = Depends(get_db)):
    detection = detect_hypoglycemia(input_data.glucose_value)
    if not detection["is_hypo"]:
        return {"message": "血糖正常，无低血糖风险", "data": detection}

    event = create_hypo_event(
        db,
        user_id=input_data.user_id,
        initial_value=input_data.glucose_value,
        severity=detection["severity"],
        trigger_glucose_id=input_data.glucose_record_id,
    )
    return {
        "message": "检测到低血糖，已启动 15-15 规则",
        "data": {**event, "protocol": detection["protocol"]},
    }


@router.post("/hypo/resolve")
def resolve_hypoglycemia(input_data: HypoResolveInput, db: Session = Depends(get_db)):
    result = resolve_hypo_event(
        db, user_id=input_data.user_id, resolved_value=input_data.resolved_value
    )
    if result is None:
        raise HTTPException(status_code=404, detail="无活跃的低血糖事件")

    if result["status"] == "resolved":
        return {"message": "血糖已恢复正常", "data": result}
    return {
        "message": "血糖仍偏低，请再次摄入 15g 碳水并等待 15 分钟后复测",
        "data": result,
    }


@router.get("/hypo/active")
def get_active_event(user_id: int = 1, db: Session = Depends(get_db)):
    event = get_active_hypo_event(db, user_id=user_id)
    return {"message": "获取活跃低血糖事件", "data": event}


@router.get("/hypo/history")
def get_history(user_id: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    events = get_hypo_history(db, user_id=user_id, limit=limit)
    return {"message": "获取低血糖事件历史", "data": events}
