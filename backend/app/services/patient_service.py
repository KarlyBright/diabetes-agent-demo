from sqlalchemy.orm import Session
from app.models.patient_model import PatientProfile
from app.models.glucose_model import GlucoseRecordModel
from datetime import datetime


def get_patient_profile(db: Session, user_id: int) -> dict | None:
    profile = db.query(PatientProfile).filter(PatientProfile.user_id == user_id).first()
    if not profile:
        return None
    
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "age": profile.age,
        "gender": profile.gender,
        "diabetes_type": profile.diabetes_type,
        "disease_duration": profile.disease_duration,
        "height": profile.height,
        "weight": profile.weight,
        "bmi": profile.bmi,
        "medications": profile.medications or [],
        "complications": profile.complications or [],
        "target_fasting_min": profile.target_fasting_min,
        "target_fasting_max": profile.target_fasting_max,
        "target_postmeal_min": profile.target_postmeal_min,
        "target_postmeal_max": profile.target_postmeal_max,
        "profile_completed": profile.profile_completed,
    }


def get_recent_glucose(db: Session, user_id: int, limit: int = 7) -> dict:
    records = (
        db.query(GlucoseRecordModel)
        .filter(GlucoseRecordModel.user_id == user_id)
        .order_by(GlucoseRecordModel.measure_time.desc())
        .limit(limit)
        .all()
    )
    
    fasting_records = []
    postmeal_records = []
    
    for r in records:
        data = {"value": r.value, "measure_time": r.measure_time}
        if r.measure_type == "fasting":
            fasting_records.append(data)
        elif r.measure_type == "post_meal":
            postmeal_records.append(data)
    
    return {
        "fasting": fasting_records,
        "postmeal": postmeal_records,
    }


def save_patient_profile(
    db: Session,
    user_id: int,
    name: str | None = None,
    age: int | None = None,
    gender: str | None = None,
    diabetes_type: str | None = None,
    disease_duration: int | None = None,
    height: float | None = None,
    weight: float | None = None,
    medications: list | None = None,
    complications: list | None = None,
    target_fasting_min: float | None = None,
    target_fasting_max: float | None = None,
    target_postmeal_min: float | None = None,
    target_postmeal_max: float | None = None,
) -> PatientProfile:
    profile = db.query(PatientProfile).filter(PatientProfile.user_id == user_id).first()
    
    now = datetime.now().isoformat()
    
    if not profile:
        profile = PatientProfile(user_id=user_id, created_at=now)
        db.add(profile)
    
    if name is not None:
        profile.name = name
    if age is not None:
        profile.age = age
    if gender is not None:
        profile.gender = gender
    if diabetes_type is not None:
        profile.diabetes_type = diabetes_type
    if disease_duration is not None:
        profile.disease_duration = disease_duration
    if height is not None:
        profile.height = height
        if weight and height:
            profile.bmi = weight / ((height / 100) ** 2)
    if weight is not None:
        profile.weight = weight
        if height and profile.height:
            profile.bmi = weight / ((profile.height / 100) ** 2)
    if medications is not None:
        profile.medications = medications
    if complications is not None:
        profile.complications = complications
    if target_fasting_min is not None:
        profile.target_fasting_min = target_fasting_min
    if target_fasting_max is not None:
        profile.target_fasting_max = target_fasting_max
    if target_postmeal_min is not None:
        profile.target_postmeal_min = target_postmeal_min
    if target_postmeal_max is not None:
        profile.target_postmeal_max = target_postmeal_max
    
    profile.updated_at = now
    
    profile.profile_completed = bool(
        profile.age and profile.diabetes_type and profile.height and profile.weight
    )
    
    db.commit()
    db.refresh(profile)
    return profile
