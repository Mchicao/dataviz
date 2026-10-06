"""Query AST neutra y versionada — semántica de consulta *allowlisted*.

Define un conjunto **cerrado y tipado** de operaciones de consulta (selección,
filtro, agregación, orden y límite) que cualquier consumidor destino (p. ej. el
generador PBIR) puede implementar de forma finita y predecible. No parsea SQL ni
admite SQL arbitrario: no existe ningún campo ``raw_sql`` y toda operación fuera
de las listas blancas (:data:`ALLOWED_OPS`, :data:`ALLOWED_FUNCS`) se rechaza
al construir el nodo.

El AST es **pasivo**: sólo transporta datos, no invoca parsers ni conoce la
capa destino (PBIR / visual JSON).

Política de versionado (SemVer):

* **MAJOR**: campo eliminado/renombrado, tipo rupturista, semántica alterada.
* **MINOR**: campo nuevo opcional (con valor por defecto).
* **PATCH**: corrección que no cambia la forma serializada.

Sólo depende de la biblioteca estándar para no acoplar el contrato a parsers ni
a generadores destino.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Any, Literal

from core.contracts.semantic_ir import Expression

SCHEMA_VERSION: str = "2.1.0"
"""Versión actual del contrato. Ver política SemVer en el docstring del módulo."""

#: Lista blanca cerrada de operadores de predicado. ``raw_sql`` y ``LIKE``
#: quedan fuera a propósito: la IR no admite SQL arbitrario ni operadores que
#: un consumidor finito no pueda implementar de forma determinista.
ALLOWED_OPS: frozenset[str] = frozenset(
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
        "contains",
        "not_contains",
        "starts_with",
        "not_starts_with",
        "ends_with",
        "not_ends_with",
        "is_null",
        "is_not_null",
    }
)

#: Lista blanca cerrada de funciones de agregación.
ALLOWED_FUNCS: frozenset[str] = frozenset(
    {"sum", "avg", "count", "count_distinct", "min", "max", "median"}
)

_VALID_DIRECTIONS: frozenset[str] = frozenset({"asc", "desc"})

SortDirection = Literal["asc", "desc"]
PredicateValue = str | int | float | bool | None


def _is_real_int(value: Any) -> bool:
    """Entero genuino: rechaza ``bool`` (subtipo de ``int``)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _check_non_negative_int(value: Any, name: str) -> None:
    if not _is_real_int(value) or value < 0:
        raise ValueError(f"{name} debe ser un entero no negativo")


@dataclass(frozen=True)
class Predicate:
    """Predicado de filtro con operador de la lista blanca.

    La cantidad de valores requeridos depende del operador:

    * comparadores y operadores de texto: exactamente **1** valor.
    * ``in``/``not_in``: **1 o más** valores.
    * ``between``: exactamente **2** valores (rango cerrado).
    * ``is_null``/``is_not_null``: **0** valores.
    """

    op: str
    expression: Expression
    values: list[PredicateValue] = dc_field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.expression, Expression):
            raise TypeError("Predicate.expression must be Expression")
        if self.op not in ALLOWED_OPS:
            raise ValueError(f"op no permitido: {self.op!r}; allowlist={sorted(ALLOWED_OPS)}")
        count = len(self.values)
        if self.op in {"is_null", "is_not_null"}:
            if count != 0:
                raise ValueError(f"{self.op} no admite valores")
        elif self.op in {"in", "not_in"}:
            if count < 1:
                raise ValueError(f"{self.op} requiere al menos un valor")
        elif self.op == "between":
            if count != 2:
                raise ValueError("between requiere exactamente dos valores")
        else:  # comparadores binarios eq/ne/gt/gte/lt/lte
            if count != 1:
                raise ValueError(f"{self.op} requiere exactamente un valor")


@dataclass(frozen=True)
class SelectItem:
    """Elemento de ``SELECT`` basado en una expresión neutral.

    Attributes:
        expression: Expresión neutral escalar o agregada.
        alias: Alias de salida opcional.
    """

    expression: Expression
    alias: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.expression, Expression):
            raise TypeError("SelectItem.expression must be Expression")


