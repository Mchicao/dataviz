"""Contratos neutrales del modelo semántico y su informe de capacidades.

El informe :class:`SemanticIR` conserva el tipo y el orden de cada elemento del
Source AST y lo etiqueta
con un nivel de soporte de migración (``supported`` / ``manual`` /
``unsupported``). Los estados no soportados son siempre explícitos: el IR nunca
descarta un elemento en silencio, de modo que el verificador posterior pueda
constatar que cada entrada del snapshot tiene un destino o una razón documentada.

El contrato es pasivo: sólo transporta datos, no invoca parsers ni conoce la
capa destino (PBIR / visual JSON). Sólo depende de la biblioteca estándar.

Política de versionado (SemVer):
    * MAJOR: cambio rupturista (campo eliminado/renombrado, semántica alterada).
    * MINOR: ampliación compatible (campo nuevo opcional).
    * PATCH: corrección que no cambia la forma serializada.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from enum import StrEnum
from typing import Any, Literal, cast, get_args

SCHEMA_VERSION: str = "1.0.0"
"""Versión actual del contrato. Ver política SemVer en el docstring del módulo."""


class Support(StrEnum):
    """Nivel de soporte de migración para un elemento individual del snapshot.

    Los valores se eligen como subclase de :class:`str` para que la
    serialización JSON sea directa y determinista (``"supported"``,
    ``"manual"``, ``"unsupported"``).
    """

    SUPPORTED = "supported"
    MANUAL = "manual"
    UNSUPPORTED = "unsupported"


#: Categorías reconocidas por el compilador. Cualquier otra categoría que aparezca
#: en una :class:`Capability` se considera externa y debe ser tratada como
#: desconocida por el consumidor (status ``MANUAL`` con reason explícito).
SUPPORTED_KINDS: frozenset[str] = frozenset(
    {
        "dashboard",
        "worksheet",
        "standalone_worksheet",
        "calculated_field",
        "parameter",
        "filter",
        "datasource",
    }
)


def _coerce_support(value: Any) -> Support:
    """Normaliza ``value`` a :class:`Support` aceptando enum o su cadena."""
    if isinstance(value, Support):
        return value
    if isinstance(value, str):
        try:
            return Support(value)
        except ValueError as exc:
            raise ValueError(
                f"status inválido: {value!r} (esperado uno de {[s.value for s in Support]})"
            ) from exc
    raise TypeError(f"status debe ser str o Support, no {type(value).__name__}")


@dataclass(frozen=True)
class Capability:
    """Etiqueta de capacidad de un elemento individual del snapshot.

    Attributes:
        kind: categoría del elemento (``dashboard``, ``worksheet``,
            ``calculated_field``, ``parameter``, ``filter``, ``datasource`` o
            ``standalone_worksheet``). Otros valores son válidos pero indican
            elementos fuera del catálogo canónico.
        name: nombre del elemento dentro de su categoría; cadena vacía si el
            elemento no expone un nombre natural.
        status: nivel de soporte (ver :class:`Support`).
        reason: mensaje explicativo obligatorio cuando ``status`` no es
            ``SUPPORTED``; cadena vacía cuando sí lo es.
        source_index: índice del elemento en su lista de origen (dentro de su
            ``kind``). Garantiza la preservación del orden del snapshot.
    """

    kind: str
    name: str
    status: Support
    reason: str = ""
    source_index: int = 0

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("kind es obligatorio")
        if self.source_index < 0:
            raise ValueError("source_index no puede ser negativo")
        if self.status is Support.SUPPORTED:
            if self.reason:
                raise ValueError(
                    "Un elemento SUPPORTED no debe llevar reason; use MANUAL/UNSUPPORTED"
                )
        elif not self.reason.strip():
            raise ValueError(f"Un elemento {self.status.value!r} debe llevar reason explicativo")


@dataclass(frozen=True)
class SemanticIR:
    """IR semántica etiquetada con capacidades de migración.

    Inmutable (``frozen=True``). Las capacidades se almacenan como una lista
    plana en el orden de aparición canónico del snapshot; ``source_index``
    (dentro de cada ``kind``) garantiza la reconstrucción fiel del orden por
    sección. Los estados ``MANUAL``/``UNSUPPORTED`` nunca se descartan: el IR
    los conserva explícitamente para que el verificador posterior los constate.

    Attributes:
        schema_version: versión SemVer del contrato (siempre :data:`SCHEMA_VERSION`).
        source_schema_version: versión del Source AST compilado.
        capabilities: capacidades de todos los elementos del snapshot, en orden
            de aparición por sección.
    """

    schema_version: str
    source_schema_version: str
    capabilities: list[Capability] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: esperada {SCHEMA_VERSION!r}, "
                f"recibida {self.schema_version!r}"
            )
        if not self.source_schema_version:
            raise ValueError("source_schema_version es obligatoria")
        # ``source_index`` debe ser estrictamente creciente dentro de cada kind,
        # para que el orden del snapshot quede preservado sin ambigüedad.
        seen: dict[str, int] = {}
        for cap in self.capabilities:
            last = seen.get(cap.kind, -1)
            if cap.source_index <= last:
                raise ValueError(
                    f"capabilities de kind={cap.kind!r} deben venir con "
                    f"source_index estrictamente creciente; recibido "
                    f"{cap.source_index} tras {last}"
                )
            seen[cap.kind] = cap.source_index

    @property
    def unsupported(self) -> list[Capability]:
        """Acceso rápido a las capacidades marcadas como ``UNSUPPORTED``."""
        return [c for c in self.capabilities if c.status is Support.UNSUPPORTED]

    @property
    def manual(self) -> list[Capability]:
        """Acceso rápido a las capacidades marcadas como ``MANUAL``."""
        return [c for c in self.capabilities if c.status is Support.MANUAL]

    def capabilities_for(self, kind: str) -> list[Capability]:
        """Capacidades de un ``kind`` dado, preservando el orden del snapshot."""
        return [c for c in self.capabilities if c.kind == kind]

    def kinds(self) -> list[str]:
        """Lista de kinds presentes, en orden de primera aparición."""
        seen: list[str] = []
        seen_set: set[str] = set()
        for cap in self.capabilities:
            if cap.kind not in seen_set:
                seen_set.add(cap.kind)
                seen.append(cap.kind)
        return seen

    def to_dict(self) -> dict[str, Any]:
        """Serializa el IR a un ``dict`` JSON-compatible."""
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "capabilities": [
                {
                    "kind": c.kind,
                    "name": c.name,
                    "status": c.status.value,
                    "reason": c.reason,
                    "source_index": c.source_index,
                }
                for c in self.capabilities
            ],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serializa el IR a JSON canónico (claves ordenadas, UTF-8 puro)."""
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemanticIR:
        """Reconstruye un IR desde un ``dict``, validando ``schema_version``.

        Falla rápido si la versión no coincide con :data:`SCHEMA_VERSION`, si un
        ``status`` no es uno de los valores canónicos o si la monotonia de
        ``source_index`` por ``kind`` se rompe.
        """
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: esperada {SCHEMA_VERSION!r}, recibida {received!r}"
            )
        source_version = data.get("source_schema_version")
        if not isinstance(source_version, str) or not source_version:
            raise ValueError("source_schema_version es obligatoria y debe ser str")
        caps_data = data.get("capabilities", [])
        if not isinstance(caps_data, list):
            raise TypeError("capabilities debe ser una lista")
        caps: list[Capability] = []
        for raw in caps_data:
            if not isinstance(raw, Mapping):
                raise TypeError("cada capability debe ser un Mapping")
            caps.append(
                Capability(
                    kind=str(raw["kind"]),
                    name=str(raw.get("name", "")),
                    status=_coerce_support(raw.get("status")),
                    reason=str(raw.get("reason", "")),
                    source_index=int(raw.get("source_index", 0)),
                )
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            source_schema_version=source_version,
            capabilities=caps,
        )

    @classmethod
    def from_json(cls, text: str) -> SemanticIR:
        """Reconstruye un IR desde JSON, validando ``schema_version``."""
        return cls.from_dict(json.loads(text))


# ===========================================================================
# Neutral Semantic Model (target-agnostic)
# ===========================================================================
#
# The :class:`SemanticIR` / :class:`Capability` / :class:`Support` types above
# form the explicit migration-capability report derived from Source AST. They
# are metadata alongside the neutral semantic truth, never a second model.
#
# The types below are the neutral semantic model: entities, relationships,
# grain, metrics, parameters, filters, row-level security intent and the typed
# expression grammar that backs metrics, filters and RLS. They do NOT encode
# any target system as the semantic truth: target adapters map these neutral
# types to their native equivalents. The contract is passive (data only) and
# depends on the standard library alone.

MODEL_SCHEMA_VERSION: str = "2.0.0"
"""Current schema version of the neutral :class:`SemanticModel`."""


class DataType(StrEnum):
    """Neutral scalar data types (target-agnostic).

    These are intentionally generic logical types; a target adapter maps them
    to its native type system.
    """

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    BINARY = "binary"
    VARIANT = "variant"
    UNKNOWN = "unknown"


def _coerce_data_type(value: object) -> DataType:
    """Normalize ``value`` to :class:`DataType`, accepting the enum or its string."""
    if isinstance(value, DataType):
        return value
    if isinstance(value, str):
        try:
            return DataType(value)
        except ValueError as exc:
            raise ValueError(
                f"invalid data_type: {value!r} (expected one of {[d.value for d in DataType]})"
            ) from exc
    raise TypeError(f"data_type must be str or DataType, not {type(value).__name__}")


