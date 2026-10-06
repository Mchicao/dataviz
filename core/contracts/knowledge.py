"""Versioned contracts for the DataVIZ knowledge plane (Wave K1).

Implements KP-CONTRACT-001 of ``PLAN_DATAVIZ_DATA_KNOWLEDGE_PLANE_2026-09-04.md``:
asset identity/registry, source bindings, append-only lineage edges,
semantic/metric registry metadata and grounded insight records.

Design (Ponytail): Pydantic models over the stdlib plus the already-frozen
primitives in :mod:`core.contracts.provenance` (hashes, sanitized paths,
``OriginKind``) and :mod:`core.contracts.connector` (credential-reference
semantics, execution modes). The registry stores *references* into canonical IR
versions (D-KP-004); formulas and IR data are never duplicated here. No query
execution, no graph/vector store, no services — contracts only.

Versioning policy (SemVer, additive-minor, fail-closed):

* MAJOR: removed/renamed field, breaking type or closed-enum narrowing.
* MINOR: new optional field. Readers accept older minors of the same major and
  normalize them to the current version; newer minors are rejected.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from core.contracts.connector import (
    CredentialRef,
    ExecutionMode,
    FreshnessPolicy,
)
from core.contracts.provenance import OriginKind, compute_hash

SCHEMA_VERSION = "1.0.0"
"""Current knowledge-plane contract version (SemVer)."""

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CONNECTOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# RFC 3986 userInfo (``user:pass@``) reused from the provenance contract's rule.
_CRED_RE = re.compile(r"[a-zA-Z0-9_.%+-]+:[^/@]+@")
_NODE_PATH_RE = re.compile(r"^[A-Za-z0-9_./\[\]-]{1,512}$")
_IR_KINDS = ("semantic", "presentation", "interaction", "source")
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[\s_-]?key|private[\s_-]?key)", re.IGNORECASE
)

#: Placeholder hashes that must never ground evidence (P1-5): all-zero and
#: all-``f`` SHA-256 shapes are documented stand-ins, never real artifacts.
PLACEHOLDER_EVIDENCE_HASHES = frozenset({"0" * 64, "f" * 64})


def _check_aware_datetime(value: datetime, label: str) -> datetime:
    """Reject naive datetimes before persistence; no host-timezone coercion.

    A naive datetime is ambiguous: persisting it would silently reinterpret it
    in the host (or storage) timezone. Fail closed instead.
    """
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime, got {value!r}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{label} must be timezone-aware (UTC recommended); naive datetimes are rejected"
        )
    return value


class AssetKind(StrEnum):
    """Closed vocabulary of registrable asset kinds (Section 2.1)."""

    SOURCE_ARTIFACT = "source_artifact"
    CONNECTION = "connection"
    DATASET = "dataset"
    SEMANTIC_MODEL = "semantic_model"
    DASHBOARD = "dashboard"
    REPORT_PAGE = "report_page"
    VISUAL = "visual"
    METRIC = "metric"
    COLUMN = "column"
    TABLE = "table"
    INSIGHT = "insight"


class AssetState(StrEnum):
    """Soft lifecycle state; ``deleted`` preserves audit history."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class LineageEdgeKind(StrEnum):
    """Closed vocabulary of append-only lineage edges (Section 2.3).

    Direction is expressed as *semantic flow*: ``from`` is the derived or
    referencing asset, ``to`` is the origin or referenced asset, matching the
    ``derived_from`` naming. Closed direction pairs per kind are enforced by
    :func:`allowed_edge_directions` and documented on the kinds that are
    intentionally broad.
    """

    DERIVED_FROM = "derived_from"
    COMPILED_FROM = "compiled_from"
    BINDS_TO = "binds_to"
    REFERENCES = "references"
    MIGRATED_FROM = "migrated_from"
    ORIGIN_OF = "origin_of"
    GROUNDED_BY = "grounded_by"


class MetricCertification(StrEnum):
    """Certification state for metric registry entries (Section 2.4)."""

    NONE = "none"
    IN_REVIEW = "in_review"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"


class InsightCertification(StrEnum):
    """Human-only certification state machine for insights (Section 2.5)."""

    DRAFT = "draft"
    REVIEWED = "reviewed"
    CERTIFIED = "certified"
    REJECTED = "rejected"


class SemanticEntityType(StrEnum):
    """Entities projected from Semantic IR versions."""

    TABLE = "table"
    COLUMN = "column"
    RELATIONSHIP = "relationship"