@dataclass(frozen=True)
class SortKey:
    """Clave de ordenación: campo + dirección.

    Attributes:
        expression: Expresión cuyo resultado determina el orden.
        direction: ``"asc"`` o ``"desc"``.
    """

    expression: Expression
    direction: SortDirection = "asc"

    def __post_init__(self) -> None:
        if not isinstance(self.expression, Expression):
            raise TypeError("SortKey.expression must be Expression")
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(f"direction no permitida: {self.direction!r}")


@dataclass(frozen=True)
class QuerySpec:
    """Consulta neutra: ``SELECT FROM FILTER GROUP SORT LIMIT``.

    Invariante: nunca transporta SQL crudo. Toda operación es un nodo de la
    lista blanca. ``from_datasource`` identifica de forma única el datasource
    base (sin JOINs arbitrarios): la federación Tableau se resuelve fuera de la
    IR, al construir el snapshot.

    Attributes:
        from_datasource: Nombre del datasource base. Obligatorio.
        select: Lista de items (campo con agregación opcional).
        filters: Lista de predicados (cada op ∈ :data:`ALLOWED_OPS`).
        group_by: Campos de agrupación.
        sort_by: Claves de ordenación.
        limit: Límite de filas (None o entero no negativo).
    """

    from_datasource: str
    select: list[SelectItem] = dc_field(default_factory=list)
    filters: list[Predicate] = dc_field(default_factory=list)
    group_by: list[Expression] = dc_field(default_factory=list)
    sort_by: list[SortKey] = dc_field(default_factory=list)
    limit: int | None = None
    parameters: dict[str, Any] = dc_field(default_factory=dict)
    selections: list[Predicate] = dc_field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.from_datasource:
            raise ValueError("from_datasource es obligatorio")
        if self.limit is not None:
            _check_non_negative_int(self.limit, "limit")

    def to_dict(self) -> dict[str, Any]:
        """Serializa la consulta a un ``dict`` JSON-compatible."""
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serializa la consulta a JSON canónico (claves ordenadas, UTF-8 puro)."""
        return json.dumps(
            asdict(self),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuerySpec:
        """Reconstruye una consulta desde un ``dict``, de forma estricta.

        Valida tipos anidados y rechaza claves inesperadas con ``TypeError``.
        """
        payload = dict(data)
        payload["select"] = [_select_item(s) for s in payload.get("select", [])]
        payload["filters"] = [_predicate(p) for p in payload.get("filters", [])]
        payload["selections"] = [_predicate(p) for p in payload.get("selections", [])]
        payload["group_by"] = [_expression(g) for g in payload.get("group_by", [])]
        payload["sort_by"] = [_sort_key(s) for s in payload.get("sort_by", [])]
        return cls(**payload)  # strict: claves extra -> TypeError

    @classmethod
    def from_json(cls, text: str) -> QuerySpec:
        """Atajo ``from_dict(json.loads(text))``."""
        return cls.from_dict(json.loads(text))


# --- Deserializadores estrictos -------------------------------------------
#
# Cada helper reenvía el dict al constructor del dataclass con ``**``, de modo
# que una clave inesperada lanza ``TypeError`` (igual que el contrato
# :mod:`core.contracts.source_snapshot`).


def _predicate(data: Mapping[str, Any]) -> Predicate:
    payload = dict(data)
    payload["expression"] = _expression(payload["expression"])
    # ``values`` ya es una lista de escalares nativos; sin transformación.
    return Predicate(**payload)


def _select_item(data: Mapping[str, Any]) -> SelectItem:
    payload = dict(data)
    payload["expression"] = _expression(payload["expression"])
    return SelectItem(**payload)


def _sort_key(data: Mapping[str, Any]) -> SortKey:
    payload = dict(data)
    payload["expression"] = _expression(payload["expression"])
    return SortKey(**payload)


def _expression(data: Mapping[str, Any]) -> Expression:
    payload = dict(data)
    payload["children"] = tuple(_expression(child) for child in payload.get("children", ()))
    return Expression(**payload)


@dataclass(frozen=True)
class QueryPolicy:
    """Política declarativa que el planificador de consultas inyecta antes de ejecutar.

    El AST (:class:`QuerySpec`) ya es estructuralmente libre de SQL/Python crudo:
    no existe ningún campo ``raw_sql``/``raw_expr`` y toda operación está en las
    listas blancas :data:`ALLOWED_OPS`/:data:`ALLOWED_FUNCS`. :class:`QueryPolicy`
    añade una capa de aprobación por *tenant* sobre datasources, campos y límites.
    Un conjunto vacío significa *sin restricción en esta capa* (la validación
    final de campos contra el esquema del dataset la realiza el ejecutor).

    Attributes:
        allowed_datasources: Datasources permitidos para
            ``QuerySpec.from_datasource``; vacío = cualquiera.
        allowed_fields: Campos referenciados por expresiones ``field_ref`` permitidos en
            ``select``/``filters``/``group_by``; vacío = cualquiera.
        denied_fields: Campos siempre rechazados, incluso si estuviesen listados
            en ``allowed_fields`` (la intersección se rechaza al construir).
        max_limit: Tope duro para ``QuerySpec.limit``; el planificador lo limita.
        default_limit: Límite inyectado cuando la consulta no declara ``limit``.
        require_non_empty_select: Si ``True``, rechaza ``SELECT *`` (select vacío).
        reject_raw_expressions: Si ``True`` (por defecto), el planificador verifica
            que ningún nodo del AST transporte atributos de expresión cruda.
    """

    allowed_datasources: frozenset[str] = frozenset()
    allowed_fields: frozenset[str] = frozenset()
    denied_fields: frozenset[str] = frozenset()
    max_limit: int | None = None
    default_limit: int | None = None
    require_non_empty_select: bool = False
    reject_raw_expressions: bool = True

    def __post_init__(self) -> None:
        if self.max_limit is not None:
            _check_non_negative_int(self.max_limit, "max_limit")
        if self.default_limit is not None:
            _check_non_negative_int(self.default_limit, "default_limit")
        overlap = self.allowed_fields & self.denied_fields
        if overlap:
            raise ValueError(
                f"campos en allowed_fields y denied_fields a la vez: {sorted(overlap)}"
            )
        if (
            self.max_limit is not None
            and self.default_limit is not None
            and self.default_limit > self.max_limit
        ):
            raise ValueError(
                f"default_limit ({self.default_limit}) no puede superar "
                f"max_limit ({self.max_limit})"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serializa la política a ``dict`` JSON-compatible (frozenset -> lista ordenada)."""
        return {
            "allowed_datasources": sorted(self.allowed_datasources),
            "allowed_fields": sorted(self.allowed_fields),
            "denied_fields": sorted(self.denied_fields),
            "max_limit": self.max_limit,
            "default_limit": self.default_limit,
            "require_non_empty_select": self.require_non_empty_select,
            "reject_raw_expressions": self.reject_raw_expressions,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serializa la política a JSON canónico (claves ordenadas, UTF-8 puro)."""
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QueryPolicy:
        """Reconstruye una política desde un ``dict``, de forma estricta.

        Las colecciones se normalizan a ``frozenset``; las claves inesperadas se
        rechazan con ``TypeError`` (igual que :meth:`QuerySpec.from_dict`).
        """
        payload = dict(data)
        payload["allowed_datasources"] = frozenset(payload.get("allowed_datasources", ()))
        payload["allowed_fields"] = frozenset(payload.get("allowed_fields", ()))
        payload["denied_fields"] = frozenset(payload.get("denied_fields", ()))
        return cls(**payload)  # strict: claves extra -> TypeError

    @classmethod
    def from_json(cls, text: str) -> QueryPolicy:
        """Atajo ``from_dict(json.loads(text))``."""
        return cls.from_dict(json.loads(text))


__all__ = [
    "ALLOWED_FUNCS",
    "ALLOWED_OPS",
    "SCHEMA_VERSION",
    "Predicate",
    "PredicateValue",
    "QueryPolicy",
    "QuerySpec",
    "SelectItem",
    "SortDirection",
    "SortKey",
]
