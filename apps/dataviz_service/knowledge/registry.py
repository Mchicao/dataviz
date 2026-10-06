"""Internal registration service for the DataVIZ knowledge plane (KP-REGISTRY-001).

Persistently registers canonical ``KnowledgeAsset``, ``SourceBinding`` and
``LineageEdge`` records over SQLite using the K1 schema
(:mod:`apps.dataviz_service.storage.knowledge_schema`) and the K1 contracts
(:mod:`core.contracts.knowledge`).

Guarantees (from the plan and the worker brief):

* **Genuinely atomic transactions**: every registration runs inside a
  ``SAVEPOINT`` (nested-transparent), so atomicity holds regardless of the
  connection's ``isolation_level``/autocommit mode, and nested or
  caller-embedded transactions compose correctly.
* **Structural tenant isolation**: every read/write is scoped by
  ``tenant_id``; a cross-tenant asset reference fails closed.
* **Logical (UUID-independent) replay**: identity is derived from typed
  fields, never from caller UUIDs. Equivalent ``SourceBinding`` payloads with
  different ``binding_id`` values resolve to the same stored binding;
  equivalent generic lineage edges with different ``edge_id`` values resolve
  to the same stored logical edge. Assets replay by natural key and accept a
  new surrogate ``asset_id`` only when all non-observational metadata
  (fingerprint, origin, kind, display fields) matches — otherwise the replay
  fails closed.
* **Derived semantic checksum**: ``register_semantic_model`` derives the
  SHA-256 of ``SemanticModel.to_json(indent=None)`` — the IR contract's
  single canonical serialization — so the registration hash and the IR
  contract hash are the same bytes by construction (P1-4); no second
  serialization recipe lives here. A caller may pass ``ir_checksum`` only to
  assert it equals the computed value; a mismatched (forged) hash fails
  closed.
* **Explicit version evolution**: ``origin_of`` predecessor edges are created
  only from a caller-supplied predecessor asset id (same tenant, same kind);
  no automatic predecessor discovery and no display-text inference. A
  successor advances from its single current predecessor; changing it
  requires an explicit ``supersedes_edge_id`` targeting the current edge, and
  correction edges never fork already-superseded history.
* **Canonical graph endpoints**: compound registration remaps edge endpoints
  to the stored canonical ``asset_id`` values, so caller-supplied UUIDs for
  replayed assets never leak into the persisted lineage graph. A
  ``supersedes_edge_id`` aimed at an earlier edge of the same batch is
  remapped to that edge's canonical stored ``edge_id`` too, and an ambiguous
  caller edge id fails closed instead of guessing (P1-7).
* **Identidad semántica multi-origen (P1-6)**: the ``semantic_model``
  natural key is the origin-neutral canonical IR checksum, so the identical
  model registered from different origins replays the SAME canonical asset
  (origin is observational for that kind; first write wins). Per-origin
  provenance stays on the distinct ``source_artifact`` assets and their
  ``derived_from`` edges; every other asset kind keeps ``origin_kind`` as
  non-observational identity metadata.
* **Identidad coherente**: every registered ``KnowledgeAsset`` must derive
  its natural key from the same evidence as its ``content_fingerprint`` (the
  key hash and the fingerprint coincide per kind); a forged or incoherent
  identity fails closed at the registry boundary even if it bypassed the
  contract validator.
* **Evidencia resoluble (P1-5)**: every NEW edge's ``evidence_ref`` must
  resolve to content the registry knows in the same tenant — ``ir:<sha256>``
  to a registered ``semantic_model`` (by fingerprint or ``semantic:{checksum}``
  natural key), a bare sha256 to a registered ``KnowledgeAsset`` whose
  ``content_fingerprint`` matches — and placeholder hashes (all-zero or
  all-``f``) are rejected outright; the registry never references an IR or
  artifact it does not know.
* **Grafo de versiones acíclico (D-KP-004)**: ``origin_of`` chains may not
  form cycles; a successor that would re-reach one of its own ancestors
  fails closed instead of persisting a version loop.
* **Conector consistente**: a ``SourceBinding``'s ``connector_id`` must match
  the ``{connector_id}:`` prefix of its ``connection``/``dataset`` endpoint
  natural keys; a payload that claims one connector while its endpoints
  identify another is rejected.
* **Reference-only**: formulas and semantic definitions are never copied into
  the registry; the semantic model asset references the canonical IR
  checksum only.

Out of scope (later waves): lineage traversal, semantic/metric projection,
search, insights lifecycle, API/MCP, RLS/permissions, publication.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from apps.dataviz_service.storage.knowledge_schema import apply_migrations
from core.contracts.connector import ExecutionMode, FreshnessPolicy
from core.contracts.knowledge import (
    PLACEHOLDER_EVIDENCE_HASHES,
    SCHEMA_VERSION,
    AssetKind,
    AssetState,
    KnowledgeAsset,
    LineageEdge,
    LineageEdgeKind,
    SourceBinding,
    check_asset_identity_coherence,
    check_edge_direction,
    natural_key_connector_id,
    validate_natural_key,
)
from core.contracts.provenance import OriginKind, ProvenanceRecord, sanitize_relative_path
from core.contracts.semantic_ir import SemanticModel
from core.contracts.source_ast import SourceAST

__all__ = [
    "KnowledgeRegistry",
    "RegistrationConflict",
]


class RegistrationConflict(ValueError):
    """Fail-closed rejection: replay with conflicting content/payload.

    Raised when the same natural identity is re-registered with a different
    ``content_fingerprint`` or an incompatible payload. History is never
    overwritten; the caller must decide how to supersede explicitly.
    """


def _utc_now() -> datetime:

    return datetime.now(UTC)


def _iso(value: datetime) -> str:

    return value.astimezone(UTC).isoformat()


def canonical_semantic_checksum(model: SemanticModel) -> str:
    """SHA-256 of the canonical JSON serialization defined by the IR contract.

    Delegates to ``SemanticModel.to_json(indent=None)`` — the IR contract's
    single canonical serialization (sorted keys, UTF-8) — instead of keeping a
    second ``json.dumps`` recipe here, so the registration hash and the IR
    contract hash are the same bytes by construction (P1-4). The canonical IR
    remains the sole semantic authority — this hash is only a derived pointer
    to an IR version.
    """
    return hashlib.sha256(model.to_json(indent=None).encode("utf-8")).hexdigest()


def _deterministic_uuid(*parts: str) -> str:
    """Stable UUID5 (DNS-less namespace) over ``parts`` for replay idempotence."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "\x1f".join(parts)))


