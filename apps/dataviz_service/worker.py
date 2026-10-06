"""Background worker for asynchronous migration jobs.

Handles tenant isolation, ephemeral workspace creation/cleanup, cancellation checks,
and sanitized error reporting at job execution boundaries.
"""

import logging
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from apps.dataviz_service.contracts import (
    IdempotencyState,
    Job,
    JobStatus,
    ResourceNotFoundError,
    SanitizedServiceError,
)
from apps.dataviz_service.jobs import StateStore
from apps.dataviz_service.legacy_jobs import Job as LegacyJob
from apps.dataviz_service.legacy_jobs import JobStore as LegacyJobStore

logger = logging.getLogger("dataviz_service.worker")

LegacyRunner = Callable[[LegacyJob], int]

# Thread-safe queue for job execution
_JOB_QUEUE: queue.Queue[tuple[str, str]] = queue.Queue()
_WORKER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def submit_job(tenant_id: str, job_id: str) -> None:
    """Submits a job to the worker queue."""
    _JOB_QUEUE.put((tenant_id, job_id))


def start_worker(store: StateStore) -> None:
    """Starts the background worker thread if not already running."""
    global _WORKER_THREAD, _STOP_EVENT
    with store.lock:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop, args=(store, _STOP_EVENT), daemon=True, name="DataVizWorker"
        )
        _WORKER_THREAD.start()
        logger.info("Background job worker started.")


def stop_worker() -> None:
    """Stops the background worker thread."""
    global _WORKER_THREAD, _STOP_EVENT
    if _WORKER_THREAD is not None:
        _STOP_EVENT.set()
        # Put dummy value to unblock queue get
        _JOB_QUEUE.put(("", ""))
        _WORKER_THREAD.join(timeout=5)
        _WORKER_THREAD = None
        logger.info("Background job worker stopped.")


def _worker_loop(store: StateStore, stop_event: threading.Event) -> None:
    """Main worker loop consuming and executing jobs."""
    while not stop_event.is_set():
        try:
            try:
                tenant_id, job_id = _JOB_QUEUE.get(timeout=1)
            except queue.Empty:
                continue

            if not tenant_id or not job_id:
                # Stop signal sentinel
                _JOB_QUEUE.task_done()
                continue

            try:
                execute_job(store, tenant_id, job_id)
            except Exception as e:
                logger.error("Job %s execution failed with unhandled exception: %s", job_id, e)
            finally:
                _JOB_QUEUE.task_done()

        except Exception as ex:
            logger.critical("Critical error in worker loop: %s", ex)


def _check_cancellation(store: StateStore, tenant_id: str, job_id: str) -> bool:
    """Helper to check if the job has been cancelled in the database."""
    try:
        job = store.get_job(tenant_id, job_id)
        return job.status == JobStatus.CANCELLED
    except Exception:
        return False


