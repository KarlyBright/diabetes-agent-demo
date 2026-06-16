from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.proactive_care_service import (
    scan_and_generate_care_events,
    get_pending_care_events,
    dismiss_care_event,
    deliver_care_event,
)
from app.services.reverse_inquiry_service import start_inquiry

router = APIRouter()
DEMO_USER_ID = 1
CARE_EVENT_INQUIRY_TRIGGERS = {
    "consecutive_high": "consecutive_high",
    "medication_missed": "medication_missed",
    "hypo_followup": "hypoglycemia_followup",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/care/pending")
def pending_care(db: Session = Depends(get_db)):
    scan_and_generate_care_events(db, user_id=DEMO_USER_ID)
    events = get_pending_care_events(db, user_id=DEMO_USER_ID)
    return {"code": 0, "message": "获取主动关怀消息成功", "data": events}


@router.post("/care/dismiss/{event_id}")
def dismiss_event(event_id: int, db: Session = Depends(get_db)):
    success = dismiss_care_event(db, event_id=event_id, user_id=DEMO_USER_ID)
    if not success:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {
        "code": 0,
        "message": "已忽略该关怀消息",
        "data": {"id": event_id, "status": "dismissed"},
    }


@router.post("/care/deliver/{event_id}")
def deliver_event(event_id: int, db: Session = Depends(get_db)):
    success = deliver_care_event(db, event_id=event_id, user_id=DEMO_USER_ID)
    if not success:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {
        "code": 0,
        "message": "已推送该关怀消息",
        "data": {"id": event_id, "status": "delivered"},
    }


@router.post("/care/{event_id}/start-inquiry")
def start_care_inquiry(event_id: int, db: Session = Depends(get_db)):
    events = get_pending_care_events(db, user_id=DEMO_USER_ID)
    target_event = next((event for event in events if event["id"] == event_id), None)
    if target_event is None:
        raise HTTPException(status_code=404, detail="事件不存在")

    trigger_type = CARE_EVENT_INQUIRY_TRIGGERS.get(target_event["event_type"])
    if trigger_type is None:
        raise HTTPException(status_code=400, detail="不支持该关怀事件启动反向问诊")

    context = {"care_event_id": event_id, "care_event_type": target_event["event_type"]}
    if isinstance(target_event.get("plan_id"), int):
        context = {**context, "plan_id": target_event["plan_id"]}
    inquiry = start_inquiry(
        db,
        user_id=DEMO_USER_ID,
        trigger_type=trigger_type,
        context=context,
    )
    return {"code": 0, "message": "已启动反向问诊", "data": inquiry}
