"""Cost data endpoints router."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..api.schemas import (
    CostAnomalyDetection,
    CostMetrics,
    CostTrendAnalysis,
    ResourceCostAnalysis,
    SyncResult,
)
from ..core.cost_tracker import cost_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/accounts/{account_id}", tags=["Cost Data"])


@router.post("/sync", response_model=SyncResult)
async def sync_cost_data(
    account_id: str,
    background_tasks: BackgroundTasks,
    days_back: int = Query(30, ge=1, le=365),
):
    """Sync cost data from cloud provider."""
    try:
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
            status_code=500,
            detail="Failed to start cost data sync",
        ) from e


@router.get("/metrics", response_model=CostMetrics)
async def get_cost_metrics(
    account_id: str,
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Get cost metrics for a period."""
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
            status_code=500,
            detail="Failed to get cost metrics",
        ) from e


@router.get("/analysis", response_model=list[ResourceCostAnalysis])
async def analyze_resource_costs(account_id: str, days_back: int = Query(30, ge=1, le=365)):
    """Analyze resource costs and efficiency."""
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
            status_code=500,
            detail="Failed to analyze resource costs",
        ) from e


@router.get("/anomalies", response_model=list[CostAnomalyDetection])
async def detect_cost_anomalies(account_id: str, days_back: int = Query(30, ge=1, le=365)):
    """Detect cost anomalies."""
    try:
        anomalies = await cost_tracker.detect_cost_anomalies(account_id, days_back)
        return [
            CostAnomalyDetection(
                date=anomaly["date"],
                actual_cost=anomaly["actual_cost"],
                expected_cost=anomaly["expected_cost"],
                deviation_percentage=anomaly["deviation_percentage"],
                severity=anomaly["severity"],
            )
            for anomaly in anomalies
        ]

    except Exception as e:
        logger.error(f"Failed to detect cost anomalies: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to detect cost anomalies",
        ) from e


@router.get("/trends", response_model=list[CostTrendAnalysis])
async def get_cost_trends(account_id: str, days_back: int = Query(90, ge=1, le=365)):
    """Get cost trends analysis."""
    try:
        trends = await cost_tracker.get_cost_trends(account_id, days_back)
        return [
            CostTrendAnalysis(
                period_start=trend["period_start"],
                period_end=trend["period_end"],
                total_cost=trend["total_cost"],
                cost_change=trend["cost_change"],
                cost_change_percentage=trend["cost_change_percentage"],
                trend_direction=trend["trend_direction"],
                trend_strength=trend["trend_strength"],
            )
            for trend in trends
        ]

    except Exception as e:
        logger.error(f"Failed to get cost trends: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get cost trends",
        ) from e
