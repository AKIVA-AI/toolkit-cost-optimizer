"""
API schemas for Toolkit Cost Optimization Engine
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Base schemas
class BaseSchema(BaseModel):
    """Base schema with common fields"""
    id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# Cloud Account schemas
class CloudAccountBase(BaseModel):
    """Base cloud account schema"""
    name: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(..., pattern=r"^(aws|azure|gcp)$")
    account_id: str = Field(..., min_length=1, max_length=255)
    region: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    tags: dict[str, Any] | None = None


class CloudAccountCreate(CloudAccountBase):
    """Schema for creating cloud accounts"""
    access_key: str | None = None
    secret_key: str | None = None
    tenant_id: str | None = None
    subscription_id: str | None = None
    project_id: str | None = None


class CloudAccountUpdate(BaseModel):
    """Schema for updating cloud accounts"""
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    tenant_id: str | None = None
    subscription_id: str | None = None
    project_id: str | None = None
    tags: dict[str, Any] | None = None
    is_active: bool | None = None


class CloudAccount(CloudAccountBase):
    """Complete cloud account schema"""
    is_active: bool = True
    is_connected: bool = False
    last_sync: datetime | None = None
    
    model_config = ConfigDict(from_attributes=True)


# Cost Data schemas
class CostDataBase(BaseModel):
    """Base cost data schema"""
    service_name: str = Field(..., min_length=1, max_length=255)
    service_category: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    usage_quantity: Decimal | None = None
    usage_unit: str | None = None
    cost_amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    rate: Decimal | None = None
    pricing_model: str | None = None
    region: str | None = None
    availability_zone: str | None = None
    tags: dict[str, Any] | None = None


class CostDataCreate(CostDataBase):
    """Schema for creating cost data"""
    cloud_account_id: str
    usage_start_date: date
    usage_end_date: date
    billing_period: str


class CostData(CostDataBase):
    """Complete cost data schema"""
    cloud_account_id: str
    usage_start_date: date
    usage_end_date: date
    billing_period: str
    raw_data: dict[str, Any] | None = None
    
    model_config = ConfigDict(from_attributes=True)


# Resource Usage schemas
class ResourceUsageBase(BaseModel):
    """Base resource usage schema"""
    resource_id: str = Field(..., min_length=1, max_length=255)
    resource_name: str | None = None
    resource_type: str = Field(..., min_length=1, max_length=255)
    service_name: str = Field(..., min_length=1, max_length=255)
    period: str = Field(..., pattern=r"^(hourly|daily)$")
    cpu_utilization: float | None = Field(None, ge=0, le=100)
    memory_utilization: float | None = Field(None, ge=0, le=100)
    disk_utilization: float | None = Field(None, ge=0, le=100)
    network_in: Decimal | None = Field(None, ge=0)
    network_out: Decimal | None = Field(None, ge=0)
    requests_per_second: float | None = Field(None, ge=0)
    cpu_cores: int | None = Field(None, gt=0)
    memory_gb: int | None = Field(None, gt=0)
    disk_gb: int | None = Field(None, ge=0)
    instance_type: str | None = None
    status: str | None = None
    region: str | None = None
    availability_zone: str | None = None
    tags: dict[str, Any] | None = None
    metadata_: dict[str, Any] | None = Field(default=None, alias="metadata")

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)


class ResourceUsageCreate(ResourceUsageBase):
    """Schema for creating resource usage"""
    cloud_account_id: str
    timestamp: datetime


class ResourceUsage(ResourceUsageBase):
    """Complete resource usage schema"""
    cloud_account_id: str
    timestamp: datetime
    
    model_config = ConfigDict(
        from_attributes=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


# Optimization Recommendation schemas
class RecommendationType(str, Enum):
    """Recommendation types"""
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


class Priority(str, Enum):
    """Priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Effort(str, Enum):
    """Effort levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OptimizationRecommendationBase(BaseModel):
    """Base optimization recommendation schema"""
    recommendation_type: RecommendationType
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    resource_id: str | None = None
    resource_type: str | None = None
    service_name: str | None = None
    current_monthly_cost: Decimal | None = Field(None, ge=0)
    projected_monthly_cost: Decimal | None = Field(None, ge=0)
    monthly_savings: Decimal = Field(..., ge=0)
    savings_percentage: float = Field(..., ge=0, le=100)
    effort: Effort
    risk_level: str = Field(..., pattern=r"^(low|medium|high)$")
    confidence_score: float = Field(..., ge=0, le=1)
    priority: Priority
    implementation_steps: list[str] | None = None
    rollback_plan: str | None = None
    analysis_data: dict[str, Any] | None = None
    tags: dict[str, Any] | None = None


class OptimizationRecommendationCreate(OptimizationRecommendationBase):
    """Schema for creating optimization recommendations"""
    cloud_account_id: str


class OptimizationRecommendationUpdate(BaseModel):
    """Schema for updating optimization recommendations"""
    status: str | None = Field(None, pattern=r"^(pending|approved|rejected|implemented)$")
    priority: Priority | None = None
    implementation_steps: list[str] | None = None
    rollback_plan: str | None = None
    notes: str | None = None


class OptimizationRecommendation(OptimizationRecommendationBase):
    """Complete optimization recommendation schema"""
    cloud_account_id: str
    status: str = "pending"
    generated_at: datetime
    expires_at: datetime | None = None
    implemented_at: datetime | None = None
    
    model_config = ConfigDict(from_attributes=True)


# Budget schemas
class BudgetType(str, Enum):
    """Budget types"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class ScopeType(str, Enum):
    """Scope types"""
    ACCOUNT = "account"
    SERVICE = "service"
    TAG = "tag"
    RESOURCE = "resource"


