from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.tir_service import calculate_tir, get_tir_trend

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/tir")
def get_tir(user_id: int = 1, days: int = 14, db: Session = Depends(get_db)):
    result = calculate_tir(db, user_id=user_id, days=days)
    return {"message": "TIR 计算完成", "data": result}


@router.get("/tir/trend")
def get_tir_trend_endpoint(
    user_id: int = 1, weeks: int = 4, db: Session = Depends(get_db)
):
    trend = get_tir_trend(db, user_id=user_id, weeks=weeks)
    return {"message": "TIR 趋势获取成功", "data": trend}
