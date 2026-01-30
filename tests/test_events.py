"""Tests for Event queue async (core/events.py)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from microvm_orchestrator.core.events import (
    EventQueue,
    EventType,
    TaskEvent,
)


# =============================================================================
# Basic Queue Operations Tests
# =============================================================================


class TestBasicQueueOperations:
    """Tests for basic emit and pop operations."""

    def test_emit_and_pop(self, event_queue: EventQueue, sample_completed_event: TaskEvent):
        """Basic FIFO behavior - emit then pop returns same event."""
        event_queue.emit(sample_completed_event)

        result = event_queue._try_pop()

        assert result is sample_completed_event

    def test_empty_queue_pop_returns_none(self, event_queue: EventQueue):
        """Pop from empty queue returns None."""
        result = event_queue._try_pop()

        assert result is None

    def test_fifo_order(self, event_queue: EventQueue, fixed_uuid: str, frozen_time: datetime):
        """Events are returned in FIFO order."""
        event1 = TaskEvent(
            task_id="task-1",
            event_type=EventType.COMPLETED,
            timestamp=frozen_time,
        )
        event2 = TaskEvent(
            task_id="task-2",
            event_type=EventType.FAILED,
            timestamp=frozen_time,
        )
        event3 = TaskEvent(
            task_id="task-3",
            event_type=EventType.COMPLETED,
            timestamp=frozen_time,
        )

        event_queue.emit(event1)
        event_queue.emit(event2)
        event_queue.emit(event3)

        assert event_queue._try_pop() is event1
        assert event_queue._try_pop() is event2
        assert event_queue._try_pop() is event3
        assert event_queue._try_pop() is None


# =============================================================================
# Synchronous Wait Tests
# =============================================================================


class TestSynchronousWait:
    """Tests for synchronous wait() method."""

    def test_wait_returns_event(self, event_queue: EventQueue, sample_completed_event: TaskEvent):
        """Sync wait succeeds when event is already in queue."""
        event_queue.emit(sample_completed_event)

        result = event_queue.wait(timeout_ms=100)

        assert result is sample_completed_event

    def test_wait_timeout(self, event_queue: EventQueue):
        """Returns None after timeout when no events."""
        start = time.monotonic()
        result = event_queue.wait(timeout_ms=50)
        elapsed = time.monotonic() - start

        assert result is None
        # Should have waited approximately 50ms
        assert 0.04 <= elapsed <= 0.2

    def test_wait_receives_event_during_wait(
        self, event_queue: EventQueue, sample_completed_event: TaskEvent
    ):
        """Wait returns event that arrives during wait period."""
        def emit_after_delay():
            time.sleep(0.02)  # 20ms delay
            event_queue.emit(sample_completed_event)

        thread = threading.Thread(target=emit_after_delay)
        thread.start()

        result = event_queue.wait(timeout_ms=1000)
        thread.join()

        assert result is sample_completed_event


# =============================================================================
# Asynchronous Wait Tests
# =============================================================================


class TestAsyncWait:
    """Tests for async wait_async() method."""

    @pytest.mark.asyncio
    async def test_wait_async_returns_event(
        self, event_queue: EventQueue, sample_completed_event: TaskEvent
    ):
        """Async version returns event from queue."""
        event_queue.emit(sample_completed_event)

        result = await event_queue.wait_async(timeout_ms=100)

        assert result is sample_completed_event

    @pytest.mark.asyncio
    async def test_wait_async_timeout(self, event_queue: EventQueue):
        """Async timeout handling - returns None after timeout."""
        start = time.monotonic()
        result = await event_queue.wait_async(timeout_ms=50)
        elapsed = time.monotonic() - start

        assert result is None
        # Should have waited approximately 50ms
        assert 0.04 <= elapsed <= 0.2

    @pytest.mark.asyncio
    async def test_wait_async_cancellation(self, event_queue: EventQueue):
        """CancelledError propagates correctly."""
        async def wait_and_cancel():
            task = asyncio.create_task(event_queue.wait_async(timeout_ms=10000))
            await asyncio.sleep(0.01)  # Let the task start
            task.cancel()
            return await task

        with pytest.raises(asyncio.CancelledError):
            await wait_and_cancel()

    @pytest.mark.asyncio
    async def test_wait_async_receives_event_during_wait(
        self, event_queue: EventQueue, sample_completed_event: TaskEvent
    ):
        """Async wait returns event that arrives during wait period."""
        async def emit_after_delay():
            await asyncio.sleep(0.02)  # 20ms delay
            event_queue.emit(sample_completed_event)

        emit_task = asyncio.create_task(emit_after_delay())

        result = await event_queue.wait_async(timeout_ms=1000)
        await emit_task

        assert result is sample_completed_event


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread-safe operations."""

    def test_thread_safe_emission(self, event_queue: EventQueue, frozen_time: datetime):
        """Multiple threads emitting events concurrently."""
        num_threads = 10
        events_per_thread = 100

        def emit_events(thread_id: int):
            for i in range(events_per_thread):
                event = TaskEvent(
                    task_id=f"task-{thread_id}-{i}",
                    event_type=EventType.COMPLETED,
                    timestamp=frozen_time,
                )
                event_queue.emit(event)

        # Emit from multiple threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(emit_events, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        # Count events
        count = 0
        while event_queue._try_pop() is not None:
            count += 1

        assert count == num_threads * events_per_thread

    def test_concurrent_emit_and_pop(self, event_queue: EventQueue, frozen_time: datetime):
        """Concurrent emitting and popping is thread-safe."""
        num_events = 1000
        emitted = []
        popped = []
        emit_lock = threading.Lock()
        pop_lock = threading.Lock()

        def emit_events():
            for i in range(num_events):
                event = TaskEvent(
                    task_id=f"task-{i}",
                    event_type=EventType.COMPLETED,
                    timestamp=frozen_time,
                )
                event_queue.emit(event)
                with emit_lock:
                    emitted.append(event.task_id)

        def pop_events():
            while True:
                event = event_queue._try_pop()
                if event:
                    with pop_lock:
                        popped.append(event.task_id)
                elif len(popped) >= num_events:
                    break
                else:
                    time.sleep(0.001)

        emit_thread = threading.Thread(target=emit_events)
        pop_thread = threading.Thread(target=pop_events)

        emit_thread.start()
        pop_thread.start()

        emit_thread.join()
        pop_thread.join(timeout=5)

        assert len(popped) == num_events
        assert set(emitted) == set(popped)


