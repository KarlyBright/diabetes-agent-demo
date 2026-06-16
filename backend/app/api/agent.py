import json
from typing import AsyncIterator, cast

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, PositiveInt, ValidationError
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..db.database import SessionLocal
from ..services.agent_chat_service import (
    ALLOWED_CHAT_IMAGE_MIME_TYPES,
    CHAT_IMAGE_MAX_BYTES,
    CHAT_IMAGE_MAX_COUNT,
    MEAL_TEXT_MAX_LENGTH,
    AgentChatService,
    ChatImageInput,
    detect_chat_image_mime,
    format_agent_response,
)
from ..services.medical_safety_service import (
    build_safety_response,
    classify_medical_risk,
    log_safety_intervention,
    should_block_agent_run,
)
from ..services.medication_reminder_service import (
    acknowledge_chat_reminders,
    get_pending_chat_reminders,
)
from ..services.reminder_stream_service import (
    format_sse_event,
    reminder_broker,
    stream_queue_events,
)


DEMO_USER_ID = 1
router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class ReminderAcknowledgeRequest(BaseModel):
    reminder_ids: list[PositiveInt] = Field(..., min_length=1, max_length=20)


async def _read_limited_upload(file: UploadFile) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > CHAT_IMAGE_MAX_BYTES:
            raise HTTPException(status_code=400, detail="图片大小不能超过5MB")
    return bytes(buffer)


def _normalize_chat_message(message: str) -> str:
    normalized = message.strip()
    if len(normalized) > MEAL_TEXT_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="请求参数不完整或格式不正确")
    return normalized


async def _parse_chat_images(files: list[UploadFile]) -> list[ChatImageInput]:
    if len(files) > CHAT_IMAGE_MAX_COUNT:
        raise HTTPException(
            status_code=400, detail=f"最多只能上传{CHAT_IMAGE_MAX_COUNT}张图片"
        )

    parsed_images: list[ChatImageInput] = []
    for file in files:
        content = await _read_limited_upload(file)
        detected_mime = detect_chat_image_mime(content)
        if detected_mime not in ALLOWED_CHAT_IMAGE_MIME_TYPES:
            raise HTTPException(status_code=400, detail="仅支持 JPG、PNG、WEBP 图片")

        parsed_images.append(
            ChatImageInput(
                filename=file.filename or "upload-image",
                content_type=detected_mime,
                content=content,
            )
        )
    return parsed_images


async def _parse_chat_request(request: Request) -> tuple[str, list[ChatImageInput]]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            raw_payload = await request.json()
            payload = ChatRequest.model_validate(raw_payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail="请求体不是合法的 JSON"
            ) from exc
        except ValidationError as exc:
            raise HTTPException(
                status_code=400, detail="请求参数不完整或格式不正确"
            ) from exc
        return _normalize_chat_message(payload.message), []

    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="表单数据解析失败") from exc
        message = _normalize_chat_message(str(form.get("message") or ""))
        files = [
            cast(UploadFile, item)
            for item in form.getlist("images")
            if hasattr(item, "read")
        ]
        images = await _parse_chat_images(files)
        return message, images

    raise HTTPException(
        status_code=415, detail="仅支持 JSON 或 multipart/form-data 请求"
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session_factory():
    return SessionLocal


@router.post("/chat")
async def chat_with_agent(request: Request, db: Session = Depends(get_db)):
    message, images = await _parse_chat_request(request)
    if not message and len(images) == 0:
        raise HTTPException(status_code=400, detail="message或图片不能为空")

    safety_result = classify_medical_risk(message)
    if should_block_agent_run(safety_result):
        response = build_safety_response(safety_result)
        log_safety_intervention(
            db,
            user_id=DEMO_USER_ID,
            result=safety_result,
            action="blocked",
        )
        return format_agent_response(
            content=response["content"],
            refresh=[],
            safety_level=response["safety_level"],
            quick_replies=response["quick_replies"],
        )

    service = AgentChatService(db)
    result = await service.run_chat(message, images=images)
    if safety_result.risk_level == "medium":
        log_safety_intervention(
            db,
            user_id=DEMO_USER_ID,
            result=safety_result,
            action="warned",
        )
        result["data"]["safety_level"] = "medium"
    return result


@router.get("/reminders")
def get_chat_reminders(db: Session = Depends(get_db)):
    return {
        "code": 0,
        "data": get_pending_chat_reminders(db, user_id=DEMO_USER_ID),
    }


@router.get("/reminders/stream")
async def stream_chat_reminders(
    request: Request,
    once: bool = False,
    session_factory=Depends(get_db_session_factory),
):
    user_id = DEMO_USER_ID
    queue = None if once else reminder_broker.subscribe(user_id=user_id)

    def fetch_pending_reminders() -> list[dict]:
        db = session_factory()
        try:
            return get_pending_chat_reminders(db, user_id=user_id)
        finally:
            db.close()

    initial_reminders = await run_in_threadpool(fetch_pending_reminders)

    def get_valid_live_reminder(reminder_id: int) -> dict | None:
        reminders = fetch_pending_reminders()
        return next(
            (
                reminder
                for reminder in reminders
                if reminder.get("reminder_id") == reminder_id
            ),
            None,
        )

    async def event_generator() -> AsyncIterator[str]:
        emitted_reminder_ids = set()
        try:
            for reminder in initial_reminders:
                reminder_id = reminder.get("reminder_id")
                emitted_reminder_ids.add(reminder_id)
                yield format_sse_event(reminder)
            if queue is None:
                return

            async for event in stream_queue_events(queue):
                if await request.is_disconnected():
                    break
                if event.startswith(":") or event.startswith("event: close"):
                    yield event
                    continue
                try:
                    data_line = next(
                        line for line in event.splitlines() if line.startswith("data: ")
                    )
                    payload = json.loads(data_line.removeprefix("data: "))
                except (StopIteration, json.JSONDecodeError):
                    continue

                reminder_id = payload.get("reminder_id")
                if (
                    not isinstance(reminder_id, int)
                    or reminder_id in emitted_reminder_ids
                ):
                    continue
                valid_reminder = await run_in_threadpool(
                    get_valid_live_reminder, reminder_id
                )
                if valid_reminder is None:
                    continue
                emitted_reminder_ids.add(reminder_id)
                yield format_sse_event(valid_reminder)
        finally:
            if queue is not None:
                reminder_broker.unsubscribe(queue, user_id=user_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/reminders/ack")
def acknowledge_reminders(
    req: ReminderAcknowledgeRequest,
    db: Session = Depends(get_db),
):
    result = acknowledge_chat_reminders(
        db,
        user_id=DEMO_USER_ID,
        reminder_ids=list(dict.fromkeys(req.reminder_ids)),
    )
    return {"code": 0, "data": result}
