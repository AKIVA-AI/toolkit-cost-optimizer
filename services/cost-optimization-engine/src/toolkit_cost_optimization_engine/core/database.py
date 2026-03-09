"""
Database configuration and connection management for Toolkit Cost Optimization Engine.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool

from .config import get_database_settings, get_settings

logger = logging.getLogger(__name__)

Base = declarative_base()

engine = None
AsyncSessionLocal = None


def _build_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://")
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///")
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://")
    return database_url


def create_async_engine_instance():
    """Create async engine based on settings."""
    settings = get_settings()
    database_settings = get_database_settings()
    database_url = _build_async_database_url(database_settings.database_url)
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}

    engine_kwargs = {
        "echo": settings.DEBUG,
        "connect_args": connect_args,
        "future": True,
    }
    if not is_sqlite:
        engine_kwargs["pool_size"] = database_settings.POOL_SIZE
        engine_kwargs["max_overflow"] = database_settings.MAX_OVERFLOW
        engine_kwargs["pool_timeout"] = database_settings.POOL_TIMEOUT
        engine_kwargs["pool_recycle"] = database_settings.POOL_RECYCLE
        engine_kwargs["pool_pre_ping"] = database_settings.POOL_PRE_PING
    else:
        engine_kwargs["poolclass"] = NullPool

    return create_async_engine(database_url, **engine_kwargs)


def create_session_factory(db_engine):
    return async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def init_db() -> None:
    """Initialize database engine and create tables."""
    global engine, AsyncSessionLocal

    if engine is None:
        engine = create_async_engine_instance()
        AsyncSessionLocal = create_session_factory(engine)

    try:
        async with engine.begin() as conn:
            from ..models import models  # noqa: F401
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception as exc:
        logger.error("Failed to initialize database", exc_info=exc)
        raise


async def close_db() -> None:
    """Close database connections."""
    global engine
    if engine is None:
        return
    await engine.dispose()
    logger.info("Database connections closed")
    engine = None


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions."""
    if AsyncSessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception as exc:
            logger.error("Database session error", exc_info=exc)
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency to get a database session."""
    async with get_db_session() as session:
        yield session


async def check_db_connection() -> bool:
    """Check database connection health."""
    if engine is None:
        return False
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database connection check failed", exc_info=exc)
        return False


T = TypeVar("T")


async def with_retry(
    func: Callable[..., T],
    *args,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retryable_exceptions: tuple[type[Exception], ...] = (OperationalError, ConnectionError, OSError),
    **kwargs,
) -> T:
    """Execute an async callable with exponential backoff on transient failures.

    Parameters
    ----------
    func:
        Async callable to execute.
    max_retries:
        Total number of attempts (including the first). Minimum 1.
    base_delay:
        Initial delay in seconds before the first retry.
    max_delay:
        Maximum delay cap in seconds.
    retryable_exceptions:
        Exception types that trigger a retry. All others propagate immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(max(max_retries, 1)):
        try:
            return await func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt + 1 >= max_retries:
                logger.error(
                    "Database operation failed after %d attempts: %s",
                    max_retries,
                    exc,
                )
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "Retryable database error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    # Should not reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]


class DatabaseManager:
    """Database connection manager with health checks and retry support."""

    async def health_check(self) -> dict:
        if engine is None:
            return {"status": "not_initialized"}
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                await result.fetchone()
            return {"status": "healthy"}
        except Exception as exc:
            logger.error("Database health check failed", exc_info=exc)
            return {"status": "unhealthy", "error": str(exc)}

    async def execute_raw_sql(self, query: str, params: dict | None = None) -> list:
        if engine is None:
            raise RuntimeError("Database not initialized. Call init_db() first.")
        async with engine.begin() as conn:
            result = await conn.execute(text(query), params or {})
            return result.fetchall()

    async def execute_with_retry(
        self,
        query: str,
        params: dict | None = None,
        max_retries: int = 3,
    ) -> list:
        """Execute raw SQL with exponential backoff retry on transient errors."""

        async def _execute():
            return await self.execute_raw_sql(query, params)

        return await with_retry(_execute, max_retries=max_retries)


db_manager = DatabaseManager()

