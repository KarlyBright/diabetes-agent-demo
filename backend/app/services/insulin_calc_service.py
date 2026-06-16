from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.insulin_calc_model import InsulinCalculation

SAFETY_RULES = {
    "max_single_dose": 20.0,
    "min_glucose_for_correction": 4.5,
    "iob_duration_hours": 4,
    "disclaimer_required": True,
    "disclaimer_text": "⚠️ 此计算仅供参考，不构成医疗建议。请遵医嘱调整剂量。",
}


def calculate_insulin_dose(
    *,
    current_glucose: float,
    carbs_intake: float,
    icr: float,
    isf: float,
    target_glucose: float = 5.5,
    iob: float = 0.0,
    max_bolus: float = 15.0,
) -> dict[str, Any]:
    if icr <= 0 or isf <= 0:
        raise ValueError("ICR 和 ISF 必须大于 0")

    correction_dose = 0.0
    if current_glucose >= SAFETY_RULES["min_glucose_for_correction"]:
        correction_dose = max(0.0, (current_glucose - target_glucose) / isf)

    carb_dose = carbs_intake / icr

    raw_dose = correction_dose + carb_dose - iob
    suggested_dose = max(0.0, raw_dose)

    effective_max = min(max_bolus, SAFETY_RULES["max_single_dose"])
    capped = suggested_dose > effective_max
    suggested_dose = min(suggested_dose, effective_max)

    suggested_dose = round(round(suggested_dose * 2) / 2, 1)

    return {
        "suggested_dose": suggested_dose,
        "correction_dose": round(correction_dose, 2),
        "carb_dose": round(carb_dose, 2),
        "iob_deducted": round(iob, 2),
        "capped": capped,
        "calculation_detail": {
            "current_glucose": current_glucose,
            "target_glucose": target_glucose,
            "carbs_intake": carbs_intake,
            "icr": icr,
            "isf": isf,
            "iob": iob,
            "max_bolus": effective_max,
        },
        "disclaimer": SAFETY_RULES["disclaimer_text"],
    }


def save_insulin_calculation(
    db: Session,
    *,
    user_id: int,
    current_glucose: float,
    carbs_intake: float,
    iob: float,
    suggested_dose: float,
    correction_dose: float,
    carb_dose: float,
    accepted: bool = False,
) -> dict[str, Any]:
    record = InsulinCalculation(
        user_id=user_id,
        current_glucose=current_glucose,
        carbs_intake=carbs_intake,
        iob=iob,
        suggested_dose=suggested_dose,
        correction_dose=correction_dose,
        carb_dose=carb_dose,
        accepted=1 if accepted else 0,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "user_id": record.user_id,
        "suggested_dose": record.suggested_dose,
        "created_at": record.created_at,
    }