class BudgetBase(BaseModel):
    """Base budget schema"""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    budget_type: BudgetType
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    start_date: date
    end_date: date
    scope_type: ScopeType
    scope_filter: dict[str, Any] | None = None
    alert_threshold: float = Field(default=0.8, ge=0, le=1)
    critical_threshold: float = Field(default=0.95, ge=0, le=1)
    
    @field_validator('end_date')
    @classmethod
    def end_date_after_start_date(cls, v, info):
        start_date = info.data.get('start_date')
        if start_date and v <= start_date:
            raise ValueError('end_date must be after start_date')
        return v
    
    @field_validator('critical_threshold')
    @classmethod
    def critical_threshold_after_alert(cls, v, info):
        alert_threshold = info.data.get('alert_threshold')
        if alert_threshold is not None and v <= alert_threshold:
            raise ValueError('critical_threshold must be greater than alert_threshold')
        return v


class BudgetCreate(BudgetBase):
    """Schema for creating budgets"""
    cloud_account_id: str


class BudgetUpdate(BaseModel):
    """Schema for updating budgets"""
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    amount: Decimal | None = Field(None, gt=0)
    alert_threshold: float | None = Field(None, ge=0, le=1)
    critical_threshold: float | None = Field(None, ge=0, le=1)
    is_active: bool | None = None


class Budget(BudgetBase):
    """Complete budget schema"""
    cloud_account_id: str
    is_active: bool = True
    current_spend: Decimal = Decimal('0')
    forecasted_spend: Decimal | None = None
    utilization_percentage: float = 0
    
    model_config = ConfigDict(from_attributes=True)


