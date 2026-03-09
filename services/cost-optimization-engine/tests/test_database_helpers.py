from toolkit_cost_optimization_engine.core.database import _build_async_database_url


def test_build_async_database_url_postgres():
    url = "postgresql://user:pass@localhost:5432/dbname"
    assert _build_async_database_url(url) == "postgresql+asyncpg://user:pass@localhost:5432/dbname"


def test_build_async_database_url_sqlite():
    url = "sqlite:///./local.db"
    assert _build_async_database_url(url) == "sqlite+aiosqlite:///./local.db"


def test_build_async_database_url_already_async():
    url = "sqlite+aiosqlite:///./local.db"
    assert _build_async_database_url(url) == "sqlite+aiosqlite:///./local.db"