class CalendarGrain(StrEnum):
    """Calendar grain at which a claim or metric holds."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class EvidenceKind(StrEnum):
    """Kinds of evidence artifacts that may ground an insight."""

    MIGRATION_ORACLE_REPORT = "migration_oracle_report"
    RUNTIME_CAPTURE = "runtime_capture"
    DAX_TRACE = "dax_trace"


def allowed_edge_directions(kind: LineageEdgeKind) -> tuple[tuple[AssetKind, ...], ...] | None:
    """Closed ``(from_kinds, to_kinds)`` pairs for an edge kind; ``None`` = open.

    Known closed pairs (semantic flow: ``from`` derives/references, ``to`` is
    the origin/referenced asset):

    * ``compiled_from``: dashboard -> semantic_model
    * ``binds_to``: dataset or semantic_model -> connection
    * ``references``: visual -> metric or column
    * ``origin_of``: same-kind successor -> predecessor (exact kind match
      required; version evolution only)
    * ``derived_from``: intentionally open (generic artifact -> artifact
      derivation, D-KP-003 §2.3). The K1 ``register_semantic_model`` helper
      enforces its own narrower rule (semantic_model -> source_artifact) at
      the service layer; the generic contract does not invent a closed
      matrix that a later wave would have to break.
    * ``migrated_from``, ``grounded_by``: intentionally broad (cross-tool
      migration targets and insight grounding); no closed pair is defined in
      the K1 contract. Inventing rules here would reject valid future use, so
      these kinds are documented as open.
    """
    if kind is LineageEdgeKind.COMPILED_FROM:
        return ((AssetKind.DASHBOARD,), (AssetKind.SEMANTIC_MODEL,))
    if kind is LineageEdgeKind.BINDS_TO:
        return (
            (AssetKind.DATASET, AssetKind.SEMANTIC_MODEL),
            (AssetKind.CONNECTION,),
        )
    if kind is LineageEdgeKind.REFERENCES:
        return (
            (AssetKind.VISUAL,),
            (AssetKind.METRIC, AssetKind.COLUMN),
        )
    if kind is LineageEdgeKind.ORIGIN_OF:
        return None  # same-kind requirement is a pair-wise check, not a fixed pair
    if kind is LineageEdgeKind.DERIVED_FROM:
        return None  # open: generic artifact -> artifact derivation (D-KP-003 §2.3)
    return None  # migrated_from, grounded_by: intentionally broad (documented)


def check_edge_direction(kind: LineageEdgeKind, from_kind: AssetKind, to_kind: AssetKind) -> None:
    """Enforce closed lineage-kind direction pairs; fail closed on violation.

    ``origin_of`` additionally requires ``from_kind == to_kind`` (same-kind
    version evolution). Kinds documented as broad (see
    :func:`allowed_edge_directions`) raise no direction error here.
    """
    if kind is LineageEdgeKind.ORIGIN_OF:
        if from_kind is not to_kind:
            raise ValueError(
                f"origin_of requires same-kind endpoints, got {from_kind.value} -> {to_kind.value}"
            )
        return
    pairs = allowed_edge_directions(kind)
    if pairs is None:
        return
    from_kinds, to_kinds = pairs
    if from_kind not in from_kinds or to_kind not in to_kinds:
        raise ValueError(
            f"edge kind {kind.value} requires from in {[k.value for k in from_kinds]}"
            f" and to in {[k.value for k in to_kinds]}, got"
            f" {from_kind.value} -> {to_kind.value}"
        )


def _check_schema_version(value: str) -> str:
    """Validate ``value`` against the additive-minor policy."""
    if not isinstance(value, str) or not _SEMVER_RE.match(value):
        raise ValueError(f"schema_version must be SemVer MAJOR.MINOR.PATCH, got {value!r}")
    current_major, current_minor, _ = map(int, SCHEMA_VERSION.split("."))
    major, minor, _ = map(int, value.split("."))
    if major != current_major:
        raise ValueError(
            f"unsupported schema major {major} (current {SCHEMA_VERSION}); fail-closed"
        )
    if (major, minor) > (current_major, current_minor):
        raise ValueError(
            f"schema_version {value} is newer than supported {SCHEMA_VERSION}; fail-closed"
        )
    return SCHEMA_VERSION


def _check_tenant(value: str) -> str:
    if not isinstance(value, str) or not _TENANT_RE.match(value):
        raise ValueError("tenant_id is mandatory and must match '^[A-Za-z0-9_-]{1,128}$'")
    return value


def _check_uuid(value: str, label: str) -> str:
    candidate = value.lower() if isinstance(value, str) else value
    if not isinstance(value, str) or not _UUID_RE.match(candidate):
        raise ValueError(f"{label} must be a lowercase UUID string, got {value!r}")
    return candidate


def _check_hash(value: str, label: str) -> str:
    candidate = value.lower() if isinstance(value, str) else value
    if not isinstance(value, str) or not _HASH_RE.match(candidate):
        raise ValueError(f"{label} must be a 64-char lowercase SHA-256 hex, got {value!r}")
    return candidate


def _check_principal(value: str) -> str:
    """``created_by``/``certified_by``/``owner`` must be a principal or system job."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("principal must be a non-empty string (<= 200 chars)")
    if _CRED_RE.search(value):
        raise ValueError("principal must not contain embedded credentials")
    return value


def is_agent_principal(principal: str) -> bool:
    """``True`` when ``principal`` denotes an agent (cannot certify)."""
    return isinstance(principal, str) and principal.startswith("agent:")


