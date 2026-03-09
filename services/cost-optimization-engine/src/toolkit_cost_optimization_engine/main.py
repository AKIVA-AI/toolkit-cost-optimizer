"""
Main FastAPI application for Toolkit Cost Optimization Engine
"""

import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import uvicorn
from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text

from .api.schemas import (
    CloudAccount,
    CloudAccountCreate,
    CloudAccountUpdate,
    CostAnomalyDetection,
    CostMetrics,
    CostTrendAnalysis,
    ErrorResponse,
    HealthCheck,
    OptimizationRecommendation,
    OptimizationSummary,
    ResourceCostAnalysis,
    SyncResult,
    SystemStatus,
)
from .core.config import get_settings
from .core.cost_tracker import cost_tracker
from .core.credential_encryption import encrypt_credential
from .core.database import get_db_session, init_db
from .core.logging_config import configure_logging
from .core.optimization_engine import optimization_engine
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
# RequestIDMiddleware is pure ASGI — wrap after other middleware so it runs outermost
app.add_middleware(RequestIDMiddleware)


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


# Cloud Account endpoints
@app.post("/api/v1/accounts", response_model=CloudAccount, tags=["Cloud Accounts"])
async def create_cloud_account(account_data: CloudAccountCreate):
    """Create a new cloud account"""
    try:
        async with get_db_session() as session:
            # Check if account already exists
            from sqlalchemy import select
            existing = await session.execute(
                select(CloudAccountModel).where(
                    CloudAccountModel.provider == account_data.provider,
                    CloudAccountModel.account_id == account_data.account_id,
                    CloudAccountModel.region == account_data.region,
                ),
            )
            
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cloud account with this provider,"
                        " account ID, and region already exists"
                    ),
                )
            
            # Create new account with encrypted credentials
            account_dict = account_data.model_dump()
            if account_dict.get("access_key"):
                account_dict["access_key"] = encrypt_credential(account_dict["access_key"])
            if account_dict.get("secret_key"):
                account_dict["secret_key"] = encrypt_credential(account_dict["secret_key"])
            account = CloudAccountModel(**account_dict)
            session.add(account)
            await session.commit()
            await session.refresh(account)
            
            # Update metrics
            await update_metrics()
            
            return CloudAccount.model_validate(account)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to create cloud account",
        ) from e


