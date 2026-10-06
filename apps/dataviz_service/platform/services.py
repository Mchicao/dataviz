"""Platform-level services that run on top of tenant jobs.

This module wires the contract models into stateless, tenant-scoped services:

  * :class:`RefreshScheduler` — creates, lists, and triggers dataset refresh
    schedules. ``compute_due``/``tick`` drive the loop on an injected clock.
  * :class:`SubscriptionService` — manages subscriptions and dispatches alerts
    to the configured :class:`MessageSink` when a matching trigger fires.
  * :class:`AlertService` — emits :class:`AlertEvent` records and routes them
    through a sink, keeping an audit trail per tenant.
  * :class:`EmbedTokenService` — issues, validates, and revokes short-lived
    bearer tokens bound to a tenant + target, with TTL governed by the clock.
  * :class:`ApiQuotaService` — enforces per-tenant minute/day request windows
    and concurrent-job ceilings on the public API surface.

Design rules enforced everywhere:

  * **Tenant isolation:** every public method takes a :class:`RequestContext`
    and calls :func:`check_authorization` before any state mutation, so a
    foreign tenant can neither read nor mutate another tenant's data.
  * **Deterministic time:** services depend on a :data:`Clock` callable rather
    than ``datetime.utcnow()`` directly, so tests advance time explicitly.
  * **No real I/O:** alerts go through a :class:`MessageSink`; nothing in this
    module sends email, calls a paid API, or performs network I/O.
  * **In-memory state:** persistence is the deployment's responsibility. The
    services expose plain Python objects so a storage layer can mirror them.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta
from typing import Any

from apps.dataviz_service.contracts import (
    AccessDeniedError,
    RequestContext,
    ResourceNotFoundError,
    Scope,
    check_authorization,
)
from apps.dataviz_service.platform.clocks import Clock, SystemClock
from apps.dataviz_service.platform.contracts import (
    AlertChannel,
    AlertEvent,
    AlertSeverity,
    AlertTargetType,
    AlertTrigger,
    ApiQuota,
    EmbedTargetType,
    EmbedToken,
    QuotaExceededError,
    RefreshFrequency,
    RefreshRun,
    RefreshRunStatus,
    RefreshSchedule,
    Subscription,
    TokenRevokedError,
)
from apps.dataviz_service.platform.sinks import LoggingSink, MessageSink

logger = logging.getLogger("dataviz_service.platform.services")


def _new_id(prefix: str) -> str:
    """Return a fresh prefixed identifier (e.g. ``sched-<uuid>``)."""
    return f"{prefix}-{uuid.uuid4()}"


def _require_resource_tenant(resource_tenant_id: str) -> str:
    """Validate a non-empty tenant identifier for resource operations."""
    if not resource_tenant_id or not resource_tenant_id.strip():
        raise ValueError("Resource tenant_id must not be empty")
    return resource_tenant_id.strip()


# =====================================================================
# Scheduled refresh
# =====================================================================


def compute_next_run(
    frequency: RefreshFrequency,
    *,
    hour_of_day: int,
    day_of_week: int,
    from_time: Any,
) -> Any | None:
    """Compute the next ``datetime`` a schedule is due, or ``None`` for MANUAL.

    Uses UTC calendar arithmetic. For ``HOURLY`` the next run is the start of
    the next UTC hour; for ``DAILY`` the next ``hour_of_day`` (today or
    tomorrow); for ``WEEKLY`` the next matching ``day_of_week`` at the
    configured hour. Past timestamps always roll forward to the next future
    slot so a schedule created at 10:00 with ``hour_of_day=9`` first fires the
    following day.
    """
    if frequency == RefreshFrequency.MANUAL:
        return None

    if frequency == RefreshFrequency.HOURLY:
        # Top of the next UTC hour strictly after from_time.
        next_hour = from_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return next_hour

    # DAILY: next occurrence of hour_of_day strictly in the future.
    candidate = from_time.replace(hour=hour_of_day, minute=0, second=0, microsecond=0)
    if candidate <= from_time:
        candidate = candidate + timedelta(days=1)

    if frequency == RefreshFrequency.DAILY:
        return candidate

    # WEEKLY: advance days until weekday matches day_of_week (0=Mon..6=Sun).
    days_ahead = (day_of_week - candidate.weekday()) % 7
    if days_ahead == 0 and candidate <= from_time:
        days_ahead = 7
    return candidate + timedelta(days=days_ahead)


class RefreshScheduler:
    """Manages tenant-scoped dataset refresh schedules and run history.

    The scheduler holds schedules and runs in memory keyed by tenant id. A
    caller-provided ``refresh_runner`` callable (default: no-op success) is
    invoked when a refresh fires, so production code can wire in a real job
    dispatch without coupling the contract module to the job runtime.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        refresh_runner: Any | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        # tenant_id -> {schedule_id -> RefreshSchedule}
        self._schedules: dict[str, dict[str, RefreshSchedule]] = {}
        # tenant_id -> {run_id -> RefreshRun}; run_id is globally unique per tenant
        self._runs: dict[str, dict[str, RefreshRun]] = {}
        # Hook invoked as refresh_runner(ctx, schedule) -> Optional[str error_message]
        self._refresh_runner = refresh_runner

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_schedule(
        self,
        ctx: RequestContext,
        *,
        project_id: str,
        dataset_id: str,
        frequency: RefreshFrequency,
        hour_of_day: int = 0,
        day_of_week: int = 0,
        enabled: bool = True,
    ) -> RefreshSchedule:
        """Create a tenant-owned refresh schedule.

        Requires ``Scope.WRITE``. The ``next_run_at`` is derived from the
        injected clock unless ``frequency == MANUAL``.
        """
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        now = self._clock()
        next_run = (
            None
            if frequency == RefreshFrequency.MANUAL
            else compute_next_run(
                frequency,
                hour_of_day=hour_of_day,
                day_of_week=day_of_week,
                from_time=now,
            )
        )
        schedule = RefreshSchedule(
            id=_new_id("sched"),
            tenant_id=ctx.tenant_id,
            project_id=project_id,
            dataset_id=dataset_id,
            frequency=frequency,
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            enabled=enabled,
            next_run_at=next_run,
            created_at=now,
            updated_at=now,
        )
        self._schedules.setdefault(ctx.tenant_id, {})[schedule.id] = schedule
        logger.info(
            "created refresh schedule tenant=%s schedule=%s frequency=%s next_run_at=%s",
            ctx.tenant_id,
            schedule.id,
            frequency.value,
            next_run,
        )
        return schedule

    def list_schedules(self, ctx: RequestContext) -> list[RefreshSchedule]:
        """Return all schedules owned by the caller's tenant (READ scope)."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        return list(self._schedules.get(ctx.tenant_id, {}).values())

    def get_schedule(self, ctx: RequestContext, schedule_id: str) -> RefreshSchedule:
        """Return a schedule by id, scoped to the caller's tenant."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        bucket = self._schedules.get(ctx.tenant_id, {})
        if schedule_id not in bucket:
            raise ResourceNotFoundError("RefreshSchedule", schedule_id)
        return bucket[schedule_id]

    def disable_schedule(self, ctx: RequestContext, schedule_id: str) -> RefreshSchedule:
        """Disable a schedule (WRITE scope). Disabled schedules never fire."""
        return self._patch_schedule(ctx, schedule_id, enabled=False)

    def enable_schedule(self, ctx: RequestContext, schedule_id: str) -> RefreshSchedule:
        """Re-enable a schedule (WRITE scope) and recompute ``next_run_at``."""
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        bucket = self._schedules.get(ctx.tenant_id, {})
        if schedule_id not in bucket:
            raise ResourceNotFoundError("RefreshSchedule", schedule_id)
        current = bucket[schedule_id]
        now = self._clock()
        next_run = (
            None
            if current.frequency == RefreshFrequency.MANUAL
            else compute_next_run(
                current.frequency,
                hour_of_day=current.hour_of_day,
                day_of_week=current.day_of_week,
                from_time=now,
            )
        )
        updated = current.model_copy(
            update={
                "enabled": True,
                "next_run_at": next_run,
                "updated_at": now,
            }
        )
        bucket[schedule_id] = updated
        return updated

    def delete_schedule(self, ctx: RequestContext, schedule_id: str) -> None:
        """Delete a schedule (WRITE scope). No-op if it does not exist."""
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        self._schedules.get(ctx.tenant_id, {}).pop(schedule_id, None)

    def _patch_schedule(
        self, ctx: RequestContext, schedule_id: str, **changes: Any
    ) -> RefreshSchedule:
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        bucket = self._schedules.get(ctx.tenant_id, {})
        if schedule_id not in bucket:
            raise ResourceNotFoundError("RefreshSchedule", schedule_id)
        current = bucket[schedule_id]
        updates = dict(changes)
        updates["updated_at"] = self._clock()
        updated = current.model_copy(update=updates)
        bucket[schedule_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def compute_due(self, ctx: RequestContext, now: Any | None = None) -> list[RefreshSchedule]:
        """Return enabled schedules whose ``next_run_at`` is due on or before ``now``."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        effective = now if now is not None else self._clock()
        due: list[RefreshSchedule] = []
        for schedule in self._schedules.get(ctx.tenant_id, {}).values():
            if not schedule.enabled:
                continue
            if schedule.next_run_at is None:
                continue
            if schedule.next_run_at <= effective:
                due.append(schedule)
        return due

    def trigger_refresh(
        self,
        ctx: RequestContext,
        schedule_id: str,
    ) -> RefreshRun:
        """Execute one refresh for ``schedule_id``.

        Records a :class:`RefreshRun`, invokes the optional ``refresh_runner``
        hook to determine success/failure, advances ``last_run_at`` and
        ``next_run_at`` on the schedule, and returns the run record. Requires
        ``Scope.RUN`` (running a job is distinct from authoring a schedule).
        """
        check_authorization(ctx, {Scope.RUN}, ctx.tenant_id)
        bucket = self._schedules.get(ctx.tenant_id, {})
        if schedule_id not in bucket:
            raise ResourceNotFoundError("RefreshSchedule", schedule_id)
        schedule = bucket[schedule_id]
        started_at = self._clock()

        run_id = _new_id("run")
        run = RefreshRun(
            id=run_id,
            schedule_id=schedule_id,
            tenant_id=ctx.tenant_id,
            status=RefreshRunStatus.RUNNING,
            started_at=started_at,
        )
        self._runs.setdefault(ctx.tenant_id, {})[run_id] = run

        error_message: str | None = None
        try:
            if self._refresh_runner is not None:
                error_message = self._refresh_runner(ctx, schedule)
        except Exception:  # pragma: no cover - defensive: runner must not crash pipeline
            logger.exception(
                "refresh_runner raised for tenant=%s schedule=%s", ctx.tenant_id, schedule_id
            )
            error_message = "Refresh runner raised an unexpected error."

        completed_at = self._clock()
        if error_message is not None:
            finished = run.model_copy(
                update={
                    "status": RefreshRunStatus.FAILED,
                    "completed_at": completed_at,
                    "error_message": error_message,
                }
            )
        else:
            finished = run.model_copy(
                update={"status": RefreshRunStatus.COMPLETED, "completed_at": completed_at}
            )
        self._runs[ctx.tenant_id][run_id] = finished

        # Advance schedule timing regardless of success/failure so a transient
        # failure does not pin the schedule to the past and starve later runs.
        next_run = (
            None
            if schedule.frequency == RefreshFrequency.MANUAL
            else compute_next_run(
                schedule.frequency,
                hour_of_day=schedule.hour_of_day,
                day_of_week=schedule.day_of_week,
                from_time=started_at,
            )
        )
        updates: dict[str, object] = {
            "next_run_at": next_run,
            "updated_at": completed_at,
        }
        if error_message is None:
            updates["last_run_at"] = completed_at
        bucket[schedule_id] = schedule.model_copy(update=updates)
        logger.info(
            "refresh executed tenant=%s schedule=%s run=%s status=%s",
            ctx.tenant_id,
            schedule_id,
            run_id,
            finished.status.value,
        )
        return finished

    def tick(self, ctx: RequestContext) -> list[RefreshRun]:
        """Fire every due schedule for the caller's tenant at the current clock.

        Convenience entrypoint for a scheduler loop: returns the list of runs
        produced by this tick, in the order they were executed.
        """
        check_authorization(ctx, {Scope.RUN}, ctx.tenant_id)
        due = self.compute_due(ctx)
        runs: list[RefreshRun] = []
        for schedule in due:
            runs.append(self.trigger_refresh(ctx, schedule.id))
        return runs

    def list_runs(self, ctx: RequestContext, schedule_id: str | None = None) -> list[RefreshRun]:
        """Return run history for the caller's tenant, optionally filtered by schedule."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        runs = list(self._runs.get(ctx.tenant_id, {}).values())
        if schedule_id is not None:
            runs = [r for r in runs if r.schedule_id == schedule_id]
        return runs


# =====================================================================
# Alert pipeline
# =====================================================================


class AlertService:
    """Persists alert events and routes them to a :class:`MessageSink`.

    The service is intentionally narrow: it builds an :class:`AlertEvent` from
    a triggering context, stores it per-tenant for audit, and delegates actual
    delivery to the injected sink. Sink failures are logged and swallowed so
    a misconfigured destination never blocks the alert pipeline.
    """

    def __init__(self, *, sink: MessageSink | None = None, clock: Clock | None = None) -> None:
        self._sink: MessageSink = sink if sink is not None else LoggingSink()
        self._clock: Clock = clock if clock is not None else SystemClock()
        # tenant_id -> [AlertEvent]
        self._events: dict[str, list[AlertEvent]] = {}

    def emit(
        self,
        ctx: RequestContext,
        *,
        subscription: Subscription,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> AlertEvent:
        """Materialize an :class:`AlertEvent` for ``subscription`` and deliver it.

        Requires ``Scope.READ``: emitting an alert is a read-tier action because
        it neither mutates tenant configuration nor runs jobs. The subscription
        must belong to the caller's tenant; mismatch raises ``AccessDeniedError``.
        """
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        if subscription.tenant_id != ctx.tenant_id:
            raise AccessDeniedError("Cannot emit alert for a subscription owned by another tenant.")
        now = self._clock()
        event = AlertEvent(
            id=_new_id("alert"),
            tenant_id=ctx.tenant_id,
            subscription_id=subscription.id,
            target_type=subscription.target_type,
            target_id=subscription.target_id,
            trigger=subscription.trigger,
            severity=subscription.severity,
            message=message,
            payload=payload or {},
            created_at=now,
        )
        self._events.setdefault(ctx.tenant_id, []).append(event)
        try:
            self._sink.send(event, subscription)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "alert sink failed tenant=%s subscription=%s event=%s",
                ctx.tenant_id,
                subscription.id,
                event.id,
            )
        return event

    def list_events(
        self,
        ctx: RequestContext,
        *,
        subscription_id: str | None = None,
        target_id: str | None = None,
    ) -> list[AlertEvent]:
        """Return alert events for the caller's tenant with optional filters."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        events = list(self._events.get(ctx.tenant_id, []))
        if subscription_id is not None:
            events = [e for e in events if e.subscription_id == subscription_id]
        if target_id is not None:
            events = [e for e in events if e.target_id == target_id]
        return events


# =====================================================================
# Subscriptions
# =====================================================================


class SubscriptionService:
    """Manages subscriptions and translates platform events into alerts.

    The service owns subscription CRUD and exposes ``notify_*`` helpers that
    platform code (e.g. :class:`RefreshScheduler`) calls when a triggering
    event occurs. Matching enabled subscriptions are routed through the
    injected :class:`AlertService`.
    """

    def __init__(
        self,
        *,
        alert_service: AlertService,
        clock: Clock | None = None,
    ) -> None:
        self._alerts = alert_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        # tenant_id -> {subscription_id -> Subscription}
        self._subs: dict[str, dict[str, Subscription]] = {}

    def create_subscription(
        self,
        ctx: RequestContext,
        *,
        principal_id: str,
        target_type: AlertTargetType,
        target_id: str,
        trigger: AlertTrigger,
        channel: AlertChannel,
        destination: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        threshold_expression: str | None = None,
        enabled: bool = True,
    ) -> Subscription:
        """Create a tenant-owned subscription (WRITE scope)."""
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        subscription = Subscription(
            id=_new_id("sub"),
            tenant_id=ctx.tenant_id,
            principal_id=principal_id,
            target_type=target_type,
            target_id=target_id,
            trigger=trigger,
            severity=severity,
            channel=channel,
            destination=destination,
            threshold_expression=threshold_expression,
            enabled=enabled,
            created_at=self._clock(),
        )
        self._subs.setdefault(ctx.tenant_id, {})[subscription.id] = subscription
        return subscription

    def list_subscriptions(
        self,
        ctx: RequestContext,
        *,
        target_id: str | None = None,
        trigger: AlertTrigger | None = None,
    ) -> list[Subscription]:
        """Return subscriptions owned by the caller's tenant with optional filters."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        items = list(self._subs.get(ctx.tenant_id, {}).values())
        if target_id is not None:
            items = [s for s in items if s.target_id == target_id]
        if trigger is not None:
            items = [s for s in items if s.trigger == trigger]
        return items

    def get_subscription(self, ctx: RequestContext, subscription_id: str) -> Subscription:
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        bucket = self._subs.get(ctx.tenant_id, {})
        if subscription_id not in bucket:
            raise ResourceNotFoundError("Subscription", subscription_id)
        return bucket[subscription_id]

    def disable_subscription(self, ctx: RequestContext, subscription_id: str) -> Subscription:
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        bucket = self._subs.get(ctx.tenant_id, {})
        if subscription_id not in bucket:
            raise ResourceNotFoundError("Subscription", subscription_id)
        current = bucket[subscription_id]
        updated = current.model_copy(update={"enabled": False})
        bucket[subscription_id] = updated
        return updated

    def delete_subscription(self, ctx: RequestContext, subscription_id: str) -> None:
        check_authorization(ctx, {Scope.WRITE}, ctx.tenant_id)
        self._subs.get(ctx.tenant_id, {}).pop(subscription_id, None)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _matching(
        self, ctx: RequestContext, *, trigger: AlertTrigger, target_id: str
    ) -> list[Subscription]:
        return [
            s
            for s in self._subs.get(ctx.tenant_id, {}).values()
            if s.enabled and s.trigger == trigger and s.target_id == target_id
        ]

    def notify_refresh_completed(
        self,
        ctx: RequestContext,
        *,
        target_id: str,
        run: RefreshRun,
    ) -> list[AlertEvent]:
        """Dispatch ``DATA_REFRESHED`` alerts for matching subscriptions."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        events: list[AlertEvent] = []
        for sub in self._matching(ctx, trigger=AlertTrigger.DATA_REFRESHED, target_id=target_id):
            event = self._alerts.emit(
                ctx,
                subscription=sub,
                message=f"Refresh for {target_id} completed (run {run.id}).",
                payload={"run_id": run.id, "status": run.status.value},
            )
            events.append(event)
        return events

    def notify_refresh_failed(
        self,
        ctx: RequestContext,
        *,
        target_id: str,
        run: RefreshRun,
    ) -> list[AlertEvent]:
        """Dispatch ``REFRESH_FAILED`` alerts for matching subscriptions."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        events: list[AlertEvent] = []
        for sub in self._matching(ctx, trigger=AlertTrigger.REFRESH_FAILED, target_id=target_id):
            event = self._alerts.emit(
                ctx,
                subscription=sub,
                message=f"Refresh for {target_id} failed: {run.error_message or 'unknown error'}.",
                payload={"run_id": run.id, "error_message": run.error_message},
            )
            events.append(event)
        return events


# =====================================================================
# Embedding tokens
# =====================================================================


class EmbedTokenService:
    """Issues, validates, and revokes short-lived embedding tokens.

    Tokens are opaque URL-safe bearer strings generated with
    :func:`secrets.token_urlsafe`. The service keeps every issued token in
    memory keyed by its string so validation can confirm the token exists,
    has not been revoked, and has not passed its ``expires_at``.

    The default TTL is intentionally short (10 minutes). Callers may pass a
    shorter ``ttl_seconds`` at issue time, but never a longer one than the
    service's configured maximum — this prevents a compromised principal from
    minting arbitrarily long-lived tokens.
    """

    DEFAULT_TTL_SECONDS = 600
    MAX_TTL_SECONDS = 3600

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_ttl_seconds: int = MAX_TTL_SECONDS,
    ) -> None:
        if default_ttl_seconds <= 0 or max_ttl_seconds <= 0:
            raise ValueError("TTL values must be positive")
        if default_ttl_seconds > max_ttl_seconds:
            raise ValueError("default_ttl_seconds cannot exceed max_ttl_seconds")
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._default_ttl = default_ttl_seconds
        self._max_ttl = max_ttl_seconds
        # token_string -> EmbedToken
        self._tokens: dict[str, EmbedToken] = {}

    def issue(
        self,
        ctx: RequestContext,
        *,
        target_type: EmbedTargetType,
        target_id: str,
        ttl_seconds: int | None = None,
    ) -> EmbedToken:
        """Issue a tenant-scoped embed token (READ scope).

        Viewers can embed; the token binds to ``ctx.tenant_id`` and cannot be
        reused to address a different tenant's resource.
        """
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        if ttl > self._max_ttl:
            raise ValueError(f"ttl_seconds exceeds maximum of {self._max_ttl}")
        now = self._clock()
        token = EmbedToken(
            token=secrets.token_urlsafe(32),
            tenant_id=ctx.tenant_id,
            target_type=target_type,
            target_id=target_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
            issued_by=_principal_id(ctx),
        )
        self._tokens[token.token] = token
        logger.info(
            "embed token issued tenant=%s target=%s/%s expires_at=%s",
            ctx.tenant_id,
            target_type.value,
            target_id,
            token.expires_at,
        )
        return token

    def validate(self, token_string: str) -> EmbedToken:
        """Validate a bearer token and return it; raise on unknown/expired/revoked."""
        record = self._tokens.get(token_string)
        if record is None:
            raise TokenRevokedError("Embedding token is unknown.")
        if record.revoked:
            raise TokenRevokedError("Embedding token has been revoked.")
        if record.expires_at <= self._clock():
            raise TokenRevokedError("Embedding token has expired.")
        return record

    def revoke(self, token_string: str) -> EmbedToken:
        """Mark a token as revoked. Idempotent: revoking twice is a no-op.

        Raises :class:`TokenRevokedError` if the token was never issued, since
        revoking an unknown token is almost always a caller bug.
        """
        record = self._tokens.get(token_string)
        if record is None:
            raise TokenRevokedError("Embedding token is unknown.")
        if record.revoked:
            return record
        revoked = record.model_copy(update={"revoked": True})
        self._tokens[token_string] = revoked
        logger.info(
            "embed token revoked tenant=%s target=%s/%s",
            revoked.tenant_id,
            revoked.target_type.value,
            revoked.target_id,
        )
        return revoked


