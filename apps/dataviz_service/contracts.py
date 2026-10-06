"""Data contracts, security scopes, idempotency, and error boundary sanitization for the DataVIZ SaaS service."""

import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("dataviz_service.contracts")

# =====================================================================
# 1. Security Scopes & Deny-by-Default Authorization
# =====================================================================


class Scope(StrEnum):
    """Authorization scopes available in the SaaS service."""

    READ = "dataviz:read"
    WRITE = "dataviz:write"
    RUN = "jobs:run"
    ADMIN = "admin:all"


class RequestContext(BaseModel):
    """Security context of the authenticated user or service initiating a request."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="ID of the authenticated tenant")
    scopes: set[Scope] = Field(
        default_factory=set, description="Set of authorization scopes associated with the request"
    )

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Tenant ID must not be empty")
        return v.strip()


# =====================================================================
# 2. Exceptions and Sanitized Error Boundary
# =====================================================================


class SanitizedServiceError(Exception):
    """Base class for controlled business exceptions safe to expose to external clients."""

    def __init__(
        self, message: str, status_code: int = 400, error_code: str = "BAD_REQUEST"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.correlation_id: str = str(uuid.uuid4())


class AccessDeniedError(SanitizedServiceError):
    """Raised when scopes are insufficient or tenant boundaries are violated."""

    def __init__(self, message: str = "Access denied. Insufficient permissions.") -> None:
        super().__init__(message, status_code=403, error_code="ACCESS_DENIED")


class ResourceNotFoundError(SanitizedServiceError):
    """Raised when a requested resource is not found."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        super().__init__(
            f"The resource '{resource_type}' with ID '{resource_id}' was not found.",
            status_code=404,
            error_code="RESOURCE_NOT_FOUND",
        )


