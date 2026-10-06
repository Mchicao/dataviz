"""State store and SaasJob idempotency manager for the DataVIZ SaaS service.

Enforces tenant-level directory isolation on disk. All files and workspaces
reside under tenant-specific paths to prevent cross-tenant data leakage.
"""

import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apps.dataviz_service.contracts import (
    IdempotencyState,
    Project,
    ResourceNotFoundError,
    Tenant,
    Version,
)
from apps.dataviz_service.contracts import (
    Job as SaasJob,
)
from apps.dataviz_service.contracts import (
    JobStatus as SaasJobStatus,
)
from apps.dataviz_service.legacy_jobs import (
    Job as LegacyJob,
)
from apps.dataviz_service.legacy_jobs import (
    JobStatus as LegacyJobStatus,
)
from apps.dataviz_service.legacy_jobs import (
    JobStore as LegacyJobStore,
)

logger = logging.getLogger("dataviz_service.jobs")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


class StateStore:
    """Thread-safe disk-backed store for multi-tenant DataVIZ metadata and workspaces.

    Enforces strict isolation by storing all tenant resources within a dedicated
    folder structure: .cache/dataviz_service/tenants/<tenant_id>/
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            self.base_dir = Path("C:/Proyectos/TableauToPBIP_V2/.cache/dataviz_service")
        else:
            self.base_dir = Path(base_dir)
        self.lock = threading.RLock()

    def _get_tenant_dir(self, tenant_id: str) -> Path:
        """Constructs and returns the isolated directory path for a tenant."""
        # Sanitize tenant_id to prevent directory traversal attacks
        clean_id = _safe_component(tenant_id, "tenant ID")
        path = self.base_dir / "tenants" / clean_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_list(self, filepath: Path) -> list[dict[str, Any]]:
        if not filepath.exists():
            return []
        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load list from %s: %s", filepath, e)
            return []

    def _save_list(self, filepath: Path, data: list[dict[str, Any]]) -> None:
        self._save_json_atomic(filepath, data)

    def _load_dict(self, filepath: Path) -> dict[str, dict[str, Any]]:
        if not filepath.exists():
            return {}
        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load dict from %s: %s", filepath, e)
            return {}

    def _save_dict(self, filepath: Path, data: dict[str, dict[str, Any]]) -> None:
        self._save_json_atomic(filepath, data)

    @staticmethod
    def _save_json_atomic(filepath: Path, data: Any) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        temporary = filepath.with_name(f".{filepath.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, filepath)
        finally:
            temporary.unlink(missing_ok=True)

    def _recover_job_acquisition(self, tenant_id: str) -> None:
        transaction_path = self._get_tenant_dir(tenant_id) / "job-acquisition.json"
        if not transaction_path.exists():
            return
        with open(transaction_path, encoding="utf-8") as f:
            transaction = json.load(f)
        tenant_dir = self._get_tenant_dir(tenant_id)
        self._save_list(tenant_dir / "jobs.json", transaction["jobs"])
        self._save_dict(tenant_dir / "idempotency.json", transaction["states"])
        transaction_path.unlink(missing_ok=True)

    # --- Tenants API ---

    def get_tenants(self) -> list[Tenant]:
        """Gets all tenants in the system."""
        with self.lock:
            filepath = self.base_dir / "tenants.json"
            data = self._load_list(filepath)
            return [Tenant.model_validate(t) for t in data]

    def get_tenant(self, tenant_id: str) -> Tenant:
        """Retrieves a specific tenant by ID, raising ResourceNotFoundError if not found."""
        tenants = self.get_tenants()
        for t in tenants:
            if t.id == tenant_id:
                return t
        raise ResourceNotFoundError("Tenant", tenant_id)

    def save_tenant(self, tenant: Tenant) -> None:
        """Saves or updates a tenant's information."""
        with self.lock:
            filepath = self.base_dir / "tenants.json"
            tenants = self.get_tenants()
            tenants = [t for t in tenants if t.id != tenant.id]
            tenants.append(tenant)
            self._save_list(filepath, [t.model_dump(mode="json") for t in tenants])

    # --- Projects API ---

    def get_projects(self, tenant_id: str) -> list[Project]:
        """Gets all projects for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "projects.json"
            data = self._load_list(filepath)
            return [Project.model_validate(p) for p in data]

    def get_project(self, tenant_id: str, project_id: str) -> Project:
        """Retrieves a specific project, raising ResourceNotFoundError if not found."""
        projects = self.get_projects(tenant_id)
        for p in projects:
            if p.id == project_id:
                return p
        raise ResourceNotFoundError("Project", project_id)

    def save_project(self, tenant_id: str, project: Project) -> None:
        """Saves or updates a project for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "projects.json"
            projects = self.get_projects(tenant_id)
            projects = [p for p in projects if p.id != project.id]
            projects.append(project)
            self._save_list(filepath, [p.model_dump(mode="json") for p in projects])

    # --- Versions API ---

    def get_versions(self, tenant_id: str) -> list[Version]:
        """Gets all versions for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "versions.json"
            data = self._load_list(filepath)
            return [Version.model_validate(v) for v in data]

    def get_version(self, tenant_id: str, version_id: str) -> Version:
        """Retrieves a specific version, raising ResourceNotFoundError if not found."""
        versions = self.get_versions(tenant_id)
        for v in versions:
            if v.id == version_id:
                return v
        raise ResourceNotFoundError("Version", version_id)

    def save_version(self, tenant_id: str, version: Version) -> None:
        """Saves or updates a version for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "versions.json"
            versions = self.get_versions(tenant_id)
            versions = [v for v in versions if v.id != version.id]
            versions.append(version)
            self._save_list(filepath, [v.model_dump(mode="json") for v in versions])

    # --- Jobs API ---

    def get_jobs(self, tenant_id: str) -> list[SaasJob]:
        """Gets all jobs for a specific tenant."""
        with self.lock:
            self._recover_job_acquisition(tenant_id)
            filepath = self._get_tenant_dir(tenant_id) / "jobs.json"
            data = self._load_list(filepath)
            return [SaasJob.model_validate(j) for j in data]

    def get_job(self, tenant_id: str, job_id: str) -> SaasJob:
        """Retrieves a specific SaasJob, raising ResourceNotFoundError if not found."""
        jobs = self.get_jobs(tenant_id)
        for j in jobs:
            if j.id == job_id:
                return j
        raise ResourceNotFoundError("SaasJob", job_id)

    def save_job(self, tenant_id: str, job: SaasJob) -> None:
        """Saves or updates a SaasJob for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "jobs.json"
            jobs = self.get_jobs(tenant_id)
            jobs = [j for j in jobs if j.id != job.id]
            jobs.append(job)
            self._save_list(filepath, [j.model_dump(mode="json") for j in jobs])

    # --- Idempotency API ---

    def get_idempotency_states(self, tenant_id: str) -> dict[str, IdempotencyState]:
        """Gets all idempotency states for a specific tenant."""
        with self.lock:
            self._recover_job_acquisition(tenant_id)
            filepath = self._get_tenant_dir(tenant_id) / "idempotency.json"
            data = self._load_dict(filepath)
            return {k: IdempotencyState.model_validate(v) for k, v in data.items()}

    def save_idempotency_state(self, tenant_id: str, state: IdempotencyState) -> None:
        """Saves or updates an idempotency state for a specific tenant."""
        with self.lock:
            filepath = self._get_tenant_dir(tenant_id) / "idempotency.json"
            states = self._load_dict(filepath)
            states[state.idempotency_key] = state.model_dump(mode="json")
            self._save_dict(filepath, states)

    def save_job_and_idempotency_state(
        self, tenant_id: str, job: SaasJob, state: IdempotencyState | None = None
    ) -> None:
        """Publish a job transition and optional idempotency state together."""
        with self.lock:
            tenant_dir = self._get_tenant_dir(tenant_id)
            jobs_path = tenant_dir / "jobs.json"
            states_path = tenant_dir / "idempotency.json"
            jobs = self._load_list(jobs_path)
            jobs = [item for item in jobs if item.get("id") != job.id]
            jobs.append(job.model_dump(mode="json"))
            states = self._load_dict(states_path)
            if state is not None:
                states[state.idempotency_key] = state.model_dump(mode="json")
            transaction_path = tenant_dir / "job-acquisition.json"
            self._save_json_atomic(transaction_path, {"jobs": jobs, "states": states})
            self._save_list(jobs_path, jobs)
            self._save_dict(states_path, states)
            transaction_path.unlink(missing_ok=True)

    # --- Workspace & Cleanup API ---

    def get_workspace_dir(self, tenant_id: str, job_id: str) -> Path:
        """Returns the ephemeral workspace directory for a SaasJob."""
        return self._get_tenant_dir(tenant_id) / "workspaces" / _safe_component(job_id, "job ID")

    def get_result_dir(self, tenant_id: str, job_id: str) -> Path:
        """Returns the permanent migration results directory for a SaasJob."""
        return self._get_tenant_dir(tenant_id) / "results" / _safe_component(job_id, "job ID")

    def cleanup_workspace(self, tenant_id: str, job_id: str) -> None:
        """Deletes the ephemeral workspace directory for a SaasJob."""
        workspace = self.get_workspace_dir(tenant_id, job_id)
        if workspace.exists():
            try:
                shutil.rmtree(workspace)
                logger.info("Cleaned up workspace for SaasJob %s of tenant %s", job_id, tenant_id)
            except Exception as e:
                logger.error("Failed to cleanup workspace %s: %s", workspace, e)


def acquire_or_get_job(
    store: StateStore,
    tenant_id: str,
    idempotency_key: str,
    project_id: str,
    version_id: str,
    job_type: str,
    lease_duration_seconds: int = 300,
) -> tuple[SaasJob, bool]:
    """Acquires a lease for an idempotency key or retrieves the existing SaasJob state.

    Locks the resource, checks for collisions, handles expired leases,
    and returns (SaasJob, is_new).
    """
    with store.lock:
        states = store.get_idempotency_states(tenant_id)
        now = datetime.utcnow()

        if idempotency_key in states:
            state = states[idempotency_key]
            is_active = state.status in (SaasJobStatus.PENDING, SaasJobStatus.RUNNING)
            is_expired = is_active and state.lease_expires_at < now

            if is_expired:
                # Expired lease: transition old SaasJob to FAILED
                logger.warning(
                    "SaasJob lease %s expired for tenant %s. Initiating new execution.",
                    state.job_id,
                    tenant_id,
                )
                try:
                    old_job = store.get_job(tenant_id, state.job_id)
                    failed_job = SaasJob(
                        id=old_job.id,
                        tenant_id=old_job.tenant_id,
                        project_id=old_job.project_id,
                        version_id=old_job.version_id,
                        job_type=old_job.job_type,
                        status=SaasJobStatus.FAILED,
                        idempotency_key=old_job.idempotency_key,
                        created_at=old_job.created_at,
                        started_at=old_job.started_at,
                        completed_at=now,
                        error_code="LEASE_EXPIRED",
                        error_message="The execution lease expired before completion.",
                    )
                    store.save_job(tenant_id, failed_job)
                except ResourceNotFoundError:
                    pass
            else:
                # Active lease or finished SaasJob: return it
                job = store.get_job(tenant_id, state.job_id)
                return job, False

        # Acquire new lease
        job_id = f"job-{uuid.uuid4()}"
        lease_expires = now + timedelta(seconds=lease_duration_seconds)

        new_job = SaasJob(
            id=job_id,
            tenant_id=tenant_id,
            project_id=project_id,
            version_id=version_id,
            job_type=job_type,
            status=SaasJobStatus.PENDING,
            idempotency_key=idempotency_key,
            created_at=now,
        )

        new_state = IdempotencyState(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            job_id=job_id,
            status=SaasJobStatus.PENDING,
            lease_expires_at=lease_expires,
        )

        store.save_job_and_idempotency_state(tenant_id, new_job, new_state)

        return new_job, True


# Public compatibility surface for the original local SQLite CLI. The SaaS
# StateStore above deliberately uses the aliased Pydantic contracts instead.
Job = LegacyJob
JobStatus = LegacyJobStatus
JobStore = LegacyJobStore