_ASSET_COLUMNS = (
    "schema_version, asset_id, tenant_id, asset_kind, natural_key, display_name,"
    " description, content_fingerprint, origin_kind, first_seen_at, last_seen_at,"
    " state, created_by"
)
_ASSET_INSERT = (
    "INSERT INTO knowledge_assets (schema_version, asset_id, tenant_id, asset_kind,"
    " natural_key, display_name, description, content_fingerprint, origin_kind,"
    " first_seen_at, last_seen_at, state, created_by)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_BINDING_COLUMNS = (
    "schema_version, binding_id, tenant_id, dataset_asset_id, connection_asset_id,"
    " connector_id, capability_snapshot, credential_ref, mode, freshness_policy,"
    " created_at, created_by"
)
_EDGE_COLUMNS = (
    "schema_version, edge_id, tenant_id, from_asset_id, to_asset_id, edge_kind,"
    " observed_at, evidence_ref, supersedes_edge_id"
)


class KnowledgeRegistry:
    """Minimal internal registry over the K1 SQLite schema.

    Owns its connection (typically ``:memory:`` in tests or a service-managed
    file). Migrations are applied on construction, which requires the
    connection to be transaction-idle: migrations commit internally, so
    constructing the registry while the caller owns an open transaction fails
    closed (it would silently commit the caller's work). All methods take an
    explicit ``tenant_id``; nothing is cached between calls so state always
    reflects the database.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        # Las migraciones hacen commit interno; construirlas dentro de una
        # transacción ajena confirmaría el trabajo del caller. Fallo cerrado.
        if connection.in_transaction:
            raise ValueError(
                "cannot construct KnowledgeRegistry over a connection with an"
                " open transaction: schema migrations commit internally and"
                " would clobber the caller's transaction; migrations must run"
                " outside a caller-owned transaction (commit or roll back"
                " first)"
            )
        self._conn = connection
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._tx_depth = 0
        apply_migrations(self._conn)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Run a registration inside one atomic scope (savepoint-based).

        Atomicity holds regardless of the connection's
        ``isolation_level``/autocommit mode and composes with caller-managed
        transactions: each nesting level opens its own ``SAVEPOINT``; only
        the outermost scope opened by this registry commits (or, when the
        registry was entered inside a caller transaction, releases its
        savepoint into it without committing the caller's work).
        """
        outermost = self._tx_depth == 0
        # A transaction already open before we started belongs to the caller;
        # we must not commit (or roll back) their scope.
        pre_existing = outermost and self._conn.in_transaction
        self._conn.execute("SAVEPOINT kp_registry_tx")
        self._tx_depth += 1
        try:
            yield self._conn
        except Exception:
            self._tx_depth -= 1
            self._conn.execute("ROLLBACK TO kp_registry_tx")
            self._conn.execute("RELEASE kp_registry_tx")
            raise
        else:
            self._tx_depth -= 1
            self._conn.execute("RELEASE kp_registry_tx")
            if outermost and not pre_existing and self._conn.in_transaction:
                self._conn.commit()

    # -- Asset registration ------------------------------------------------

    def register_asset(
        self,
        asset: KnowledgeAsset,
        *,
        actor: str,
    ) -> KnowledgeAsset:
        """Idempotently persist ``asset`` by natural identity.

        Natural identity is ``(tenant_id, asset_kind, natural_key)``:

        * exact replay — same fingerprint, origin, kind and display fields —
          returns the stored row unchanged, accepting a new surrogate
          ``asset_id`` for the same natural identity;
        * any conflicting non-observational metadata (``content_fingerprint``,
          ``origin_kind``, ``display_name``, ``description``) fails closed
          with :class:`RegistrationConflict` instead of silently ignoring it;
          timestamps, state and ``created_by`` are observational (the first
          write wins).

        Returns:
            The canonical stored asset.
        """
        del actor  # replay keeps the original created_by; reserved for audit
        validate_natural_key(asset.asset_kind, asset.natural_key)
        with self._tx():
            row = self._conn.execute(
                f"SELECT {_ASSET_COLUMNS} FROM knowledge_assets"
                " WHERE tenant_id = ? AND asset_kind = ? AND natural_key = ?",
                (asset.tenant_id, asset.asset_kind.value, asset.natural_key),
            ).fetchone()
            if row is not None:
                stored = _row_to_asset(row)
                self._assert_asset_replay_compatible(stored, asset)
                return stored
            check_asset_identity_coherence(
                asset_kind=asset.asset_kind,
                natural_key=asset.natural_key,
                origin_kind=asset.origin_kind,
                content_fingerprint=asset.content_fingerprint,
                first_seen_at=asset.first_seen_at,
                last_seen_at=asset.last_seen_at,
            )
            self._conn.execute(
                _ASSET_INSERT,
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
                    _iso(asset.first_seen_at),
                    _iso(asset.last_seen_at),
                    asset.state.value,
                    asset.created_by,
                ),
            )
            stored = self._row_by_asset_id(asset.tenant_id, asset.asset_id)
        assert stored is not None
        return stored

    @staticmethod
    def _assert_asset_replay_compatible(stored: KnowledgeAsset, incoming: KnowledgeAsset) -> None:
        """Fail closed on conflicting non-observational metadata on replay."""
        if stored.content_fingerprint != incoming.content_fingerprint:
            raise RegistrationConflict(
                f"asset {incoming.natural_key!r} already registered with a"
                " different content_fingerprint"
                f" ({stored.content_fingerprint}); history is append-only"
            )
        if (
            stored.origin_kind != incoming.origin_kind
            # P1-6: the semantic_model natural key is the origin-neutral
            # canonical IR checksum, so origin is observational (first write
            # wins) for that kind — a second origin that produced the
            # identical canonical model is the same semantic asset. Every
            # other kind keeps origin_kind non-observational.
            and stored.asset_kind is not AssetKind.SEMANTIC_MODEL
        ):
            raise RegistrationConflict(
                f"asset {incoming.natural_key!r} already registered with"
                f" origin {stored.origin_kind.value!r}, got"
                f" {incoming.origin_kind.value!r}"
            )
        if stored.display_name != incoming.display_name:
            raise RegistrationConflict(
                f"asset {incoming.natural_key!r} already registered with"
                f" display_name {stored.display_name!r}, got"
                f" {incoming.display_name!r}"
            )
        if stored.description != incoming.description:
            raise RegistrationConflict(
                f"asset {incoming.natural_key!r} already registered with"
                f" description {stored.description!r}, got"
                f" {incoming.description!r}"
            )

    def _row_by_asset_id(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None:
        row = self._conn.execute(
            f"SELECT {_ASSET_COLUMNS} FROM knowledge_assets WHERE tenant_id = ? AND asset_id = ?",
            (tenant_id, asset_id),
        ).fetchone()
        return None if row is None else _row_to_asset(row)

    def get_asset(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None:
        """Read one asset; structurally isolated to ``tenant_id``."""
        return self._row_by_asset_id(tenant_id, asset_id)

    # -- Source artifact helper ---------------------------------------------

    def register_source_artifact(
        self,
        *,
        provenance: ProvenanceRecord,
        tenant_id: str,
        display_name: str,
        asset_id: str | None = None,
        created_by: str,
        predecessor_asset_id: str | None = None,
    ) -> KnowledgeAsset:
        """Register a ``source_artifact`` asset from sanitized provenance.

        Requires an already-sanitized :class:`ProvenanceRecord` (path + SHA-256
        + origin); absolute paths, drive letters and UNC paths are rejected by
        the provenance contract itself before reaching storage.

        When ``predecessor_asset_id`` is supplied, an explicit ``origin_of``
        edge (successor -> predecessor, both source artifacts in this tenant)
        is created atomically with the asset. There is no automatic
        predecessor discovery: the caller must state the lineage explicitly.

        Returns the persisted (or replayed) asset.
        """
        # Re-validate the path defensively (provenance already sanitized it).
        sanitize_relative_path(provenance.artifact_path)
        origin = OriginKind(provenance.origin)
        natural_key = f"{origin.value}:{provenance.artifact_hash}"
        surrogate = asset_id or _deterministic_uuid(tenant_id, "source_artifact", natural_key)
        now = _utc_now()
        asset = KnowledgeAsset(
            asset_id=surrogate,
            tenant_id=tenant_id,
            asset_kind=AssetKind.SOURCE_ARTIFACT,
            natural_key=natural_key,
            display_name=display_name,
            description=None,
            content_fingerprint=provenance.artifact_hash,
            origin_kind=origin,
            first_seen_at=now,
            last_seen_at=now,
            created_by=created_by,
        )
        if predecessor_asset_id is None:
            return self.register_asset(asset, actor=created_by)
        stored = self.register_assets_and_edges(
            assets=[asset],
            edges=[
                self.build_origin_of_edge(
                    tenant_id=tenant_id,
                    successor_asset_id=surrogate,
                    predecessor_asset_id=predecessor_asset_id,
                    observed_at=now,
                    evidence=asset.content_fingerprint,
                )
            ],
        )
        return stored[0]

    # -- Semantic model helper ----------------------------------------------

    def register_semantic_model(
        self,
        *,
        model: SemanticModel,
        tenant_id: str,
        origin_kind: OriginKind,
        created_by: str,
        derived_from_asset_id: str | None = None,
        predecessor_asset_id: str | None = None,
        ir_checksum: str | None = None,
    ) -> KnowledgeAsset | tuple[KnowledgeAsset, LineageEdge | None]:
        """Register a canonical ``semantic_model`` asset.

        The checksum is always derived from the exact canonical
        ``SemanticModel`` serialization
        (:func:`canonical_semantic_checksum`); a caller-supplied
        ``ir_checksum`` is strictly verified against the computed value and a
        mismatch fails closed. The natural key is ``semantic:{checksum}`` so
        re-registering the same canonical IR version replays the same asset.
        No formulas or semantic definitions are copied: only the checksum
        reference plus presentation fields (name/description).

        Optional explicit lineage, created atomically with the asset:

        * ``derived_from_asset_id``: a registered ``source_artifact`` in this
          tenant (a ``derived_from`` edge);
        * ``predecessor_asset_id``: a registered ``semantic_model`` in this
          tenant (an ``origin_of`` version-evolution edge).

        Predecessors are never discovered automatically nor inferred from
        display text; the contract cannot safely support that, so the helper
        fails closed and requires the explicit id.

        Returns the asset, or ``(asset, edge)`` when lineage was requested.
        The edge (when returned) is always the canonical stored edge — on
        replay, the previously persisted edge, never a newly constructed one.
        """
        computed = canonical_semantic_checksum(model)
        if ir_checksum is not None and ir_checksum != computed:
            raise ValueError(
                "ir_checksum does not match the canonical serialization of the"
                " supplied SemanticModel; refusing to trust a caller-provided"
                f" hash (computed {computed})"
            )
        natural_key = f"semantic:{computed}"
        surrogate = _deterministic_uuid(tenant_id, "semantic_model", natural_key)
        now = _utc_now()
        asset = KnowledgeAsset(
            asset_id=surrogate,
            tenant_id=tenant_id,
            asset_kind=AssetKind.SEMANTIC_MODEL,
            natural_key=natural_key,
            display_name=model.name,
            description=model.description or None,
            content_fingerprint=computed,
            origin_kind=origin_kind,
            first_seen_at=now,
            last_seen_at=now,
            created_by=created_by,
        )
        with self._tx():
            stored_asset = self.register_asset(asset, actor=created_by)
            edge: LineageEdge | None = None
            if derived_from_asset_id is not None:
                # ``derived_from`` es genérico en el contrato (D-KP-003), así
                # que este helper hace valer su regla propia de tipo destino:
                # un semantic_model sólo deriva de un source_artifact.
                target = self.get_asset(tenant_id, derived_from_asset_id)
                if target is None or target.asset_kind is not AssetKind.SOURCE_ARTIFACT:
                    got = "missing asset" if target is None else target.asset_kind.value
                    raise ValueError(
                        "derived_from target must be a registered"
                        f" source_artifact asset in tenant {tenant_id!r}, got"
                        f" {got!r}"
                    )
                edge = self.register_lineage_edge(
                    self._derived_from_edge(
                        tenant_id=tenant_id,
                        from_asset_id=stored_asset.asset_id,
                        to_asset_id=derived_from_asset_id,
                        observed_at=now,
                        evidence=f"ir:{stored_asset.content_fingerprint}",
                    )
                )
            if predecessor_asset_id is not None:
                edge = self.register_lineage_edge(
                    self.build_origin_of_edge(
                        tenant_id=tenant_id,
                        successor_asset_id=stored_asset.asset_id,
                        predecessor_asset_id=predecessor_asset_id,
                        observed_at=now,
                        evidence=f"ir:{stored_asset.content_fingerprint}",
                    )
                )
        return (stored_asset, edge) if edge is not None else stored_asset

    # -- SourceAST convenience ----------------------------------------------

    def register_source_ast(
        self,
        ast: SourceAST,
        *,
        tenant_id: str,
        created_by: str,
        display_name: str | None = None,
    ) -> KnowledgeAsset:
        """Register a :class:`SourceAST` that carries sanitized provenance.

        Fails closed (``ValueError``) when the AST has no provenance record:
        a source artifact without a content hash cannot be identified. The
        AST tree itself is never stored here; only the artifact identity.
        """
        if ast.provenance is None:
            raise ValueError(
                "SourceAST.provenance is required to register a source artifact"
                " (sanitized path + SHA-256 + origin)"
            )
        return self.register_source_artifact(
            provenance=ast.provenance,
            tenant_id=tenant_id,
            display_name=display_name or ast.provenance.artifact_path,
            created_by=created_by,
        )

    # -- Source bindings ------------------------------------------------------

    @staticmethod
    def _binding_logical_key(
        tenant_id: str, dataset_asset_id: str, connection_asset_id: str
    ) -> str:
        """Smallest deterministic logical identity of a SourceBinding.

        The endpoint pair (tenant + dataset asset + connection asset) is the
        identity: one dataset binds to one connection at a time. All other
        fields (connector semantics, capability snapshot, credential ref,
        mode, freshness) are payload and must match exactly on replay.
        """
        return "\x1f".join(("binding", tenant_id, dataset_asset_id, connection_asset_id))

    @staticmethod
    def _assert_binding_connector_consistency(
        *,
        dataset_asset: KnowledgeAsset,
        connection_asset: KnowledgeAsset,
        binding: SourceBinding,
    ) -> None:
        """Valida que el ``connector_id`` coincida con los endpoints.

        El natural key de los endpoints ``connection``/``dataset`` lleva el
        conector como prefijo (``{connector_id}:{{identidad}}``); un binding
        que declara un conector distinto del que identifican sus propios
        endpoints es un payload incoherente y se rechaza. Los endpoints
        ``semantic_model`` no llevan conector en el natural key y se omiten.
        """
        for label, endpoint in (("connection", connection_asset), ("dataset", dataset_asset)):
            expected = natural_key_connector_id(endpoint.asset_kind, endpoint.natural_key)
            if expected is not None and binding.connector_id != expected:
                raise ValueError(
                    f"SourceBinding connector_id {binding.connector_id!r} does"
                    f" not match the {label} endpoint natural key connector"
                    f" prefix {expected!r}"
                )

    def register_source_binding(self, binding: SourceBinding) -> SourceBinding:
        """Idempotently persist a connector ``SourceBinding``.

        Logical identity is the endpoint pair
        ``(tenant_id, dataset_asset_id, connection_asset_id)`` — independent
        of the caller's ``binding_id`` — so an equivalent re-registration
        with a fresh UUID resolves to the same stored binding. Endpoint kind
        semantics are enforced: the dataset side must be a ``dataset`` or
        ``semantic_model`` asset and the connection side a ``connection``
        asset. Replay with a conflicting payload fails closed. No secret
        resolution and no raw query execution happen here — the
        ``credential_ref`` is an opaque id validated by the contract.
        """
        dataset_asset = self.get_asset(binding.tenant_id, binding.dataset_asset_id)
        connection_asset = self.get_asset(binding.tenant_id, binding.connection_asset_id)
        for label, asset in (("dataset", dataset_asset), ("connection", connection_asset)):
            if asset is None:
                raise ValueError(
                    f"binding {binding.binding_id!r} references unknown {label} asset"
                    f" in tenant {binding.tenant_id!r}"
                )
        assert dataset_asset is not None and connection_asset is not None
        if dataset_asset.asset_kind not in (AssetKind.DATASET, AssetKind.SEMANTIC_MODEL):
            raise ValueError(
                "SourceBinding dataset endpoint must be a dataset or"
                f" semantic_model asset, got {dataset_asset.asset_kind.value!r}"
            )
        if connection_asset.asset_kind is not AssetKind.CONNECTION:
            raise ValueError(
                "SourceBinding connection endpoint must be a connection asset,"
                f" got {connection_asset.asset_kind.value!r}"
            )
        self._assert_binding_connector_consistency(
            dataset_asset=dataset_asset,
            connection_asset=connection_asset,
            binding=binding,
        )
        logical_id = _deterministic_uuid(
            self._binding_logical_key(
                binding.tenant_id, binding.dataset_asset_id, binding.connection_asset_id
            )
        )
        with self._tx():
            row = self._conn.execute(
                f"SELECT {_BINDING_COLUMNS} FROM knowledge_source_bindings"
                " WHERE tenant_id = ? AND binding_id = ?",
                (binding.tenant_id, logical_id),
            ).fetchone()
            if row is not None:
                stored = _row_to_binding(row)
                _assert_replay_equal(
                    _binding_payload(stored),
                    _binding_payload(binding),
                    f"source binding {binding.binding_id!r}",
                )
                return stored
            self._conn.execute(
                "INSERT INTO knowledge_source_bindings (schema_version, binding_id,"
                " tenant_id, dataset_asset_id, connection_asset_id, connector_id,"
                " capability_snapshot, credential_ref, mode, freshness_policy,"
                " created_at, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    logical_id,
                    binding.tenant_id,
                    binding.dataset_asset_id,
                    binding.connection_asset_id,
                    binding.connector_id,
                    json.dumps(binding.capability_snapshot, sort_keys=True),
                    binding.credential_ref,
                    binding.mode.value,
                    (
                        None
                        if binding.freshness_policy is None
                        else json.dumps(
                            {"ttl_seconds": binding.freshness_policy.ttl_seconds},
                            sort_keys=True,
                        )
                    ),
                    _iso(binding.created_at),
                    binding.created_by,
                ),
            )
            stored = self._read_binding(binding.tenant_id, logical_id)
        assert stored is not None
        return stored

    def _read_binding(self, tenant_id: str, binding_id: str) -> SourceBinding | None:
        row = self._conn.execute(
            f"SELECT {_BINDING_COLUMNS} FROM knowledge_source_bindings"
            " WHERE tenant_id = ? AND binding_id = ?",
            (tenant_id, binding_id),
        ).fetchone()
        return None if row is None else _row_to_binding(row)

    # -- Lineage edges --------------------------------------------------------

    def register_lineage_edge(self, edge: LineageEdge) -> LineageEdge:
        """Append one lineage edge idempotently (logical identity).

        Logical identity is
        ``(tenant_id, edge_kind, from_asset_id, to_asset_id, supersedes_edge_id)``
        — independent of the caller's ``edge_id`` — so an equivalent
        re-registration with a fresh UUID resolves to the same stored logical
        edge (a faithful replay skips all new-edge validation below and
        returns the stored row). Both endpoints must exist in the edge's
        tenant (cross-tenant or missing endpoints fail closed) and the closed
        direction pairs per edge kind are enforced (``compiled_from``
        dashboard->semantic_model, ``binds_to`` dataset/semantic_model->
        connection, ``references`` visual->metric/column, ``origin_of``
        same-kind successor->predecessor; ``derived_from`` is generically
        open (artifact->artifact, D-KP-003) with the semantic-model helper
        enforcing its own narrower target kind; ``migrated_from`` and
        ``grounded_by`` are intentionally broad and documented as such).

        New (non-replay) edges are additionally validated:

        * ``supersedes_edge_id`` must resolve to an existing edge of the same
          tenant, same edge kind and same ``from_asset_id``, and that edge
          must still be current (not yet superseded) — forking history fails
          closed;
        * a new ``origin_of`` edge may only advance the successor from its
          single current predecessor: a different predecessor is rejected
          unless ``supersedes_edge_id`` explicitly targets the current edge.

        Replay with a conflicting payload (``evidence_ref``) fails closed.
        The table has no UPDATE/DELETE path (schema triggers), so the graph
        is append-only by construction.
        """
        with self._tx():
            from_asset = self._assert_endpoints(edge)
            to_asset = self._assert_to_endpoint(edge)
            assert from_asset is not None and to_asset is not None
            check_edge_direction(edge.edge_kind, from_asset.asset_kind, to_asset.asset_kind)
            # Identidad lógica ANTES de cualquier validación de borde nuevo:
            # un replay fiel del mismo input debe seguir siendo idempotente.
            logical_id = self._edge_logical_id(edge)
            row = self._conn.execute(
                f"SELECT {_EDGE_COLUMNS} FROM knowledge_lineage_edges"
                " WHERE tenant_id = ? AND edge_id = ?",
                (edge.tenant_id, logical_id),
            ).fetchone()
            if row is not None:
                stored = _row_to_edge(row)
                if stored.evidence_ref != edge.evidence_ref:
                    raise RegistrationConflict(
                        f"lineage edge {logical_id!r} already registered with a"
                        " different evidence_ref; history is append-only"
                    )
                return stored
            self._validate_new_edge(edge, logical_id)
            self._conn.execute(
                "INSERT INTO knowledge_lineage_edges (schema_version, edge_id,"
                " tenant_id, from_asset_id, to_asset_id, edge_kind, observed_at,"
                " evidence_ref, supersedes_edge_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SCHEMA_VERSION,
                    logical_id,
                    edge.tenant_id,
                    edge.from_asset_id,
                    edge.to_asset_id,
                    edge.edge_kind.value,
                    _iso(edge.observed_at),
                    edge.evidence_ref,
                    edge.supersedes_edge_id,
                ),
            )
            stored = self._read_edge(edge.tenant_id, logical_id)
        assert stored is not None
        return stored

    def _validate_new_edge(self, edge: LineageEdge, logical_id: str) -> None:
        """Validaciones exclusivas del camino de borde nuevo (post-replay)."""
        self._assert_evidence_resolves(edge)
        if edge.supersedes_edge_id is not None:
            self._assert_supersedes_target(edge, logical_id)
        if edge.edge_kind is LineageEdgeKind.ORIGIN_OF:
            self._assert_single_current_predecessor(edge)
            self._assert_acyclic_current_predecessors(edge)

    def _assert_supersedes_target(self, edge: LineageEdge, logical_id: str) -> None:
        """Exige que ``supersedes_edge_id`` apunte a un borde válido y vigente.

        El borde superado debe existir en el tenant del borde nuevo, compartir
        kind y ``from_asset_id``, y seguir vigente (nadie más lo superó): un
        correction edge que bifurca la historia se rechaza. La comprobación
        excluye el ``logical_id`` del borde que se registra para que un replay
        fiel nunca compita consigo mismo.
        """
        superseded = self._read_edge(edge.tenant_id, edge.supersedes_edge_id)
        if superseded is None:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} supersedes unknown edge"
                f" {edge.supersedes_edge_id!r} in tenant {edge.tenant_id!r}"
                " (cross-tenant supersedes targets are rejected)"
            )
        if superseded.edge_kind is not edge.edge_kind:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} supersedes an edge of a"
                f" different kind ({superseded.edge_kind.value!r}); corrections"
                " must stay within the same edge kind"
            )
        if superseded.from_asset_id != edge.from_asset_id:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} supersedes an edge with a"
                f" different from endpoint ({superseded.from_asset_id!r});"
                " corrections must advance the same successor asset"
            )
        fork = self._conn.execute(
            "SELECT 1 FROM knowledge_lineage_edges"
            " WHERE tenant_id = ? AND supersedes_edge_id = ? AND edge_id != ?"
            " LIMIT 1",
            (edge.tenant_id, edge.supersedes_edge_id, logical_id),
        ).fetchone()
        if fork is not None:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} forks history: superseded edge"
                f" {edge.supersedes_edge_id!r} is already superseded; only the"
                " current edge may be superseded"
            )

    def _assert_single_current_predecessor(self, edge: LineageEdge) -> None:
        """Aplica D-KP-002: un sucesor tiene un único predecesor vigente.

        Un ``origin_of`` nuevo para un activo que ya tiene un borde
        ``origin_of`` vigente desde un predecesor DISTINTO se rechaza salvo
        que ``supersedes_edge_id`` apunte explícitamente a ese borde vigente.
        "Vigente" se deriva del grafo append-only: un borde deja de estarlo
        cuando otro borde lo supera.
        """
        rows = self._conn.execute(
            f"SELECT {_EDGE_COLUMNS} FROM knowledge_lineage_edges"
            " WHERE tenant_id = ? AND edge_kind = 'origin_of' AND from_asset_id = ?"
            " AND edge_id NOT IN (SELECT supersedes_edge_id FROM"
            " knowledge_lineage_edges WHERE tenant_id = ? AND"
            " supersedes_edge_id IS NOT NULL)",
            (edge.tenant_id, edge.from_asset_id, edge.tenant_id),
        ).fetchall()
        for row in rows:
            current = _row_to_edge(row)
            if (
                current.to_asset_id != edge.to_asset_id
                and edge.supersedes_edge_id != current.edge_id
            ):
                raise ValueError(
                    f"lineage edge {edge.edge_id!r}: asset"
                    f" {edge.from_asset_id!r} already has a current origin_of"
                    f" predecessor {current.to_asset_id!r} (edge"
                    f" {current.edge_id!r}); advancing to"
                    f" {edge.to_asset_id!r} requires supersedes_edge_id"
                    " targeting that current edge"
                )

    def _assert_acyclic_current_predecessors(self, edge: LineageEdge) -> None:
        """Aplica D-KP-004: el grafo de versiones ``origin_of`` es acíclico.

        Camina la cadena de predecesores vigentes desde el destino del borde
        nuevo; si la cadena re-alcanza el predecesor propuesto (origen del
        borde nuevo), el grafo quedaría cíclico y se rechaza. Un nodo con más
        de un predecesor vigente es ambiguo y también se rechaza (no se
        adivina la cadena).
        """
        visited = {edge.from_asset_id}
        current_id = edge.to_asset_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError(
                    f"lineage edge {edge.edge_id!r} would create a cycle in"
                    " the origin_of version graph"
                )
            visited.add(current_id)
            rows = self._conn.execute(
                f"SELECT {_EDGE_COLUMNS} FROM knowledge_lineage_edges"
                " WHERE tenant_id = ? AND edge_kind = 'origin_of'"
                " AND from_asset_id = ?"
                " AND edge_id NOT IN (SELECT supersedes_edge_id FROM"
                " knowledge_lineage_edges WHERE tenant_id = ? AND"
                " supersedes_edge_id IS NOT NULL)",
                (edge.tenant_id, current_id, edge.tenant_id),
            ).fetchall()
            if not rows:
                break
            if len(rows) > 1:
                raise ValueError(
                    f"lineage edge {edge.edge_id!r}: asset {current_id!r} has"
                    " an ambiguous current origin_of predecessor set;"
                    " refusing to guess a version chain"
                )
            current_id = _row_to_edge(rows[0]).to_asset_id

    def _assert_evidence_resolves(self, edge: LineageEdge) -> None:
        """Exige que la evidencia del borde resuelva a contenido conocido.

        * ``ir:<sha256>`` debe resolver a un ``semantic_model`` registrado del
          mismo tenant (por ``content_fingerprint`` o por natural key
          ``semantic:{checksum}``): el registry nunca referencia un IR que no
          conoce.
        * Un hash crudo (sin prefijo ``ir:``) debe resolver a un
          ``KnowledgeAsset`` registrado del mismo tenant cuyo
          ``content_fingerprint`` coincida (típicamente el source_artifact
          que respalda la afirmación de lineage).
        * Los hashes placeholder (todo ceros / todo efes) se rechazan
          siempre: nunca fundamentan una afirmación (P1-5).
        """
        evidence = edge.evidence_ref
        checksum = evidence[3:] if evidence.startswith("ir:") else evidence
        if checksum in PLACEHOLDER_EVIDENCE_HASHES:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} carries placeholder evidence"
                f" {evidence!r}; evidence must cite a real artifact or IR"
                " checksum"
            )
        if evidence.startswith("ir:"):
            row = self._conn.execute(
                "SELECT 1 FROM knowledge_assets WHERE tenant_id = ?"
                " AND asset_kind = 'semantic_model'"
                " AND (content_fingerprint = ? OR natural_key = ?) LIMIT 1",
                (edge.tenant_id, checksum, f"semantic:{checksum}"),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"lineage edge {edge.edge_id!r} carries ir evidence"
                    f" {evidence!r} that does not resolve to a"
                    " registered semantic_model in the same tenant; the registry"
                    " cannot reference an unknown IR"
                )
            return
        row = self._conn.execute(
            "SELECT 1 FROM knowledge_assets WHERE tenant_id = ?"
            " AND content_fingerprint = ? LIMIT 1",
            (edge.tenant_id, checksum),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"lineage edge {edge.edge_id!r} carries evidence"
                f" {evidence!r} that does not resolve to a registered asset"
                " in the same tenant; lineage evidence must be grounded in a"
                " known artifact"
            )

    @staticmethod
    def _edge_logical_id(edge: LineageEdge) -> str:
        """Deterministic surrogate ``edge_id`` from the logical identity."""
        return _deterministic_uuid(
            "edge",
            edge.tenant_id,
            edge.edge_kind.value,
            edge.from_asset_id,
            edge.to_asset_id,
            edge.supersedes_edge_id or "",
        )

    def _read_edge(self, tenant_id: str, edge_id: str) -> LineageEdge | None:
        row = self._conn.execute(
            f"SELECT {_EDGE_COLUMNS} FROM knowledge_lineage_edges"
            " WHERE tenant_id = ? AND edge_id = ?",
            (tenant_id, edge_id),
        ).fetchone()
        return None if row is None else _row_to_edge(row)

    def _assert_endpoints(self, edge: LineageEdge) -> KnowledgeAsset | None:
        asset = self.get_asset(edge.tenant_id, edge.from_asset_id)
        if asset is None:
            raise ValueError(
                f"lineage edge {edge.edge_id!r}: from endpoint {edge.from_asset_id!r}"
                f" does not exist in tenant {edge.tenant_id!r} (cross-tenant"
                " or missing endpoints are rejected)"
            )
        return asset

    def _assert_to_endpoint(self, edge: LineageEdge) -> KnowledgeAsset | None:
        asset = self.get_asset(edge.tenant_id, edge.to_asset_id)
        if asset is None:
            raise ValueError(
                f"lineage edge {edge.edge_id!r}: to endpoint {edge.to_asset_id!r}"
                f" does not exist in tenant {edge.tenant_id!r} (cross-tenant"
                " or missing endpoints are rejected)"
            )
        return asset

    def _derived_from_edge(
        self,
        *,
        tenant_id: str,
        from_asset_id: str,
        to_asset_id: str,
        observed_at: datetime,
        evidence: str,
    ) -> LineageEdge:
        return LineageEdge(
            edge_id=_deterministic_uuid(
                "edge",
                tenant_id,
                LineageEdgeKind.DERIVED_FROM.value,
                from_asset_id,
                to_asset_id,
                "",
            ),
            tenant_id=tenant_id,
            from_asset_id=from_asset_id,
            to_asset_id=to_asset_id,
            edge_kind=LineageEdgeKind.DERIVED_FROM,
            observed_at=observed_at,
            evidence_ref=evidence,
        )

    def build_origin_of_edge(
        self,
        *,
        tenant_id: str,
        successor_asset_id: str,
        predecessor_asset_id: str,
        observed_at: datetime,
        evidence: str,
    ) -> LineageEdge:
        """Construct the explicit ``origin_of`` version-evolution edge.

        The edge points successor -> predecessor, both in ``tenant_id`` and
        of the same asset kind (enforced at registration by
        :func:`core.contracts.knowledge.check_edge_direction`). This helper
        never guesses a predecessor from display text or hashes; the caller
        states it explicitly — including the ``evidence`` backing the lineage
        claim, which is mandatory: a placeholder hash is never invented.
        """
        return LineageEdge(
            edge_id=_deterministic_uuid(
                "edge",
                tenant_id,
                LineageEdgeKind.ORIGIN_OF.value,
                successor_asset_id,
                predecessor_asset_id,
                "",
            ),
            tenant_id=tenant_id,
            from_asset_id=successor_asset_id,
            to_asset_id=predecessor_asset_id,
            edge_kind=LineageEdgeKind.ORIGIN_OF,
            observed_at=observed_at,
            evidence_ref=evidence,
        )

    # -- Atomic multi-record registration -------------------------------------

    def register_assets_and_edges(
        self,
        *,
        assets: list[KnowledgeAsset],
        edges: list[LineageEdge] | None = None,
    ) -> list[KnowledgeAsset]:
        """Register assets and edges atomically (single transaction).

        Used when a semantic model and its lineage must land together: a
        failed edge (e.g. missing endpoint) rolls back the asset too, so no
        partial graph remains. Semantics per record are identical to
        :meth:`register_asset` / :meth:`register_lineage_edge`.

        Edge endpoints are remapped to the canonical stored ``asset_id`` of
        the assets in this call before insertion: a caller may supply fresh
        UUIDs for assets that replay an existing natural identity, and the
        persisted graph must reference the stored ids, never the caller's
        throwaway UUIDs. A ``supersedes_edge_id`` aimed at an EARLIER edge of
        this same batch is likewise remapped to that edge's canonical stored
        ``edge_id`` (P1-7); a reference that matches no batch edge and no
        stored edge fails closed at supersedes-target validation, and a
        caller ``edge_id`` shared by input edges that resolve to different
        canonical edges is an ambiguous remap and fails closed here. Two
        input assets that resolve to the same stored id with conflicting
        logical identity (tenant/kind/natural key) are an ambiguous duplicate
        and fail closed.
        """
        edges = edges or []
        tenants = {asset.tenant_id for asset in assets} | {e.tenant_id for e in edges}
        if len(tenants) > 1:
            raise ValueError("cross-tenant registration is not allowed in one call")
        with self._tx():
            resolved_ids = self._resolve_input_asset_ids(assets)
            stored: list[KnowledgeAsset] = []
            for asset in assets:
                stored.append(self.register_asset(asset, actor=asset.created_by))
            edge_id_remap: dict[str, str] = {}
            for edge in edges:
                remapped = edge.model_copy(
                    update={
                        "from_asset_id": resolved_ids.get(edge.from_asset_id, edge.from_asset_id),
                        "to_asset_id": resolved_ids.get(edge.to_asset_id, edge.to_asset_id),
                    }
                )
                if (
                    remapped.supersedes_edge_id is not None
                    and remapped.supersedes_edge_id in edge_id_remap
                ):
                    remapped = remapped.model_copy(
                        update={"supersedes_edge_id": edge_id_remap[remapped.supersedes_edge_id]}
                    )
                canonical = self._edge_logical_id(remapped)
                prior = edge_id_remap.get(edge.edge_id)
                if prior is not None and prior != canonical:
                    raise ValueError(
                        "ambiguous supersedes remap: input edges share caller"
                        f" edge_id {edge.edge_id!r} but resolve to different"
                        " canonical edges; refusing to guess which one is"
                        " superseded"
                    )
                edge_id_remap[edge.edge_id] = canonical
                self.register_lineage_edge(remapped)
        return stored

    def _resolve_input_asset_ids(self, assets: list[KnowledgeAsset]) -> dict[str, str]:
        """Resuelve cada ``asset_id`` entrante al id canónico almacenado.

        Se ejecuta antes de insertar para que un duplicado ambiguo (dos
        activos de entrada que comparten un UUID de caller pero tienen
        identidad lógica distinta) falle cerrado con un error claro en lugar
        de un ``IntegrityError`` de clave primaria. Varias entradas que
        comparten la MISMA identidad lógica (tenant/kind/natural key) se
        consideran el mismo activo: la primera del lote fija el id canónico y
        las demás se resuelven a él (los UUID de caller nunca se persisten).
        Para activos nuevos la resolución es el propio id del caller (aún no
        almacenado).
        """
        resolved: dict[str, str] = {}
        batch_natural: dict[tuple[str, str, str], str] = {}
        claimed_identity: dict[str, tuple[str, str, str]] = {}
        for asset in assets:
            identity = (asset.tenant_id, asset.asset_kind.value, asset.natural_key)
            canonical = batch_natural.get(identity)
            if canonical is None:
                row = self._conn.execute(
                    "SELECT asset_id FROM knowledge_assets"
                    " WHERE tenant_id = ? AND asset_kind = ? AND natural_key = ?",
                    (asset.tenant_id, asset.asset_kind.value, asset.natural_key),
                ).fetchone()
                canonical = row[0] if row is not None else asset.asset_id
                batch_natural[identity] = canonical
            prior = claimed_identity.get(asset.asset_id)
            if prior is not None and prior != identity:
                raise ValueError(
                    "ambiguous duplicate registration: input assets"
                    f" {prior[2]!r} and {asset.natural_key!r} both resolve to"
                    f" stored asset {asset.asset_id!r} with conflicting logical"
                    " identity (tenant/kind/natural key)"
                )
            claimed_identity[asset.asset_id] = identity
            resolved[asset.asset_id] = canonical
        return resolved


