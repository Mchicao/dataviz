"""Platform-level services on top of tenant jobs.

Public surface for the platform layer: scheduled refresh, alert/subscription
contracts, embedding tokens, and public-API quotas. All services are
tenant-scoped, deterministic via injected :class:`Clock`, and free of any real
network or paid-service I/O.
"""

from apps.dataviz_service.platform.clocks import Clock, SystemClock, TestClock
from apps.dataviz_service.platform.contracts import (
    AlertChannel,
    AlertEvent,
    AlertSeverity,
    AlertTargetType,
    AlertTrigger,
    ApiQuota,
    EmbedTargetType,
    EmbedToken,
    PlatformServiceError,
    QuotaExceededError,
    RefreshFrequency,
    RefreshRun,
    RefreshRunStatus,
    RefreshSchedule,
    Subscription,
    TokenRevokedError,
)
from apps.dataviz_service.platform.services import (
    AlertService,
    ApiQuotaService,
    EmbedTokenService,
    RefreshScheduler,
    SubscriptionService,
    compute_next_run,
)
from apps.dataviz_service.platform.sinks import InMemorySink, LoggingSink, MessageSink

__all__ = [
    "AlertChannel",
    "AlertEvent",
    "AlertService",
    "AlertSeverity",
    "AlertTargetType",
    "AlertTrigger",
    "ApiQuota",
    "ApiQuotaService",
    "Clock",
    "EmbedTargetType",
    "EmbedToken",
    "EmbedTokenService",
    "InMemorySink",
    "LoggingSink",
    "MessageSink",
    "PlatformServiceError",
    "QuotaExceededError",
    "RefreshFrequency",
    "RefreshRun",
    "RefreshRunStatus",
    "RefreshSchedule",
    "RefreshScheduler",
    "Subscription",
    "SubscriptionService",
    "SystemClock",
    "TestClock",
    "TokenRevokedError",
    "compute_next_run",
]
