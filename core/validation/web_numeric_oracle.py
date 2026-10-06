"""Oráculo numérico web independiente del corpus DataVIZ (sólo stdlib).

Recompone filtros, agrupaciones, campos calculados de fila y agregaciones
directamente desde los datos materializados (``query_context.json``) y compara
el resultado contra lo producido por el runtime (``payload.json``). No importa
el materializador ni el evaluador de expresiones del producto: la independencia
garantiza que el runtime no se valida a sí mismo.

Extiende la cobertura del oráculo 2026-08-24
(``scripts/validate_dataviz_runtime_numeric.py``) de forma genérica: campos
calculados evaluables por fila, filtros sobre expresiones, ops de filtro
adicionales, métricas condicionales y recursión ``measure_ref``. Lo que el
oráculo no sabe recomputar (window, LOD, sets sin definición) queda
``not_evaluable`` con causa de la taxonomía: nunca inventa valores esperados.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = [
    "CAUSA_DATASET",
    "CAUSA_FILTER",
    "CAUSA_FILTER_OP",
    "CAUSA_FORECAST",
    "CAUSA_GROUP_BY",
    "CAUSA_LIMITED",
    "CAUSA_NO_MEASURE",
    "CAUSA_NO_VALUES",
    "CAUSA_RUNTIME_RESULT",
    "CAUSA_TABLE_CALC",
    "TOLERANCE_ABSOLUTE",
    "TOLERANCE_RELATIVE",
    "VisualVerdict",
    "evaluate_case",
    "evaluate_corpus",
    "evaluate_visual",
    "executable_demo_payloads",
]

#: Tolerancias fijas del oráculo (idénticas al oráculo 2026-08-24; no se relajan).
TOLERANCE_RELATIVE = 1e-9
TOLERANCE_ABSOLUTE = 1e-8

#: Taxonomía de causas ``not_evaluable``.
CAUSA_NO_MEASURE = "no_measure"
CAUSA_TABLE_CALC = "table_calc_unsupported"
CAUSA_DATASET = "dataset_unavailable"
CAUSA_LIMITED = "limited_query"
CAUSA_GROUP_BY = "computed_group_by_unsupported"
CAUSA_FILTER = "computed_filter_unsupported"
CAUSA_FILTER_OP = "unsupported_filter_op"
CAUSA_FORECAST = "forecast_unsupported"
CAUSA_RUNTIME_RESULT = "missing_runtime_result"
CAUSA_NO_VALUES = "no_values_checked"

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Máximo de diferencias detalladas por visual (con valores reales y esperados).
_MAX_DIFFERENCES = 25


def _sha256(path: Path) -> str | None:
    """SHA-256 hex del archivo, o None si no existe."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_date(value: Any) -> Any:
    """Alinea serializaciones equivalentes de fecha sin cambiar el valor."""
    if isinstance(value, str) and len(value) >= 19 and value[10:19] in {" 00:00:00", "T00:00:00"}:
        return value[:10]
    return value


def _comparable(left: Any, right: Any) -> tuple[Any, Any]:
    """Devuelve el par comparable: numérico si ambos lo son, si no normalizado."""
    if (
        not isinstance(left, bool)
        and not isinstance(right, bool)
        and isinstance(left, (int, float))
        and isinstance(right, (int, float))
    ):
        return float(left), float(right)
    return _normalize_date(left), _normalize_date(right)


def _same_number(actual: Any, expected: float) -> bool:
    """Comparación con tolerancia fija; NaN y blank (None) son equivalentes."""
    if math.isnan(expected):
        if actual is None:
            return True
        return isinstance(actual, float) and math.isnan(actual)
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return math.isclose(float(actual), expected, rel_tol=TOLERANCE_RELATIVE, abs_tol=TOLERANCE_ABSOLUTE)


