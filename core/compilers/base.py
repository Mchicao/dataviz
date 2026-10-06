"""Pure destination compiler boundaries.

Defines the neutral -> destination compilation interface and a generic,
versioned :class:`DestinationPlan` envelope. Destination compilers consume the
neutral :class:`~core.contracts.semantic_ir.SemanticModel` and emit a
destination-specific plan wrapped in that envelope.

Boundary rules (one-way dependency):

* A compiler **may** depend on contracts (``core.contracts.*``) and the
  standard library.
* A compiler **must not** import concrete destination runtimes (PBIR / visual
  JSON / DAX / React / connectors) -- it emits a structural plan, never renders.
* Contracts **must not** import compilers -- the arrow of dependency is
  ``source -> contracts -> compiler -> plan``, never the reverse. Import cycles
  from contracts to destinations are rejected by
  :mod:`tests.compilers.test_destination_boundaries`.

This module defines **boundaries only**; :class:`PBIPPlanCompiler` is a
proof-level compiler that emits the *shape* of a PBIP plan (tables, measures,
relationships, parameters). It does **not** generate TMDL, DAX or PBIR bytes --
that is a future renderer's responsibility.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.contracts.provenance import OriginKind
from core.contracts.semantic_ir import SemanticModel

SCHEMA_VERSION: str = "1.0.0"
"""Current schema version of the destination plan envelope (SemVer)."""

#: Destination ids produced by the proof compilers. Closed catalog so the
#: boundary is explicit about which destinations exist at this layer.
DESTINATION_IDS: frozenset[str] = frozenset({"power_bi_pbip", "dataviz_render"})


def _module_imports(module) -> list[str]:
    """Return the fully-qualified import names declared in ``module``.

    Public helper reused by the import-isolation tests to scan both contract
    and compiler modules for forbidden dependencies without importing them.
    """
    import ast
    import inspect

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


@dataclass(frozen=True)
class DestinationPlan:
    """Generic versioned envelope around a destination-specific plan body.

    The envelope is destination-neutral: it carries traceability
    (``source_model``, ``source_schema_version``, ``origin``) plus a
    ``destination`` id and a JSON-compatible ``body`` produced by a compiler.
    The body is validated for JSON serializability on construction, so every
    plan is guaranteed persistence-safe.

    Immutable (``frozen=True``). Round-trips through JSON via
    :meth:`to_json` / :meth:`from_json`.

    Attributes:
        schema_version: SemVer; must equal :data:`SCHEMA_VERSION`.
        destination: destination id (e.g. ``"power_bi_pbip"``,
            ``"dataviz_render"``).
        origin: :class:`~core.contracts.provenance.OriginKind` value of the
            compiled source.
        source_model: name of the compiled neutral :class:`SemanticModel`.
        source_schema_version: ``schema_version`` of the compiled model, copied
            as a string so the envelope does not couple to the model contract.
        body: destination-specific JSON-compatible plan payload.
    """

    schema_version: str
    destination: str
    origin: str
    source_model: str
    source_schema_version: str
    body: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, "
                f"received {self.schema_version!r}"
            )
        if not isinstance(self.destination, str) or not self.destination.strip():
            raise ValueError("DestinationPlan.destination is required")
        allowed = {k.value for k in OriginKind}
        if self.origin not in allowed:
            raise ValueError(f"origin must be one of {sorted(allowed)}, got {self.origin!r}")
        if not isinstance(self.source_model, str) or not self.source_model.strip():
            raise ValueError("DestinationPlan.source_model is required")
        if not isinstance(self.source_schema_version, str) or not self.source_schema_version:
            raise ValueError("DestinationPlan.source_schema_version is required")
        if not isinstance(self.body, Mapping):
            raise TypeError("DestinationPlan.body must be a Mapping")
        # Store a shallow copy so callers cannot mutate the envelope's body.
        object.__setattr__(self, "body", dict(self.body))
        # Guarantee the body is JSON-serializable (persistence-safe envelope).
        try:
            json.dumps(self.body)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"DestinationPlan.body must be JSON-serializable: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (body copied)."""
        return {
            "schema_version": self.schema_version,
            "destination": self.destination,
            "origin": self.origin,
            "source_model": self.source_model,
            "source_schema_version": self.source_schema_version,
            "body": dict(self.body),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DestinationPlan:
        """Rebuild an envelope from a dict, enforcing ``schema_version`` (no migration)."""
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, received {received!r}"
            )
        body = data.get("body")
        if not isinstance(body, Mapping):
            raise ValueError("DestinationPlan.body is required and must be a Mapping")
        return cls(
            schema_version=SCHEMA_VERSION,
            destination=str(data["destination"]),
            origin=str(data.get("origin", OriginKind.UNKNOWN.value)),
            source_model=str(data["source_model"]),
            source_schema_version=str(data["source_schema_version"]),
            body=dict(body),
        )

    @classmethod
    def from_json(cls, text: str) -> DestinationPlan:
        """Rebuild from canonical JSON (``from_dict(json.loads(text))``)."""
        return cls.from_dict(json.loads(text))


