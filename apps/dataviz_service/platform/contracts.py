"""Data contracts for platform-level services on top of tenant jobs.

This module captures the immutable Pydantic models that describe:

  * **Scheduled refresh** of tenant datasets (frequency, run history).
  * **Subscriptions** that bind a principal to a target and an alert trigger.
  * **Alert events** emitted by platform operations and dispatched to sinks.
  * **Embedding tokens** used to render authenticated artifacts in foreign frames.
  * **Public API quotas** that throttle outbound tenant traffic per clock window.

Every model is tenant-scoped: a ``tenant_id`` field identifies the owner and
every service validates it against the incoming :class:`RequestContext` via the
shared :func:`apps.dataviz_service.contracts.check_authorization` primitive.

The models intentionally carry no behaviour: they are value objects exchanged
between the stateless services in :mod:`apps.dataviz_service.platform.services`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.dataviz_service.contracts import SanitizedServiceError

# =====================================================================
# Common primitives
# =====================================================================


class PlatformServiceError(SanitizedServiceError):
    """Base class for controlled platform-service exceptions."""


class QuotaExceededError(PlatformServiceError):
    """Raised when a tenant exceeds a configured public API quota."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=429, error_code="QUOTA_EXCEEDED")


class TokenRevokedError(PlatformServiceError):
    """Raised when validating a revoked or expired embedding token."""

    def __init__(self, message: str = "Embedding token is expired or revoked.") -> None:
        super().__init__(message, status_code=401, error_code="EMBED_TOKEN_INVALID")


# =====================================================================
# Scheduled refresh
# =====================================================================


class RefreshFrequency(StrEnum):
    """Supported cadences for dataset refresh schedules."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MANUAL = "manual"


class RefreshRunStatus(StrEnum):
    """Terminal/non-terminal states for an individual refresh execution."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RefreshSchedule(BaseModel):
    """Tenant-scoped schedule describing when a dataset should be refreshed."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique schedule identifier")
    tenant_id: str = Field(..., description="Owning tenant")
    project_id: str = Field(..., description="Project that owns the dataset")
    dataset_id: str = Field(..., description="Target dataset to refresh")
    frequency: RefreshFrequency = Field(..., description="Refresh cadence")
    hour_of_day: int = Field(
        default=0, ge=0, le=23, description="Hour (0-23 UTC) for daily/weekly runs"
    )
    day_of_week: int = Field(
        default=0, ge=0, le=6, description="Weekday (0=Mon..6=Sun) for weekly runs"
    )
    enabled: bool = Field(default=True, description="Whether the schedule is active")
    last_run_at: datetime | None = Field(default=None, description="Last successful run time")
    next_run_at: datetime | None = Field(
        default=None, description="Next due time; None for MANUAL frequency"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("tenant_id", "project_id", "dataset_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Schedule identifier fields must not be empty")
        return v.strip()


class RefreshRun(BaseModel):
    """Immutable record of a single refresh execution against a schedule."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique run identifier")
    schedule_id: str = Field(..., description="Parent schedule identifier")
    tenant_id: str = Field(..., description="Owning tenant")
    status: RefreshRunStatus = Field(..., description="Current run status")
    started_at: datetime = Field(..., description="Run start timestamp")
    completed_at: datetime | None = Field(default=None, description="Run completion timestamp")
    error_message: str | None = Field(
        default=None, description="Sanitized failure message for FAILED runs"
    )

    @field_validator("schedule_id", "tenant_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Run identifier fields must not be empty")
        return v.strip()


# =====================================================================
# Subscriptions and alerts
# =====================================================================


class AlertTrigger(StrEnum):
    """Events that may fire a subscription's alert."""

    DATA_REFRESHED = "data_refreshed"
    REFRESH_FAILED = "refresh_failed"
    THRESHOLD_BREACHED = "threshold_breached"


