"""Durable worker built exclusively on PostgreSQL leases and checkpoints.

The worker pairs a tenant-scoped claim with a heartbeat thread and fenced
completion transitions. Checkpoints survive lease loss and reclaim; a handler
that opts into resume receives the last persisted checkpoint, but actual reuse
of staged work remains handler-specific.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from apps.dataviz_service.contracts import Job, SanitizedServiceError

logger = logging.getLogger("dataviz_service.job_worker")

CheckpointWriter = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class VersionJobCompletion:
    """Prepared version mutation that must be committed through the lease fence.

    The handler may stage immutable object artifacts before returning this
    value. The worker is the only component that can ask the durable store to
    make the version materialization authoritative, and the store performs that
    mutation together with the terminal COMPLETED transition.
    """

    result: Mapping[str, Any]
    materialization: Mapping[str, Any]


JobHandlerResult = Mapping[str, Any] | VersionJobCompletion
JobHandler = Callable[[Job, Mapping[str, Any], CheckpointWriter], JobHandlerResult]
ResumableJobHandler = Callable[
    [Job, Mapping[str, Any], CheckpointWriter, Mapping[str, Any] | None], JobHandlerResult
]


class DurableJobStore(Protocol):
    def recover_expired_jobs(self, tenant_id: str) -> tuple[int, int]: ...

    def claim_job(self, tenant_id: str, *, lease_seconds: int) -> tuple[Job, str] | None: ...

    def get_job_input(self, tenant_id: str, job_id: str) -> dict[str, Any]: ...

    def get_job_checkpoint(self, tenant_id: str, job_id: str) -> Mapping[str, Any] | None: ...

    def heartbeat_job(
        self, tenant_id: str, job_id: str, lease_token: str, *, lease_seconds: int
    ) -> bool: ...

    def checkpoint_job(
        self, tenant_id: str, job_id: str, lease_token: str, checkpoint: Mapping[str, Any]
    ) -> bool: ...

    def complete_job(
        self, tenant_id: str, job_id: str, lease_token: str, result: Mapping[str, Any]
    ) -> bool: ...

    def complete_version_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        project_id: str,
        version_id: str,
        result: Mapping[str, Any],
        materialization: Mapping[str, Any],
    ) -> bool: ...

    def fail_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> bool: ...


class LeaseLostError(RuntimeError):
    """The worker no longer owns the job lease."""


def _accepts_resume(handler: Callable[..., Any]) -> bool:
    """Return True if ``handler`` declares a fourth ``resume`` parameter."""
    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        return False
    return "resume" in params or len(params) >= 4


class ProductionWorker:
    """Execute one tenant-scoped durable job with heartbeat and fenced commit."""

    def __init__(
        self,
        store: DurableJobStore,
        handler: JobHandler,
        *,
        lease_seconds: int = 300,
        heartbeat_seconds: int = 30,
    ) -> None:
        if not 0 < heartbeat_seconds < lease_seconds:
            raise ValueError("heartbeat_seconds must be within the lease interval")
        self.store = store
        self.handler = handler
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._resume_capable = _accepts_resume(handler)

    def run_once(self, tenant_id: str) -> Job | None:
        """Recover expired work, claim at most one job, and execute it."""
        self.store.recover_expired_jobs(tenant_id)
        claimed = self.store.claim_job(tenant_id, lease_seconds=self.lease_seconds)
        if claimed is None:
            return None
        job, token = claimed
        stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(job, token, stop, lease_lost),
            name=f"dataviz-heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            input_payload = self.store.get_job_input(tenant_id, job.id)
            resume_checkpoint = self.store.get_job_checkpoint(tenant_id, job.id)

            def checkpoint(payload: Mapping[str, Any]) -> None:
                if lease_lost.is_set() or not self.store.checkpoint_job(
                    tenant_id, job.id, token, payload
                ):
                    raise LeaseLostError("Job lease was lost while checkpointing")

            if self._resume_capable:
                completion = self.handler(job, input_payload, checkpoint, resume_checkpoint)
            else:
                completion = self.handler(job, input_payload, checkpoint)

            if lease_lost.is_set():
                raise LeaseLostError("Job lease was lost before completion")
            if isinstance(completion, VersionJobCompletion):
                completed = self.store.complete_version_job(
                    tenant_id,
                    job.id,
                    token,
                    project_id=job.project_id,
                    version_id=job.version_id,
                    result=completion.result,
                    materialization=completion.materialization,
                )
            else:
                completed = self.store.complete_job(tenant_id, job.id, token, completion)
            if not completed:
                raise LeaseLostError("Job lease was lost before completion")
        except LeaseLostError:
            logger.warning("job_lease_lost tenant=%s job=%s", tenant_id, job.id)
            raise
        except SanitizedServiceError as exc:
            self.store.fail_job(
                tenant_id,
                job.id,
                token,
                error_code=exc.error_code,
                error_message=exc.message,
                retryable=False,
            )
        except Exception:
            logger.exception("job_failed tenant=%s job=%s", tenant_id, job.id)
            self.store.fail_job(
                tenant_id,
                job.id,
                token,
                error_code="INTERNAL_JOB_ERROR",
                error_message="The job failed unexpectedly.",
                retryable=True,
            )
        finally:
            stop.set()
            heartbeat.join(timeout=self.heartbeat_seconds + 1)
        return job

    def _heartbeat(
        self,
        job: Job,
        token: str,
        stop: threading.Event,
        lease_lost: threading.Event,
    ) -> None:
        while not stop.wait(self.heartbeat_seconds):
            if not self.store.heartbeat_job(
                job.tenant_id, job.id, token, lease_seconds=self.lease_seconds
            ):
                lease_lost.set()
                return


def run_worker_loop(
    store: DurableJobStore,
    handler: JobHandler,
    tenants: Sequence[str],
    *,
    idle_seconds: float = 5.0,
    lease_seconds: int = 300,
    heartbeat_seconds: int = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Loop fairly over explicitly configured tenants until interrupted."""
    rotation_order = tuple(tenants)
    if not rotation_order:
        raise ValueError("At least one tenant is required")
    if not 0 < idle_seconds <= 60:
        raise ValueError("idle_seconds must be in (0, 60]")
    worker = ProductionWorker(
        store, handler, lease_seconds=lease_seconds, heartbeat_seconds=heartbeat_seconds
    )
    rotation = 0
    try:
        while True:
            progressed = False
            for offset in range(len(rotation_order)):
                tenant_id = rotation_order[(rotation + offset) % len(rotation_order)]
                try:
                    if worker.run_once(tenant_id) is not None:
                        progressed = True
                except LeaseLostError:
                    logger.warning("job_lease_lost_in_loop tenant=%s", tenant_id)
            rotation = (rotation + 1) % len(rotation_order)
            if not progressed:
                sleep(idle_seconds)
    except KeyboardInterrupt:
        logger.info("worker_loop_stopped signal=keyboard_interrupt")