class _RowEvaluator:
    """Evalúa expresiones neutras fila a fila con la biblioteca estándar.

    Resuelve campos calculados declarados en la IR (con guarda de profundidad
    contra ciclos), parámetros por su ``default_value`` y medidas cuyo cuerpo
    es una agregación simple: dentro de una agregación externa, el
    ``measure_ref`` toma el valor de fila del campo agregado. Las expresiones
    de ventana, LOD o sets no son evaluables por fila y devuelven ``None``.
    """

    _MAX_DEPTH = 32

    def __init__(
        self,
        calc_fields: Mapping[str, dict],
        parameters: Mapping[str, Any],
        rows: list[dict],
        row_measures: Mapping[str, dict] | None = None,
    ) -> None:
        self._calc_fields = calc_fields
        self._parameters = parameters
        self._row_measures = row_measures or {}
        self._rows = rows
        self._lod_cache: dict[tuple[int, tuple[Any, ...]], tuple[Any, str | None]] = {}

    def value(self, expr: Any, row: dict, _depth: int = 0) -> tuple[Any, str | None]:
        """Devuelve ``(valor, razón)``; razón no None cuando no es evaluable."""
        if _depth > self._MAX_DEPTH:
            return None, "profundidad de recursión excedida en campos calculados"
        if not isinstance(expr, dict):
            return None, "expresión no es un objeto"
        kind = expr.get("kind")
        return self._value_uncached(expr, kind, row, _depth)

    def _value_uncached(self, expr: dict, kind: Any, row: dict, depth: int) -> tuple[Any, str | None]:
        if kind == "literal":
            return expr.get("value"), None
        if kind == "field_ref":
            name = expr.get("name")
            if not isinstance(name, str) or not name:
                return None, "field_ref sin nombre"
            if name in row:
                return row.get(name), None
            calc = self._calc_fields.get(name)
            if calc is not None:
                return self.value(calc, row, depth + 1)
            return None, f"campo ausente en datos: {name!r}"
        if kind == "parameter_ref":
            name = expr.get("name")
            if name in self._parameters:
                return self._parameters[name], None
            return None, f"parámetro sin valor: {name!r}"
        if kind == "measure_ref":
            name = expr.get("name")
            inner = self._row_measures.get(str(name))
            if inner is not None:
                return self.value(inner, row, depth + 1)
            return None, f"medida sin valor por fila: {name!r}"
        if kind == "set_membership":
            set_name = str(expr.get("name") or "")
            target = str(expr.get("entity") or "")
            if set_name not in self._parameters:
                return None, f"set sin miembros declarados: {set_name!r}"
            members = self._parameters[set_name]
            if not isinstance(members, (list, tuple, set, frozenset)):
                return None, f"set con valor no enumerable: {set_name!r}"
            if target in row:
                source = row.get(target)
            else:
                calc = self._calc_fields.get(target)
                if calc is None:
                    return None, f"campo de set ausente en datos: {target!r}"
                source, why = self.value(calc, row, depth + 1)
                if why:
                    return None, why
            return (True if source in members else None), None
        if kind == "lod":
            return self._lod_value(expr, row, depth + 1)
        if kind == "unary":
            operand, why = self.value(expr.get("children", [None])[0], row, depth + 1)
            if why:
                return None, why
            return -operand, None if expr.get("op") == "neg" else f"unario no soportado: {expr.get('op')!r}"
        if kind == "binary":
            children = expr.get("children", [])
            if len(children) != 2:
                return None, "binary sin dos hijos"
            left, left_why = self.value(children[0], row, depth + 1)
            if left_why:
                return None, left_why
            right, right_why = self.value(children[1], row, depth + 1)
            if right_why:
                return None, right_why
            return _binary_value(expr.get("op"), left, right)
        if kind == "conditional":
            children = expr.get("children", [])
            if len(children) != 3:
                return None, "conditional sin tres hijos"
            condition, why = self.value(children[0], row, depth + 1)
            if why:
                return None, why
            if not isinstance(condition, bool):
                return None, "condición no booleana"
            return self.value(children[1] if condition else children[2], row, depth + 1)
        if kind == "func":
            return self._func_value(expr, row, depth)
        if kind == "lookup_map":
            source, why = self.value({"kind": "field_ref", "name": expr.get("name")}, row, depth + 1)
            if why:
                return None, why
            mapping = expr.get("value")
            if not isinstance(mapping, dict):
                return None, "lookup_map sin diccionario"
            key = _normalize_date(source)
            for candidate in (source, key):
                if candidate in mapping:
                    return mapping[candidate], None
            return None, None  # clave sin mapeo: blank como el runtime
        if kind == "measure_ref":
            return None, "measure_ref no evaluable por fila"
        return None, f"expresión no evaluable por fila: kind={kind!r}"

    def _func_value(self, expr: dict, row: dict, depth: int) -> tuple[Any, str | None]:
        """Funciones escalares con equivalente stdlib directo."""
        name = expr.get("name")
        args: list[Any] = []
        for child in expr.get("children", []):
            value, why = self.value(child, row, depth + 1)
            if why:
                return None, why
            args.append(value)
        if name == "coalesce":
            for value in args:
                if value is not None:
                    return value, None
            return None, None
        if name == "contains":
            return (
                str(args[1]).lower() in str(args[0] or "").lower() if len(args) == 2 else None,
                None if len(args) == 2 else "contains requiere dos argumentos",
            )
        if name == "length":
            return float(len(args[0])) if isinstance(args[0], (str, list)) else None, None
        if name == "lower":
            return str(args[0]).lower(), None
        if name == "upper":
            return str(args[0]).upper(), None
        if name == "trim":
            return str(args[0]).strip(), None
        if name == "abs":
            return abs(args[0]), None
        if name == "to_string":
            return str(args[0]), None
        if name == "split" and len(args) >= 1:
            separator = args[1] if len(args) > 1 else " "
            parts = str(args[0]).split(str(separator))
            # Convención Tableau: el índice de token es 1-based.
            index = int(args[2]) - 1 if len(args) > 2 else 0
            return (parts[index] if 0 <= index < len(parts) else None), None
        if name == "replace" and len(args) == 3:
            return str(args[0]).replace(str(args[1]), str(args[2])), None
        if name == "regexp_extract" and len(args) == 2:
            match = re.search(str(args[1]), str(args[0]))
            return (match.group(0) if match else None), None
        if name == "regexp_replace" and len(args) == 3:
            return re.sub(str(args[1]), str(args[2]), str(args[0])), None
        return None, f"función no soportada por el oráculo: {name!r}"


    def _aggregate_rows(self, expr: dict, rows: list[dict], depth: int) -> tuple[Any, str | None]:
        if expr.get("kind") != "agg":
            return None, "LOD requiere una agregaci?n como primer hijo"
        children = expr.get("children", [])
        if len(children) != 1:
            return None, "agg LOD sin un hijo"
        values: list[Any] = []
        for candidate in rows:
            value, why = self.value(children[0], candidate, depth + 1)
            if why:
                return None, why
            values.append(value)
        name = expr.get("name")
        nonblank = [value for value in values if value is not None]
        if name == "count":
            return float(len(nonblank)), None
        if name == "count_distinct":
            return float(len({repr(_normalize_date(value)) for value in nonblank})), None
        if name in {"min", "max"}:
            if not nonblank:
                return math.nan, None
            normalized = [_normalize_date(value) for value in nonblank]
            return (min(normalized) if name == "min" else max(normalized)), None
        numeric = [float(value) for value in nonblank if _is_num(value)]
        if len(numeric) != len(nonblank):
            return None, "agregaci?n LOD num?rica sobre valores no num?ricos"
        if name == "sum":
            return (float(sum(numeric)) if numeric else math.nan), None
        if name == "avg":
            return (sum(numeric) / len(numeric)) if numeric else math.nan, None
        if name == "median":
            return statistics.median(numeric) if numeric else math.nan, None
        return None, f"agregaci?n LOD no soportada: {name!r}"

    def _lod_value(self, expr: dict, row: dict, depth: int) -> tuple[Any, str | None]:
        if expr.get("name") != "fixed":
            return None, f"LOD por fila no soportado: {expr.get('name')!r}"
        children = expr.get("children", [])
        if not children or not isinstance(children[0], dict):
            return None, "LOD FIXED sin expresi?n agregada"
        dimensions = children[1:]
        key_values: list[Any] = []
        for dimension in dimensions:
            value, why = self.value(dimension, row, depth + 1)
            if why:
                return None, why
            key_values.append(_normalize_date(value))
        cache_key = (id(expr), tuple(key_values))
        cached = self._lod_cache.get(cache_key)
        if cached is not None:
            return cached
        candidates: list[dict] = []
        for candidate in self._rows:
            matches = True
            for dimension, expected in zip(dimensions, key_values):
                actual, why = self.value(dimension, candidate, depth + 1)
                if why:
                    result = (None, why)
                    self._lod_cache[cache_key] = result
                    return result
                if _normalize_date(actual) != expected:
                    matches = False
                    break
            if matches:
                candidates.append(candidate)
        result = self._aggregate_rows(children[0], candidates, depth + 1)
        self._lod_cache[cache_key] = result
        return result

