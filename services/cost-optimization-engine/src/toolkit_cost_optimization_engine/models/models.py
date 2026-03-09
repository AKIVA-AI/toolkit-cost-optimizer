"""
Database models for Toolkit Cost Optimization Engine
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Numeric as SQLDecimal,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from ..core.database import Base


class CloudAccount(Base):
    """
    Cloud provider account configuration
    """

    __tablename__ = "cloud_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)  # aws, azure, gcp
    account_id = Column(String(255), nullable=False)
    region = Column(String(100), nullable=False)

    # Credentials (stored encrypted via credential_encryption module)
    access_key = Column(Text, nullable=True)
    secret_key = Column(Text, nullable=True)
    tenant_id = Column(String(255), nullable=True)
    subscription_id = Column(String(255), nullable=True)
    project_id = Column(String(255), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_connected = Column(Boolean, default=False)
    last_sync = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    description = Column(Text, nullable=True)
    tags = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    cost_data = relationship("CostData", back_populates="cloud_account")
    resource_usage = relationship("ResourceUsage", back_populates="cloud_account")
    service_costs = relationship("ServiceCost", back_populates="cloud_account")

    __table_args__ = (
        Index("idx_cloud_accounts_provider", "provider"),
        Index("idx_cloud_accounts_active", "is_active"),
        UniqueConstraint("provider", "account_id", "region", name="uq_cloud_account"),
    )


class CostData(Base):
    """
    Raw cost data from cloud providers
    """

    __tablename__ = "cost_data"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Time dimensions
    usage_start_date = Column(Date, nullable=False)
    usage_end_date = Column(Date, nullable=False)
    billing_period = Column(String(50), nullable=False)

    # Cost dimensions
    service_name = Column(String(255), nullable=False)
    service_category = Column(String(100), nullable=True)
    resource_type = Column(String(255), nullable=True)
    resource_id = Column(String(255), nullable=True)

    # Cost amounts
    usage_quantity = Column(SQLDecimal(15, 6), nullable=True)
    usage_unit = Column(String(50), nullable=True)
    cost_amount = Column(SQLDecimal(15, 4), nullable=False)
    currency = Column(String(3), default="USD")

    # Pricing details
    rate = Column(SQLDecimal(15, 6), nullable=True)
    pricing_model = Column(String(50), nullable=True)  # on-demand, reserved, spot

    # Location and tags
    region = Column(String(100), nullable=True)
    availability_zone = Column(String(50), nullable=True)
    tags = Column(JSON, default=dict, nullable=True)

    # Metadata
    raw_data = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    cloud_account = relationship("CloudAccount", back_populates="cost_data")

    __table_args__ = (
        Index("idx_cost_data_account_date", "cloud_account_id", "usage_start_date"),
        Index("idx_cost_data_service", "service_name"),
        Index("idx_cost_data_cost", "cost_amount"),
        Index("idx_cost_data_billing_period", "billing_period"),
    )


class ResourceUsage(Base):
    """
    Resource usage metrics for optimization analysis
    """

    __tablename__ = "resource_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Resource identification
    resource_id = Column(String(255), nullable=False)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(255), nullable=False)
    service_name = Column(String(255), nullable=False)

    # Time dimensions
    timestamp = Column(DateTime(timezone=True), nullable=False)
    period = Column(String(20), nullable=False)  # hourly, daily

    # Usage metrics
    cpu_utilization = Column(Float, nullable=True)
    memory_utilization = Column(Float, nullable=True)
    disk_utilization = Column(Float, nullable=True)
    network_in = Column(SQLDecimal(15, 6), nullable=True)
    network_out = Column(SQLDecimal(15, 6), nullable=True)
    requests_per_second = Column(Float, nullable=True)

    # Resource configuration
    cpu_cores = Column(Integer, nullable=True)
    memory_gb = Column(Integer, nullable=True)
    disk_gb = Column(Integer, nullable=True)
    instance_type = Column(String(100), nullable=True)

    # Status and location
    status = Column(String(50), nullable=True)  # running, stopped, terminated
    region = Column(String(100), nullable=True)
    availability_zone = Column(String(50), nullable=True)

    # Tags and metadata
    tags = Column(JSON, default=dict, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    cloud_account = relationship("CloudAccount", back_populates="resource_usage")

    __table_args__ = (
        Index("idx_resource_usage_account_time", "cloud_account_id", "timestamp"),
        Index("idx_resource_usage_resource", "resource_id"),
        Index("idx_resource_usage_type", "resource_type"),
        Index("idx_resource_usage_utilization", "cpu_utilization"),
    )


class OptimizationRecommendation(Base):
    """
    Cost optimization recommendations
    """

    __tablename__ = "optimization_recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Recommendation details
    # right_size, schedule, terminate, etc.
    recommendation_type = Column(String(100), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)

    # Target resource
    resource_id = Column(String(255), nullable=True)
    resource_type = Column(String(255), nullable=True)
    service_name = Column(String(255), nullable=True)

    # Financial impact
    current_monthly_cost = Column(SQLDecimal(15, 4), nullable=True)
    projected_monthly_cost = Column(SQLDecimal(15, 4), nullable=True)
    monthly_savings = Column(SQLDecimal(15, 4), nullable=True)
    savings_percentage = Column(Float, nullable=True)

    # Implementation details
    effort = Column(String(20), nullable=False)  # low, medium, high
    risk_level = Column(String(20), nullable=False)  # low, medium, high
    confidence_score = Column(Float, nullable=False)

    # Status and lifecycle
    status = Column(String(20), default="pending")  # pending, approved, rejected, implemented
    priority = Column(String(20), nullable=False)  # low, medium, high, critical

    # Implementation details
    implementation_steps = Column(JSON, default=list, nullable=True)
    rollback_plan = Column(Text, nullable=True)

    # Metadata
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    implemented_at = Column(DateTime(timezone=True), nullable=True)

    # Additional data
    analysis_data = Column(JSON, default=dict, nullable=True)
    tags = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_recommendations_account", "cloud_account_id"),
        Index("idx_recommendations_type", "recommendation_type"),
        Index("idx_recommendations_status", "status"),
        Index("idx_recommendations_priority", "priority"),
        Index("idx_recommendations_savings", "monthly_savings"),
    )


class Budget(Base):
    """
    Budget definitions and tracking
    """

    __tablename__ = "budgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Budget details
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    budget_type = Column(String(50), nullable=False)  # monthly, quarterly, yearly

    # Financial limits
    amount = Column(SQLDecimal(15, 4), nullable=False)
    currency = Column(String(3), default="USD")

    # Time period
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    # Scope
    scope_type = Column(String(50), nullable=False)  # account, service, tag, resource
    scope_filter = Column(JSON, default=dict, nullable=True)

    # Alert thresholds
    alert_threshold = Column(Float, default=0.8)  # 80%
    critical_threshold = Column(Float, default=0.95)  # 95%

    # Status
    is_active = Column(Boolean, default=True)

    # Current spending (calculated)
    current_spend = Column(SQLDecimal(15, 4), default=0)
    forecasted_spend = Column(SQLDecimal(15, 4), nullable=True)
    utilization_percentage = Column(Float, default=0)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_budgets_account", "cloud_account_id"),
        Index("idx_budgets_period", "start_date", "end_date"),
        Index("idx_budgets_active", "is_active"),
        CheckConstraint("amount > 0", name="check_budget_amount_positive"),
        CheckConstraint(
            "alert_threshold > 0 AND alert_threshold <= 1",
            name="check_alert_threshold",
        ),
        CheckConstraint(
            "critical_threshold > 0 AND critical_threshold <= 1",
            name="check_critical_threshold",
        ),
    )


class CostForecast(Base):
    """
    Cost forecasting data
    """

    __tablename__ = "cost_forecasts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Forecast details
    forecast_type = Column(String(50), nullable=False)  # daily, weekly, monthly
    model_name = Column(String(100), nullable=False)
    model_version = Column(String(50), nullable=False)

    # Time dimensions
    forecast_date = Column(Date, nullable=False)
    target_date = Column(Date, nullable=False)

    # Forecast values
    predicted_cost = Column(SQLDecimal(15, 4), nullable=False)
    confidence_interval_lower = Column(SQLDecimal(15, 4), nullable=True)
    confidence_interval_upper = Column(SQLDecimal(15, 4), nullable=True)
    confidence_score = Column(Float, nullable=False)

    # Accuracy metrics
    mae = Column(Float, nullable=True)  # Mean Absolute Error
    mape = Column(Float, nullable=True)  # Mean Absolute Percentage Error
    rmse = Column(Float, nullable=True)  # Root Mean Square Error

    # Scope
    scope_type = Column(String(50), nullable=True)
    scope_filter = Column(JSON, default=dict, nullable=True)

    # Metadata
    generated_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    training_data_period = Column(String(50), nullable=True)
    features_used = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_forecasts_account", "cloud_account_id"),
        Index("idx_forecasts_date", "forecast_date"),
        Index("idx_forecasts_target", "target_date"),
        Index("idx_forecasts_model", "model_name"),
    )


class CostAlert(Base):
    """
    Cost alerts and notifications
    """

    __tablename__ = "cost_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Alert details
    alert_type = Column(String(50), nullable=False)  # budget_threshold, anomaly, cost_spike
    severity = Column(String(20), nullable=False)  # info, warning, critical
    title = Column(String(500), nullable=False)
    message = Column(Text, nullable=False)

    # Trigger conditions
    trigger_value = Column(SQLDecimal(15, 4), nullable=True)
    threshold_value = Column(SQLDecimal(15, 4), nullable=True)
    threshold_percentage = Column(Float, nullable=True)

    # Status
    status = Column(String(20), default="active")  # active, acknowledged, resolved
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Scope
    scope_type = Column(String(50), nullable=True)
    scope_filter = Column(JSONB, nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Additional data
    alert_data = Column(JSON, default=dict, nullable=True)

    __table_args__ = (
        Index("idx_alerts_account", "cloud_account_id"),
        Index("idx_alerts_type", "alert_type"),
        Index("idx_alerts_severity", "severity"),
        Index("idx_alerts_status", "status"),
        Index("idx_alerts_created", "created_at"),
    )


class CostAnomaly(Base):
    """
    Detected cost anomalies
    """

    __tablename__ = "cost_anomalies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Anomaly details
    anomaly_type = Column(String(50), nullable=False)  # spike, drop, unusual_pattern
    severity = Column(String(20), nullable=False)  # low, medium, high, critical

    # Time dimensions
    detected_date = Column(Date, nullable=False)
    anomaly_start_date = Column(Date, nullable=False)
    anomaly_end_date = Column(Date, nullable=True)

    # Anomaly metrics
    expected_value = Column(SQLDecimal(15, 4), nullable=False)
    actual_value = Column(SQLDecimal(15, 4), nullable=False)
    deviation_percentage = Column(Float, nullable=False)
    anomaly_score = Column(Float, nullable=False)

    # Scope
    scope_type = Column(String(50), nullable=True)
    scope_filter = Column(JSONB, nullable=True)

    # Status
    status = Column(String(20), default="investigating")  # investigating, explained, resolved
    investigation_notes = Column(Text, nullable=True)

    # Metadata
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Analysis data
    analysis_data = Column(JSON, default=dict, nullable=True)

    __table_args__ = (
        Index("idx_anomalies_account", "cloud_account_id"),
        Index("idx_anomalies_type", "anomaly_type"),
        Index("idx_anomalies_severity", "severity"),
        Index("idx_anomalies_detected", "detected_date"),
        Index("idx_anomalies_score", "anomaly_score"),
    )


class OptimizationRule(Base):
    """
    Custom optimization rules
    """

    __tablename__ = "optimization_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Rule details
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    rule_type = Column(String(50), nullable=False)  # threshold, pattern, custom

    # Rule definition
    conditions = Column(JSON, nullable=False)
    actions = Column(JSON, nullable=False)

    # Configuration
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)

    # Execution
    schedule = Column(String(100), nullable=True)  # cron expression
    last_run = Column(DateTime(timezone=True), nullable=True)
    next_run = Column(DateTime(timezone=True), nullable=True)

    # Statistics
    total_runs = Column(Integer, default=0)
    successful_runs = Column(Integer, default=0)
    total_savings = Column(SQLDecimal(15, 4), default=0)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_rules_active", "is_active"),
        Index("idx_rules_type", "rule_type"),
        Index("idx_rules_priority", "priority"),
    )


class CostTrend(Base):
    """
    Cost trend analysis data
    """

    __tablename__ = "cost_trends"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Time dimensions
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_type = Column(String(20), nullable=False)  # daily, weekly, monthly

    # Trend metrics
    total_cost = Column(SQLDecimal(15, 4), nullable=False)
    cost_change = Column(SQLDecimal(15, 4), nullable=True)
    cost_change_percentage = Column(Float, nullable=True)

    # Trend analysis
    trend_direction = Column(String(20), nullable=True)  # increasing, decreasing, stable
    trend_strength = Column(Float, nullable=True)  # 0-1 scale

    # Scope
    scope_type = Column(String(50), nullable=True)
    scope_filter = Column(JSON, default=dict, nullable=True)

    # Metadata
    calculated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_trends_account", "cloud_account_id"),
        Index("idx_trends_period", "period_start", "period_end"),
        Index("idx_trends_direction", "trend_direction"),
    )


class ResourceMetrics(Base):
    """
    Detailed resource metrics for optimization
    """

    __tablename__ = "resource_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Resource identification
    resource_id = Column(String(255), nullable=False)
    resource_type = Column(String(255), nullable=False)

    # Time dimensions
    timestamp = Column(DateTime(timezone=True), nullable=False)

    # Performance metrics
    cpu_avg = Column(Float, nullable=True)
    cpu_max = Column(Float, nullable=True)
    cpu_min = Column(Float, nullable=True)
    memory_avg = Column(Float, nullable=True)
    memory_max = Column(Float, nullable=True)
    memory_min = Column(Float, nullable=True)
    disk_avg = Column(Float, nullable=True)
    disk_max = Column(Float, nullable=True)
    disk_min = Column(Float, nullable=True)

    # Network metrics
    network_in_avg = Column(SQLDecimal(15, 6), nullable=True)
    network_out_avg = Column(SQLDecimal(15, 6), nullable=True)

    # Application metrics
    request_count = Column(Integer, nullable=True)
    error_rate = Column(Float, nullable=True)
    response_time_avg = Column(Float, nullable=True)

    # Efficiency metrics
    cost_per_request = Column(SQLDecimal(15, 6), nullable=True)
    cost_per_hour = Column(SQLDecimal(15, 4), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_metrics_account_time", "cloud_account_id", "timestamp"),
        Index("idx_metrics_resource", "resource_id"),
        Index("idx_metrics_type", "resource_type"),
    )


class ServiceCost(Base):
    """
    Service-level cost breakdown
    """

    __tablename__ = "service_costs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Service identification
    service_name = Column(String(255), nullable=False)
    service_category = Column(String(100), nullable=True)

    # Time dimensions
    billing_period = Column(String(50), nullable=False)
    usage_start_date = Column(Date, nullable=False)
    usage_end_date = Column(Date, nullable=False)

    # Cost breakdown
    total_cost = Column(SQLDecimal(15, 4), nullable=False)
    compute_cost = Column(SQLDecimal(15, 4), nullable=True)
    storage_cost = Column(SQLDecimal(15, 4), nullable=True)
    network_cost = Column(SQLDecimal(15, 4), nullable=True)
    other_cost = Column(SQLDecimal(15, 4), nullable=True)

    # Usage metrics
    usage_quantity = Column(SQLDecimal(15, 6), nullable=True)
    usage_unit = Column(String(50), nullable=True)

    # Growth metrics
    cost_growth_rate = Column(Float, nullable=True)
    usage_growth_rate = Column(Float, nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    cloud_account = relationship("CloudAccount", back_populates="service_costs")

    __table_args__ = (
        Index("idx_service_costs_account", "cloud_account_id"),
        Index("idx_service_costs_service", "service_name"),
        Index("idx_service_costs_period", "billing_period"),
        Index("idx_service_costs_total", "total_cost"),
    )


class TagCost(Base):
    """
    Cost breakdown by tags
    """

    __tablename__ = "tag_costs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Tag identification
    tag_key = Column(String(255), nullable=False)
    tag_value = Column(String(255), nullable=False)

    # Time dimensions
    billing_period = Column(String(50), nullable=False)
    usage_start_date = Column(Date, nullable=False)
    usage_end_date = Column(Date, nullable=False)

    # Cost metrics
    total_cost = Column(SQLDecimal(15, 4), nullable=False)
    resource_count = Column(Integer, nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_tag_costs_account", "cloud_account_id"),
        Index("idx_tag_costs_key", "tag_key"),
        Index("idx_tag_costs_value", "tag_value"),
        Index("idx_tag_costs_period", "billing_period"),
        UniqueConstraint(
            "cloud_account_id",
            "tag_key",
            "tag_value",
            "billing_period",
            name="uq_tag_cost",
        ),
    )


class SavingsOpportunity(Base):
    """
    Identified savings opportunities
    """

    __tablename__ = "savings_opportunities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id"), nullable=False)

    # Opportunity details
    opportunity_type = Column(String(100), nullable=False)  # reserved_instances, savings_plan, etc.
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)

    # Target resources
    resource_ids = Column(JSONB, nullable=True)  # Array of resource IDs
    service_name = Column(String(255), nullable=True)

    # Financial impact
    current_cost = Column(SQLDecimal(15, 4), nullable=True)
    potential_savings = Column(SQLDecimal(15, 4), nullable=False)
    savings_percentage = Column(Float, nullable=True)
    investment_required = Column(SQLDecimal(15, 4), nullable=True)
    roi_period_months = Column(Integer, nullable=True)

    # Implementation details
    effort = Column(String(20), nullable=False)
    risk_level = Column(String(20), nullable=False)
    confidence_score = Column(Float, nullable=False)

    # Status
    status = Column(String(20), default="identified")  # identified, evaluating, implemented

    # Time constraints
    valid_until = Column(Date, nullable=True)
    implementation_deadline = Column(Date, nullable=True)

    # Metadata
    identified_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    analysis_data = Column(JSON, default=dict, nullable=True)

    __table_args__ = (
        Index("idx_savings_account", "cloud_account_id"),
        Index("idx_savings_type", "opportunity_type"),
        Index("idx_savings_status", "status"),
        Index("idx_savings_potential", "potential_savings"),
    )