class IdempotencyConflictError(SanitizedServiceError):
    """Raised when an idempotency collision or concurrent job execution conflict is detected."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, error_code="IDEMPOTENCY_CONFLICT")


class SafeErrorResponse(BaseModel):
    """Structured, sanitized error response returned to external clients."""

    model_config = ConfigDict(frozen=True)

    status_code: int = Field(..., description="HTTP status code")
    error_code: str = Field(..., description="Internal error code suffix/identifier")
    message: str = Field(..., description="Sanitized, safe error message description")
    correlation_id: str = Field(..., description="Correlation ID for matching server-side logs")


F = TypeVar("F", bound=Callable[..., Any])


def sanitized_error_boundary() -> Callable[[F], F]:
    """Decorator to intercept and sanitize exceptions at API boundaries.

    Any SanitizedServiceError is raised directly.
    Any other unexpected internal exception is masked as an INTERNAL_SERVER_ERROR,
    and the real exception is logged internally with a correlation ID.
    """

    def decorator(func: F) -> F:
        import functools

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except SanitizedServiceError as sse:
                raise sse
            except Exception as ex:
                correlation_id = str(uuid.uuid4())
                logger.error(
                    "Unexpected internal error [Correlation ID: %s]: %s",
                    correlation_id,
                    str(ex),
                    exc_info=True,
                )
                safe_error = SanitizedServiceError(
                    message="An unexpected error occurred while processing your request. Please contact support.",
                    status_code=500,
                    error_code="INTERNAL_SERVER_ERROR",
                )
                safe_error.correlation_id = correlation_id
                raise safe_error from ex

        return wrapper  # type: ignore

    return decorator


def check_authorization(
    context: RequestContext, required_scopes: set[Scope], resource_tenant_id: str
) -> None:
    """Verifies that the request context has authorization to access a tenant's resource.

    Enforces two strict constraints:
    1. Tenant Isolation: The request context's tenant_id must match the resource_tenant_id.
    2. Deny-by-Default Scopes: The context must contain Scope.ADMIN or at least one of required_scopes.
    """
    if context.tenant_id != resource_tenant_id:
        logger.warning(
            "Access denied: Context tenant '%s' does not match resource tenant '%s'",
            context.tenant_id,
            resource_tenant_id,
        )
        raise AccessDeniedError()

    if Scope.ADMIN in context.scopes:
        return

    if required_scopes and not (context.scopes & required_scopes):
        logger.warning(
            "Access denied: Request context scopes %s do not contain any of required scopes %s",
            [s.value for s in context.scopes],
            [s.value for s in required_scopes],
        )
        raise AccessDeniedError()


# =====================================================================
# 3. SaaS Entity Data Contracts
# =====================================================================


class Tenant(BaseModel):
    """Data contract representing a Tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique identifier of the tenant")
    name: str = Field(..., description="Commercial name of the tenant")
    status: str = Field(default="active", description="Status of the tenant (active, suspended)")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    quotas: dict[str, int | float] = Field(
        default_factory=dict,
        description="Quota definitions for storage limits, projects, or job concurrency",
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Tenant ID must not be empty")
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Tenant name must not be empty")
        if len(v) > 100:
            raise ValueError("Tenant name must not exceed 100 characters")
        return v.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid_statuses = {"active", "suspended"}
        if v not in valid_statuses:
            raise ValueError(f"Tenant status must be one of: {valid_statuses}")
        return v


class Project(BaseModel):
    """Data contract representing a Project within a Tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique identifier of the project")
    tenant_id: str = Field(..., description="ID of the owner tenant")
    name: str = Field(..., description="Name of the project")
    description: str | None = Field(default=None, description="Optional description of the project")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("id", "tenant_id")
    @classmethod
    def validate_non_empty_ids(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Project and Tenant IDs must not be empty")
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Project name must not be empty")
        if len(v) > 100:
            raise ValueError("Project name must not exceed 100 characters")
        return v.strip()

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> str | None:
        if v is not None:
            if len(v) > 500:
                raise ValueError("Project description must not exceed 500 characters")
            return v.strip()
        return v


class Version(BaseModel):
    """Data contract representing a versioned snapshot of migration metadata."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique identifier of the version")
    project_id: str = Field(..., description="ID of the associated project")
    version_number: int = Field(..., description="Incremental version number")
    checksum: str = Field(..., description="SHA-256 checksum of the layout/migration report")
    is_active: bool = Field(
        default=True, description="Flag indicating if this is the active version"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("id", "project_id")
    @classmethod
    def validate_non_empty_ids(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Version and Project IDs must not be empty")
        return v.strip()

    @field_validator("version_number")
    @classmethod
    def validate_version_number(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Version number must be greater than 0")
        return v

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[0-9a-f]{64}$", v):
            raise ValueError("Checksum must be a valid 64-character SHA-256 hex string")
        return v


class JobStatus(StrEnum):
    """Asynchronous job execution states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Job(BaseModel):
    """Data contract representing an asynchronous migration execution task (Job)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Unique identifier of the job")
    tenant_id: str = Field(..., description="ID of the owner tenant")
    project_id: str = Field(..., description="ID of the associated project")
    version_id: str = Field(..., description="ID of the associated version")
    job_type: str = Field(..., description="Type of job (e.g. TABLEAU_TO_PBIP)")
    status: JobStatus = Field(default=JobStatus.PENDING, description="Current execution state")
    idempotency_key: str = Field(..., description="Unique key for idempotent execution tracking")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = Field(default=None, description="Timestamp of execution start")
    completed_at: datetime | None = Field(
        default=None, description="Timestamp of execution completion"
    )
    error_code: str | None = Field(default=None, description="Sanitized internal error code suffix")
    error_message: str | None = Field(default=None, description="Sanitized public error message")

    @field_validator("id", "tenant_id", "project_id", "version_id", "job_type", "idempotency_key")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("String field must not be empty")
        return v.strip()


# =====================================================================
# 4. Idempotency State Contract
# =====================================================================


class IdempotencyState(BaseModel):
    """Data contract tracking the execution state and cached result for idempotency."""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str = Field(..., description="Unique idempotency identifier")
    tenant_id: str = Field(..., description="Tenant owner ID")
    job_id: str = Field(..., description="Associated job ID")
    status: JobStatus = Field(..., description="Current status of the associated job")
    lease_expires_at: datetime = Field(..., description="Time limit for the active lock lease")
    cached_result: dict[str, Any] | None = Field(
        default=None, description="Sanitized result cache if job is completed"
    )

    @field_validator("idempotency_key", "tenant_id", "job_id")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("String field must not be empty")
        return v.strip()
