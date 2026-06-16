from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.case_reference_model import CaseReference
from app.models.exercise_model import ExerciseRecord
from app.models.glucose_model import GlucoseRecordModel
from app.models.medication_model import MedicationPlan, MedicationTakenRecord
from app.models.patient_model import PatientProfile


@dataclass(frozen=True, slots=True)
class ScoredCaseReference:
    case: CaseReference
    score: int
    match_reasons: tuple[str, ...]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_diabetes_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "2" in text or "t2" in text or "二" in text:
        return "t2dm"
    if "1" in text or "t1" in text or "一" in text:
        return "t1dm"
    return "unknown"


def _age_band(age: int | None) -> str:
    if age is None:
        return "unknown"
    if age >= 65:
        return "elderly"
    if age >= 45:
        return "middle_aged"
    return "young"


def _bmi_category(bmi: float | None) -> str:
    if bmi is None:
        return "unknown"
    if bmi >= 28:
        return "obese"
    if bmi >= 24:
        return "overweight"
    if bmi < 18.5:
        return "underweight"
    return "normal"


def _medication_tags(values: list[Any]) -> list[str]:
    tags: list[str] = []
    for value in values:
        text = str(value).lower()
        if "二甲" in text or "metformin" in text:
            tags.append("metformin")
        if "胰岛素" in text or "insulin" in text:
            tags.append("insulin")
        if "sglt" in text:
            tags.append("sglt2")
    return sorted(set(tags))


def _glucose_patterns(records: list[GlucoseRecordModel]) -> list[str]:
    patterns: set[str] = set()
    fasting_high = [item for item in records if item.measure_type == "fasting" and item.value >= 7.0]
    post_meal_high = [item for item in records if item.measure_type == "post_meal" and item.value >= 10.0]
    lows = [item for item in records if item.value < 3.9]
    if len(fasting_high) >= 2:
        patterns.add("fasting_high")
    if len(post_meal_high) >= 2:
        patterns.add("post_meal_high")
    if lows:
        patterns.add("hypoglycemia")
    return sorted(patterns)


def _behavior_tags(db: Session, *, user_id: int) -> list[str]:
    tags: set[str] = set()
    exercise_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    exercise_count = (
        db.query(ExerciseRecord)
        .filter(ExerciseRecord.user_id == user_id, ExerciseRecord.exercise_time >= exercise_cutoff)
        .count()
    )
    if exercise_count == 0:
        tags.add("sedentary")

    today_start = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
    missed_count = (
        db.query(MedicationTakenRecord)
        .filter(
            MedicationTakenRecord.user_id == user_id,
            MedicationTakenRecord.created_at >= today_start,
            MedicationTakenRecord.status == "missed",
        )
        .count()
    )
    if missed_count:
        tags.add("missed_medication")
    return sorted(tags)


def build_user_case_features(db: Session, user_id: int) -> dict[str, Any]:
    profile = db.query(PatientProfile).filter(PatientProfile.user_id == user_id).first()
    records = (
        db.query(GlucoseRecordModel)
        .filter(GlucoseRecordModel.user_id == user_id)
        .order_by(GlucoseRecordModel.measure_time.desc())
        .limit(14)
        .all()
    )
    plans = (
        db.query(MedicationPlan)
        .filter(MedicationPlan.user_id == user_id, MedicationPlan.status == "active")
        .all()
    )
    profile_medications = list(profile.medications or []) if profile is not None else []
    plan_medications = [plan.drug_name for plan in plans]
    return {
        "diabetes_type": _normalize_diabetes_type(profile.diabetes_type if profile else None),
        "age_band": _age_band(profile.age if profile else None),
        "bmi_category": _bmi_category(profile.bmi if profile else None),
        "glucose_patterns": _glucose_patterns(records),
        "medication_tags": _medication_tags([*profile_medications, *plan_medications]),
        "behavior_tags": _behavior_tags(db, user_id=user_id),
    }


