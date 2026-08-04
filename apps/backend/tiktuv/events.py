"""In-process event bus feeding Server-Sent Events to the UI.

The worker runs on a thread; the API serves SSE on the asyncio loop. Publishing
is therefore thread-safe and non-blocking: a slow or vanished browser tab drops
its own events instead of stalling transcription.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .logging_conf import get_logger

log = get_logger("events")

#: Per-subscriber buffer. Beyond this the oldest events are dropped — the UI
#: always re-syncs from ``GET /api/jobs``, so a gap is recoverable.
_QUEUE_MAX = 256


class EventType(StrEnum):
    JOB_CREATED = "job.created"
    JOB_PROGRESS = "job.progress"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELLED = "job.cancelled"
    JOB_DELETED = "job.deleted"
    TRANSCRIPT_PARTIAL = "transcript.partial"
    QUEUE_CHANGED = "queue.changed"
    MODEL_STATUS = "model.status"
    MEDIA_FETCH = "media.fetch"


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self, event_id: int) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"id: {event_id}\nevent: {self.type.value}\ndata: {payload}\n\n"


class EventBus:
    """Fan-out bus with per-subscriber bounded queues."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._counter = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop that worker threads should publish into."""
        self._loop = loop

    # -- publishing --------------------------------------------------------
    def publish_threadsafe(self, event: Event) -> None:
        """Publish from any thread. Safe to call before the loop exists."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # The loop can close between the check above and this call.
        with contextlib.suppress(RuntimeError):  # pragma: no cover - shutdown race
            loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: Event) -> None:
        self._counter += 1
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event for this subscriber and retry once.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    log.warning("dropped event for a slow subscriber")

    async def publish(self, event: Event) -> None:
        self._publish(event)

    # -- subscribing -------------------------------------------------------
    async def stream(self) -> AsyncIterator[str]:
        """Yield SSE frames until the client disconnects."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers.add(queue)
        sequence = 0
        try:
            # Tell the client we are live so it can drop its "connecting" state.
            yield "retry: 2000\n\n"
            yield Event(EventType.QUEUE_CHANGED, {"reason": "subscribed"}).to_sse(0)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # Comment frame: keeps proxies and the browser from timing
                    # the connection out during a long quiet stretch.
                    yield ": keepalive\n\n"
                    continue
                sequence += 1
                yield event.to_sse(sequence)
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


#: Process-wide bus. Injected into the app state at startup.
bus = EventBus()
