"""Tenant-isolated, immutable-version artifact storage.

:class:`ArtifactService` layers two hard boundaries on top of a dumb
:class:`~apps.dataviz_service.storage.backend.ObjectBackend`:

  1. **Tenant prefix isolation.** Every object key is tenant-led and every
     operation requires ``principal.tenant_id`` to equal the resource's tenant.
     A principal can never read, write, list, or delete another tenant's prefix,
     and ``list`` is always scoped under the caller's tenant (there is no global
     listing).
  2. **Immutable versions.** A written artifact version can never be overwritten
     or deleted: ``put`` on an existing key raises
     :class:`ArtifactImmutableError` and ``delete`` on any existing key raises
     the same. The only way to "change" an artifact is to write a new version id.

Path-traversal hardening: every key segment is validated by :func:`_safe_segment`,
which rejects empty values, separators (``/``, ``\\``), the dot/dot-dot names,
control characters, and anything outside a conservative ``[A-Za-z0-9._-]`` set.
This prevents a malicious blob name from escaping its tenant prefix.

The service does not perform RBAC; that is the job of
:class:`~apps.dataviz_service.security.authorization.AuthorizationService` at the
endpoint layer, exactly as @Docs/architecture/tenant_security.md prescribes.
The expected wiring is: coarse scope gate -> RBAC decision -> artifact call.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.dataviz_service.contracts import (
    AccessDeniedError,
    ResourceNotFoundError,
    SanitizedServiceError,
)
from apps.dataviz_service.security.authorization import ResourceRef
from apps.dataviz_service.security.identity import Principal
from apps.dataviz_service.storage.backend import ObjectBackend

logger = logging.getLogger("dataviz_service.storage.artifacts")

# Conservative allow-list for any single hierarchical key segment. Keeping the
# alphabet small and bounded makes prefix-escape attempts trivially detectable.
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def _safe_segment(value: str, field: str) -> str:
    """Validate a single hierarchical key segment to prevent prefix escape.

    Raises ``ValueError`` if the value is empty, contains a path separator
    (``/`` or ``\\``), equals ``.`` or ``..``, contains a newline/control
    character, or falls outside the safe alphabet.
    """
    v = value.strip() if isinstance(value, str) else ""
    if (
        not v
        or "/" in v
        or "\\" in v
        or "\n" in v
        or v in (".", "..")
        or not _SAFE_SEGMENT_RE.match(v)
    ):
        raise ValueError(
            f"Invalid artifact key segment '{field}': must be 1-255 characters of [A-Za-z0-9._-]."
        )
    return v


class ArtifactImmutableError(SanitizedServiceError):
    """Raised when an operation would overwrite or delete an immutable version."""

    def __init__(self, message: str = "Artifact version is immutable.") -> None:
        super().__init__(message, status_code=409, error_code="ARTIFACT_IMMUTABLE")


class ArtifactRef(BaseModel):
    """Tenant-led reference to a single blob inside a versioned artifact.

    The :class:`ResourceRef` carries the tenant/organization/project/version
    identity (reused from the authorization layer so storage and RBAC share one
    resource model); ``name`` is the blob within that version.
    """

    model_config = ConfigDict(frozen=True)

    resource: ResourceRef
    name: str = Field(..., description="Blob name within the version")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _safe_segment(v, "name")

    @model_validator(mode="after")
    def _require_version(self) -> ArtifactRef:
        # Storage is versioned by definition: a version id is mandatory.
        if not self.resource.version_id:
            raise ValueError("ArtifactRef.resource.version_id is required")
        return self

    @property
    def tenant_id(self) -> str:
        return self.resource.tenant_id

    @property
    def object_key(self) -> str:
        """Canonical tenant-led object key.

        Every segment is re-validated here as defense in depth: even if a
        :class:`ResourceRef` somehow carried an unsafe identifier, it could not
        escape the tenant prefix.
        """
        parts = [
            _safe_segment(self.resource.tenant_id, "tenant_id"),
            _safe_segment(self.resource.organization_id, "organization_id"),
        ]
        if self.resource.project_id:
            parts.append(_safe_segment(self.resource.project_id, "project_id"))
        parts.append(_safe_segment(self.resource.version_id, "version_id"))
        parts.append(_safe_segment(self.name, "name"))
        return "/".join(parts)


class ArtifactVersion(BaseModel):
    """Immutable metadata record returned when a version is written."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    organization_id: str
    project_id: str | None
    version_id: str
    name: str
    object_key: str
    size_bytes: int = Field(..., ge=0)
    checksum: str
    created_at: datetime


