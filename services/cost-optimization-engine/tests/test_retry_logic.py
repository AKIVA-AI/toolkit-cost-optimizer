"""Tests for database retry logic with exponential backoff."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError

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


class TestWithRetry:
    async def test_succeeds_on_first_attempt(self):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(return_value="ok")
        result = await with_retry(func, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert func.call_count == 1

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_operational_error(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(
            side_effect=[
                OperationalError("conn failed", {}, None),
                "recovered",
            ],
        )
        result = await with_retry(func, max_retries=3, base_delay=0.01)
        assert result == "recovered"
        assert func.call_count == 2
        mock_sleep.assert_called_once()

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_connection_error(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(
            side_effect=[
                ConnectionError("refused"),
                "ok",
            ],
        )
        result = await with_retry(func, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert func.call_count == 2

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausts_retries(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(
            side_effect=OperationalError("persistent failure", {}, None),
        )
        with pytest.raises(OperationalError):
            await with_retry(func, max_retries=3, base_delay=0.01)
        assert func.call_count == 3
        assert mock_sleep.call_count == 2  # No sleep after final failure

    async def test_non_retryable_exception_propagates_immediately(self):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(side_effect=ValueError("bad input"))
        with pytest.raises(ValueError, match="bad input"):
            await with_retry(func, max_retries=3, base_delay=0.01)
        assert func.call_count == 1  # No retries for ValueError

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_delay_increases_exponentially(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(
            side_effect=[
                OperationalError("e1", {}, None),
                OperationalError("e2", {}, None),
                OperationalError("e3", {}, None),
            ],
        )
        with pytest.raises(OperationalError):
            await with_retry(func, max_retries=3, base_delay=1.0, max_delay=10.0)

        # Two sleeps: base_delay * 2^0 = 1.0, base_delay * 2^1 = 2.0
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == pytest.approx([1.0, 2.0])

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_delay_capped_at_max(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(
            side_effect=[
                OperationalError("e1", {}, None),
                OperationalError("e2", {}, None),
                OperationalError("e3", {}, None),
                OperationalError("e4", {}, None),
            ],
        )
        with pytest.raises(OperationalError):
            await with_retry(func, max_retries=4, base_delay=5.0, max_delay=8.0)

        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # 5*2^0=5, 5*2^1=10->capped 8, 5*2^2=20->capped 8
        assert delays == pytest.approx([5.0, 8.0, 8.0])

    async def test_max_retries_minimum_one(self):
        from toolkit_cost_optimization_engine.core.database import with_retry

        func = AsyncMock(return_value="ok")
        result = await with_retry(func, max_retries=0, base_delay=0.01)
        assert result == "ok"
        assert func.call_count == 1


class TestDatabaseManagerExecuteWithRetry:
    async def test_execute_with_retry_success(self):
        from toolkit_cost_optimization_engine.core.database import DatabaseManager

        mgr = DatabaseManager()
        mgr.execute_raw_sql = AsyncMock(return_value=[("result",)])
        result = await mgr.execute_with_retry("SELECT 1")
        assert result == [("result",)]

    @patch("toolkit_cost_optimization_engine.core.database.asyncio.sleep", new_callable=AsyncMock)
    async def test_execute_with_retry_recovers(self, mock_sleep):
        from toolkit_cost_optimization_engine.core.database import DatabaseManager

        mgr = DatabaseManager()
        mgr.execute_raw_sql = AsyncMock(
            side_effect=[
                OperationalError("transient", {}, None),
                [("ok",)],
            ],
        )
        result = await mgr.execute_with_retry("SELECT 1")
        assert result == [("ok",)]
