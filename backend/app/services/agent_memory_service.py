from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent_memory_model import AgentMemory

VALID_MEMORY_CATEGORIES = {
    "preference",
    "allergy",
    "lifestyle",
    "goal",
    "care_context",
    "communication",
}


@dataclass(frozen=True, slots=True)
class ExtractedMemoryFact:
    category: str
    key: str
    value: str
    confidence: float = 0.9
    source: str = "system_extract"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate_category(category: str) -> str:
    normalized = category.strip()
    if normalized not in VALID_MEMORY_CATEGORIES:
        raise ValueError("unsupported memory category")
    return normalized


def _normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("memory text cannot be blank")
    return normalized


def memory_to_dict(memory: AgentMemory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "user_id": memory.user_id,
        "category": memory.category,
        "key": memory.key,
        "value": memory.value,
        "confidence": memory.confidence,
        "source": memory.source,
        "status": memory.status,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def upsert_memory(
    db: Session,
    *,
    user_id: int,
    category: str,
    key: str,
    value: str,
    confidence: float = 0.8,
    source: str = "chat",
) -> AgentMemory:
    normalized_category = _validate_category(category)
    normalized_key = _normalize_text(key)
    normalized_value = _normalize_text(value)
    now = _now_iso()

    memory = (
        db.query(AgentMemory)
        .filter(
            AgentMemory.user_id == user_id,
            AgentMemory.category == normalized_category,
            AgentMemory.key == normalized_key,
        )
        .first()
    )
    if memory is None:
        memory = AgentMemory(
            user_id=user_id,
            category=normalized_category,
            key=normalized_key,
            value=normalized_value,
            confidence=confidence,
            source=source,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(memory)
    else:
        memory.value = normalized_value
        memory.confidence = confidence
        memory.source = source
        memory.status = "active"
        memory.updated_at = now

    db.commit()
    db.refresh(memory)
    return memory


def list_memories(
    db: Session,
    *,
    user_id: int,
    category: str | None = None,
    include_archived: bool = False,
) -> list[AgentMemory]:
    query = db.query(AgentMemory).filter(AgentMemory.user_id == user_id)
    if category is not None:
        query = query.filter(AgentMemory.category == _validate_category(category))
    if not include_archived:
        query = query.filter(AgentMemory.status == "active")
    return list(query.order_by(AgentMemory.id.asc()).all())


def archive_memory(db: Session, *, user_id: int, memory_id: int) -> bool:
    memory = (
        db.query(AgentMemory)
        .filter(AgentMemory.id == memory_id, AgentMemory.user_id == user_id)
        .first()
    )
    if memory is None:
        return False

    memory.status = "archived"
    memory.updated_at = _now_iso()
    db.commit()
    return True


def archive_memory_by_key(
    db: Session,
    *,
    user_id: int,
    key: str,
    category: str | None = None,
) -> bool:
    query = db.query(AgentMemory).filter(
        AgentMemory.user_id == user_id,
        AgentMemory.key == _normalize_text(key),
        AgentMemory.status == "active",
    )
    if category is not None:
        query = query.filter(AgentMemory.category == _validate_category(category))

    memory = query.order_by(AgentMemory.id.desc()).first()
    if memory is None:
        return False

    memory.status = "archived"
    memory.updated_at = _now_iso()
    db.commit()
    return True


def extract_memory_fact(message: str) -> ExtractedMemoryFact | None:
    normalized = message.strip()
    if not normalized.startswith("记住我"):
        return None

    body = normalized.removeprefix("记住我").strip()
    if not body:
        return None

    if body.endswith("过敏") and len(body) > 2:
        return ExtractedMemoryFact(category="allergy", key=body[:-2], value="过敏")

    if body.startswith("晚上不喝") and len(body) > len("晚上不喝"):
        drink = body.removeprefix("晚上不喝")
        return ExtractedMemoryFact(category="preference", key=drink, value="晚上不喝")

    return None


def extract_and_persist_memory(
    db: Session,
    *,
    user_id: int,
    message: str,
) -> dict[str, Any] | None:
    extracted = extract_memory_fact(message)
    if extracted is None:
        return None

    memory = upsert_memory(
        db,
        user_id=user_id,
        category=extracted.category,
        key=extracted.key,
        value=extracted.value,
        confidence=extracted.confidence,
        source=extracted.source,
    )
    return memory_to_dict(memory)


def extracted_fact_to_dict(fact: ExtractedMemoryFact) -> dict[str, Any]:
    return asdict(fact)
