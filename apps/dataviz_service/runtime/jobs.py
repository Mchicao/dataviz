"""Tenant-scoped job runtime for the DataVIZ SaaS service.

Adds the execution-control layer that the worker needs to drive a long-running
migration safely on top of :class:`apps.dataviz_service.jobs.StateStore`:

* **Idempotent launch** -- a duplicate ``(tenant_id, idempotency_key)`` returns
  the existing job and never launches duplicate work.
* **Token-based leases** -- ``acquire`` mints an opaque lease token; every
  state transition (``complete``/``fail``/``cancel``) and ``heartbeat`` must
  present the live token, so a stale or restarted worker cannot mutate a job
  owned by another worker.
* **Heartbeat** -- renews both the execution lease and the idempotency
  reservation, so an actively-heartbeating job can never be displaced by a
  duplicate submission.
* **Cancellation** -- cooperative ``cancel`` transitions an active job to
  ``CANCELLED``; workers poll ``is_cancelled`` between steps.
* **Retries** -- ``fail(retryable=True)`` re-arms the job as ``PENDING`` up to
  ``max_attempts`` times before persisting ``FAILED``.
* **Atomic checkpoints** -- ``save_checkpoint``/``load_checkpoint`` are written
  via ``tmp + os.replace`` so a crash never leaves a torn progress file.
* **Cleanup** -- removes the ephemeral workspace, checkpoint and lease entries
  for a job, idempotently.
* **Sanitized boundary** -- ``run_sanitized`` masks any unexpected exception
  behind a ``correlation_id``; controlled ``SanitizedServiceError`` subclasses
  pass through unchanged.

The runtime is single-process / multi-thread (it shares the concurrency model
of :class:`StateStore`). Lock ordering is ``JobRuntime._lock`` (outer) then
``StateStore.lock`` (inner); never invert it.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.dataviz_service.contracts import (
    IdempotencyState,
    Job,
    JobStatus,
    ResourceNotFoundError,
    SanitizedServiceError,
)
from apps.dataviz_service.jobs import StateStore, acquire_or_get_job

logger = logging.getLogger("dataviz_service.runtime.jobs")

# Statuses that no longer accept transitions.
_TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


# =====================================================================
# Sanitized runtime errors (safe to surface through the API boundary)
# =====================================================================


class LeaseLostError(SanitizedServiceError):
    """Raised when a lease token is unknown, expired, or the job is no longer RUNNING."""

    def __init__(self, message: str = "The execution lease was lost or expired.") -> None:
        super().__init__(message, status_code=409, error_code="LEASE_LOST")


class DuplicateRunError(SanitizedServiceError):
    """Raised when an explicit ``run_id`` already exists for the tenant."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            f"A run with id '{run_id}' already exists for this tenant.",
            status_code=409,
            error_code="DUPLICATE_RUN",
        )


class JobNotCancelableError(SanitizedServiceError):
    """Raised when ``cancel`` is called on a job already in a terminal state."""

    def __init__(self, job_id: str, status: JobStatus) -> None:
        super().__init__(
            f"Job '{job_id}' is in terminal state '{status.value}' and cannot be cancelled.",
            status_code=409,
            error_code="JOB_NOT_CANCELABLE",
        )


class SanitizedErrorInfo(BaseModel):
    """Sanitized error descriptor returned by :meth:`JobRuntime.run_sanitized`."""

    model_config = ConfigDict(frozen=True)

    error_code: str = Field(..., description="Public, stable error code suffix")
    message: str = Field(..., description="Safe message for external clients")
    correlation_id: str = Field(..., description="Correlation ID matching server-side logs")


# =====================================================================
# Checkpoint contract
# =====================================================================


