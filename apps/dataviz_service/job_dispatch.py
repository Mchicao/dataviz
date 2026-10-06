"""Closed job_type routing for the production worker.

Dispatch happens at this handler boundary only: the API enqueues a fixed
``job_type`` string and this module maps it to the single registered production
handler. No request input can name or import a handler, and
``compile_source_ast`` is never modified for routing purposes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.dataviz_service.contracts import Job, SanitizedServiceError
from apps.dataviz_service.job_worker import JobHandler, JobHandlerResult, _accepts_resume
from apps.dataviz_service.migration_job import PowerBIMigrationHandler, TableauMigrationHandler
from apps.dataviz_service.storage.backend import ObjectBackend

JOB_TYPE_TABLEAU_TO_PBIP = "TABLEAU_TO_PBIP"
JOB_TYPE_POWER_BI_IMPORT = "POWER_BI_IMPORT"


class UnsupportedJobTypeError(SanitizedServiceError):
    """Raised when a claimed job's type has no registered production handler."""

    def __init__(self) -> None:
        super().__init__(
            "The job type is not supported.",
            status_code=422,
            error_code="UNSUPPORTED_JOB_TYPE",
        )


def production_handlers(
    object_store: ObjectBackend, *, pbi_tools_path: str | None = None
) -> dict[str, JobHandler]:
    """Closed registry: both import origins share one durable worker runtime."""
    return {
        JOB_TYPE_TABLEAU_TO_PBIP: TableauMigrationHandler(object_store),
        JOB_TYPE_POWER_BI_IMPORT: PowerBIMigrationHandler(
            object_store, pbi_tools_path=pbi_tools_path
        ),
    }


def make_dispatcher(handlers: Mapping[str, JobHandler]) -> JobHandler:
    """Wrap a closed registry into a single resume-capable worker handler."""

    def dispatch(
        job: Job,
        input_payload: Mapping[str, Any],
        checkpoint: Any,
        resume: Mapping[str, Any] | None = None,
    ) -> JobHandlerResult:
        handler = handlers.get(job.job_type)
        if handler is None:
            # Sanitized failure: ProductionWorker marks the job FAILED with
            # this code, non-retryable, instead of guessing a handler.
            raise UnsupportedJobTypeError()
        if _accepts_resume(handler):
            return handler(job, input_payload, checkpoint, resume)
        return handler(job, input_payload, checkpoint)

    return dispatch