class DestinationCompiler(ABC):
    """Abstract boundary: compile a neutral :class:`SemanticModel` into a plan.

    Subclasses declare a stable ``destination`` id and implement
    :meth:`compile`. The ``origin`` of the compiled source is a construction
    parameter (defaults to ``"unknown"``) so the same compiler can be reused
    across source platforms.

    The dependency is strictly one-way: a compiler reads contracts and emits a
    :class:`DestinationPlan`. It must never import a destination runtime, and
    contracts must never import a compiler.
    """

    def __init__(self, *, origin: str = OriginKind.UNKNOWN.value) -> None:
        allowed = {k.value for k in OriginKind}
        if origin not in allowed:
            raise ValueError(f"origin must be one of {sorted(allowed)}, got {origin!r}")
        self._origin = origin

    @property
    def origin(self) -> str:
        """Origin of the source being compiled (an :class:`OriginKind` value)."""
        return self._origin

    @property
    @abstractmethod
    def destination(self) -> str:
        """Stable destination id this compiler emits (e.g. ``"power_bi_pbip"``)."""

    @abstractmethod
    def compile(self, model: SemanticModel) -> DestinationPlan:
        """Compile a neutral model into a destination-specific plan."""

    def _envelope(self, model: SemanticModel, body: Mapping[str, Any]) -> DestinationPlan:
        """Build a :class:`DestinationPlan` tagged with this compiler's identity."""
        return DestinationPlan(
            schema_version=SCHEMA_VERSION,
            destination=self.destination,
            origin=self._origin,
            source_model=model.name,
            source_schema_version=model.schema_version,
            body=body,
        )


class PBIPPlanCompiler(DestinationCompiler):
    """Proof-level compiler: emits the *shape* of a Power BI PBIP plan.

    Maps the neutral model to a structural PBIP-style body (tables, measures,
    relationships, parameters). This is a **boundary stub**: it does not
    generate TMDL, DAX expressions or PBIR bytes. Measure entries carry only
    identity and formatting metadata; the neutral expression is intentionally
    left for a future DAX/TMDL renderer to emit.
    """

    @property
    def destination(self) -> str:
        return "power_bi_pbip"

    def compile(self, model: SemanticModel) -> DestinationPlan:
        tables = [
            {
                "name": entity.name,
                "columns": [
                    {
                        "name": field.name,
                        "data_type": field.data_type.value,
                        "is_key": field.is_key,
                    }
                    for field in entity.fields
                ],
            }
            for entity in model.entities
        ]
        # Boundary stub: measure identity + formatting only; no DAX emitted.
        measures = [
            {
                "name": metric.name,
                "data_type": metric.data_type.value,
                "format_string": metric.format_string,
            }
            for metric in model.metrics
        ]
        relationships = [
            {
                "name": rel.name,
                "from_table": rel.from_entity,
                "to_table": rel.to_entity,
                "from_columns": list(rel.from_fields),
                "to_columns": list(rel.to_fields),
                "cardinality": rel.cardinality,
                "cross_filter": rel.cross_filter,
            }
            for rel in model.relationships
        ]
        parameters = [
            {"name": param.name, "data_type": param.data_type.value} for param in model.parameters
        ]
        body: dict[str, Any] = {
            "tables": tables,
            "measures": measures,
            "relationships": relationships,
            "parameters": parameters,
        }
        return self._envelope(model, body)


__all__ = [
    "DESTINATION_IDS",
    "SCHEMA_VERSION",
    "DestinationCompiler",
    "DestinationPlan",
    "PBIPPlanCompiler",
]