_SECRET_ASSIGN_RE = re.compile(
    r"(?:password|passwd|secret|token|api[\s_-]?key|private[\s_-]?key|pwd|auth[\s_-]?token)"
    r"\s*[:=]\s*\S+"
    r"|authorization\s*[:=]\s*\S+"
    r"|(?:bearer|basic)\s+(?:\S*[0-9.:/+=_-]\S*|\S{40,})",
    re.IGNORECASE,
)

# Raw executable SQL leads (DML/DDL/CTE plus PRAGMA/ATTACH/DETACH/VACUUM and
# SQL comment opens). Prose that merely mentions SQL is allowed; a payload
# segment that *starts* with one of these is a query carrier.
_SQL_LEAD_RE = re.compile(
    r"^\s*(?:--|/\*|(?:select|with|insert|update|delete|merge|create|alter|drop"
    r"|truncate|pragma|attach|detach|vacuum)\b)",
    re.IGNORECASE,
)

# PEM-style private key blocks, with or without the dashed armor.
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-{2,}\s*BEGIN(?:[ ]+[A-Z0-9]+)*[ ]+PRIVATE[ ]+KEY",
    re.IGNORECASE,
)

# P1-1: single centralized credential-material scan shared by the scalar
# prose validator and the recursive JSON-payload validator.
_QUERY_CRED_RES: tuple[re.Pattern[str], ...] = (
    _CRED_RE,
    _SECRET_KEY_RE,
    _SECRET_ASSIGN_RE,
    _PRIVATE_KEY_BLOCK_RE,
)


def _carries_query_or_credentials(value: str) -> bool:
    return any(pattern.search(value) for pattern in _QUERY_CRED_RES)


def _check_sql_free(value: str, label: str) -> None:
    """Reject payload leaves that smuggle raw executable SQL or SQL comments.

    Each ``;``-separated segment that begins with a DML/DDL lead keyword, a
    ``PRAGMA``/``ATTACH``/``DETACH``/``VACUUM`` statement, or a ``--``/``/*``
    comment open is an executable query carrier, not metadata; anything else
    (plain values, mentions of SQL, bearer/oauth vocabulary without a carrier
    shape) stays allowed. Fail-closed: metadata payloads must never carry
    executable SQL.
    """
    for segment in value.split(";"):
        if _SQL_LEAD_RE.match(segment.strip()):
            raise ValueError(f"{label} must not carry raw executable SQL")


def _check_scalar_metadata(value: str, label: str) -> None:
    """Shared scalar scan (P1-1): credential material and query carriers.

    Prose stays prose: mentioning SQL vocabulary or auth words is allowed;
    carrying a credential shape, a private-key block, userinfo/``@`` or a
    segment that starts like executable SQL is not.
    """
    if _carries_query_or_credentials(value) or "@" in value:
        raise ValueError(f"{label} must not contain credentials or secret material")
    _check_sql_free(value, label)


def _check_secret_free(value: str, label: str) -> str:
    """Reject free-text fields carrying credentials or raw-query carriers."""
    if isinstance(value, str):
        _check_scalar_metadata(value, label)
    return value


