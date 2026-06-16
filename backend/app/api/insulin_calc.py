from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.insulin_calc_service import calculate_insulin_dose, save_insulin_calculation
from app.services.patient_service import get_patient_profile

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class InsulinCalcInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    current_glucose: float = Field(ge=1.0, le=33.3)
    carbs_intake: float = Field(ge=0)
    iob: float = Field(default=0, ge=0)


@router.post("/insulin/calculate")
def calculate_insulin(input_data: InsulinCalcInput, db: Session = Depends(get_db)):
    profile = get_patient_profile(db, input_data.user_id)
    if not profile:
        raise HTTPException(status_code=400, detail="请先完善个人档案")

    icr = profile.get("icr")
    isf = profile.get("isf")
    if not icr or not isf:
        raise HTTPException(
            status_code=400,
            detail="请先在个人档案中设置碳水比(ICR)和敏感系数(ISF)",
        )

    target_glucose = profile.get("target_glucose", 5.5)
    max_bolus = profile.get("max_bolus", 15.0)

    result = calculate_insulin_dose(
        current_glucose=input_data.current_glucose,
        carbs_intake=input_data.carbs_intake,
        icr=icr,
        isf=isf,
        target_glucose=target_glucose,
        iob=input_data.iob,
        max_bolus=max_bolus,
    )

    save_insulin_calculation(
        db,
        user_id=input_data.user_id,
        current_glucose=input_data.current_glucose,
        carbs_intake=input_data.carbs_intake,
        iob=input_data.iob,
        suggested_dose=result["suggested_dose"],
        correction_dose=result["correction_dose"],
        carb_dose=result["carb_dose"],
    )

    return {"message": "胰岛素剂量计算完成", "data": result}