def _binary_value(op: Any, left: Any, right: Any) -> tuple[Any, str | None]:
    """Aplica un operador binario neutro con semántica de comparación alineada."""
    if op == "add":
        if isinstance(left, str) and isinstance(right, str):
            return left + right, None  # concatenación de texto
        return (left + right, None) if _is_num(left) and _is_num(right) else (None, "add no numérico")
    if op == "sub":
        return (left - right, None) if _is_num(left) and _is_num(right) else (None, "sub no numérico")
    if op == "mul":
        return (left * right, None) if _is_num(left) and _is_num(right) else (None, "mul no numérico")
    if op == "div":
        if not (_is_num(left) and _is_num(right)):
            return None, "div no numérico"
        return (left / right if right else math.nan), None
    if op in {"eq", "ne", "lt", "le", "gt", "ge"}:
        left_cmp, right_cmp = _comparable(left, right)
        result = {
            "eq": left_cmp == right_cmp,
            "ne": left_cmp != right_cmp,
            "lt": left_cmp < right_cmp,
            "le": left_cmp <= right_cmp,
            "gt": left_cmp > right_cmp,
            "ge": left_cmp >= right_cmp,
        }[op]
        return result, None
    if op == "and":
        return (bool(left) and bool(right)), None
    if op == "or":
        return (bool(left) or bool(right)), None
    return None, f"operador binario no soportado: {op!r}"


