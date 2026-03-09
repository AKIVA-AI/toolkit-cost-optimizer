"""
Optimization recommendations engine for Toolkit Cost Optimization Engine
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

import numpy as np
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import optimization_settings
from ..core.database import get_db_session
from ..models.models import CloudAccount, CostData, OptimizationRecommendation, ResourceUsage

logger = logging.getLogger(__name__)


class RecommendationType(Enum):
    """Types of optimization recommendations"""
    RIGHT_SIZE = "right_size"
    SCHEDULE = "schedule"
    TERMINATE = "terminate"
    RESERVED_INSTANCES = "reserved_instances"
    SAVINGS_PLAN = "savings_plan"
    SPOT_INSTANCES = "spot_instances"
    STORAGE_OPTIMIZATION = "storage_optimization"
    NETWORK_OPTIMIZATION = "network_optimization"
    AUTOSCALING = "autoscaling"
    CLEANUP = "cleanup"


class Priority(Enum):
    """Recommendation priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Effort(Enum):
    """Implementation effort levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class OptimizationContext:
    """Context for optimization analysis"""
    account_id: str
    provider: str
    region: str
    analysis_period: int  # days
    current_date: date


@dataclass
class RecommendationData:
    """Data structure for optimization recommendations"""
    type: RecommendationType
    title: str
    description: str
    resource_id: str | None
    resource_type: str | None
    service_name: str | None
    current_cost: Decimal | None
    projected_cost: Decimal | None
    monthly_savings: Decimal
    savings_percentage: float
    effort: Effort
    risk_level: str
    confidence_score: float
    priority: Priority
    implementation_steps: list[str]
    rollback_plan: str
    analysis_data: dict[str, Any]


class OptimizationRuleBase:
    """Base class for optimization rules"""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
    
    async def analyze(self, context: OptimizationContext) -> list[RecommendationData]:
        """Analyze and generate recommendations"""
        raise NotImplementedError
    
    def calculate_confidence(self, data_points: int, data_quality: float) -> float:
        """Calculate confidence score based on data availability and quality"""
        base_confidence = min(data_points / 30, 1.0)  # More data points = higher confidence
        quality_factor = data_quality  # 0-1 scale
        return base_confidence * quality_factor


class RightSizingRule(OptimizationRuleBase):
    """Rule for right-sizing underutilized resources"""
    
    def __init__(self):
        super().__init__(
            "Right Sizing",
            "Identify underutilized resources that can be downsized",
        )
    
    async def analyze(self, context: OptimizationContext) -> list[RecommendationData]:
        """Analyze resource utilization for right-sizing opportunities"""
        recommendations = []
        
        async with get_db_session() as session:
            try:
                # Get resource utilization data
                utilization_query = select(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                    func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
                    func.avg(ResourceUsage.memory_utilization).label('avg_memory'),
                    func.max(ResourceUsage.cpu_utilization).label('max_cpu'),
                    func.max(ResourceUsage.memory_utilization).label('max_memory'),
                    func.count(ResourceUsage.id).label('data_points'),
                ).where(
                    and_(
                        ResourceUsage.cloud_account_id == context.account_id,
                        ResourceUsage.timestamp >= datetime.combine(
                            context.current_date - timedelta(days=context.analysis_period),
                            datetime.min.time(),
                        ),
                        ResourceUsage.status == 'running',
                    ),
                ).group_by(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                ).having(
                    func.count(ResourceUsage.id) >= 7,  # At least 7 data points
                )
                
                result = await session.execute(utilization_query)
                
                for row in result:
                    avg_cpu = float(row.avg_cpu or 0)
                    avg_memory = float(row.avg_memory or 0)
                    max_cpu = float(row.max_cpu or 0)
                    max_memory = float(row.max_memory or 0)
                    data_points = row.data_points
                    
                    # Check if resource is underutilized
                    if (
                        avg_cpu < optimization_settings.RESOURCE_UTILIZATION_THRESHOLD
                        and avg_memory < optimization_settings.RESOURCE_UTILIZATION_THRESHOLD
                    ):
                        # Get current cost
                        cost_query = select(
                            func.sum(CostData.cost_amount).label('total_cost'),
                        ).where(
                            and_(
                                CostData.cloud_account_id == context.account_id,
                                CostData.resource_id == row.resource_id,
                                CostData.usage_start_date >= (
                                    context.current_date - timedelta(days=30)
                                ),
                            ),
                        )

                        cost_result = await session.execute(cost_query)
                        current_cost = cost_result.scalar() or Decimal('0')

                        if current_cost > 0:
                            # Deterministic savings estimate based on utilization
                            # Lower utilization => higher savings potential (30-50% range)
                            avg_util = (avg_cpu + avg_memory) / 2
                            # Map utilization 0..threshold to savings 0.50..0.30
                            threshold = optimization_settings.RESOURCE_UTILIZATION_THRESHOLD
                            util_ratio = min(avg_util / (threshold * 100), 1.0) if threshold > 0 else 0
                            savings_percentage = 0.50 - (util_ratio * 0.20)  # 0.50 at 0% util, 0.30 at threshold
                            monthly_savings = current_cost * Decimal(str(round(savings_percentage, 4)))
                            
                            # Determine target instance type
                            target_type = self._suggest_target_instance(
                                row.resource_type, avg_cpu, avg_memory,
                            )
                            
                            recommendation = RecommendationData(
                                type=RecommendationType.RIGHT_SIZE,
                                title=f"Right-size {row.resource_type} {row.resource_id}",
                                description=(
                                    f"Resource has low CPU ({avg_cpu:.1f}%)"
                                    f" and memory ({avg_memory:.1f}%)"
                                    f" utilization. Consider downsizing"
                                    f" to {target_type}."
                                ),
                                resource_id=row.resource_id,
                                resource_type=row.resource_type,
                                service_name=row.service_name,
                                current_cost=current_cost,
                                projected_cost=current_cost - monthly_savings,
                                monthly_savings=monthly_savings,
                                savings_percentage=float(savings_percentage * 100),
                                effort=Effort.MEDIUM,
                                risk_level="low",
                                confidence_score=self.calculate_confidence(data_points, 0.8),
                                priority=Priority.MEDIUM if monthly_savings > 50 else Priority.LOW,
                                implementation_steps=[
                                    f"Analyze workload patterns for {row.resource_id}",
                                    f"Select appropriate {target_type} instance",
                                    "Schedule maintenance window",
                                    "Create backup/snapshot",
                                    "Resize instance",
                                    "Monitor performance after resize",
                                ],
                                rollback_plan=(
                                    f"Resize back to original"
                                    f" {row.resource_type}"
                                    f" if performance issues occur"
                                ),
                                analysis_data={
                                    'avg_cpu': avg_cpu,
                                    'avg_memory': avg_memory,
                                    'max_cpu': max_cpu,
                                    'max_memory': max_memory,
                                    'data_points': data_points,
                                    'target_type': target_type,
                                },
                            )
                            
                            recommendations.append(recommendation)
                
                return recommendations
                
            except Exception as e:
                logger.error(f"Failed to analyze right-sizing opportunities: {e}")
                return []
    
    def _suggest_target_instance(self, current_type: str, avg_cpu: float, avg_memory: float) -> str:
        """Suggest target instance type based on utilization"""
        # Simplified logic - would use actual instance type mappings
        if 'large' in current_type.lower():
            if avg_cpu < 20 and avg_memory < 20:
                return f"{current_type.replace('large', 'medium')}"
            elif avg_cpu < 10 and avg_memory < 10:
                return f"{current_type.replace('large', 'small')}"
        elif 'medium' in current_type.lower():
            if avg_cpu < 10 and avg_memory < 10:
                return f"{current_type.replace('medium', 'small')}"
        
        return f"{current_type} (optimized)"


class SchedulingRule(OptimizationRuleBase):
    """Rule for scheduling resources to run only when needed"""
    
    def __init__(self):
        super().__init__(
            "Resource Scheduling",
            "Identify resources that can be scheduled to run only during business hours",
        )
    
    async def analyze(self, context: OptimizationContext) -> list[RecommendationData]:
        """Analyze resource usage patterns for scheduling opportunities"""
        recommendations = []
        
        async with get_db_session() as session:
            try:
                # Get resources with predictable usage patterns
                pattern_query = select(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                    func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
                    func.count(ResourceUsage.id).label('data_points'),
                ).where(
                    and_(
                        ResourceUsage.cloud_account_id == context.account_id,
                        ResourceUsage.timestamp >= datetime.combine(
                            context.current_date - timedelta(days=context.analysis_period),
                            datetime.min.time(),
                        ),
                        ResourceUsage.status == 'running',
                    ),
                ).group_by(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                ).having(
                    and_(
                        func.count(ResourceUsage.id) >= 168,  # At least 1 week of hourly data
                        func.avg(ResourceUsage.cpu_utilization) < 10,  # Low overall utilization
                    ),
                )
                
                result = await session.execute(pattern_query)
                
                for row in result:
                    # Get hourly usage pattern
                    hourly_pattern = await self._analyze_hourly_pattern(
                        session, context.account_id, row.resource_id,
                    )
                    
                    if self._has_business_hours_pattern(hourly_pattern):
                        # Get current cost
                        cost_query = select(
                            func.sum(CostData.cost_amount).label('total_cost'),
                        ).where(
                            and_(
                                CostData.cloud_account_id == context.account_id,
                                CostData.resource_id == row.resource_id,
                                CostData.usage_start_date >= (
                                    context.current_date - timedelta(days=30)
                                ),
                            ),
                        )

                        cost_result = await session.execute(cost_query)
                        current_cost = cost_result.scalar() or Decimal('0')

                        if current_cost > 0:
                            # Calculate savings (12h/day instead of 24)
                            savings_percentage = 0.5
                            monthly_savings = current_cost * Decimal(
                                str(savings_percentage),
                            )

                            recommendation = RecommendationData(
                                type=RecommendationType.SCHEDULE,
                                title=(
                                    f"Schedule {row.resource_type}"
                                    f" {row.resource_id}"
                                ),
                                description=(
                                    "Resource shows business hours usage"
                                    " pattern. Schedule to run only"
                                    " during business hours (8am-6pm)"
                                    " to save 50% of costs."
                                ),
                                resource_id=row.resource_id,
                                resource_type=row.resource_type,
                                service_name=row.service_name,
                                current_cost=current_cost,
                                projected_cost=current_cost - monthly_savings,
                                monthly_savings=monthly_savings,
                                savings_percentage=50.0,
                                effort=Effort.LOW,
                                risk_level="low",
                                confidence_score=self.calculate_confidence(
                                    row.data_points, 0.7,
                                ),
                                priority=(
                                    Priority.MEDIUM
                                    if monthly_savings > 100
                                    else Priority.LOW
                                ),
                                implementation_steps=[
                                    "Create start/stop schedule for business hours",
                                    "Configure auto-start at 8am and auto-stop at 6pm",
                                    "Set up weekend shutdown",
                                    "Test schedule with monitoring",
                                    "Implement automated scheduling",
                                ],
                                rollback_plan=(
                                    "Disable scheduling and run resource"
                                    " 24/7 if issues occur"
                                ),
                                analysis_data={
                                    'hourly_pattern': hourly_pattern,
                                    'avg_cpu': float(row.avg_cpu),
                                    'data_points': row.data_points,
                                },
                            )
                            
                            recommendations.append(recommendation)
                
                return recommendations
                
            except Exception as e:
                logger.error(f"Failed to analyze scheduling opportunities: {e}")
                return []
    
    async def _analyze_hourly_pattern(
        self, session: AsyncSession, account_id: str, resource_id: str,
    ) -> dict[int, float]:
        """Analyze hourly usage pattern for a resource"""
        hourly_query = select(
            func.extract('hour', ResourceUsage.timestamp).label('hour'),
            func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
        ).where(
            and_(
                ResourceUsage.cloud_account_id == account_id,
                ResourceUsage.resource_id == resource_id,
                ResourceUsage.timestamp >= datetime.combine(
                    date.today() - timedelta(days=14),
                    datetime.min.time(),
                ),
            ),
        ).group_by('hour').order_by('hour')
        
        result = await session.execute(hourly_query)
        return {int(row.hour): float(row.avg_cpu or 0) for row in result}
    
    def _has_business_hours_pattern(self, hourly_pattern: dict[int, float]) -> bool:
        """Check if usage pattern indicates business hours usage"""
        # Check if usage is significantly higher during business hours (8am-6pm)
        business_hours = list(range(8, 19))  # 8am to 6pm
        non_business_hours = [h for h in range(24) if h not in business_hours]
        
        business_avg = np.mean([hourly_pattern.get(h, 0) for h in business_hours])
        non_business_avg = np.mean([hourly_pattern.get(h, 0) for h in non_business_hours])
        
        return (
            business_avg > 5
            and non_business_avg < 2
            and business_avg > non_business_avg * 2
        )


class TerminationRule(OptimizationRuleBase):
    """Rule for identifying unused resources for termination"""
    
    def __init__(self):
        super().__init__(
            "Resource Termination",
            "Identify unused or idle resources that can be terminated",
        )
    
    async def analyze(self, context: OptimizationContext) -> list[RecommendationData]:
        """Analyze resources for termination opportunities"""
        recommendations = []
        
        async with get_db_session() as session:
            try:
                # Find resources with no recent activity
                idle_days = (
                    optimization_settings.IDLE_RESOURCE_TIMEOUT_HOURS // 24
                )
                idle_threshold = datetime.combine(
                    context.current_date - timedelta(days=idle_days),
                    datetime.min.time(),
                )
                
                idle_query = select(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                    func.max(ResourceUsage.timestamp).label('last_activity'),
                    func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
                    func.count(ResourceUsage.id).label('data_points'),
                ).where(
                    and_(
                        ResourceUsage.cloud_account_id == context.account_id,
                        ResourceUsage.timestamp >= idle_threshold,
                        ResourceUsage.status == 'running',
                    ),
                ).group_by(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                ).having(
                    and_(
                        func.max(ResourceUsage.timestamp) < idle_threshold,
                        func.avg(ResourceUsage.cpu_utilization) < 1,  # Near-zero CPU usage
                    ),
                )
                
                result = await session.execute(idle_query)
                
                for row in result:
                    # Get current cost
                    cost_query = select(
                        func.sum(CostData.cost_amount).label('total_cost'),
                    ).where(
                        and_(
                            CostData.cloud_account_id == context.account_id,
                            CostData.resource_id == row.resource_id,
                            CostData.usage_start_date >= context.current_date - timedelta(days=30),
                        ),
                    )
                    
                    cost_result = await session.execute(cost_query)
                    current_cost = cost_result.scalar() or Decimal('0')
                    
                    if current_cost > 0:
                        # Calculate savings (100% since resource will be terminated)
                        monthly_savings = current_cost
                        days_idle = (context.current_date - row.last_activity.date()).days
                        
                        recommendation = RecommendationData(
                            type=RecommendationType.TERMINATE,
                            title=f"Terminate idle {row.resource_type} {row.resource_id}",
                            description=(
                                f"Resource has been idle for"
                                f" {days_idle} days with near-zero CPU"
                                f" usage ({float(row.avg_cpu):.1f}%)."
                                f" Consider termination."
                            ),
                            resource_id=row.resource_id,
                            resource_type=row.resource_type,
                            service_name=row.service_name,
                            current_cost=current_cost,
                            projected_cost=Decimal('0'),
                            monthly_savings=monthly_savings,
                            savings_percentage=100.0,
                            effort=Effort.LOW,
                            risk_level="medium",
                            confidence_score=self.calculate_confidence(row.data_points, 0.9),
                            priority=Priority.HIGH if monthly_savings > 200 else Priority.MEDIUM,
                            implementation_steps=[
                                "Verify resource is not needed",
                                "Create final backup if needed",
                                "Document termination reason",
                                "Terminate resource",
                                "Clean up associated resources",
                            ],
                            rollback_plan=(
                                "Resource cannot be easily restored"
                                " after termination - requires"
                                " recreation from backup"
                            ),
                            analysis_data={
                                'last_activity': row.last_activity.isoformat(),
                                'days_idle': days_idle,
                                'avg_cpu': float(row.avg_cpu),
                                'data_points': row.data_points,
                            },
                        )
                        
                        recommendations.append(recommendation)
                
                return recommendations
                
            except Exception as e:
                logger.error(f"Failed to analyze termination opportunities: {e}")
                return []


class ReservedInstancesRule(OptimizationRuleBase):
    """Rule for reserved instances recommendations"""
    
    def __init__(self):
        super().__init__(
            "Reserved Instances",
            "Identify opportunities for reserved instance purchases",
        )
    
    async def analyze(self, context: OptimizationContext) -> list[RecommendationData]:
        """Analyze resources for reserved instance opportunities"""
        recommendations = []
        
        async with get_db_session() as session:
            try:
                # Find stable, long-running resources
                stable_query = select(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                    func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
                    func.count(ResourceUsage.id).label('data_points'),
                ).where(
                    and_(
                        ResourceUsage.cloud_account_id == context.account_id,
                        ResourceUsage.timestamp >= datetime.combine(
                            context.current_date - timedelta(days=context.analysis_period),
                            datetime.min.time(),
                        ),
                        ResourceUsage.status == 'running',
                    ),
                ).group_by(
                    ResourceUsage.resource_id,
                    ResourceUsage.resource_type,
                    ResourceUsage.service_name,
                ).having(
                    and_(
                        func.count(ResourceUsage.id) >= 720,  # At least 30 days of hourly data
                        func.avg(ResourceUsage.cpu_utilization) > 20,  # Consistent usage
                        func.avg(ResourceUsage.cpu_utilization) < 90,  # Not overutilized
                    ),
                )
                
                result = await session.execute(stable_query)
                
                for row in result:
                    # Get current cost
                    cost_query = select(
                        func.sum(CostData.cost_amount).label('total_cost'),
                    ).where(
                        and_(
                            CostData.cloud_account_id == context.account_id,
                            CostData.resource_id == row.resource_id,
                            CostData.usage_start_date >= context.current_date - timedelta(days=30),
                        ),
                    )
                    
                    cost_result = await session.execute(cost_query)
                    current_cost = cost_result.scalar() or Decimal('0')
                    
                    if current_cost > 100:  # Only recommend for significant costs
                        # Deterministic RI savings estimate based on usage stability
                        # Higher, more consistent usage => more RI savings (30-60% range)
                        avg_cpu = float(row.avg_cpu or 0)
                        data_points = row.data_points
                        # Stable, moderate usage gets higher savings
                        stability_factor = min(data_points / 720, 1.0)  # Normalized to 30 days
                        usage_factor = min(avg_cpu / 90.0, 1.0)  # Normalized to max 90%
                        savings_percentage = 0.30 + (stability_factor * 0.15) + (usage_factor * 0.15)
                        savings_percentage = min(savings_percentage, 0.60)
                        monthly_savings = current_cost * Decimal(str(round(savings_percentage, 4)))
                        
                        recommendation = RecommendationData(
                            type=RecommendationType.RESERVED_INSTANCES,
                            title=(
                                f"Purchase Reserved Instance for"
                                f" {row.resource_type}"
                                f" {row.resource_id}"
                            ),
                            description=(
                                f"Resource shows stable usage pattern."
                                f" Purchasing a 1-year or 3-year"
                                f" reserved instance can save"
                                f" {savings_percentage * 100:.0f}%"
                                f" on costs."
                            ),
                            resource_id=row.resource_id,
                            resource_type=row.resource_type,
                            service_name=row.service_name,
                            current_cost=current_cost,
                            projected_cost=current_cost - monthly_savings,
                            monthly_savings=monthly_savings,
                            savings_percentage=float(savings_percentage * 100),
                            effort=Effort.MEDIUM,
                            risk_level="low",
                            confidence_score=self.calculate_confidence(row.data_points, 0.8),
                            priority=Priority.HIGH if monthly_savings > 500 else Priority.MEDIUM,
                            implementation_steps=[
                                "Analyze usage stability over 90 days",
                                "Compare on-demand vs reserved pricing",
                                "Select appropriate RI term (1-year or 3-year)",
                                "Purchase reserved instance",
                                "Apply RI to running instances",
                                "Monitor savings realization",
                            ],
                            rollback_plan=(
                                "Reserved instances cannot be cancelled"
                                " - consider convertible RIs"
                                " for flexibility"
                            ),
                            analysis_data={
                                'avg_cpu': float(row.avg_cpu),
                                'data_points': row.data_points,
                                'savings_percentage': float(savings_percentage),
                            },
                        )
                        
                        recommendations.append(recommendation)
                
                return recommendations
                
            except Exception as e:
                logger.error(f"Failed to analyze reserved instance opportunities: {e}")
                return []


class OptimizationEngine:
    """
    Main optimization recommendations engine
    """
    
    def __init__(self):
        self.rules = [
            RightSizingRule(),
            SchedulingRule(),
            TerminationRule(),
            ReservedInstancesRule(),
        ]
    
    async def generate_recommendations(
        self, account_id: str, analysis_period: int = 30,
    ) -> dict[str, Any]:
        """
        Generate optimization recommendations for an account
        """
        try:
            # Get account information
            async with get_db_session() as session:
                result = await session.execute(
                    select(CloudAccount).where(CloudAccount.id == account_id),
                )
                account = result.scalar_one_or_none()
                
                if not account:
                    raise ValueError(f"Cloud account {account_id} not found")
            
            context = OptimizationContext(
                account_id=account_id,
                provider=account.provider,
                region=account.region,
                analysis_period=analysis_period,
                current_date=date.today(),
            )
            
            # Generate recommendations from all rules
            all_recommendations = []
            for rule in self.rules:
                try:
                    recommendations = await rule.analyze(context)
                    all_recommendations.extend(recommendations)
                    logger.info(
                        f"Generated {len(recommendations)}"
                        f" recommendations from {rule.name}",
                    )
                except Exception as e:
                    logger.error(f"Failed to generate recommendations from {rule.name}: {e}")
            
            # Sort by priority and savings
            priority_order = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
            all_recommendations.sort(
                key=lambda x: (priority_order.get(x.priority.value, 0), x.monthly_savings),
                reverse=True,
            )
            
            # Limit to maximum recommendations
            max_recommendations = optimization_settings.MAX_RECOMMENDATIONS_PER_RUN
            if len(all_recommendations) > max_recommendations:
                all_recommendations = all_recommendations[:max_recommendations]
            
            # Store recommendations in database
            stored_count = await self._store_recommendations(account_id, all_recommendations)
            
            # Calculate summary statistics
            total_savings = sum(rec.monthly_savings for rec in all_recommendations)
            avg_confidence = (
                np.mean([rec.confidence_score for rec in all_recommendations])
                if all_recommendations
                else 0
            )
            
            summary = {
                'account_id': account_id,
                'account_name': account.name,
                'provider': account.provider,
                'analysis_period': analysis_period,
                'recommendations_generated': len(all_recommendations),
                'recommendations_stored': stored_count,
                'total_monthly_savings': total_savings,
                'average_confidence': avg_confidence,
                'recommendations_by_type': {
                    rec_type.value: len([r for r in all_recommendations if r.type == rec_type])
                    for rec_type in RecommendationType
                },
                'recommendations_by_priority': {
                    priority.value: len([r for r in all_recommendations if r.priority == priority])
                    for priority in Priority
                },
                'generated_at': datetime.now(timezone.utc).isoformat(),
            }
            
            logger.info(
                f"Generated {len(all_recommendations)} recommendations"
                f" with ${total_savings:.2f} potential monthly savings",
            )
            
            return {
                'summary': summary,
                'recommendations': [
                    {
                        'type': rec.type.value,
                        'title': rec.title,
                        'description': rec.description,
                        'resource_id': rec.resource_id,
                        'resource_type': rec.resource_type,
                        'service_name': rec.service_name,
                        'current_cost': float(rec.current_cost or 0),
                        'projected_cost': float(rec.projected_cost or 0),
                        'monthly_savings': float(rec.monthly_savings),
                        'savings_percentage': rec.savings_percentage,
                        'effort': rec.effort.value,
                        'risk_level': rec.risk_level,
                        'confidence_score': rec.confidence_score,
                        'priority': rec.priority.value,
                        'implementation_steps': rec.implementation_steps,
                        'rollback_plan': rec.rollback_plan,
                        'analysis_data': rec.analysis_data,
                    }
                    for rec in all_recommendations
                ],
            }
            
        except Exception as e:
            logger.error(f"Failed to generate recommendations: {e}")
            raise
    
    async def _store_recommendations(
        self, account_id: str, recommendations: list[RecommendationData],
    ) -> int:
        """Store recommendations in database"""
        stored_count = 0
        
        async with get_db_session() as session:
            try:
                for rec in recommendations:
                    # Check if similar recommendation already exists
                    existing = await session.execute(
                        select(OptimizationRecommendation).where(
                            and_(
                                OptimizationRecommendation.cloud_account_id == account_id,
                                OptimizationRecommendation.resource_id == rec.resource_id,
                                OptimizationRecommendation.recommendation_type == rec.type.value,
                                OptimizationRecommendation.status == 'pending',
                            ),
                        ),
                    )
                    
                    if not existing.scalar_one_or_none():
                        recommendation = OptimizationRecommendation(
                            cloud_account_id=account_id,
                            recommendation_type=rec.type.value,
                            title=rec.title,
                            description=rec.description,
                            resource_id=rec.resource_id,
                            resource_type=rec.resource_type,
                            service_name=rec.service_name,
                            current_monthly_cost=rec.current_cost,
                            projected_monthly_cost=rec.projected_cost,
                            monthly_savings=rec.monthly_savings,
                            savings_percentage=rec.savings_percentage,
                            effort=rec.effort.value,
                            risk_level=rec.risk_level,
                            confidence_score=rec.confidence_score,
                            priority=rec.priority.value,
                            implementation_steps=rec.implementation_steps,
                            rollback_plan=rec.rollback_plan,
                            analysis_data=rec.analysis_data,
                        )
                        session.add(recommendation)
                        stored_count += 1
                
                await session.commit()
                return stored_count
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to store recommendations: {e}")
                raise
    
    async def get_recommendations(self, account_id: str, status: str | None = None, 
                                limit: int = 50) -> list[dict]:
        """Get stored recommendations"""
        async with get_db_session() as session:
            try:
                query = select(OptimizationRecommendation).where(
                    OptimizationRecommendation.cloud_account_id == account_id,
                )
                
                if status:
                    query = query.where(OptimizationRecommendation.status == status)
                
                query = query.order_by(
                    desc(OptimizationRecommendation.monthly_savings),
                ).limit(limit)
                
                result = await session.execute(query)
                recommendations = result.scalars().all()
                
                return [
                    {
                        'id': str(rec.id),
                        'type': rec.recommendation_type,
                        'title': rec.title,
                        'description': rec.description,
                        'resource_id': rec.resource_id,
                        'resource_type': rec.resource_type,
                        'service_name': rec.service_name,
                        'current_monthly_cost': float(rec.current_monthly_cost or 0),
                        'projected_monthly_cost': float(rec.projected_monthly_cost or 0),
                        'monthly_savings': float(rec.monthly_savings),
                        'savings_percentage': rec.savings_percentage,
                        'effort': rec.effort,
                        'risk_level': rec.risk_level,
                        'confidence_score': rec.confidence_score,
                        'priority': rec.priority,
                        'status': rec.status,
                        'implementation_steps': rec.implementation_steps,
                        'rollback_plan': rec.rollback_plan,
                    'generated_at': rec.generated_at.isoformat(),
                    'expires_at': rec.expires_at.isoformat() if rec.expires_at else None,
                }
                for rec in recommendations
            ]
                
            except Exception as e:
                logger.error(f"Failed to get recommendations: {e}")
                raise
    
    async def update_recommendation_status(self, recommendation_id: str, status: str, 
                                         implemented_by: str | None = None) -> bool:
        """Update recommendation status"""
        async with get_db_session() as session:
            try:
                result = await session.execute(
                    select(OptimizationRecommendation).where(
                        OptimizationRecommendation.id == recommendation_id,
                    ),
                )
                recommendation = result.scalar_one_or_none()
                
                if not recommendation:
                    return False
                
                recommendation.status = status
                if status == 'implemented':
                    recommendation.implemented_at = datetime.now(timezone.utc)
                
                await session.commit()
                return True
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to update recommendation status: {e}")
                return False


# Global optimization engine instance
optimization_engine = OptimizationEngine()

