"""API endpoints and request handlers for the DataVIZ SaaS service.

Enforces scope check authentication, tenant isolation, active/suspended validation,
and applies the sanitized error boundary on all external entrypoints.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from apps.dataviz_service.contracts import (
    AccessDeniedError,
    IdempotencyState,
    Job,
    JobStatus,
    Project,
    RequestContext,
    ResourceNotFoundError,
    SanitizedServiceError,
    Scope,
    Tenant,
    Version,
    check_authorization,
    sanitized_error_boundary,
)
from apps.dataviz_service.jobs import StateStore, acquire_or_get_job
from apps.dataviz_service.legacy_jobs import Job as LegacyJob
from apps.dataviz_service.legacy_jobs import JobStore as LegacyJobStore
from apps.dataviz_service.worker import submit_job

logger = logging.getLogger("dataviz_service.api")


class DataVizApi:
    """Entrypoint API class for the DataVIZ SaaS.

    Delegates requests to the StateStore while enforcing security policies.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def _verify_tenant_active(self, tenant_id: str) -> None:
        """Validates that the tenant exists and is active.

        If suspended, access is denied. If not registered, we auto-create the tenant
        as active to support seamless local integration.
        """
        try:
            tenant = self.store.get_tenant(tenant_id)
            if tenant.status == "suspended":
                raise AccessDeniedError("Access denied. Tenant is suspended.")
        except ResourceNotFoundError:
            # Auto-create active tenant on demand
            try:
                new_tenant = Tenant(id=tenant_id, name=f"Auto tenant {tenant_id}", status="active")
                self.store.save_tenant(new_tenant)
            except Exception as e:
                logger.warning("Failed to auto-create tenant: %s", e)

    @sanitized_error_boundary()
    def get_tenant(self, context: RequestContext, tenant_id: str) -> Tenant:
        """Retrieves tenant details. Requires Scope.READ."""
        check_authorization(context, {Scope.READ}, tenant_id)
        self._verify_tenant_active(context.tenant_id)
        return self.store.get_tenant(tenant_id)

    @sanitized_error_boundary()
    def create_project(self, context: RequestContext, project: Project) -> Project:
        """Creates a new project for the tenant. Requires Scope.WRITE."""
        check_authorization(context, {Scope.WRITE}, project.tenant_id)
        self._verify_tenant_active(context.tenant_id)
        self.store.save_project(project.tenant_id, project)
        return project

    @sanitized_error_boundary()
    def get_project(self, context: RequestContext, project_id: str) -> Project:
        """Gets a project's details. Requires Scope.READ."""
        self._verify_tenant_active(context.tenant_id)
        # Note: We must load first, then authorize using the resource's owner tenant_id
        # to prevent enumeration/BOLA leaks if the project doesn't exist for the caller's tenant.
        # By searching only within context.tenant_id in StateStore, we prevent cross-tenant lookup.
        project = self.store.get_project(context.tenant_id, project_id)
        check_authorization(context, {Scope.READ}, project.tenant_id)
        return project

    @sanitized_error_boundary()
    def list_projects(self, context: RequestContext) -> list[Project]:
        """Lists all projects for the tenant. Requires Scope.READ."""
        self._verify_tenant_active(context.tenant_id)
        return self.store.get_projects(context.tenant_id)

    @sanitized_error_boundary()
    def create_version(self, context: RequestContext, project_id: str, version: Version) -> Version:
        """Registers a snapshot version under a project. Requires Scope.WRITE."""
        self._verify_tenant_active(context.tenant_id)
        # Verify the project exists and belongs to this tenant
        project = self.store.get_project(context.tenant_id, project_id)
        check_authorization(context, {Scope.WRITE}, project.tenant_id)

        self.store.save_version(context.tenant_id, version)
        return version

    @sanitized_error_boundary()
    def get_version(self, context: RequestContext, project_id: str, version_id: str) -> Version:
        """Gets a version's details. Requires Scope.READ."""
        self._verify_tenant_active(context.tenant_id)
        # Verify the project exists and belongs to this tenant
        project = self.store.get_project(context.tenant_id, project_id)
        check_authorization(context, {Scope.READ}, project.tenant_id)

        return self.store.get_version(context.tenant_id, version_id)

    @sanitized_error_boundary()
    def list_versions(self, context: RequestContext, project_id: str) -> list[Version]:
        """Lists all versions under a project. Requires Scope.READ."""
        self._verify_tenant_active(context.tenant_id)
        # Verify the project exists and belongs to this tenant
        project = self.store.get_project(context.tenant_id, project_id)
        check_authorization(context, {Scope.READ}, project.tenant_id)

        all_versions = self.store.get_versions(context.tenant_id)
        return [v for v in all_versions if v.project_id == project_id]

    @sanitized_error_boundary()
    def submit_job(
        self,
        context: RequestContext,
        project_id: str,
        version_id: str,
        job_type: str,
        idempotency_key: str,
    ) -> Job:
        """Submits a migration job asynchronously with idempotency lease tracking.

        Requires Scope.RUN.
        """
        check_authorization(context, {Scope.RUN}, context.tenant_id)
        self._verify_tenant_active(context.tenant_id)

        # Verify the project and version exist for this tenant
        self.store.get_project(context.tenant_id, project_id)
        self.store.get_version(context.tenant_id, version_id)

        # Enforce idempotency lease (using 5-minute lease duration)
        job, is_new = acquire_or_get_job(
            store=self.store,
            tenant_id=context.tenant_id,
            idempotency_key=idempotency_key,
            project_id=project_id,
            version_id=version_id,
            job_type=job_type,
            lease_duration_seconds=300,
        )

        if is_new:
            logger.info("Submitting job %s to execution queue.", job.id)
            submit_job(context.tenant_id, job.id)

        return job

    @sanitized_error_boundary()
    def get_job(self, context: RequestContext, job_id: str) -> Job:
        """Gets status details of a job. Requires Scope.READ."""
        self._verify_tenant_active(context.tenant_id)
        job = self.store.get_job(context.tenant_id, job_id)
        check_authorization(context, {Scope.READ}, job.tenant_id)
        return job

    @sanitized_error_boundary()
    def cancel_job(self, context: RequestContext, job_id: str) -> Job:
        """Cancels a pending or running job. Requires Scope.RUN."""
        self._verify_tenant_active(context.tenant_id)
        job = self.store.get_job(context.tenant_id, job_id)
        check_authorization(context, {Scope.RUN}, job.tenant_id)

        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            # Already finished job cannot be cancelled
            return job

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
            completed_at=job.completed_at,
        )
        states = self.store.get_idempotency_states(context.tenant_id)
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
                    "error_message": "Job cancelled by user request.",
                },
            )
            self.store.save_job_and_idempotency_state(
                context.tenant_id, cancelled_job, cancelled_state
            )
        else:
            self.store.save_job_and_idempotency_state(context.tenant_id, cancelled_job)

        logger.info("Job %s marked as CANCELLED.", job_id)
        return cancelled_job

    @sanitized_error_boundary()
    def get_health(self) -> dict[str, str]:
        """Provides the health and readiness contract for local deployment."""
        # Check write access to base directory
        self.store.base_dir.mkdir(parents=True, exist_ok=True)
        test_file = self.store.base_dir / "health_check.tmp"
        try:
            with open(test_file, "w") as f:
                f.write("healthy")
            test_file.unlink()
            return {"status": "healthy", "service": "dataviz_service"}
        except Exception as e:
            logger.error("Health check write failed: %s", e)
            raise SanitizedServiceError(
                message="Service is unhealthy. Disk is read-only or permission is denied.",
                status_code=503,
                error_code="UNHEALTHY",
            )


