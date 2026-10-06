"""Internal read-only facade over the Knowledge Plane (D-KP-007).

Read surfaces are typed, tenant-scoped and carry a ``schema_version`` +
``as_of`` envelope. Every operation authorizes ``Permission.READ`` through the
canonical authorization service before delegating the tenant-scoped read to a
repository. The facade owns no SQL, no free-text query carriers and no write
capability: there is deliberately no register/approve/publish surface here.
Bounded lineage is limited to the direct usage edges the current registry
already projects safely (depth-1 ``used_by`` around a metric); general lineage
traversal is out of scope (KP-LINEAGE-001).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.dataviz_service.contracts import ResourceNotFoundError
from apps.dataviz_service.security.authorization import (
    AuthorizationService,
    Permission,
    ResourceRef,
)
from apps.dataviz_service.security.identity import Principal
from core.contracts.knowledge import SCHEMA_VERSION, AssetKind, KnowledgeAsset

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100
MAX_LINEAGE_EDGES = 100
LINEAGE_DEPTH_LIMIT = 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeReadRepository(Protocol):
    """Read-only repository contract satisfied by the production registry."""

    def get_asset(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None: ...

    def list_assets(
        self,
        tenant_id: str,
        *,
        asset_kind: AssetKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[KnowledgeAsset], bool]: ...

    def describe_metric(self, tenant_id: str, metric_id: str) -> dict[str, Any]: ...


class KnowledgeReadEnvelope(BaseModel):
    """Base read envelope: schema version plus read timestamp (D-KP-007)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    as_of: datetime = Field(default_factory=_utc_now)
    tenant_id: str

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        return value

    @field_validator("tenant_id")
    @classmethod
    def _tenant_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("tenant_id must not be empty")
        return value.strip()


class AssetListEnvelope(KnowledgeReadEnvelope):
    """Paginated asset listing with bounded page size."""

    asset_kind: AssetKind | None = None
    limit: int
    offset: int
    has_more: bool = False
    items: list[KnowledgeAsset] = Field(default_factory=list)


class AssetEnvelope(KnowledgeReadEnvelope):
    """Single asset detail."""

    asset: KnowledgeAsset


class MetricEnvelope(KnowledgeReadEnvelope):
    """Metric governance detail with its bounded depth-1 usage lineage."""

    metric: dict[str, Any]
    used_by: list[dict[str, Any]] = Field(default_factory=list)
    business_rules: list[dict[str, Any]] = Field(default_factory=list)
    insights: list[dict[str, Any]] = Field(default_factory=list)
    lineage_depth: int = LINEAGE_DEPTH_LIMIT
    lineage_truncated: bool = False


class KnowledgeReadService:
    """Authorization-gated, tenant-scoped read surface for the Knowledge Plane.

    Trusted arguments arrive as ``principal`` + ``resource``; identifiers are
    validated, page sizes are clamped, and no free-text query is accepted.
    """

    def __init__(
        self,
        *,
        authorization: AuthorizationService,
        repository: KnowledgeReadRepository,
    ) -> None:
        self._authorization = authorization
        self._repository = repository

    def list_assets(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        asset_kind: AssetKind | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> AssetListEnvelope:
        """Return a bounded, authorized asset page for ``resource.tenant_id``."""
        self._authorization.authorize(principal, Permission.READ, resource)
        if asset_kind is not None and not isinstance(asset_kind, AssetKind):
            raise ValueError("asset_kind must be an AssetKind")
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        if offset < 0:
            raise ValueError("offset must be non-negative")
        items, has_more = self._repository.list_assets(
            resource.tenant_id,
            asset_kind=asset_kind,
            limit=limit,
            offset=offset,
        )
        return AssetListEnvelope(
            as_of=_utc_now(),
            tenant_id=resource.tenant_id,
            asset_kind=asset_kind,
            limit=limit,
            offset=offset,
            has_more=has_more,
            items=items,
        )

    def get_asset(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        asset_id: str,
    ) -> AssetEnvelope:
        """Return one asset or raise ``ResourceNotFoundError`` (404)."""
        self._authorization.authorize(principal, Permission.READ, resource)
        if not asset_id or not asset_id.strip():
            raise ValueError("asset_id must not be empty")
        asset_id = asset_id.strip()
        asset = self._repository.get_asset(resource.tenant_id, asset_id)
        if asset is None:
            raise ResourceNotFoundError("asset", asset_id)
        return AssetEnvelope(as_of=_utc_now(), tenant_id=resource.tenant_id, asset=asset)

    def get_metric(
        self,
        principal: Principal,
        resource: ResourceRef,
        *,
        metric_id: str,
    ) -> MetricEnvelope:
        """Return metric governance with bounded depth-1 usage lineage."""
        self._authorization.authorize(principal, Permission.READ, resource)
        if not metric_id or not metric_id.strip():
            raise ValueError("metric_id must not be empty")
        metric_id = metric_id.strip()
        try:
            detail = self._repository.describe_metric(resource.tenant_id, metric_id)
        except ValueError as exc:
            raise ResourceNotFoundError("metric", metric_id) from exc
        used_by = detail.get("used_by", [])
        truncated = len(used_by) > MAX_LINEAGE_EDGES
        return MetricEnvelope(
            as_of=_utc_now(),
            tenant_id=resource.tenant_id,
            metric=detail["metric"],
            used_by=used_by[:MAX_LINEAGE_EDGES],
            business_rules=detail.get("business_rules", []),
            insights=detail.get("insights", []),
            lineage_depth=LINEAGE_DEPTH_LIMIT,
            lineage_truncated=truncated,
        )
