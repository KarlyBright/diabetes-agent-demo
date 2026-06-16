from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class ReminderBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, *, user_id: int) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(user_id, set()).add(queue)
        return queue

    def unsubscribe(
        self, queue: asyncio.Queue[dict[str, Any]], *, user_id: int
    ) -> None:
        subscribers = self._subscribers.get(user_id)
        if subscribers is None:
            return

        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(user_id, None)

    def close_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait({"__close__": True})

    def publish(self, reminders: list[dict[str, Any]]) -> None:
        for reminder in reminders:
            user_id = reminder.get("user_id")
            if not isinstance(user_id, int):
                continue

            for queue in list(self._subscribers.get(user_id, set())):
                try:
                    queue.put_nowait(reminder)
                except asyncio.QueueFull:
                    self.close_queue(queue)
                    self.unsubscribe(queue, user_id=user_id)


reminder_broker = ReminderBroker()


def format_sse_event(payload: dict[str, Any], event_name: str = "message") -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n"


async def stream_queue_events(
    queue: asyncio.Queue[dict[str, Any]], *, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    while True:
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            if payload.get("__close__") is True:
                yield "event: close\ndata: queue-overflow\n\n"
                return
            yield format_sse_event(payload)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
