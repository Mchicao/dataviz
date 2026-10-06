"""Canonical SemanticModel-to-Knowledge projection helpers.

The canonical Semantic IR remains the semantic authority. This module only
registers addressable Knowledge assets and semantic/metric references so read
surfaces (including MCP) can discover metrics without copying formulas into a
second source of truth.

PostgreSQL production sync delegates to ``PostgresKnowledgeRegistry`` because
that registry resolves and verifies the durable three-IR ``versions`` row in a
single transaction. SQLite is a test/dev compatibility backend, so its sync
accepts an already validated ``SemanticModel`` and mirrors the same projection
shape over the K1 Knowledge schema.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.dataviz_service.knowledge.postgres_registry import (
    PostgresKnowledgeRegistry,
    SemanticProjectionResult,
)
from apps.dataviz_service.knowledge.registry import (
    KnowledgeRegistry,
    RegistrationConflict,
    canonical_semantic_checksum,
)
from core.contracts.knowledge import (
    SCHEMA_VERSION,
    AssetKind,
    KnowledgeAsset,
    MetricCertification,
    MetricRegistryEntry,
    SemanticEntity,
    SemanticEntityType,
)
from core.contracts.provenance import OriginKind
from core.contracts.semantic_ir import Grain, SemanticModel

__all__ = [
    "SQLiteSemanticProjectionResult",
    "register_semantic_assets",
    "sync_semantic_model",
]


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join(parts)))


def _node_checksum(payload: dict[str, Any]) -> str:
    blob = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _grain_label(grain: Grain | None) -> str | None:
    if grain is None or not grain.fields:
        return None
    value = ",".join(grain.fields)
    if len(value) > 200:
        raise ValueError("entity grain label exceeds 200 chars")
    return value


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class SQLiteSemanticProjectionResult:
    """Result of projecting one canonical SemanticModel into SQLite Knowledge."""

    ir_checksum: str
    semantic_asset: KnowledgeAsset
    tables: tuple[KnowledgeAsset, ...]
    columns: tuple[KnowledgeAsset, ...]
    metric_assets: tuple[KnowledgeAsset, ...]
    semantic_entities: tuple[SemanticEntity, ...]
    metric_entries: tuple[MetricRegistryEntry, ...]
    formula_node_paths: tuple[str, ...]
    dimension_node_paths: tuple[str, ...]


def _sqlite_semantic_entity(
    registry: KnowledgeRegistry,
    entity: SemanticEntity,
) -> SemanticEntity:
    conn = registry._conn  # noqa: SLF001 - compatibility adapter for the legacy SQLite store.
    conn.execute(
        """INSERT OR IGNORE INTO knowledge_semantic_entities(
               schema_version, entity_id, tenant_id, ir_version, node_path,
               entity_type, grain, created_at, created_by
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            SCHEMA_VERSION,
            entity.entity_id,
            entity.tenant_id,
            entity.ir_version,
            entity.node_path,
            entity.entity_type.value,
            entity.grain,
            _iso(entity.created_at),
            entity.created_by,
        ),
    )
    row = conn.execute(
        """SELECT schema_version, entity_id, tenant_id, ir_version, node_path,
                  entity_type, grain, created_at, created_by
           FROM knowledge_semantic_entities
           WHERE tenant_id = ? AND ir_version = ? AND node_path = ?""",
        (entity.tenant_id, entity.ir_version, entity.node_path),
    ).fetchone()
    assert row is not None
    stored = SemanticEntity(
        schema_version=row[0],
        entity_id=row[1],
        tenant_id=row[2],
        ir_version=row[3],
        node_path=row[4],
        entity_type=row[5],
        grain=row[6],
        created_at=datetime.fromisoformat(row[7]),
        created_by=row[8],
    )
    if stored.model_dump(exclude={"entity_id", "created_at", "created_by"}) != entity.model_dump(
        exclude={"entity_id", "created_at", "created_by"}
    ):
        raise RegistrationConflict("semantic entity replay changed payload")
    return stored


