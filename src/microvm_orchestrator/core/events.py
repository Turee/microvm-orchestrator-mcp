"""Event queue for task completion notifications."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskEvent:
    """Event emitted when a task completes or fails."""

    task_id: str
    event_type: EventType
    timestamp: datetime
    exit_code: Optional[int] = None
    error: Optional[str] = None
    result: Optional[dict] = None
    merge_result: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "event": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "exit_code": self.exit_code,
            "error": self.error,
            "result": self.result,
            "merge_result": self.merge_result,
        }


class EventQueue:
    """Thread-safe FIFO queue for task events with async support.

    Events are emitted from background threads (VM monitors) and consumed
    asynchronously by the MCP server. Uses asyncio primitives for proper
    cancellation support.
    """

    def __init__(self):
        self._queue: deque[TaskEvent] = deque()
        self._lock = threading.Lock()
        # Track the asyncio loop and event for cross-thread signaling
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_event: Optional[asyncio.Event] = None

    def _get_async_event(self, loop: asyncio.AbstractEventLoop) -> asyncio.Event:
        """Get or create the asyncio.Event for the given loop."""
        if self._loop is not loop or self._async_event is None:
            self._loop = loop
            self._async_event = asyncio.Event()
        return self._async_event

    def emit(self, event: TaskEvent) -> None:
        """Add event to queue (thread-safe, called from VM threads)."""
        with self._lock:
            self._queue.append(event)

        # Signal async waiters if there's an active loop
        if self._loop is not None and self._async_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._async_event.set)
            except RuntimeError:
                # Loop is closed, ignore
                pass

    def _try_pop(self) -> Optional[TaskEvent]:
        """Try to pop an event from the queue (thread-safe)."""
        with self._lock:
            if self._queue:
                return self._queue.popleft()
            return None

    def wait(self, timeout_ms: int = 30000) -> Optional[TaskEvent]:
        """
        Wait for the next event (synchronous, for tests).

        Returns the next event in FIFO order, or None on timeout.
        """
        import time
        timeout_sec = timeout_ms / 1000.0
        end_time = time.monotonic() + timeout_sec

        while True:
            if event := self._try_pop():
                return event

            remaining = end_time - time.monotonic()
            if remaining <= 0:
                return None

            # Poll every 10ms
            time.sleep(min(0.01, remaining))

    async def wait_async(self, timeout_ms: int = 30000) -> Optional[TaskEvent]:
        """
        Wait for the next event (async).

        Properly cancellable by asyncio - no background threads that outlive
        the cancelled task.

        Returns the next event in FIFO order, or None on timeout.
        """
        loop = asyncio.get_running_loop()
        async_event = self._get_async_event(loop)
        timeout_sec = timeout_ms / 1000.0

        # Check if there's already an event
        if event := self._try_pop():
            return event

        # Wait for an event with timeout, checking periodically
        # Use short waits to allow checking the queue and handling cancellation
        end_time = loop.time() + timeout_sec

        while True:
            remaining = end_time - loop.time()
            if remaining <= 0:
                return None

            # Clear the event before waiting
            async_event.clear()

            # Check queue again (event might have arrived between clear and wait)
            if event := self._try_pop():
                return event

            try:
                # Wait for signal or timeout (whichever comes first)
                await asyncio.wait_for(async_event.wait(), timeout=min(remaining, 1.0))
            except asyncio.TimeoutError:
                # Check if we have an event anyway
                if event := self._try_pop():
                    return event
                # Continue waiting if we haven't hit total timeout
                continue

            # Got signaled, try to pop
            if event := self._try_pop():
                return event
            # Spurious wakeup, continue waiting

    def create_completed_event(
        self,
        task_id: str,
        exit_code: int,
        result: Optional[dict] = None,
        merge_result: Optional[dict] = None,
    ) -> TaskEvent:
        """Create a completion event."""
        return TaskEvent(
            task_id=task_id,
            event_type=EventType.COMPLETED if exit_code == 0 else EventType.FAILED,
            timestamp=datetime.now(timezone.utc),
            exit_code=exit_code,
            result=result,
            merge_result=merge_result,
        )

    def create_failed_event(
        self,
        task_id: str,
        error: str,
    ) -> TaskEvent:
        """Create a failure event."""
        return TaskEvent(
            task_id=task_id,
            event_type=EventType.FAILED,
            timestamp=datetime.now(timezone.utc),
            error=error,
        )