class AlertSeverity(StrEnum):
    """Severity classification for emitted alert events."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannel(StrEnum):
    """Delivery channel for a subscription's alert payload."""

    EMAIL = "email"
    WEBHOOK = "webhook"


class AlertTargetType(StrEnum):
    """Kind of tenant resource that can be subscribed to."""

    DATASET = "dataset"
    REPORT = "report"
    DASHBOARD = "dashboard"


class Subscription(BaseModel):
    """Binds a principal to receive alerts for a target under a tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique subscription identifier")
    tenant_id: str = Field(..., description="Owning tenant")
    principal_id: str = Field(..., description="Subscriber principal id (sub)")
    target_type: AlertTargetType = Field(..., description="Kind of target")
    target_id: str = Field(..., description="Subscribed resource id")
    trigger: AlertTrigger = Field(..., description="Event that fires the alert")
    severity: AlertSeverity = Field(
        default=AlertSeverity.INFO, description="Severity assigned to emitted events"
    )
    channel: AlertChannel = Field(..., description="Delivery channel")
    destination: str = Field(..., description="Email address or webhook URL")
    threshold_expression: str | None = Field(
        default=None,
        description="Optional serialized threshold expression evaluated by the emitter",
    )
    enabled: bool = Field(default=True, description="Whether the subscription is active")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("tenant_id", "principal_id", "target_id", "destination")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Subscription fields must not be empty")
        return v.strip()


class AlertEvent(BaseModel):
    """Immutable record of an alert dispatched for a subscription."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique event identifier")
    tenant_id: str = Field(..., description="Owning tenant")
    subscription_id: str = Field(..., description="Subscription that produced the event")
    target_type: AlertTargetType = Field(..., description="Kind of target")
    target_id: str = Field(..., description="Target resource id")
    trigger: AlertTrigger = Field(..., description="Trigger that fired")
    severity: AlertSeverity = Field(..., description="Event severity")
    message: str = Field(..., description="Human-readable alert message")
    payload: dict[str, object] = Field(default_factory=dict, description="Structured alert payload")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("tenant_id", "subscription_id", "target_id", "message")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Alert event fields must not be empty")
        return v.strip()


# =====================================================================
# Embedding tokens
# =====================================================================


class EmbedTargetType(StrEnum):
    """Kind of tenant resource that an embedding token can render."""

    REPORT = "report"
    DASHBOARD = "dashboard"


class EmbedToken(BaseModel):
    """Short-lived bearer token authorizing embedded rendering of one target."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(..., description="Opaque bearer string")
    tenant_id: str = Field(..., description="Owning tenant")
    target_type: EmbedTargetType = Field(..., description="Kind of target")
    target_id: str = Field(..., description="Target resource id")
    issued_at: datetime = Field(..., description="Issuance timestamp")
    expires_at: datetime = Field(..., description="Absolute expiry timestamp")
    issued_by: str = Field(..., description="Principal id that requested the token")
    revoked: bool = Field(default=False, description="Whether the token has been revoked")

    @field_validator("token", "tenant_id", "target_id", "issued_by")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Embed token fields must not be empty")
        return v.strip()


# =====================================================================
# Public API quotas
# =====================================================================


class ApiQuota(BaseModel):
    """Per-tenant public-API quota configuration.

    All values are inclusive ceilings: a tenant may consume *up to* the
    configured number within each rolling window.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Owning tenant")
    requests_per_minute: int = Field(
        default=120, ge=0, description="Max public API requests per minute"
    )
    requests_per_day: int = Field(
        default=10000, ge=0, description="Max public API requests per calendar day"
    )
    concurrent_jobs: int = Field(
        default=3, ge=0, description="Max simultaneously running tenant jobs"
    )

    @field_validator("tenant_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Quota tenant_id must not be empty")
        return v.strip()

    @field_validator("requests_per_minute", "requests_per_day", "concurrent_jobs")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Quota limits must be non-negative")
        return v
