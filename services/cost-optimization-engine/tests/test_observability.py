"""Tests for Sprint 1 observability features:

- Structured JSON logging (LOG_FORMAT=json)
- OpenTelemetry telemetry stubs / init
- Request ID middleware and propagation
"""

from __future__ import annotations

import json
import logging
import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Force test environment before any service imports
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-not-for-production"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["POSTGRES_PASSWORD"] = "testpass"


def _register_sqlite_type_adapters():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
        SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"  # noqa: ARG005
    if not hasattr(SQLiteTypeCompiler, "visit_UUID"):
        SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # noqa: ARG005


_register_sqlite_type_adapters()


@pytest.fixture(autouse=True)
def _clear_caches():
    from toolkit_cost_optimization_engine.core.config import (
        get_database_settings,
        get_settings,
    )

    get_settings.cache_clear()
    get_database_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_database_settings.cache_clear()


@pytest.fixture
async def async_client():
    from toolkit_cost_optimization_engine.core import database as db_module
    from toolkit_cost_optimization_engine.core.database import Base

    orig_engine = db_module.engine
    orig_session = db_module.AsyncSessionLocal

    test_engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    test_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    db_module.engine = test_engine
    db_module.AsyncSessionLocal = test_session_factory

    async with test_engine.begin() as conn:
        from toolkit_cost_optimization_engine.models import models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)

    from toolkit_cost_optimization_engine.main import app

    app.router.on_startup.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()

    db_module.engine = orig_engine
    db_module.AsyncSessionLocal = orig_session


# ============================================================================
# Structured JSON Logging
# ============================================================================


class TestJSONLogging:
    """Tests for structured JSON log output."""

    def test_json_formatter_produces_valid_json(self):
        from toolkit_cost_optimization_engine.core.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert "timestamp" in parsed

    def test_json_formatter_includes_request_id(self):
        from toolkit_cost_optimization_engine.core.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="with id",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "abc-123"

    def test_json_formatter_includes_exception(self):
        import traceback

        from toolkit_cost_optimization_engine.core.logging_config import JSONFormatter

        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "boom" in parsed["exception"]

    def test_text_formatter_output(self):
        from toolkit_cost_optimization_engine.core.logging_config import TextFormatter

        formatter = TextFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "hello" in output
        assert "INFO" in output

    def test_configure_logging_json_mode(self):
        from toolkit_cost_optimization_engine.core.logging_config import (
            JSONFormatter,
            configure_logging,
        )

        configure_logging(log_level="DEBUG", log_format="json")
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_configure_logging_text_mode(self):
        from toolkit_cost_optimization_engine.core.logging_config import (
            TextFormatter,
            configure_logging,
        )

        configure_logging(log_level="WARNING", log_format="text")
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, TextFormatter)


# ============================================================================
# OpenTelemetry Telemetry
# ============================================================================


class TestTelemetry:
    """Tests for OpenTelemetry wiring (no-op mode when SDK not enabled)."""

    def test_get_tracer_returns_noop_by_default(self):
        from toolkit_cost_optimization_engine.core.telemetry import _NoOpTracer, get_tracer

        tracer = get_tracer()
        assert isinstance(tracer, _NoOpTracer)

    def test_noop_span_attributes(self):
        from toolkit_cost_optimization_engine.core.telemetry import _NoOpSpan

        s = _NoOpSpan()
        # Should not raise
        s.set_attribute("key", "value")
        s.set_status("ok")
        s.record_exception(ValueError("test"))

    def test_noop_span_context_manager(self):
        from toolkit_cost_optimization_engine.core.telemetry import _NoOpSpan

        with _NoOpSpan() as s:
            s.set_attribute("x", 1)

    def test_span_helper_returns_noop(self):
        from toolkit_cost_optimization_engine.core.telemetry import span

        with span("test-span", http_method="GET") as s:
            s.set_attribute("key", "val")

    def test_init_telemetry_disabled(self):
        from toolkit_cost_optimization_engine.core.telemetry import (
            _NoOpTracer,
            get_tracer,
            init_telemetry,
        )

        os.environ.pop("OTEL_ENABLED", None)
        init_telemetry()
        tracer = get_tracer()
        assert isinstance(tracer, _NoOpTracer)


# ============================================================================
# Request ID Middleware
# ============================================================================


class TestRequestIDMiddleware:
    """Tests for X-Request-ID header propagation."""

    async def test_response_includes_request_id(self, async_client: AsyncClient):
        """Every response must include X-Request-ID."""
        response = await async_client.get("/health")
        assert response.status_code == 200
        assert "x-request-id" in response.headers

    async def test_request_id_propagated_from_caller(self, async_client: AsyncClient):
        """If the caller sends X-Request-ID, the same value is echoed back."""
        custom_id = "my-custom-request-id-12345"
        response = await async_client.get(
            "/health",
            headers={"X-Request-ID": custom_id},
        )
        assert response.status_code == 200
        assert response.headers["x-request-id"] == custom_id

    async def test_request_id_generated_when_absent(self, async_client: AsyncClient):
        """When no X-Request-ID header is sent, one is generated."""
        response = await async_client.get("/")
        rid = response.headers.get("x-request-id")
        assert rid is not None
        assert len(rid) == 32  # uuid4 hex

    async def test_different_requests_get_different_ids(self, async_client: AsyncClient):
        """Two requests without X-Request-ID should get different IDs."""
        r1 = await async_client.get("/")
        r2 = await async_client.get("/")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_request_id_filter_injects_into_record(self):
        from toolkit_cost_optimization_engine.core.request_id import (
            REQUEST_ID_CTX,
            RequestIDFilter,
        )

        f = RequestIDFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        # No active request
        f.filter(record)
        assert record.request_id == "-"  # type: ignore[attr-defined]

        # With active request
        token = REQUEST_ID_CTX.set("req-abc")
        f.filter(record)
        assert record.request_id == "req-abc"  # type: ignore[attr-defined]
        REQUEST_ID_CTX.reset(token)

    def test_get_request_id_returns_none_by_default(self):
        from toolkit_cost_optimization_engine.core.request_id import get_request_id

        assert get_request_id() is None