def _is_num(value: Any) -> bool:
    """Indica si el valor es numérico real (excluye bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _GroupEvaluator:
    """Evalúa métricas agregadas por grupo con la biblioteca estándar."""

    def __init__(
        self,
        metrics: Mapping[str, dict],
        row_eval: _RowEvaluator,
        parameters: Mapping[str, Any] | None = None,
        *,
        group_fields: tuple[str, ...] = (),
        partition_size: int = 0,
    ) -> None:
        self._metrics = metrics
        self._row_eval = row_eval
        self._parameters = parameters or {}
        self._group_fields = group_fields
        self._partition_size = partition_size

    def value(self, expr: Any, group_rows: list[dict], seen: frozenset[str] = frozenset()) -> tuple[
        Any, str | None
    ]:
        """Devuelve ``(valor, razón)`` de la métrica sobre las filas del grupo."""
        if not isinstance(expr, dict):
            return None, "expresión no es un objeto"
        kind = expr.get("kind")
        if kind == "literal":
            return expr.get("value"), None
        if kind == "parameter_ref":
            name = expr.get("name")
            if name in self._parameters:
                return self._parameters[name], None
            return None, f"parámetro sin valor: {name!r}"
        if kind == "measure_ref":
            name = expr.get("name")
            if name in seen:
                return None, f"ciclo de measure_ref: {name!r}"
            metric = self._metrics.get(name)
            if not isinstance(metric, dict):
                return None, f"medida no declarada: {name!r}"
            return self.value(metric.get("expression"), group_rows, seen | {name})
        if kind == "agg":
            return self._agg_value(expr, group_rows)
        if kind == "window" and expr.get("name") == "size":
            metadata = expr.get("value")
            order_fields = (
                metadata.get("order_fields")
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                isinstance(order_fields, list)
                and order_fields
                and set(map(str, order_fields)) == set(self._group_fields)
            ):
                return float(self._partition_size), None
            return None, "SIZE table-calc addressing does not match visual grouping"
        if kind in {"binary", "conditional", "unary"}:
            return self._composite_value(expr, group_rows, seen)
        return None, f"agregación no soportada por el oráculo: kind={kind!r}"

    def _composite_value(self, expr: dict, group_rows: list[dict], seen: frozenset[str]) -> tuple[
        Any, str | None
    ]:
        """Combinadores evaluados a nivel de grupo (binary, conditional, unary)."""
        kind = expr.get("kind")
        if kind == "unary":
            operand, why = self.value(expr.get("children", [None])[0], group_rows, seen)
            if why:
                return None, why
            return (-operand, None) if expr.get("op") == "neg" else (None, f"unario no soportado: {expr.get('op')!r}")
        evaluated: list[Any] = []
        children = expr.get("children", [])
        if kind == "conditional" and len(children) != 3:
            return None, "conditional sin tres hijos"
        for child in children:
            value, why = self.value(child, group_rows, seen)
            if why:
                return None, why
            evaluated.append(value)
        if kind == "binary":
            return _binary_value(expr.get("op"), evaluated[0], evaluated[1])
        condition = evaluated[0]
        if not isinstance(condition, bool):
            return None, "condición no booleana"
        return (evaluated[1] if condition else evaluated[2]), None

    def _agg_value(self, expr: dict, group_rows: list[dict]) -> tuple[float | None, str | None]:
        """Agregaciones sobre un hijo evaluable fila a fila."""
        name = expr.get("name")
        children = expr.get("children", [])
        if len(children) != 1:
            return None, "agg sin un hijo"
        raw_values: list[Any] = []
        for row in group_rows:
            value, why = self._row_eval.value(children[0], row)
            if why:
                return None, why
            raw_values.append(value)
        if name == "count":
            return float(sum(1 for value in raw_values if value is not None)), None
        if name == "count_distinct":
            return float(len({repr(_normalize_date(v)) for v in raw_values if v is not None})), None
        non_null = [value for value in raw_values if value is not None]
        numeric = [float(value) for value in non_null if _is_num(value)]
        if len(numeric) != len(non_null):
            return None, "numeric aggregation contains non-numeric values"
        if name == "sum":
            return (float(sum(numeric)) if numeric else math.nan), None
        if name == "avg":
            return (sum(numeric) / len(numeric)) if numeric else math.nan, None
        if name == "min":
            return (min(numeric) if numeric else math.nan), None
        if name == "max":
            return (max(numeric) if numeric else math.nan), None
        if name == "median":
            return (float(statistics.median(numeric)) if numeric else math.nan), None
        return None, f"agregación no soportada: {name!r}"


_FILTER_OPS = frozenset(
    {
        "eq", "ne", "neq", "in", "not_in", "lt", "lte", "gt", "gte", "between",
        "contains", "not_contains", "starts_with", "ends_with", "is_null", "is_not_null",
    }
)


def _predicate_holds(op: str, actual: Any, values: list[Any]) -> bool | None:
    """Aplica el op del filtro al valor de fila; None si el op no está soportado."""
    if op == "is_null":
        return actual is None
    if op == "is_not_null":
        return actual is not None
    if op in {"eq", "ne", "neq"}:
        left, right = _comparable(actual, values[0] if values else None)
        return left == right if op == "eq" else left != right
    if op in {"in", "not_in"}:
        hit = False
        for candidate in values:
            left, right = _comparable(actual, candidate)
            if left == right:
                hit = True
                break
        return hit if op == "in" else not hit
    if op in {"lt", "lte", "gt", "gte"}:
        if actual is None:
            return False
        left, right = _comparable(actual, values[0] if values else None)
        if not (_is_num(left) and _is_num(right)) and not (isinstance(left, str) and isinstance(right, str)):
            return None
        return {"lt": left < right, "lte": left <= right, "gt": left > right, "gte": left >= right}[op]
    if op == "between" and len(values) == 2:
        if actual is None:
            return False
        low = _comparable(actual, values[0])
        high = _comparable(actual, values[1])
        return low[0] >= low[1] and high[0] <= high[1]
    if op in {"contains", "not_contains", "starts_with", "ends_with"}:
        if actual is None:
            return op == "not_contains"
        needle = str(values[0]) if values else ""
        haystack = str(actual)
        if op == "contains":
            return needle.lower() in haystack.lower()
        if op == "not_contains":
            return needle.lower() not in haystack.lower()
        return haystack.startswith(needle) if op == "starts_with" else haystack.endswith(needle)
    return None


@dataclass(frozen=True)
class VisualVerdict:
    """Veredicto numérico del oráculo web para un visual individual."""

    visual_id: str
    page: str
    kind: str
    status: str  # 'match' | 'mismatch' | 'not_evaluable'
    cause: str | None
    limitations: tuple[str, ...] = ()
    checked_values: int = 0
    groups: int = 0
    differences: tuple[str, ...] = ()
    detail: str = field(default="", compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el veredicto con sus diferencias acotadas."""
        return {
            "visual_id": self.visual_id,
            "page": self.page,
            "kind": self.kind,
            "status": self.status,
            "cause": self.cause,
            "limitations": list(self.limitations),
            "checked_values": self.checked_values,
            "groups": self.groups,
            "differences": list(self.differences),
        }


