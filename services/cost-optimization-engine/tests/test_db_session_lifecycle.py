"""Database session lifecycle tests — init, session creation, commit, rollback, close."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
async def test_db():
    """Set up a test database engine and session factory."""
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

    yield {"engine": test_engine, "session_factory": test_session_factory}

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()

    db_module.engine = orig_engine
    db_module.AsyncSessionLocal = orig_session


class TestSessionLifecycle:
    async def test_session_yields_valid_session(self, test_db):
        """get_db_session returns a working async session."""
        from toolkit_cost_optimization_engine.core.database import get_db_session

        async with get_db_session() as session:
            result = await session.execute(text("SELECT 1"))
            row = result.fetchone()
            assert row[0] == 1

    async def test_session_commit_persists_data(self, test_db):
        """Data committed in one session is visible in a new session."""
        from toolkit_cost_optimization_engine.core.database import get_db_session
        from toolkit_cost_optimization_engine.models.models import (
            CloudAccount as CloudAccountModel,
        )

        # Write — let DB generate UUID
        async with get_db_session() as session:
            account = CloudAccountModel(
                name="Lifecycle Test",
                provider="aws",
                account_id="lifecycle-001",
                region="us-east-1",
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = str(account.id)

        # Read in new session — use account_id field (string) for lookup
        async with get_db_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(CloudAccountModel).where(
                    CloudAccountModel.account_id == "lifecycle-001",
                ),
            )
            found = result.scalar_one_or_none()
            assert found is not None
            assert found.name == "Lifecycle Test"

    async def test_session_rollback_on_error(self, test_db):
        """An exception inside get_db_session triggers rollback."""
        from toolkit_cost_optimization_engine.core.database import get_db_session

        with pytest.raises(RuntimeError, match="Deliberate"):
            async with get_db_session() as session:
                await session.execute(text("SELECT 1"))
                raise RuntimeError("Deliberate test error")

        # Session should still work after rollback
        async with get_db_session() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.fetchone()[0] == 1

    async def test_session_factory_configuration(self, test_db):
        """Session factory has expected settings."""
        from toolkit_cost_optimization_engine.core.database import create_session_factory

        engine = test_db["engine"]
        factory = create_session_factory(engine)
        # Create a session and verify it works
        async with factory() as session:
            assert isinstance(session, AsyncSession)
            # Verify session can execute queries (factory is properly configured)
            result = await session.execute(text("SELECT 1"))
            assert result.fetchone()[0] == 1

    async def test_get_db_generator(self, test_db):
        """get_db FastAPI dependency yields a session."""
        from toolkit_cost_optimization_engine.core.database import get_db

        gen = get_db()
        session = await gen.__anext__()
        assert isinstance(session, AsyncSession)
        result = await session.execute(text("SELECT 1"))
        assert result.fetchone()[0] == 1
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass

    async def test_check_db_connection_healthy(self, test_db):
        """check_db_connection returns True when engine is alive."""
        from toolkit_cost_optimization_engine.core.database import check_db_connection

        assert await check_db_connection() is True

    async def test_multiple_concurrent_sessions(self, test_db):
        """Multiple sessions can operate concurrently without interfering."""
        from toolkit_cost_optimization_engine.core.database import get_db_session

        import asyncio

        results = []

        async def query_in_session(value: int):
            async with get_db_session() as session:
                result = await session.execute(text(f"SELECT {value}"))
                results.append(result.fetchone()[0])

        await asyncio.gather(
            query_in_session(1),
            query_in_session(2),
            query_in_session(3),
        )
        assert sorted(results) == [1, 2, 3]
