import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.agent import router as agent_router
from app.api.agent_memory import router as agent_memory_router
from app.api.device import router as device_router
from app.api.hba1c import router as hba1c_router
from app.api.hypoglycemia import router as hypo_router
from app.api.tir import router as tir_router
from app.api.exercise import router as exercise_router
from app.api.insulin_calc import router as insulin_router
from app.api.screening import router as screening_router
from app.api.proactive_care import router as care_router
from app.api.meal_recognition import router as meal_recognition_router
from app.api.knowledge import router as knowledge_router
from app.api.agent_inquiry import router as agent_inquiry_router
from app.api.cases import router as cases_router
from app.db.database import SessionLocal
from app.db.init_db import init_db
from app.models.glucose_model import GlucoseRecordModel
from app.models.medication_model import (
    MedicationPlan,
    MedicationTakenRecord,
    PendingMedicationPlan,
)
from app.services.adherence_service import analyze_adherence_logic
from app.services.advice_service import generate_daily_advice_logic
from app.services.agent_chat_service import (
    MEAL_TEXT_MAX_LENGTH,
    analyze_diet_with_gl,
    build_meal_analysis_payload,
)
from app.services.glucose_ingestion_service import (
    GlucoseIngestionInput,
    ingest_glucose_reading,
)
from app.services.glucose_summary_service import summarize_glucose_records
from app.services.medication_reminder_service import sync_due_reminders
from app.services.patient_service import get_patient_profile, save_patient_profile
from app.services.reminder_stream_service import reminder_broker


logger = logging.getLogger(__name__)
REMINDER_SCAN_INTERVAL_SECONDS = 30
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


async def reminder_scheduler_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            created_events = sync_due_reminders(db)
            reminder_broker.publish(
                [
                    {
                        "user_id": event.user_id,
                        "reminder_id": event.reminder_id,
                        "role": "assistant",
                        "content": event.message_content,
                        "refresh": ["medication", "adherence", "advice"],
                    }
                    for event in created_events
                ]
            )
        except Exception:
            logger.exception("Failed to sync due medication reminders")
        finally:
            db.close()

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=REMINDER_SCAN_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    stop_event = asyncio.Event()
    reminder_task = asyncio.create_task(reminder_scheduler_loop(stop_event))
    app.state.reminder_stop_event = stop_event
    app.state.reminder_task = reminder_task

    try:
        yield
    finally:
        stop_event.set()
        reminder_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router, prefix="/api/agent")