def _apply_filters(
    rows: list[dict], filters: Any, row_eval: _RowEvaluator
) -> tuple[list[dict], str | None]:
    """Filtra filas evaluando cada predicado por fila con el oráculo."""
    if not isinstance(filters, list):
        return rows, "query.filters no es una lista"
    current = rows
    for item in filters:
        if not isinstance(item, dict):
            return current, "filtro no es un objeto"
        op = item.get("op")
        values = item.get("values")
        if op not in _FILTER_OPS:
            return current, f"{CAUSA_FILTER_OP}: {op!r}"
        if not isinstance(values, list):
            values = [values] if values is not None else []
        filtered: list[dict] = []
        for row in current:
            actual, why = row_eval.value(item.get("expression"), row)
            if why:
                return current, f"{CAUSA_FILTER}: {why}"
            holds = _predicate_holds(op, actual, values)
            if holds is None:
                return current, f"{CAUSA_FILTER_OP}: {op!r} con valores {values!r}"
            if holds:
                filtered.append(row)
        current = filtered
    return current, None


def _forecast_period_start(value: Any, period: str) -> Any:
    """Reagrupa una fecha al inicio de su periodo (mes o año) sin cambiar el valor."""
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if period == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}-01"
    if period == "year":
        return f"{parsed.year:04d}-01-01"
    return value


def _group_keys(
    visual: dict, rows: list[dict], row_eval: _RowEvaluator
) -> tuple[list[str], list[tuple[tuple[Any, ...], list[dict]]], str | None]:
    """Agrupa las filas por las claves del query.group_by (campos o cálculos).

    Cuando el visual declara forecast, el campo temporal del agrupamiento se
    reagrupa al inicio de su periodo (regla independiente, misma semántica que
    el oráculo 2026-08-24).
    """
    query = visual.get("query", {})
    group_by = query.get("group_by", [])
    if not isinstance(group_by, list):
        return [], [], "query.group_by no es una lista"
    forecast = visual.get("forecast")
    period = forecast.get("period") if isinstance(forecast, dict) else None
    time_field = forecast.get("time_field") if isinstance(forecast, dict) else None
    grouped: dict[tuple[Any, ...], list[dict]] = {}
    keys = [str(item.get("name")) if isinstance(item, dict) else "?" for item in group_by]
    for row in rows:
        key_values: list[Any] = []
        for item in group_by:
            value, why = row_eval.value(item, row)
            if why:
                return [], [], f"{CAUSA_GROUP_BY}: {why}"
            if period and isinstance(item, dict) and item.get("name") == time_field:
                value = _forecast_period_start(value, period)
            key_values.append(_normalize_date(value))
        grouped.setdefault(tuple(key_values), []).append(row)
    return keys, sorted(grouped.items(), key=lambda pair: repr(pair[0])), None