class ArtifactService:
    """Tenant-isolated, immutable-version artifact storage."""

    def __init__(
        self,
        backend: ObjectBackend,
        *,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._backend = backend
        self._clock = clock

    def put(self, principal: Principal, ref: ArtifactRef, data: bytes) -> ArtifactVersion:
        """Write a new immutable version. Rejects cross-tenant and overwrite."""
        self._require_same_tenant(principal, ref.resource)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        payload = bytes(data)
        key = ref.object_key
        if not self._backend.put_if_absent(key, payload):
            logger.warning(
                "Immutable artifact overwrite blocked: key=%s tenant=%s",
                key,
                principal.tenant_id,
            )
            raise ArtifactImmutableError()
        version = ArtifactVersion(
            tenant_id=ref.resource.tenant_id,
            organization_id=ref.resource.organization_id,
            project_id=ref.resource.project_id,
            version_id=ref.resource.version_id,
            name=ref.name,
            object_key=key,
            size_bytes=len(payload),
            checksum=hashlib.sha256(payload).hexdigest(),
            created_at=self._clock(),
        )
        logger.info(
            "Artifact written: key=%s size=%d tenant=%s",
            key,
            len(payload),
            principal.tenant_id,
        )
        return version

    def get(self, principal: Principal, ref: ArtifactRef) -> bytes:
        """Return the bytes of an artifact. Cross-tenant access is denied."""
        self._require_same_tenant(principal, ref.resource)
        return self._backend.get(ref.object_key)

    def delete(self, principal: Principal, ref: ArtifactRef) -> None:
        """Reject deletion of an existing version (immutability).

        A missing object raises :class:`ResourceNotFoundError`; this method
        exists primarily to make the immutability contract explicit and
        testable.
        """
        self._require_same_tenant(principal, ref.resource)
        key = ref.object_key
        if self._backend.exists(key):
            logger.warning(
                "Immutable artifact delete blocked: key=%s tenant=%s",
                key,
                principal.tenant_id,
            )
            raise ArtifactImmutableError()
        raise ResourceNotFoundError("artifact", key)

    def list(self, principal: Principal, resource: ResourceRef) -> list[str]:
        """List object keys under a tenant-scoped prefix.

        Always tenant-led: the prefix is derived from the caller's resource and
        cross-tenant listing is denied before reaching the backend.
        """
        self._require_same_tenant(principal, resource)
        return self._backend.list(self._prefix_for(resource))

    def _prefix_for(self, resource: ResourceRef) -> str:
        parts = [
            _safe_segment(resource.tenant_id, "tenant_id"),
            _safe_segment(resource.organization_id, "organization_id"),
        ]
        if resource.project_id:
            parts.append(_safe_segment(resource.project_id, "project_id"))
        if resource.version_id:
            parts.append(_safe_segment(resource.version_id, "version_id"))
        return "/".join(parts) + "/"

    @staticmethod
    def _require_same_tenant(principal: Principal, resource: ResourceRef) -> None:
        if principal.tenant_id != resource.tenant_id:
            logger.warning(
                "Cross-tenant artifact access denied: principal_tenant=%s resource_tenant=%s",
                principal.tenant_id,
                resource.tenant_id,
            )
            raise AccessDeniedError("Access denied: artifact belongs to a different tenant.")
