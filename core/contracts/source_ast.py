"""Versioned origin-aware Source AST contract.

Defines the neutral, target-agnostic structural tree that captures a BI source
artifact (Tableau TWB/TWBX, Power BI PBIP, ...) without loss. It is the first
stage of the DataVIZ pipeline: importers emit this tree, and downstream layers
(Semantic IR, Presentation IR, Interaction IR) are derived from it.

Key properties:

* **Versioned**: a single :data:`SCHEMA_VERSION` (SemVer). Deserialization
  requires an exact match; there is no silent schema migration.
* **Origin-aware**: every node and the document carry an :class:`OriginKind` so
  the tree remembers which platform it was captured from.
* **Stable ids**: nodes carry a ``node_id`` that callers should derive via
  :func:`stable_node_id` (deterministic SHA-256 prefix) so re-extraction of the
  same source reproduces identical, referenceable ids.
* **Unknown-node preservation**: :attr:`NodeKind.UNKNOWN` plus ``origin_tag``
  and ``raw_payload`` let the importer retain any element it cannot map, so no
  source information is dropped.
* **Sanitized provenance**: the document optionally embeds a
  :class:`~core.contracts.provenance.ProvenanceRecord` (sanitized relative
  path + SHA-256 + origin) reused from :mod:`core.contracts.provenance`.

Design (Ponytail): reuses ``OriginKind`` and ``ProvenanceRecord`` from the
sibling provenance contract; the node tree is built on stdlib dataclasses only.
No PBIR, DAX, React or connector imports.

Schema Versioning Policy (SemVer):

* **MAJOR**: Breaking changes (field removals, type changes, semantic shifts).
* **MINOR**: Backward-compatible extensions (optional fields with defaults).
* **PATCH**: Internal fixes preserving the serialized format.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.contracts.provenance import (
    OriginKind,
    ProvenanceRecord,
)

SCHEMA_VERSION: str = "2.0.0"
"""Current schema version for the Source AST contract. See SemVer policy above."""


class NodeKind(StrEnum):
    """Neutral structural node kinds for the Source AST tree.

    Closed, platform-agnostic concept set. :attr:`UNKNOWN` preserves any
    source element the importer cannot map, so no information is lost. Names
    deliberately avoid PBIR visual types (e.g. ``clusteredBarChart``), DAX
    concepts (e.g. ``measure``) and React component names.
    """

    DOCUMENT = "document"
    DATASOURCE = "datasource"
    FIELD = "field"
    CALCULATION = "calculation"
    PARAMETER = "parameter"
    FILTER = "filter"
    PAGE = "page"
    VISUAL = "visual"
    LAYOUT_ZONE = "layout_zone"
    RELATIONSHIP = "relationship"
    FORECAST = "forecast"
    UNKNOWN = "unknown"


def stable_node_id(origin: OriginKind | str, *path: str) -> str:
    """Derive a deterministic stable node id from ``origin`` and ``path``.

    The same ``(origin, path...)`` always yields the same id (truncated
    SHA-256), so re-extracting the same source artifact reproduces identical
    node ids that downstream artifacts can reference without a registry.

    Args:
        origin: Origin platform (:class:`OriginKind` or its string value).
        *path: Ordered path segments identifying the node within the artifact
            (e.g. ``"datasource1"``, ``"field:Sales"``).

    Returns:
        Stable id of the form ``"sn_<16-hex-chars>"``.

    Raises:
        ValueError: If ``path`` is empty or any segment is not a non-empty str.
    """
    origin_value = origin.value if isinstance(origin, OriginKind) else str(origin)
    if not path:
        raise ValueError("stable_node_id requires at least one path segment")
    for idx, seg in enumerate(path):
        if not isinstance(seg, str) or not seg:
            raise ValueError(f"path segment #{idx} must be a non-empty string, got {seg!r}")
    # Unit separator (\x1f) between joined fields avoids accidental collisions
    # that simple "/" joining could cause across different path shapes.
    raw = "\x1f".join([origin_value, *path])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sn_{digest[:16]}"


@dataclass(frozen=True)
class SourceNode:
    """A single immutable node in the origin-aware Source AST tree.

    Attributes:
        node_id: Stable, unique-within-document node identifier. Derive it via
            :func:`stable_node_id` so re-extraction reproduces the same id.
        kind: Neutral :class:`NodeKind` of this node.
        origin_kind: Origin platform this node was captured from.
        origin_tag: Source-native element name (e.g. ``"worksheet"``,
            ``"zone"``). Preserves the original concept and should be set when
            ``kind`` is :attr:`NodeKind.UNKNOWN` so the preserved element is
            identifiable.
        name: Optional human-readable name (field/sheet/visual name).
        attributes: JSON-compatible scalar/simple attributes (intent, types,
            flags). Inner keys are not schema-validated (escape hatch).
        raw_payload: Raw unparsed structure for unrecognized nodes; preserves
            the original content losslessly for later analysis.
        children: Ordered child nodes.
    """

    node_id: str
    kind: NodeKind
    origin_kind: OriginKind = OriginKind.UNKNOWN
    origin_tag: str = ""
    name: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None
    children: tuple[SourceNode, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("node_id must be a non-empty string")
        # Coerce str -> enum for friendlier construction; reject anything else.
        if not isinstance(self.kind, NodeKind):
            try:
                object.__setattr__(self, "kind", NodeKind(self.kind))
            except ValueError as exc:
                raise ValueError(f"kind must be a NodeKind, got {self.kind!r}") from exc
        if not isinstance(self.origin_kind, OriginKind):
            try:
                object.__setattr__(self, "origin_kind", OriginKind(self.origin_kind))
            except ValueError as exc:
                raise ValueError(
                    f"origin_kind must be an OriginKind, got {self.origin_kind!r}"
                ) from exc

    def to_dict(self) -> dict[str, Any]:
        """Serialize this node (and its children) to a JSON-compatible dict."""
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "origin_kind": self.origin_kind.value,
            "origin_tag": self.origin_tag,
            "name": self.name,
            "attributes": dict(self.attributes),
            "raw_payload": (dict(self.raw_payload) if self.raw_payload is not None else None),
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceNode:
        """Reconstruct a node from a dict, strictly rejecting unexpected keys.

        Raises:
            TypeError: If ``data`` contains keys other than the declared fields.
        """
        allowed = {
            "node_id",
            "kind",
            "origin_kind",
            "origin_tag",
            "name",
            "attributes",
            "raw_payload",
            "children",
        }
        extra = set(data.keys()) - allowed
        if extra:
            raise TypeError(f"Unexpected SourceNode keys: {sorted(extra)}")
        children_raw = data.get("children", ())
        children = tuple(cls.from_dict(c) if isinstance(c, Mapping) else c for c in children_raw)
        return cls(
            node_id=str(data["node_id"]),
            kind=data["kind"],
            origin_kind=data.get("origin_kind", OriginKind.UNKNOWN.value),
            origin_tag=str(data.get("origin_tag", "")),
            name=str(data.get("name", "")),
            attributes=dict(data.get("attributes", {})),
            raw_payload=(
                dict(data["raw_payload"]) if data.get("raw_payload") is not None else None
            ),
            children=children,
        )


@dataclass(frozen=True)
class SourceAST:
    """Canonical versioned origin-aware Source AST document (tree root).

    Wraps the root :class:`SourceNode` with the schema version, a document-level
    origin and an optional sanitized :class:`ProvenanceRecord`. Deserialization
    enforces exact schema-version equality (no silent migration) and node_id
    uniqueness across the whole tree.

    Attributes:
        schema_version: SemVer string; must equal :data:`SCHEMA_VERSION`.
        origin_kind: Document-level origin platform.
        root: Root node (typically :attr:`NodeKind.DOCUMENT`).
        provenance: Optional sanitized provenance record linking back to the
            source artifact bytes (relative path + SHA-256 + origin).
        metadata: Free-form JSON metadata. Must never carry secrets.
    """

    schema_version: str = SCHEMA_VERSION
    origin_kind: OriginKind = OriginKind.UNKNOWN
    root: SourceNode = field(
        default_factory=lambda: SourceNode(
            node_id=stable_node_id(OriginKind.UNKNOWN, "root"),
            kind=NodeKind.DOCUMENT,
        )
    )
    provenance: ProvenanceRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize document-level origin to the enum; reject unknown values.
        if not isinstance(self.origin_kind, OriginKind):
            try:
                object.__setattr__(self, "origin_kind", OriginKind(self.origin_kind))
            except ValueError as exc:
                raise ValueError(
                    f"origin_kind must be an OriginKind, got {self.origin_kind!r}"
                ) from exc

    def validate(self) -> None:
        """Validate schema version and node_id uniqueness across the whole tree.

        Raises:
            ValueError: If the schema version does not match :data:`SCHEMA_VERSION`
                or if two nodes share the same ``node_id``.
        """
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        seen: set[str] = set()
        stack: list[SourceNode] = [self.root]
        while stack:
            node = stack.pop()
            if node.node_id in seen:
                raise ValueError(f"Duplicate node_id detected: {node.node_id!r}")
            seen.add(node.node_id)
            stack.extend(node.children)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the document to a JSON-compatible dict."""
        origin = self.origin_kind
        return {
            "schema_version": self.schema_version,
            "origin_kind": (origin.value if isinstance(origin, OriginKind) else str(origin)),
            "root": self.root.to_dict(),
            "provenance": (self.provenance.model_dump() if self.provenance is not None else None),
            "metadata": dict(self.metadata),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceAST:
        """Reconstruct a document from a dict with strict schema enforcement.

        Requires ``schema_version == SCHEMA_VERSION`` (no silent migration),
        reconstructs the node tree and provenance, then runs :meth:`validate`.
        """
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, got {received!r}"
            )
        origin_raw = data.get("origin_kind", OriginKind.UNKNOWN.value)
        try:
            origin_kind = OriginKind(origin_raw) if isinstance(origin_raw, str) else origin_raw
        except ValueError as exc:
            raise ValueError(f"Invalid origin_kind: {origin_raw!r}") from exc

        root_raw = data.get("root")
        if not isinstance(root_raw, Mapping):
            raise ValueError("SourceAST.root must be a mapping")
        root = SourceNode.from_dict(root_raw)

        prov_raw = data.get("provenance")
        provenance = ProvenanceRecord(**dict(prov_raw)) if prov_raw else None

        doc = cls(
            schema_version=SCHEMA_VERSION,
            origin_kind=origin_kind,
            root=root,
            provenance=provenance,
            metadata=dict(data.get("metadata", {})),
        )
        doc.validate()
        return doc

    @classmethod
    def from_json(cls, text: str) -> SourceAST:
        """Reconstruct from canonical JSON (``from_dict(json.loads(text))``)."""
        return cls.from_dict(json.loads(text))


__all__ = [
    "NodeKind",
    "SCHEMA_VERSION",
    "SourceAST",
    "SourceNode",
    "stable_node_id",
]
