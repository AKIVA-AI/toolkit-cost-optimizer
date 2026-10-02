"""Tests for observability module: structured logging, metrics, time tracking."""

from __future__ import annotations

import json
import logging

import pytest

from toolkit_cost_latency_opt.observability import (
    Metrics,
    StructuredFormatter,
    configure_structured_logging,
    get_metrics,
    track_time,
)

# ============================================================================
# Metrics Tests
# ============================================================================


class TestMetrics:
    def test_increment_counter(self) -> None:
        m = Metrics()
        m.increment("test_counter")
        assert m.get_counter("test_counter") == 1
        m.increment("test_counter", 5)
        assert m.get_counter("test_counter") == 6

    def test_counter_default_zero(self) -> None:
        m = Metrics()
        assert m.get_counter("nonexistent") == 0

    def test_negative_increment_rejected(self) -> None:
        m = Metrics()
        with pytest.raises(ValueError, match="non-negative"):
            m.increment("test", -1)

    def test_record_gauge(self) -> None:
        m = Metrics()
        m.record_gauge("latency", 42.5)
        assert m.get_gauge("latency") == 42.5

    def test_add_gauge(self) -> None:
        m = Metrics()
        m.add_gauge("total_cost", 1.5)
        m.add_gauge("total_cost", 2.5)
        assert m.get_gauge("total_cost") == 4.0

    def test_gauge_default_zero(self) -> None:
        m = Metrics()
        assert m.get_gauge("nonexistent") == 0.0

    def test_snapshot(self) -> None:
        m = Metrics()
        m.increment("ops", 3)
        m.record_gauge("latency", 10.0)
        snap = m.snapshot()
        assert snap["counters"]["ops"] == 3
        assert snap["gauges"]["latency"] == 10.0

    def test_reset(self) -> None:
        m = Metrics()
        m.increment("ops", 3)
        m.record_gauge("latency", 10.0)
        m.reset()
        assert m.get_counter("ops") == 0
        assert m.get_gauge("latency") == 0.0

    def test_snapshot_empty(self) -> None:
        m = Metrics()
        snap = m.snapshot()
        assert snap == {"counters": {}, "gauges": {}}


# ============================================================================
# Global Metrics Tests
# ============================================================================


class TestGlobalMetrics:
    def test_get_metrics_returns_singleton(self) -> None:
        m1 = get_metrics()
        m2 = get_metrics()
        assert m1 is m2

    def test_global_metrics_tracks_state(self) -> None:
        m = get_metrics()
        m.reset()
        m.increment("test_global")
        assert m.get_counter("test_global") == 1
        m.reset()


# ============================================================================
# Structured Formatter Tests
# ============================================================================


class TestStructuredFormatter:
    def test_json_output(self) -> None:
        formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert parsed["logger"] == "test"
        assert "timestamp" in parsed

    def test_extra_fields_included(self) -> None:
        formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Op done",
            args=None,
            exc_info=None,
        )
        record.operation = "validate"  # type: ignore[attr-defined]
        record.duration_ms = 42.5  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["operation"] == "validate"
        assert parsed["duration_ms"] == 42.5

    def test_exception_included(self) -> None:
        formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S")
        try:
            raise RuntimeError("test error")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=None,
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "RuntimeError" in parsed["exception"]


# ============================================================================
# Track Time Decorator Tests
# ============================================================================


class TestTrackTime:
    def test_successful_function(self) -> None:
        metrics = get_metrics()
        metrics.reset()

        @track_time("test_op")
        def sample_func() -> int:
            return 42

        result = sample_func()
        assert result == 42
        assert metrics.get_counter("operations_completed") == 1
        # Duration may be 0.0 on fast machines (sub-microsecond resolution)
        assert metrics.get_gauge("last_test_op_duration_ms") >= 0

    def test_failing_function(self) -> None:
        metrics = get_metrics()
        metrics.reset()

        @track_time("fail_op")
        def failing_func() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            failing_func()
        assert metrics.get_counter("operations_failed") == 1

    def test_preserves_function_name(self) -> None:
        @track_time("op")
        def my_named_function() -> None:
            pass

        assert my_named_function.__name__ == "my_named_function"


# ============================================================================
# Configure Structured Logging Tests
# ============================================================================


class TestConfigureStructuredLogging:
    def test_json_format(self) -> None:
        configure_structured_logging(level=logging.DEBUG, json_format=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) > 0
        handler = root.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)
        # Clean up
        root.handlers.clear()

    def test_plain_format(self) -> None:
        configure_structured_logging(level=logging.WARNING, json_format=False)
        root = logging.getLogger()
        assert root.level == logging.WARNING
        # Clean up
        root.handlers.clear()
