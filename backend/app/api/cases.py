from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.case_reference_service import (
    build_user_case_features,
    format_case_insight,
    search_similar_cases,
)

DEMO_USER_ID = 1
router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/cases/similar")
def get_similar_cases(
    limit: int = Query(default=3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    features = build_user_case_features(db, DEMO_USER_ID)
    cases = search_similar_cases(db, features, limit=limit)
    return {
        "code": 0,
        "data": {
            "cases": cases,
            "insight": format_case_insight(cases),
        },
    }