# --- Neutral expression grammar -------------------------------------------
#
# A single discriminated :class:`Expression` node backs literals, references,
# operators, scalar functions, aggregations and modified filter context. The
# ``kind`` discriminator selects the semantics; ``__post_init__`` enforces the
# per-kind invariants (closed allowlists and operand arity). The grammar is
# recursive through ``children`` (a tuple of Expression nodes).

ALLOWED_EXPR_KINDS: frozenset[str] = frozenset(
    {
        "literal",
        "field_ref",
        "parameter_ref",
        "measure_ref",
        "principal",
        "unary",
        "binary",
        "func",
        "agg",
        "modify",
        "conditional",
        "lod",
        "window",
        "lookup_map",
        "set_membership",
        "opaque",
    }
)
ALLOWED_UNARY_OPS: frozenset[str] = frozenset({"neg", "not"})
ALLOWED_BINARY_OPS: frozenset[str] = frozenset(
    {
        "add",
        "sub",
        "mul",
        "div",
        "concat",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and",
        "or",
    }
)
ALLOWED_SCALAR_FUNCS: frozenset[str] = frozenset(
    {
        "abs",
        "round",
        "floor",
        "ceil",
        "coalesce",
        "is_blank",
        "lower",
        "upper",
        "substring",
        "length",
        "trim",
        "year",
        "month",
        "day",
        "quarter",
        "date_add",
        "date_diff",
        "today",
        "now",
        "cast",
        "contains",
        "regexp_extract",
        "regexp_replace",
        "split",
        "to_string",
    }
)
ALLOWED_AGG_FUNCS: frozenset[str] = frozenset(
    {"sum", "avg", "count", "count_distinct", "min", "max", "median"}
)
ALLOWED_LOD_NAMES: frozenset[str] = frozenset({"fixed", "include", "exclude"})
ALLOWED_WINDOW_FUNCS: frozenset[str] = frozenset({"lookup", "min", "max", "sum", "size"})

_OPAQUE_DAX_FORBIDDEN_LEAD_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:select|insert|update|delete|create|alter|drop|truncate|merge|grant|revoke|"
    r"execute|exec|pragma|attach|detach|vacuum|import|from|def|class|lambda|print|eval)\b",
    re.IGNORECASE,
)
"""Obvious non-DAX code carriers rejected by the narrow opaque DAX escape hatch."""


@dataclass(frozen=True)
class Expression:
    """Neutral, typed, recursive expression node (closed grammar).

    Per-kind semantics (enforced in ``__post_init__``):

    * ``literal``: a scalar ``value``; ``children`` empty.
    * ``field_ref``: references ``entity.name``; ``children`` empty. An empty
      ``entity`` denotes an unqualified reference resolved later by a consumer.
    * ``parameter_ref`` / ``measure_ref``: references ``name``; no children.
    * ``principal``: the executing user identifier; optional ``name`` selects
      an identity attribute supported by the trusted runtime, never a query parameter.
    * ``unary``: one child; ``op`` in :data:`ALLOWED_UNARY_OPS`.
    * ``binary``: two children; ``op`` in :data:`ALLOWED_BINARY_OPS`.
    * ``func``: ``name`` in :data:`ALLOWED_SCALAR_FUNCS`; children are arguments
      (zero or more, e.g. ``today`` takes none).
    * ``agg``: ``name`` in :data:`ALLOWED_AGG_FUNCS`; ``children[0]`` is the
      aggregated expression and the optional ``children[1]`` is a boolean
      filter predicate applied within the aggregation.
    * ``modify``: evaluate ``children[0]`` under the boolean filter predicates
      in ``children[1:]`` (neutral analog of a modified filter context).
    * ``aggregate_ref`` / ``window``: window/table-calc functions; ``window``
      may carry a Mapping ``value`` holding typed table-calc metadata.
    * ``set_membership``: ``name`` references a set parameter (``is_set=True``),
      ``entity`` the target field, and ``value`` a derived snapshot of selected
      members normalized to a tuple (``None`` when the selection is unknown).
    * ``opaque``: a lossless source-language expression that the neutral grammar
      cannot represent. Only ``name="dax"`` is allowed; ``value`` is the non-empty
      DAX body and ``children`` is empty. Consumers that cannot execute DAX must
      fail explicitly rather than reinterpret the payload.

    Attributes:
        kind: node discriminator (in :data:`ALLOWED_EXPR_KINDS`).
        value: scalar payload for ``literal`` (str/int/float/bool/None), mapping
            payload for ``lookup_map``/``window``, set-membership snapshot, or
            None.
        data_type: neutral :class:`DataType` (for ``literal`` and casts).
        name: referenced name (field / parameter / measure / function / agg).
        entity: entity qualifier for ``field_ref`` ("" if unqualified).
        op: operator token for ``unary`` / ``binary``.
        distinct: explicit distinct flag for ``agg`` (for display only).
        children: ordered operand expressions.
    """

    kind: str
    value: object = None
    data_type: DataType = DataType.UNKNOWN
    name: str = ""
    entity: str = ""
    op: str = ""
    distinct: bool = False
    children: tuple[Expression, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in ALLOWED_EXPR_KINDS:
            raise ValueError(
                f"expression kind not allowed: {self.kind!r}; "
                f"allowlist={sorted(ALLOWED_EXPR_KINDS)}"
            )
        if not isinstance(self.children, tuple):
            object.__setattr__(self, "children", tuple(self.children))
        for child in self.children:
            if not isinstance(child, Expression):
                raise TypeError("expression children must be Expression nodes")
        if not isinstance(self.data_type, DataType):
            object.__setattr__(self, "data_type", _coerce_data_type(self.data_type))

        k = self.kind
        if k == "literal":
            if self.children:
                raise ValueError("literal must have no children")
            if self.value is not None and not isinstance(self.value, (bool, int, float, str)):
                raise ValueError(
                    f"literal value must be a scalar (bool, int, float, str), got {type(self.value).__name__}"
                )
        elif k == "lookup_map":
            if not self.name or not isinstance(self.value, Mapping) or self.children:
                raise ValueError("lookup_map requires a source field name and mapping value")
            for mk, mv in self.value.items():
                if not isinstance(mk, (bool, int, float, str)) or (
                    mv is not None and not isinstance(mv, (bool, int, float, str))
                ):
                    raise ValueError("lookup_map keys and values must be scalars")
        elif k == "set_membership":
            if not self.name or not self.entity or self.children:
                raise ValueError("set_membership requires set name and target field")
            if self.value is not None:
                if not isinstance(self.value, (list, tuple)):
                    raise ValueError(
                        f"set_membership value must be a list or tuple of scalars, got {type(self.value).__name__}"
                    )
                for item in self.value:
                    if item is not None and not isinstance(item, (bool, int, float, str)):
                        raise ValueError(
                            f"set_membership value items must be scalars, got {type(item).__name__}"
                        )
                if isinstance(self.value, list):
                    object.__setattr__(self, "value", tuple(self.value))
        elif k == "window":
            if self.value is not None and not isinstance(self.value, Mapping):
                raise ValueError(
                    f"window value must be a mapping of table-calc metadata or None, got {type(self.value).__name__}"
                )
        elif k == "opaque":
            if self.name != "dax":
                raise ValueError("opaque expression language must be 'dax'")
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("opaque dax expression requires a non-empty string value")
            if _OPAQUE_DAX_FORBIDDEN_LEAD_RE.search(self.value):
                raise ValueError("opaque dax expression must not carry SQL/Python source")
            if self.children:
                raise ValueError("opaque dax expression must have no children")
        elif self.value is not None:
            raise ValueError(f"{k} expression must not have a value")

        if k in {"field_ref", "parameter_ref", "measure_ref"}:
            if not self.name:
                raise ValueError(f"{k} requires name")
            if self.children:
                raise ValueError(f"{k} must have no children")

        elif k == "principal":
            if self.children:
                raise ValueError("principal must have no children")
        elif k == "unary":
            if self.op not in ALLOWED_UNARY_OPS:
                raise ValueError(
                    f"unary op not allowed: {self.op!r}; allowlist={sorted(ALLOWED_UNARY_OPS)}"
                )
            if len(self.children) != 1:
                raise ValueError("unary requires exactly one child")
        elif k == "binary":
            if self.op not in ALLOWED_BINARY_OPS:
                raise ValueError(
                    f"binary op not allowed: {self.op!r}; allowlist={sorted(ALLOWED_BINARY_OPS)}"
                )
            if len(self.children) != 2:
                raise ValueError("binary requires exactly two children")
        elif k == "func":
            if self.name not in ALLOWED_SCALAR_FUNCS:
                raise ValueError(
                    f"scalar function not allowed: {self.name!r}; "
                    f"allowlist={sorted(ALLOWED_SCALAR_FUNCS)}"
                )
        elif k == "agg":
            if self.name not in ALLOWED_AGG_FUNCS:
                raise ValueError(
                    f"aggregation function not allowed: {self.name!r}; "
                    f"allowlist={sorted(ALLOWED_AGG_FUNCS)}"
                )
            if not (1 <= len(self.children) <= 2):
                raise ValueError("agg requires one or two children (expression [, filter])")
        elif k == "modify":
            if len(self.children) < 1:
                raise ValueError("modify requires a body expression")
        elif k == "conditional":
            if len(self.children) != 3:
                raise ValueError("conditional requires condition, true and false expressions")
        elif k == "lod":
            if self.name not in ALLOWED_LOD_NAMES:
                raise ValueError(
                    f"lod name not allowed: {self.name!r}; allowlist={sorted(ALLOWED_LOD_NAMES)}"
                )
            if not self.children:
                raise ValueError("lod requires a body expression")
        elif k == "window":
            if self.name not in ALLOWED_WINDOW_FUNCS:
                raise ValueError(
                    f"window function not allowed: {self.name!r}; "
                    f"allowlist={sorted(ALLOWED_WINDOW_FUNCS)}"
                )


def walk_expression(expr: Expression):
    """Yield ``expr`` and every nested child expression (depth-first, pre-order).

    Public helper so consumers (and :class:`SemanticModel` referential
    validation) can traverse an :class:`Expression` uniformly.
    """
    yield expr
    for child in expr.children:
        yield from walk_expression(child)


_BINARY_SYMBOLS: Mapping[str, str] = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "concat": "||",
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "and": "AND",
    "or": "OR",
}
_UNARY_SYMBOLS: Mapping[str, str] = {"neg": "-", "not": "NOT"}