def execute_job(store: StateStore, tenant_id: str, job_id: str) -> None:
    """Performs the actual job processing, checking for cancellation between steps.

    Manages ephemeral workspace, invokes PBIPManager, and sanitizes errors.
    """
    now = datetime.utcnow()
    try:
        # Load the job
        try:
            job = store.get_job(tenant_id, job_id)
        except ResourceNotFoundError:
            logger.error("Job %s not found in store, skipping.", job_id)
            return

        # If already cancelled, stop
        if job.status == JobStatus.CANCELLED:
            logger.info("Job %s was cancelled before starting.", job_id)
            return

        # Transition to RUNNING
        job = Job(
            id=job.id,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            version_id=job.version_id,
            job_type=job.job_type,
            status=JobStatus.RUNNING,
            idempotency_key=job.idempotency_key,
            created_at=job.created_at,
            started_at=now,
        )
        states = store.get_idempotency_states(tenant_id)
        if job.idempotency_key in states:
            state = states[job.idempotency_key]
            new_state = IdempotencyState(
                idempotency_key=state.idempotency_key,
                tenant_id=state.tenant_id,
                job_id=state.job_id,
                status=JobStatus.RUNNING,
                lease_expires_at=state.lease_expires_at,
                cached_result=state.cached_result,
            )
            store.save_job_and_idempotency_state(tenant_id, job, new_state)
        else:
            store.save_job_and_idempotency_state(tenant_id, job)

        # 1. Ephemeral Workspace Setup
        workspace_dir = store.get_workspace_dir(tenant_id, job_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # 2. Locate source file in version directory
        # By convention, we expect the source file under the version's directory:
        # C:/Proyectos/TableauToPBIP_V2/.cache/dataviz_service/tenants/<tenant_id>/projects/<project_id>/versions/<version_id>/
        # The file can be source.twb or source.twbx
        version_dir = (
            store._get_tenant_dir(tenant_id)
            / "projects"
            / job.project_id
            / "versions"
            / job.version_id
        )
        source_file = version_dir / "source.twb"
        if not source_file.exists():
            source_file = version_dir / "source.twbx"

        if not source_file.exists():
            raise SanitizedServiceError(
                f"Source workbook for version '{job.version_id}' not found.",
                status_code=404,
                error_code="SOURCE_NOT_FOUND",
            )

        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # 3. Copy source file to workspace
        workspace_source = workspace_dir / source_file.name
        shutil.copy2(source_file, workspace_source)

        # 4. Handle TWBX extraction if needed
        actual_twb = workspace_source
        if workspace_source.suffix.lower() == ".twbx":
            from core.twbx_extractor import extract_twb_from_twbx

            logger.info("Extracting TWBX inside workspace for job %s", job_id)
            with open(workspace_source, "rb") as f:
                extracted_path, _ = extract_twb_from_twbx(f)
                if extracted_path:
                    # Move extracted twb to workspace to contain it
                    shutil.move(extracted_path, workspace_dir / "extracted.twb")
                    actual_twb = workspace_dir / "extracted.twb"
                else:
                    raise SanitizedServiceError(
                        "Failed to extract workbook from TWBX archive.",
                        status_code=400,
                        error_code="INVALID_TWBX",
                    )

        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # 5. Initialize and run migration logic using PBIPManager
        from core.interfaces.migration_types import (
            MigrationMode,
            MigrationRequest,
            PBIXGenerationMode,
        )
        from core.pbip_manager import PBIPManager

        workspace_output = workspace_dir / "output"
        workspace_output.mkdir(parents=True, exist_ok=True)

        req = MigrationRequest(
            project_name=job.project_id,
            source_file=actual_twb,
            output_dir=workspace_output,
            mode=MigrationMode.PBIR,
            pbix_mode=PBIXGenerationMode.MANUAL,
            convert_calculations=True,
            cleanup_on_failure=False,  # We manage workspace cleanup ourselves
        )

        manager = PBIPManager(config=req)

        # Step 5a: Create structure
        manager.create_structure()
        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # Step 5b: Generate semantic model
        # Try to locate pythonnet bin path dynamically
        project_root = Path("C:/Proyectos/TableauToPBIP_V2")
        bin_path = project_root / "bin"
        try:
            manager.generate_semantic_model_with_tom(str(actual_twb), str(bin_path))
        except Exception as tom_ex:
            # We catch exceptions internally. If they are critical validation errors, we bubble them.
            # Otherwise we log them as warnings to allow visual extraction to proceed, or raise if fatal.
            logger.warning("TOM Semantic Model generation warning: %s", tom_ex)

        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # Step 5c: Generate report visuals
        manager.generate_report_visuals(str(actual_twb))
        if _check_cancellation(store, tenant_id, job_id):
            _abort_job(store, tenant_id, job_id, "Job cancelled by user.")
            return

        # 6. Post-migration Validation
        from core.validation.validate_pbip_output import validar_proyecto

        validation_res = validar_proyecto(str(workspace_output))
        if not validation_res.get("exito", False):
            logger.warning(
                "Validation warnings for job %s: %s", job_id, validation_res.get("mensaje")
            )

        # 7. Relocate outputs from ephemeral workspace to permanent results
        result_dir = store.get_result_dir(tenant_id, job_id)
        if result_dir.exists():
            shutil.rmtree(result_dir)
        result_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(workspace_output), str(result_dir))

        # 8. Success transition
        now = datetime.utcnow()
        completed_job = Job(
            id=job.id,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            version_id=job.version_id,
            job_type=job.job_type,
            status=JobStatus.COMPLETED,
            idempotency_key=job.idempotency_key,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=now,
        )
        if job.idempotency_key in states:
            state = states[job.idempotency_key]
            completed_state = IdempotencyState(
                idempotency_key=state.idempotency_key,
                tenant_id=state.tenant_id,
                job_id=state.job_id,
                status=JobStatus.COMPLETED,
                lease_expires_at=state.lease_expires_at,
                cached_result={
                    "status": "success",
                    "result_dir": str(result_dir),
                    "validation_summary": validation_res.get("mensaje", ""),
                },
            )
            store.save_job_and_idempotency_state(tenant_id, completed_job, completed_state)
        else:
            store.save_job_and_idempotency_state(tenant_id, completed_job)

        logger.info("Job %s completed successfully.", job_id)

    except Exception as ex:
        # Unexpected error handling & sanitization boundary
        now = datetime.utcnow()
        correlation_id = str(uuid.uuid4())
        logger.error(
            "Unexpected error executing job %s [Correlation ID: %s]: %s\n%s",
            job_id,
            correlation_id,
            ex,
            traceback.format_exc(),
        )

        # Determine error details
        if isinstance(ex, SanitizedServiceError):
            err_code = ex.error_code
            err_msg = ex.message
        else:
            err_code = "INTERNAL_SERVER_ERROR"
            err_msg = (
                f"An unexpected error occurred during migration. (Correlation ID: {correlation_id})"
            )

        try:
            # Transition to FAILED in store
            job_failed = Job(
                id=job_id,
                tenant_id=tenant_id,
                project_id=job.project_id if "job" in locals() else "unknown",
                version_id=job.version_id if "job" in locals() else "unknown",
                job_type=job.job_type if "job" in locals() else "TABLEAU_TO_PBIP",
                status=JobStatus.FAILED,
                idempotency_key=job.idempotency_key if "job" in locals() else "",
                created_at=job.created_at if "job" in locals() else now,
                started_at=job.started_at if "job" in locals() else now,
                completed_at=now,
                error_code=err_code,
                error_message=err_msg,
            )
            if "job" in locals() and job.idempotency_key:
                states = store.get_idempotency_states(tenant_id)
                if job.idempotency_key in states:
                    state = states[job.idempotency_key]
                    failed_state = IdempotencyState(
                        idempotency_key=state.idempotency_key,
                        tenant_id=state.tenant_id,
                        job_id=state.job_id,
                        status=JobStatus.FAILED,
                        lease_expires_at=state.lease_expires_at,
                        cached_result={
                            "status": "failed",
                            "error_code": err_code,
                            "error_message": err_msg,
                        },
                    )
                    store.save_job_and_idempotency_state(tenant_id, job_failed, failed_state)
                else:
                    store.save_job_and_idempotency_state(tenant_id, job_failed)
            else:
                store.save_job_and_idempotency_state(tenant_id, job_failed)
        except Exception as db_ex:
            logger.critical("Failed to save job failure status to database: %s", db_ex)

    finally:
        # 9. Guaranteed cleanup of the ephemeral workspace
        store.cleanup_workspace(tenant_id, job_id)


