from sqlalchemy import inspect, text

from app.db.database import Base, engine
from app.models.glucose_model import GlucoseRecordModel
from app.models.medication_model import MedicationPlan, MedicationTakenRecord, PendingMedicationPlan
from app.models.patient_model import PatientProfile
from app.models.hba1c_model import HbA1cRecord  # noqa: F401
from app.models.hypoglycemia_model import HypoglycemiaEvent  # noqa: F401
from app.models.exercise_model import ExerciseRecord  # noqa: F401
from app.models.insulin_calc_model import InsulinCalculation  # noqa: F401
from app.models.screening_model import ScreeningItem, ScreeningRecord  # noqa: F401
from app.models.proactive_care_model import ProactiveCareEvent  # noqa: F401
from app.models.safety_model import SafetyIntervention  # noqa: F401
from app.models.agent_memory_model import AgentMemory  # noqa: F401
from app.models.knowledge_model import KnowledgeChunk, KnowledgeDocument  # noqa: F401
from app.models.questionnaire_model import AgentQuestionSession  # noqa: F401
from app.models.case_reference_model import CaseReference  # noqa: F401
from app.services.case_reference_service import seed_demo_case_references


def _add_column_if_missing(table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing_columns:
        return

    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def init_db():
    Base.metadata.create_all(bind=engine)
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        seed_demo_case_references(db)
    finally:
        db.close()
    _add_column_if_missing("medication_plan", "last_reminded_at", "last_reminded_at DATETIME")
    _add_column_if_missing("medication_reminder_event", "scheduled_at", "scheduled_at DATETIME")
    _add_column_if_missing("proactive_care_events", "plan_id", "plan_id INTEGER")
    _add_column_if_missing("patient_profiles", "icr", "icr REAL")
    _add_column_if_missing("patient_profiles", "isf", "isf REAL")
    _add_column_if_missing("patient_profiles", "target_glucose", "target_glucose REAL DEFAULT 5.5")
    _add_column_if_missing("patient_profiles", "max_bolus", "max_bolus REAL DEFAULT 15.0")