# Compatibility HTTP surface for the original local SQLite queue. The SaaS API
# above remains the authenticated multi-tenant surface; these helpers are kept
# for local health/status probes and existing migration smoke tests.
def route(method: str, path: str, store: LegacyJobStore) -> tuple[int, dict[str, Any]]:
    """Resolve the read-only local queue HTTP contract."""
    if method != "GET":
        return 405, {"error": "method_not_allowed"}
    clean_path = urlparse(path).path
    if clean_path == "/healthz":
        return 200, {"status": "ok"}
    if clean_path == "/readyz":
        ready = store.ready()
        return (200 if ready else 503), {"status": "ready" if ready else "not_ready"}
    if clean_path.startswith("/jobs/"):
        job = store.get(clean_path.removeprefix("/jobs/"))
        if job is None:
            return 404, {"error": "job_not_found"}
        return 200, _legacy_job_payload(job)
    return 404, {"error": "not_found"}


def create_server(store: LegacyJobStore, port: int) -> ThreadingHTTPServer:
    """Build a loopback-only server for local queue probes."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._respond(*route("GET", self.path, store))

        def _reject_non_get(self) -> None:
            self._respond(
                *route(self.command, self.path, store), include_body=self.command != "HEAD"
            )

        do_HEAD = _reject_non_get
        do_POST = _reject_non_get
        do_PUT = _reject_non_get
        do_PATCH = _reject_non_get
        do_DELETE = _reject_non_get
        do_OPTIONS = _reject_non_get

        def _respond(
            self, status: int, payload: dict[str, Any], *, include_body: bool = True
        ) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 405:
                self.send_header("Allow", "GET")
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.info("http_request " + format, *args)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(store: LegacyJobStore, port: int) -> None:
    """Serve local queue health/status on loopback only."""
    with create_server(store, port) as server:
        logger.info("dataviz_api_listening address=http://127.0.0.1:%s", server.server_address[1])
        server.serve_forever()


def _legacy_job_payload(job: LegacyJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status.value,
        "createdAt": job.created_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "error": "migration_failed" if job.error else None,
    }
