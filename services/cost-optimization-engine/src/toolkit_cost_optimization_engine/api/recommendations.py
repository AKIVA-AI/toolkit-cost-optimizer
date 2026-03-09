"""Optimization recommendations router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from sqlalchemy import desc, func, select

from ..api.schemas import (
    OptimizationRecommendation,
    OptimizationRecommendationUpdate,
    OptimizationSummary,
    PaginatedRecommendations,
)
from ..core.database import get_db_session
from ..core.optimization_engine import optimization_engine
from ..models.models import OptimizationRecommendation as RecModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Recommendations"])


@router.post(
    "/api/v1/accounts/{account_id}/recommendations/generate",
    response_model=OptimizationSummary,
)
async def generate_recommendations(
    account_id: str,
    background_tasks: BackgroundTasks,
    analysis_period: int = Query(30, ge=1, le=365),
):
    """Generate optimization recommendations."""
    try:
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


@router.get(
    "/api/v1/accounts/{account_id}/recommendations",
    response_model=PaginatedRecommendations,
)
async def get_recommendations(
    account_id: str,
    status: str | None = Query(None, pattern=r"^(pending|approved|rejected|implemented)$"),
    rec_type: str | None = Query(None, alias="type"),
    priority: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """Get optimization recommendations with pagination metadata."""
    try:
        async with get_db_session() as session:
            base_query = select(RecModel).where(
                RecModel.cloud_account_id == account_id,
            )
            if status:
                base_query = base_query.where(RecModel.status == status)

            count_query = select(func.count()).select_from(base_query.subquery())
            total_result = await session.execute(count_query)
            total = total_result.scalar() or 0

            paginated = (
                base_query.order_by(desc(RecModel.monthly_savings))
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(paginated)
            recs = result.scalars().all()

            total_pages = max((total + page_size - 1) // page_size, 1)

            return PaginatedRecommendations(
                items=[OptimizationRecommendation.model_validate(r) for r in recs],
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )

    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get recommendations",
        ) from e


@router.put("/api/v1/recommendations/{recommendation_id}")
async def update_recommendation_status(
    recommendation_id: str,
    status_data: OptimizationRecommendationUpdate = Body(...),
):
    """Update recommendation status."""
    try:
        success = await optimization_engine.update_recommendation_status(
            recommendation_id=recommendation_id,
            status=status_data.status,
            implemented_by=None,
        )

        if not success:
            raise HTTPException(status_code=404, detail="Recommendation not found")

        return {"message": "Recommendation status updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update recommendation status: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update recommendation status",
        ) from e