def _forecast_limitation(visual: dict, grouped_count: int) -> tuple[int, tuple[str, ...], str | None]:
    """Devuelve (grupos a ignorar, limitaciones, causa) según el forecast del visual."""
    forecast = visual.get("forecast")
    if not isinstance(forecast, dict):
        return 0, (), None
    period = forecast.get("period")
    time_field = forecast.get("time_field")
    if period not in {"month", "year"} or not isinstance(time_field, str):
        return 0, (), f"{CAUSA_FORECAST}: periodo o campo temporal no soportado"
    ignore_last = forecast.get("ignore_last", 0)
    ignore_last = ignore_last if isinstance(ignore_last, int) and not isinstance(ignore_last, bool) else 0
    return max(ignore_last, 0), ("forecast_not_evaluated",), None


def _select_measures(
    visual: dict, metrics: Mapping[str, dict], group_eval: _GroupEvaluator, sample_rows: list[dict]
) -> tuple[list[tuple[str, dict]], list[str], str | None, bool]:
    """Resuelve las medidas del select.

    Devuelve ``(alias→expr, no soportadas, causa, hay_candidatas)``:
    ``hay_candidatas`` distingue un visual sin medidas (slicer, text_box) de
    uno cuyas medidas el oráculo no puede recomputar.
    """
    select = visual.get("query", {}).get("select", [])
    if not isinstance(select, list):
        return [], [], "query.select no es una lista", False
    resolved: list[tuple[str, dict]] = []
    unsupported: list[str] = []
    candidates = 0
    for item in select:
        if not isinstance(item, dict):
            continue
        expression = item.get("expression")
        if not isinstance(expression, dict):
            continue
        if expression.get("kind") not in {"measure_ref", "agg", "binary", "conditional", "literal", "unary"}:
            continue  # dimensión del select: no es candidata a medida
        candidates += 1
        alias = item.get("alias")
        if not isinstance(alias, str) or not alias:
            alias = str(expression.get("name") or "")
        if expression.get("kind") == "measure_ref":
            metric = metrics.get(expression.get("name"))
            if not isinstance(metric, dict):
                unsupported.append(f"{alias}: medida no declarada")
                continue
            expression = metric.get("expression")
        value, why = group_eval.value(expression, sample_rows or [{}])
        if why and value is None:
            unsupported.append(f"{alias}: {why}")
            continue
        resolved.append((alias, expression))
    if not candidates:
        return [], [], CAUSA_NO_MEASURE, False
    return resolved, unsupported, None, True


def _case_dir_from_payload(path: Path) -> Path:
    """Normaliza la ruta del inventario (payload.json) al directorio del caso."""
    return path.parent if path.name == "payload.json" else path


def _row_level_measures(metrics: Mapping[str, dict]) -> dict[str, dict]:
    """Deriva el valor por fila de medidas simples para contextos agregados.

    Dentro de ``sum(conditional(..., measure_ref: X, ...))`` el ``measure_ref``
    toma por fila el cuerpo de la medida X cuando X es una agregación simple
    (el hijo del ``agg``) o un literal. Las demás medidas no tienen valor por
    fila y quedan sin resolver.
    """
    row_measures: dict[str, dict] = {}
    for name, metric in metrics.items():
        expression = metric.get("expression")
        if not isinstance(expression, dict):
            continue
        if expression.get("kind") == "agg":
            children = expression.get("children", [])
            if len(children) == 1 and isinstance(children[0], dict):
                row_measures[name] = children[0]
        elif expression.get("kind") == "literal":
            row_measures[name] = expression
    return row_measures