class Checkpoint(BaseModel):
    """Durable progress marker for a single job."""

    model_config = ConfigDict(frozen=True)

    step: str = Field(..., description="Identifier of the completed logical step")
    payload: dict[str, Any] = Field(default_factory=dict, description="Step-specific progress data")
    attempt: int = Field(
        default=1, ge=1, description="Attempt number when this checkpoint was written"
    )
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# =====================================================================
# Atomic JSON helpers
# =====================================================================


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tmp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Any | None:
    """Read JSON from ``path``; return ``None`` if missing or unreadable."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# =====================================================================
# JobRuntime
# =====================================================================


class JobRuntime:
    """Execution-control layer over :class:`StateStore`.

    The runtime owns three per-tenant artifacts, all written atomically and
    isolated under ``<tenant_dir>``:

    * ``runtime_state.json`` -- lease tokens and per-job attempt counters.
    * ``checkpoints/<job_id>.json`` -- durable progress per job.
    * the existing ``workspaces/<job_id>`` directory managed by ``StateStore``.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        default_lease_seconds: int = 300,
        default_max_attempts: int = 3,
        default_heartbeat_seconds: int = 30,
    ) -> None:
        if default_lease_seconds <= 0:
            raise ValueError("default_lease_seconds must be positive")
        if default_max_attempts < 1:
            raise ValueError("default_max_attempts must be >= 1")
        if not 0 < default_heartbeat_seconds < default_lease_seconds:
            raise ValueError("default_heartbeat_seconds must be within (0, default_lease_seconds)")
        self.store = store
        self.default_lease_seconds = default_lease_seconds
        self.default_max_attempts = default_max_attempts
        self.default_heartbeat_seconds = default_heartbeat_seconds
        # Outer lock; StateStore.lock is always acquired inner. See module docstring.
        self._lock = __import__("threading").RLock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _tenant_dir(self, tenant_id: str) -> Path:
        """Isolated tenant directory (delegates to StateStore's sanitizer)."""
        return self.store._get_tenant_dir(tenant_id)  # noqa: SLF001 (established accessor)

    def _state_path(self, tenant_id: str) -> Path:
        return self._tenant_dir(tenant_id) / "runtime_state.json"

    def _ckpt_path(self, tenant_id: str, job_id: str) -> Path:
        return self._tenant_dir(tenant_id) / "checkpoints" / f"{job_id}.json"

    # ------------------------------------------------------------------
    # runtime_state.json load / save
    # ------------------------------------------------------------------

    def _load_state(self, tenant_id: str) -> dict[str, Any]:
        data = _read_json(self._state_path(tenant_id))
        if not isinstance(data, dict):
            return {"leases": {}, "attempts": {}}
        data.setdefault("leases", {})
        data.setdefault("attempts", {})
        return data

    def _save_state(self, tenant_id: str, state: dict[str, Any]) -> None:
        _atomic_write_json(self._state_path(tenant_id), state)

    # ------------------------------------------------------------------
    # Lease primitives
    # ------------------------------------------------------------------

    def _put_lease(
        self,
        state: dict[str, Any],
        job_id: str,
        token: str,
        expires_at: datetime,
    ) -> None:
        state["leases"][job_id] = {"token": token, "expires_at": expires_at.isoformat()}

    def _revoke_lease(self, state: dict[str, Any], job_id: str) -> None:
        state["leases"].pop(job_id, None)

    def _require_active_lease(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate the lease token is live and not expired; raise LeaseLostError otherwise."""
        owned = state is not None
        if state is None:
            state = self._load_state(tenant_id)
        lease = state["leases"].get(job_id)
        if lease is None or lease.get("token") != lease_token:
            raise LeaseLostError(f"Unknown or mismatched lease token for job '{job_id}'.")
        if datetime.utcnow() > _parse_dt(lease["expires_at"]):
            raise LeaseLostError(f"Lease for job '{job_id}' has expired.")
        if not owned:
            # Caller passed no state container: persist nothing here; readers use this path.
            pass
        return lease

    # ------------------------------------------------------------------
    # Attempt tracking
    # ------------------------------------------------------------------

    def _seed_attempts(self, state: dict[str, Any], job_id: str, max_attempts: int) -> None:
        state["attempts"][job_id] = {"attempt": 1, "max_attempts": max_attempts}

    def _attempt_of(self, state: dict[str, Any], job_id: str) -> int:
        entry = state["attempts"].get(job_id)
        return entry["attempt"] if entry else 1

    def _max_attempts_of(self, state: dict[str, Any], job_id: str) -> int:
        entry = state["attempts"].get(job_id)
        return entry["max_attempts"] if entry else self.default_max_attempts

    def _bump_attempt(self, state: dict[str, Any], job_id: str) -> int:
        entry = state["attempts"].setdefault(
            job_id, {"attempt": 1, "max_attempts": self.default_max_attempts}
        )
        entry["attempt"] += 1
        return entry["attempt"]

    # ------------------------------------------------------------------
    # Idempotency-state sync
    # ------------------------------------------------------------------

    def _sync_idem(
        self,
        tenant_id: str,
        idempotency_key: str,
        *,
        status: JobStatus,
        lease_expires_at: datetime | None = None,
        cached_result: dict[str, Any] | None = None,
    ) -> None:
        """Mirror a job status transition into the idempotency reservation."""
        states = self.store.get_idempotency_states(tenant_id)
        if idempotency_key not in states:
            return
        current: IdempotencyState = states[idempotency_key]
        update: dict[str, Any] = {"status": status}
        if lease_expires_at is not None:
            update["lease_expires_at"] = lease_expires_at
        if cached_result is not None:
            update["cached_result"] = cached_result
        self.store.save_idempotency_state(tenant_id, current.model_copy(update=update))

    def _transition_job(
        self,
        tenant_id: str,
        job: Job,
        *,
        status: JobStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        clear_started_at: bool = False,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Job:
        """Apply a frozen ``model_copy`` transition and persist it. Returns the new Job."""
        update: dict[str, Any] = {"status": status}
        if started_at is not None:
            update["started_at"] = started_at
        elif clear_started_at:
            update["started_at"] = None
        if completed_at is not None:
            update["completed_at"] = completed_at
        if error_code is not None:
            update["error_code"] = error_code
        if error_message is not None:
            update["error_message"] = error_message
        updated = job.model_copy(update=update)
        self.store.save_job(tenant_id, updated)
        return updated

    # ==================================================================
    # Public API
    # ==================================================================

    def launch(
        self,
        tenant_id: str,
        idempotency_key: str,
        project_id: str,
        version_id: str,
        job_type: str,
        *,
        lease_seconds: int | None = None,
        max_attempts: int | None = None,
        run_id: str | None = None,
    ) -> tuple[Job, bool]:
        """Idempotently register a new job.

        A duplicate ``(tenant_id, idempotency_key)`` returns the existing job
        with ``is_new=False`` and launches no duplicate work. When ``run_id`` is
        supplied and a job with that id already exists for the tenant, raises
        :class:`DuplicateRunError`.
        """
        lease_seconds = self.default_lease_seconds if lease_seconds is None else lease_seconds
        max_attempts = self.default_max_attempts if max_attempts is None else max_attempts
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        with self._lock:
            if run_id is not None:
                try:
                    self.store.get_job(tenant_id, run_id)
                    raise DuplicateRunError(run_id)
                except ResourceNotFoundError:
                    pass

            job, is_new = acquire_or_get_job(
                self.store,
                tenant_id,
                idempotency_key,
                project_id,
                version_id,
                job_type,
                lease_duration_seconds=lease_seconds,
            )
            if is_new:
                state = self._load_state(tenant_id)
                self._seed_attempts(state, job.id, max_attempts)
                self._save_state(tenant_id, state)
            return job, is_new

    def acquire(
        self,
        tenant_id: str,
        job_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> str:
        """Claim a ``PENDING`` job for execution and return a fresh lease token.

        Raises :class:`LeaseLostError` if the job is not ``PENDING`` (already
        claimed or finished) or does not exist.
        """
        lease_seconds = self.default_lease_seconds if lease_seconds is None else lease_seconds
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        with self._lock:
            try:
                job = self.store.get_job(tenant_id, job_id)
            except ResourceNotFoundError as exc:
                raise LeaseLostError(f"Job '{job_id}' not found.") from exc
            if job.status != JobStatus.PENDING:
                raise LeaseLostError(
                    f"Job '{job_id}' is not claimable (status={job.status.value})."
                )

            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=lease_seconds)
            token = uuid.uuid4().hex

            self._transition_job(tenant_id, job, status=JobStatus.RUNNING, started_at=now)
            self._sync_idem(
                tenant_id,
                job.idempotency_key,
                status=JobStatus.RUNNING,
                lease_expires_at=expires_at,
            )

            state = self._load_state(tenant_id)
            self._put_lease(state, job_id, token, expires_at)
            self._save_state(tenant_id, state)
            logger.info("Acquired lease for job %s (tenant=%s).", job_id, tenant_id)
            return token

    def heartbeat(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        lease_seconds: int | None = None,
    ) -> Job:
        """Renew the execution lease and the idempotency reservation.

        Rejects stale or expired leases via :class:`LeaseLostError`.
        """
        lease_seconds = self.default_lease_seconds if lease_seconds is None else lease_seconds
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        with self._lock:
            try:
                job = self.store.get_job(tenant_id, job_id)
            except ResourceNotFoundError as exc:
                raise LeaseLostError(f"Job '{job_id}' not found.") from exc
            if job.status != JobStatus.RUNNING:
                raise LeaseLostError(
                    f"Cannot heartbeat job '{job_id}' (status={job.status.value})."
                )

            state = self._load_state(tenant_id)
            self._require_active_lease(tenant_id, job_id, lease_token, state=state)

            new_expiry = datetime.utcnow() + timedelta(seconds=lease_seconds)
            self._put_lease(state, job_id, lease_token, new_expiry)
            self._save_state(tenant_id, state)
            # Extend the idempotency reservation too, so a duplicate submission
            # cannot displace an actively-heartbeating job.
            self._sync_idem(
                tenant_id,
                job.idempotency_key,
                status=JobStatus.RUNNING,
                lease_expires_at=new_expiry,
            )
            return job

    def complete(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> Job:
        """Transition a RUNNING job to COMPLETED and revoke its lease."""
        with self._lock:
            state = self._load_state(tenant_id)
            self._require_active_lease(tenant_id, job_id, lease_token, state=state)
            job = self.store.get_job(tenant_id, job_id)
            completed = self._transition_job(
                tenant_id, job, status=JobStatus.COMPLETED, completed_at=datetime.utcnow()
            )
            cached = result if result is not None else {"status": "completed"}
            self._sync_idem(
                tenant_id,
                job.idempotency_key,
                status=JobStatus.COMPLETED,
                cached_result=cached,
            )
            self._revoke_lease(state, job_id)
            self._save_state(tenant_id, state)
            logger.info("Job %s completed (tenant=%s).", job_id, tenant_id)
            return completed

    def fail(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        error: BaseException | str,
        retryable: bool = False,
        error_code: str = "INTERNAL_SERVER_ERROR",
    ) -> Job:
        """Transition a RUNNING job to FAILED, or re-arm as PENDING when retryable.

        When ``retryable`` is true and the attempt budget remains, the job is
        returned to ``PENDING`` (attempt counter incremented) so another worker
        can claim it. Once the budget is exhausted the job is persisted as
        ``FAILED`` with a sanitized message.
        """
        sanitized = _sanitize_error(error, error_code)
        with self._lock:
            state = self._load_state(tenant_id)
            self._require_active_lease(tenant_id, job_id, lease_token, state=state)
            job = self.store.get_job(tenant_id, job_id)

            if retryable:
                attempt = self._attempt_of(state, job_id)
                max_attempts = self._max_attempts_of(state, job_id)
                if attempt < max_attempts:
                    next_attempt = self._bump_attempt(state, job_id)
                    rearmed = self._transition_job(
                        tenant_id,
                        job,
                        status=JobStatus.PENDING,
                        started_at=None,
                        clear_started_at=True,
                        error_code="RETRYING",
                        error_message=f"Attempt {attempt} failed; retrying as attempt {next_attempt}.",
                    )
                    self._sync_idem(tenant_id, job.idempotency_key, status=JobStatus.PENDING)
                    self._revoke_lease(state, job_id)
                    self._save_state(tenant_id, state)
                    logger.warning(
                        "Job %s failed (retryable); re-armed attempt %s/%s (tenant=%s).",
                        job_id,
                        next_attempt,
                        max_attempts,
                        tenant_id,
                    )
                    return rearmed

            failed = self._transition_job(
                tenant_id,
                job,
                status=JobStatus.FAILED,
                completed_at=datetime.utcnow(),
                error_code=sanitized.error_code,
                error_message=sanitized.message,
            )
            self._sync_idem(
                tenant_id,
                job.idempotency_key,
                status=JobStatus.FAILED,
                cached_result={
                    "status": "failed",
                    "error_code": sanitized.error_code,
                    "error_message": sanitized.message,
                },
            )
            self._revoke_lease(state, job_id)
            self._save_state(tenant_id, state)
            logger.error(
                "Job %s failed permanently [corr=%s] (tenant=%s).",
                job_id,
                sanitized.correlation_id,
                tenant_id,
            )
            return failed

    def cancel(
        self,
        tenant_id: str,
        job_id: str,
        *,
        reason: str = "Cancelled by request.",
        lease_token: str | None = None,
    ) -> Job:
        """Cooperatively cancel an active job.

        Terminal jobs raise :class:`JobNotCancelableError`. When ``lease_token``
        is provided it must be the live token (worker self-cancel); when
        omitted, the caller is an administrator / external request and the
        current lease is forcibly revoked.
        """
        with self._lock:
            job = self.store.get_job(tenant_id, job_id)
            if job.status in _TERMINAL_STATUSES:
                raise JobNotCancelableError(job_id, job.status)

            state = self._load_state(tenant_id)
            if lease_token is not None:
                self._require_active_lease(tenant_id, job_id, lease_token, state=state)

            cancelled = self._transition_job(
                tenant_id,
                job,
                status=JobStatus.CANCELLED,
                completed_at=datetime.utcnow(),
                error_code="CANCELLED",
                error_message=reason,
            )
            self._sync_idem(
                tenant_id,
                job.idempotency_key,
                status=JobStatus.CANCELLED,
                cached_result={"status": "cancelled", "error_message": reason},
            )
            self._revoke_lease(state, job_id)
            self._save_state(tenant_id, state)
            logger.info("Job %s cancelled (tenant=%s): %s", job_id, tenant_id, reason)
            return cancelled

    def is_cancelled(self, tenant_id: str, job_id: str) -> bool:
        """Cooperative cancellation probe for workers polling between steps."""
        try:
            return self.store.get_job(tenant_id, job_id).status == JobStatus.CANCELLED
        except ResourceNotFoundError:
            return False

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    def save_checkpoint(
        self,
        tenant_id: str,
        job_id: str,
        step: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """Atomically persist a progress checkpoint for the job."""
        with self._lock:
            state = self._load_state(tenant_id)
            checkpoint = Checkpoint(
                step=step,
                payload=payload or {},
                attempt=self._attempt_of(state, job_id),
                updated_at=datetime.utcnow(),
            )
            _atomic_write_json(
                self._ckpt_path(tenant_id, job_id), checkpoint.model_dump(mode="json")
            )
            return checkpoint

    def load_checkpoint(self, tenant_id: str, job_id: str) -> Checkpoint | None:
        """Load the latest checkpoint, or ``None`` if none exists."""
        data = _read_json(self._ckpt_path(tenant_id, job_id))
        if data is None:
            return None
        return Checkpoint.model_validate(data)

    # ------------------------------------------------------------------
    # Cleanup & recovery
    # ------------------------------------------------------------------

    def cleanup(self, tenant_id: str, job_id: str) -> None:
        """Remove the ephemeral workspace, checkpoint and lease/attempts entries.

        Idempotent: safe to call repeatedly or after a crash.
        """
        with self._lock:
            self.store.cleanup_workspace(tenant_id, job_id)
            state = self._load_state(tenant_id)
            removed = job_id in state["leases"] or job_id in state["attempts"]
            self._revoke_lease(state, job_id)
            state["attempts"].pop(job_id, None)
            if removed:
                self._save_state(tenant_id, state)
            ckpt = self._ckpt_path(tenant_id, job_id)
            if ckpt.exists():
                try:
                    ckpt.unlink()
                except OSError as exc:
                    logger.warning("Could not remove checkpoint %s: %s", ckpt, exc)

    def sweep_expired(self, tenant_id: str, *, now: datetime | None = None) -> int:
        """Revoke leases past their expiry and FAIL their RUNNING jobs.

        Returns the number of swept leases. A swept job is marked
        ``LEASE_EXPIRED`` (terminal); a fresh run requires a new idempotency key.
        """
        now = now or datetime.utcnow()
        with self._lock:
            state = self._load_state(tenant_id)
            swept = 0
            for job_id, lease in list(state["leases"].items()):
                if _parse_dt(lease["expires_at"]) >= now:
                    continue
                self._revoke_lease(state, job_id)
                try:
                    job = self.store.get_job(tenant_id, job_id)
                except ResourceNotFoundError:
                    pass
                else:
                    if job.status == JobStatus.RUNNING:
                        self._transition_job(
                            tenant_id,
                            job,
                            status=JobStatus.FAILED,
                            completed_at=now,
                            error_code="LEASE_EXPIRED",
                            error_message="Execution lease expired before completion.",
                        )
                        self._sync_idem(tenant_id, job.idempotency_key, status=JobStatus.FAILED)
                swept += 1
            if swept:
                self._save_state(tenant_id, state)
            return swept

    # ------------------------------------------------------------------
    # Sanitized execution boundary
    # ------------------------------------------------------------------

    def run_sanitized(
        self, func: Any, *args: Any, **kwargs: Any
    ) -> tuple[Any, SanitizedErrorInfo | None]:
        """Execute ``func``; mask any unexpected exception behind a correlation_id.

        Controlled :class:`SanitizedServiceError` subclasses propagate
        unchanged. Any other exception is logged with a full traceback and
        returned as :class:`SanitizedErrorInfo`.
        """
        try:
            return func(*args, **kwargs), None
        except SanitizedServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 (boundary: we mask on purpose)
            info = _sanitize_error(exc, "INTERNAL_SERVER_ERROR")
            logger.error(
                "[corr=%s] sanitized execution failure: %s",
                info.correlation_id,
                exc,
                exc_info=True,
            )
            return None, info


# =====================================================================
# Error sanitization helper
# =====================================================================


def _sanitize_error(error: BaseException | str, error_code: str) -> SanitizedErrorInfo:
    """Produce a :class:`SanitizedErrorInfo` from an arbitrary error.

    ``SanitizedServiceError`` instances keep their own code/message; everything
    else is masked behind a fresh correlation_id and a generic message.
    """
    if isinstance(error, SanitizedServiceError):
        return SanitizedErrorInfo(
            error_code=error.error_code,
            message=error.message,
            correlation_id=error.correlation_id,
        )
    correlation_id = uuid.uuid4().hex
    return SanitizedErrorInfo(
        error_code=error_code,
        message=(
            f"An unexpected error occurred while processing the job. "
            f"(Correlation ID: {correlation_id})"
        ),
        correlation_id=correlation_id,
    )