def format_expression(expr: Expression) -> str:
    """Representa una expresión neutral para diagnóstico, nunca como código destino."""

    def render(node: Expression) -> str:
        if node.kind == "literal":
            return "NULL" if node.value is None else repr(node.value)
        if node.kind == "field_ref":
            return f"[{node.entity}.{node.name}]" if node.entity else f"[{node.name}]"
        if node.kind == "parameter_ref":
            return f"@{node.name}"
        if node.kind == "measure_ref":
            return f"{{{node.name}}}"
        if node.kind == "principal":
            return "CURRENT_PRINCIPAL"
        if node.kind == "opaque":
            return f"<opaque:{node.name}>"
        if node.kind == "unary":
            return f"({_UNARY_SYMBOLS.get(node.op, node.op)} {render(node.children[0])})"
        if node.kind == "binary":
            symbol = _BINARY_SYMBOLS.get(node.op, node.op)
            return f"({render(node.children[0])} {symbol} {render(node.children[1])})"
        if node.kind == "func":
            return f"{node.name.upper()}({', '.join(render(c) for c in node.children)})"
        if node.kind == "agg":
            head = "COUNT" if node.name == "count_distinct" else node.name.upper()
            distinct = "DISTINCT " if node.distinct or node.name == "count_distinct" else ""
            result = f"{head}({distinct}{render(node.children[0])})"
            return (
                f"{result} FILTER {render(node.children[1])}" if len(node.children) == 2 else result
            )
        if node.kind == "modify":
            body = render(node.children[0])
            filters = ", ".join(render(child) for child in node.children[1:])
            return f"({body} WITH FILTERS {filters})" if filters else f"({body})"
        return f"<unknown:{node.kind}>"

    return render(expr)


def _check_opaque_scope(expr: Expression, context: str, *, allow_metric_root: bool = False) -> None:
    """Keep opaque DAX confined to a whole metric body, never RLS/filter/subexpressions."""
    opaque_nodes = [node for node in walk_expression(expr) if node.kind == "opaque"]
    if not opaque_nodes:
        return
    if allow_metric_root and expr.kind == "opaque" and len(opaque_nodes) == 1:
        return
    raise ValueError(f"{context}: opaque DAX is only allowed as a whole metric expression")


def _check_expression_refs(
    expr: Expression,
    entity_fields: Mapping[str, frozenset[str]],
    measures: frozenset[str],
    params: frozenset[str],
    context: str,
    *,
    set_parameters: Mapping[str, Parameter] | None = None,
) -> None:
    """Validate that every reference in ``expr`` resolves against the model.

    Qualified ``field_ref`` nodes must point at a known entity and field.
    ``measure_ref`` and ``parameter_ref`` nodes must point at declared names.
    Unqualified field refs (``entity == ""``) are allowed and resolved later by
    a consumer.

    ``set_membership`` nodes must reference a declared set parameter
    (``is_set=True``), point at a known field, and (when the model set
    parameters are supplied) stay coherent with the canonical set selection:
    the derived snapshot ``value`` must agree with ``Parameter.default_value``
    (both ``None`` for an unknown/dynamic selection, both materialized and
    equal otherwise).
    """
    for node in walk_expression(expr):
        k = node.kind
        if k == "field_ref" and node.entity:
            known = entity_fields.get(node.entity)
            if known is None:
                raise ValueError(f"{context}: references unknown entity {node.entity!r}")
            if node.name not in known:
                raise ValueError(f"{context}: references unknown field {node.entity}.{node.name}")
        elif k == "lookup_map" and not any(
            node.name in fields for fields in entity_fields.values()
        ):
            raise ValueError(f"{context}: references unknown field {node.name!r}")
        elif k == "set_membership":
            if node.name not in params:
                raise ValueError(f"{context}: references unknown set parameter {node.name!r}")
            if not any(node.entity in fields for fields in entity_fields.values()):
                raise ValueError(f"{context}: references unknown set field {node.entity!r}")
            if set_parameters is not None:
                parameter = set_parameters.get(node.name)
                if parameter is None or not parameter.is_set:
                    raise ValueError(
                        f"{context}: set_membership {node.name!r} does not reference a set parameter (is_set=True)"
                    )
                if (node.value is None) != (parameter.default_value is None):
                    raise ValueError(
                        f"{context}: set_membership {node.name!r} selection snapshot disagrees "
                        f"with set parameter {node.name!r} default (snapshot={node.value!r}, "
                        f"default={parameter.default_value!r})"
                    )
                if node.value is not None and tuple(node.value) != tuple(parameter.default_value):
                    raise ValueError(
                        f"{context}: set_membership value snapshot {tuple(node.value)!r} diverges "
                        f"from set parameter {node.name!r} default {tuple(parameter.default_value)!r}"
                    )
        elif k == "measure_ref":
            if node.name not in measures:
                raise ValueError(f"{context}: references unknown measure {node.name!r}")
        elif k == "parameter_ref":
            if node.name not in params:
                raise ValueError(f"{context}: references unknown parameter {node.name!r}")


# --- Neutral model types --------------------------------------------------

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
CrossFilterDirection = Literal["single", "both"]

_VALID_CARDINALITY: frozenset[str] = frozenset(get_args(Cardinality))
_VALID_CROSS_FILTER: frozenset[str] = frozenset(get_args(CrossFilterDirection))


@dataclass(frozen=True)
class Field:
    """A column of a neutral :class:`Entity`.

    Attributes:
        name: logical field name (unique within its entity).
        data_type: neutral :class:`DataType`.
        is_key: True if the field participates in the entity's natural key.
        nullable: True if the field may hold NULL.
        hidden: True if the field is internal (not exposed for authoring).
        description: free-form documentation.
        source_column: neutral reference to the originating source column, if any.
    """

    name: str
    data_type: DataType = DataType.UNKNOWN
    is_key: bool = False
    nullable: bool = True
    hidden: bool = False
    description: str = ""
    source_column: str = ""
    expression: Expression | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Field.name is required")
        if not isinstance(self.data_type, DataType):
            object.__setattr__(self, "data_type", _coerce_data_type(self.data_type))
        if self.expression is not None and not isinstance(self.expression, Expression):
            raise TypeError("Field.expression must be Expression or None")


@dataclass(frozen=True)
class Grain:
    """The grain of an entity: the field set that uniquely identifies a row.

    Attributes:
        fields: non-empty list of field names (must exist on the owning entity).
        description: free-form documentation.
    """

    fields: list[str] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.fields:
            raise ValueError("Grain.fields must be non-empty")
        for f in self.fields:
            if not isinstance(f, str) or not f:
                raise ValueError("Grain.fields entries must be non-empty strings")


@dataclass(frozen=True)
class Entity:
    """A neutral entity (logical table) with fields and an optional grain.

    Attributes:
        name: unique entity name.
        fields: ordered list of :class:`Field` (names unique within the entity).
        grain: optional :class:`Grain`; when present its fields must exist here.
        description: free-form documentation.
        hidden: True if the entity is internal.
    """

    name: str
    fields: list[Field] = field(default_factory=list)
    grain: Grain | None = None
    description: str = ""
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Entity.name is required")
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate field names in entity {self.name!r}")
        if self.grain is not None:
            known = set(names)
            unknown = [g for g in self.grain.fields if g not in known]
            if unknown:
                raise ValueError(
                    f"grain references unknown field(s) {unknown} in entity {self.name!r}"
                )


@dataclass(frozen=True)
class Relationship:
    """A neutral relationship between two entities.

    Attributes:
        name: unique relationship name.
        from_entity / to_entity: related entity names.
        from_fields / to_fields: paired key columns (equal length, non-empty).
        cardinality: one of :data:`Cardinality`.
        cross_filter: neutral filter propagation (``"single"`` or ``"both"``).
        active: True if the relationship is active by default.
        description: free-form documentation.
    """

    name: str
    from_entity: str
    from_fields: list[str]
    to_entity: str
    to_fields: list[str]
    cardinality: Cardinality = "many_to_one"
    cross_filter: CrossFilterDirection = "single"
    active: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Relationship.name is required")
        if not self.from_entity or not self.to_entity:
            raise ValueError("from_entity and to_entity are required")
        if not self.from_fields or not self.to_fields:
            raise ValueError("from_fields and to_fields are required")
        if len(self.from_fields) != len(self.to_fields):
            raise ValueError(f"relationship {self.name!r}: from_fields/to_fields length mismatch")
        if self.cardinality not in _VALID_CARDINALITY:
            raise ValueError(f"invalid cardinality: {self.cardinality!r}")
        if self.cross_filter not in _VALID_CROSS_FILTER:
            raise ValueError(f"invalid cross_filter: {self.cross_filter!r}")


