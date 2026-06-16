from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.reverse_inquiry_service import advance_inquiry, start_inquiry

DEMO_USER_ID = 1
router = APIRouter()


class InquiryStartRequest(BaseModel):
    trigger_type: str = Field(min_length=1, max_length=50)
    context: dict = Field(default_factory=dict)


class InquiryReplyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/inquiry/start")
def start_agent_inquiry(request: InquiryStartRequest, db: Session = Depends(get_db)):
    try:
        result = start_inquiry(
            db,
            user_id=DEMO_USER_ID,
            trigger_type=request.trigger_type,
            context=request.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": result}


@router.post("/inquiry/{session_id}/reply")
def reply_agent_inquiry(
    session_id: int,
    request: InquiryReplyRequest,
    db: Session = Depends(get_db),
):
    try:
        result = advance_inquiry(
            db,
            session_id=session_id,
            user_id=DEMO_USER_ID,
            message=request.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": result}