def evaluate_visual(visual: dict, runtime_result: dict | None, context: dict) -> VisualVerdict:
    """Evalúa un visual del plan contra el resultado runtime con el oráculo."""
    name = str(visual.get("name", ""))
    page = str(visual.get("page", ""))
    kind = str(visual.get("kind", ""))
    query = visual.get("query")
    if not isinstance(query, dict):
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_NO_MEASURE)

    datasets = context.get("datasets", {})
    dataset = datasets.get(query.get("from_datasource"))
    rows = dataset.get("rows") if isinstance(dataset, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_DATASET)
    if not rows:
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_NO_VALUES)

    if query.get("limit") is not None:
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_LIMITED)

    metrics = {
        metric.get("name"): metric
        for metric in context.get("semantic_model", {}).get("metrics", [])
        if isinstance(metric, dict) and isinstance(metric.get("name"), str)
    }
    calc_fields: dict[str, dict] = {}
    for entity in context.get("semantic_model", {}).get("entities", []):
        if not isinstance(entity, dict):
            continue
        for entity_field in entity.get("fields", []):
            if isinstance(entity_field, dict) and isinstance(entity_field.get("expression"), dict):
                calc_fields[str(entity_field.get("name"))] = entity_field["expression"]
    parameters = {
        parameter.get("name"): parameter.get("default_value")
        for parameter in context.get("semantic_model", {}).get("parameters", [])
        if isinstance(parameter, dict) and isinstance(parameter.get("name"), str)
    }

    row_eval = _RowEvaluator(calc_fields, parameters, rows, _row_level_measures(metrics))
    filtered, filter_blocker = _apply_filters(rows, query.get("filters", []), row_eval)
    if filter_blocker:
        return VisualVerdict(name, page, kind, "not_evaluable", filter_blocker)

    group_keys, grouped, group_blocker = _group_keys(visual, filtered, row_eval)
    if group_blocker:
        return VisualVerdict(name, page, kind, "not_evaluable", group_blocker)

    ignore_last, limitations, forecast_blocker = _forecast_limitation(visual, len(grouped))
    if forecast_blocker:
        return VisualVerdict(name, page, kind, "not_evaluable", forecast_blocker)
    if ignore_last:
        grouped = grouped[:-ignore_last] if ignore_last < len(grouped) else []

    group_eval = _GroupEvaluator(
        metrics,
        row_eval,
        parameters,
        group_fields=tuple(group_keys),
        partition_size=len(grouped),
    )
    # Muestra de detección de soporte: filas filtradas y, si los filtros
    # vacían el conjunto, el dataset completo (los grupos vacíos se
    # reportan aparte como no_values_checked, nunca como medida no soportada).
    sample_rows = filtered if filtered else rows
    measures, unsupported, measure_blocker, has_candidates = _select_measures(
        visual, metrics, group_eval, sample_rows
    )
    if measure_blocker:
        return VisualVerdict(name, page, kind, "not_evaluable", measure_blocker)
    limitations = limitations + tuple(f"unsupported_measure: {item}" for item in unsupported)
    if not measures:
        if not has_candidates:
            return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_NO_MEASURE)
        detail = unsupported[0] if unsupported else "sin medidas seleccionadas"
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_TABLE_CALC, limitations, detail=detail)

    if runtime_result is None:
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_RUNTIME_RESULT, limitations)

    expected_keys = {
        *(f"field:{key}" for key in group_keys),
        *(f"measure:{alias}" for alias, _ in measures),
    }
    actual_columns = {
        key: (value if isinstance(value, list) else [value])
        for key, value in (runtime_result or {}).items()
        if key in expected_keys
    }
    missing = sorted(expected_keys.difference(actual_columns))
    if missing:
        return VisualVerdict(
            name, page, kind, "mismatch", None, limitations,
            differences=(f"missing result columns: {missing}",),
        )
    lengths = {len(value) for value in actual_columns.values()}
    if len(lengths) != 1:
        return VisualVerdict(name, page, kind, "mismatch", None, limitations, differences=("ragged result",))
    row_count = next(iter(lengths), 0)
    actual_by_group: dict[tuple[Any, ...], int] = {}
    for index in range(row_count):
        key = tuple(_normalize_date(actual_columns[f"field:{key}"][index]) for key in group_keys)
        actual_by_group[key] = index

    differences: list[str] = []
    checked = 0
    expected_group_keys = {group_key for group_key, _ in grouped}
    extra_groups = set(actual_by_group.keys() - expected_group_keys)
    if "forecast_not_evaluated" in limitations and expected_group_keys:
        forecast = visual.get("forecast")
        time_field = forecast.get("time_field") if isinstance(forecast, dict) else None
        if isinstance(time_field, str) and time_field in group_keys:
            time_index = group_keys.index(time_field)
            expected_times = [
                group[time_index]
                for group in expected_group_keys
                if group[time_index] is not None
            ]
            if expected_times:
                try:
                    historical_boundary = max(expected_times)
                    extra_groups = {
                        group for group in extra_groups
                        if group[time_index] is None or group[time_index] <= historical_boundary
                    }
                except TypeError:
                    pass
    for extra_group in sorted(extra_groups, key=repr):
        differences.append(f"extra group: {extra_group!r}")

    def _matches(actual: Any, expected: Any) -> bool:
        """Compara un valor esperado con el runtime: numérico, texto o blank."""
        if expected is None or (isinstance(expected, float) and math.isnan(expected)):
            return actual is None or (isinstance(actual, float) and math.isnan(actual))
        if isinstance(expected, str):
            return actual == expected
        return _same_number(actual, expected)

    for group_key, group_rows in grouped:
        index = actual_by_group.get(group_key)
        if index is None:
            differences.append(f"missing group: {group_key!r}")
            continue
        for alias, expression in measures:
            expected, why = group_eval.value(expression, group_rows)
            if why:
                differences.append(f"measure:{alias} {group_key!r}: no evaluable ({why})")
                continue
            column = actual_columns.get(f"measure:{alias}")
            if column is None or index >= len(column):
                differences.append(f"missing measure:{alias} for {group_key!r}")
                continue
            checked += 1
            if not _matches(column[index], expected):
                differences.append(
                    f"measure:{alias} {group_key!r}: actual={column[index]!r} expected={expected!r}"
                )
    if differences:
        return VisualVerdict(
            name, page, kind, "mismatch", None, limitations, checked, len(grouped),
            tuple(differences[:_MAX_DIFFERENCES]),
        )
    if checked == 0:
        if not grouped and row_count == 0:
            return VisualVerdict(
                name,
                page,
                kind,
                "match",
                None,
                limitations + ("empty_result_verified",),
                0,
                0,
            )
        return VisualVerdict(name, page, kind, "not_evaluable", CAUSA_NO_VALUES, limitations, 0, len(grouped))
    return VisualVerdict(name, page, kind, "match", None, limitations, checked, len(grouped))


