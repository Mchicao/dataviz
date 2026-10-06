"""Typed authoring operations over the neutral :class:`SemanticModel`.

Operations are immutable value objects that describe a single intent against a
**stable IR id** — the ``name`` of an entity / metric / parameter / filter /
relationship / rls_intent. They never carry arbitrary code: the only way to
introduce logic is via the closed
:class:`core.contracts.semantic_ir.Expression` grammar, which is enforced by
the contract constructors. Add/update payloads MUST be the typed contract
instances (``Entity`` / ``Metric`` / ...); raw dicts or source strings are
rejected at construction.

Validation happens in three layers:

1. **Structural** (:meth:`Operation.__post_init__`): op kind/target allowlists,
   payload presence and exact type, and ``payload.name == op.name``.
2. **Existence** (:func:`materialize` batch loop): add requires the id to be
   absent, update/remove require it to be present. The check runs against the
   evolving working set, so a batch may legitimately add-then-update the same
   id.
3. **Semantic** (:class:`SemanticModel` reconstruction): uniqueness, relationship
   endpoint resolution and referential integrity of every expression. If this
   fails the whole :func:`materialize` call raises and nothing is committed
   (the input model is immutable).

Versioning policy (SemVer):

* MAJOR: op shape change (new required field, removed kind/target).
* MINOR: new optional op kind/target (additive).
* PATCH: internal fix preserving serialized form.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal, get_args

from core.contracts.semantic_ir import (
    Entity,
    Filter,
    Metric,
    Parameter,
    Relationship,
    RLSIntent,
    SemanticModel,
    semantic_item_from_dict,
)

OpKind = Literal["add", "update", "remove"]
TargetKind = Literal["entity", "metric", "parameter", "filter", "relationship", "rls_intent"]

VALID_OP_KINDS: frozenset[str] = frozenset(get_args(OpKind))
"""Closed allowlist of operation kinds."""

VALID_TARGET_KINDS: frozenset[str] = frozenset(get_args(TargetKind))
"""Closed allowlist of IR sections addressable by stable id."""

#: Maps each target kind to the contract dataclass accepted as add/update payload.
_PAYLOAD_TYPE: dict[str, type] = {
    "entity": Entity,
    "metric": Metric,
    "parameter": Parameter,
    "filter": Filter,
    "relationship": Relationship,
    "rls_intent": RLSIntent,
}


def _names(model: SemanticModel, target: str) -> set[str]:
    """Return the set of stable ids currently registered under ``target``."""
    if target == "entity":
        return {e.name for e in model.entities}
    if target == "metric":
        return {m.name for m in model.metrics}
    if target == "parameter":
        return {p.name for p in model.parameters}
    if target == "filter":
        return {f.name for f in model.filters}
    if target == "relationship":
        return {r.name for r in model.relationships}
    return {r.name for r in model.rls_intents}  # rls_intent


def _opaque_dax_body(payload: object | None) -> str | None:
    """Return an imported opaque metric body, if this payload carries one."""
    if isinstance(payload, Metric) and payload.expression.kind == "opaque":
        value = payload.expression.value
        return value if isinstance(value, str) else None
    return None


def _guard_opaque_authoring(op: Operation, section: dict[str, object]) -> None:
    """Opaque DAX is import-preservation state, not an authoring code channel."""
    if op.target != "metric" or op.kind == "remove":
        return
    proposed = _opaque_dax_body(op.payload)
    if proposed is None:
        return
    if op.kind == "add":
        raise ValueError("authoring cannot introduce opaque DAX metrics")
    current = _opaque_dax_body(section.get(op.name))
    if current != proposed:
        raise ValueError("authoring cannot introduce or modify opaque DAX bodies")


def _section(model: SemanticModel, target: str) -> list[tuple[str, object]]:
    """Ordered ``(name, instance)`` pairs for one section of ``model``."""
    if target == "entity":
        return [(e.name, e) for e in model.entities]
    if target == "metric":
        return [(m.name, m) for m in model.metrics]
    if target == "parameter":
        return [(p.name, p) for p in model.parameters]
    if target == "filter":
        return [(f.name, f) for f in model.filters]
    if target == "relationship":
        return [(r.name, r) for r in model.relationships]
    return [(r.name, r) for r in model.rls_intents]  # rls_intent


@dataclass(frozen=True)
class Operation:
    """A single typed authoring operation against a stable IR id.

    Attributes:
        kind: ``"add"``, ``"update"`` or ``"remove"``.
        target: IR section addressed by the stable id.
        name: stable id of the element. For add/update it MUST equal
            ``payload.name``.
        payload: typed contract instance required for ``add`` and ``update``;
            MUST be ``None`` for ``remove``. Raw dicts or source code strings
            are rejected here, so arbitrary code cannot enter the model through
            an operation.
    """

    kind: OpKind
    target: TargetKind
    name: str
    payload: object | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_OP_KINDS:
            raise ValueError(
                f"op kind not allowed: {self.kind!r}; allowlist={sorted(VALID_OP_KINDS)}"
            )
        if self.target not in VALID_TARGET_KINDS:
            raise ValueError(
                f"op target not allowed: {self.target!r}; allowlist={sorted(VALID_TARGET_KINDS)}"
            )
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("op.name must be a non-empty string")
        if self.kind == "remove":
            if self.payload is not None:
                raise ValueError("remove ops must not carry a payload")
        else:
            if self.payload is None:
                raise ValueError(f"{self.kind} ops require a payload")
            expected = _PAYLOAD_TYPE[self.target]
            if not isinstance(self.payload, expected):
                raise TypeError(
                    f"{self.target} {self.kind} payload must be {expected.__name__}, "
                    f"got {type(self.payload).__name__}"
                )
            if self.payload.name != self.name:
                raise ValueError(
                    f"payload.name {self.payload.name!r} must equal op.name {self.name!r}"
                )

    def validate(self, model: SemanticModel) -> None:
        """Validate this op against a single ``model`` snapshot.

        Useful for pre-checking one operation in isolation. Batch apply
        (:func:`materialize`) re-runs the same existence checks against the
        evolving working set, so callers do not need to invoke this manually
        before :func:`materialize`.
        """
        existing = _names(model, self.target)
        if self.kind == "add" and self.name in existing:
            raise ValueError(f"cannot add {self.target} {self.name!r}: already exists")
        if self.kind in {"update", "remove"} and self.name not in existing:
            raise ValueError(f"cannot {self.kind} {self.target} {self.name!r}: not found")

    def to_dict(self) -> dict[str, object]:
        payload = self.payload
        return {
            "kind": self.kind,
            "target": self.target,
            "name": self.name,
            "payload": None if payload is None else _payload_to_dict(payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Operation:
        if not isinstance(data, dict):
            raise TypeError("authoring operation must be an object")
        unknown = set(data) - {"kind", "target", "name", "payload"}
        if unknown:
            raise ValueError(f"unknown semantic operation field(s): {sorted(unknown)}")
        kind = data.get("kind")
        target = data.get("target")
        name = data.get("name")
        if not isinstance(kind, str) or not isinstance(target, str) or not isinstance(name, str):
            raise TypeError("semantic operation kind, target and name must be strings")
        raw_payload = data.get("payload")
        if kind == "remove":
            if raw_payload is not None:
                raise ValueError("remove ops must not carry a payload")
            payload = None
        else:
            if not isinstance(raw_payload, dict):
                raise TypeError(f"{kind or 'authoring'} ops require an object payload")
            payload = semantic_item_from_dict(target, raw_payload)
        return cls(kind, target, name, payload)  # type: ignore[arg-type]


def _payload_to_dict(payload: object) -> dict[str, object]:
    """Serialize a typed semantic op payload without inventing a parallel schema."""
    if type(payload) not in {Entity, Relationship, Metric, Parameter, Filter, RLSIntent}:
        raise TypeError(f"unsupported semantic authoring payload: {type(payload).__name__}")
    # ``asdict`` follows the exact dataclass contract recursively. JSON
    # roundtrip normalizes str-enums such as DataType to their wire values
    # without evaluating referential integrity outside the authoritative base.
    return json.loads(json.dumps(asdict(payload), ensure_ascii=False))


# --- typed constructors ----------------------------------------------------
#
# Thin factories so callers get per-target static type hints while the internal
# representation stays a single discriminated :class:`Operation` dataclass.


def add_entity(entity: Entity) -> Operation:
    """Add a new :class:`Entity` (fails if the name already exists)."""
    return Operation("add", "entity", entity.name, entity)


def add_metric(metric: Metric) -> Operation:
    """Add a new :class:`Metric` (its expression must use the closed grammar)."""
    return Operation("add", "metric", metric.name, metric)


def add_parameter(parameter: Parameter) -> Operation:
    return Operation("add", "parameter", parameter.name, parameter)


def add_filter(filter_: Filter) -> Operation:
    return Operation("add", "filter", filter_.name, filter_)


def add_relationship(relationship: Relationship) -> Operation:
    return Operation("add", "relationship", relationship.name, relationship)


def add_rls_intent(rls: RLSIntent) -> Operation:
    return Operation("add", "rls_intent", rls.name, rls)


def update_entity(entity: Entity) -> Operation:
    """Replace the entity identified by ``entity.name`` (full replacement)."""
    return Operation("update", "entity", entity.name, entity)


def update_metric(metric: Metric) -> Operation:
    return Operation("update", "metric", metric.name, metric)


def update_parameter(parameter: Parameter) -> Operation:
    return Operation("update", "parameter", parameter.name, parameter)


def update_filter(filter_: Filter) -> Operation:
    return Operation("update", "filter", filter_.name, filter_)


def update_relationship(relationship: Relationship) -> Operation:
    return Operation("update", "relationship", relationship.name, relationship)


def update_rls_intent(rls: RLSIntent) -> Operation:
    return Operation("update", "rls_intent", rls.name, rls)


def remove_entity(name: str) -> Operation:
    return Operation("remove", "entity", name, None)


def remove_metric(name: str) -> Operation:
    return Operation("remove", "metric", name, None)


def remove_parameter(name: str) -> Operation:
    return Operation("remove", "parameter", name, None)


def remove_filter(name: str) -> Operation:
    return Operation("remove", "filter", name, None)


def remove_relationship(name: str) -> Operation:
    return Operation("remove", "relationship", name, None)


def remove_rls_intent(name: str) -> Operation:
    return Operation("remove", "rls_intent", name, None)


def materialize(model: SemanticModel, ops: list[Operation]) -> SemanticModel:
    """Apply ``ops`` to ``model``, returning a new validated :class:`SemanticModel`.

    Atomic: the input ``model`` is immutable and untouched; if any op fails
    structural, existence or semantic validation, ``ValueError`` (or
    ``TypeError``) propagates and no partial result is returned.

    Existence checks run against the evolving working set, so a batch may
    legitimately add-then-update or add-then-remove the same id.
    """
    working: dict[str, dict[str, object]] = {
        target: dict(_section(model, target)) for target in VALID_TARGET_KINDS
    }
    for op in ops:
        section = working[op.target]
        if op.kind == "add":
            if op.name in section:
                raise ValueError(f"cannot add {op.target} {op.name!r}: already exists")
            _guard_opaque_authoring(op, section)
            section[op.name] = op.payload
        elif op.kind == "update":
            if op.name not in section:
                raise ValueError(f"cannot update {op.target} {op.name!r}: not found")
            _guard_opaque_authoring(op, section)
            section[op.name] = op.payload
        else:  # remove
            if op.name not in section:
                raise ValueError(f"cannot remove {op.target} {op.name!r}: not found")
            del section[op.name]

    return SemanticModel(
        schema_version=model.schema_version,
        name=model.name,
        description=model.description,
        entities=list(working["entity"].values()),
        relationships=list(working["relationship"].values()),
        metrics=list(working["metric"].values()),
        parameters=list(working["parameter"].values()),
        filters=list(working["filter"].values()),
        rls_intents=list(working["rls_intent"].values()),
    )


__all__ = [
    "OpKind",
    "Operation",
    "TargetKind",
    "VALID_OP_KINDS",
    "VALID_TARGET_KINDS",
    "add_entity",
    "add_filter",
    "add_metric",
    "add_parameter",
    "add_relationship",
    "add_rls_intent",
    "materialize",
    "remove_entity",
    "remove_filter",
    "remove_metric",
    "remove_parameter",
    "remove_relationship",
    "remove_rls_intent",
    "update_entity",
    "update_filter",
    "update_metric",
    "update_parameter",
    "update_relationship",
    "update_rls_intent",
]
