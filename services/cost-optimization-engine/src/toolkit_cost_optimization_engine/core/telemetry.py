"""
OpenTelemetry instrumentation for Toolkit Cost Optimization Engine.

Initialises the OTLP tracer when the ``opentelemetry`` packages are installed
and ``OTEL_ENABLED`` is truthy. Falls back to no-op stubs when the SDK is
absent so the rest of the codebase can call ``get_tracer()`` unconditionally.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detect whether the OpenTelemetry SDK is available
# ---------------------------------------------------------------------------
_OTEL_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    _OTEL_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

_tracer: Any = None  # Will be opentelemetry.trace.Tracer or _NoOpTracer


class _NoOpSpan:
    """Minimal stand-in when OpenTelemetry is not installed."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass

    def record_exception(self, exc: BaseException) -> None:  # noqa: ARG002
        pass

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoOpTracer:
    """Tracer that always returns no-op spans."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoOpSpan:  # noqa: ARG002
        return _NoOpSpan()


def init_telemetry(service_name: str = "toolkit-cost-optimization-engine") -> None:
    """Initialise the OpenTelemetry tracer provider.

    Called once at application startup. When ``OTEL_ENABLED`` is falsy or the
    SDK is not installed this is a no-op.
    """
    global _tracer

    otel_enabled = os.getenv("OTEL_ENABLED", "false").lower() in ("1", "true", "yes")

    if not _OTEL_AVAILABLE or not otel_enabled:
        _tracer = _NoOpTracer()
        logger.info(
            "OpenTelemetry disabled (available=%s, enabled=%s)",
            _OTEL_AVAILABLE,
            otel_enabled,
        )
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Use OTLP exporter if endpoint is configured, otherwise console
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)),
            )
            logger.info("OpenTelemetry OTLP exporter configured: %s", otlp_endpoint)
        except ImportError:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            logger.warning("OTLP exporter not installed, falling back to console exporter")
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OpenTelemetry using console exporter (no OTEL_EXPORTER_OTLP_ENDPOINT)")

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    logger.info("OpenTelemetry initialised for %s", service_name)


def get_tracer() -> Any:
    """Return the application tracer (no-op safe)."""
    global _tracer
    if _tracer is None:
        _tracer = _NoOpTracer()
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Generator[Any, None, None]:
    """Convenience context manager that creates a span with attributes."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v)
        yield s
