from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.knowledge_model import KnowledgeChunk, KnowledgeDocument

TOPIC_ALIASES = {
    "hypo": {"低血糖", "hypo"},
    "glucose": {"血糖", "控糖", "glucose"},
    "diet": {"饮食", "主食", "碳水", "diet"},
    "exercise": {"运动", "exercise"},
    "medication": {"用药", "停药", "药", "medication"},
    "screening": {"筛查", "并发症", "screening"},
    "insulin": {"胰岛素", "insulin"},
}

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9-]+|[一-鿿]{1,8}")
STOPWORDS = {"怎么", "怎么办", "什么", "如何", "一下", "现在", "最近", "需要", "可以"}


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: KnowledgeChunk
    document: KnowledgeDocument
    score: float


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_required_text(value: Any, field_name: str) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


def _tokenize(text: str) -> list[str]:
    raw_tokens = [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]
    tokens: list[str] = []
    for token in raw_tokens:
        if token in STOPWORDS:
            continue
        tokens.append(token)
        if len(token) >= 2 and all("一" <= ch <= "鿿" for ch in token):
            for size in (2, 3, 4):
                for index in range(0, len(token) - size + 1):
                    gram = token[index : index + size]
                    if gram not in STOPWORDS:
                        tokens.append(gram)
    return tokens


def _infer_topic(text: str) -> str:
    lowered = text.lower()
    for topic, aliases in TOPIC_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            return topic
    return "general"


def _split_document_into_sections(content: str) -> list[tuple[str, str]]:
    normalized = content.strip()
    if not normalized:
        raise ValueError("content is required")

    parts = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    sections: list[tuple[str, str]] = []
    for part in parts:
        lines = [line.strip() for line in part.splitlines() if line.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if first_line.lower().startswith("topic:"):
            topic = _normalize_required_text(first_line.split(":", 1)[1], "topic")
            section_content = "\n".join(lines[1:]).strip()
        else:
            section_content = "\n".join(lines)
            topic = _infer_topic(section_content)
        if section_content:
            sections.append((topic, section_content))
    return sections or [("general", normalized)]


def ingest_knowledge_document(db: Session, document: dict[str, Any]) -> dict[str, Any]:
    knowledge_document = KnowledgeDocument(
        title=_normalize_required_text(document.get("title"), "title"),
        source_name=_normalize_required_text(document.get("source_name"), "source_name"),
        source_version=_normalize_optional_text(document.get("source_version")),
        source_url=_normalize_optional_text(document.get("source_url")),
        license_note=_normalize_optional_text(document.get("license_note")),
        content=_normalize_required_text(document.get("content"), "content"),
        created_at=_now_iso(),
    )
    db.add(knowledge_document)
    db.flush()

    sections = _split_document_into_sections(knowledge_document.content)
    chunks: list[KnowledgeChunk] = []
    for index, (topic, section_content) in enumerate(sections):
        chunk = KnowledgeChunk(
            document_id=knowledge_document.id,
            chunk_index=index,
            topic=topic,
            content=section_content,
            embedding_json=None,
            created_at=_now_iso(),
        )
        db.add(chunk)
        chunks.append(chunk)

    db.commit()
    db.refresh(knowledge_document)
    for chunk in chunks:
        db.refresh(chunk)

    return {
        "id": knowledge_document.id,
        "title": knowledge_document.title,
        "source_name": knowledge_document.source_name,
        "source_version": knowledge_document.source_version,
        "chunk_count": len(chunks),
    }


def _score_chunk(query_tokens: list[str], chunk: KnowledgeChunk, document: KnowledgeDocument) -> float:
    haystack = f"{document.title} {document.source_name} {chunk.topic} {chunk.content}".lower()
    chunk_tokens = _tokenize(haystack)
    if not chunk_tokens:
        return 0.0

    token_count = len(chunk_tokens)
    score = 0.0
    for token in query_tokens:
        term_frequency = chunk_tokens.count(token)
        if term_frequency == 0:
            continue
        score += (1 + math.log(term_frequency)) / (1 + math.log(token_count))
        if token in chunk.topic.lower():
            score += 1.5
    return score


def search_knowledge(
    db: Session,
    query: str,
    topics: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalized_query = _normalize_required_text(query, "query")
    query_tokens = _tokenize(normalized_query)
    if not query_tokens:
        return []

    normalized_topics = {topic.strip() for topic in (topics or []) if topic.strip()}
    records_query = (
        db.query(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .order_by(KnowledgeChunk.id.asc())
    )
    if normalized_topics:
        records_query = records_query.filter(KnowledgeChunk.topic.in_(normalized_topics))

    candidate_limit = max(limit * 20, 50)
    records = records_query.limit(candidate_limit).all()

    scored_chunks: list[ScoredChunk] = []
    for chunk, document in records:
        score = _score_chunk(query_tokens, chunk, document)
        if score <= 0:
            continue
        scored_chunks.append(ScoredChunk(chunk=chunk, document=document, score=score))

    scored_chunks.sort(key=lambda item: (-item.score, item.chunk.id))
    return [
        {
            "id": item.chunk.id,
            "document_id": item.document.id,
            "chunk_index": item.chunk.chunk_index,
            "topic": item.chunk.topic,
            "content": item.chunk.content,
            "source_name": item.document.source_name,
            "source_version": item.document.source_version,
            "source_url": item.document.source_url,
            "title": item.document.title,
            "score": round(item.score, 4),
        }
        for item in scored_chunks[:limit]
    ]


def build_cited_context(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return ""
    lines = []
    for index, chunk in enumerate(chunks, start=1):
        lines.append(
            "[#{index}] source_name={source_name}; source_version={source_version}; topic={topic}; content={content}".format(
                index=index,
                source_name=chunk.get("source_name", ""),
                source_version=chunk.get("source_version") or "",
                topic=chunk.get("topic", ""),
                content=chunk.get("content", ""),
            )
        )
    return "\n".join(lines)


def format_citations(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str | None, str]] = set()
    for chunk in chunks:
        key = (
            str(chunk.get("source_name") or ""),
            chunk.get("source_version"),
            str(chunk.get("topic") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        citations.append(
            {
                "source_name": key[0],
                "source_version": key[1],
                "topic": key[2],
                "source_url": chunk.get("source_url"),
            }
        )
    return citations
