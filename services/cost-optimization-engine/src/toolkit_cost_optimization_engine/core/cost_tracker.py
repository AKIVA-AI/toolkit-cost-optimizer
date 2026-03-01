"""
Cost tracking system for Toolkit Cost Optimization Engine
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_db_session
from ..models.models import (
    CloudAccount,
    CostAnomaly,
    CostData,
    ResourceUsage,
    ServiceCost,
    TagCost,
)

logger = logging.getLogger(__name__)


@dataclass
class CostMetrics:
    """Cost metrics data structure"""
    total_cost: Decimal
    cost_change: Decimal
    cost_change_percentage: float
    daily_average: Decimal
    projected_monthly: Decimal
    services_breakdown: dict[str, Decimal]
    tags_breakdown: dict[str, Decimal]


@dataclass
class ResourceCostAnalysis:
    """Resource cost analysis data structure"""
    resource_id: str
    resource_type: str
    service_name: str
    current_cost: Decimal
    utilization_score: float
    efficiency_score: float
    optimization_potential: Decimal
    recommendations: list[str]


class CloudProviderClient:
    """Base class for cloud provider clients"""
    
    def __init__(self, account: CloudAccount):
        self.account = account
        self.provider = account.provider
        
    async def get_cost_data(self, start_date: date, end_date: date) -> list[dict]:
        """Get cost data from cloud provider"""
        raise NotImplementedError
    
    async def get_usage_data(self, start_date: datetime, end_date: datetime) -> list[dict]:
        """Get usage data from cloud provider"""
        raise NotImplementedError
    
    async def test_connection(self) -> bool:
        """Test connection to cloud provider"""
        raise NotImplementedError


class AWSClient(CloudProviderClient):
    """AWS Cost Explorer client"""
    
    def __init__(self, account: CloudAccount):
        super().__init__(account)
        self._client = None
        
    def _get_client(self):
        """Get AWS Cost Explorer client"""
        if self._client is None:
            import boto3
            self._client = boto3.client(
                'ce',
                aws_access_key_id=self.account.access_key,
                aws_secret_access_key=self.account.secret_key,
                region_name=self.account.region,
            )
        return self._client
    
    async def get_cost_data(self, start_date: date, end_date: date) -> list[dict]:
        """Get cost data from AWS Cost Explorer"""
        try:
            client = self._get_client()
            response = await asyncio.to_thread(
                client.get_cost_and_usage,
                TimePeriod={
                    'Start': start_date.strftime('%Y-%m-%d'),
                    'End': end_date.strftime('%Y-%m-%d'),
                },
                Granularity='DAILY',
                Metrics=['BlendedCost', 'UsageQuantity'],
                GroupBy=[
                    {'Type': 'DIMENSION', 'Key': 'SERVICE'},
                    {'Type': 'DIMENSION', 'Key': 'INSTANCE_TYPE'},
                    {'Type': 'TAG', 'Key': 'Environment'},
                ],
            )
            
            cost_data = []
            for result in response.get('ResultsByTime', []):
                time_period = result['TimePeriod']
                period_start = datetime.strptime(time_period['Start'], '%Y-%m-%d').date()
                period_end = datetime.strptime(time_period['End'], '%Y-%m-%d').date()
                
                for group in result.get('Groups', []):
                    keys = group['Keys']
                    metrics = group['Metrics']
                    
                    cost_data.append({
                        'usage_start_date': period_start,
                        'usage_end_date': period_end,
                        'service_name': (
                            keys[0].split('/')[-1] if '/' in keys[0] else keys[0]
                        ),
                        'resource_type': (
                            keys[1].split('/')[-1]
                            if len(keys) > 1 and '/' in keys[1]
                            else None
                        ),
                        'tag_environment': (
                            keys[2].split('$')[-1]
                            if len(keys) > 2 and '$' in keys[2]
                            else None
                        ),
                        'cost_amount': Decimal(
                            metrics['BlendedCost']['Amount'],
                        ),
                        'usage_quantity': Decimal(
                            metrics.get('UsageQuantity', {}).get('Amount', '0'),
                        ),
                        'currency': metrics['BlendedCost']['Unit'],
                    })
            
            return cost_data
            
        except Exception as e:
            logger.error(f"Failed to get AWS cost data: {e}")
            return []
    
    async def get_usage_data(self, start_date: datetime, end_date: datetime) -> list[dict]:
        """Get usage data from AWS CloudWatch"""
        try:
            import boto3
            
            cloudwatch = boto3.client(
                'cloudwatch',
                aws_access_key_id=self.account.access_key,
                aws_secret_access_key=self.account.secret_key,
                region_name=self.account.region,
            )
            
            # Get EC2 instances
            ec2 = boto3.client(
                'ec2',
                aws_access_key_id=self.account.access_key,
                aws_secret_access_key=self.account.secret_key,
                region_name=self.account.region,
            )
            
            instances_response = await asyncio.to_thread(ec2.describe_instances)
            usage_data = []
            
            for reservation in instances_response.get('Reservations', []):
                for instance in reservation.get('Instances', []):
                    instance_id = instance['InstanceId']
                    instance_type = instance.get('InstanceType', '')
                    
                    # Get CloudWatch metrics
                    cpu_metrics = await asyncio.to_thread(
                        cloudwatch.get_metric_statistics,
                        Namespace='AWS/EC2',
                        MetricName='CPUUtilization',
                        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                        StartTime=start_date,
                        EndTime=end_date,
                        Period=3600,
                        Statistics=['Average', 'Maximum', 'Minimum'],
                    )
                    
                    if cpu_metrics['Datapoints']:
                        avg_cpu = np.mean([dp['Average'] for dp in cpu_metrics['Datapoints']])
                        max_cpu = np.mean([dp['Maximum'] for dp in cpu_metrics['Datapoints']])
                        min_cpu = np.mean([dp['Minimum'] for dp in cpu_metrics['Datapoints']])
                    else:
                        avg_cpu = max_cpu = min_cpu = 0
                    
                    usage_data.append({
                        'resource_id': instance_id,
                        'resource_type': instance_type,
                        'service_name': 'EC2',
                        'timestamp': datetime.now(timezone.utc),
                        'period': 'hourly',
                        'cpu_utilization': float(avg_cpu),
                        'status': instance.get('State', {}).get('Name', 'unknown'),
                        'region': self.account.region,
                        'metadata': {
                            'cpu_max': float(max_cpu),
                            'cpu_min': float(min_cpu),
                        },
                    })
            
            return usage_data
            
        except Exception as e:
            logger.error(f"Failed to get AWS usage data: {e}")
            return []
    
    async def test_connection(self) -> bool:
        """Test AWS connection"""
        try:
            client = self._get_client()
            await asyncio.to_thread(
                client.get_cost_and_usage,
                TimePeriod={
                    'Start': (date.today() - timedelta(days=1)).strftime('%Y-%m-%d'),
                    'End': date.today().strftime('%Y-%m-%d'),
                },
                Granularity='DAILY',
                Metrics=['BlendedCost'],
            )
            return True
        except Exception as e:
            logger.error(f"AWS connection test failed: {e}")
            return False


class AzureClient(CloudProviderClient):
    """Azure Cost Management client"""
    
    async def get_cost_data(self, start_date: date, end_date: date) -> list[dict]:
        """Get cost data from Azure Cost Management"""
        # Implementation for Azure Cost Management API
        return []
    
    async def get_usage_data(self, start_date: datetime, end_date: datetime) -> list[dict]:
        """Get usage data from Azure Monitor"""
        # Implementation for Azure Monitor API
        return []
    
    async def test_connection(self) -> bool:
        """Test Azure connection"""
        # Implementation for Azure connection test
        return False


class GCPClient(CloudProviderClient):
    """Google Cloud Billing client"""
    
    async def get_cost_data(self, start_date: date, end_date: date) -> list[dict]:
        """Get cost data from Google Cloud Billing"""
        # Implementation for Google Cloud Billing API
        return []
    
    async def get_usage_data(self, start_date: datetime, end_date: datetime) -> list[dict]:
        """Get usage data from Google Cloud Monitoring"""
        # Implementation for Google Cloud Monitoring API
        return []
    
    async def test_connection(self) -> bool:
        """Test GCP connection"""
        # Implementation for GCP connection test
        return False


class CostTracker:
    """
    Main cost tracking system
    """
    
    def __init__(self):
        self.providers = {
            'aws': AWSClient,
            'azure': AzureClient,
            'gcp': GCPClient,
        }
    
    async def get_provider_client(self, account: CloudAccount) -> CloudProviderClient:
        """Get cloud provider client"""
        client_class = self.providers.get(account.provider.lower())
        if not client_class:
            raise ValueError(f"Unsupported provider: {account.provider}")
        return client_class(account)
    
    async def sync_cost_data(self, account_id: str, days_back: int = 30) -> dict[str, Any]:
        """
        Sync cost data from cloud provider
        """
        async with get_db_session() as session:
            try:
                # Get cloud account
                result = await session.execute(
                    select(CloudAccount).where(CloudAccount.id == account_id),
                )
                account = result.scalar_one_or_none()
                
                if not account:
                    raise ValueError(f"Cloud account {account_id} not found")
                
                # Get provider client
                client = await self.get_provider_client(account)
                
                # Test connection
                if not await client.test_connection():
                    raise ValueError(f"Failed to connect to {account.provider}")
                
                # Get cost data
                end_date = date.today()
                start_date = end_date - timedelta(days=days_back)
                
                cost_data = await client.get_cost_data(start_date, end_date)
                usage_data = await client.get_usage_data(
                    datetime.combine(start_date, datetime.min.time()),
                    datetime.combine(end_date, datetime.max.time()),
                )
                
                # Store cost data
                stored_costs = 0
                for data in cost_data:
                    cost_record = CostData(
                        cloud_account_id=account.id,
                        usage_start_date=data['usage_start_date'],
                        usage_end_date=data['usage_end_date'],
                        billing_period=f"{data['usage_start_date']:%Y-%m}",
                        service_name=data['service_name'],
                        resource_type=data.get('resource_type'),
                        cost_amount=data['cost_amount'],
                        usage_quantity=data.get('usage_quantity'),
                        usage_unit=data.get('usage_unit'),
                        currency=data.get('currency', 'USD'),
                        region=account.region,
                        tags=(
                            {'environment': data.get('tag_environment')}
                            if data.get('tag_environment')
                            else None
                        ),
                        raw_data=data,
                    )
                    session.add(cost_record)
                    stored_costs += 1
                
                # Store usage data
                stored_usage = 0
                for data in usage_data:
                    usage_record = ResourceUsage(
                        cloud_account_id=account.id,
                        resource_id=data['resource_id'],
                        resource_type=data['resource_type'],
                        service_name=data['service_name'],
                        timestamp=data['timestamp'],
                        period=data['period'],
                        cpu_utilization=data.get('cpu_utilization'),
                        status=data.get('status'),
                        region=data.get('region'),
                        metadata_=data.get('metadata'),
                    )
                    session.add(usage_record)
                    stored_usage += 1
                
                # Update account sync status
                account.last_sync = datetime.now(timezone.utc)
                account.is_connected = True
                
                await session.commit()
                
                # Update service costs aggregation
                await self._update_service_costs(session, account.id, start_date, end_date)
                
                # Update tag costs aggregation
                await self._update_tag_costs(session, account.id, start_date, end_date)
                
                logger.info(
                    f"Synced {stored_costs} cost records and"
                    f" {stored_usage} usage records for {account.name}",
                )
                
                return {
                    'account_id': account_id,
                    'account_name': account.name,
                    'provider': account.provider,
                    'period': f"{start_date} to {end_date}",
                    'cost_records_stored': stored_costs,
                    'usage_records_stored': stored_usage,
                    'sync_time': datetime.now(timezone.utc).isoformat(),
                }
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to sync cost data for account {account_id}: {e}")
                raise
    
    async def _update_service_costs(self, session: AsyncSession, account_id: str, 
                                  start_date: date, end_date: date):
        """Update service costs aggregation"""
        try:
            # Delete existing aggregations for the period
            billing_period = f"{start_date:%Y-%m}"
            await session.execute(
                ServiceCost.__table__.delete().where(
                    and_(
                        ServiceCost.cloud_account_id == account_id,
                        ServiceCost.billing_period == billing_period,
                    ),
                ),
            )
            
            # Aggregate costs by service
            service_costs = await session.execute(
                select(
                    CostData.service_name,
                    func.sum(CostData.cost_amount).label('total_cost'),
                    func.count(CostData.id).label('record_count'),
                ).where(
                    and_(
                        CostData.cloud_account_id == account_id,
                        CostData.usage_start_date >= start_date,
                        CostData.usage_end_date <= end_date,
                    ),
                ).group_by(CostData.service_name),
            )
            
            for service_name, total_cost, record_count in service_costs:
                service_cost = ServiceCost(
                    cloud_account_id=account_id,
                    service_name=service_name,
                    billing_period=billing_period,
                    usage_start_date=start_date,
                    usage_end_date=end_date,
                    total_cost=total_cost,
                    resource_count=record_count,
                )
                session.add(service_cost)
            
            await session.commit()
            
        except Exception as e:
            logger.error(f"Failed to update service costs: {e}")
            raise
    
    async def _update_tag_costs(self, session: AsyncSession, account_id: str,
                              start_date: date, end_date: date):
        """Update tag costs aggregation"""
        try:
            # Delete existing aggregations for the period
            billing_period = f"{start_date:%Y-%m}"
            await session.execute(
                TagCost.__table__.delete().where(
                    and_(
                        TagCost.cloud_account_id == account_id,
                        TagCost.billing_period == billing_period,
                    ),
                ),
            )
            
            # Aggregate costs by tags (simplified - just environment tag)
            tag_costs = await session.execute(
                select(
                    func.jsonb_extract_path_text(CostData.tags, 'environment').label('tag_value'),
                    func.sum(CostData.cost_amount).label('total_cost'),
                    func.count(func.distinct(CostData.resource_id)).label('resource_count'),
                ).where(
                    and_(
                        CostData.cloud_account_id == account_id,
                        CostData.usage_start_date >= start_date,
                        CostData.usage_end_date <= end_date,
                        CostData.tags.isnot(None),
                    ),
                ).group_by('tag_value'),
            )
            
            for tag_value, total_cost, resource_count in tag_costs:
                if tag_value:  # Skip null values
                    tag_cost = TagCost(
                        cloud_account_id=account_id,
                        tag_key='environment',
                        tag_value=tag_value,
                        billing_period=billing_period,
                        usage_start_date=start_date,
                        usage_end_date=end_date,
                        total_cost=total_cost,
                        resource_count=resource_count,
                    )
                    session.add(tag_cost)
            
            await session.commit()
            
        except Exception as e:
            logger.error(f"Failed to update tag costs: {e}")
            raise
    
    async def get_cost_metrics(self, account_id: str, start_date: date, 
                              end_date: date) -> CostMetrics:
        """Get cost metrics for a period"""
        async with get_db_session() as session:
            try:
                # Get total cost
                total_cost_result = await session.execute(
                    select(func.sum(CostData.cost_amount)).where(
                        and_(
                            CostData.cloud_account_id == account_id,
                            CostData.usage_start_date >= start_date,
                            CostData.usage_end_date <= end_date,
                        ),
                    ),
                )
                total_cost = total_cost_result.scalar() or Decimal('0')
                
                # Get previous period cost for comparison
                days_diff = (end_date - start_date).days
                prev_start = start_date - timedelta(days=days_diff)
                prev_end = start_date - timedelta(days=1)
                
                prev_cost_result = await session.execute(
                    select(func.sum(CostData.cost_amount)).where(
                        and_(
                            CostData.cloud_account_id == account_id,
                            CostData.usage_start_date >= prev_start,
                            CostData.usage_end_date <= prev_end,
                        ),
                    ),
                )
                prev_cost = prev_cost_result.scalar() or Decimal('0')
                
                # Calculate change metrics
                cost_change = total_cost - prev_cost
                cost_change_percentage = (
                    float(cost_change / prev_cost * 100) if prev_cost > 0 else 0
                )
                
                # Calculate daily average
                daily_average = total_cost / days_diff if days_diff > 0 else Decimal('0')
                
                # Project monthly cost
                projected_monthly = daily_average * 30
                
                # Get services breakdown
                services_result = await session.execute(
                    select(
                        CostData.service_name,
                        func.sum(CostData.cost_amount).label('cost'),
                    ).where(
                        and_(
                            CostData.cloud_account_id == account_id,
                            CostData.usage_start_date >= start_date,
                            CostData.usage_end_date <= end_date,
                        ),
                    ).group_by(CostData.service_name).order_by(desc('cost')),
                )
                services_breakdown = {service: cost for service, cost in services_result}
                
                # Get tags breakdown
                tags_result = await session.execute(
                    select(
                        TagCost.tag_value,
                        func.sum(TagCost.total_cost).label('cost'),
                    ).where(
                        and_(
                            TagCost.cloud_account_id == account_id,
                            TagCost.usage_start_date >= start_date,
                            TagCost.usage_end_date <= end_date,
                        ),
                    ).group_by(TagCost.tag_value).order_by(desc('cost')),
                )
                tags_breakdown = {tag: cost for tag, cost in tags_result}
                
                return CostMetrics(
                    total_cost=total_cost,
                    cost_change=cost_change,
                    cost_change_percentage=cost_change_percentage,
                    daily_average=daily_average,
                    projected_monthly=projected_monthly,
                    services_breakdown=services_breakdown,
                    tags_breakdown=tags_breakdown,
                )
                
            except Exception as e:
                logger.error(f"Failed to get cost metrics: {e}")
                raise
    
    async def analyze_resource_costs(
        self, account_id: str, days_back: int = 30,
    ) -> list[ResourceCostAnalysis]:
        """Analyze resource costs and efficiency"""
        async with get_db_session() as session:
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=days_back)
                
                # Get resource costs and usage
                resource_query = select(
                    CostData.resource_id,
                    CostData.resource_type,
                    CostData.service_name,
                    func.sum(CostData.cost_amount).label('total_cost'),
                    func.avg(ResourceUsage.cpu_utilization).label('avg_cpu'),
                    func.avg(ResourceUsage.memory_utilization).label('avg_memory'),
                ).select_from(
                    CostData.__table__.join(
                        ResourceUsage.__table__,
                        and_(
                            CostData.resource_id == ResourceUsage.resource_id,
                            CostData.cloud_account_id == ResourceUsage.cloud_account_id,
                        ),
                        isouter=True,
                    ),
                ).where(
                    and_(
                        CostData.cloud_account_id == account_id,
                        CostData.usage_start_date >= start_date,
                        CostData.usage_end_date <= end_date,
                        CostData.resource_id.isnot(None),
                    ),
                ).group_by(
                    CostData.resource_id,
                    CostData.resource_type,
                    CostData.service_name,
                )
                
                result = await session.execute(resource_query)
                analyses = []
                
                for row in result:
                    resource_id = row.resource_id
                    resource_type = row.resource_type or 'unknown'
                    service_name = row.service_name or 'unknown'
                    current_cost = row.total_cost or Decimal('0')
                    avg_cpu = float(row.avg_cpu or 0)
                    avg_memory = float(row.avg_memory or 0)
                    
                    # Calculate utilization score (weighted average)
                    utilization_score = (
                        (avg_cpu * 0.7 + avg_memory * 0.3)
                        if avg_cpu and avg_memory
                        else avg_cpu or 0
                    )
                    
                    # Calculate efficiency score
                    efficiency_score = (
                        1.0 - utilization_score
                        if utilization_score <= 0.8
                        else 0.2
                    )
                    
                    # Calculate optimization potential
                    optimization_potential = current_cost * Decimal(
                        str(efficiency_score * 0.5),
                    )
                    
                    # Generate recommendations
                    recommendations = []
                    if utilization_score < 0.3:
                        recommendations.append(
                            "Consider right-sizing or terminating"
                            " underutilized resource",
                        )
                    if utilization_score > 0.9:
                        recommendations.append("Resource may be overutilized - consider scaling up")
                    if efficiency_score > 0.7:
                        recommendations.append(
                            "High optimization potential"
                            " - review resource configuration",
                        )
                    
                    analyses.append(ResourceCostAnalysis(
                        resource_id=resource_id,
                        resource_type=resource_type,
                        service_name=service_name,
                        current_cost=current_cost,
                        utilization_score=utilization_score,
                        efficiency_score=efficiency_score,
                        optimization_potential=optimization_potential,
                        recommendations=recommendations,
                    ))
                
                # Sort by optimization potential (highest first)
                analyses.sort(key=lambda x: x.optimization_potential, reverse=True)
                
                return analyses
                
            except Exception as e:
                logger.error(f"Failed to analyze resource costs: {e}")
                raise
    
    async def detect_cost_anomalies(self, account_id: str, days_back: int = 30) -> list[dict]:
        """Detect cost anomalies using statistical analysis"""
        async with get_db_session() as session:
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=days_back)
                
                # Get daily costs
                daily_costs = await session.execute(
                    select(
                        CostData.usage_start_date,
                        func.sum(CostData.cost_amount).label('daily_cost'),
                    ).where(
                        and_(
                            CostData.cloud_account_id == account_id,
                            CostData.usage_start_date >= start_date,
                            CostData.usage_end_date <= end_date,
                        ),
                    ).group_by(CostData.usage_start_date).order_by(CostData.usage_start_date),
                )
                
                costs = [float(row.daily_cost) for row in daily_costs]
                dates = [row.usage_start_date for row in daily_costs]
                
                if len(costs) < 7:  # Need at least 7 days for anomaly detection
                    return []
                
                # Calculate statistical thresholds
                mean_cost = np.mean(costs)
                std_cost = np.std(costs)
                threshold = mean_cost + (2 * std_cost)  # 2 standard deviations
                
                anomalies = []
                for cost_date, cost in zip(dates, costs, strict=False):
                    if cost > threshold:
                        deviation_percentage = ((cost - mean_cost) / mean_cost) * 100
                        
                        anomalies.append({
                            'date': cost_date,
                            'actual_cost': Decimal(str(cost)),
                            'expected_cost': Decimal(str(mean_cost)),
                            'deviation_percentage': deviation_percentage,
                            'severity': (
                                'high' if deviation_percentage > 100
                                else 'medium' if deviation_percentage > 50
                                else 'low'
                            ),
                        })
                
                # Store anomalies in database
                for anomaly in anomalies:
                    existing = await session.execute(
                        select(CostAnomaly).where(
                            and_(
                                CostAnomaly.cloud_account_id == account_id,
                                CostAnomaly.detected_date == anomaly['date'],
                            ),
                        ),
                    )
                    
                    if not existing.scalar_one_or_none():
                        cost_anomaly = CostAnomaly(
                            cloud_account_id=account_id,
                            anomaly_type='spike',
                            severity=anomaly['severity'],
                            detected_date=anomaly['date'],
                            anomaly_start_date=anomaly['date'],
                            anomaly_end_date=anomaly['date'],
                            expected_value=anomaly['expected_cost'],
                            actual_value=anomaly['actual_cost'],
                            deviation_percentage=anomaly['deviation_percentage'],
                            anomaly_score=anomaly['deviation_percentage'] / 100,
                            status='investigating',
                        )
                        session.add(cost_anomaly)
                
                await session.commit()
                
                return anomalies
                
            except Exception as e:
                logger.error(f"Failed to detect cost anomalies: {e}")
                raise
    
    async def get_cost_trends(self, account_id: str, days_back: int = 90) -> list[dict]:
        """Get cost trends analysis"""
        async with get_db_session() as session:
            try:
                end_date = date.today()
                start_date = end_date - timedelta(days=days_back)
                
                # Get weekly cost trends
                weekly_costs = await session.execute(
                    select(
                        func.date_trunc('week', CostData.usage_start_date).label('week_start'),
                        func.sum(CostData.cost_amount).label('weekly_cost'),
                    ).where(
                        and_(
                            CostData.cloud_account_id == account_id,
                            CostData.usage_start_date >= start_date,
                            CostData.usage_end_date <= end_date,
                        ),
                    ).group_by('week_start').order_by('week_start'),
                )
                
                trends = []
                costs = []
                
                for row in weekly_costs:
                    week_start = row.week_start.date()
                    weekly_cost = row.weekly_cost
                    costs.append(float(weekly_cost))
                    
                    # Calculate trend
                    if len(costs) > 1:
                        cost_change = costs[-1] - costs[-2]
                        cost_change_percentage = (
                            (cost_change / costs[-2]) * 100
                            if costs[-2] > 0
                            else 0
                        )
                        
                        # Determine trend direction
                        if cost_change_percentage > 5:
                            trend_direction = 'increasing'
                        elif cost_change_percentage < -5:
                            trend_direction = 'decreasing'
                        else:
                            trend_direction = 'stable'
                        
                        # Calculate trend strength (based on consistency)
                        if len(costs) >= 3:
                            recent_changes = [
                                costs[j] - costs[j - 1] for j in range(-3, 0)
                            ]
                            avg_change = abs(
                                sum(recent_changes) / len(recent_changes),
                            )
                            trend_strength = min(
                                avg_change / costs[-1] * 100, 100,
                            ) / 100
                        else:
                            trend_strength = 0.5
                    else:
                        cost_change = 0
                        cost_change_percentage = 0
                        trend_direction = 'stable'
                        trend_strength = 0.5
                    
                    trends.append({
                        'period_start': week_start,
                        'period_end': week_start + timedelta(days=6),
                        'total_cost': weekly_cost,
                        'cost_change': Decimal(str(cost_change)),
                        'cost_change_percentage': cost_change_percentage,
                        'trend_direction': trend_direction,
                        'trend_strength': trend_strength,
                    })
                
                return trends
                
            except Exception as e:
                logger.error(f"Failed to get cost trends: {e}")
                raise


# Global cost tracker instance
cost_tracker = CostTracker()