def _sqlite_metric_entry(
    registry: KnowledgeRegistry,
    metric: MetricRegistryEntry,
) -> MetricRegistryEntry:
    conn = registry._conn  # noqa: SLF001 - compatibility adapter for the legacy SQLite store.
    conn.execute(
        """INSERT OR IGNORE INTO knowledge_metric_registry(
               schema_version, metric_id, tenant_id, asset_id, ir_version,
               node_path, metric_checksum, owner, certification, certified_by,
               certified_at, grain, default_filter_context, created_at, created_by
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            SCHEMA_VERSION,
            metric.metric_id,
            metric.tenant_id,
            metric.asset_id,
            metric.ir_version,
            metric.node_path,
            metric.metric_checksum,
            metric.owner,
            metric.certification.value,
            metric.certified_by,
            None if metric.certified_at is None else _iso(metric.certified_at),
            metric.grain,
            None
            if metric.default_filter_context is None
            else json.dumps(metric.default_filter_context, sort_keys=True, separators=(",", ":")),
            _iso(metric.created_at),
            metric.created_by,
        ),
    )
    row = conn.execute(
        """SELECT schema_version, metric_id, tenant_id, asset_id, ir_version,
                  node_path, metric_checksum, owner, certification, certified_by,
                  certified_at, grain, default_filter_context, created_at, created_by
           FROM knowledge_metric_registry
           WHERE tenant_id = ? AND ir_version = ? AND node_path = ?""",
        (metric.tenant_id, metric.ir_version, metric.node_path),
    ).fetchone()
    assert row is not None
    stored = MetricRegistryEntry(
        schema_version=row[0],
        metric_id=row[1],
        tenant_id=row[2],
        asset_id=row[3],
        ir_version=row[4],
        node_path=row[5],
        metric_checksum=row[6],
        owner=row[7],
        certification=row[8],
        certified_by=row[9],
        certified_at=None if row[10] is None else datetime.fromisoformat(row[10]),
        grain=row[11],
        default_filter_context=None if row[12] is None else json.loads(row[12]),
        created_at=datetime.fromisoformat(row[13]),
        created_by=row[14],
    )
    if stored.model_dump(exclude={"metric_id", "created_at", "created_by"}) != metric.model_dump(
        exclude={"metric_id", "created_at", "created_by"}
    ):
        raise RegistrationConflict("metric registry replay changed payload")
    return stored


def _project_sqlite_semantic_model(
    registry: KnowledgeRegistry,
    *,
    model: SemanticModel,
    tenant_id: str,
    origin_kind: OriginKind,
    created_by: str,
    default_owner: str | None,
    expected_ir_version: str | None,
) -> SQLiteSemanticProjectionResult:
    checksum = canonical_semantic_checksum(model)
    if expected_ir_version is not None and expected_ir_version != checksum:
        raise ValueError("ir_version does not match the canonical SemanticModel checksum")

    owner = default_owner or created_by
    now = datetime.now(UTC)
    payload = model.to_dict()
    entity_payloads = {item["name"]: item for item in payload.get("entities", [])}
    metric_payloads = {item["name"]: item for item in payload.get("metrics", [])}

    tables: list[KnowledgeAsset] = []
    columns: list[KnowledgeAsset] = []
    metric_assets: list[KnowledgeAsset] = []
    semantic_entities: list[SemanticEntity] = []
    metric_entries: list[MetricRegistryEntry] = []
    formula_paths: list[str] = []
    dimension_paths: list[str] = []

    # Keep the compatibility projection atomic even though KnowledgeRegistry's
    # public methods each provide their own nested savepoint.
    with registry._tx():  # noqa: SLF001 - required to make the legacy SQLite projection atomic.
        semantic_asset = registry.register_semantic_model(
            model=model,
            tenant_id=tenant_id,
            origin_kind=origin_kind,
            created_by=created_by,
            ir_checksum=checksum,
        )
        if isinstance(semantic_asset, tuple):
            semantic_asset = semantic_asset[0]

        for entity in model.entities:
            entity_payload = entity_payloads[entity.name]
            field_payloads = {item["name"]: item for item in entity_payload.get("fields", [])}
            table_path = f"entities/{entity.name}"
            table_key = f"{semantic_asset.asset_id}:{table_path}"
            table = registry.register_asset(
                KnowledgeAsset(
                    asset_id=_stable_uuid(tenant_id, AssetKind.TABLE.value, table_key),
                    tenant_id=tenant_id,
                    asset_kind=AssetKind.TABLE,
                    natural_key=table_key,
                    display_name=entity.name,
                    description=entity.description or None,
                    content_fingerprint=_node_checksum(entity_payload),
                    origin_kind=origin_kind,
                    first_seen_at=now,
                    last_seen_at=now,
                    created_by=created_by,
                ),
                actor=created_by,
            )
            tables.append(table)
            semantic_entities.append(
                _sqlite_semantic_entity(
                    registry,
                    SemanticEntity(
                        entity_id=_stable_uuid(tenant_id, "semantic_entity", checksum, table_path),
                        tenant_id=tenant_id,
                        ir_version=checksum,
                        node_path=table_path,
                        entity_type=SemanticEntityType.TABLE,
                        grain=_grain_label(entity.grain),
                        created_at=now,
                        created_by=created_by,
                    ),
                )
            )

            for field in entity.fields:
                field_path = f"entities/{entity.name}/fields/{field.name}"
                field_key = f"{semantic_asset.asset_id}:{field_path}"
                column = registry.register_asset(
                    KnowledgeAsset(
                        asset_id=_stable_uuid(tenant_id, AssetKind.COLUMN.value, field_key),
                        tenant_id=tenant_id,
                        asset_kind=AssetKind.COLUMN,
                        natural_key=field_key,
                        display_name=field.name,
                        description=field.description or None,
                        content_fingerprint=_node_checksum(field_payloads[field.name]),
                        origin_kind=origin_kind,
                        first_seen_at=now,
                        last_seen_at=now,
                        created_by=created_by,
                    ),
                    actor=created_by,
                )
                columns.append(column)
                semantic_entities.append(
                    _sqlite_semantic_entity(
                        registry,
                        SemanticEntity(
                            entity_id=_stable_uuid(
                                tenant_id, "semantic_entity", checksum, field_path
                            ),
                            tenant_id=tenant_id,
                            ir_version=checksum,
                            node_path=field_path,
                            entity_type=SemanticEntityType.COLUMN,
                            created_at=now,
                            created_by=created_by,
                        ),
                    )
                )
                if field.expression is None:
                    dimension_paths.append(field_path)
                else:
                    # The expression is intentionally not copied into Knowledge.
                    # The fingerprint covers it, while node_path + ir_version
                    # resolves the authoritative formula from the versioned IR.
                    formula_paths.append(field_path)

        for relationship in model.relationships:
            relationship_path = f"relationships/{relationship.name}"
            semantic_entities.append(
                _sqlite_semantic_entity(
                    registry,
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
                )
            )

        for metric in model.metrics:
            metric_path = f"metrics/{metric.name}"
            metric_key = f"{semantic_asset.asset_id}:{metric_path}"
            metric_asset = registry.register_asset(
                KnowledgeAsset(
                    asset_id=_stable_uuid(tenant_id, AssetKind.METRIC.value, metric_key),
                    tenant_id=tenant_id,
                    asset_kind=AssetKind.METRIC,
                    natural_key=metric_key,
                    display_name=metric.name,
                    description=metric.description or None,
                    content_fingerprint=_node_checksum(metric_payloads[metric.name]),
                    origin_kind=origin_kind,
                    first_seen_at=now,
                    last_seen_at=now,
                    created_by=created_by,
                ),
                actor=created_by,
            )
            metric_assets.append(metric_asset)
            metric_entries.append(
                _sqlite_metric_entry(
                    registry,
                    MetricRegistryEntry(
                        metric_id=_stable_uuid(tenant_id, "metric", checksum, metric_path),
                        tenant_id=tenant_id,
                        asset_id=metric_asset.asset_id,
                        ir_version=checksum,
                        node_path=metric_path,
                        metric_checksum=metric_asset.content_fingerprint,
                        owner=owner,
                        certification=MetricCertification.NONE,
                        created_at=now,
                        created_by=created_by,
                    ),
                )
            )

    return SQLiteSemanticProjectionResult(
        ir_checksum=checksum,
        semantic_asset=semantic_asset,
        tables=tuple(tables),
        columns=tuple(columns),
        metric_assets=tuple(metric_assets),
        semantic_entities=tuple(semantic_entities),
        metric_entries=tuple(metric_entries),
        formula_node_paths=tuple(formula_paths),
        dimension_node_paths=tuple(dimension_paths),
    )


def register_semantic_assets(
    registry: KnowledgeRegistry | PostgresKnowledgeRegistry,
    *,
    tenant_id: str,
    origin_kind: OriginKind,
    created_by: str,
    semantic_model: SemanticModel | None = None,
    canonical_version_id: str | None = None,
    ir_version: str | None = None,
    default_owner: str | None = None,
) -> SQLiteSemanticProjectionResult | SemanticProjectionResult:
    """Project a canonical semantic version into the configured Knowledge store.

    PostgreSQL is production authority: callers provide ``canonical_version_id``
    and the registry resolves/verifies the durable SemanticModel itself. A
    detached ``semantic_model`` is rejected so it cannot become a competing
    authority. SQLite is dev/test compatibility: callers provide the validated
    ``semantic_model`` directly and may provide ``ir_version`` as a checksum
    assertion.

    Metrics are registered as ``metric`` assets plus ``metric_registry`` rows.
    Entity fields (including calculated fields/formulas) become ``column``
    assets and semantic-entity references. Formula bodies are never duplicated;
    ``ir_version`` + ``node_path`` resolves them from canonical IR (D-KP-004).
    """
    if isinstance(registry, PostgresKnowledgeRegistry):
        if canonical_version_id is None:
            raise ValueError("canonical_version_id is required for PostgreSQL Knowledge sync")
        if semantic_model is not None:
            raise ValueError(
                "PostgreSQL Knowledge sync resolves SemanticModel from the durable canonical version"
            )
        result = registry.project_canonical_version(
            tenant_id=tenant_id,
            version_id=canonical_version_id,
            origin_kind=origin_kind,
            created_by=created_by,
            default_owner=default_owner,
        )
        if ir_version is not None and result.ir_checksum != ir_version:
            raise ValueError("ir_version does not match the durable canonical version checksum")
        return result

    if isinstance(registry, KnowledgeRegistry):
        if semantic_model is None:
            raise ValueError("semantic_model is required for SQLite Knowledge sync")
        if canonical_version_id is not None:
            raise ValueError("canonical_version_id is not supported by the legacy SQLite Knowledge schema")
        return _project_sqlite_semantic_model(
            registry,
            model=semantic_model,
            tenant_id=tenant_id,
            origin_kind=origin_kind,
            created_by=created_by,
            default_owner=default_owner,
            expected_ir_version=ir_version,
        )

    raise TypeError("registry must be KnowledgeRegistry or PostgresKnowledgeRegistry")


def sync_semantic_model(
    registry: KnowledgeRegistry | PostgresKnowledgeRegistry,
    **kwargs: Any,
) -> SQLiteSemanticProjectionResult | SemanticProjectionResult:
    """Backward-friendly alias for :func:`register_semantic_assets`."""
    return register_semantic_assets(registry, **kwargs)