app.include_router(agent_memory_router, prefix="/api/agent")
app.include_router(agent_inquiry_router, prefix="/api/agent")
app.include_router(device_router, prefix="/api/device")
app.include_router(knowledge_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(hba1c_router, prefix="/api")
app.include_router(hypo_router, prefix="/api")
app.include_router(tir_router, prefix="/api")
app.include_router(exercise_router, prefix="/api")
app.include_router(insulin_router, prefix="/api")
app.include_router(screening_router, prefix="/api")
app.include_router(care_router, prefix="/api")
app.include_router(meal_recognition_router, prefix="/api")


glucose_records = []
meal_records = []
meal_analysis_records = []
pending_medication_plans = []
medication_plans = []
medication_taken_records = []


class GlucoseRecord(BaseModel):
    user_id: int
    value: float
    measure_time: str
    measure_type: Literal["fasting", "post_meal", "before_sleep"]
    source: Literal["manual", "device"]


class MealRecord(BaseModel):
    user_id: int
    meal_text: str = Field(min_length=1, max_length=MEAL_TEXT_MAX_LENGTH)
    meal_time: str
    image_path: Optional[str] = None


class MedicationInstructionInput(BaseModel):
    user_id: int
    instruction_text: str


# 保留兼容旧调用方，但该接口已废弃。
class MedicationPlanCreateInput(BaseModel):
    user_id: int
    drug_name: str
    dosage: str
    remind_time: str
    time_text: Optional[str] = None
    frequency: Optional[str] = "daily"


class MedicationConfirmInput(BaseModel):
    pending_id: int
    confirm: bool


class MedicationTakeInput(BaseModel):
    user_id: int
    plan_id: int
    status: Literal["taken", "missed"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "Diabetes Agent Backend is running!"}


class MealAnalyzeInput(BaseModel):
    user_id: int
    meal_text: str = Field(min_length=1, max_length=MEAL_TEXT_MAX_LENGTH)


class UserProfileInput(BaseModel):
    user_id: int = Field(default=1, ge=1)
    name: str | None = Field(default=None, max_length=100)
    age: int | None = Field(default=None, ge=1, le=120)
    gender: str | None = Field(default=None, max_length=20)
    diabetes_type: str | None = Field(default=None, max_length=50)
    disease_duration: int | None = Field(default=None, ge=0, le=100)
    height: float | None = Field(default=None, gt=0, le=300)
    weight: float | None = Field(default=None, gt=0, le=500)
    medications: list[str] | None = None
    complications: list[str] | None = None
    target_fasting_min: float | None = None
    target_fasting_max: float | None = None
    target_postmeal_min: float | None = None
    target_postmeal_max: float | None = None


def _build_empty_profile(user_id: int) -> dict:
    return {
        "user_id": user_id,
        "name": None,
        "age": None,
        "gender": None,
        "diabetes_type": None,
        "disease_duration": None,
        "height": None,
        "weight": None,
        "bmi": None,
        "medications": [],
        "complications": [],
        "target_fasting_min": None,
        "target_fasting_max": None,
        "target_postmeal_min": None,
        "target_postmeal_max": None,
        "profile_completed": False,
    }


def _build_meal_analysis_record(
    *,
    user_id: int,
    meal_text: str,
    risk_level: str,
    total_gl: float,
    gl_level: str,
    score: float,
    detected_foods: list[dict[str, Any]],
    suggestion: list[str],
    summary: str,
) -> dict[str, Any]:
    return {
        "id": len(meal_analysis_records) + 1,
        "user_id": user_id,
        "meal_text": meal_text,
        "risk_level": risk_level,
        "total_gl": total_gl,
        "gl_level": gl_level,
        "score": score,
        "detected_foods": detected_foods,
        "suggestion": suggestion,
        "summary": summary,
        "created_at": datetime.now().isoformat(),
    }


def _get_latest_meal_analysis(user_id: int) -> dict[str, Any] | None:
    for record in reversed(meal_analysis_records):
        if record.get("user_id") == user_id:
            return record
    return None


@app.get("/api/user/profile")
def get_user_profile(user_id: int = 1, db: Session = Depends(get_db)):
    return {
        "message": "获取用户资料成功",
        "data": get_patient_profile(db, user_id) or _build_empty_profile(user_id),
    }


@app.post("/api/user/profile")
def save_user_profile(input_data: UserProfileInput, db: Session = Depends(get_db)):
    medications = [
        item.strip() for item in (input_data.medications or []) if item.strip()
    ]
    complications = [
        item.strip() for item in (input_data.complications or []) if item.strip()
    ]

    profile = save_patient_profile(
        db,
        user_id=input_data.user_id,
        name=input_data.name.strip() if input_data.name else None,
        age=input_data.age,
        gender=input_data.gender.strip() if input_data.gender else None,
        diabetes_type=(
            input_data.diabetes_type.strip() if input_data.diabetes_type else None
        ),
        disease_duration=input_data.disease_duration,
        height=input_data.height,
        weight=input_data.weight,
        medications=medications,
        complications=complications,
        target_fasting_min=input_data.target_fasting_min,
        target_fasting_max=input_data.target_fasting_max,
        target_postmeal_min=input_data.target_postmeal_min,
        target_postmeal_max=input_data.target_postmeal_max,
    )

    return {
        "message": "用户资料保存成功",
        "data": get_patient_profile(db, profile.user_id)
        or _build_empty_profile(profile.user_id),
    }


@app.post("/api/meal/analyze")
def analyze_meal_endpoint(input_data: MealAnalyzeInput, db: Session = Depends(get_db)):
    result = analyze_diet_with_gl(input_data.meal_text)
    meal_analysis_data = build_meal_analysis_payload(result)
    meal_analysis_records.append(
        _build_meal_analysis_record(
            user_id=input_data.user_id,
            meal_text=input_data.meal_text,
            risk_level=meal_analysis_data["risk_level"],
            total_gl=meal_analysis_data["total_gl"],
            gl_level=meal_analysis_data["gl_level"],
            score=meal_analysis_data["score"],
            detected_foods=meal_analysis_data["detected_foods"],
            suggestion=meal_analysis_data["suggestion"],
            summary=meal_analysis_data["summary"],
        )
    )

    return {"message": "饮食分析成功", "data": meal_analysis_data}


@app.post("/api/glucose")
def create_glucose_record(record: GlucoseRecord, db: Session = Depends(get_db)):
    try:
        result = ingest_glucose_reading(
            db,
            GlucoseIngestionInput(
                user_id=record.user_id,
                value=record.value,
                measure_time=record.measure_time,
                measure_type=record.measure_type,
                source=record.source,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "message": "血糖记录保存成功" if result.created else "血糖记录已存在",
        "data": result.record,
    }


@app.get("/api/glucose/recent")
def get_recent_glucose(user_id: int = 1, db: Session = Depends(get_db)):
    records = (
        db.query(GlucoseRecordModel)
        .filter(GlucoseRecordModel.user_id == user_id)
        .order_by(GlucoseRecordModel.id.desc())
        .limit(5)
        .all()
    )

    data = []
    for r in records:
        data.append(
            {
                "id": r.id,
                "user_id": r.user_id,
                "value": r.value,
                "measure_time": r.measure_time,
                "measure_type": r.measure_type,
                "source": r.source,
                "created_at": r.created_at,
            }
        )

    return {"message": "获取最近血糖记录成功", "data": data}


@app.get("/api/glucose/trend")
def get_glucose_trend(user_id: int = 1, days: int = 7, db: Session = Depends(get_db)):
    bounded_days = max(1, min(days, 30))
    start_time = (datetime.now() - timedelta(days=bounded_days)).isoformat()
    records = (
        db.query(GlucoseRecordModel)
        .filter(
            GlucoseRecordModel.user_id == user_id,
            GlucoseRecordModel.measure_time >= start_time,
        )
        .order_by(GlucoseRecordModel.measure_time.asc())
        .all()
    )

    profile = get_patient_profile(db, user_id) or {}
    target_min = profile.get("target_fasting_min") or 3.9
    target_max = profile.get("target_postmeal_max") or 10.0

    return {
        "message": "获取血糖趋势成功",
        "data": {
            "target": {"min": target_min, "max": target_max},
            "points": [
                {
                    "measure_time": record.measure_time,
                    "value": record.value,
                    "measure_type": record.measure_type,
                }
                for record in records
            ],
        },
    }


@app.get("/api/glucose/summary")
def get_glucose_summary(user_id: int = 1, days: int = 7, db: Session = Depends(get_db)):
    if user_id != 1:
        raise HTTPException(status_code=403, detail="只能访问当前演示用户的血糖总结")

    bounded_days = max(1, min(days, 30))
    start_time = (datetime.now() - timedelta(days=bounded_days)).isoformat()
    records = (
        db.query(GlucoseRecordModel)
        .filter(
            GlucoseRecordModel.user_id == user_id,
            GlucoseRecordModel.measure_time >= start_time,
        )
        .order_by(GlucoseRecordModel.measure_time.asc())
        .all()
    )

    profile = get_patient_profile(db, user_id) or {}
    target_ranges = {
        "fasting": {
            "min": profile.get("target_fasting_min") or 3.9,
            "max": profile.get("target_fasting_max") or 7.0,
        },
        "before_sleep": {
            "min": profile.get("target_fasting_min") or 3.9,
            "max": profile.get("target_fasting_max") or 7.0,
        },
        "post_meal": {
            "min": profile.get("target_postmeal_min") or 3.9,
            "max": profile.get("target_postmeal_max") or 10.0,
        },
    }
    summary = summarize_glucose_records(
        [
            {
                "measure_time": record.measure_time,
                "value": record.value,
                "measure_type": record.measure_type,
            }
            for record in records
        ],
        days=bounded_days,
        target_ranges=target_ranges,
    )

    return {"message": "生成血糖总结成功", "data": summary}


@app.post("/api/meal")
def create_meal_record(record: MealRecord):
    new_record = {
        "id": len(meal_records) + 1,
        "user_id": record.user_id,
        "meal_text": record.meal_text,
        "meal_time": record.meal_time,
        "image_path": record.image_path,
        "created_at": datetime.now().isoformat(),
    }
    meal_records.append(new_record)
    return {"message": "饮食记录保存成功", "data": new_record}


@app.get("/api/advice/daily")
def get_daily_advice(user_id: int = 1, db: Session = Depends(get_db)):
    from app.services.hba1c_service import get_latest_hba1c

    profile = get_patient_profile(db, user_id) or _build_empty_profile(user_id)

    recent_glucose = (
        db.query(GlucoseRecordModel)
        .filter(GlucoseRecordModel.user_id == user_id)
        .order_by(GlucoseRecordModel.id.desc())
        .limit(5)
        .all()
    )

    glucose_data = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "value": r.value,
            "measure_time": r.measure_time,
            "measure_type": r.measure_type,
            "source": r.source,
        }
        for r in recent_glucose
    ]

    latest_hba1c = get_latest_hba1c(db, user_id=user_id)

    result = generate_daily_advice_logic(
        profile=profile,
        recent_glucose=glucose_data,
        recent_meal_analysis=_get_latest_meal_analysis(user_id),
        latest_hba1c=latest_hba1c,
    )

    return {"message": "今日建议生成成功", "data": {"user_id": user_id, **result}}


