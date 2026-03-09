"""
Structured logging configuration for Toolkit Cost Optimization Engine.

Supports two formats controlled by the LOG_FORMAT setting:
- "json"  : machine-readable JSON lines (default)
- "text"  : human-readable plain text
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include request_id if present on the record
        request_id = getattr(record, "request_id", None)
        if request_id:
            log_entry["request_id"] = request_id

        # Include exception info if present
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include extra fields added via `extra=` kwarg
        for key in ("status_code", "method", "path", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        return json.dumps(log_entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable log formatter with optional request_id."""

    FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT)

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None)
        if request_id:
            record.msg = f"[{request_id}] {record.msg}"
        return super().format(record)


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configure root logging with the chosen format.

    Args:
        log_level: Python log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: "json" for structured JSON lines, "text" for plain text.
    """
    handler = logging.StreamHandler(sys.stderr)

    if log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
