"""
Configuration management for Toolkit Cost Optimization Engine
"""

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # Application
    APP_NAME: str = "Toolkit Cost Optimization Engine"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8005
    
    # Security
    SECRET_KEY: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 30
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Database
    DATABASE_URL: str = "postgresql://user:password@localhost:5436/cost_optimization_engine"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 30
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 3600
    
    # Redis
    REDIS_URL: str = "redis://localhost:6382/0"
    REDIS_CACHE_TTL: int = 3600
    REDIS_SESSION_TTL: int = 86400
    
    # Cost Tracking
    COST_TRACKING_INTERVAL: int = 300  # 5 minutes
    COST_RETENTION_DAYS: int = 365
    COST_AGGREGATION_INTERVALS: list[str] = ["hourly", "daily", "weekly", "monthly"]
    
    # Cloud Provider APIs
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_DEFAULT_REGION: str = "us-east-1"
    
    AZURE_CLIENT_ID: str | None = None
    AZURE_CLIENT_SECRET: str | None = None
    AZURE_TENANT_ID: str | None = None
    AZURE_SUBSCRIPTION_ID: str | None = None
    
    GCP_PROJECT_ID: str | None = None
    GCP_SERVICE_ACCOUNT_KEY: str | None = None
    
    # Monitoring
    PROMETHEUS_URL: str = "http://localhost:9095"
    GRAFANA_URL: str = "http://localhost:3005"
    
    # Optimization
    OPTIMIZATION_CHECK_INTERVAL: int = 3600  # 1 hour
    OPTIMIZATION_CONFIDENCE_THRESHOLD: float = 0.7
    MAX_OPTIMIZATION_RECOMMENDATIONS: int = 50
    
    # Forecasting
    FORECAST_HORIZON_DAYS: int = 30
    FORECAST_MODEL_UPDATE_INTERVAL: int = 86400  # 24 hours
    FORECAST_CONFIDENCE_THRESHOLD: float = 0.8
    
    # Budget Management
    BUDGET_ALERT_THRESHOLD: float = 0.8  # 80%
    BUDGET_CRITICAL_THRESHOLD: float = 0.95  # 95%
    BUDGET_RETENTION_YEARS: int = 5
    
    # Notifications
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    
    SLACK_WEBHOOK_URL: str | None = None
    TEAMS_WEBHOOK_URL: str | None = None
    
    # File Storage
    STORAGE_TYPE: str = "local"  # local, s3, azure, gcs
    STORAGE_PATH: str = "./data"
    
    # S3 Storage (if using S3)
    S3_BUCKET: str | None = None
    S3_REGION: str = "us-east-1"
    
    # Azure Storage (if using Azure)
    AZURE_STORAGE_ACCOUNT: str | None = None
    AZURE_STORAGE_CONTAINER: str | None = None
    
    # GCS Storage (if using GCS)
    GCS_BUCKET: str | None = None
    GCS_PROJECT: str | None = None
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    LOG_FILE: str | None = None
    
    # Performance
    MAX_WORKERS: int = 4
    WORKER_CONNECTIONS: int = 1000
    KEEPALIVE_TIMEOUT: int = 65
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 100
    RATE_LIMIT_BURST: int = 200
    
    @field_validator("CORS_ORIGINS", "COST_AGGREGATION_INTERVALS", mode="before")
    @classmethod
    def parse_csv_list(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
    
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def validate_database_url(cls, value):
        if not value:
            raise ValueError("DATABASE_URL is required")
        return value
    
    @field_validator("SECRET_KEY", mode="before")
    @classmethod
    def validate_secret_key(cls, value):
        if not value or not value.strip():
            if os.getenv("ENVIRONMENT") == "production":
                raise ValueError("SECRET_KEY must be set in production")
        return value

    @field_validator("JWT_SECRET_KEY", mode="before")
    @classmethod
    def validate_jwt_secret_key(cls, value):
        if not value or not value.strip():
            if os.getenv("ENVIRONMENT") == "production":
                raise ValueError("JWT_SECRET_KEY must be set in production")
        return value


class DatabaseSettings(BaseSettings):
    """Database-specific settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # Connection URL (optional override)
    DATABASE_URL: str | None = None

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5436
    POSTGRES_DB: str = "cost_optimization_engine"
    POSTGRES_USER: str = "akiva"
    POSTGRES_PASSWORD: str = ""
    
    # Connection Pool
    POOL_SIZE: int = 20
    MAX_OVERFLOW: int = 30
    POOL_TIMEOUT: int = 30
    POOL_RECYCLE: int = 3600
    POOL_PRE_PING: bool = True
    
    # SSL
    DB_SSL_MODE: str = "prefer"
    DB_SSL_CERT: str | None = None
    DB_SSL_KEY: str | None = None
    DB_SSL_CA: str | None = None
    
    @property
    def database_url(self) -> str:
        """Construct database URL"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


class RedisSettings(BaseSettings):
    """Redis-specific settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6382
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    REDIS_MAX_CONNECTIONS: int = 100
    
    # Cache settings
    CACHE_TTL: int = 3600
    SESSION_TTL: int = 86400
    LOCK_TTL: int = 300
    
    @property
    def redis_url(self) -> str:
        """Construct Redis URL"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


class CloudProviderSettings(BaseSettings):
    """Cloud provider settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # AWS
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    AWS_DEFAULT_REGION: str = "us-east-1"
    AWS_COST_EXPLOROR_ENABLED: bool = True
    
    # Azure
    AZURE_CLIENT_ID: str | None = None
    AZURE_CLIENT_SECRET: str | None = None
    AZURE_TENANT_ID: str | None = None
    AZURE_SUBSCRIPTION_ID: str | None = None
    AZURE_COST_MANAGEMENT_ENABLED: bool = True
    
    # GCP
    GCP_PROJECT_ID: str | None = None
    GCP_SERVICE_ACCOUNT_KEY: str | None = None
    GCP_COST_MANAGEMENT_ENABLED: bool = True
    
    # Common
    CLOUD_DATA_REFRESH_INTERVAL: int = 3600  # 1 hour
    CLOUD_DATA_RETENTION_DAYS: int = 90


class OptimizationSettings(BaseSettings):
    """Optimization engine settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # General
    OPTIMIZATION_ENABLED: bool = True
    OPTIMIZATION_INTERVAL: int = 3600  # 1 hour
    OPTIMIZATION_CONFIDENCE_THRESHOLD: float = 0.7
    MAX_RECOMMENDATIONS_PER_RUN: int = 50
    
    # Cost Analysis
    COST_ANOMALY_THRESHOLD: float = 2.0  # 2x normal cost
    COST_TREND_ANALYSIS_DAYS: int = 30
    COST_BASELINE_PERIOD_DAYS: int = 7
    
    # Resource Optimization
    RESOURCE_UTILIZATION_THRESHOLD: float = 0.3  # 30%
    IDLE_RESOURCE_TIMEOUT_HOURS: int = 24
    RIGHT_SIZING_CONFIDENCE_THRESHOLD: float = 0.8
    
    # Scheduling
    SCHEDULE_OPTIMIZATION_ENABLED: bool = True
    OFF_HOURS_START: str = "18:00"
    OFF_HOURS_END: str = "08:00"
    WEEKEND_OPTIMIZATION_ENABLED: bool = True


class ForecastingSettings(BaseSettings):
    """Cost forecasting settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # General
    FORECASTING_ENABLED: bool = True
    FORECAST_HORIZON_DAYS: int = 30
    FORECAST_MODEL_UPDATE_INTERVAL: int = 86400  # 24 hours
    
    # Models
    ARIMA_ENABLED: bool = True
    PROPHET_ENABLED: bool = True
    LINEAR_REGRESSION_ENABLED: bool = True
    ENSEMBLE_ENABLED: bool = True
    
    # Accuracy
    MIN_ACCURACY_THRESHOLD: float = 0.7
    CONFIDENCE_INTERVAL_LEVEL: float = 0.95
    SEASONALITY_DETECTION_ENABLED: bool = True
    
    # Data
    TRAINING_DATA_DAYS: int = 90
    FEATURE_ENGINEERING_ENABLED: bool = True
    EXTERNAL_FACTORS_ENABLED: bool = True


class NotificationSettings(BaseSettings):
    """Notification settings"""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )
    
    # Email
    EMAIL_ENABLED: bool = False
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    EMAIL_FROM: str | None = None
    
    # Slack
    SLACK_ENABLED: bool = False
    SLACK_WEBHOOK_URL: str | None = None
    SLACK_CHANNEL: str = "#cost-alerts"
    
    # Teams
    TEAMS_ENABLED: bool = False
    TEAMS_WEBHOOK_URL: str | None = None
    
    # General
    NOTIFICATION_COOLDOWN_MINUTES: int = 60
    BATCH_NOTIFICATIONS: bool = True
    NOTIFICATION_RETENTION_DAYS: int = 30


@lru_cache
def get_settings() -> Settings:
    """Get cached settings"""
    return Settings()


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Get cached database settings"""
    return DatabaseSettings()


@lru_cache
def get_redis_settings() -> RedisSettings:
    """Get cached Redis settings"""
    return RedisSettings()


@lru_cache
def get_cloud_provider_settings() -> CloudProviderSettings:
    """Get cached cloud provider settings"""
    return CloudProviderSettings()


@lru_cache
def get_optimization_settings() -> OptimizationSettings:
    """Get cached optimization settings"""
    return OptimizationSettings()


@lru_cache
def get_forecasting_settings() -> ForecastingSettings:
    """Get cached forecasting settings"""
    return ForecastingSettings()


@lru_cache
def get_notification_settings() -> NotificationSettings:
    """Get cached notification settings"""
    return NotificationSettings()


# Settings instances
settings = get_settings()
database_settings = get_database_settings()
redis_settings = get_redis_settings()
cloud_provider_settings = get_cloud_provider_settings()
optimization_settings = get_optimization_settings()
forecasting_settings = get_forecasting_settings()
notification_settings = get_notification_settings()