# Cost Forecast schemas
class ForecastType(str, Enum):
    """Forecast types"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class CostForecastBase(BaseModel):
    """Base cost forecast schema"""
    forecast_type: ForecastType
    model_name: str = Field(..., min_length=1, max_length=100)
    model_version: str = Field(..., min_length=1, max_length=50)
    forecast_date: date
    target_date: date
    predicted_cost: Decimal = Field(..., ge=0)
    confidence_interval_lower: Decimal | None = Field(None, ge=0)
    confidence_interval_upper: Decimal | None = Field(None, ge=0)
    confidence_score: float = Field(..., ge=0, le=1)
    mae: float | None = Field(None, ge=0)  # Mean Absolute Error
    mape: float | None = Field(None, ge=0)  # Mean Absolute Percentage Error
    rmse: float | None = Field(None, ge=0)  # Root Mean Square Error
    scope_type: str | None = None
    scope_filter: dict[str, Any] | None = None
    training_data_period: str | None = None
    features_used: dict[str, Any] | None = None


class CostForecastCreate(CostForecastBase):
    """Schema for creating cost forecasts"""
    cloud_account_id: str


class CostForecast(CostForecastBase):
    """Complete cost forecast schema"""
    cloud_account_id: str
    generated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Cost Alert schemas
class AlertType(str, Enum):
    """Alert types"""
    BUDGET_THRESHOLD = "budget_threshold"
    ANOMALY = "anomaly"
    COST_SPIKE = "cost_spike"
    FORECAST_DEVIATION = "forecast_deviation"


class Severity(str, Enum):
    """Severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class CostAlertBase(BaseModel):
    """Base cost alert schema"""
    alert_type: AlertType
    severity: Severity
    title: str = Field(..., min_length=1, max_length=500)
    message: str = Field(..., min_length=1)
    trigger_value: Decimal | None = None
    threshold_value: Decimal | None = None
    threshold_percentage: float | None = None
    scope_type: str | None = None
    scope_filter: dict[str, Any] | None = None
    alert_data: dict[str, Any] | None = None


class CostAlertCreate(CostAlertBase):
    """Schema for creating cost alerts"""
    cloud_account_id: str


class CostAlertUpdate(BaseModel):
    """Schema for updating cost alerts"""
    status: str | None = Field(None, pattern=r"^(active|acknowledged|resolved)$")
    acknowledged_by: str | None = None
    notes: str | None = None


class CostAlert(CostAlertBase):
    """Complete cost alert schema"""
    cloud_account_id: str
    status: str = "active"
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    
    model_config = ConfigDict(from_attributes=True)


# Cost Anomaly schemas
class AnomalyType(str, Enum):
    """Anomaly types"""
    SPIKE = "spike"
    DROP = "drop"
    UNUSUAL_PATTERN = "unusual_pattern"


class CostAnomalyBase(BaseModel):
    """Base cost anomaly schema"""
    anomaly_type: AnomalyType
    severity: Severity
    detected_date: date
    anomaly_start_date: date
    anomaly_end_date: date | None = None
    expected_value: Decimal = Field(..., ge=0)
    actual_value: Decimal = Field(..., ge=0)
    deviation_percentage: float
    anomaly_score: float = Field(..., ge=0)
    scope_type: str | None = None
    scope_filter: dict[str, Any] | None = None
    investigation_notes: str | None = None
    analysis_data: dict[str, Any] | None = None


class CostAnomalyCreate(CostAnomalyBase):
    """Schema for creating cost anomalies"""
    cloud_account_id: str


class CostAnomalyUpdate(BaseModel):
    """Schema for updating cost anomalies"""
    status: str | None = Field(None, pattern=r"^(investigating|explained|resolved)$")
    investigation_notes: str | None = None


class CostAnomaly(CostAnomalyBase):
    """Complete cost anomaly schema"""
    cloud_account_id: str
    status: str = "investigating"
    detected_at: datetime
    resolved_at: datetime | None = None
    
    model_config = ConfigDict(from_attributes=True)


# Cost Trend schemas
class TrendDirection(str, Enum):
    """Trend directions"""
    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"