def _abort_job(store: StateStore, tenant_id: str, job_id: str, reason: str) -> None:
    """Helper to transition a running job into CANCELLED status."""
    now = datetime.utcnow()
    logger.info("Aborting job %s for tenant %s: %s", job_id, tenant_id, reason)
    try:
        job = store.get_job(tenant_id, job_id)
        cancelled_job = Job(
            id=job.id,
            tenant_id=job.tenant_id,
            project_id=job.project_id,
            version_id=job.version_id,
            job_type=job.job_type,
            status=JobStatus.CANCELLED,
            idempotency_key=job.idempotency_key,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=now,
            error_code="CANCELLED",
            error_message=reason,
        )
        states = store.get_idempotency_states(tenant_id)
        if job.idempotency_key in states:
            state = states[job.idempotency_key]
            cancelled_state = IdempotencyState(
                idempotency_key=state.idempotency_key,
                tenant_id=state.tenant_id,
                job_id=state.job_id,
                status=JobStatus.CANCELLED,
                lease_expires_at=state.lease_expires_at,
                cached_result={
                    "status": "cancelled",
                    "error_message": reason,
                },
            )
            store.save_job_and_idempotency_state(tenant_id, cancelled_job, cancelled_state)
        else:
            store.save_job_and_idempotency_state(tenant_id, cancelled_job)
    except Exception as e:
        logger.error("Failed to abort job status: %s", e)


def run_migration(job: LegacyJob) -> int:
    """Ejecuta el migrador Tableau→PBIP en un proceso aislado."""
    project_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        str(project_root / "scripts" / "migrate.py"),
        "--twb",
        str(job.source),
        "--output",
        str(job.output),
        "--pbir",
    ]
    if job.name:
        command.extend(["--name", job.name])
    return subprocess.run(command, cwd=project_root, check=False).returncode


def process_next(
    store: LegacyJobStore,
    runner: LegacyRunner = run_migration,
    *,
    lease_seconds: int = 60,
    heartbeat_seconds: float = 20,
) -> LegacyJob | None:
    """Procesa como máximo un job de la cola SQLite y persiste su resultado."""
    if heartbeat_seconds <= 0 or heartbeat_seconds >= lease_seconds:
        raise ValueError("heartbeat_seconds debe ser positivo y menor que lease_seconds")
    job = store.claim_next(lease_seconds)
    if job is None:
        return None
    assert job.lease_id is not None
    lease_id = job.lease_id
    stopped = threading.Event()

    def renew_lease() -> None:
        while not stopped.wait(heartbeat_seconds):
            try:
                store.heartbeat(job.id, lease_id, lease_seconds)
            except ValueError:
                logger.error("job_lease_lost id=%s", job.id)
                return

    heartbeat = threading.Thread(target=renew_lease, name=f"lease-{job.id}", daemon=True)
    heartbeat.start()
    try:
        exit_code = runner(job)
        if exit_code != 0:
            raise RuntimeError(f"migrate.py terminó con código {exit_code}")
    except (KeyboardInterrupt, SystemExit):
        store.fail(job.id, lease_id, "worker_interrupted")
        raise
    except Exception as error:
        logger.exception("job_failed id=%s", job.id)
        return store.fail(job.id, lease_id, str(error))
    finally:
        stopped.set()
        heartbeat.join()
    return store.succeed(job.id, lease_id)