def _resolve_source(source: Any) -> Path | None:
    """Resuelve la ruta fuente declarada en summary.json (relativa a la raíz)."""
    if not isinstance(source, str) or not source:
        return None
    path = Path(source)
    if path.is_absolute():
        return path if path.is_file() else None
    for base in (_REPO_ROOT, Path.cwd()):
        candidate = base / path
        if candidate.is_file():
            return candidate
    return None


def evaluate_case(case_dir: Path) -> dict[str, Any]:
    """Evalúa un caso completo (payload + query_context) y agrega veredictos."""
    case_dir = Path(case_dir)
    payload_path = case_dir / "payload.json"
    context_path = case_dir / "query_context.json"
    if not payload_path.is_file() or not context_path.is_file():
        return {
            "case_id": case_dir.name,
            "status": "not_evaluable",
            "cause": "falta payload.json o query_context.json",
            "counts": {"match": 0, "mismatch": 0, "not_evaluable": 0},
            "checked_values": 0,
            "groups": 0,
            "visuals": [],
        }
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    context = json.loads(context_path.read_text(encoding="utf-8"))
    runtime_visuals = payload.get("results", {}).get("visuals", {})
    verdicts = [
        evaluate_visual(visual, runtime_visuals.get(str(visual.get("name", ""))), context)
        for visual in payload.get("plan", {}).get("visuals", [])
        if isinstance(visual, dict)
    ]
    counts = {state: sum(v.status == state for v in verdicts) for state in ("match", "mismatch", "not_evaluable")}
    checked = sum(v.checked_values for v in verdicts)
    if counts["mismatch"]:
        status = "mismatch"
    elif counts["match"] and checked > 0:
        status = "match"
    else:
        status = "not_evaluable"
    summary = {}
    summary_path = case_dir / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    source = _resolve_source(summary.get("source"))
    return {
        "case_id": case_dir.name,
        "payload_path": str(payload_path),
        "payload_sha256": _sha256(payload_path),
        "query_context_sha256": _sha256(context_path),
        "source_artifact": summary.get("source"),
        "source_sha256": _sha256(source) if source else None,
        "status": status,
        "cause": None if status != "not_evaluable" else "todos los visuales no evaluables",
        "counts": counts,
        "checked_values": checked,
        "groups": sum(v.groups for v in verdicts),
        "visuals": [v.to_dict() for v in verdicts],
    }


def evaluate_corpus(case_paths: Mapping[str, Path]) -> dict[str, Any]:
    """Evalúa el corpus G0 completo y agrega totales por estado."""
    cases = [evaluate_case(Path(path)) for case_id, path in sorted(case_paths.items())]
    totals = {
        "demos": len(cases),
        "visuals": sum(len(case["visuals"]) for case in cases),
        "match": sum(case["counts"]["match"] for case in cases),
        "mismatch": sum(case["counts"]["mismatch"] for case in cases),
        "not_evaluable": sum(case["counts"]["not_evaluable"] for case in cases),
        "checked_values": sum(case["checked_values"] for case in cases),
    }
    return {
        "schema_version": "1.0.0",
        "oracle": "independent-web-stdlib-aggregate-v2-g0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "tolerances": {"relative": TOLERANCE_RELATIVE, "absolute": TOLERANCE_ABSOLUTE},
        "cases": cases,
        "totals": totals,
    }


def executable_demo_payloads(inventory: dict) -> dict[str, Path]:
    """Extrae del inventario G0 las rutas payload.json de las demos ejecutables."""
    paths: dict[str, Path] = {}
    for artifact in inventory.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        if artifact.get("type") != "web_payload" or not artifact.get("executable"):
            continue
        case_id = artifact.get("case_id")
        path = artifact.get("path")
        if isinstance(case_id, str) and isinstance(path, str):
            paths[case_id] = _case_dir_from_payload(_REPO_ROOT / path)
    return paths