class CostTrendBase(BaseModel):
    """Base cost trend schema"""
    period_start: date
    period_end: date
    period_type: str = Field(..., pattern=r"^(daily|weekly|monthly)$")
    total_cost: Decimal = Field(..., ge=0)
    cost_change: Decimal | None = None
    cost_change_percentage: float | None = None
    trend_direction: TrendDirection | None = None
    trend_strength: float | None = Field(None, ge=0, le=1)
    scope_type: str | None = None
    scope_filter: dict[str, Any] | None = None


class CostTrendCreate(CostTrendBase):
    """Schema for creating cost trends"""
    cloud_account_id: str


class CostTrend(CostTrendBase):
    """Complete cost trend schema"""
    cloud_account_id: str
    calculated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Service Cost schemas
class ServiceCostBase(BaseModel):
    """Base service cost schema"""
    service_name: str = Field(..., min_length=1, max_length=255)
    service_category: str | None = None
    billing_period: str = Field(..., min_length=1, max_length=50)
    usage_start_date: date
    usage_end_date: date
    total_cost: Decimal = Field(..., ge=0)
    compute_cost: Decimal | None = Field(None, ge=0)
    storage_cost: Decimal | None = Field(None, ge=0)
    network_cost: Decimal | None = Field(None, ge=0)
    other_cost: Decimal | None = Field(None, ge=0)
    usage_quantity: Decimal | None = Field(None, ge=0)
    usage_unit: str | None = None
    cost_growth_rate: float | None = None
    usage_growth_rate: float | None = None


class ServiceCostCreate(ServiceCostBase):
    """Schema for creating service costs"""
    cloud_account_id: str


class ServiceCost(ServiceCostBase):
    """Complete service cost schema"""
    cloud_account_id: str
    resource_count: int
    
    model_config = ConfigDict(from_attributes=True)


# Tag Cost schemas
class TagCostBase(BaseModel):
    """Base tag cost schema"""
    tag_key: str = Field(..., min_length=1, max_length=255)
    tag_value: str = Field(..., min_length=1, max_length=255)
    billing_period: str = Field(..., min_length=1, max_length=50)
    usage_start_date: date
    usage_end_date: date
    total_cost: Decimal = Field(..., ge=0)
    resource_count: int | None = Field(None, ge=0)


class TagCostCreate(TagCostBase):
    """Schema for creating tag costs"""
    cloud_account_id: str


class TagCost(TagCostBase):
    """Complete tag cost schema"""
    cloud_account_id: str
    
    model_config = ConfigDict(from_attributes=True)


# Savings Opportunity schemas
class OpportunityType(str, Enum):
    """Opportunity types"""
    RESERVED_INSTANCES = "reserved_instances"
    SAVINGS_PLAN = "savings_plan"
    SPOT_INSTANCES = "spot_instances"
    RIGHT_SIZING = "right_sizing"
    SCHEDULING = "scheduling"
    STORAGE_OPTIMIZATION = "storage_optimization"
    NETWORK_OPTIMIZATION = "network_optimization"


class SavingsOpportunityBase(BaseModel):
    """Base savings opportunity schema"""
    opportunity_type: OpportunityType
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    resource_ids: list[str] | None = None
    service_name: str | None = None
    current_cost: Decimal | None = Field(None, ge=0)
    potential_savings: Decimal = Field(..., gt=0)
    savings_percentage: float | None = Field(None, ge=0, le=100)
    investment_required: Decimal | None = Field(None, ge=0)
    roi_period_months: int | None = Field(None, gt=0)
    effort: Effort
    risk_level: str = Field(..., pattern=r"^(low|medium|high)$")
    confidence_score: float = Field(..., ge=0, le=1)
    status: str = Field(default="identified", pattern=r"^(identified|evaluating|implemented)$")
    valid_until: date | None = None
    implementation_deadline: date | None = None
    analysis_data: dict[str, Any] | None = None


class SavingsOpportunityCreate(SavingsOpportunityBase):
    """Schema for creating savings opportunities"""
    cloud_account_id: str


