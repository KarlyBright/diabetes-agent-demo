from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.agent_memory_service import (
    archive_memory,
    extract_and_persist_memory,
    extract_memory_fact,
    extracted_fact_to_dict,
    list_memories,
    memory_to_dict,
    upsert_memory,
)

DEMO_USER_ID = 1
router = APIRouter()


class MemoryUpsertRequest(BaseModel):
    user_id: int | None = Field(default=None, ge=1)
    category: str
    key: str
    value: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source: str = "chat"


class MemoryExtractRequest(BaseModel):
    user_id: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=500)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/memory")
def get_memories(db: Session = Depends(get_db)):
    memories = [memory_to_dict(item) for item in list_memories(db, user_id=DEMO_USER_ID)]
    return {"code": 0, "data": memories}


@router.post("/memory")
def save_memory(request: MemoryUpsertRequest, db: Session = Depends(get_db)):
    try:
        memory = upsert_memory(
            db,
            user_id=DEMO_USER_ID,
            category=request.category,
            key=request.key,
            value=request.value,
            confidence=request.confidence,
            source=request.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": memory_to_dict(memory)}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    archived = archive_memory(db, user_id=DEMO_USER_ID, memory_id=memory_id)
    if not archived:
        raise HTTPException(status_code=404, detail="memory not found")
    return {"code": 0, "data": {"archived": True}}


@router.post("/memory/extract")
def extract_memory(request: MemoryExtractRequest, db: Session = Depends(get_db)):
    extracted = extract_memory_fact(request.message)
    if extracted is None:
        raise HTTPException(status_code=400, detail="no memory fact extracted")

    memory = extract_and_persist_memory(db, user_id=DEMO_USER_ID, message=request.message)
    if memory is not None:
        return {"code": 0, "data": memory}
    return {"code": 0, "data": extracted_fact_to_dict(extracted)}
