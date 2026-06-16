from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.hba1c_service import (
    create_hba1c_record,
    get_hba1c_history,
    get_latest_hba1c,
    check_hba1c_reminder_needed,
)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class HbA1cInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    value: float = Field(ge=3.0, le=20.0)
    test_date: str
    source: str = "manual"


@router.post("/hba1c")
def create_hba1c(input_data: HbA1cInput, db: Session = Depends(get_db)):
    try:
        record = create_hba1c_record(
            db,
            user_id=input_data.user_id,
            value=input_data.value,
            test_date=input_data.test_date,
            source=input_data.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "HbA1c 记录保存成功", "data": record}


@router.get("/hba1c")
def list_hba1c(user_id: int = 1, limit: int = 10, db: Session = Depends(get_db)):
    records = get_hba1c_history(db, user_id=user_id, limit=limit)
    return {"message": "获取 HbA1c 历史成功", "data": records}


@router.get("/hba1c/latest")
def latest_hba1c(user_id: int = 1, db: Session = Depends(get_db)):
    record = get_latest_hba1c(db, user_id=user_id)
    reminder_needed = check_hba1c_reminder_needed(db, user_id=user_id)
    return {
        "message": "获取最近 HbA1c 成功",
        "data": record,
        "reminder_needed": reminder_needed,
    }