@app.post("/api/medication/parse")
def parse_medication(input_data: MedicationInstructionInput):
    raise HTTPException(
        status_code=410,
        detail="该接口已废弃，请改用 /api/agent/chat 让 nanobot 通过多轮对话收集药名、剂量和提醒时间。",
    )


@app.post("/api/medication/create_pending")
def create_pending_medication(
    input_data: MedicationPlanCreateInput, db: Session = Depends(get_db)
):
    from app.services.agent_chat_service import build_medication_payload

    try:
        result = build_medication_payload(
            drug_name=input_data.drug_name,
            dosage=input_data.dosage,
            remind_time=input_data.remind_time,
            time_text=input_data.time_text,
            frequency=input_data.frequency,
            user_id=input_data.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pending_record = PendingMedicationPlan(
        user_id=result["user_id"],
        drug_name=result["drug_name"],
        dosage=result["dosage"],
        time_text=result["time_text"],
        remind_time=result["remind_time"],
        frequency=result["frequency"],
        confirm_status=result["confirm_status"],
        is_valid=result["is_valid"],
        missing_fields="",
    )

    db.add(pending_record)
    db.commit()
    db.refresh(pending_record)

    return {
        "message": "待确认用药提醒创建成功，请确认后创建正式提醒",
        "data": {
            "pending_id": pending_record.pending_id,
            "user_id": pending_record.user_id,
            "drug_name": pending_record.drug_name,
            "dosage": pending_record.dosage,
            "time_text": pending_record.time_text,
            "remind_time": pending_record.remind_time,
            "frequency": pending_record.frequency,
            "confirm_status": pending_record.confirm_status,
            "is_valid": pending_record.is_valid,
            "missing_fields": [],
        },
    }


@app.post("/api/medication/confirm")
def confirm_medication(
    input_data: MedicationConfirmInput, db: Session = Depends(get_db)
):
    pending = (
        db.query(PendingMedicationPlan)
        .filter(PendingMedicationPlan.pending_id == input_data.pending_id)
        .first()
    )

    if not pending:
        return {"message": "未找到对应的待确认用药计划", "data": None}

    if not pending.is_valid:
        return {
            "message": "该用药计划信息不完整，无法确认",
            "data": {
                "pending_id": pending.pending_id,
                "user_id": pending.user_id,
                "drug_name": pending.drug_name,
                "dosage": pending.dosage,
                "time_text": pending.time_text,
                "remind_time": pending.remind_time,
                "frequency": pending.frequency,
                "confirm_status": pending.confirm_status,
                "is_valid": pending.is_valid,
                "missing_fields": (
                    pending.missing_fields.split(",") if pending.missing_fields else []
                ),
            },
        }

    if not input_data.confirm:
        pending.confirm_status = "rejected"
        db.commit()

        return {
            "message": "已取消该用药提醒设置",
            "data": {
                "pending_id": pending.pending_id,
                "confirm_status": pending.confirm_status,
            },
        }

    new_plan = MedicationPlan(
        user_id=pending.user_id,
        drug_name=pending.drug_name,
        dosage=pending.dosage,
        time_text=pending.time_text,
        remind_time=pending.remind_time,
        frequency=pending.frequency,
        status="active",
    )

    db.add(new_plan)
    pending.confirm_status = "confirmed"

    db.commit()
    db.refresh(new_plan)

    return {
        "message": "用药提醒创建成功，系统会在到点时自动推送到聊天框",
        "data": {
            "plan_id": new_plan.plan_id,
            "user_id": new_plan.user_id,
            "drug_name": new_plan.drug_name,
            "dosage": new_plan.dosage,
            "time_text": new_plan.time_text,
            "remind_time": new_plan.remind_time,
            "frequency": new_plan.frequency,
            "status": new_plan.status,
        },
    }


@app.get("/api/medication/plans")
def get_medication_plans(user_id: int = 1, db: Session = Depends(get_db)):
    user_plans = (
        db.query(MedicationPlan).filter(MedicationPlan.user_id == user_id).all()
    )

    data = []
    for p in user_plans:
        data.append(
            {
                "plan_id": p.plan_id,
                "user_id": p.user_id,
                "drug_name": p.drug_name,
                "dosage": p.dosage,
                "time_text": p.time_text,
                "remind_time": p.remind_time,
                "frequency": p.frequency,
                "status": p.status,
            }
        )

    return {"message": "获取正式用药计划成功", "data": data}


@app.post("/api/medication/take")
def record_medication_taken(
    input_data: MedicationTakeInput, db: Session = Depends(get_db)
):
    # 找对应计划
    plan = (
        db.query(MedicationPlan)
        .filter(
            MedicationPlan.plan_id == input_data.plan_id,
            MedicationPlan.user_id == input_data.user_id,
        )
        .first()
    )

    if not plan:
        return {"message": "未找到对应的正式用药计划", "data": None}

    new_record = MedicationTakenRecord(
        user_id=input_data.user_id,
        plan_id=input_data.plan_id,
        drug_name=plan.drug_name,
        dosage=plan.dosage,
        time_text=plan.time_text,
        remind_time=plan.remind_time,
        status=input_data.status,
        created_at=datetime.now(),
    )

    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    return {
        "message": "服药状态记录成功",
        "data": {
            "record_id": new_record.record_id,
            "user_id": new_record.user_id,
            "plan_id": new_record.plan_id,
            "drug_name": new_record.drug_name,
            "status": new_record.status,
            "created_at": new_record.created_at,
        },
    }


@app.get("/api/medication/taken")
def get_medication_taken_records(user_id: int = 1, db: Session = Depends(get_db)):
    records = (
        db.query(MedicationTakenRecord)
        .filter(MedicationTakenRecord.user_id == user_id)
        .all()
    )

    data = []
    for r in records:
        data.append(
            {
                "record_id": r.record_id,
                "user_id": r.user_id,
                "plan_id": r.plan_id,
                "drug_name": r.drug_name,
                "dosage": r.dosage,
                "time_text": r.time_text,
                "remind_time": r.remind_time,
                "status": r.status,
                "created_at": r.created_at,
            }
        )

    return {"message": "获取服药记录成功", "data": data}


@app.get("/api/adherence/analyze")
def analyze_adherence(user_id: int = 1, db: Session = Depends(get_db)):
    glucose_records = (
        db.query(GlucoseRecordModel).filter(GlucoseRecordModel.user_id == user_id).all()
    )

    medication_plans = (
        db.query(MedicationPlan)
        .filter(MedicationPlan.user_id == user_id, MedicationPlan.status == "active")
        .all()
    )

    medication_taken_records = (
        db.query(MedicationTakenRecord)
        .filter(MedicationTakenRecord.user_id == user_id)
        .all()
    )

    result = analyze_adherence_logic(
        user_id=user_id,
        glucose_records=[
            {
                "id": r.id,
                "user_id": r.user_id,
                "value": r.value,
                "measure_time": r.measure_time,
                "measure_type": r.measure_type,
                "source": r.source,
            }
            for r in glucose_records
        ],
        meal_analysis_records=[],
        medication_plans=[
            {
                "plan_id": p.plan_id,
                "user_id": p.user_id,
                "drug_name": p.drug_name,
                "dosage": p.dosage,
                "time_text": p.time_text,
                "remind_time": p.remind_time,
                "frequency": p.frequency,
                "status": p.status,
            }
            for p in medication_plans
        ],
        medication_taken_records=[
            {
                "record_id": r.record_id,
                "user_id": r.user_id,
                "plan_id": r.plan_id,
                "drug_name": r.drug_name,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in medication_taken_records
        ],
    )

    return {
        "message": "依从性分析成功",
        "data": {
            "score": result["adherence_score"],
            "risk_level": result["adherence_level"],
            "summary": result["summary"],
        },
    }


@app.get("/api/medication/pending")
def get_pending_medication_plans(user_id: int = 1, db: Session = Depends(get_db)):
    user_pending = (
        db.query(PendingMedicationPlan)
        .filter(PendingMedicationPlan.user_id == user_id)
        .all()
    )

    data = []
    for p in user_pending:
        data.append(
            {
                "pending_id": p.pending_id,
                "user_id": p.user_id,
                "drug_name": p.drug_name,
                "dosage": p.dosage,
                "time_text": p.time_text,
                "remind_time": p.remind_time,
                "frequency": p.frequency,
                "confirm_status": p.confirm_status,
                "is_valid": p.is_valid,
                "missing_fields": (
                    p.missing_fields.split(",") if p.missing_fields else []
                ),
            }
        )

    return {"message": "获取待确认用药计划成功", "data": data}