@dataclass(frozen=True)
class Metric:
    """A neutral metric (measure): a named expression evaluated over the model.

    Attributes:
        name: unique metric name.
        expression: :class:`Expression` that defines the metric value.
        data_type: neutral :class:`DataType` of the result.
        format_string: neutral format template (e.g. ``"#,##0.00"``).
        description: free-form documentation.
        hidden: True if the metric is internal.
    """

    name: str
    expression: Expression
    entity: str = ""
    data_type: DataType = DataType.UNKNOWN
    format_string: str = ""
    description: str = ""
    hidden: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Metric.name is required")
        if not isinstance(self.expression, Expression):
            raise ValueError("Metric.expression must be an Expression")
        if not isinstance(self.entity, str):
            raise TypeError("Metric.entity must be a string")
        if not isinstance(self.data_type, DataType):
            object.__setattr__(self, "data_type", _coerce_data_type(self.data_type))


@dataclass(frozen=True)
class Parameter:
    """A neutral parameter: a named, typed value with an optional default.

    Ordinary parameters hold one scalar value. Set parameters (``is_set=True``)
    carry the canonical set selection state:

    * ``default_value`` is a ``tuple`` of scalars (materialized selection),
      ``()`` for an explicitly empty selection, or ``None`` when the selection
      is unknown / non-materializable (e.g. a dynamic Top-N set).
    * ``data_type`` is the member type (not ``VARIANT``).
    * ``allowed_values`` holds the permitted member domain only.

    Ordinary parameters reject sequence defaults and set parameters reject
    non-sequence defaults, enforced in ``__post_init__``.

    Attributes:
        name: unique parameter name.
        data_type: neutral :class:`DataType`.
        is_set: True when the parameter represents a Tableau set selection.
        default_value: default scalar value for ordinary parameters; for set
            parameters the canonical tuple selection (see above).
        allowed_values: optional closed list of accepted values / member domain.
        expression: optional :class:`Expression` that derives the current value.
        description: free-form documentation.
    """

    name: str
    data_type: DataType
    is_set: bool = False
    default_value: object = None
    allowed_values: list[object] = field(default_factory=list)
    expression: Expression | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Parameter.name is required")
        if not isinstance(self.data_type, DataType):
            object.__setattr__(self, "data_type", _coerce_data_type(self.data_type))
        if self.is_set:
            if self.default_value is not None:
                if not isinstance(self.default_value, (list, tuple)):
                    raise ValueError(
                        f"set Parameter.default_value must be a tuple of scalars or None, got {type(self.default_value).__name__}"
                    )
                for val in self.default_value:
                    if val is not None and not isinstance(val, (bool, int, float, str)):
                        raise ValueError(
                            f"set Parameter.default_value items must be scalars (bool, int, float, str), got {type(val).__name__}"
                        )
                object.__setattr__(self, "default_value", tuple(self.default_value))
        else:
            if isinstance(self.default_value, (list, tuple)):
                raise ValueError(
                    "ordinary Parameter.default_value must be a scalar or None, not a sequence; "
                    "set parameters must declare is_set=True"
                )
            if self.default_value is not None and not isinstance(
                self.default_value, (bool, int, float, str)
            ):
                raise ValueError(
                    f"Parameter.default_value must be a scalar (bool, int, float, str) or None, got {type(self.default_value).__name__}"
                )
        for val in self.allowed_values:
            if val is not None and not isinstance(val, (bool, int, float, str)):
                raise ValueError(
                    f"Parameter.allowed_values must contain scalars (bool, int, float, str), got {type(val).__name__}"
                )
        if self.expression is not None and not isinstance(self.expression, Expression):
            raise ValueError("Parameter.expression must be Expression or None")



ALLOWED_FILTER_OPS: frozenset[str] = frozenset(
    {
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "between",
        "is_null",
        "is_not_null",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
    }
)

_VALUELESS_FILTER_OPS: frozenset[str] = frozenset({"is_null", "is_not_null"})
_TWO_VALUE_FILTER_OPS: frozenset[str] = frozenset({"between"})
_MULTI_VALUE_FILTER_OPS: frozenset[str] = frozenset({"in", "not_in"})


@dataclass(frozen=True)
class Filter:
    """A neutral filter applied to a field or measure target.

    Attributes:
        name: unique filter name.
        target: :class:`Expression` with kind ``field_ref`` or ``measure_ref``.
        operator: one of :data:`ALLOWED_FILTER_OPS`.
        values: operand scalars; count constrained by ``operator``.
        applies_to: neutral scope hints (report/page/visual/worksheet names).
        description: free-form documentation.
        viewer_hidden: when True, viewers are not shown this filter.
        viewer_locked: when True, viewers cannot change this filter.
    """

    name: str
    target: Expression
    operator: str
    values: list[object] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=list)
    description: str = ""
    viewer_hidden: bool = False
    viewer_locked: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Filter.name is required")
        if not isinstance(self.target, Expression):
            raise ValueError("Filter.target must be an Expression")
        if self.target.kind not in {"field_ref", "measure_ref"}:
            raise ValueError(
                f"Filter.target must be a field_ref or measure_ref, got {self.target.kind!r}"
            )
        if not isinstance(self.viewer_hidden, bool):
            raise ValueError("Filter.viewer_hidden must be a boolean")
        if not isinstance(self.viewer_locked, bool):
            raise ValueError("Filter.viewer_locked must be a boolean")
        if self.operator not in ALLOWED_FILTER_OPS:
            raise ValueError(
                f"filter operator not allowed: {self.operator!r}; "
                f"allowlist={sorted(ALLOWED_FILTER_OPS)}"
            )
        count = len(self.values)
        for val in self.values:
            if val is not None and not isinstance(val, (bool, int, float, str)):
                raise ValueError(
                    f"Filter.values must contain scalars (bool, int, float, str), got {type(val).__name__}"
                )
        op = self.operator

        if op in _VALUELESS_FILTER_OPS:
            if count != 0:
                raise ValueError(f"{op} takes no values")
        elif op in _TWO_VALUE_FILTER_OPS:
            if count != 2:
                raise ValueError(f"{op} requires exactly two values")
        elif op in _MULTI_VALUE_FILTER_OPS:
            if count < 1:
                raise ValueError(f"{op} requires at least one value")
        elif count != 1:
            raise ValueError(f"{op} requires exactly one value")


@dataclass(frozen=True)
class RLSIntent:
    """Neutral row-level security intent: restrict rows of ``entity`` by ``expression``.

    The ``expression`` must evaluate to a boolean over the entity's fields and
    MAY reference :class:`Expression` node ``principal`` (the current user).

    Attributes:
        name: unique RLS intent name.
        entity: the entity whose rows are restricted.
        expression: boolean :class:`Expression` defining the row policy.
        roles: list of role names the policy applies to.
        description: free-form documentation.
    """

    name: str
    entity: str
    expression: Expression
    roles: list[str] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("RLSIntent.name is required")
        if not self.entity:
            raise ValueError("RLSIntent.entity is required")
        if not isinstance(self.expression, Expression):
            raise ValueError("RLSIntent.expression must be an Expression")


