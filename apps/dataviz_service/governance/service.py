"""Governance service managing catalog, lineage, glossary, ownership, certified metrics, and audit logs.

Strictly enforces tenant boundaries and G5 acceptance prerequisites.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from apps.dataviz_service.contracts import (
    AccessDeniedError,
    RequestContext,
    ResourceNotFoundError,
    SanitizedServiceError,
)
from apps.dataviz_service.governance.models import (
    AssetOwnership,
    AssetType,
    AuditEvent,
    AuditEventAction,
    CatalogItem,
    CertificationStatus,
    CertifiedMetric,
    GlossaryTerm,
    LineageEdge,
    LineageGraph,
    OwnerType,
)

logger = logging.getLogger("dataviz_service.governance")


class G5NotAcceptedError(SanitizedServiceError):
    """Raised when G5 acceptance verification fails."""

    def __init__(
        self, message: str = "Gate 5 (G5) acceptance is required before governance operations."
    ) -> None:
        super().__init__(message, status_code=412, error_code="G5_NOT_ACCEPTED")


def verify_g5_acceptance(g5_path: str | Path = "Docs/swarm/nightly/g5_acceptance.json") -> bool:
    """Verify if Gate 5 (G5) acceptance decision is accepted.

    Args:
        g5_path: Path to g5_acceptance.json file.

    Returns:
        True if G5 acceptance file exists and has status == "accepted", False otherwise.
    """
    path = Path(g5_path)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("status") == "accepted"
    except Exception as err:
        logger.warning(f"Failed to read G5 acceptance file at {g5_path}: {err}")
        return False


class GovernanceService:
    """Tenant-scoped data governance service for catalog, lineage, glossary, ownership, and audit."""

    def __init__(
        self,
        g5_acceptance_path: str | Path = "Docs/swarm/nightly/g5_acceptance.json",
        require_g5: bool = True,
    ) -> None:
        if require_g5 and not verify_g5_acceptance(g5_acceptance_path):
            raise G5NotAcceptedError(
                f"G5 acceptance artifact at '{g5_acceptance_path}' not found or not accepted."
            )

        # In-memory tenant stores: tenant_id -> asset_id/edge_id -> object
        self._catalog: dict[str, dict[str, CatalogItem]] = {}
        self._lineage_edges: dict[str, dict[str, LineageEdge]] = {}
        self._glossary: dict[str, dict[str, GlossaryTerm]] = {}
        self._certified_metrics: dict[str, dict[str, CertifiedMetric]] = {}
        self._audit_log: dict[str, list[AuditEvent]] = {}

    def _validate_context_tenant(self, ctx: RequestContext, target_tenant_id: str) -> None:
        """Enforce strict tenant scoping match between request context and target object."""
        if ctx.tenant_id != target_tenant_id:
            raise AccessDeniedError("Cross-tenant access or operation is prohibited.")

    def _record_audit(
        self,
        tenant_id: str,
        actor_id: str,
        action: AuditEventAction,
        target_id: str,
        target_type: str,
        details: dict[str, Any],
    ) -> AuditEvent:
        """Record an immutable audit log entry scoped to a tenant."""
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            target_id=target_id,
            target_type=target_type,
            details=details,
        )
        if tenant_id not in self._audit_log:
            self._audit_log[tenant_id] = []
        self._audit_log[tenant_id].append(event)
        return event

    # =========================================================================
    # Catalog Operations
    # =========================================================================

    def register_catalog_item(
        self, ctx: RequestContext, item: CatalogItem, actor_id: str = "system"
    ) -> CatalogItem:
        """Register a new catalog item within the context's tenant."""
        self._validate_context_tenant(ctx, item.tenant_id)
        if ctx.tenant_id not in self._catalog:
            self._catalog[ctx.tenant_id] = {}

        self._catalog[ctx.tenant_id][item.id] = item
        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.ASSET_CREATED,
            target_id=item.id,
            target_type="catalog_item",
            details={"name": item.name, "asset_type": item.asset_type.value},
        )
        return item

    def get_catalog_item(self, ctx: RequestContext, item_id: str) -> CatalogItem:
        """Retrieve a catalog item by ID scoped to the context's tenant."""
        tenant_catalog = self._catalog.get(ctx.tenant_id, {})
        item = tenant_catalog.get(item_id)
        if not item:
            raise ResourceNotFoundError("catalog_item", item_id)
        return item

    def search_catalog(
        self, ctx: RequestContext, query: str, asset_type: AssetType | None = None
    ) -> list[CatalogItem]:
        """Search catalog items strictly within the authenticated tenant boundary."""
        tenant_catalog = self._catalog.get(ctx.tenant_id, {})
        q = query.strip().lower()
        results: list[CatalogItem] = []

        for item in tenant_catalog.values():
            if asset_type and item.asset_type != asset_type:
                continue
            if not q or (
                q in item.name.lower()
                or q in item.description.lower()
                or any(q in t.lower() for t in item.tags)
            ):
                results.append(item)

        return results

    # =========================================================================
    # Lineage Operations
    # =========================================================================

    def add_lineage_edge(
        self, ctx: RequestContext, edge: LineageEdge, actor_id: str = "system"
    ) -> LineageEdge:
        """Add a directed lineage edge between two assets within the tenant."""
        self._validate_context_tenant(ctx, edge.tenant_id)

        # Validate source and target nodes exist in the tenant's catalog
        tenant_catalog = self._catalog.get(ctx.tenant_id, {})
        if edge.source_id not in tenant_catalog:
            raise ResourceNotFoundError("catalog_item (source)", edge.source_id)
        if edge.target_id not in tenant_catalog:
            raise ResourceNotFoundError("catalog_item (target)", edge.target_id)

        if ctx.tenant_id not in self._lineage_edges:
            self._lineage_edges[ctx.tenant_id] = {}

        self._lineage_edges[ctx.tenant_id][edge.id] = edge
        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.LINEAGE_LINKED,
            target_id=edge.id,
            target_type="lineage_edge",
            details={
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "relationship_type": edge.relationship_type,
            },
        )
        return edge

    def get_lineage(self, ctx: RequestContext, item_id: str) -> LineageGraph:
        """Build full lineage graph for an asset strictly within tenant boundaries."""
        root_item = self.get_catalog_item(ctx, item_id)
        tenant_catalog = self._catalog.get(ctx.tenant_id, {})
        edges = list(self._lineage_edges.get(ctx.tenant_id, {}).values())

        # Traverse upstream (sources feeding into item_id)
        upstream_ids: set[str] = set()
        queue = [item_id]
        visited: set[str] = {item_id}

        while queue:
            curr = queue.pop(0)
            for edge in edges:
                if edge.target_id == curr and edge.source_id not in visited:
                    visited.add(edge.source_id)
                    upstream_ids.add(edge.source_id)
                    queue.append(edge.source_id)

        # Traverse downstream (targets fed by item_id)
        downstream_ids: set[str] = set()
        queue = [item_id]
        visited = {item_id}

        while queue:
            curr = queue.pop(0)
            for edge in edges:
                if edge.source_id == curr and edge.target_id not in visited:
                    visited.add(edge.target_id)
                    downstream_ids.add(edge.target_id)
                    queue.append(edge.target_id)

        all_node_ids = {item_id} | upstream_ids | downstream_ids
        connected_nodes = [tenant_catalog[nid] for nid in all_node_ids if nid in tenant_catalog]
        connected_edges = [
            edge
            for edge in edges
            if edge.source_id in all_node_ids and edge.target_id in all_node_ids
        ]

        return LineageGraph(
            tenant_id=ctx.tenant_id,
            root_id=root_item.id,
            nodes=connected_nodes,
            edges=connected_edges,
            upstream_ids=list(upstream_ids),
            downstream_ids=list(downstream_ids),
        )

    # =========================================================================
    # Glossary Operations
    # =========================================================================

    def create_glossary_term(
        self, ctx: RequestContext, term: GlossaryTerm, actor_id: str = "system"
    ) -> GlossaryTerm:
        """Create a business glossary term scoped to the context's tenant."""
        self._validate_context_tenant(ctx, term.tenant_id)
        tenant_catalog = self._catalog.get(ctx.tenant_id, {})

        # Verify linked asset IDs belong to this tenant catalog
        for asset_id in term.linked_asset_ids:
            if asset_id not in tenant_catalog:
                raise ResourceNotFoundError("catalog_item", asset_id)

        if ctx.tenant_id not in self._glossary:
            self._glossary[ctx.tenant_id] = {}

        self._glossary[ctx.tenant_id][term.id] = term
        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.GLOSSARY_TERM_CREATED,
            target_id=term.id,
            target_type="glossary_term",
            details={"name": term.name},
        )
        return term

    def search_glossary(self, ctx: RequestContext, query: str) -> list[GlossaryTerm]:
        """Search glossary terms strictly within the context's tenant boundary."""
        tenant_glossary = self._glossary.get(ctx.tenant_id, {})
        q = query.strip().lower()
        results: list[GlossaryTerm] = []

        for term in tenant_glossary.values():
            if not q or (
                q in term.name.lower()
                or q in term.definition.lower()
                or any(q in syn.lower() for syn in term.synonyms)
            ):
                results.append(term)

        return results

    # =========================================================================
    # Ownership & Certified Metrics Operations
    # =========================================================================

    def assign_ownership(
        self,
        ctx: RequestContext,
        asset_id: str,
        owner_id: str,
        owner_type: OwnerType = OwnerType.USER,
        actor_id: str = "system",
    ) -> AssetOwnership:
        """Assign or update ownership of a catalog asset with audit tracking."""
        item = self.get_catalog_item(ctx, asset_id)

        updated_item = CatalogItem(
            id=item.id,
            tenant_id=item.tenant_id,
            name=item.name,
            asset_type=item.asset_type,
            description=item.description,
            owner_id=owner_id,
            tags=item.tags,
            metadata=item.metadata,
            created_at=item.created_at,
            updated_at=datetime.utcnow(),
        )
        self._catalog[ctx.tenant_id][asset_id] = updated_item

        ownership = AssetOwnership(
            asset_id=asset_id,
            tenant_id=ctx.tenant_id,
            owner_id=owner_id,
            owner_type=owner_type,
            assigned_by=actor_id,
            assigned_at=datetime.utcnow(),
        )

        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.OWNERSHIP_ASSIGNED,
            target_id=asset_id,
            target_type="catalog_item",
            details={"owner_id": owner_id, "owner_type": owner_type.value},
        )
        return ownership

    def register_certified_metric(
        self, ctx: RequestContext, metric: CertifiedMetric, actor_id: str = "system"
    ) -> CertifiedMetric:
        """Register a certified metric within the context's tenant."""
        self._validate_context_tenant(ctx, metric.tenant_id)
        if metric.catalog_item_id:
            # Validate catalog item exists in tenant
            self.get_catalog_item(ctx, metric.catalog_item_id)

        if ctx.tenant_id not in self._certified_metrics:
            self._certified_metrics[ctx.tenant_id] = {}

        self._certified_metrics[ctx.tenant_id][metric.id] = metric
        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.ASSET_CREATED,
            target_id=metric.id,
            target_type="certified_metric",
            details={"metric_name": metric.metric_name, "status": metric.status.value},
        )
        return metric

    def certify_metric(
        self,
        ctx: RequestContext,
        metric_id: str,
        status: CertificationStatus,
        actor_id: str,
        reason: str | None = None,
    ) -> CertifiedMetric:
        """Update certification status of a metric with audit tracking."""
        tenant_metrics = self._certified_metrics.get(ctx.tenant_id, {})
        metric = tenant_metrics.get(metric_id)
        if not metric:
            raise ResourceNotFoundError("certified_metric", metric_id)

        updated_metric = CertifiedMetric(
            id=metric.id,
            tenant_id=metric.tenant_id,
            metric_name=metric.metric_name,
            expression=metric.expression,
            status=status,
            certified_by=actor_id
            if status == CertificationStatus.CERTIFIED
            else metric.certified_by,
            certified_at=datetime.utcnow()
            if status == CertificationStatus.CERTIFIED
            else metric.certified_at,
            reason=reason or metric.reason,
            catalog_item_id=metric.catalog_item_id,
            created_at=metric.created_at,
            updated_at=datetime.utcnow(),
        )
        self._certified_metrics[ctx.tenant_id][metric_id] = updated_metric

        self._record_audit(
            tenant_id=ctx.tenant_id,
            actor_id=actor_id,
            action=AuditEventAction.METRIC_CERTIFIED,
            target_id=metric_id,
            target_type="certified_metric",
            details={"status": status.value, "certified_by": actor_id, "reason": reason},
        )
        return updated_metric

    def search_certified_metrics(self, ctx: RequestContext, query: str) -> list[CertifiedMetric]:
        """Search certified metrics strictly within the context's tenant boundary."""
        tenant_metrics = self._certified_metrics.get(ctx.tenant_id, {})
        q = query.strip().lower()
        results: list[CertifiedMetric] = []

        for metric in tenant_metrics.values():
            if not q or (
                q in metric.metric_name.lower()
                or q in metric.expression.lower()
                or (metric.reason and q in metric.reason.lower())
            ):
                results.append(metric)

        return results

    # =========================================================================
    # Audit Log Operations
    # =========================================================================

    def get_audit_events(
        self, ctx: RequestContext, target_id: str | None = None
    ) -> list[AuditEvent]:
        """Retrieve audit log events strictly for the context's tenant."""
        tenant_logs = self._audit_log.get(ctx.tenant_id, [])
        if target_id:
            return [e for e in tenant_logs if e.target_id == target_id]
        return list(tenant_logs)
