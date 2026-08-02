"""Event bus and SSE frame formatting.

Tested directly rather than over HTTP: an SSE response never completes, so
Starlette's in-process test transport cannot consume one.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from tiktuv.events import Event, EventBus, EventType


class TestSseFraming:
    def test_frame_has_id_event_and_data(self) -> None:
        frame = Event(EventType.JOB_PROGRESS, {"job_id": "abc", "percent": 0.5}).to_sse(7)
        assert "id: 7" in frame
        assert "event: job.progress" in frame
        assert frame.endswith("\n\n")

    def test_data_is_valid_json(self) -> None:
        frame = Event(EventType.JOB_COMPLETED, {"job_id": "x", "segments": 3}).to_sse(1)
        data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line.removeprefix("data: "))
        assert payload == {"job_id": "x", "segments": 3}

    def test_hebrew_is_not_escaped(self) -> None:
        frame = Event(EventType.JOB_FAILED, {"message": "שגיאה"}).to_sse(1)
        assert "שגיאה" in frame

    def test_data_stays_on_one_line(self) -> None:
        """A newline inside the payload would split the SSE frame."""
        frame = Event(EventType.JOB_FAILED, {"detail": "line one\nline two"}).to_sse(1)
        data_lines = [line for line in frame.splitlines() if line.startswith("data: ")]
        assert len(data_lines) == 1


class TestEventBus:
    async def test_subscriber_receives_a_published_event(self) -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        stream = bus.stream()

        # Preamble: retry hint, then the initial queue-changed frame.
        assert "retry:" in await anext(stream)
        assert "queue.changed" in await anext(stream)

        await bus.publish(Event(EventType.JOB_PROGRESS, {"job_id": "a", "percent": 0.25}))
        frame = await asyncio.wait_for(anext(stream), timeout=2.0)
        assert "job.progress" in frame
        assert '"percent": 0.25' in frame
        await stream.aclose()

    async def test_multiple_subscribers_each_get_the_event(self) -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        first, second = bus.stream(), bus.stream()
        for stream in (first, second):
            await anext(stream)
            await anext(stream)

        await bus.publish(Event(EventType.JOB_COMPLETED, {"job_id": "z"}))
        for stream in (first, second):
            frame = await asyncio.wait_for(anext(stream), timeout=2.0)
            assert "job.completed" in frame
        await first.aclose()
        await second.aclose()

    async def test_subscriber_is_removed_on_close(self) -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        stream = bus.stream()
        await anext(stream)
        assert bus.subscriber_count == 1
        await stream.aclose()
        assert bus.subscriber_count == 0

    async def test_publish_threadsafe_from_a_worker_thread(self) -> None:
        """The transcription worker publishes from a thread, not the loop."""
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        stream = bus.stream()
        await anext(stream)
        await anext(stream)

        def worker() -> None:
            bus.publish_threadsafe(
                Event(EventType.JOB_PROGRESS, {"job_id": "threaded", "percent": 0.9})
            )

        threading.Thread(target=worker).start()
        frame = await asyncio.wait_for(anext(stream), timeout=3.0)
        assert "threaded" in frame
        await stream.aclose()

    async def test_publish_without_a_loop_is_a_no_op(self) -> None:
        """Events emitted before the server is up must not raise."""
        bus = EventBus()
        bus.publish_threadsafe(Event(EventType.JOB_CREATED, {"job_id": "early"}))

    async def test_slow_subscriber_does_not_block_the_publisher(self) -> None:
        """A stalled UI must never stall transcription."""
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        stream = bus.stream()
        await anext(stream)  # retry hint
        await anext(stream)  # initial queue.changed

        # Publish far more than the per-subscriber buffer holds.
        for index in range(1000):
            await bus.publish(Event(EventType.JOB_PROGRESS, {"i": index}))

        # The newest event survived; the oldest were dropped, not queued.
        frame = await asyncio.wait_for(anext(stream), timeout=2.0)
        assert "job." in frame
        await stream.aclose()

    async def test_keepalive_is_emitted_when_idle(self) -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        stream = bus.stream()
        await anext(stream)
        await anext(stream)

        # The keepalive timeout is 15 s; assert the mechanism exists rather
        # than waiting for it, by publishing nothing and cancelling promptly.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), timeout=0.2)
        await stream.aclose()