# =============================================================================
# Multiple Waiters Tests
# =============================================================================


class TestMultipleWaiters:
    """Tests for multiple consumers."""

    @pytest.mark.asyncio
    async def test_multiple_waiters(self, event_queue: EventQueue, frozen_time: datetime):
        """Two consumers each get different events."""
        event1 = TaskEvent(
            task_id="task-1",
            event_type=EventType.COMPLETED,
            timestamp=frozen_time,
        )
        event2 = TaskEvent(
            task_id="task-2",
            event_type=EventType.COMPLETED,
            timestamp=frozen_time,
        )

        # Start two waiters
        waiter1 = asyncio.create_task(event_queue.wait_async(timeout_ms=1000))
        waiter2 = asyncio.create_task(event_queue.wait_async(timeout_ms=1000))

        # Give waiters time to start
        await asyncio.sleep(0.01)

        # Emit two events
        event_queue.emit(event1)
        event_queue.emit(event2)

        # Both waiters should get events
        results = await asyncio.gather(waiter1, waiter2)

        assert len(results) == 2
        assert event1 in results
        assert event2 in results


# =============================================================================
# Event Factory Tests
# =============================================================================


class TestEventFactories:
    """Tests for event factory methods."""

    def test_create_completed_event(self, event_queue: EventQueue, fixed_uuid: str):
        """Factory function creates completion event."""
        result = {"summary": "Test completed"}
        merge_result = {"merged": True}

        event = event_queue.create_completed_event(
            task_id=fixed_uuid,
            exit_code=0,
            result=result,
            merge_result=merge_result,
        )

        assert event.task_id == fixed_uuid
        assert event.event_type == EventType.COMPLETED
        assert event.exit_code == 0
        assert event.result == result
        assert event.merge_result == merge_result
        assert event.timestamp is not None
        assert event.error is None

    def test_create_completed_event_non_zero_exit(
        self, event_queue: EventQueue, fixed_uuid: str
    ):
        """Factory creates FAILED event type for non-zero exit code."""
        event = event_queue.create_completed_event(
            task_id=fixed_uuid,
            exit_code=1,
        )

        assert event.event_type == EventType.FAILED
        assert event.exit_code == 1

    def test_create_failed_event(self, event_queue: EventQueue, fixed_uuid: str):
        """Factory function creates failure event."""
        error_msg = "Task execution failed"

        event = event_queue.create_failed_event(
            task_id=fixed_uuid,
            error=error_msg,
        )

        assert event.task_id == fixed_uuid
        assert event.event_type == EventType.FAILED
        assert event.error == error_msg
        assert event.timestamp is not None
        assert event.exit_code is None


# =============================================================================
# TaskEvent Tests
# =============================================================================


class TestTaskEvent:
    """Tests for TaskEvent dataclass."""

    def test_to_dict(self, sample_completed_event: TaskEvent):
        """to_dict() returns correct dictionary representation."""
        result = sample_completed_event.to_dict()

        assert result["task_id"] == sample_completed_event.task_id
        assert result["event"] == "completed"
        assert result["timestamp"] == sample_completed_event.timestamp.isoformat()
        assert result["exit_code"] == sample_completed_event.exit_code
        assert result["result"] == sample_completed_event.result

    def test_to_dict_failed_event(self, sample_failed_event: TaskEvent):
        """to_dict() handles failed events correctly."""
        result = sample_failed_event.to_dict()

        assert result["event"] == "failed"
        assert result["error"] == sample_failed_event.error

    def test_event_type_values(self):
        """EventType enum has correct string values."""
        assert EventType.COMPLETED.value == "completed"
        assert EventType.FAILED.value == "failed"