def _principal_id(ctx: RequestContext) -> str:
    """Best-effort principal id for audit fields.

    :class:`RequestContext` carries scopes but not the principal id; we record
    the tenant id by default and let callers override via a custom sink if they
    need richer audit. This keeps the contract module decoupled from the
    identity layer.
    """
    return ctx.tenant_id


# =====================================================================
# Public API quotas
# =====================================================================


class ApiQuotaService:
    """Enforces per-tenant request and concurrency quotas on the public API.

    Windows are **fixed** (not sliding) for simplicity and predictability:

      * **Minute window:** keyed by ``YYYY-MM-DDTHH:MM`` (UTC).
      * **Day window:** keyed by ``YYYY-MM-DD`` (UTC).

    A window's counter resets to zero the first time a request arrives in a
    new window. Concurrency is tracked as a set of in-flight job ids per tenant.
    """

    DEFAULT_QUOTA = ApiQuota(
        tenant_id="__default__",
        requests_per_minute=120,
        requests_per_day=10_000,
        concurrent_jobs=3,
    )

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        # tenant_id -> ApiQuota
        self._quotas: dict[str, ApiQuota] = {}
        # tenant_id -> {"minute_key": str, "minute_count": int,
        #               "day_key": str, "day_count": int}
        self._usage: dict[str, dict[str, Any]] = {}
        # tenant_id -> set[str] of in-flight job ids
        self._inflight: dict[str, set[str]] = {}

    def set_quota(self, ctx: RequestContext, quota: ApiQuota) -> ApiQuota:
        """Configure a tenant's quota. Requires ``Scope.ADMIN``."""
        check_authorization(ctx, {Scope.ADMIN}, ctx.tenant_id)
        if quota.tenant_id != ctx.tenant_id:
            raise AccessDeniedError("Quota tenant_id must match the caller's tenant.")
        self._quotas[ctx.tenant_id] = quota
        return quota

    def get_quota(self, ctx: RequestContext) -> ApiQuota:
        """Return the effective quota for the caller's tenant."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        return self._quotas.get(ctx.tenant_id, self.DEFAULT_QUOTA)

    def check_and_consume(self, ctx: RequestContext) -> None:
        """Consume one request against the minute/day windows.

        Raises :class:`QuotaExceededError` if either ceiling would be exceeded.
        Called by every public API handler before performing real work.
        """
        check_authorization(ctx, set(), ctx.tenant_id)
        quota = self.get_quota(ctx)
        now = self._clock()
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        day_key = now.strftime("%Y-%m-%d")

        bucket = self._usage.get(ctx.tenant_id)
        if bucket is None:
            bucket = {
                "minute_key": minute_key,
                "minute_count": 0,
                "day_key": day_key,
                "day_count": 0,
            }
            self._usage[ctx.tenant_id] = bucket

        if bucket["minute_key"] != minute_key:
            bucket["minute_key"] = minute_key
            bucket["minute_count"] = 0
        if bucket["day_key"] != day_key:
            bucket["day_key"] = day_key
            bucket["day_count"] = 0

        if quota.requests_per_minute != 0 and bucket["minute_count"] >= quota.requests_per_minute:
            raise QuotaExceededError(
                f"Per-minute request quota exceeded ({quota.requests_per_minute}/min)."
            )
        if quota.requests_per_day != 0 and bucket["day_count"] >= quota.requests_per_day:
            raise QuotaExceededError(
                f"Daily request quota exceeded ({quota.requests_per_day}/day)."
            )

        bucket["minute_count"] += 1
        bucket["day_count"] += 1

    def get_usage(self, ctx: RequestContext) -> dict[str, Any]:
        """Return current windowed counters for the caller's tenant (READ scope)."""
        check_authorization(ctx, {Scope.READ}, ctx.tenant_id)
        bucket = self._usage.get(ctx.tenant_id)
        if bucket is None:
            return {"minute_count": 0, "day_count": 0, "inflight_jobs": 0}
        inflight = self._inflight.get(ctx.tenant_id, set())
        return {
            "minute_key": bucket["minute_key"],
            "minute_count": bucket["minute_count"],
            "day_key": bucket["day_key"],
            "day_count": bucket["day_count"],
            "inflight_jobs": len(inflight),
        }

    def acquire_job(self, ctx: RequestContext, job_id: str) -> None:
        """Reserve a concurrent-job slot for ``job_id``.

        Raises :class:`QuotaExceededError` if the tenant's ``concurrent_jobs``
        ceiling is reached. Idempotent: re-acquiring the same ``job_id`` is a
        no-op rather than an error, so a retried dispatch does not falsely fail.
        """
        check_authorization(ctx, {Scope.RUN}, ctx.tenant_id)
        quota = self.get_quota(ctx)
        active = self._inflight.setdefault(ctx.tenant_id, set())
        if job_id in active:
            return
        if quota.concurrent_jobs != 0 and len(active) >= quota.concurrent_jobs:
            raise QuotaExceededError(f"Concurrent job quota reached ({quota.concurrent_jobs}).")
        active.add(job_id)

    def release_job(self, ctx: RequestContext, job_id: str) -> None:
        """Release a previously reserved job slot. No-op if not held."""
        check_authorization(ctx, {Scope.RUN}, ctx.tenant_id)
        self._inflight.get(ctx.tenant_id, set()).discard(job_id)
