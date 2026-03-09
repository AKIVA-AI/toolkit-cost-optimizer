from toolkit_cost_optimization_engine.core.config import DatabaseSettings, Settings


def test_settings_parse_csv_list():
    settings = Settings(CORS_ORIGINS="http://localhost:3000, http://localhost:8080")
    assert settings.CORS_ORIGINS == ["http://localhost:3000", "http://localhost:8080"]


def test_database_url_override():
    database_settings = DatabaseSettings(DATABASE_URL="postgresql://override/db")
    assert database_settings.database_url == "postgresql://override/db"
