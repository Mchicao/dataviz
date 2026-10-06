"""Data governance models for catalog, lineage, glossary, ownership, certified metrics, and audit logs.

All models enforce strict tenant boundaries and auditability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetType(StrEnum):
    """Supported asset types in the data catalog."""

    DATASET = "dataset"
    TABLE = "table"
    COLUMN = "column"
    METRIC = "metric"
    REPORT = "report"
    DASHBOARD = "dashboard"


class CertificationStatus(StrEnum):
    """Certification lifecycle status for metrics and semantic assets."""

    PROPOSED = "proposed"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"


class OwnerType(StrEnum):
    """Classification of asset owners."""

    USER = "user"
    TEAM = "team"


class AuditEventAction(StrEnum):
    """Governance action types recorded in the audit log."""

    ASSET_CREATED = "asset_created"
    ASSET_UPDATED = "asset_updated"
    OWNERSHIP_ASSIGNED = "ownership_assigned"
    METRIC_CERTIFIED = "metric_certified"
    GLOSSARY_TERM_CREATED = "glossary_term_created"
    LINEAGE_LINKED = "lineage_linked"


class CatalogItem(BaseModel):
    """Catalog entry representing a data or analytics asset within a tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Catalog asset ID")
    tenant_id: str = Field(..., description="Tenant owner ID")
    name: str = Field(..., description="Name of the asset")
    asset_type: AssetType = Field(..., description="Asset type classification")
    description: str = Field(default="", description="Detailed description")
    owner_id: str | None = Field(default=None, description="Primary owner principal/team ID")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary typed metadata")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    @field_validator("id", "tenant_id", "name")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class LineageEdge(BaseModel):
    """Directed dependency link between two catalog assets within a tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Lineage edge ID")
    tenant_id: str = Field(..., description="Tenant boundary")
    source_id: str = Field(..., description="Upstream asset ID")
    target_id: str = Field(..., description="Downstream asset ID")
    relationship_type: str = Field(default="DERIVED_FROM", description="Type of relationship")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")

    @field_validator("id", "tenant_id", "source_id", "target_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifier must not be empty")
        return v.strip()


class LineageGraph(BaseModel):
    """Complete lineage graph for an asset within a tenant boundary."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Tenant boundary")
    root_id: str = Field(..., description="Target asset ID for which lineage was requested")
    nodes: list[CatalogItem] = Field(
        default_factory=list, description="All connected catalog nodes"
    )
    edges: list[LineageEdge] = Field(
        default_factory=list, description="All connected lineage edges"
    )
    upstream_ids: list[str] = Field(
        default_factory=list, description="Upstream dependency asset IDs"
    )
    downstream_ids: list[str] = Field(
        default_factory=list, description="Downstream dependent asset IDs"
    )


class GlossaryTerm(BaseModel):
    """Business glossary term definition scoped to a tenant."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Glossary term ID")
    tenant_id: str = Field(..., description="Tenant owner ID")
    name: str = Field(..., description="Term title")
    definition: str = Field(..., description="Formal definition")
    synonyms: list[str] = Field(default_factory=list, description="Alternative names/synonyms")
    linked_asset_ids: list[str] = Field(
        default_factory=list, description="IDs of linked catalog assets"
    )
    owner_id: str | None = Field(default=None, description="Term steward ID")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    @field_validator("id", "tenant_id", "name", "definition")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class AssetOwnership(BaseModel):
    """Ownership assignment record for a governance asset."""

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(..., description="Target catalog asset ID")
    tenant_id: str = Field(..., description="Tenant boundary")
    owner_id: str = Field(..., description="Owner principal/team ID")
    owner_type: OwnerType = Field(default=OwnerType.USER, description="Type of owner")
    assigned_by: str = Field(..., description="Principal ID who assigned ownership")
    assigned_at: datetime = Field(
        default_factory=datetime.utcnow, description="Assignment timestamp"
    )

    @field_validator("asset_id", "tenant_id", "owner_id", "assigned_by")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifier must not be empty")
        return v.strip()


class CertifiedMetric(BaseModel):
    """Certified business metric specification."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Certified metric ID")
    tenant_id: str = Field(..., description="Tenant boundary")
    metric_name: str = Field(..., description="Canonical metric name")
    expression: str = Field(..., description="Calculation expression or AST representation")
    status: CertificationStatus = Field(default=CertificationStatus.PROPOSED, description="Status")
    certified_by: str | None = Field(default=None, description="Certifier principal ID")
    certified_at: datetime | None = Field(default=None, description="Certification timestamp")
    reason: str | None = Field(default=None, description="Certification note or reason")
    catalog_item_id: str | None = Field(default=None, description="Associated catalog item ID")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    @field_validator("id", "tenant_id", "metric_name", "expression")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class AuditEvent(BaseModel):
    """Immutable governance audit event."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Audit event ID")
    tenant_id: str = Field(..., description="Tenant boundary")
    actor_id: str = Field(..., description="Principal ID performing the action")
    action: AuditEventAction = Field(..., description="Action performed")
    target_id: str = Field(..., description="Target asset ID")
    target_type: str = Field(..., description="Type of target object")
    details: dict[str, Any] = Field(default_factory=dict, description="Audit event details payload")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")

    @field_validator("id", "tenant_id", "actor_id", "target_id", "target_type")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifier must not be empty")
        return v.strip()
