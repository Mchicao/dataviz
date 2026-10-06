"""Approval-gated publication authority for versioned DataVIZ documents.

Publication is intentionally owned by the service governance layer. Callers name
a target version; they do not supply the diff that gets approved. The authority
resolves the immutable version snapshot from a tenant-scoped version resolver,
hashes its authoritative diff, and re-resolves the snapshot before activation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.dataviz_service.contracts import ResourceNotFoundError, SanitizedServiceError
from apps.dataviz_service.security.authorization import AuthorizationService, ResourceRef
from apps.dataviz_service.security.identity import Principal
from core.contracts.permissions import AuthoringAction


class PublicationKind(StrEnum):
    PUBLISH = "publish"
    ROLLBACK = "rollback"


class PublicationApprovalError(SanitizedServiceError):
    """Raised when publication has no valid approval for the exact candidate."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=412, error_code="PUBLICATION_APPROVAL_REQUIRED")


class PublicationConflictError(SanitizedServiceError):
    """Raised when a candidate/version is stale, mismatched, or already consumed."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, error_code="PUBLICATION_CONFLICT")


def _validate_sha256(value: str, *, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return normalized


class PublicationSnapshot(BaseModel):
    """Authoritative immutable version identity and its human-reviewable diff."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    organization_id: str
    project_id: str
    target_version: int = Field(ge=1)
    checksum: str
    diff_payload: Any

    @field_validator("tenant_id", "organization_id", "project_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("identifier must not be empty")
        return value.strip()

    @field_validator("checksum")
    @classmethod
    def _valid_checksum(cls, value: str) -> str:
        return _validate_sha256(value, field_name="checksum")


class PublicationVersionResolver(Protocol):
    """Tenant-scoped authority used to resolve immutable publication snapshots."""

    def resolve_publication_snapshot(
        self,
        resource: ResourceRef,
        target_version: int,
        *,
        base_version: int | None,
    ) -> PublicationSnapshot: ...


class PublicationRepository(Protocol):
    """Durable authority for candidates, approvals and the published pointer."""

    def save_publication_candidate(
        self, candidate: PublicationCandidate
    ) -> PublicationCandidate: ...

    def get_publication_candidate(
        self, tenant_id: str, candidate_id: str
    ) -> PublicationCandidate: ...

    def save_publication_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...

    def get_publication_approval(self, tenant_id: str, approval_id: str) -> ApprovalRecord: ...

    def activate_publication(
        self,
        tenant_id: str,
        *,
        candidate_id: str,
        approval_id: str,
        published_by: str,
    ) -> PublicationRecord: ...

    def get_published_version(self, tenant_id: str, project_id: str) -> int | None: ...

    def get_publication_history(
        self, tenant_id: str, project_id: str
    ) -> tuple[PublicationRecord, ...]: ...


class PublicationCandidate(BaseModel):
    """Immutable request to make one exact authoritative version/diff live."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    organization_id: str
    project_id: str
    target_version: int = Field(ge=1)
    expected_published_version: int | None = Field(default=None, ge=1)
    kind: PublicationKind = PublicationKind.PUBLISH
    target_checksum: str
    diff_sha256: str
    requested_by: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("id", "tenant_id", "organization_id", "project_id", "requested_by")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("identifier must not be empty")
        return value.strip()

    @field_validator("target_checksum")
    @classmethod
    def _valid_target_checksum(cls, value: str) -> str:
        return _validate_sha256(value, field_name="target_checksum")

    @field_validator("diff_sha256")
    @classmethod
    def _valid_diff_sha256(cls, value: str) -> str:
        return _validate_sha256(value, field_name="diff_sha256")


class ApprovalRecord(BaseModel):
    """Human approval bound to one immutable publication candidate."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: str
    tenant_id: str
    organization_id: str
    project_id: str
    target_version: int = Field(ge=1)
    kind: PublicationKind
    target_checksum: str
    diff_sha256: str
    approved_by: str
    approved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicationRecord(BaseModel):
    """Immutable audit record for a publication or approved rollback."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: str
    approval_id: str
    tenant_id: str
    organization_id: str
    project_id: str
    previous_version: int | None = Field(default=None, ge=1)
    target_version: int = Field(ge=1)
    kind: PublicationKind
    target_checksum: str
    diff_sha256: str
    approved_by: str
    approved_at: datetime
    published_by: str
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _canonical_sha256(payload: Any, *, label: str) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def publication_diff_sha256(diff_payload: Any) -> str:
    """Hash a JSON-safe diff canonically so key order cannot change approval identity."""
    return _canonical_sha256(diff_payload, label="publication diff")


def publication_version_payload(
    semantic_model: Any, presentation_ir: Any, interaction_ir: Any
) -> dict[str, Any]:
    """Canonical three-IR payload used only for durable identity/checksums."""
    return {
        "semantic": semantic_model,
        "presentation": presentation_ir,
        "interaction": interaction_ir,
    }


def publication_version_checksum(
    semantic_model: Any, presentation_ir: Any, interaction_ir: Any
) -> str:
    """Checksum exactly the canonical Semantic/Presentation/Interaction IR triple."""
    return _canonical_sha256(
        publication_version_payload(semantic_model, presentation_ir, interaction_ir),
        label="publication version",
    )


def publication_version_diff_payload(
    base_payload: dict[str, Any] | None, target_payload: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic review identity derived from durable three-IR snapshots."""
    return {"base": base_payload, "target": target_payload}


class PublicationService:
    """Domain orchestration over one durable publication repository.

    The service owns authorization and snapshot-validation rules, but it does
    not own publication state. Candidates, approvals, the published pointer and
    history live exclusively behind ``PublicationRepository``.
    """

    def __init__(
        self,
        authorization: AuthorizationService,
        versions: PublicationVersionResolver,
        repository: PublicationRepository,
    ) -> None:
        self._authorization = authorization
        self._versions = versions
        self._repository = repository

    def request_publication(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        target_version: int,
    ) -> PublicationCandidate:
        self._authorization.authorize_action(principal, AuthoringAction.REQUEST_PUBLISH, resource)
        return self._create_candidate(
            principal,
            resource,
            target_version=target_version,
            kind=PublicationKind.PUBLISH,
        )

    def request_rollback(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        target_version: int,
    ) -> PublicationCandidate:
        self._authorization.authorize_action(principal, AuthoringAction.REVERT, resource)
        if resource.project_id is None:
            raise ValueError("publication resource must identify a project")
        current = self._repository.get_published_version(resource.tenant_id, resource.project_id)
        if current is None:
            raise PublicationConflictError("Cannot roll back a project with no published version.")
        if target_version == current:
            raise PublicationConflictError("Rollback target is already the published version.")
        published_targets = {
            record.target_version
            for record in self._repository.get_publication_history(
                resource.tenant_id, resource.project_id
            )
        }
        if target_version not in published_targets:
            raise PublicationConflictError("Rollback target was never published by this authority.")
        return self._create_candidate(
            principal,
            resource,
            target_version=target_version,
            kind=PublicationKind.ROLLBACK,
        )

    def approve(self, principal: Principal, candidate_id: str) -> ApprovalRecord:
        candidate = self._get_candidate(principal.tenant_id, candidate_id)
        resource = self._candidate_resource(candidate)
        self._authorization.authorize_action(principal, AuthoringAction.PUBLISH, resource)
        if principal.id == candidate.requested_by:
            raise PublicationApprovalError(
                "The publication requester cannot approve the same candidate."
            )
        self._assert_candidate_snapshot_current(candidate)
        approval = ApprovalRecord(
            candidate_id=candidate.id,
            tenant_id=candidate.tenant_id,
            organization_id=candidate.organization_id,
            project_id=candidate.project_id,
            target_version=candidate.target_version,
            kind=candidate.kind,
            target_checksum=candidate.target_checksum,
            diff_sha256=candidate.diff_sha256,
            approved_by=principal.id,
        )
        return self._repository.save_publication_approval(approval)

    def publish(
        self,
        principal: Principal,
        candidate_id: str,
        *,
        approval_id: str | None,
    ) -> PublicationRecord:
        candidate = self._get_candidate(principal.tenant_id, candidate_id)
        if candidate.kind is not PublicationKind.PUBLISH:
            raise PublicationConflictError("Rollback candidates must use rollback().")
        resource = self._candidate_resource(candidate)
        self._authorization.authorize_action(principal, AuthoringAction.PUBLISH, resource)
        self._assert_candidate_snapshot_current(candidate)
        approval = self._validated_approval(candidate, approval_id)
        return self._repository.activate_publication(
            candidate.tenant_id,
            candidate_id=candidate.id,
            approval_id=approval.id,
            published_by=principal.id,
        )

    def rollback(
        self,
        principal: Principal,
        candidate_id: str,
        *,
        approval_id: str | None,
    ) -> PublicationRecord:
        candidate = self._get_candidate(principal.tenant_id, candidate_id)
        if candidate.kind is not PublicationKind.ROLLBACK:
            raise PublicationConflictError("Publication candidates must use publish().")
        resource = self._candidate_resource(candidate)
        self._authorization.authorize_action(principal, AuthoringAction.REVERT, resource)
        self._assert_candidate_snapshot_current(candidate)
        approval = self._validated_approval(candidate, approval_id)
        return self._repository.activate_publication(
            candidate.tenant_id,
            candidate_id=candidate.id,
            approval_id=approval.id,
            published_by=principal.id,
        )

    def published_version(self, resource: ResourceRef) -> int | None:
        if resource.project_id is None:
            raise ValueError("publication resource must identify a project")
        return self._repository.get_published_version(resource.tenant_id, resource.project_id)

    def history(self, resource: ResourceRef) -> tuple[PublicationRecord, ...]:
        if resource.project_id is None:
            raise ValueError("publication resource must identify a project")
        return self._repository.get_publication_history(resource.tenant_id, resource.project_id)

    def _create_candidate(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        target_version: int,
        kind: PublicationKind,
    ) -> PublicationCandidate:
        if resource.project_id is None:
            raise ValueError("publication resource must identify a project")
        expected = self._repository.get_published_version(resource.tenant_id, resource.project_id)
        snapshot = self._resolve_snapshot(resource, target_version, base_version=expected)
        candidate = PublicationCandidate(
            tenant_id=resource.tenant_id,
            organization_id=resource.organization_id,
            project_id=resource.project_id,
            target_version=target_version,
            expected_published_version=expected,
            kind=kind,
            target_checksum=snapshot.checksum,
            diff_sha256=publication_diff_sha256(snapshot.diff_payload),
            requested_by=principal.id,
        )
        return self._repository.save_publication_candidate(candidate)

    def _assert_candidate_snapshot_current(self, candidate: PublicationCandidate) -> None:
        resource = self._candidate_resource(candidate)
        snapshot = self._resolve_snapshot(
            resource,
            candidate.target_version,
            base_version=candidate.expected_published_version,
        )
        current_diff_hash = publication_diff_sha256(snapshot.diff_payload)
        if snapshot.checksum != candidate.target_checksum or current_diff_hash != candidate.diff_sha256:
            raise PublicationConflictError(
                "The authoritative target version or reviewed diff changed after the candidate was created."
            )

    def _validated_approval(
        self,
        candidate: PublicationCandidate,
        approval_id: str | None,
    ) -> ApprovalRecord:
        if approval_id is None:
            raise PublicationApprovalError("Human approval is required before publication.")
        try:
            approval = self._repository.get_publication_approval(candidate.tenant_id, approval_id)
        except ResourceNotFoundError as exc:
            raise PublicationApprovalError("Publication approval does not exist.") from exc
        if approval.candidate_id != candidate.id:
            raise PublicationApprovalError("Approval belongs to a different publication candidate.")
        if approval.target_checksum != candidate.target_checksum:
            raise PublicationApprovalError("Approval version checksum does not match the candidate.")
        if approval.diff_sha256 != candidate.diff_sha256:
            raise PublicationApprovalError("Approval diff hash does not match the candidate.")
        return approval

    def _resolve_snapshot(
        self,
        resource: ResourceRef,
        target_version: int,
        *,
        base_version: int | None,
    ) -> PublicationSnapshot:
        snapshot = self._versions.resolve_publication_snapshot(
            resource,
            target_version,
            base_version=base_version,
        )
        if not isinstance(snapshot, PublicationSnapshot):
            raise TypeError("publication version resolver must return PublicationSnapshot")
        if (
            snapshot.tenant_id != resource.tenant_id
            or snapshot.organization_id != resource.organization_id
            or snapshot.project_id != resource.project_id
            or snapshot.target_version != target_version
        ):
            raise PublicationConflictError(
                "Publication version resolver returned a snapshot for a different resource/version."
            )
        publication_diff_sha256(snapshot.diff_payload)
        return snapshot

    def _get_candidate(self, tenant_id: str, candidate_id: str) -> PublicationCandidate:
        return self._repository.get_publication_candidate(tenant_id, candidate_id)

    @staticmethod
    def _candidate_resource(candidate: PublicationCandidate) -> ResourceRef:
        return ResourceRef(
            tenant_id=candidate.tenant_id,
            organization_id=candidate.organization_id,
            project_id=candidate.project_id,
        )


__all__ = [
    "ApprovalRecord",
    "PublicationApprovalError",
    "PublicationCandidate",
    "PublicationConflictError",
    "PublicationKind",
    "PublicationRecord",
    "PublicationRepository",
    "PublicationService",
    "PublicationSnapshot",
    "PublicationVersionResolver",
    "publication_diff_sha256",
    "publication_version_checksum",
    "publication_version_diff_payload",
    "publication_version_payload",
]
