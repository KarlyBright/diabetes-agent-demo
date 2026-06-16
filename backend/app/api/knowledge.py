from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.services.knowledge_service import (
    format_citations,
    ingest_knowledge_document,
    search_knowledge,
)

router = APIRouter()


class KnowledgeDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_name: str = Field(min_length=1, max_length=255)
    source_version: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=500)
    license_note: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/knowledge/documents")
def create_knowledge_document(
    request: KnowledgeDocumentRequest,
    db: Session = Depends(get_db),
):
    try:
        result = ingest_knowledge_document(db, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": result}


@router.get("/knowledge/search")
def get_knowledge_search(
    query: str = Query(min_length=1),
    topic: list[str] | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=10),
    db: Session = Depends(get_db),
):
    snippets = search_knowledge(db, query, topics=topic, limit=limit)
    return {
        "code": 0,
        "data": {
            "snippets": snippets,
            "citations": format_citations(snippets),
        },
    }
