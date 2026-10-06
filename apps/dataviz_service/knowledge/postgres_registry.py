"""Production PostgreSQL writer/reader for the DataVIZ Knowledge Plane.

Knowledge stores references, provenance and governance metadata only. Canonical
semantic/presentation/interaction content is always re-resolved from
``versions`` and its durable triple-IR checksum is the ``ir_version`` used by
knowledge projections.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg2.errors import UniqueViolation
from psycopg2.extras import Json

from apps.dataviz_service.governance.publication import publication_version_checksum
from apps.dataviz_service.knowledge.registry import RegistrationConflict
from apps.dataviz_service.storage.postgres import PostgresStore
from core.contracts.interaction_ir import InteractionIR
from core.contracts.knowledge import (
    PLACEHOLDER_EVIDENCE_HASHES,
    SCHEMA_VERSION,
    AssetKind,
    BusinessRule,
    InsightRecord,
    KnowledgeAsset,
    LineageEdge,
    LineageEdgeKind,
    MetricCertification,
    MetricRegistryEntry,
    SemanticEntity,
    SemanticEntityType,
    SourceBinding,
    check_asset_identity_coherence,
    check_edge_direction,
    natural_key_connector_id,
    validate_natural_key,
)
from core.contracts.presentation_ir import PresentationIR
from core.contracts.provenance import OriginKind
from core.contracts.semantic_ir import Grain, SemanticModel

__all__ = ["PostgresKnowledgeRegistry"]


def _now() -> datetime:
    return datetime.now(UTC)


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join(parts)))


@dataclass(frozen=True)
class SemanticProjectionResult:
    """Immutable outcome of :meth:`PostgresKnowledgeRegistry.project_canonical_version`."""

    canonical_version_id: str
    ir_checksum: str
    semantic_asset: KnowledgeAsset
    tables: tuple[KnowledgeAsset, ...]
    columns: tuple[KnowledgeAsset, ...]
    metric_assets: tuple[KnowledgeAsset, ...]
    semantic_entities: tuple[SemanticEntity, ...]
    metric_entries: tuple[MetricRegistryEntry, ...]


class PostgresKnowledgeRegistry:
    """Tenant-scoped production Knowledge Plane over :class:`PostgresStore`."""

    def __init__(self, store: PostgresStore) -> None:
        if not isinstance(store, PostgresStore):
            raise TypeError("PostgresKnowledgeRegistry requires PostgresStore")
        self._store = store

    @staticmethod
    def _asset(row: Mapping[str, Any]) -> KnowledgeAsset:
        return KnowledgeAsset(
            schema_version=row["schema_version"],
            asset_id=row["asset_id"],
            tenant_id=row["tenant_id"],
            asset_kind=row["asset_kind"],
            natural_key=row["natural_key"],
            display_name=row["display_name"],
            description=row["description"],
            content_fingerprint=row["content_fingerprint"],
            origin_kind=row["origin_kind"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            state=row["state"],
            created_by=row["created_by"],
        )

    @staticmethod
    def _binding(row: Mapping[str, Any]) -> SourceBinding:
        freshness = row["freshness_policy"]
        from core.contracts.connector import FreshnessPolicy

        return SourceBinding(
            schema_version=row["schema_version"],
            binding_id=row["binding_id"],
            tenant_id=row["tenant_id"],
            dataset_asset_id=row["dataset_asset_id"],
            connection_asset_id=row["connection_asset_id"],
            connector_id=row["connector_id"],
            capability_snapshot=dict(row["capability_snapshot"]),
            credential_ref=row["credential_ref"],
            mode=row["mode"],
            freshness_policy=None if freshness is None else FreshnessPolicy.from_dict(freshness),
            created_at=row["created_at"],
            created_by=row["created_by"],
        )

    @staticmethod
    def _edge(row: Mapping[str, Any]) -> LineageEdge:
        return LineageEdge(
            schema_version=row["schema_version"],
            edge_id=row["edge_id"],
            tenant_id=row["tenant_id"],
            from_asset_id=row["from_asset_id"],
            to_asset_id=row["to_asset_id"],
            edge_kind=row["edge_kind"],
            observed_at=row["observed_at"],
            evidence_ref=row["evidence_ref"],
            supersedes_edge_id=row["supersedes_edge_id"],
        )

    @staticmethod
    def _version_snapshot(
        cursor: Any, tenant_id: str, version_id: str
    ) -> tuple[Mapping[str, Any], SemanticModel, PresentationIR, InteractionIR]:
        cursor.execute(
            """SELECT id, project_id, version_number, checksum,
                      semantic_model, presentation_ir, interaction_ir
               FROM versions
               WHERE tenant_id=%s AND id=%s
               FOR SHARE""",
            (tenant_id, version_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"canonical version {version_id!r} does not exist in tenant")
        if not all(
            isinstance(row[name], Mapping)
            for name in ("semantic_model", "presentation_ir", "interaction_ir")
        ):
            raise ValueError("canonical version does not contain a complete three-IR snapshot")
        semantic = SemanticModel.from_dict(row["semantic_model"])
        presentation = PresentationIR.from_dict(row["presentation_ir"])
        interaction = InteractionIR.from_dict(row["interaction_ir"])
        if presentation.doc_id != interaction.doc_id:
            raise ValueError("canonical version presentation/interaction document identity mismatch")
        checksum = publication_version_checksum(
            semantic.to_dict(), presentation.to_dict(), interaction.to_dict()
        )
        if row["checksum"] != checksum:
            raise ValueError("canonical version checksum does not match its durable three-IR content")
        return row, semantic, presentation, interaction

    @classmethod
    def _assert_projection_version(
        cls, cursor: Any, tenant_id: str, canonical_version_id: str, ir_version: str
    ) -> tuple[Mapping[str, Any], SemanticModel]:
        row, semantic, _, _ = cls._version_snapshot(cursor, tenant_id, canonical_version_id)
        if row["checksum"] != ir_version:
            raise ValueError(
                "knowledge ir_version must equal the durable checksum of canonical_version_id"
            )
        return row, semantic

    @staticmethod
    def _semantic_node(semantic: SemanticModel, node_path: str, *, allowed: set[str]) -> Any:
        """Resolve the small documented Knowledge projection path grammar."""

        parts = node_path.split("/")
        if parts[0] == "metrics" and len(parts) == 2 and "metric" in allowed:
            return next((metric for metric in semantic.metrics if metric.name == parts[1]), None)
        if parts[0] == "relationships" and len(parts) == 2 and "relationship" in allowed:
            return next((rel for rel in semantic.relationships if rel.name == parts[1]), None)
        if parts[0] == "filters" and len(parts) == 2 and "filter" in allowed:
            return next((flt for flt in semantic.filters if flt.name == parts[1]), None)
        if parts[0] == "rls_intents" and len(parts) == 2 and "rls_intent" in allowed:
            return next((rls for rls in semantic.rls_intents if rls.name == parts[1]), None)
        if parts[0] == "entities" and len(parts) == 2 and "entity" in allowed:
            return next((entity for entity in semantic.entities if entity.name == parts[1]), None)
        if (
            parts[0] == "entities"
            and len(parts) == 4
            and parts[2] == "fields"
            and "field" in allowed
        ):
            entity = next((item for item in semantic.entities if item.name == parts[1]), None)
            if entity is None:
                return None
            return next((field for field in entity.fields if field.name == parts[3]), None)
        return None

    @staticmethod
    def _node_checksum(payload: Mapping[str, Any]) -> str:
        """Deterministic canonical SHA-256 of one Semantic IR node payload."""
        blob = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def _metric_checksum(semantic: SemanticModel, metric_name: str) -> str:
        metric_payload = next(
            (metric for metric in semantic.to_dict().get("metrics", []) if metric.get("name") == metric_name),
            None,
        )
        if metric_payload is None:
            raise ValueError(f"metric {metric_name!r} does not exist in canonical SemanticModel")
        return PostgresKnowledgeRegistry._node_checksum(metric_payload)

    def get_asset(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None:
        with self._store.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM knowledge_assets WHERE tenant_id=%s AND asset_id=%s",
                (tenant_id, asset_id),
            )
            row = cursor.fetchone()
        return None if row is None else self._asset(row)

    def get_asset_version_ref(self, tenant_id: str, asset_id: str) -> str | None:
        with self._store.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT canonical_version_id FROM knowledge_assets WHERE tenant_id=%s AND asset_id=%s",
                (tenant_id, asset_id),
            )
            row = cursor.fetchone()
        return None if row is None else row["canonical_version_id"]

    def list_assets(
        self,
        tenant_id: str,
        *,
        asset_kind: AssetKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[KnowledgeAsset], bool]:
        """Bounded tenant-scoped asset listing ordered by ``asset_id``.

        Soft-deleted assets are excluded. ``limit`` may not exceed 100 and
        ``offset`` must be non-negative; ``has_more`` reports whether another
        page exists after the returned one.
        """
        if limit < 1 or limit > 100:
            raise ValueError("asset list limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("asset list offset must be non-negative")
        kind_clause = ""
        params: list[Any] = [tenant_id]
        if asset_kind is not None:
            kind_clause = " AND asset_kind=%s"
            params.append(asset_kind.value)
        params.extend([limit + 1, offset])
        with self._store.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM knowledge_assets"
                " WHERE tenant_id=%s AND state<>'deleted'"
                + kind_clause
                + " ORDER BY asset_id LIMIT %s OFFSET %s",
                params,
            )
            rows = cursor.fetchall()
        has_more = len(rows) > limit
        return [self._asset(row) for row in rows[:limit]], has_more

    @staticmethod
    def _assert_asset_replay(
        stored: KnowledgeAsset,
        incoming: KnowledgeAsset,
        *,
        stored_version_id: str | None,
        incoming_version_id: str | None,
    ) -> None:
        if stored.content_fingerprint != incoming.content_fingerprint:
            raise RegistrationConflict("asset replay changed content_fingerprint")
        if stored.asset_kind is not AssetKind.SEMANTIC_MODEL and stored.origin_kind != incoming.origin_kind:
            raise RegistrationConflict("asset replay changed origin_kind")
        if stored.display_name != incoming.display_name or stored.description != incoming.description:
            raise RegistrationConflict("asset replay changed immutable descriptive identity")
        if stored_version_id != incoming_version_id:
            raise RegistrationConflict("asset replay changed canonical_version_id")

    def _register_asset_in_tx(
        self,
        cursor: Any,
        asset: KnowledgeAsset,
        *,
        canonical_version_id: str | None,
    ) -> KnowledgeAsset:
        validate_natural_key(asset.asset_kind, asset.natural_key)
        check_asset_identity_coherence(
            asset_kind=asset.asset_kind,
            natural_key=asset.natural_key,
            origin_kind=asset.origin_kind,
            content_fingerprint=asset.content_fingerprint,
            first_seen_at=asset.first_seen_at,
            last_seen_at=asset.last_seen_at,
        )
        if asset.asset_kind is AssetKind.SEMANTIC_MODEL:
            if canonical_version_id is None:
                raise ValueError("production semantic_model knowledge assets require canonical_version_id")
            version, _, _, _ = self._version_snapshot(cursor, asset.tenant_id, canonical_version_id)
            if version["checksum"] != asset.content_fingerprint:
                raise ValueError("semantic_model asset fingerprint must equal canonical triple-IR checksum")
        elif canonical_version_id is not None:
            self._version_snapshot(cursor, asset.tenant_id, canonical_version_id)

        cursor.execute(
            """SELECT * FROM knowledge_assets
               WHERE tenant_id=%s AND asset_kind=%s AND natural_key=%s
               FOR UPDATE""",
            (asset.tenant_id, asset.asset_kind.value, asset.natural_key),
        )
        row = cursor.fetchone()
        if row is not None:
            stored = self._asset(row)
            self._assert_asset_replay(
                stored,
                asset,
                stored_version_id=row["canonical_version_id"],
                incoming_version_id=canonical_version_id,
            )
            return stored
        try:
            cursor.execute(
                """INSERT INTO knowledge_assets(
                       schema_version, asset_id, tenant_id, asset_kind, natural_key,
                       display_name, description, content_fingerprint, origin_kind,
                       canonical_version_id, first_seen_at, last_seen_at, state, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    SCHEMA_VERSION,
                    asset.asset_id,
                    asset.tenant_id,
                    asset.asset_kind.value,
                    asset.natural_key,
                    asset.display_name,
                    asset.description,
                    asset.content_fingerprint,
                    asset.origin_kind.value,
                    canonical_version_id,
                    asset.first_seen_at,
                    asset.last_seen_at,
                    asset.state.value,
                    asset.created_by,
                ),
            )
        except UniqueViolation as exc:
            raise RegistrationConflict("knowledge asset identity collision") from exc
        return self._asset(cursor.fetchone())

    def register_asset(
        self,
        asset: KnowledgeAsset,
        *,
        actor: str,
        canonical_version_id: str | None = None,
    ) -> KnowledgeAsset:
        if actor != asset.created_by:
            raise ValueError("actor must match KnowledgeAsset.created_by")
        with self._store.transaction(asset.tenant_id) as cursor:
            return self._register_asset_in_tx(
                cursor, asset, canonical_version_id=canonical_version_id
            )

    def _register_canonical_version_in_tx(
        self,
        cursor: Any,
        *,
        tenant_id: str,
        version_id: str,
        origin_kind: OriginKind,
        created_by: str,
    ) -> KnowledgeAsset:
        row, semantic, _, _ = self._version_snapshot(cursor, tenant_id, version_id)
        checksum = row["checksum"]
        asset = KnowledgeAsset(
            asset_id=_stable_uuid(tenant_id, "semantic_model", checksum),
            tenant_id=tenant_id,
            asset_kind=AssetKind.SEMANTIC_MODEL,
            natural_key=f"semantic:{checksum}",
            display_name=semantic.name,
            description=semantic.description or None,
            content_fingerprint=checksum,
            origin_kind=origin_kind,
            first_seen_at=_now(),
            last_seen_at=_now(),
            created_by=created_by,
        )
        return self._register_asset_in_tx(cursor, asset, canonical_version_id=version_id)

    def register_canonical_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
        origin_kind: OriginKind,
        created_by: str,
    ) -> KnowledgeAsset:
        """Register one durable three-IR version as a semantic knowledge asset."""

        with self._store.transaction(tenant_id) as cursor:
            return self._register_canonical_version_in_tx(
                cursor,
                tenant_id=tenant_id,
                version_id=version_id,
                origin_kind=origin_kind,
                created_by=created_by,
            )

    @staticmethod
    def _assert_binding_connector(
        dataset: KnowledgeAsset, connection: KnowledgeAsset, binding: SourceBinding
    ) -> None:
        if dataset.asset_kind not in (AssetKind.DATASET, AssetKind.SEMANTIC_MODEL):
            raise ValueError("binding dataset endpoint must be dataset or semantic_model")
        if connection.asset_kind is not AssetKind.CONNECTION:
            raise ValueError("binding connection endpoint must be connection")
        for endpoint in (dataset, connection):
            expected = natural_key_connector_id(endpoint.asset_kind, endpoint.natural_key)
            if expected is not None and expected != binding.connector_id:
                raise ValueError("binding connector_id does not match endpoint natural key")

    def register_source_binding(self, binding: SourceBinding) -> SourceBinding:
        logical_id = _stable_uuid(
            "binding",
            binding.tenant_id,
            binding.dataset_asset_id,
            binding.connection_asset_id,
        )
        with self._store.transaction(binding.tenant_id) as cursor:
            cursor.execute(
                """SELECT * FROM knowledge_assets
                   WHERE tenant_id=%s AND asset_id IN (%s,%s)
                   ORDER BY asset_id FOR SHARE""",
                (binding.tenant_id, binding.dataset_asset_id, binding.connection_asset_id),
            )
            rows = {row["asset_id"]: row for row in cursor.fetchall()}
            if binding.dataset_asset_id not in rows or binding.connection_asset_id not in rows:
                raise ValueError("source binding endpoint missing or cross-tenant")
            dataset = self._asset(rows[binding.dataset_asset_id])
            connection = self._asset(rows[binding.connection_asset_id])
            self._assert_binding_connector(dataset, connection, binding)
            cursor.execute(
                """SELECT * FROM knowledge_source_bindings
                   WHERE tenant_id=%s AND dataset_asset_id=%s AND connection_asset_id=%s
                   FOR UPDATE""",
                (binding.tenant_id, binding.dataset_asset_id, binding.connection_asset_id),
            )
            row = cursor.fetchone()
            if row is not None:
                stored = self._binding(row)
                if stored.model_dump(
                    exclude={"schema_version", "binding_id", "created_at", "created_by"}
                ) != binding.model_dump(
                    exclude={"schema_version", "binding_id", "created_at", "created_by"}
                ):
                    raise RegistrationConflict("source binding replay changed durable payload")
                return stored
            cursor.execute(
                """INSERT INTO knowledge_source_bindings(
                       schema_version, binding_id, tenant_id, dataset_asset_id,
                       connection_asset_id, connector_id, capability_snapshot,
                       credential_ref, mode, freshness_policy, created_at, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    SCHEMA_VERSION,
                    logical_id,
                    binding.tenant_id,
                    binding.dataset_asset_id,
                    binding.connection_asset_id,
                    binding.connector_id,
                    Json(binding.capability_snapshot),
                    binding.credential_ref,
                    binding.mode.value,
                    None
                    if binding.freshness_policy is None
                    else Json(binding.freshness_policy.to_dict()),
                    binding.created_at,
                    binding.created_by,
                ),
            )
            return self._binding(cursor.fetchone())

    @staticmethod
    def _edge_logical_id(edge: LineageEdge) -> str:
        return _stable_uuid(
            "edge",
            edge.tenant_id,
            edge.edge_kind.value,
            edge.from_asset_id,
            edge.to_asset_id,
            edge.supersedes_edge_id or "",
        )

    @staticmethod
    def _assert_evidence(cursor: Any, edge: LineageEdge) -> None:
        checksum = edge.evidence_ref[3:] if edge.evidence_ref.startswith("ir:") else edge.evidence_ref
        if checksum in PLACEHOLDER_EVIDENCE_HASHES:
            raise ValueError("placeholder lineage evidence is forbidden")
        if edge.evidence_ref.startswith("ir:"):
            cursor.execute(
                """SELECT 1 FROM knowledge_assets
                   WHERE tenant_id=%s AND asset_kind='semantic_model'
                     AND content_fingerprint=%s LIMIT 1""",
                (edge.tenant_id, checksum),
            )
        else:
            cursor.execute(
                """SELECT 1 FROM knowledge_assets
                   WHERE tenant_id=%s AND content_fingerprint=%s LIMIT 1""",
                (edge.tenant_id, checksum),
            )
        if cursor.fetchone() is None:
            raise ValueError("lineage evidence does not resolve in the same tenant")

    @classmethod
    def _assert_origin_chain(cls, cursor: Any, edge: LineageEdge) -> None:
        cursor.execute(
            """SELECT e.* FROM knowledge_lineage_edges e
               WHERE e.tenant_id=%s AND e.edge_kind='origin_of' AND e.from_asset_id=%s
                 AND NOT EXISTS (
                     SELECT 1 FROM knowledge_lineage_edges s
                     WHERE s.tenant_id=e.tenant_id AND s.supersedes_edge_id=e.edge_id
                 )""",
            (edge.tenant_id, edge.from_asset_id),
        )
        current = cursor.fetchall()
        for row in current:
            if row["to_asset_id"] != edge.to_asset_id and edge.supersedes_edge_id != row["edge_id"]:
                raise ValueError("origin_of successor already has a different current predecessor")

        visited = {edge.from_asset_id}
        current_id: str | None = edge.to_asset_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError("origin_of would create a lineage cycle")
            visited.add(current_id)
            cursor.execute(
                """SELECT e.to_asset_id FROM knowledge_lineage_edges e
                   WHERE e.tenant_id=%s AND e.edge_kind='origin_of' AND e.from_asset_id=%s
                     AND NOT EXISTS (
                         SELECT 1 FROM knowledge_lineage_edges s
                         WHERE s.tenant_id=e.tenant_id AND s.supersedes_edge_id=e.edge_id
                     )""",
                (edge.tenant_id, current_id),
            )
            rows = cursor.fetchall()
            if not rows:
                break
            if len(rows) != 1:
                raise ValueError("origin_of predecessor chain is ambiguous")
            current_id = rows[0]["to_asset_id"]

    def register_lineage_edge(self, edge: LineageEdge) -> LineageEdge:
        logical_id = self._edge_logical_id(edge)
        with self._store.transaction(edge.tenant_id) as cursor:
            cursor.execute(
                """SELECT * FROM knowledge_assets
                   WHERE tenant_id=%s AND asset_id IN (%s,%s)
                   ORDER BY asset_id FOR UPDATE""",
                (edge.tenant_id, edge.from_asset_id, edge.to_asset_id),
            )
            assets = {row["asset_id"]: self._asset(row) for row in cursor.fetchall()}
            if edge.from_asset_id not in assets or edge.to_asset_id not in assets:
                raise ValueError("lineage endpoint missing or cross-tenant")
            check_edge_direction(
                edge.edge_kind,
                assets[edge.from_asset_id].asset_kind,
                assets[edge.to_asset_id].asset_kind,
            )
            cursor.execute(
                """SELECT * FROM knowledge_lineage_edges
                   WHERE tenant_id=%s AND edge_kind=%s AND from_asset_id=%s
                     AND to_asset_id=%s AND supersedes_edge_id IS NOT DISTINCT FROM %s
                   FOR UPDATE""",
                (
                    edge.tenant_id,
                    edge.edge_kind.value,
                    edge.from_asset_id,
                    edge.to_asset_id,
                    edge.supersedes_edge_id,
                ),
            )
            replay = cursor.fetchone()
            if replay is not None:
                stored = self._edge(replay)
                if stored.evidence_ref != edge.evidence_ref:
                    raise RegistrationConflict("lineage replay changed evidence_ref")
                return stored
            self._assert_evidence(cursor, edge)
            if edge.supersedes_edge_id is not None:
                cursor.execute(
                    """SELECT * FROM knowledge_lineage_edges
                       WHERE tenant_id=%s AND edge_id=%s FOR UPDATE""",
                    (edge.tenant_id, edge.supersedes_edge_id),
                )
                prior = cursor.fetchone()
                if prior is None:
                    raise ValueError("supersedes_edge_id does not exist in tenant")
                if prior["edge_kind"] != edge.edge_kind.value or prior["from_asset_id"] != edge.from_asset_id:
                    raise ValueError("superseded edge must keep kind and successor endpoint")
                cursor.execute(
                    """SELECT 1 FROM knowledge_lineage_edges
                       WHERE tenant_id=%s AND supersedes_edge_id=%s LIMIT 1""",
                    (edge.tenant_id, edge.supersedes_edge_id),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("superseded lineage edge is no longer current")
            if edge.edge_kind is LineageEdgeKind.ORIGIN_OF:
                self._assert_origin_chain(cursor, edge)
            try:
                cursor.execute(
                    """INSERT INTO knowledge_lineage_edges(
                           schema_version, edge_id, tenant_id, from_asset_id, to_asset_id,
                           edge_kind, observed_at, evidence_ref, supersedes_edge_id
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING *""",
                    (
                        SCHEMA_VERSION,
                        logical_id,
                        edge.tenant_id,
                        edge.from_asset_id,
                        edge.to_asset_id,
                        edge.edge_kind.value,
                        edge.observed_at,
                        edge.evidence_ref,
                        edge.supersedes_edge_id,
                    ),
                )
            except UniqueViolation as exc:
                raise RegistrationConflict("lineage logical identity conflict") from exc
            return self._edge(cursor.fetchone())

    def _register_semantic_entity_in_tx(
        self,
        cursor: Any,
        entity: SemanticEntity,
        *,
        canonical_version_id: str,
    ) -> SemanticEntity:
        _, semantic = self._assert_projection_version(
            cursor, entity.tenant_id, canonical_version_id, entity.ir_version
        )
        allowed = {
            "table": {"entity"},
            "column": {"field"},
            "relationship": {"relationship"},
        }[entity.entity_type.value]
        if self._semantic_node(semantic, entity.node_path, allowed=allowed) is None:
            raise ValueError("semantic entity node_path does not resolve in canonical SemanticModel")
        cursor.execute(
            """INSERT INTO knowledge_semantic_entities(
                   schema_version, entity_id, tenant_id, canonical_version_id,
                   ir_version, node_path, entity_type, grain, created_at, created_by
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, canonical_version_id, node_path) DO NOTHING""",
            (
                SCHEMA_VERSION,
                entity.entity_id,
                entity.tenant_id,
                canonical_version_id,
                entity.ir_version,
                entity.node_path,
                entity.entity_type.value,
                entity.grain,
                entity.created_at,
                entity.created_by,
            ),
        )
        cursor.execute(
            """SELECT * FROM knowledge_semantic_entities
               WHERE tenant_id=%s AND canonical_version_id=%s AND node_path=%s""",
            (entity.tenant_id, canonical_version_id, entity.node_path),
        )
        row = cursor.fetchone()
        stored = SemanticEntity(
            schema_version=row["schema_version"],
            entity_id=row["entity_id"],
            tenant_id=row["tenant_id"],
            ir_version=row["ir_version"],
            node_path=row["node_path"],
            entity_type=row["entity_type"],
            grain=row["grain"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )
        if stored.model_dump(exclude={"entity_id", "created_at", "created_by"}) != entity.model_dump(
            exclude={"entity_id", "created_at", "created_by"}
        ):
            raise RegistrationConflict("semantic entity replay changed payload")
        return stored

    def register_semantic_entity(
        self, entity: SemanticEntity, *, canonical_version_id: str
    ) -> SemanticEntity:
        with self._store.transaction(entity.tenant_id) as cursor:
            return self._register_semantic_entity_in_tx(
                cursor, entity, canonical_version_id=canonical_version_id
            )

    def _register_metric_in_tx(
        self,
        cursor: Any,
        metric: MetricRegistryEntry,
        *,
        canonical_version_id: str,
    ) -> MetricRegistryEntry:
        _, semantic = self._assert_projection_version(
            cursor, metric.tenant_id, canonical_version_id, metric.ir_version
        )
        node = self._semantic_node(semantic, metric.node_path, allowed={"metric"})
        if node is None:
            raise ValueError("metric node_path does not resolve in canonical SemanticModel")
        metric_name = metric.node_path.split("/", 1)[1]
        authoritative_metric_checksum = self._metric_checksum(semantic, metric_name)
        if metric.metric_checksum != authoritative_metric_checksum:
            raise ValueError("metric_checksum does not match canonical metric definition")
        cursor.execute(
            "SELECT * FROM knowledge_assets WHERE tenant_id=%s AND asset_id=%s FOR SHARE",
            (metric.tenant_id, metric.asset_id),
        )
        asset = cursor.fetchone()
        if asset is None or asset["asset_kind"] != AssetKind.METRIC.value:
            raise ValueError("metric registry entry requires a metric KnowledgeAsset")
        if asset["canonical_version_id"] != canonical_version_id:
            raise ValueError("metric KnowledgeAsset must reference the same canonical version")
        if asset["content_fingerprint"] != metric.metric_checksum:
            raise ValueError("metric KnowledgeAsset fingerprint must equal metric_checksum")
        cursor.execute(
            """SELECT asset_id FROM knowledge_assets
               WHERE tenant_id=%s AND asset_kind='semantic_model' AND canonical_version_id=%s""",
            (metric.tenant_id, canonical_version_id),
        )
        semantic_asset = cursor.fetchone()
        if semantic_asset is None or not asset["natural_key"].startswith(
            f"{semantic_asset['asset_id']}:"
        ):
            raise ValueError("metric asset natural key must be rooted in canonical semantic asset")
        cursor.execute(
            """INSERT INTO knowledge_metric_registry(
                   schema_version, metric_id, tenant_id, asset_id, canonical_version_id,
                   ir_version, node_path, metric_checksum, owner, certification,
                   certified_by, certified_at, grain, default_filter_context, created_at, created_by
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, canonical_version_id, node_path) DO NOTHING""",
            (
                SCHEMA_VERSION,
                metric.metric_id,
                metric.tenant_id,
                metric.asset_id,
                canonical_version_id,
                metric.ir_version,
                metric.node_path,
                metric.metric_checksum,
                metric.owner,
                metric.certification.value,
                metric.certified_by,
                metric.certified_at,
                metric.grain,
                None if metric.default_filter_context is None else Json(metric.default_filter_context),
                metric.created_at,
                metric.created_by,
            ),
        )
        cursor.execute(
            """SELECT * FROM knowledge_metric_registry
               WHERE tenant_id=%s AND canonical_version_id=%s AND node_path=%s""",
            (metric.tenant_id, canonical_version_id, metric.node_path),
        )
        row = cursor.fetchone()
        stored = MetricRegistryEntry(
            schema_version=row["schema_version"],
            metric_id=row["metric_id"],
            tenant_id=row["tenant_id"],
            asset_id=row["asset_id"],
            ir_version=row["ir_version"],
            node_path=row["node_path"],
            metric_checksum=row["metric_checksum"],
            owner=row["owner"],
            certification=row["certification"],
            certified_by=row["certified_by"],
            certified_at=row["certified_at"],
            grain=row["grain"],
            default_filter_context=row["default_filter_context"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )
        if stored.model_dump(exclude={"metric_id", "created_at", "created_by"}) != metric.model_dump(
            exclude={"metric_id", "created_at", "created_by"}
        ):
            raise RegistrationConflict("metric registry replay changed payload")
        return stored

    def register_metric(
        self, metric: MetricRegistryEntry, *, canonical_version_id: str
    ) -> MetricRegistryEntry:
        with self._store.transaction(metric.tenant_id) as cursor:
            return self._register_metric_in_tx(
                cursor, metric, canonical_version_id=canonical_version_id
            )

    @staticmethod
    def _grain_label(grain: Grain | None) -> str | None:
        if grain is None or not grain.fields:
            return None
        label = ",".join(grain.fields)
        if len(label) > 200:
            raise ValueError("entity grain label exceeds 200 chars")
        return label

    def project_canonical_version(
        self,
        *,
        tenant_id: str,
        version_id: str,
        origin_kind: OriginKind,
        created_by: str,
        default_owner: str | None = None,
    ) -> SemanticProjectionResult:
        """Replay one canonical three-IR version into semantic Knowledge rows.

        Everything derives from the durable version row: asset ids, natural
        keys, fingerprints and checksums are recomputed from the canonical
        SemanticModel, so a caller can never inject fingerprint or natural-key
        content. The whole projection commits atomically or rolls back
        completely. Metric entries are registered without certification
        (``MetricCertification.NONE``): certification is a human decision and
        agent/system principals are structurally unable to grant it here.
        Business rules and lineage edges are never auto-projected: rules are
        human statements and lineage belongs to its own KP-LINEAGE-001 package.
        """

        owner = default_owner or created_by
        with self._store.transaction(tenant_id) as cursor:
            row, semantic, _, _ = self._version_snapshot(cursor, tenant_id, version_id)
            checksum = row["checksum"]
            semantic_asset = self._register_canonical_version_in_tx(
                cursor,
                tenant_id=tenant_id,
                version_id=version_id,
                origin_kind=origin_kind,
                created_by=created_by,
            )
            now = _now()
            payload = semantic.to_dict()
            entities_payload = {item["name"]: item for item in payload.get("entities", [])}
            metrics_payload = {item["name"]: item for item in payload.get("metrics", [])}

            tables: list[KnowledgeAsset] = []
            columns: list[KnowledgeAsset] = []
            metric_assets: list[KnowledgeAsset] = []
            semantic_entities: list[SemanticEntity] = []
            metric_entries: list[MetricRegistryEntry] = []

            for entity in semantic.entities:
                fields_payload = {
                    item["name"]: item for item in entities_payload[entity.name].get("fields", [])
                }
                table_key = f"{semantic_asset.asset_id}:entities/{entity.name}"
                stored_table = self._register_asset_in_tx(
                    cursor,
                    KnowledgeAsset(
                        asset_id=_stable_uuid(tenant_id, AssetKind.TABLE.value, table_key),
                        tenant_id=tenant_id,
                        asset_kind=AssetKind.TABLE,
                        natural_key=table_key,
                        display_name=entity.name,
                        description=entity.description or None,
                        content_fingerprint=self._node_checksum(entities_payload[entity.name]),
                        origin_kind=origin_kind,
                        first_seen_at=now,
                        last_seen_at=now,
                        created_by=created_by,
                    ),
                    canonical_version_id=version_id,
                )
                tables.append(stored_table)
                semantic_entities.append(
                    self._register_semantic_entity_in_tx(
                        cursor,
                        SemanticEntity(
                            entity_id=_stable_uuid(
                                tenant_id, "semantic_entity", checksum, f"entities/{entity.name}"
                            ),
                            tenant_id=tenant_id,
                            ir_version=checksum,
                            node_path=f"entities/{entity.name}",
                            entity_type=SemanticEntityType.TABLE,
                            grain=self._grain_label(entity.grain),
                            created_at=now,
                            created_by=created_by,
                        ),
                        canonical_version_id=version_id,
                    )
                )
                for field in entity.fields:
                    field_key = (
                        f"{semantic_asset.asset_id}:entities/{entity.name}/fields/{field.name}"
                    )
                    stored_column = self._register_asset_in_tx(
                        cursor,
                        KnowledgeAsset(
                            asset_id=_stable_uuid(tenant_id, AssetKind.COLUMN.value, field_key),
                            tenant_id=tenant_id,
                            asset_kind=AssetKind.COLUMN,
                            natural_key=field_key,
                            display_name=field.name,
                            description=field.description or None,
                            content_fingerprint=self._node_checksum(fields_payload[field.name]),
                            origin_kind=origin_kind,
                            first_seen_at=now,
                            last_seen_at=now,
                            created_by=created_by,
                        ),
                        canonical_version_id=version_id,
                    )
                    columns.append(stored_column)
                    semantic_entities.append(
                        self._register_semantic_entity_in_tx(
                            cursor,
                            SemanticEntity(
                                entity_id=_stable_uuid(
                                    tenant_id,
                                    "semantic_entity",
                                    checksum,
                                    f"entities/{entity.name}/fields/{field.name}",
                                ),
                                tenant_id=tenant_id,
                                ir_version=checksum,
                                node_path=f"entities/{entity.name}/fields/{field.name}",
                                entity_type=SemanticEntityType.COLUMN,
                                created_at=now,
                                created_by=created_by,
                            ),
                            canonical_version_id=version_id,
                        )
                    )

            for relationship in semantic.relationships:
                relationship_path = f"relationships/{relationship.name}"
                semantic_entities.append(
                    self._register_semantic_entity_in_tx(
                        cursor,
                        SemanticEntity(
                            entity_id=_stable_uuid(
                                tenant_id, "semantic_entity", checksum, relationship_path
                            ),
                            tenant_id=tenant_id,
                            ir_version=checksum,
                            node_path=relationship_path,
                            entity_type=SemanticEntityType.RELATIONSHIP,
                            created_at=now,
                            created_by=created_by,
                        ),
                        canonical_version_id=version_id,
                    )
                )

            for metric in semantic.metrics:
                metric_key = f"{semantic_asset.asset_id}:metrics/{metric.name}"
                stored_metric_asset = self._register_asset_in_tx(
                    cursor,
                    KnowledgeAsset(
                        asset_id=_stable_uuid(tenant_id, AssetKind.METRIC.value, metric_key),
                        tenant_id=tenant_id,
                        asset_kind=AssetKind.METRIC,
                        natural_key=metric_key,
                        display_name=metric.name,
                        description=metric.description or None,
                        content_fingerprint=self._node_checksum(metrics_payload[metric.name]),
                        origin_kind=origin_kind,
                        first_seen_at=now,
                        last_seen_at=now,
                        created_by=created_by,
                    ),
                    canonical_version_id=version_id,
                )
                metric_assets.append(stored_metric_asset)
                metric_entries.append(
                    self._register_metric_in_tx(
                        cursor,
                        MetricRegistryEntry(
                            metric_id=_stable_uuid(
                                tenant_id, "metric", checksum, f"metrics/{metric.name}"
                            ),
                            tenant_id=tenant_id,
                            asset_id=stored_metric_asset.asset_id,
                            ir_version=checksum,
                            node_path=f"metrics/{metric.name}",
                            metric_checksum=stored_metric_asset.content_fingerprint,
                            owner=owner,
                            certification=MetricCertification.NONE,
                            created_at=now,
                            created_by=created_by,
                        ),
                        canonical_version_id=version_id,
                    )
                )

            return SemanticProjectionResult(
                canonical_version_id=version_id,
                ir_checksum=checksum,
                semantic_asset=semantic_asset,
                tables=tuple(tables),
                columns=tuple(columns),
                metric_assets=tuple(metric_assets),
                semantic_entities=tuple(semantic_entities),
                metric_entries=tuple(metric_entries),
            )

    def register_business_rule(
        self, rule: BusinessRule, *, canonical_version_id: str
    ) -> BusinessRule:
        with self._store.transaction(rule.tenant_id) as cursor:
            _, semantic = self._assert_projection_version(
                cursor, rule.tenant_id, canonical_version_id, rule.ir_version
            )
            allowed = {"rls_intent"} if rule.references_rls else {"filter", "metric"}
            if self._semantic_node(semantic, rule.node_path, allowed=allowed) is None:
                raise ValueError("business rule node_path does not resolve in canonical SemanticModel")
            cursor.execute(
                """INSERT INTO knowledge_business_rules(
                       schema_version, rule_id, tenant_id, canonical_version_id, ir_version,
                       node_path, statement, references_rls, owner, created_at, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, canonical_version_id, node_path) DO NOTHING""",
                (
                    SCHEMA_VERSION,
                    rule.rule_id,
                    rule.tenant_id,
                    canonical_version_id,
                    rule.ir_version,
                    rule.node_path,
                    rule.statement,
                    rule.references_rls,
                    rule.owner,
                    rule.created_at,
                    rule.created_by,
                ),
            )
            cursor.execute(
                """SELECT * FROM knowledge_business_rules
                   WHERE tenant_id=%s AND canonical_version_id=%s AND node_path=%s""",
                (rule.tenant_id, canonical_version_id, rule.node_path),
            )
            row = cursor.fetchone()
            stored = BusinessRule(
                schema_version=row["schema_version"],
                rule_id=row["rule_id"],
                tenant_id=row["tenant_id"],
                ir_version=row["ir_version"],
                node_path=row["node_path"],
                statement=row["statement"],
                references_rls=row["references_rls"],
                owner=row["owner"],
                created_at=row["created_at"],
                created_by=row["created_by"],
            )
            if stored.model_dump(exclude={"rule_id", "created_at", "created_by"}) != rule.model_dump(
                exclude={"rule_id", "created_at", "created_by"}
            ):
                raise RegistrationConflict("business rule replay changed payload")
            return stored

    def register_insight(
        self, insight: InsightRecord, *, canonical_version_id: str
    ) -> InsightRecord:
        with self._store.transaction(insight.tenant_id) as cursor:
            self._assert_projection_version(
                cursor, insight.tenant_id, canonical_version_id, insight.ir_version
            )
            cursor.execute(
                """SELECT canonical_version_id FROM knowledge_metric_registry
                   WHERE tenant_id=%s AND metric_id=%s""",
                (insight.tenant_id, insight.metric_ref),
            )
            metric = cursor.fetchone()
            if metric is None:
                raise ValueError("insight metric_ref does not resolve in tenant")
            if metric["canonical_version_id"] != canonical_version_id:
                raise ValueError("insight must reference a metric occurrence from the same canonical version")
            payload = insight.model_dump(mode="json")
            cursor.execute(
                """INSERT INTO knowledge_insights(
                       schema_version, insight_id, tenant_id, statement, metric_ref, grain,
                       filter_context, time_range, value_refs, canonical_version_id, ir_version,
                       query_plan_version, evidence, certification, certified_by, certified_at,
                       as_of, created_at, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, insight_id) DO NOTHING""",
                (
                    SCHEMA_VERSION,
                    insight.insight_id,
                    insight.tenant_id,
                    insight.statement,
                    insight.metric_ref,
                    insight.grain,
                    Json(payload["filter_context"]),
                    Json(payload["time_range"]),
                    Json(payload["value_refs"]),
                    canonical_version_id,
                    insight.ir_version,
                    insight.query_plan_version,
                    Json(payload["evidence"]),
                    insight.certification.value,
                    insight.certified_by,
                    insight.certified_at,
                    insight.as_of,
                    insight.created_at,
                    insight.created_by,
                ),
            )
            cursor.execute(
                "SELECT * FROM knowledge_insights WHERE tenant_id=%s AND insight_id=%s",
                (insight.tenant_id, insight.insight_id),
            )
            row = cursor.fetchone()
            stored = InsightRecord.model_validate(
                {
                    "schema_version": row["schema_version"],
                    "insight_id": row["insight_id"],
                    "tenant_id": row["tenant_id"],
                    "statement": row["statement"],
                    "metric_ref": row["metric_ref"],
                    "grain": row["grain"],
                    "filter_context": row["filter_context"],
                    "time_range": row["time_range"],
                    "value_refs": row["value_refs"],
                    "ir_version": row["ir_version"],
                    "query_plan_version": row["query_plan_version"],
                    "evidence": row["evidence"],
                    "certification": row["certification"],
                    "certified_by": row["certified_by"],
                    "certified_at": row["certified_at"],
                    "as_of": row["as_of"],
                    "created_at": row["created_at"],
                    "created_by": row["created_by"],
                }
            )
            if stored != insight:
                raise RegistrationConflict("insight replay changed immutable payload")
            return stored

    def describe_metric(self, tenant_id: str, metric_id: str) -> dict[str, Any]:
        """Return reusable metric governance, lineage usage and prior insights."""

        with self._store.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT m.*, a.display_name, a.description, a.natural_key
                   FROM knowledge_metric_registry m
                   JOIN knowledge_assets a
                     ON a.tenant_id=m.tenant_id AND a.asset_id=m.asset_id
                   WHERE m.tenant_id=%s AND m.metric_id=%s""",
                (tenant_id, metric_id),
            )
            metric = cursor.fetchone()
            if metric is None:
                raise ValueError("metric not found in tenant")
            cursor.execute(
                """SELECT edge_id, from_asset_id, edge_kind, evidence_ref
                   FROM knowledge_lineage_edges
                   WHERE tenant_id=%s AND to_asset_id=%s
                   ORDER BY observed_at""",
                (tenant_id, metric["asset_id"]),
            )
            usages = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT insight_id, statement, grain, filter_context, time_range,
                          certification, as_of
                   FROM knowledge_insights
                   WHERE tenant_id=%s AND metric_ref=%s
                   ORDER BY as_of DESC""",
                (tenant_id, metric_id),
            )
            insights = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT rule_id, node_path, statement, references_rls, owner
                   FROM knowledge_business_rules
                   WHERE tenant_id=%s AND canonical_version_id=%s
                   ORDER BY node_path""",
                (tenant_id, metric["canonical_version_id"]),
            )
            rules = [dict(row) for row in cursor.fetchall()]
        return {
            "metric": dict(metric),
            "used_by": usages,
            "business_rules": rules,
            "insights": insights,
        }