def _check_secret_free_node(node: Any, label: str) -> None:
    """Recursively reject credential/query carriers in JSON-like payloads.

    Walks dicts, lists and scalar strings so nested secret-looking material
    cannot be persisted inside a capability snapshot or filter context. Keys
    matching the secret vocabulary, userinfo/``user:pass@`` shapes, secret
    assignments, private-key blocks and raw SQL carriers are all rejected at
    any depth (fail-closed).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text) or _CRED_RE.search(key_text):
                raise ValueError(f"{label} key {key_text!r} looks like a secret; rejected")
            _check_secret_free_node(value, label)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _check_secret_free_node(item, label)
    elif isinstance(node, str):
        _check_scalar_metadata(node, label)
    elif node is not None and not isinstance(node, (bool, int, float)):
        raise ValueError(f"{label} must contain only JSON-like values")


def validate_natural_key(kind: AssetKind | str, natural_key: str) -> str:
    """Validate the kind-specific stable natural key; fail-closed on unknown.

    Stable natural keys per kind (Section 2.1, D-KP-002):

    * ``source_artifact``: ``{origin_kind}:{sha256(file bytes)}``.
    * ``connection``: ``{connector_id}:{sanitized endpoint identity}`` (no
      credentials, no userinfo, no query string).
    * ``dataset``: ``{connector_id}:{dataset identity}``.
    * ``semantic_model``/``dashboard``: ``{ir_kind}:{ir checksum}``.
    * ``report_page``/``visual``/``metric``/``column``/``table``:
      ``{parent asset uuid}:{node_path}``.
    * ``insight``: ``insight:{sha256 of canonical grounding}``.
    """
    kind_value = kind.value if isinstance(kind, AssetKind) else kind
    try:
        resolved = AssetKind(kind_value)
    except ValueError as exc:
        allowed = [member.value for member in AssetKind]
        raise ValueError(
            f"unknown asset_kind {kind_value!r} (expected one of {allowed}); natural key is fail-closed"
        ) from exc

    if not isinstance(natural_key, str) or not natural_key.strip():
        raise ValueError("natural_key is mandatory")

    prefix, sep, rest = natural_key.partition(":")
    if not sep or not rest:
        raise ValueError(
            f"natural_key for {resolved.value!r} must be '{{spec}}:{{value}}', got {natural_key!r}"
        )

    if resolved is AssetKind.SOURCE_ARTIFACT:
        origins = {member.value for member in OriginKind}
        if prefix not in origins or not _HASH_RE.match(rest):
            raise ValueError(
                f"source_artifact natural key must be '{{origin_kind}}:{{sha256}}' "
                f"with origin in {sorted(origins)}, got {natural_key!r}"
            )
    elif resolved is AssetKind.CONNECTION:
        if not _CONNECTOR_ID_RE.match(prefix):
            raise ValueError(
                f"connection natural key prefix must be a connector_id, got {prefix!r}"
            )
        if (
            not rest
            or any(ch.isspace() for ch in rest)
            or _CRED_RE.search(natural_key)
            or "@" in natural_key
            or "?" in natural_key
            or _SECRET_ASSIGN_RE.search(natural_key)
        ):
            raise ValueError(
                "connection endpoint identity must be sanitized: non-empty, no "
                f"whitespace, userinfo, query string or secret assignments, got {rest!r}"
            )
    elif resolved is AssetKind.DATASET:
        # Same identity hygiene as connections: the dataset identity must not
        # smuggle userinfo, query strings or secret assignments.
        if not _CONNECTOR_ID_RE.match(prefix) or not rest or any(ch.isspace() for ch in rest):
            raise ValueError(
                f"dataset natural key must be '{{connector_id}}:{{dataset id}}', got {natural_key!r}"
            )
        if _CRED_RE.search(natural_key) or "@" in natural_key or "?" in natural_key:
            raise ValueError(
                "dataset natural key must be sanitized: no userinfo '@', no query "
                f"string, no embedded credentials, got {natural_key!r}"
            )
        if _SECRET_ASSIGN_RE.search(natural_key):
            raise ValueError(
                f"dataset natural key must not carry secret assignments, got {natural_key!r}"
            )
    elif resolved in (AssetKind.SEMANTIC_MODEL, AssetKind.DASHBOARD):
        if prefix not in _IR_KINDS or not _HASH_RE.match(rest):
            raise ValueError(
                f"{resolved.value} natural key must be '{{ir_kind}}:{{ir checksum}}' "
                f"with ir_kind in {_IR_KINDS}, got {natural_key!r}"
            )
    elif resolved in (
        AssetKind.REPORT_PAGE,
        AssetKind.VISUAL,
        AssetKind.METRIC,
        AssetKind.COLUMN,
        AssetKind.TABLE,
    ):
        if not _UUID_RE.match(prefix.lower()) or not _NODE_PATH_RE.match(rest):
            raise ValueError(
                f"{resolved.value} natural key must be '{{parent asset uuid}}:{{node_path}}', got {natural_key!r}"
            )
    elif resolved is AssetKind.INSIGHT:
        if prefix != "insight" or not _HASH_RE.match(rest):
            raise ValueError(
                f"insight natural key must be 'insight:{{sha256}}', got {natural_key!r}"
            )
    return natural_key


def check_asset_identity_coherence(
    *,
    asset_kind: AssetKind | str,
    natural_key: str,
    origin_kind: OriginKind | None = None,
    content_fingerprint: str,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> None:
    """Valida que la identidad lógica derive de la misma evidencia que el fingerprint.

    Un ``KnowledgeAsset`` es coherente cuando su natural key se computa desde
    la misma evidencia que ``content_fingerprint``:

    * ``source_artifact``: el prefijo debe coincidir con ``origin_kind`` y el
      hash del natural key con ``content_fingerprint`` (ambos derivan de los
      bytes del artefacto);
    * ``semantic_model`` / ``dashboard`` / ``insight``: el hash tras el primer
      ``:`` debe coincidir con ``content_fingerprint`` (el checksum del IR).

    Las identidades de endpoint/nodo — ``connection``, ``dataset``,
    ``report_page``, ``visual``, ``metric``, ``column``, ``table`` — describen
    el conector/path, no el contenido derivado, y se omiten. Además
    ``last_seen_at`` nunca puede anteceder a ``first_seen_at``.
    """
    kind_value = asset_kind.value if isinstance(asset_kind, AssetKind) else asset_kind
    resolved = AssetKind(kind_value)
    if first_seen_at is not None and last_seen_at is not None and last_seen_at < first_seen_at:
        raise ValueError("last_seen_at must not precede first_seen_at")
    prefix, _, rest = natural_key.partition(":")
    if resolved is AssetKind.SOURCE_ARTIFACT:
        if origin_kind is None or prefix != origin_kind.value:
            raise ValueError(
                f"source_artifact natural key prefix {prefix!r} must equal"
                f" origin_kind {origin_kind.value if origin_kind else '<unset>'!r}"
            )
        if rest != content_fingerprint:
            raise ValueError("source_artifact natural key hash must equal content_fingerprint")
    elif resolved in (AssetKind.SEMANTIC_MODEL, AssetKind.DASHBOARD, AssetKind.INSIGHT):
        if rest != content_fingerprint:
            raise ValueError(f"{resolved.value} natural key hash must equal content_fingerprint")


def natural_key_connector_id(kind: AssetKind | str, natural_key: str) -> str | None:
    """Extrae el ``connector_id`` de un natural key de endpoint.

    Sólo ``connection`` y ``dataset`` llevan el conector como prefijo
    (``{connector_id}:{{identidad}}``); el resto de kinds devuelve ``None``
    (para ``semantic_model`` el prefijo es un ir_kind, no un conector).
    """
    kind_value = kind.value if isinstance(kind, AssetKind) else kind
    if kind_value not in (AssetKind.CONNECTION.value, AssetKind.DATASET.value):
        return None
    prefix, sep, _ = natural_key.partition(":")
    return prefix if sep else None


class _KnowledgeModel(BaseModel):
    """Shared model config: frozen, fail-closed on extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class KnowledgeAsset(_KnowledgeModel):
    """Registry row for one tenant-scoped asset (Section 2.1)."""

    schema_version: str = SCHEMA_VERSION
    asset_id: str
    tenant_id: str
    asset_kind: AssetKind
    natural_key: str
    display_name: str
    description: str | None = None
    content_fingerprint: str
    origin_kind: OriginKind
    first_seen_at: datetime
    last_seen_at: datetime
    state: AssetState = AssetState.ACTIVE
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("asset_id")
    @classmethod
    def _v_asset_id(cls, value: str) -> str:
        return _check_uuid(value, "asset_id")

    @field_validator("display_name")
    @classmethod
    def _v_display_name(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("display_name must be a non-empty string (<= 512 chars)")
        return _check_secret_free(value, "display_name")

    @field_validator("description")
    @classmethod
    def _v_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError("description must be a string (<= 4000 chars)")
        return _check_secret_free(value, "description")

    @field_validator("content_fingerprint")
    @classmethod
    def _v_fingerprint(cls, value: str) -> str:
        return _check_hash(value, "content_fingerprint")

    @field_validator("first_seen_at", "last_seen_at")
    @classmethod
    def _v_datetimes(cls, value: datetime, info: Any) -> datetime:
        return _check_aware_datetime(value, str(info.field_name))

    @field_validator("created_by")
    @classmethod
    def _v_created_by(cls, value: str) -> str:
        return _check_principal(value)

    @model_validator(mode="after")
    def _v_natural_key(self) -> KnowledgeAsset:
        validate_natural_key(self.asset_kind, self.natural_key)
        check_asset_identity_coherence(
            asset_kind=self.asset_kind,
            natural_key=self.natural_key,
            origin_kind=self.origin_kind,
            content_fingerprint=self.content_fingerprint,
            first_seen_at=self.first_seen_at,
            last_seen_at=self.last_seen_at,
        )
        return self

    @classmethod
    def from_source_bytes(
        cls,
        *,
        artifact_path: str,
        data: bytes | bytearray,
        tenant_id: str,
        asset_id: str,
        display_name: str,
        origin_kind: OriginKind = OriginKind.UNKNOWN,
        created_by: str,
        seen_at: datetime,
    ) -> KnowledgeAsset:
        """Build a ``source_artifact`` asset, hashing bytes with provenance."""
        digest = compute_hash(data)
        # Reuse the provenance sanitizer to reject unsafe artifact paths.
        from core.contracts.provenance import sanitize_relative_path

        sanitize_relative_path(artifact_path)
        return cls(
            asset_id=asset_id,
            tenant_id=tenant_id,
            asset_kind=AssetKind.SOURCE_ARTIFACT,
            natural_key=f"{origin_kind.value}:{digest}",
            display_name=display_name,
            content_fingerprint=digest,
            origin_kind=origin_kind,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            created_by=created_by,
        )


class SourceBinding(_KnowledgeModel):
    """Link from a dataset/semantic_model asset to a connection asset (2.2).

    Logical identity (used for replay resolution) is the endpoint pair plus
    the connector semantics; ``binding_id`` is a surrogate key.
    """

    schema_version: str = SCHEMA_VERSION
    binding_id: str
    tenant_id: str
    dataset_asset_id: str
    connection_asset_id: str
    connector_id: str
    capability_snapshot: dict[str, Any]
    credential_ref: str | None = None
    mode: ExecutionMode
    freshness_policy: FreshnessPolicy | None = None
    created_at: datetime
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("binding_id")
    @classmethod
    def _v_binding_id(cls, value: str) -> str:
        return _check_uuid(value, "binding_id")

    @field_validator("dataset_asset_id", "connection_asset_id")
    @classmethod
    def _v_asset_ref(cls, value: str) -> str:
        return _check_uuid(value, "asset reference")

    @field_validator("created_at")
    @classmethod
    def _v_created_at(cls, value: datetime) -> datetime:
        return _check_aware_datetime(value, "created_at")

    @field_validator("created_by")
    @classmethod
    def _v_created_by(cls, value: str) -> str:
        return _check_principal(value)

    @field_validator("credential_ref")
    @classmethod
    def _v_credential_ref(cls, value: str | None) -> str | None:
        # Reuse the connector contract's opaque-reference semantics; a
        # credential-bearing string fails validation here (P2/D008).
        return None if value is None else CredentialRef(value).value

    @field_validator("capability_snapshot")
    @classmethod
    def _v_snapshot(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Fail closed, recursively: secret-looking keys at any depth and
        # credential-shaped strings must never be persisted. Capability
        # metadata describes connector abilities; it is never a credential,
        # raw SQL or query carrier.
        _check_secret_free_node(value, "capability_snapshot")
        return value

    @model_validator(mode="after")
    def _v_coherence(self) -> SourceBinding:
        if not _CONNECTOR_ID_RE.match(self.connector_id):
            raise ValueError(f"connector_id must match {_CONNECTOR_ID_RE.pattern!r}")
        # Mirror the connector contract: extract requires a freshness policy.
        if self.mode is ExecutionMode.EXTRACT and self.freshness_policy is None:
            raise ValueError("mode 'extract' requires a freshness policy")
        if self.mode is ExecutionMode.LIVE and self.freshness_policy is not None:
            raise ValueError("mode 'live' must not declare a freshness policy")
        return self


class LineageEdge(_KnowledgeModel):
    """Append-only lineage edge (Section 2.3, D-KP-003).

    Historical edges are never mutated or deleted; corrections are new edges
    carrying ``supersedes_edge_id``. No update or delete path exists in these
    contracts, and the storage layer rejects both at the persistence level.

    Logical identity (used for replay resolution) is
    ``(tenant_id, edge_kind, from_asset_id, to_asset_id, supersedes_edge_id)``;
    ``edge_id`` is a surrogate key and ``observed_at`` is observational.
    """

    schema_version: str = SCHEMA_VERSION
    edge_id: str
    tenant_id: str
    from_asset_id: str
    to_asset_id: str
    edge_kind: LineageEdgeKind
    observed_at: datetime
    evidence_ref: str
    supersedes_edge_id: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("edge_id", "from_asset_id", "to_asset_id")
    @classmethod
    def _v_uuids(cls, value: str, info: Any) -> str:
        return _check_uuid(value, info.field_name or "uuid field")

    @field_validator("observed_at")
    @classmethod
    def _v_observed_at(cls, value: datetime) -> datetime:
        return _check_aware_datetime(value, "observed_at")

    @field_validator("evidence_ref")
    @classmethod
    def _v_evidence(cls, value: str) -> str:
        # Evidence is an evidence-root hash or an IR-version checksum pointer;
        # a placeholder hash never grounds a lineage claim (P1-5).
        if _CRED_RE.search(value):
            raise ValueError("evidence_ref must not contain credentials")
        checksum = value[3:] if value.startswith("ir:") else value
        if not _HASH_RE.match(checksum):
            raise ValueError(
                "evidence_ref must be a SHA-256 evidence hash or an"
                " 'ir:{canonical sha256 checksum}' version pointer,"
                f" got {value!r}"
            )
        if checksum in PLACEHOLDER_EVIDENCE_HASHES:
            raise ValueError(
                f"evidence_ref {value!r} is a placeholder hash (all-zero or"
                " all-f); evidence must cite a real artifact or IR checksum"
            )
        return value

    @field_validator("supersedes_edge_id")
    @classmethod
    def _v_supersedes(cls, value: str | None) -> str | None:
        return None if value is None else _check_uuid(value, "supersedes_edge_id")

    @model_validator(mode="after")
    def _v_no_self_edge(self) -> LineageEdge:
        if self.from_asset_id == self.to_asset_id:
            raise ValueError("lineage edges cannot be self-referential")
        if self.supersedes_edge_id is not None and self.supersedes_edge_id == self.edge_id:
            raise ValueError("an edge cannot supersede itself")
        return self


class SemanticEntity(_KnowledgeModel):
    """Table/column/relationship projected from a Semantic IR version (2.4)."""

    schema_version: str = SCHEMA_VERSION
    entity_id: str
    tenant_id: str
    ir_version: str
    node_path: str
    entity_type: SemanticEntityType
    grain: str | None = None
    created_at: datetime
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("entity_id")
    @classmethod
    def _v_entity_id(cls, value: str) -> str:
        return _check_uuid(value, "entity_id")

    @field_validator("ir_version")
    @classmethod
    def _v_ir_version(cls, value: str) -> str:
        return _check_hash(value, "ir_version")

    @field_validator("node_path")
    @classmethod
    def _v_node_path(cls, value: str) -> str:
        if not _NODE_PATH_RE.match(value):
            raise ValueError(f"node_path must match {_NODE_PATH_RE.pattern!r}, got {value!r}")
        return value

    @field_validator("created_at")
    @classmethod
    def _v_created_at(cls, value: datetime) -> datetime:
        return _check_aware_datetime(value, "created_at")

    @field_validator("created_by")
    @classmethod
    def _v_created_by(cls, value: str) -> str:
        return _check_principal(value)


class MetricRegistryEntry(_KnowledgeModel):
    """Metric occurrence projected from the Semantic IR (Section 2.4, D-KP-004).

    Stores only the reference (``ir_version`` + ``node_path`` + checksum) and
    governance metadata; the formula lives in the IR version itself.
    """

    schema_version: str = SCHEMA_VERSION
    metric_id: str
    tenant_id: str
    asset_id: str
    ir_version: str
    node_path: str
    metric_checksum: str
    owner: str
    certification: MetricCertification = MetricCertification.NONE
    certified_by: str | None = None
    certified_at: datetime | None = None
    grain: str | None = None
    default_filter_context: dict[str, Any] | None = None
    created_at: datetime
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("metric_id", "asset_id")
    @classmethod
    def _v_uuids(cls, value: str, info: Any) -> str:
        return _check_uuid(value, info.field_name or "uuid field")

    @field_validator("ir_version", "metric_checksum")
    @classmethod
    def _v_hashes(cls, value: str, info: Any) -> str:
        return _check_hash(value, str(info.field_name))

    @field_validator("node_path")
    @classmethod
    def _v_node_path(cls, value: str) -> str:
        if not _NODE_PATH_RE.match(value):
            raise ValueError(f"node_path must match {_NODE_PATH_RE.pattern!r}, got {value!r}")
        return value

    @field_validator("owner", "created_by", "certified_by")
    @classmethod
    def _v_principals(cls, value: str | None) -> str | None:
        return None if value is None else _check_principal(value)

    @field_validator("certified_at")
    @classmethod
    def _v_certified_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _check_aware_datetime(value, "certified_at")

    @field_validator("created_at")
    @classmethod
    def _v_created_at(cls, value: datetime) -> datetime:
        return _check_aware_datetime(value, "created_at")

    @field_validator("default_filter_context")
    @classmethod
    def _v_filter_context(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        _check_secret_free_node(value, "default_filter_context")
        return value

    @model_validator(mode="after")
    def _v_certification(self) -> MetricRegistryEntry:
        if self.certification is MetricCertification.CERTIFIED:
            if not self.certified_by or not self.certified_at:
                raise ValueError("certified metrics require certified_by and certified_at")
            if self.certified_by == self.created_by:
                raise ValueError(
                    "certification cannot self-certify: certified_by must differ from created_by"
                )
            if is_agent_principal(self.certified_by):
                raise ValueError("agent principals cannot certify metrics")
        elif self.certified_by or self.certified_at:
            raise ValueError(
                "certified_by/certified_at are only valid when certification is 'certified'"
            )
        return self


class BusinessRule(_KnowledgeModel):
    """Approved rule statement linked read-only to an IR filter/RLS node (2.4)."""

    schema_version: str = SCHEMA_VERSION
    rule_id: str
    tenant_id: str
    ir_version: str
    node_path: str
    statement: str
    references_rls: bool = False
    owner: str
    created_at: datetime
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("rule_id")
    @classmethod
    def _v_rule_id(cls, value: str) -> str:
        return _check_uuid(value, "rule_id")

    @field_validator("ir_version")
    @classmethod
    def _v_ir_version(cls, value: str) -> str:
        return _check_hash(value, "ir_version")

    @field_validator("node_path")
    @classmethod
    def _v_node_path(cls, value: str) -> str:
        if not _NODE_PATH_RE.match(value):
            raise ValueError(f"node_path must match {_NODE_PATH_RE.pattern!r}, got {value!r}")
        return value

    @field_validator("statement")
    @classmethod
    def _v_statement(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("statement must be a non-empty string (<= 2000 chars)")
        return _check_secret_free(value, "statement")

    @field_validator("created_at")
    @classmethod
    def _v_created_at(cls, value: datetime) -> datetime:
        return _check_aware_datetime(value, "created_at")

    @field_validator("owner", "created_by")
    @classmethod
    def _v_principals(cls, value: str) -> str:
        return _check_principal(value)


class EvidenceArtifact(_KnowledgeModel):
    """One evidence pointer grounding an insight (Section 2.5)."""

    artifact_hash: str
    kind: EvidenceKind

    @field_validator("artifact_hash")
    @classmethod
    def _v_hash(cls, value: str) -> str:
        return _check_hash(value, "artifact_hash")


class TimeRange(_KnowledgeModel):
    """Closed time range with calendar grain at which a claim holds."""

    start: datetime
    end: datetime
    calendar_grain: CalendarGrain

    @field_validator("start", "end")
    @classmethod
    def _v_datetimes(cls, value: datetime, info: Any) -> datetime:
        return _check_aware_datetime(value, f"time_range.{info.field_name}")

    @model_validator(mode="after")
    def _v_order(self) -> TimeRange:
        if self.end < self.start:
            raise ValueError("time_range.end must not precede time_range.start")
        return self


class ValueRef(_KnowledgeModel):
    """Hash-stamped pointer to a result row/number backing an insight."""

    value_checksum: str
    locator: str

    @field_validator("value_checksum")
    @classmethod
    def _v_checksum(cls, value: str) -> str:
        return _check_hash(value, "value_checksum")

    @field_validator("locator")
    @classmethod
    def _v_locator(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("locator must be a non-empty string (<= 512 chars)")
        return _check_secret_free(value, "locator")


class InsightRecord(_KnowledgeModel):
    """Grounded insight record (Section 2.5, D-KP-005).

    Grounding is explicit and structural:

    * ``filter_context`` must be supplied by the caller. An empty object is
      allowed and means *explicit unfiltered context* — the claim is stated
      over the whole dataset with no filters applied.
    * at least one ``value_refs`` entry and non-empty ``evidence`` are
      required; an insight without ``metric_ref`` and ``evidence`` is
      rejected at the contract level, not by policy review alone.
    """

    schema_version: str = SCHEMA_VERSION
    insight_id: str
    tenant_id: str
    statement: str
    metric_ref: str
    grain: str
    filter_context: dict[str, Any]
    time_range: TimeRange
    value_refs: tuple[ValueRef, ...] = ()
    ir_version: str
    query_plan_version: str
    evidence: tuple[EvidenceArtifact, ...]
    certification: InsightCertification = InsightCertification.DRAFT
    certified_by: str | None = None
    certified_at: datetime | None = None
    as_of: datetime
    created_at: datetime
    created_by: str

    @field_validator("schema_version")
    @classmethod
    def _v_schema(cls, value: str) -> str:
        return _check_schema_version(value)

    @field_validator("tenant_id")
    @classmethod
    def _v_tenant(cls, value: str) -> str:
        return _check_tenant(value)

    @field_validator("insight_id", "metric_ref")
    @classmethod
    def _v_uuids(cls, value: str, info: Any) -> str:
        return _check_uuid(value, str(info.field_name))

    @field_validator("ir_version")
    @classmethod
    def _v_ir_version(cls, value: str) -> str:
        return _check_hash(value, "ir_version")

    @field_validator("statement")
    @classmethod
    def _v_statement(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("statement must be a non-empty string (<= 2000 chars)")
        return _check_secret_free(value, "statement")

    @field_validator("grain")
    @classmethod
    def _v_grain(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("grain must be a non-empty string (<= 200 chars)")
        return _check_secret_free(value, "grain")

    @field_validator("query_plan_version")
    @classmethod
    def _v_qp_version(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError("query_plan_version must be a non-empty string (<= 128 chars)")
        return _check_secret_free(value, "query_plan_version")

    @field_validator("created_by", "certified_by")
    @classmethod
    def _v_principals(cls, value: str | None) -> str | None:
        return None if value is None else _check_principal(value)

    @field_validator("as_of", "created_at")
    @classmethod
    def _v_datetimes(cls, value: datetime, info: Any) -> datetime:
        return _check_aware_datetime(value, str(info.field_name))

    @field_validator("certified_at")
    @classmethod
    def _v_certified_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _check_aware_datetime(value, "certified_at")

    @field_validator("filter_context")
    @classmethod
    def _v_filter_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Explicit caller-supplied context (empty = explicit unfiltered);
        # never a credential or raw SQL/query carrier.
        _check_secret_free_node(value, "filter_context")
        return value

    @model_validator(mode="after")
    def _v_grounding(self) -> InsightRecord:
        # Grounding is structural: metric_ref is typed mandatory, evidence
        # must be non-empty and at least one value_ref must be cited
        # (D-KP-005).
        if not self.evidence:
            raise ValueError("insights require at least one evidence artifact")
        if not self.value_refs:
            raise ValueError("insights require at least one value_ref")
        if self.certification is InsightCertification.CERTIFIED:
            if not self.certified_by or not self.certified_at:
                raise ValueError("certified insights require certified_by and certified_at")
            if self.certified_by == self.created_by:
                raise ValueError(
                    "insight certification cannot self-certify: reviewer must differ from author"
                )
            if is_agent_principal(self.certified_by):
                raise ValueError("agent principals cannot certify insights")
        elif self.certified_by or self.certified_at:
            raise ValueError(
                "certified_by/certified_at are only valid when certification is 'certified'"
            )
        return self


__all__ = [
    "SCHEMA_VERSION",
    "AssetKind",
    "AssetState",
    "CalendarGrain",
    "EvidenceArtifact",
    "EvidenceKind",
    "InsightCertification",
    "InsightRecord",
    "KnowledgeAsset",
    "LineageEdge",
    "LineageEdgeKind",
    "MetricCertification",
    "MetricRegistryEntry",
    "PLACEHOLDER_EVIDENCE_HASHES",
    "SemanticEntity",
    "SemanticEntityType",
    "SourceBinding",
    "TimeRange",
    "ValueRef",
    "allowed_edge_directions",
    "check_asset_identity_coherence",
    "check_edge_direction",
    "compute_hash",
    "is_agent_principal",
    "natural_key_connector_id",
    "validate_natural_key",
]