def seed_demo_case_references(db: Session) -> int:
    existing_count = db.query(CaseReference).count()
    if existing_count:
        return existing_count
    cases = (
        CaseReference(
            case_type="t2dm",
            tags=json.dumps(["middle_aged", "overweight", "post_meal_high", "metformin", "sedentary"], ensure_ascii=False),
            profile_summary="匿名T2DM中年超重用户",
            pattern_summary="晚餐后血糖连续偏高，近期运动不足。",
            intervention_summary="常见改善动作包括晚餐主食减量约1/3，餐后步行15-20分钟，并持续记录餐后血糖。",
            outcome_summary="demo案例中餐后血糖波动有所下降。",
            evidence_level="demo",
            created_at=_now_iso(),
        ),
        CaseReference(
            case_type="t2dm",
            tags=json.dumps(["elderly", "fasting_high", "metformin", "missed_medication"], ensure_ascii=False),
            profile_summary="匿名T2DM老年用户",
            pattern_summary="空腹血糖偏高并伴随漏服记录。",
            intervention_summary="常见改善动作包括核对用药提醒、固定早餐后服药打卡，并联系医生评估持续高糖。",
            outcome_summary="demo案例中依从性改善后空腹波动减少。",
            evidence_level="demo",
            created_at=_now_iso(),
        ),
        CaseReference(
            case_type="t2dm",
            tags=json.dumps(["hypoglycemia", "insulin", "middle_aged"], ensure_ascii=False),
            profile_summary="匿名胰岛素使用用户",
            pattern_summary="运动或进餐延迟后出现低血糖。",
            intervention_summary="常见改善动作包括随身携带快速糖，记录低血糖诱因，并和医生确认胰岛素方案。",
            outcome_summary="demo案例中低血糖复发减少。",
            evidence_level="demo",
            created_at=_now_iso(),
        ),
    )
    db.add_all(cases)
    db.commit()
    return len(cases)


def _case_tags(case: CaseReference) -> set[str]:
    try:
        parsed = json.loads(case.tags)
    except json.JSONDecodeError:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {str(item) for item in parsed}


def _score_case(case: CaseReference, features: dict[str, Any]) -> ScoredCaseReference:
    score = 0
    reasons: list[str] = []
    tags = _case_tags(case)
    if case.case_type == features.get("diabetes_type"):
        score += 20
        reasons.append("糖尿病类型相似")
    for key, label, points in (
        ("age_band", "年龄段相似", 10),
        ("bmi_category", "BMI分类相似", 10),
    ):
        value = features.get(key)
        if value and value in tags:
            score += points
            reasons.append(label)
    for pattern in features.get("glucose_patterns", []):
        if pattern in tags:
            score += 30
            reasons.append("血糖模式相似")
            break
    for medication in features.get("medication_tags", []):
        if medication in tags:
            score += 15
            reasons.append("用药类型相似")
            break
    for behavior in features.get("behavior_tags", []):
        if behavior in tags:
            score += 15
            reasons.append("行为标签相似")
            break
    return ScoredCaseReference(case=case, score=score, match_reasons=tuple(dict.fromkeys(reasons)))


def search_similar_cases(db: Session, features: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    safe_limit = min(max(int(limit), 1), 10)
    cases = db.query(CaseReference).order_by(CaseReference.id.asc()).all()
    scored = [_score_case(case, features) for case in cases]
    ranked = [item for item in scored if item.score > 0]
    ranked.sort(key=lambda item: (-item.score, item.case.id))
    return [
        {
            "id": item.case.id,
            "case_type": item.case.case_type,
            "score": item.score,
            "match_reasons": list(item.match_reasons),
            "profile_summary": item.case.profile_summary,
            "pattern_summary": item.case.pattern_summary,
            "intervention_summary": item.case.intervention_summary,
            "outcome_summary": item.case.outcome_summary,
            "evidence_level": item.case.evidence_level,
        }
        for item in ranked[:safe_limit]
    ]


def format_case_insight(cases: list[dict[str, Any]]) -> str:
    if not cases:
        return "暂未找到足够相似的匿名案例。仅供参考，不替代医生建议。"
    lines = [f"我找到 {len(cases)} 个相似模式的匿名案例："]
    for case in cases:
        reasons = "、".join(case.get("match_reasons", [])) or "存在部分相似因素"
        lines.append(
            "- 共同点：{reasons}；模式：{pattern}；常见改善动作：{intervention}".format(
                reasons=reasons,
                pattern=case.get("pattern_summary", ""),
                intervention=case.get("intervention_summary", ""),
            )
        )
    lines.append("提醒：这些只是匿名 demo 群体经验，仅供参考，不替代医生建议。")
    return "\n".join(lines)