# --- Row mappers --------------------------------------------------------------


def _row_to_asset(row: tuple) -> KnowledgeAsset:
    return KnowledgeAsset(
        schema_version=row[0],
        asset_id=row[1],
        tenant_id=row[2],
        asset_kind=AssetKind(row[3]),
        natural_key=row[4],
        display_name=row[5],
        description=row[6],
        content_fingerprint=row[7],
        origin_kind=OriginKind(row[8]),
        first_seen_at=datetime.fromisoformat(row[9]),
        last_seen_at=datetime.fromisoformat(row[10]),
        state=AssetState(row[11]),
        created_by=row[12],
    )


def _row_to_binding(row: tuple) -> SourceBinding:
    freshness_raw = row[9]
    return SourceBinding(
        schema_version=row[0],
        binding_id=row[1],
        tenant_id=row[2],
        dataset_asset_id=row[3],
        connection_asset_id=row[4],
        connector_id=row[5],
        capability_snapshot=json.loads(row[6]),
        credential_ref=row[7],
        mode=ExecutionMode(row[8]),
        freshness_policy=(
            None if freshness_raw is None else FreshnessPolicy(**json.loads(freshness_raw))
        ),
        created_at=datetime.fromisoformat(row[10]),
        created_by=row[11],
    )


def _row_to_edge(row: tuple) -> LineageEdge:
    return LineageEdge(
        schema_version=row[0],
        edge_id=row[1],
        tenant_id=row[2],
        from_asset_id=row[3],
        to_asset_id=row[4],
        edge_kind=LineageEdgeKind(row[5]),
        observed_at=datetime.fromisoformat(row[6]),
        evidence_ref=row[7],
        supersedes_edge_id=row[8],
    )


def _binding_payload(binding: SourceBinding) -> dict:
    """Non-observational SourceBinding payload for replay comparison."""
    return binding.model_dump(exclude={"schema_version", "binding_id", "created_at", "created_by"})


def _assert_replay_equal(stored: dict, incoming: dict, label: str) -> None:
    """Fail closed when the same logical id replays with a different payload.

    Compares contract dumps field-by-field (surrogate ids and timestamps
    already excluded by the caller) so an exact replay is recognized while
    any mutation of history is rejected.
    """
    differing = sorted(key for key in stored if stored[key] != incoming.get(key))
    if differing:
        raise RegistrationConflict(
            f"{label} already registered with a different payload"
            f" (differing fields: {differing}); history is append-only"
        )