class SavingsOpportunityUpdate(BaseModel):
    """Schema for updating savings opportunities"""
    status: str | None = Field(None, pattern=r"^(identified|evaluating|implemented)$")
    notes: str | None = None


class SavingsOpportunity(SavingsOpportunityBase):
    """Complete savings opportunity schema"""
    cloud_account_id: str
    identified_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# Response schemas
class CostMetrics(BaseModel):
    """Cost metrics response schema"""
    total_cost: Decimal
    cost_change: Decimal
    cost_change_percentage: float
    daily_average: Decimal
    projected_monthly: Decimal
    services_breakdown: dict[str, Decimal]
    tags_breakdown: dict[str, Decimal]


class ResourceCostAnalysis(BaseModel):
    """Resource cost analysis response schema"""
    resource_id: str
    resource_type: str
    service_name: str
    current_cost: Decimal
    utilization_score: float
    efficiency_score: float
    optimization_potential: Decimal
    recommendations: list[str]


class CostAnomalyDetection(BaseModel):
    """Cost anomaly detection response schema"""
    date: date
    actual_cost: Decimal
    expected_cost: Decimal
    deviation_percentage: float
    severity: Severity


class CostTrendAnalysis(BaseModel):
    """Cost trend analysis response schema"""
    period_start: date
    period_end: date
    total_cost: Decimal
    cost_change: Decimal
    cost_change_percentage: float
    trend_direction: TrendDirection
    trend_strength: float


class OptimizationSummary(BaseModel):
    """Optimization summary response schema"""
    account_id: str
    account_name: str
    provider: str
    analysis_period: int
    recommendations_generated: int
    recommendations_stored: int
    total_monthly_savings: Decimal
    average_confidence: float
    recommendations_by_type: dict[str, int]
    recommendations_by_priority: dict[str, int]
    generated_at: datetime


class SyncResult(BaseModel):
    """Cost data sync result schema"""
    account_id: str
    account_name: str
    provider: str
    period: str
    cost_records_stored: int
    usage_records_stored: int
    sync_time: datetime


# Health and status schemas
class HealthCheck(BaseModel):
    """Health check response schema"""
    status: str
    timestamp: datetime
    version: str
    database: dict[str, Any]
    redis: dict[str, Any] | None = None


class SystemStatus(BaseModel):
    """System status response schema"""
    status: str
    uptime: str
    active_accounts: int
    total_recommendations: int
    total_savings: Decimal
    last_sync: datetime | None = None


# Query parameters
class DateRangeQuery(BaseModel):
    """Date range query parameters"""
    start_date: date
    end_date: date
    
    @field_validator('end_date')
    @classmethod
    def end_date_after_start_date(cls, v, info):
        start_date = info.data.get('start_date')
        if start_date and v <= start_date:
            raise ValueError('end_date must be after start_date')
        return v


class PaginationQuery(BaseModel):
    """Pagination query parameters"""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class RecommendationQuery(PaginationQuery):
    """Recommendation query parameters"""
    status: str | None = Field(None, pattern=r"^(pending|approved|rejected|implemented)$")
    type: RecommendationType | None = None
    priority: Priority | None = None
    min_savings: Decimal | None = Field(None, ge=0)


class CostAnalysisQuery(DateRangeQuery):
    """Cost analysis query parameters"""
    group_by: str | None = Field(None, pattern=r"^(service|tag|resource|day)$")
    include_forecast: bool = False


# Error response schemas
class ErrorDetail(BaseModel):
    """Error detail schema"""
    field: str | None = None
    message: str
    code: str | None = None


class ErrorResponse(BaseModel):
    """Error response schema"""
    error: str
    message: str
    details: list[ErrorDetail] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Success response schemas
class SuccessResponse(BaseModel):
    """Success response schema"""
    success: bool = True
    message: str
    data: Any | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BatchResponse(BaseModel):
    """Batch operation response schema"""
    success_count: int
    error_count: int
    total_count: int
    errors: list[ErrorDetail] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

