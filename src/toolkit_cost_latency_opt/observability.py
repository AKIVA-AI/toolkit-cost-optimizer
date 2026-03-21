"""Structured logging, metrics, and execution time tracking.

Provides:
- JSON-formatted structured logging for production use
- Execution time tracking decorator
- Basic metrics counters (analyses run, errors, cost savings)
"""

from __future__ import annotations

import functools
import json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class StructuredFormatter(logging.Formatter):
    """JSON-structured log formatter for machine-readable output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Include extra structured fields if present
        for key in ("operation", "duration_ms", "model", "count", "error_type"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, default=str)


class Metrics:
    """Simple in-process metrics counters for observability.

    Thread-safe for single-process use. Tracks:
    - analyses_run: total analysis operations completed
    - errors: total errors encountered
    - cost_savings_usd: cumulative cost savings calculated
    - operation counts by type
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a counter metric."""
        if amount < 0:
            raise ValueError(f"Counter increment must be non-negative, got: {amount}")
        self._counters[name] += amount

    def record_gauge(self, name: str, value: float) -> None:
        """Record a gauge (point-in-time) metric."""
        self._gauges[name] = value

    def add_gauge(self, name: str, value: float) -> None:
        """Add to a gauge metric (accumulator pattern)."""
        self._gauges[name] += value

    def get_counter(self, name: str) -> int:
        """Get current counter value."""
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        """Get current gauge value."""
        return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of all metrics."""
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
        }

    def reset(self) -> None:
        """Reset all metrics (useful for testing)."""
        self._counters.clear()
        self._gauges.clear()


# Global metrics instance
_metrics = Metrics()


def get_metrics() -> Metrics:
    """Get the global metrics instance."""
    return _metrics


def track_time(operation: str) -> Callable[[F], F]:
    """Decorator to track execution time of a function.

    Logs duration at INFO level and records in metrics.

    Args:
        operation: Name of the operation being tracked
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = logging.getLogger(func.__module__)
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "Operation completed",
                    extra={
                        "operation": operation,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                )
                _metrics.increment("operations_completed")
                _metrics.record_gauge(f"last_{operation}_duration_ms", elapsed_ms)
                return result
            except Exception:
                elapsed_ms = (time.monotonic() - start) * 1000
                _metrics.increment("operations_failed")
                logger.warning(
                    "Operation failed",
                    extra={
                        "operation": operation,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                )
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


def configure_structured_logging(
    level: int = logging.WARNING,
    json_format: bool = False,
) -> None:
    """Configure logging with optional JSON structured output.

    Args:
        level: Logging level
        json_format: If True, use JSON formatter; otherwise use human-readable format
    """
    root = logging.getLogger()
    # Remove existing handlers to avoid duplicates
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    if json_format:
        handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(handler)
    root.setLevel(level)
