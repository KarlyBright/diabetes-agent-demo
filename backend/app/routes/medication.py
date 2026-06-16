from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.medication_model import MedicationPlanModel, MedicationRecordModel

router = APIRouter(prefix="/medication", tags=["medication"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TakeMedicationRequest(BaseModel):
    plan_id: int


@router.get("/today")
def get_today_medications(db: Session = Depends(get_db)):
    plans = db.query(MedicationPlanModel).filter(
        MedicationPlanModel.user_id == 1,
        MedicationPlanModel.is_active == True
    ).all()

    result = []
    today_str = str(date.today())

    for plan in plans:
        record = db.query(MedicationRecordModel).filter(
            MedicationRecordModel.plan_id == plan.id,
            MedicationRecordModel.created_at >= datetime.strptime(today_str, "%Y-%m-%d")
        ).first()

        status = "taken" if record else "pending"

        result.append({
            "id": plan.id,
            "drug_name": plan.drug_name,
            "dose": plan.dose,
            "frequency": plan.frequency,
            "remind_time": plan.remind_time,
            "meal_relation": plan.meal_relation,
            "status": status
        })

    return {
        "code": 0,
        "data": result
    }


@router.post("/take")
def take_medication(req: TakeMedicationRequest, db: Session = Depends(get_db)):
    plan = db.query(MedicationPlanModel).filter(
        MedicationPlanModel.id == req.plan_id,
        MedicationPlanModel.user_id == 1
    ).first()

    if not plan:
        raise HTTPException(status_code=404, detail="未找到药物计划")

    existing = db.query(MedicationRecordModel).filter(
        MedicationRecordModel.plan_id == plan.id,
        MedicationRecordModel.created_at >= datetime.combine(date.today(), datetime.min.time())
    ).first()

    if existing:
        return {
            "code": 0,
            "message": "今天这次服药已经记录过了"
        }

    record = MedicationRecordModel(
        user_id=1,
        plan_id=plan.id,
        drug_name=plan.drug_name,
        scheduled_time=plan.remind_time,
        taken_time=datetime.now(),
        status="taken"
    )

    db.add(record)
    db.commit()

    return {
        "code": 0,
        "message": "已记录服药"
    }