@app.get("/api/v1/accounts", response_model=list[CloudAccount], tags=["Cloud Accounts"])
async def list_cloud_accounts(
    provider: str | None = Query(None, pattern=r"^(aws|azure|gcp)$"),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List cloud accounts"""
    try:
        async with get_db_session() as session:
            from sqlalchemy import select
            
            query = select(CloudAccountModel)
            
            if provider:
                query = query.where(CloudAccountModel.provider == provider)
            if is_active is not None:
                query = query.where(CloudAccountModel.is_active == is_active)
            
            query = query.offset((page - 1) * page_size).limit(page_size)
            
            result = await session.execute(query)
            accounts = result.scalars().all()
            
            return [CloudAccount.model_validate(account) for account in accounts]
            
    except Exception as e:
        logger.error(f"Failed to list cloud accounts: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to list cloud accounts",
        ) from e


@app.get("/api/v1/accounts/{account_id}", response_model=CloudAccount, tags=["Cloud Accounts"])
async def get_cloud_account(account_id: str):
    """Get a specific cloud account"""
    try:
        async with get_db_session() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()
            
            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")
            
            return CloudAccount.model_validate(account)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get cloud account",
        ) from e


@app.put("/api/v1/accounts/{account_id}", response_model=CloudAccount, tags=["Cloud Accounts"])
async def update_cloud_account(account_id: str, account_data: CloudAccountUpdate):
    """Update a cloud account"""
    try:
        async with get_db_session() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()
            
            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")
            
            # Update account (encrypt credential fields)
            update_data = account_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                if field in ("access_key", "secret_key") and value:
                    value = encrypt_credential(value)
                setattr(account, field, value)
            
            await session.commit()
            await session.refresh(account)
            
            return CloudAccount.model_validate(account)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to update cloud account",
        ) from e


@app.delete("/api/v1/accounts/{account_id}", tags=["Cloud Accounts"])
async def delete_cloud_account(account_id: str):
    """Delete a cloud account"""
    try:
        async with get_db_session() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(CloudAccountModel).where(CloudAccountModel.id == account_id),
            )
            account = result.scalar_one_or_none()
            
            if not account:
                raise HTTPException(status_code=404, detail="Cloud account not found")
            
            await session.delete(account)
            await session.commit()
            
            # Update metrics
            await update_metrics()
            
            return {"message": "Cloud account deleted successfully"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete cloud account: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to delete cloud account",
        ) from e


# Cost Data endpoints
@app.post("/api/v1/accounts/{account_id}/sync", response_model=SyncResult, tags=["Cost Data"])
async def sync_cost_data(
    account_id: str,
    background_tasks: BackgroundTasks,
    days_back: int = Query(30, ge=1, le=365),
):
    """Sync cost data from cloud provider"""
    try:
        # Start sync in background
        background_tasks.add_task(
            cost_tracker.sync_cost_data,
            account_id=account_id,
            days_back=days_back,
        )
        
        return {
            "message": "Cost data sync started",
            "account_id": account_id,
            "days_back": days_back,
        }
        
    except Exception as e:
        logger.error(f"Failed to start cost data sync: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to start cost data sync",
        ) from e


@app.get("/api/v1/accounts/{account_id}/metrics", response_model=CostMetrics, tags=["Cost Data"])
async def get_cost_metrics(
    account_id: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Get cost metrics for a period"""
    try:
        metrics = await cost_tracker.get_cost_metrics(account_id, start_date, end_date)
        return CostMetrics(
            total_cost=metrics.total_cost,
            cost_change=metrics.cost_change,
            cost_change_percentage=metrics.cost_change_percentage,
            daily_average=metrics.daily_average,
            projected_monthly=metrics.projected_monthly,
            services_breakdown=metrics.services_breakdown,
            tags_breakdown=metrics.tags_breakdown,
        )
        
    except Exception as e:
        logger.error(f"Failed to get cost metrics: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get cost metrics",
        ) from e


@app.get(
    "/api/v1/accounts/{account_id}/analysis",
    response_model=list[ResourceCostAnalysis],
    tags=["Cost Data"],
)
async def analyze_resource_costs(account_id: str, days_back: int = Query(30, ge=1, le=365)):
    """Analyze resource costs and efficiency"""
    try:
        analyses = await cost_tracker.analyze_resource_costs(account_id, days_back)
        return [
            ResourceCostAnalysis(
                resource_id=analysis.resource_id,
                resource_type=analysis.resource_type,
                service_name=analysis.service_name,
                current_cost=analysis.current_cost,
                utilization_score=analysis.utilization_score,
                efficiency_score=analysis.efficiency_score,
                optimization_potential=analysis.optimization_potential,
                recommendations=analysis.recommendations,
            )
            for analysis in analyses
        ]
        
    except Exception as e:
        logger.error(f"Failed to analyze resource costs: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to analyze resource costs",
        ) from e


@app.get(
    "/api/v1/accounts/{account_id}/anomalies",
    response_model=list[CostAnomalyDetection],
    tags=["Cost Data"],
)
async def detect_cost_anomalies(account_id: str, days_back: int = Query(30, ge=1, le=365)):
    """Detect cost anomalies"""
    try:
        anomalies = await cost_tracker.detect_cost_anomalies(account_id, days_back)
        return [
            CostAnomalyDetection(
                date=anomaly['date'],
                actual_cost=anomaly['actual_cost'],
                expected_cost=anomaly['expected_cost'],
                deviation_percentage=anomaly['deviation_percentage'],
                severity=anomaly['severity'],
            )
            for anomaly in anomalies
        ]
        
    except Exception as e:
        logger.error(f"Failed to detect cost anomalies: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to detect cost anomalies",
        ) from e


@app.get(
    "/api/v1/accounts/{account_id}/trends",
    response_model=list[CostTrendAnalysis],
    tags=["Cost Data"],
)
async def get_cost_trends(account_id: str, days_back: int = Query(90, ge=1, le=365)):
    """Get cost trends analysis"""
    try:
        trends = await cost_tracker.get_cost_trends(account_id, days_back)
        return [
            CostTrendAnalysis(
                period_start=trend['period_start'],
                period_end=trend['period_end'],
                total_cost=trend['total_cost'],
                cost_change=trend['cost_change'],
                cost_change_percentage=trend['cost_change_percentage'],
                trend_direction=trend['trend_direction'],
                trend_strength=trend['trend_strength'],
            )
            for trend in trends
        ]
        
    except Exception as e:
        logger.error(f"Failed to get cost trends: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get cost trends",
        ) from e


# Optimization Recommendations endpoints
@app.post(
    "/api/v1/accounts/{account_id}/recommendations/generate",
    response_model=OptimizationSummary,
    tags=["Recommendations"],
)
async def generate_recommendations(
    account_id: str,
    background_tasks: BackgroundTasks,
    analysis_period: int = Query(30, ge=1, le=365),
):
    """Generate optimization recommendations"""
    try:
        # Generate recommendations in background
        background_tasks.add_task(
            optimization_engine.generate_recommendations,
            account_id=account_id,
            analysis_period=analysis_period,
        )
        
        return {
            "message": "Recommendation generation started",
            "account_id": account_id,
            "analysis_period": analysis_period,
        }
        
    except Exception as e:
        logger.error(f"Failed to start recommendation generation: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to start recommendation generation",
        ) from e


@app.get(
    "/api/v1/accounts/{account_id}/recommendations",
    response_model=list[OptimizationRecommendation],
    tags=["Recommendations"],
)
async def get_recommendations(
    account_id: str,
    status: str | None = Query(None, pattern=r"^(pending|approved|rejected|implemented)$"),
    rec_type: str | None = Query(None, alias="type"),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    """Get optimization recommendations"""
    try:
        recommendations = await optimization_engine.get_recommendations(
            account_id=account_id,
            status=status,
            limit=limit,
        )
        
        return [
            OptimizationRecommendation(**rec) for rec in recommendations
        ]
        
    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get recommendations",
        ) from e


@app.put("/api/v1/recommendations/{recommendation_id}", tags=["Recommendations"])
async def update_recommendation_status(
    recommendation_id: str,
    status_data: dict = Body(...),
    implemented_by: str | None = None,
):
    """Update recommendation status"""
    try:
        success = await optimization_engine.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=status_data.get("status"),
            implemented_by=implemented_by,
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        
        # Update metrics
        await update_metrics()
        
        return {"message": "Recommendation status updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update recommendation status: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update recommendation status",
        ) from e


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

