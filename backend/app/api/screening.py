from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.screening_service import (
    init_default_screening_items,
    get_screening_items,
    create_screening_item,
    record_screening,
    get_screening_calendar,
    get_overdue_screenings,
)

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ScreeningItemInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    name: str
    category: str
    interval_months: int = Field(ge=1, le=60)


class ScreeningRecordInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    screening_item_id: int
    check_date: str
    result: str = "normal"
    notes: str | None = None


@router.get("/screening/items")
def list_screening_items(user_id: int = 1, db: Session = Depends(get_db)):
    items = get_screening_items(db, user_id=user_id)
    if not items:
        items = init_default_screening_items(db, user_id=user_id)
    return {"message": "获取筛查项列表成功", "data": items}


@router.post("/screening/items")
def add_screening_item(input_data: ScreeningItemInput, db: Session = Depends(get_db)):
    item = create_screening_item(
        db,
        user_id=input_data.user_id,
        name=input_data.name,
        category=input_data.category,
        interval_months=input_data.interval_months,
    )
    return {"message": "筛查项添加成功", "data": item}


@router.post("/screening/records")
def add_screening_record(
    input_data: ScreeningRecordInput, db: Session = Depends(get_db)
):
    try:
        record = record_screening(
            db,
            user_id=input_data.user_id,
            screening_item_id=input_data.screening_item_id,
            check_date=input_data.check_date,
            result=input_data.result,
            notes=input_data.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "筛查记录保存成功", "data": record}


@router.get("/screening/calendar")
def screening_calendar(user_id: int = 1, db: Session = Depends(get_db)):
    items = get_screening_items(db, user_id=user_id)
    if not items:
        init_default_screening_items(db, user_id=user_id)
    calendar = get_screening_calendar(db, user_id=user_id)
    return {"message": "获取筛查日历成功", "data": calendar}


@router.get("/screening/overdue")
def overdue_screenings(user_id: int = 1, db: Session = Depends(get_db)):
    items = get_screening_items(db, user_id=user_id)
    if not items:
        init_default_screening_items(db, user_id=user_id)
    overdue = get_overdue_screenings(db, user_id=user_id)
    return {"message": "获取过期筛查项成功", "data": overdue}