@dataclass(frozen=True)
class SemanticModel:
    """Root of the neutral, target-agnostic semantic model.

    Immutable (``frozen=True``). Construction validates: schema version,
    uniqueness of entity / metric / parameter / filter / relationship / rls
    intent names, relationship endpoint resolution, and referential integrity
    of every expression (field / measure / parameter references).

    Serialization is JSON-canonical via :meth:`to_json` / :meth:`from_json`,
    which round-trip the whole model (including nested expressions).
    """

    schema_version: str
    name: str
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    rls_intents: list[RLSIntent] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {MODEL_SCHEMA_VERSION!r}, "
                f"received {self.schema_version!r}"
            )
        if not self.name:
            raise ValueError("SemanticModel.name is required")
        self._check_unique("entity", [e.name for e in self.entities])
        self._check_unique("metric", [m.name for m in self.metrics])
        self._check_unique("parameter", [p.name for p in self.parameters])
        self._check_unique("filter", [f.name for f in self.filters])
        self._check_unique("relationship", [r.name for r in self.relationships])
        self._check_unique("rls_intent", [r.name for r in self.rls_intents])
        self._check_relationships()
        self._check_rls_entities()
        self._check_expression_refs()

    @staticmethod
    def _check_unique(label: str, names: list[str]) -> None:
        seen: set[str] = set()
        for n in names:
            if n in seen:
                raise ValueError(f"duplicate {label} name: {n!r}")
            seen.add(n)

    def _check_relationships(self) -> None:
        entities = {e.name for e in self.entities}
        for rel in self.relationships:
            if rel.from_entity not in entities:
                raise ValueError(
                    f"relationship {rel.name!r}: unknown from_entity {rel.from_entity!r}"
                )
            if rel.to_entity not in entities:
                raise ValueError(f"relationship {rel.name!r}: unknown to_entity {rel.to_entity!r}")

    def _check_rls_entities(self) -> None:
        entities = {e.name for e in self.entities}
        for rls in self.rls_intents:
            if rls.entity not in entities:
                raise ValueError(f"rls_intent {rls.name!r}: unknown entity {rls.entity!r}")

    def _check_expression_refs(self) -> None:
        entity_fields: dict[str, frozenset[str]] = {
            e.name: frozenset(f.name for f in e.fields) for e in self.entities
        }
        measures = frozenset(m.name for m in self.metrics)
        params = frozenset(p.name for p in self.parameters)
        set_parameters = {p.name: p for p in self.parameters if p.is_set}
        for m in self.metrics:
            if m.entity and m.entity not in entity_fields:
                raise ValueError(f"metric {m.name!r}: unknown entity {m.entity!r}")
            _check_opaque_scope(m.expression, f"metric {m.name!r}", allow_metric_root=True)
            _check_expression_refs(
                m.expression,
                entity_fields,
                measures,
                params,
                f"metric {m.name!r}",
                set_parameters=set_parameters,
            )
        for entity in self.entities:
            for model_field in entity.fields:
                if model_field.expression is not None:
                    _check_opaque_scope(
                        model_field.expression, f"field {entity.name}.{model_field.name}"
                    )
                    _check_expression_refs(
                        model_field.expression,
                        entity_fields,
                        measures,
                        params,
                        f"field {entity.name}.{model_field.name}",
                        set_parameters=set_parameters,
                    )
        for p in self.parameters:
            if p.expression is not None:
                _check_opaque_scope(p.expression, f"parameter {p.name!r}")
                _check_expression_refs(
                    p.expression,
                    entity_fields,
                    measures,
                    params,
                    f"parameter {p.name!r}",
                    set_parameters=set_parameters,
                )
        for f in self.filters:
            _check_opaque_scope(f.target, f"filter {f.name!r}")
            _check_expression_refs(
                f.target,
                entity_fields,
                measures,
                params,
                f"filter {f.name!r}",
                set_parameters=set_parameters,
            )
        for r in self.rls_intents:
            _check_opaque_scope(r.expression, f"rls_intent {r.name!r}")
            _check_expression_refs(
                r.expression,
                entity_fields,
                measures,
                params,
                f"rls_intent {r.name!r}",
                set_parameters=set_parameters,
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the model to a JSON-compatible ``dict`` (recursive)."""
        return _to_jsonable(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize the model to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemanticModel:
        """Rebuild a model from a ``dict``, validating ``schema_version``.

        Each sub-section is reconstructed through a typed helper so that
        unknown keys or malformed expressions raise ``ValueError`` / ``KeyError``
        instead of being silently accepted.
        """
        received = data.get("schema_version")
        if received != MODEL_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {MODEL_SCHEMA_VERSION!r}, "
                f"received {received!r}"
            )
        return cls(
            schema_version=MODEL_SCHEMA_VERSION,
            name=str(data["name"]),
            description=str(data.get("description", "")),
            entities=[_entity_from_dict(e) for e in data.get("entities", [])],
            relationships=[_relationship_from_dict(r) for r in data.get("relationships", [])],
            metrics=[_metric_from_dict(m) for m in data.get("metrics", [])],
            parameters=[_parameter_from_dict(p) for p in data.get("parameters", [])],
            filters=[_filter_from_dict(f) for f in data.get("filters", [])],
            rls_intents=[_rls_from_dict(r) for r in data.get("rls_intents", [])],
        )

    @classmethod
    def from_json(cls, text: str) -> SemanticModel:
        """Rebuild a model from JSON, validating ``schema_version``."""
        return cls.from_dict(json.loads(text))


# --- Serialization helpers ------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert contract objects to JSON-compatible primitives."""
    if isinstance(obj, DataType):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


_ALLOWED_EXPRESSION_KEYS = frozenset({
    "kind", "value", "data_type", "name", "entity", "op", "distinct", "children"
})
_ALLOWED_FIELD_KEYS = frozenset({
    "name", "data_type", "is_key", "nullable", "hidden", "description", "source_column", "expression"
})
_ALLOWED_GRAIN_KEYS = frozenset({"fields", "description"})
_ALLOWED_ENTITY_KEYS = frozenset({"name", "fields", "grain", "description", "hidden"})
_ALLOWED_RELATIONSHIP_KEYS = frozenset({
    "name", "from_entity", "from_fields", "to_entity", "to_fields",
    "cardinality", "cross_filter", "active", "description"
})
_ALLOWED_METRIC_KEYS = frozenset({
    "name", "expression", "entity", "data_type", "format_string", "description", "hidden"
})
_ALLOWED_PARAMETER_KEYS = frozenset({
    "name", "data_type", "is_set", "default_value", "allowed_values", "expression", "description"
})
_ALLOWED_FILTER_KEYS = frozenset({
    "name", "target", "operator", "values", "applies_to", "description",
    "viewer_hidden", "viewer_locked"
})
_ALLOWED_RLS_KEYS = frozenset({
    "name", "entity", "expression", "roles", "description"
})


def _expression_from_dict(data: Mapping[str, Any]) -> Expression:
    if not isinstance(data, Mapping):
        raise TypeError("expression must be an object")
    unknown = set(data) - _ALLOWED_EXPRESSION_KEYS
    if unknown:
        raise ValueError(f"unknown expression field(s): {sorted(unknown)}")
    children_raw = data.get("children", [])
    if not isinstance(children_raw, (list, tuple)):
        raise TypeError("expression children must be a list")
    for child in children_raw:
        if not isinstance(child, Mapping):
            raise TypeError("expression child must be an object")
    return Expression(
        kind=str(data["kind"]),
        value=data.get("value"),
        data_type=_coerce_data_type(data.get("data_type", DataType.UNKNOWN.value)),
        name=str(data.get("name", "")),
        entity=str(data.get("entity", "")),
        op=str(data.get("op", "")),
        distinct=bool(data.get("distinct", False)),
        children=tuple(_expression_from_dict(c) for c in children_raw),
    )


def _field_from_dict(data: Mapping[str, Any]) -> Field:
    if not isinstance(data, Mapping):
        raise TypeError("field must be an object")
    unknown = set(data) - _ALLOWED_FIELD_KEYS
    if unknown:
        raise ValueError(f"unknown field field(s): {sorted(unknown)}")
    return Field(
        name=str(data["name"]),
        data_type=_coerce_data_type(data.get("data_type", DataType.UNKNOWN.value)),
        is_key=bool(data.get("is_key", False)),
        nullable=bool(data.get("nullable", True)),
        hidden=bool(data.get("hidden", False)),
        description=str(data.get("description", "")),
        source_column=str(data.get("source_column", "")),
        expression=(
            _expression_from_dict(data["expression"])
            if data.get("expression") is not None
            else None
        ),
    )


def _grain_from_dict(data: Mapping[str, Any]) -> Grain:
    if not isinstance(data, Mapping):
        raise TypeError("grain must be an object")
    unknown = set(data) - _ALLOWED_GRAIN_KEYS
    if unknown:
        raise ValueError(f"unknown grain field(s): {sorted(unknown)}")
    raw_fields = data.get("fields", [])
    if not isinstance(raw_fields, (list, tuple)):
        raise TypeError("grain fields must be a list")
    for f in raw_fields:
        if not isinstance(f, str):
            raise TypeError("grain fields items must be strings")
    return Grain(
        fields=[str(f) for f in raw_fields],
        description=str(data.get("description", "")),
    )


def _entity_from_dict(data: Mapping[str, Any]) -> Entity:
    if not isinstance(data, Mapping):
        raise TypeError("entity must be an object")
    unknown = set(data) - _ALLOWED_ENTITY_KEYS
    if unknown:
        raise ValueError(f"unknown entity field(s): {sorted(unknown)}")
    grain = data.get("grain")
    fields_raw = data.get("fields", [])
    if not isinstance(fields_raw, (list, tuple)):
        raise TypeError("entity fields must be a list")
    return Entity(
        name=str(data["name"]),
        fields=[_field_from_dict(f) for f in fields_raw],
        grain=_grain_from_dict(grain) if grain else None,
        description=str(data.get("description", "")),
        hidden=bool(data.get("hidden", False)),
    )


def _relationship_from_dict(data: Mapping[str, Any]) -> Relationship:
    if not isinstance(data, Mapping):
        raise TypeError("relationship must be an object")
    unknown = set(data) - _ALLOWED_RELATIONSHIP_KEYS
    if unknown:
        raise ValueError(f"unknown relationship field(s): {sorted(unknown)}")
    from_fields_raw = data.get("from_fields", [])
    if not isinstance(from_fields_raw, (list, tuple)):
        raise TypeError("relationship from_fields must be a list")
    for f in from_fields_raw:
        if not isinstance(f, str):
            raise TypeError("relationship from_fields items must be strings")
    to_fields_raw = data.get("to_fields", [])
    if not isinstance(to_fields_raw, (list, tuple)):
        raise TypeError("relationship to_fields must be a list")
    for f in to_fields_raw:
        if not isinstance(f, str):
            raise TypeError("relationship to_fields items must be strings")
    return Relationship(
        name=str(data["name"]),
        from_entity=str(data["from_entity"]),
        from_fields=[str(f) for f in from_fields_raw],
        to_entity=str(data["to_entity"]),
        to_fields=[str(f) for f in to_fields_raw],
        cardinality=cast(Cardinality, str(data.get("cardinality", "many_to_one"))),
        cross_filter=cast(CrossFilterDirection, str(data.get("cross_filter", "single"))),
        active=bool(data.get("active", True)),
        description=str(data.get("description", "")),
    )


def _metric_from_dict(data: Mapping[str, Any]) -> Metric:
    if not isinstance(data, Mapping):
        raise TypeError("metric must be an object")
    unknown = set(data) - _ALLOWED_METRIC_KEYS
    if unknown:
        raise ValueError(f"unknown metric field(s): {sorted(unknown)}")
    expr_raw = data.get("expression")
    if not isinstance(expr_raw, Mapping):
        raise TypeError("metric expression must be an object")
    return Metric(
        name=str(data["name"]),
        expression=_expression_from_dict(expr_raw),
        entity=str(data.get("entity", "")),
        data_type=_coerce_data_type(data.get("data_type", DataType.UNKNOWN.value)),
        format_string=str(data.get("format_string", "")),
        description=str(data.get("description", "")),
        hidden=bool(data.get("hidden", False)),
    )


def _parameter_from_dict(data: Mapping[str, Any]) -> Parameter:
    if not isinstance(data, Mapping):
        raise TypeError("parameter must be an object")
    unknown = set(data) - _ALLOWED_PARAMETER_KEYS
    if unknown:
        raise ValueError(f"unknown parameter field(s): {sorted(unknown)}")
    expression = data.get("expression")
    if expression is not None and not isinstance(expression, Mapping):
        raise TypeError("parameter expression must be an object or null")
    is_set = bool(data.get("is_set", False))
    default_val = data.get("default_value")
    if is_set:
        if default_val is not None:
            if not isinstance(default_val, (list, tuple)):
                raise ValueError(
                    f"set parameter default_value must be None or a list/tuple of scalars, got {type(default_val).__name__}"
                )
            for av in default_val:
                if av is not None and not isinstance(av, (bool, int, float, str)):
                    raise ValueError(
                        f"parameter default_value items must be scalars, got {type(av).__name__}"
                    )
            default_val = tuple(default_val)
    else:
        if default_val is not None:
            if isinstance(default_val, (list, tuple)):
                raise ValueError(
                    "ordinary parameter default_value must be a scalar or null, not a sequence; "
                    "set parameters must declare is_set=True"
                )
            if not isinstance(default_val, (bool, int, float, str)):
                raise ValueError(
                    f"parameter default_value must be a scalar or null, got {type(default_val).__name__}"
                )
    allowed_vals_raw = data.get("allowed_values", [])
    if not isinstance(allowed_vals_raw, (list, tuple)):
        raise TypeError("parameter allowed_values must be a list")
    for av in allowed_vals_raw:
        if av is not None and not isinstance(av, (bool, int, float, str)):
            raise ValueError(
                f"parameter allowed_values items must be scalars, got {type(av).__name__}"
            )
    return Parameter(
        name=str(data["name"]),
        data_type=_coerce_data_type(str(data["data_type"])),
        is_set=is_set,
        default_value=default_val,
        allowed_values=list(allowed_vals_raw),
        expression=_expression_from_dict(expression) if expression else None,
        description=str(data.get("description", "")),
    )


def _filter_from_dict(data: Mapping[str, Any]) -> Filter:
    if not isinstance(data, Mapping):
        raise TypeError("filter must be an object")
    unknown = set(data) - _ALLOWED_FILTER_KEYS
    if unknown:
        raise ValueError(f"unknown filter field(s): {sorted(unknown)}")
    target_raw = data.get("target")
    if not isinstance(target_raw, Mapping):
        raise TypeError("filter target must be an object")
    vals_raw = data.get("values", [])
    if not isinstance(vals_raw, (list, tuple)):
        raise TypeError("filter values must be a list")
    for v in vals_raw:
        if v is not None and not isinstance(v, (bool, int, float, str)):
            raise ValueError(f"filter values items must be scalars, got {type(v).__name__}")
    applies_raw = data.get("applies_to", [])
    if not isinstance(applies_raw, (list, tuple)):
        raise TypeError("filter applies_to must be a list")
    for a in applies_raw:
        if not isinstance(a, str):
            raise TypeError("filter applies_to items must be strings")
    for flag in ("viewer_hidden", "viewer_locked"):
        raw = data.get(flag, False)
        if not isinstance(raw, bool):
            raise TypeError(f"filter {flag} must be a boolean")
    return Filter(
        name=str(data["name"]),
        target=_expression_from_dict(target_raw),
        operator=str(data["operator"]),
        values=list(vals_raw),
        applies_to=[str(a) for a in applies_raw],
        description=str(data.get("description", "")),
        viewer_hidden=bool(data.get("viewer_hidden", False)),
        viewer_locked=bool(data.get("viewer_locked", False)),
    )


def _rls_from_dict(data: Mapping[str, Any]) -> RLSIntent:
    if not isinstance(data, Mapping):
        raise TypeError("rls_intent must be an object")
    unknown = set(data) - _ALLOWED_RLS_KEYS
    if unknown:
        raise ValueError(f"unknown rls_intent field(s): {sorted(unknown)}")
    expr_raw = data.get("expression")
    if not isinstance(expr_raw, Mapping):
        raise TypeError("rls_intent expression must be an object")
    roles_raw = data.get("roles", [])
    if not isinstance(roles_raw, (list, tuple)):
        raise TypeError("rls_intent roles must be a list")
    for r in roles_raw:
        if not isinstance(r, str):
            raise TypeError("rls_intent roles items must be strings")
    return RLSIntent(
        name=str(data["name"]),
        entity=str(data["entity"]),
        expression=_expression_from_dict(expr_raw),
        roles=[str(r) for r in roles_raw],
        description=str(data.get("description", "")),
    )



def semantic_item_from_dict(target: str, data: Mapping[str, Any]) -> object:
    """Decode one canonical semantic-section item for typed authoring boundaries.

    This is the public, closed dispatcher used by HTTP/MCP authoring adapters so
    they do not duplicate the recursive SemanticModel decoding rules. It returns
    the exact typed contract accepted by ``core.authoring.operations.Operation``.
    """
    decoders = {
        "entity": (_entity_from_dict, Entity),
        "relationship": (_relationship_from_dict, Relationship),
        "metric": (_metric_from_dict, Metric),
        "parameter": (_parameter_from_dict, Parameter),
        "filter": (_filter_from_dict, Filter),
        "rls_intent": (_rls_from_dict, RLSIntent),
    }
    entry = decoders.get(target)
    if entry is None:
        raise ValueError(f"unknown semantic authoring target: {target!r}")
    if not isinstance(data, Mapping):
        raise TypeError("semantic authoring payload must be an object")
    decoder, payload_type = entry
    unknown = set(data) - {contract_field.name for contract_field in fields(payload_type)}
    if unknown:
        raise ValueError(f"unknown {target} payload field(s): {sorted(unknown)}")
    return decoder(data)


# ===========================================================================
# Cross-language canonical contract (single mechanical source of truth)
# ===========================================================================
#
# Python owns the canonical IR contracts; TypeScript must not maintain a
# second hand-written list of kinds, operators or discriminants. The helpers
# below project the live contract constants and dataclasses into one
# versioned JSON descriptor that the TypeScript authoring contract embeds
# verbatim (like a lockfile: generated, versioned, never hand-edited).
#
# Regenerate the TypeScript embedding with:
#     uv run python -c "from core.contracts.semantic_ir import \
#         canonical_contract_json as j; print(j())"
# Verify an embedded copy (decoded from TypeScript) with:
#     verify_cross_language_contract(<decoded descriptor dict>)
#
# Version policy ("same-major", the single explicit policy on every
# boundary): a payload is accepted when its schema version is well-formed
# SemVer with the same MAJOR as the current contract; any other MAJOR or a
# malformed version is rejected. MINOR differences are additive-compatible
# in both directions because parsers on both sides ignore unknown optional
# keys. Python constructors remain exact-match: the authority only ever
# builds its own version, and the policy governs boundary acceptance only.

CROSS_LANGUAGE_CONTRACT_VERSION: str = "1.0.0"
"""Version of the descriptor shape itself (independent of any IR version)."""

VERSION_POLICY_RULE: str = "same-major"
"""Name of the explicit cross-language schema-version compatibility policy."""

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def check_additive_schema_version(
    received: object, current: str, *, label: str = "schema_version"
) -> str:
    """Enforce the ``same-major`` policy of ``received`` against ``current``.

    Returns ``received`` when acceptable. Raises ``ValueError`` when the
    version is malformed, not a string, or targets a different (unknown)
    MAJOR. Same-MAJOR payloads are accepted regardless of MINOR/PATCH: a
    higher MINOR means the producer added optional fields (ignored by this
    consumer), a lower MINOR means an older compatible document.
    """
    if not isinstance(received, str):
        raise ValueError(f"{label} must be a string, not {type(received).__name__}")
    match = _SEMVER_RE.match(received)
    if match is None:
        raise ValueError(f"{label} is not a valid semantic version: {received!r}")
    current_match = _SEMVER_RE.match(current)
    if current_match is None:
        raise ValueError(f"current {label} is not a valid semantic version: {current!r}")
    if match.group(1) != current_match.group(1):
        raise ValueError(
            f"{label} incompatible major: received {received!r}, current {current!r}; "
            f"policy {VERSION_POLICY_RULE!r} rejects unknown majors"
        )
    return received


def _expression_constraints() -> dict[str, dict[str, Any]]:
    """Per-kind structural constraints mirrored from ``Expression.__post_init__``.

    ``children`` is the exact operand-count rule; ``requires`` lists
    attributes that must be present and non-empty; ``op_enum`` / ``name_enum``
    name the allowlist that ``op`` / ``name`` must belong to. Consumed by
    generated boundary validators so the rules exist only here.
    """
    return {
        "literal": {"children": "none"},
        "field_ref": {"children": "none", "requires": ["name"]},
        "parameter_ref": {"children": "none", "requires": ["name"]},
        "measure_ref": {"children": "none", "requires": ["name"]},
        "principal": {"children": "none"},
        "unary": {"children": "one", "requires": ["op"], "op_enum": "unary_ops"},
        "binary": {"children": "two", "requires": ["op"], "op_enum": "binary_ops"},
        "func": {"children": "any", "requires": ["name"], "name_enum": "scalar_funcs"},
        "agg": {"children": "one_or_two", "requires": ["name"], "name_enum": "agg_funcs"},
        "modify": {"children": "one_or_more"},
        "conditional": {"children": "three"},
        "lod": {"children": "one_or_more", "requires": ["name"], "name_enum": "lod_names"},
        "window": {"children": "any", "requires": ["name"], "name_enum": "window_funcs"},
        "lookup_map": {"children": "none", "requires": ["name", "value"]},
        "set_membership": {"children": "none", "requires": ["name", "entity"]},
        "opaque": {"children": "none", "requires": ["name", "value"], "name_enum": "opaque_languages"},
    }


_CONTRACT_SECTIONS: tuple[tuple[str, type], ...] = (
    ("semantic_model", SemanticModel),
    ("entity", Entity),
    ("field", Field),
    ("grain", Grain),
    ("relationship", Relationship),
    ("metric", Metric),
    ("parameter", Parameter),
    ("filter", Filter),
    ("rls_intent", RLSIntent),
    ("expression", Expression),
)


def _section_field_schema(cls: type) -> dict[str, list[str]]:
    """Split a contract dataclass into required (no default) and optional fields."""
    required: list[str] = []
    optional: list[str] = []
    for f in fields(cls):
        has_default = f.default is not MISSING or f.default_factory is not MISSING
        (optional if has_default else required).append(f.name)
    return {"required": required, "optional": optional}


def _filter_value_rules() -> dict[str, list[str]]:
    """Operand-count rules per filter operator, derived from the live sets."""
    exactly_one = (
        ALLOWED_FILTER_OPS - _VALUELESS_FILTER_OPS - _TWO_VALUE_FILTER_OPS - _MULTI_VALUE_FILTER_OPS
    )
    return {
        "none": sorted(_VALUELESS_FILTER_OPS),
        "exactly_one": sorted(exactly_one),
        "exactly_two": sorted(_TWO_VALUE_FILTER_OPS),
        "at_least_one": sorted(_MULTI_VALUE_FILTER_OPS),
    }


def build_cross_language_contract() -> dict[str, Any]:
    """Project the live Python contracts into the canonical descriptor.

    Every vocabulary is derived from the constants and dataclasses the
    constructors actually enforce; nothing is restated by hand. Canonical
    authoring operation discriminants are read from the authoring modules
    through a lazy, derivation-only import (a module-level import would be
    circular because ``core.authoring`` imports this module).
    """
    # ponytail: lazy import inverts contracts->authoring only inside this
    # derivation helper; if the descriptor grows to need authoring types
    # structurally, move the canonical op allowlists down into contracts.
    from core.authoring.interaction_operations import (
        VALID_INTERACTION_OP_KINDS,
        VALID_INTERACTION_TARGETS,
    )
    from core.authoring.operations import VALID_OP_KINDS, VALID_TARGET_KINDS
    from core.authoring.presentation_operations import (
        VALID_PRESENTATION_OP_KINDS,
        VALID_PRESENTATION_TARGETS,
    )
    from core.contracts import interaction_ir, presentation_ir

    return {
        "contract_version": CROSS_LANGUAGE_CONTRACT_VERSION,
        "version_policy": {
            "rule": VERSION_POLICY_RULE,
            "unknown_major": "reject",
            "additive_minor": "accept",
        },
        "semantic": {
            "schema_version": MODEL_SCHEMA_VERSION,
            "data_types": sorted(d.value for d in DataType),
            "expression": {
                "kinds": sorted(ALLOWED_EXPR_KINDS),
                "unary_ops": sorted(ALLOWED_UNARY_OPS),
                "binary_ops": sorted(ALLOWED_BINARY_OPS),
                "scalar_funcs": sorted(ALLOWED_SCALAR_FUNCS),
                "agg_funcs": sorted(ALLOWED_AGG_FUNCS),
                "lod_names": sorted(ALLOWED_LOD_NAMES),
                "window_funcs": sorted(ALLOWED_WINDOW_FUNCS),
                "opaque_languages": ["dax"],
                "constraints": _expression_constraints(),
            },
            "filter_operators": sorted(ALLOWED_FILTER_OPS),
            "filter_value_rules": _filter_value_rules(),
            "cardinalities": sorted(get_args(Cardinality)),
            "cross_filter_directions": sorted(get_args(CrossFilterDirection)),
            "sections": {name: _section_field_schema(cls) for name, cls in _CONTRACT_SECTIONS},
        },
        "presentation": {
            "schema_version": presentation_ir.SCHEMA_VERSION,
            "visual_intents": sorted(k.value for k in presentation_ir.VisualIntentKind),
            "field_roles": sorted(r.value for r in presentation_ir.FieldRole),
            "layout_modes": sorted(m.value for m in presentation_ir.ContainerKind),
        },
        "interaction": {
            "schema_version": interaction_ir.SCHEMA_VERSION,
            "selection_modes": sorted(m.value for m in interaction_ir.SelectionMode),
            "filter_operators": sorted(op.value for op in interaction_ir.FilterOperator),
            "filter_scopes": sorted(s.value for s in interaction_ir.FilterScope),
            "cross_filter_behaviors": sorted(b.value for b in interaction_ir.CrossFilterBehavior),
            "tooltip_kinds": sorted(k.value for k in interaction_ir.TooltipKind),
            "navigation_kinds": sorted(k.value for k in interaction_ir.NavigationKind),
        },
        "operations": {
            "semantic": {
                "kinds": sorted(VALID_OP_KINDS),
                "targets": sorted(VALID_TARGET_KINDS),
                "id_field": "name",
            },
            "presentation": {
                "kinds": sorted(VALID_PRESENTATION_OP_KINDS),
                "targets": sorted(VALID_PRESENTATION_TARGETS),
                "id_field": "page_id|visual_id (+page_id for visual)",
            },
            "interaction": {
                "kinds": sorted(VALID_INTERACTION_OP_KINDS),
                "targets": sorted(VALID_INTERACTION_TARGETS),
                "id_field": "page_id|filter_id",
            },
        },
    }


def canonical_contract_json(*, indent: int | None = 2) -> str:
    """Canonical JSON of the descriptor (sorted keys, UTF-8 pure)."""
    return json.dumps(
        build_cross_language_contract(), indent=indent, sort_keys=True, ensure_ascii=False
    )


def contract_checksum() -> str:
    """SHA-256 fingerprint of the compact canonical descriptor JSON."""
    payload = json.dumps(build_cross_language_contract(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_known_subset(expected: Mapping[str, Any], received: Any, path: str) -> None:
    """Require every canonical key/value to be present and equal in ``received``.

    Extra keys in ``received`` are additive extensions and are ignored; lists
    must match exactly (canonical lists are sorted, so order matters).
    """
    if isinstance(expected, Mapping):
        if not isinstance(received, Mapping):
            raise ValueError(f"cross-language contract drift at {path}: expected an object")
        for key, value in expected.items():
            if key not in received:
                raise ValueError(f"cross-language contract drift at {path}.{key}: missing")
            _assert_known_subset(value, received[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if not isinstance(received, list) or list(expected) != received:
            raise ValueError(f"cross-language contract drift at {path}: list mismatch")
    elif expected != received:
        raise ValueError(f"cross-language contract drift at {path}: value mismatch")


def verify_cross_language_contract(data: Mapping[str, Any]) -> None:
    """Verify an embedded descriptor (e.g. the TypeScript copy) against Python.

    Fails fast with ``ValueError`` on any drift from the live contracts.
    Extra unknown keys are tolerated only under the same-major policy (they
    are additive extensions published by a newer authority).
    """
    check_additive_schema_version(
        data.get("contract_version"),
        CROSS_LANGUAGE_CONTRACT_VERSION,
        label="contract_version",
    )
    _assert_known_subset(build_cross_language_contract(), data, "contract")


def build_contract_fixture() -> dict[str, Any]:
    """Canonical ``SemanticModel`` payload covering every expression kind/field.

    Exercises all expression kinds (including ``conditional``, ``lod``,
    ``window``, ``lookup_map`` and ``set_membership``), ``Field.expression``
    (calculated column), ``Metric.entity`` and every model section, so
    boundary validators on both languages check the complete grammar instead
    of a happy subset.
    """
    amount = Expression(kind="field_ref", entity="Sales", name="SalesAmount")
    order_id = Expression(kind="field_ref", entity="Sales", name="OrderID")
    region = Expression(kind="field_ref", entity="Sales", name="Region")
    west = Expression(kind="literal", value="West", data_type=DataType.STRING)
    model = SemanticModel(
        schema_version=MODEL_SCHEMA_VERSION,
        name="Cross Language Contract Fixture",
        description="Mechanical coverage fixture for boundary validators",
        entities=[
            Entity(
                name="Sales",
                fields=[
                    Field("OrderID", DataType.INTEGER, is_key=True, nullable=False),
                    Field("SalesAmount", DataType.DECIMAL),
                    Field("OrderDate", DataType.DATE),
                    Field("Region", DataType.STRING),
                    Field("RegionCode", DataType.STRING),
                    Field(
                        "DiscountedAmount",
                        DataType.DECIMAL,
                        description="calculated column coverage",
                        expression=Expression(
                            kind="binary",
                            op="mul",
                            children=(
                                amount,
                                Expression(kind="literal", value=0.9, data_type=DataType.DECIMAL),
                            ),
                        ),
                    ),
                ],
                grain=Grain(fields=["OrderID"]),
            ),
            Entity(
                name="Customer",
                fields=[
                    Field("CustomerKey", DataType.INTEGER, is_key=True, nullable=False),
                    Field("Region", DataType.STRING),
                ],
                grain=Grain(fields=["CustomerKey"]),
            ),
        ],
        relationships=[
            Relationship(
                name="SalesToCustomer",
                from_entity="Sales",
                from_fields=["OrderID"],
                to_entity="Customer",
                to_fields=["CustomerKey"],
                cardinality="many_to_one",
            )
        ],
        metrics=[
            Metric(
                name="Total Sales",
                expression=Expression(kind="agg", name="sum", children=(amount,)),
                data_type=DataType.DECIMAL,
                format_string="#,##0.00",
            ),
            Metric(
                name="West Sales",
                expression=Expression(
                    kind="agg",
                    name="sum",
                    children=(
                        amount,
                        Expression(kind="binary", op="eq", children=(region, west)),
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Avg Order Size",
                expression=Expression(
                    kind="binary",
                    op="div",
                    children=(
                        Expression(kind="measure_ref", name="Total Sales"),
                        Expression(kind="agg", name="count", children=(order_id,)),
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Negated Variance",
                expression=Expression(
                    kind="unary",
                    op="neg",
                    children=(
                        Expression(
                            kind="func",
                            name="abs",
                            children=(
                                Expression(
                                    kind="binary",
                                    op="sub",
                                    children=(
                                        amount,
                                        Expression(
                                            kind="literal", value=100, data_type=DataType.INTEGER
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                data_type=DataType.DECIMAL,
                hidden=True,
            ),
            Metric(
                name="Priority Sales",
                expression=Expression(
                    kind="conditional",
                    children=(
                        Expression(
                            kind="binary",
                            op="lt",
                            children=(
                                amount,
                                Expression(kind="parameter_ref", name="Sales Floor"),
                            ),
                        ),
                        Expression(kind="measure_ref", name="Total Sales"),
                        Expression(kind="literal", value=0, data_type=DataType.INTEGER),
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Region Fixed Sales",
                entity="Sales",
                expression=Expression(
                    kind="lod",
                    name="fixed",
                    children=(
                        Expression(kind="agg", name="sum", children=(amount,)),
                        region,
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Trend Lookup",
                expression=Expression(
                    kind="window",
                    name="lookup",
                    children=(
                        Expression(kind="measure_ref", name="Total Sales"),
                        Expression(kind="literal", value=1, data_type=DataType.INTEGER),
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Adjusted Total",
                expression=Expression(
                    kind="modify",
                    children=(
                        Expression(kind="measure_ref", name="Total Sales"),
                        Expression(kind="binary", op="eq", children=(region, west)),
                    ),
                ),
                data_type=DataType.DECIMAL,
            ),
            Metric(
                name="Region Label",
                expression=Expression(
                    kind="lookup_map", name="RegionCode", value={"N": "Norte", "S": "Sur"}
                ),
                data_type=DataType.STRING,
            ),
            Metric(
                name="In Target Set",
                expression=Expression(kind="set_membership", name="RegionSet", entity="Region", value=()),
                data_type=DataType.BOOLEAN,
            ),
            Metric(
                name="Opaque DAX Fixture",
                expression=Expression(kind="opaque", name="dax", value="COUNTROWS('Sales')"),
                data_type=DataType.INTEGER,
            ),
        ],
        parameters=[
            Parameter(name="Sales Floor", data_type=DataType.DECIMAL, default_value=0),
            Parameter(name="RegionSet", data_type=DataType.STRING, is_set=True, default_value=()),
            Parameter(
                name="Run Marker",
                data_type=DataType.DATE,
                expression=Expression(kind="func", name="today"),
            ),
        ],
        filters=[
            Filter(
                name="High Sales",
                target=Expression(kind="measure_ref", name="Total Sales"),
                operator="gt",
                values=[1000],
            ),
            Filter(
                name="Region Filter",
                target=region,
                operator="in",
                values=["West", "East"],
                applies_to=["page:1"],
            ),
        ],
        rls_intents=[
            RLSIntent(
                name="Customer Region Policy",
                entity="Customer",
                expression=Expression(
                    kind="binary",
                    op="or",
                    children=(
                        Expression(kind="set_membership", name="RegionSet", entity="Region", value=()),
                        Expression(
                            kind="binary",
                            op="eq",
                            children=(
                                Expression(kind="field_ref", entity="Customer", name="Region"),
                                Expression(kind="principal"),
                            ),
                        ),
                    ),
                ),
                roles=["Sales"],
            )
        ],
    )
    return model.to_dict()


def run_contract_self_check() -> None:
    """Runnable, assertion-based proof that the contract projection is sound.

    Execute with:
        uv run python -c "from core.contracts.semantic_ir import \
            run_contract_self_check as s; s()"
    Covers: canonical JSON stability, descriptor verification (positive and
    tampered), full expression-kind coverage of the fixture, canonical
    round-trip stability, and the explicit same-major version policy
    (additive minors accepted; unknown majors and malformed versions
    rejected).
    """
    contract = build_cross_language_contract()
    assert json.loads(canonical_contract_json()) == contract
    assert contract["semantic"]["expression"]["kinds"] == sorted(ALLOWED_EXPR_KINDS)

    fixture = build_contract_fixture()
    rebuilt = SemanticModel.from_dict(fixture)
    assert rebuilt.to_dict() == fixture, "canonical representation is not round-trip stable"

    kinds_in_fixture: set[str] = set()
    expressions = [m.expression for m in rebuilt.metrics]
    expressions += [f.expression for e in rebuilt.entities for f in e.fields if f.expression]
    expressions += [p.expression for p in rebuilt.parameters if p.expression]
    expressions += [f.target for f in rebuilt.filters]
    expressions += [r.expression for r in rebuilt.rls_intents]
    for expression in expressions:
        kinds_in_fixture.update(node.kind for node in walk_expression(expression))
    missing = ALLOWED_EXPR_KINDS - kinds_in_fixture
    assert not missing, f"fixture does not cover expression kinds: {sorted(missing)}"

    verify_cross_language_contract(json.loads(canonical_contract_json()))
    tampered = json.loads(canonical_contract_json())
    tampered["semantic"]["data_types"] = ["bogus"]
    try:
        verify_cross_language_contract(tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("verify_cross_language_contract accepted a tampered descriptor")

    major, minor, patch = (int(part) for part in MODEL_SCHEMA_VERSION.split("."))
    check_additive_schema_version(MODEL_SCHEMA_VERSION, MODEL_SCHEMA_VERSION)
    check_additive_schema_version(f"{major}.{minor + 1}.{patch}", MODEL_SCHEMA_VERSION)
    check_additive_schema_version(f"{major}.{max(minor - 1, 0)}.{patch}", MODEL_SCHEMA_VERSION)
    for bad in (
        f"{major + 1}.0.0",
        f"{major + 1}.2.0",
        "2.0",
        "x.y.z",
        "",
        None,
        2,
    ):
        try:
            check_additive_schema_version(bad, MODEL_SCHEMA_VERSION)
        except ValueError:
            pass
        else:
            raise AssertionError(f"version policy accepted invalid schema_version {bad!r}")


__all__ = [
    # Explicit capability report
    "SCHEMA_VERSION",
    "SUPPORTED_KINDS",
    "Capability",
    "Support",
    "SemanticIR",
    # Neutral semantic model
    "MODEL_SCHEMA_VERSION",
    "ALLOWED_AGG_FUNCS",
    "ALLOWED_BINARY_OPS",
    "ALLOWED_EXPR_KINDS",
    "ALLOWED_FILTER_OPS",
    "ALLOWED_LOD_NAMES",
    "ALLOWED_SCALAR_FUNCS",
    "ALLOWED_UNARY_OPS",
    "ALLOWED_WINDOW_FUNCS",
    "Cardinality",
    "CrossFilterDirection",
    "DataType",
    "Entity",
    "Expression",
    "Field",
    "Filter",
    "Grain",
    "Metric",
    "Parameter",
    "RLSIntent",
    "Relationship",
    "SemanticModel",
    "format_expression",
    "walk_expression",
    # Cross-language canonical contract
    "CROSS_LANGUAGE_CONTRACT_VERSION",
    "VERSION_POLICY_RULE",
    "build_contract_fixture",
    "build_cross_language_contract",
    "canonical_contract_json",
    "check_additive_schema_version",
    "contract_checksum",
    "run_contract_self_check",
    "verify_cross_language_contract",
]
