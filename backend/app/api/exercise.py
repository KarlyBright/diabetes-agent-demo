from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.exercise_service import (
    create_exercise_record,
    get_exercise_history,
    get_exercise_summary,
    VALID_EXERCISE_TYPES,
    VALID_INTENSITIES,
)
from app.services.patient_service import get_patient_profile

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ExerciseInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    exercise_type: str
    intensity: str
    duration_minutes: int = Field(ge=1, le=600)
    exercise_time: str | None = None
    notes: str | None = None


@router.post("/exercise")
def create_exercise(input_data: ExerciseInput, db: Session = Depends(get_db)):
    profile = get_patient_profile(db, input_data.user_id)
    weight_kg = profile.get("weight", 70.0) if profile else 70.0

    try:
        record = create_exercise_record(
            db,
            user_id=input_data.user_id,
            exercise_type=input_data.exercise_type,
            intensity=input_data.intensity,
            duration_minutes=input_data.duration_minutes,
            weight_kg=weight_kg or 70.0,
            exercise_time=input_data.exercise_time,
            notes=input_data.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "运动记录保存成功", "data": record}


@router.get("/exercise")
def list_exercise(user_id: int = 1, days: int = 7, db: Session = Depends(get_db)):
    records = get_exercise_history(db, user_id=user_id, days=days)
    return {"message": "获取运动记录成功", "data": records}


@router.get("/exercise/summary")
def exercise_summary(user_id: int = 1, days: int = 7, db: Session = Depends(get_db)):
    summary = get_exercise_summary(db, user_id=user_id, days=days)
    return {"message": "获取运动统计成功", "data": summary}
