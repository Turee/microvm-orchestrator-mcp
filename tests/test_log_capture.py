"""Tests for tui/log_capture.py - thread-safe logging handler."""

from __future__ import annotations

import logging
import threading

from microvm_orchestrator.tui.log_capture import LogCapture


class TestLogCaptureBasic:
    def test_captures_log_records(self):
        handler = LogCapture()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.capture.basic")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("hello world")
            lines = handler.get_lines()
            assert lines == ["hello world"]
        finally:
            logger.removeHandler(handler)

    def test_multiline_message_split(self):
        handler = LogCapture()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.capture.multiline")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("line1\nline2\nline3")
            lines = handler.get_lines()
            assert lines == ["line1", "line2", "line3"]
        finally:
            logger.removeHandler(handler)

    def test_maxlen_bounds(self):
        handler = LogCapture(maxlen=3)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.capture.maxlen")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            for i in range(10):
                logger.info("msg-%d", i)
            lines = handler.get_lines()
            assert len(lines) == 3
            assert lines == ["msg-7", "msg-8", "msg-9"]
        finally:
            logger.removeHandler(handler)

    def test_get_lines_returns_copy(self):
        handler = LogCapture()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.capture.copy")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("data")
            lines = handler.get_lines()
            lines.clear()
            assert handler.get_lines() == ["data"]
        finally:
            logger.removeHandler(handler)

    def test_empty_buffer(self):
        handler = LogCapture()
        assert handler.get_lines() == []


class TestLogCaptureThreadSafety:
    def test_concurrent_writes(self):
        handler = LogCapture(maxlen=10_000)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.capture.threadsafe")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        n_threads = 4
        n_messages = 100
        barrier = threading.Barrier(n_threads)

        def writer(thread_id: int) -> None:
            barrier.wait()
            for i in range(n_messages):
                logger.info("t%d-msg%d", thread_id, i)

        try:
            threads = [
                threading.Thread(target=writer, args=(t,)) for t in range(n_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            lines = handler.get_lines()
            assert len(lines) == n_threads * n_messages
        finally:
            logger.removeHandler(handler)
