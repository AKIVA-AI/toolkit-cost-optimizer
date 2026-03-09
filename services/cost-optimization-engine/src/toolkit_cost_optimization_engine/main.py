"""
Main FastAPI application for Toolkit Cost Optimization Engine
"""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text

from .api.accounts import router as accounts_router
from .api.cost_data import router as cost_data_router
from .api.recommendations import router as recommendations_router
from .api.schemas import (
    ErrorResponse,
    HealthCheck,
    SystemStatus,
)
from .core.config import get_settings
from .core.database import get_db_session, init_db
from .core.logging_config import configure_logging
from .core.rate_limit import RateLimitMiddleware
from .core.request_id import RequestIDFilter, RequestIDMiddleware
from .core.telemetry import get_tracer, init_telemetry
from .models.models import CloudAccount as CloudAccountModel

# Configure logging (structured JSON by default, controlled by LOG_FORMAT env var)
settings = get_settings()
configure_logging(log_level=settings.LOG_LEVEL, log_format=settings.LOG_FORMAT)
# Install request-ID filter so every log record carries the current request ID
logging.getLogger().addFilter(RequestIDFilter())
logger = logging.getLogger(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter(
    'cost_optimization_requests_total', 'Total requests',
    ['method', 'endpoint', 'status'],
)
REQUEST_DURATION = Histogram('cost_optimization_request_duration_seconds', 'Request duration')
ACTIVE_ACCOUNTS = Gauge('cost_optimization_active_accounts', 'Number of active cloud accounts')
TOTAL_RECOMMENDATIONS = Gauge(
    'cost_optimization_total_recommendations', 'Total number of recommendations',
)
TOTAL_SAVINGS = Gauge('cost_optimization_total_savings_usd', 'Total potential savings in USD')

# Create FastAPI application
app = FastAPI(
    title="Toolkit Cost Optimization Engine",
    description="Enterprise-grade cost optimization and monitoring platform for cloud resources",
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    openapi_url="/openapi.json" if settings.ENVIRONMENT != "production" else None,
)

# Add middleware (order matters: last-added runs first in Starlette)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Rate limiting middleware (uses config values)
app.add_middleware(
    RateLimitMiddleware,
    rate_per_minute=settings.RATE_LIMIT_PER_MINUTE,
    burst=settings.RATE_LIMIT_BURST,
)
# RequestIDMiddleware is pure ASGI — wrap after other middleware so it runs outermost
app.add_middleware(RequestIDMiddleware)

# Include routers
app.include_router(accounts_router)
app.include_router(cost_data_router)
app.include_router(recommendations_router)


# Middleware for metrics and tracing
@app.middleware("http")
async def metrics_middleware(request, call_next):
    """Middleware to collect request metrics and propagate trace spans"""
    start_time = time.time()

    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
    ) as otel_span:
        otel_span.set_attribute("http.method", request.method)
        otel_span.set_attribute("http.url", str(request.url))

        response = await call_next(request)

        # Record metrics
        duration = time.time() - start_time
        REQUEST_DURATION.observe(duration)
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code,
        ).inc()

        otel_span.set_attribute("http.status_code", response.status_code)
        otel_span.set_attribute("http.duration_ms", round(duration * 1000, 2))

    return response


# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize application on startup"""
    logger.info("Starting Toolkit Cost Optimization Engine...")

    try:
        # Initialize telemetry
        init_telemetry()

        # Initialize database
        await init_db()
        logger.info("Database initialized successfully")

        # Update metrics
        await update_metrics()

        logger.info("Toolkit Cost Optimization Engine started successfully")

    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Toolkit Cost Optimization Engine...")


# Utility functions
async def update_metrics():
    """Update Prometheus metrics"""
    try:
        async with get_db_session() as session:
            # Count active accounts
            from sqlalchemy import func, select
            account_count = await session.execute(
                select(func.count(CloudAccountModel.id)).where(
                    CloudAccountModel.is_active.is_(True),
                ),
            )
            ACTIVE_ACCOUNTS.set(account_count.scalar() or 0)

            # Count total recommendations and savings
            from .models.models import OptimizationRecommendation
            rec_count = await session.execute(
                select(func.count(OptimizationRecommendation.id)),
            )
            TOTAL_RECOMMENDATIONS.set(rec_count.scalar() or 0)

            savings_sum = await session.execute(
                select(func.sum(OptimizationRecommendation.monthly_savings)),
            )
            TOTAL_SAVINGS.set(float(savings_sum.scalar() or 0))

    except Exception as e:
        logger.error(f"Failed to update metrics: {e}")


# Health check endpoints
@app.get("/health", response_model=HealthCheck, tags=["Health"])
async def health_check():
    """Health check endpoint"""
    try:
        # Check database connection
        async with get_db_session() as session:
            await session.execute(text("SELECT 1"))
            db_status = {"status": "healthy", "response_time": "< 100ms"}
    except Exception as e:
        db_status = {"status": "unhealthy", "error": str(e)}

    return HealthCheck(
        status="healthy" if db_status["status"] == "healthy" else "unhealthy",
        timestamp=datetime.now(timezone.utc),
        version="1.0.0",
        database=db_status,
    )


@app.get("/status", response_model=SystemStatus, tags=["Health"])
async def system_status():
    """Get system status"""
    try:
        async with get_db_session() as session:
            # Get account statistics
            from sqlalchemy import func, select

            from .models.models import CloudAccount, OptimizationRecommendation

            active_accounts = await session.execute(
                select(func.count(CloudAccount.id)).where(
                    CloudAccount.is_active.is_(True),
                ),
            )

            total_recommendations = await session.execute(
                select(func.count(OptimizationRecommendation.id)),
            )

            total_savings = await session.execute(
                select(func.sum(OptimizationRecommendation.monthly_savings)),
            )

            last_sync = await session.execute(
                select(func.max(CloudAccount.last_sync)).where(
                    CloudAccount.is_connected.is_(True),
                ),
            )

            return SystemStatus(
                status="operational",
                uptime="0d 0h 0m",  # Would calculate actual uptime
                active_accounts=active_accounts.scalar() or 0,
                total_recommendations=total_recommendations.scalar() or 0,
                total_savings=total_savings.scalar() or Decimal('0'),
                last_sync=last_sync.scalar(),
            )

    except Exception as e:
        logger.error(f"Failed to get system status: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get system status",
        ) from e


# Metrics endpoint
@app.get("/metrics", tags=["Health"])
async def metrics():
    """Prometheus metrics endpoint"""
    await update_metrics()
    return Response(generate_latest(), media_type="text/plain; version=0.0.4")


# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            message=str(exc),
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal server error",
            message="An unexpected error occurred",
        ).model_dump(mode="json"),
    )


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint"""
    return {
        "name": "Toolkit Cost Optimization Engine",
        "version": "1.0.0",
        "status": "operational",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# Run application
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
