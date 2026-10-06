"""Ejecutor seguro de Query AST 2.1 sobre datasets neutrales en memoria."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from apps.dataviz_service.runtime.errors import (
    OpaqueDAXExecutionUnavailableError,
    UnknownFieldError,
)
from core.contracts.dataset import Dataset
from core.contracts.query_ast import Predicate, QuerySpec, SelectItem
from core.contracts.semantic_ir import Expression, SemanticModel, walk_expression


def _sort_key(value: Any) -> tuple[int, Any]:
    return (1, 0) if value is None else (0, value)


class QueryExecutor:
    """Evalúa la gramática cerrada de expresiones, sin SQL ni ``eval``."""

    def __init__(
        self,
        model: SemanticModel | None = None,
        parameters: Mapping[str, object] | None = None,
        runtime_parameters: Mapping[str, object] | None = None,
    ) -> None:
        self._model = model
        self._configured_parameters = dict(parameters or {})
        self.metrics = {metric.name: metric for metric in (model.metrics if model else ())}
        self.calculated_fields = {
            model_field.name: model_field.expression
            for entity in (model.entities if model else ())
            for model_field in entity.fields
            if model_field.expression is not None
        }
        defaults = {
            parameter.name: parameter.default_value
            for parameter in (model.parameters if model else ())
        }
        self._runtime_parameters = dict(runtime_parameters or {})
        defaults.update(self._configured_parameters)
        defaults.update(self._runtime_parameters)
        self.parameters = defaults

    def execute(self, query: QuerySpec, dataset: Dataset) -> Dataset:
        if query.parameters or query.selections:
            effective = self
            merged_parameters = self._configured_parameters
            if query.parameters:
                # Re-instanciar sólo si cambian los parámetros configurados;
                # re-crear el executor por consulta repite el análisis completo
                # del modelo semántico (métricas, campos calculados, defaults).
                if any(
                    self._configured_parameters.get(name) is not value
                    for name, value in query.parameters.items()
                ):
                    merged_parameters = {**self._configured_parameters, **query.parameters}
                    effective = QueryExecutor(
                        model=self._model,
                        parameters=merged_parameters,
                        runtime_parameters=self._runtime_parameters,
                    )
                else:
                    merged_parameters = dict(self._configured_parameters)
                    merged_parameters.update(query.parameters)
            parameters_before = self.parameters
            self.parameters = {
                **parameters_before,
                **merged_parameters,
                **query.parameters,
            }
            try:
                return effective.execute(
                    QuerySpec(
                        from_datasource=query.from_datasource,
                        select=query.select,
                        filters=[*query.filters, *query.selections],
                        group_by=query.group_by,
                        sort_by=query.sort_by,
                        limit=query.limit,
                        parameters={},
                        selections=[],
                    ),
                    dataset,
                )
            finally:
                self.parameters = parameters_before
        self._validate(query, dataset)
        rows = [dict(row) for row in dataset.rows]
        rows = [row for row in rows if all(self._predicate_ok(p, row) for p in query.filters)]
        select = query.select or self._all_columns_select(dataset)
        output_names = self._output_names(select)
        grouped = any(self._contains_aggregate(item.expression) for item in select)
        if grouped:
            invalid = [
                item.expression
                for item in select
                if not self._contains_aggregate(item.expression)
                and self._contains_row_reference(item.expression)
                and item.expression not in query.group_by
            ]
            if invalid:
                raise UnknownFieldError("cada proyección no agregada debe pertenecer a group_by")
        if grouped or query.group_by:
            output = self._aggregate(select, output_names, query.group_by, rows)
        else:
            output = [self._project_row(select, output_names, row) for row in rows]
        output = self._sort(query, select, output_names, output)
        if query.limit is not None:
            output = output[: query.limit]
        return Dataset(name=f"{dataset.name}__result", columns=output_names, rows=tuple(output))

    @staticmethod
    def _all_columns_select(dataset: Dataset) -> list[SelectItem]:
        return [
            SelectItem(Expression(kind="field_ref", name=column), alias=column)
            for column in dataset.columns
        ]

    def _validate(self, query: QuerySpec, dataset: Dataset) -> None:
        expressions = [item.expression for item in query.select]
        expressions.extend(predicate.expression for predicate in query.filters)
        expressions.extend(query.group_by)
        output_names = {self._output_name(item) for item in query.select}
        expressions.extend(
            key.expression
            for key in query.sort_by
            if not (key.expression.kind == "field_ref" and key.expression.name in output_names)
        )
        for expression in expressions:
            for node in self._walk_resolved(expression):
                if node.kind == "opaque":
                    raise OpaqueDAXExecutionUnavailableError(
                        "Opaque DAX requires an authorized DAX execution capability; "
                        "the neutral runtime will not reinterpret it."
                    )
                if (
                    node.kind == "field_ref"
                    and not dataset.has_column(node.name)
                    and node.name not in self.calculated_fields
                ):
                    raise UnknownFieldError(
                        f"campo {node.name!r} no existe en dataset {dataset.name!r}"
                    )
                if node.kind == "measure_ref" and node.name not in self.metrics:
                    raise UnknownFieldError(f"medida desconocida: {node.name!r}")
                if (
                    node.kind == "lookup_map"
                    and not dataset.has_column(node.name)
                    and node.name not in self.calculated_fields
                ):
                    raise UnknownFieldError(
                        f"campo {node.name!r} no existe en dataset {dataset.name!r}"
                    )
                if node.kind == "parameter_ref" and node.name not in self.parameters:
                    raise UnknownFieldError(f"parámetro desconocido: {node.name!r}")
                if (
                    node.kind == "set_membership"
                    and not dataset.has_column(node.entity)
                    and node.entity not in self.calculated_fields
                ):
                    raise UnknownFieldError(
                        f"campo {node.entity!r} no existe en dataset {dataset.name!r}"
                    )

    def _walk_resolved(self, expression: Expression, seen: set[str] | None = None):
        seen = seen or set()
        for node in walk_expression(expression):
            yield node
            if node.kind == "measure_ref" and node.name in self.metrics and node.name not in seen:
                seen.add(node.name)
                yield from self._walk_resolved(self.metrics[node.name].expression, seen)
            if (
                node.kind == "field_ref"
                and node.name in self.calculated_fields
                and f"field:{node.name}" not in seen
            ):
                seen.add(f"field:{node.name}")
                yield from self._walk_resolved(self.calculated_fields[node.name], seen)

    def _predicate_ok(self, predicate: Predicate, row: Mapping[str, Any]) -> bool:
        value = self._evaluate(predicate.expression, [row], row)
        values = predicate.values
        op = predicate.op
        if op == "is_null":
            return value is None
        if op == "is_not_null":
            return value is not None
        if op == "eq":
            return value == values[0]
        if op == "ne":
            return value != values[0]
        if op == "in":
            return value in values
        if op == "not_in":
            return value not in values
        if value is None:
            return False
        if op in {
            "contains",
            "not_contains",
            "starts_with",
            "not_starts_with",
            "ends_with",
            "not_ends_with",
        }:
            source = str(value).casefold()
            pattern = str(values[0]).casefold()
            matches = (
                pattern in source
                if op in {"contains", "not_contains"}
                else source.startswith(pattern)
                if op in {"starts_with", "not_starts_with"}
                else source.endswith(pattern)
            )
            return not matches if op.startswith("not_") else matches
        try:
            if op == "between":
                return values[0] <= value <= values[1]
            if op == "gt":
                return value > values[0]
            if op == "gte":
                return value >= values[0]
            if op == "lt":
                return value < values[0]
            if op == "lte":
                return value <= values[0]
        except TypeError:
            return False
        raise UnknownFieldError(f"operador no implementado: {op!r}")

    def _aggregate(
        self,
        select: Sequence[SelectItem],
        output_names: Sequence[str],
        group_by: Sequence[Expression],
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        order: list[tuple[Any, ...]] = []
        if group_by:
            for row in rows:
                key = tuple(
                    self._evaluate(expr, [row], row, all_rows=rows, group_by=group_by)
                    for expr in group_by
                )
                if key not in groups:
                    order.append(key)
                groups[key].append(row)
        else:
            groups[()] = rows
            order.append(())
        return [
            self._project_group(select, output_names, groups[key], rows, group_by) for key in order
        ]

    def _project_row(
        self,
        select: Sequence[SelectItem],
        output_names: Sequence[str],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            name: self._evaluate(item.expression, [row], row)
            for item, name in zip(select, output_names, strict=True)
        }

    def _project_group(
        self,
        select: Sequence[SelectItem],
        output_names: Sequence[str],
        rows: list[dict[str, Any]],
        all_rows: list[dict[str, Any]],
        group_by: Sequence[Expression],
    ) -> dict[str, Any]:
        first = rows[0] if rows else {}
        return {
            name: self._evaluate(item.expression, rows, first, all_rows=all_rows, group_by=group_by)
            for item, name in zip(select, output_names, strict=True)
        }

    def _sort(
        self,
        query: QuerySpec,
        select: Sequence[SelectItem],
        output_names: Sequence[str],
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not query.sort_by:
            return rows
        aliases = {
            self._expression_name(item.expression): name
            for item, name in zip(select, output_names, strict=True)
        }
        output = list(rows)
        for key in reversed(query.sort_by):
            column = aliases.get(
                self._expression_name(key.expression), self._expression_name(key.expression)
            )
            if not column or any(column not in row for row in output):
                raise UnknownFieldError(f"ordenación fuera de columnas de salida: {column!r}")
            output.sort(
                key=lambda row, name=column: _sort_key(row.get(name)),
                reverse=key.direction == "desc",
            )
        return output

    def _evaluate(
        self,
        expression: Expression,
        rows: Sequence[Mapping[str, Any]],
        row: Mapping[str, Any],
        *,
        all_rows: Sequence[Mapping[str, Any]] | None = None,
        group_by: Sequence[Expression] = (),
    ) -> Any:
        all_rows = all_rows if all_rows is not None else rows
        kind = expression.kind
        if kind == "literal":
            return expression.value
        if kind == "field_ref":
            return self._field_value(
                expression.name, rows, row, all_rows=all_rows, group_by=group_by
            )
        if kind == "parameter_ref":
            return self.parameters[expression.name]
        if kind == "measure_ref":
            return self._evaluate(
                self.metrics[expression.name].expression,
                rows,
                row,
                all_rows=all_rows,
                group_by=group_by,
            )
        if kind == "principal":
            raise UnknownFieldError("principal requiere un contexto RLS explícito")
        if kind == "agg":
            return self._aggregate_expression(expression, rows)
        if kind == "conditional":
            condition = self._evaluate(
                expression.children[0], rows, row, all_rows=all_rows, group_by=group_by
            )
            branch = expression.children[1] if condition else expression.children[2]
            return self._evaluate(branch, rows, row, all_rows=all_rows, group_by=group_by)
        if kind == "lod":
            return self._evaluate_lod(expression, rows, row, all_rows, group_by)
        if kind == "window":
            return self._evaluate_window(expression, rows, row, all_rows, group_by)
        if kind == "lookup_map":
            source = self._field_value(
                expression.name, rows, row, all_rows=all_rows, group_by=group_by
            )
            mapping = expression.value
            assert isinstance(mapping, Mapping)
            return mapping.get(str(source), source)
        if kind == "set_membership":
            members = self.parameters.get(expression.name, ())
            source = self._field_value(
                expression.entity, rows, row, all_rows=all_rows, group_by=group_by
            )
            return True if source in members else None
        if kind == "modify":
            selected = list(rows)
            for condition in expression.children[1:]:
                selected = [r for r in selected if bool(self._evaluate(condition, [r], r))]
            first = selected[0] if selected else {}
            return self._evaluate(expression.children[0], selected, first)
        values = [
            self._evaluate(child, rows, row, all_rows=all_rows, group_by=group_by)
            for child in expression.children
        ]
        if kind == "unary":
            return self._unary(expression.op, values[0])
        if kind == "binary":
            return self._binary(expression.op, values[0], values[1])
        if kind == "func":
            return self._function(expression.name, values)
        raise UnknownFieldError(f"expresión no implementada: {kind!r}")

    def _field_value(
        self,
        name: str,
        rows: Sequence[Mapping[str, Any]],
        row: Mapping[str, Any],
        *,
        all_rows: Sequence[Mapping[str, Any]],
        group_by: Sequence[Expression],
    ) -> Any:
        """Resuelve una columna física o calculada mediante el IR neutral."""
        if name in row:
            return row.get(name)
        calculated = self.calculated_fields.get(name)
        if calculated is None:
            return None
        return self._evaluate(calculated, rows, row, all_rows=all_rows, group_by=group_by)

    def _aggregate_expression(
        self, expression: Expression, rows: Sequence[Mapping[str, Any]]
    ) -> Any:
        selected = rows
        if len(expression.children) == 2:
            selected = [
                row for row in rows if bool(self._evaluate(expression.children[1], [row], row))
            ]
        values = [self._evaluate(expression.children[0], [row], row) for row in selected]
        values = [value for value in values if value is not None]
        name = expression.name
        if name == "count":
            return len(values)
        if name == "count_distinct":
            return len(set(values))
        if not values:
            return None
        if name == "sum":
            return sum(values)
        if name == "avg":
            return sum(values) / len(values)
        if name == "min":
            return min(values)
        if name == "max":
            return max(values)
        if name == "median":
            return median(values)
        raise UnknownFieldError(f"agregación no implementada: {name!r}")

    def _lod_dimension_key(self, expression: Expression) -> tuple[str, ...] | None:
        """Return a stable identity for supported LOD dimensions.

        Tableau set references are lowered to ``set_membership`` expressions.
        A calculated field that is exactly a set membership must therefore
        compare as the same LOD dimension instead of being silently ignored.
        """
        if expression.kind == "field_ref":
            calculated = self.calculated_fields.get(expression.name)
            if calculated is not None and calculated.kind == "set_membership":
                return ("set_membership", calculated.name, calculated.entity)
            return ("field_ref", expression.entity, expression.name)
        if expression.kind == "set_membership":
            return ("set_membership", expression.name, expression.entity)
        return None

    def _lod_dimension_value(
        self,
        expression: Expression,
        candidate: Mapping[str, Any],
        all_rows: Sequence[Mapping[str, Any]],
    ) -> Any:
        return self._evaluate(
            expression,
            [candidate],
            candidate,
            all_rows=all_rows,
            group_by=(),
        )

    def _evaluate_lod(
        self,
        expression: Expression,
        rows: Sequence[Mapping[str, Any]],
        row: Mapping[str, Any],
        all_rows: Sequence[Mapping[str, Any]],
        group_by: Sequence[Expression],
    ) -> Any:
        dimensions = list(expression.children[1:])
        dimension_keys = [self._lod_dimension_key(item) for item in dimensions]
        if any(key is None for key in dimension_keys):
            raise UnknownFieldError("LOD dimension kind is not supported by the neutral runtime")

        group_dimensions = [item for item in group_by if self._lod_dimension_key(item) is not None]
        if expression.name == "fixed":
            retained = dimensions
        elif expression.name == "exclude":
            excluded = {key for key in dimension_keys if key is not None}
            retained = [
                item for item in group_dimensions if self._lod_dimension_key(item) not in excluded
            ]
        else:  # include
            retained = list(group_dimensions)
            known = {self._lod_dimension_key(item) for item in retained}
            retained.extend(
                item for item in dimensions if self._lod_dimension_key(item) not in known
            )

        current_key = tuple(
            self._lod_dimension_value(item, row, all_rows) for item in retained
        )
        selected = [
            candidate
            for candidate in all_rows
            if tuple(
                self._lod_dimension_value(item, candidate, all_rows) for item in retained
            )
            == current_key
        ]
        body = expression.children[0]
        if expression.name != "include" or not dimensions:
            first = selected[0] if selected else {}
            return self._evaluate(body, selected, first, all_rows=all_rows, group_by=retained)
        buckets: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for candidate in selected:
            key = tuple(
                self._lod_dimension_value(item, candidate, all_rows) for item in retained
            )
            buckets[key].append(candidate)
        values = [
            self._evaluate(body, bucket, bucket[0], all_rows=all_rows, group_by=retained)
            for bucket in buckets.values()
        ]
        numeric = [value for value in values if value is not None]
        return sum(numeric) if numeric else None

    def _evaluate_window(
        self,
        expression: Expression,
        rows: Sequence[Mapping[str, Any]],
        row: Mapping[str, Any],
        all_rows: Sequence[Mapping[str, Any]],
        group_by: Sequence[Expression],
    ) -> Any:
        if expression.name == "size":
            if not group_by:
                return len(rows)
            return len(
                {
                    tuple(self._evaluate(item, [candidate], candidate) for item in group_by)
                    for candidate in all_rows
                }
            )
        if expression.name == "lookup":
            lookup_expression = next(iter(expression.children), None)
            if lookup_expression is None:
                raise UnknownFieldError("lookup requiere una expresión")
            offset = (
                self._evaluate(expression.children[1], rows, row)
                if len(expression.children) > 1
                else 0
            )
            if offset != 0:
                raise UnknownFieldError("lookup sólo admite offset cero en el runtime actual")
            return self._evaluate(
                lookup_expression, rows, row, all_rows=all_rows, group_by=group_by
            )
        window_expression = next(iter(expression.children), None)
        if window_expression is None:
            raise UnknownFieldError("window requiere una expresión")
        if group_by:
            buckets: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
            for candidate in all_rows:
                key = tuple(self._evaluate(item, [candidate], candidate) for item in group_by)
                buckets[key].append(candidate)
            values = [
                self._evaluate(window_expression, bucket, bucket[0], all_rows=all_rows)
                for bucket in buckets.values()
            ]
        else:
            first = all_rows[0] if all_rows else {}
            values = [self._evaluate(window_expression, all_rows, first, all_rows=all_rows)]
        values = [value for value in values if value is not None]
        if not values:
            return None
        return {"min": min, "max": max, "sum": sum}[expression.name](values)

    @staticmethod
    def _unary(op: str, value: Any) -> Any:
        if op == "not":
            return not bool(value)
        if op == "neg":
            return None if value is None else -value
        raise UnknownFieldError(f"operador unario no implementado: {op!r}")

    @staticmethod
    def _binary(op: str, left: Any, right: Any) -> Any:
        if op == "and":
            return bool(left) and bool(right)
        if op == "or":
            return bool(left) or bool(right)
        if op == "coalesce":
            return left if left is not None else right
        if op in {"eq", "ne"}:
            return left == right if op == "eq" else left != right
        if left is None or right is None:
            return None
        operations = {
            "add": lambda: left + right,
            "sub": lambda: left - right,
            "mul": lambda: left * right,
            "div": lambda: None if right == 0 else left / right,
            "mod": lambda: None if right == 0 else left % right,
            "gt": lambda: left > right,
            "ge": lambda: left >= right,
            "lt": lambda: left < right,
            "le": lambda: left <= right,
            "concat": lambda: f"{left}{right}",
        }
        try:
            return operations[op]()
        except (KeyError, TypeError, ValueError):
            if op not in operations:
                raise UnknownFieldError(f"operador binario no implementado: {op!r}") from None
            return None

    @staticmethod
    def _function(name: str, values: list[Any]) -> Any:
        value = values[0] if values else None
        funcs = {
            "abs": lambda: None if value is None else abs(value),
            "round": lambda: (
                None if value is None else round(value, int(values[1]) if len(values) > 1 else 0)
            ),
            "floor": lambda: None if value is None else math.floor(value),
            "ceil": lambda: None if value is None else math.ceil(value),
            "coalesce": lambda: next((item for item in values if item is not None), None),
            "is_blank": lambda: value is None or value == "",
            "lower": lambda: None if value is None else str(value).lower(),
            "upper": lambda: None if value is None else str(value).upper(),
            "substring": lambda: (
                None
                if value is None
                else str(value)[int(values[1]) : int(values[1]) + int(values[2])]
            ),
            "length": lambda: None if value is None else len(value),
            "trim": lambda: None if value is None else str(value).strip(),
            "year": lambda: None if value is None else value.year,
            "month": lambda: None if value is None else value.month,
            "day": lambda: None if value is None else value.day,
            "quarter": lambda: None if value is None else (value.month - 1) // 3 + 1,
            "today": date.today,
            "now": datetime.now,
            "date_add": lambda: (
                None if value is None else value + timedelta(**{str(values[1]): int(values[2])})
            ),
            "date_diff": lambda: (
                None if value is None or values[1] is None else (values[1] - value).days
            ),
            "cast": lambda: value,
            "contains": lambda: False if value is None else str(values[1]) in str(value),
            "regexp_extract": lambda: (
                None
                if value is None
                else (
                    match.group(int(values[2]))
                    if (match := re.search(str(values[1]), str(value)))
                    else None
                )
            ),
            "regexp_replace": lambda: (
                None if value is None else re.sub(str(values[1]), str(values[2]), str(value))
            ),
            "split": lambda: (
                None
                if value is None
                else (
                    parts[int(values[2]) - 1]
                    if 0 < int(values[2]) <= len(parts := str(value).split(str(values[1])))
                    else None
                )
            ),
            "to_string": lambda: None if value is None else str(value),
        }
        try:
            return funcs[name]()
        except KeyError:
            raise UnknownFieldError(f"función no implementada: {name!r}") from None

    def _contains_aggregate(self, expression: Expression) -> bool:
        return any(
            node.kind == "agg"
            or (node.kind == "measure_ref" and self._metric_contains_aggregate(node.name, set()))
            for node in walk_expression(expression)
        )

    def _contains_row_reference(self, expression: Expression) -> bool:
        """Distingue campos por fila de parámetros/literales escalares."""
        return any(
            node.kind in {"field_ref", "lookup_map"} for node in self._walk_resolved(expression)
        )

    def _metric_contains_aggregate(self, name: str, seen: set[str]) -> bool:
        if name in seen or name not in self.metrics:
            return False
        seen.add(name)
        expression = self.metrics[name].expression
        return any(
            node.kind == "agg"
            or (node.kind == "measure_ref" and self._metric_contains_aggregate(node.name, seen))
            for node in walk_expression(expression)
        )

    @staticmethod
    def _expression_name(expression: Expression) -> str:
        return expression.name if expression.kind in {"field_ref", "measure_ref"} else ""

    def _output_name(self, item: SelectItem) -> str:
        name = item.alias or self._expression_name(item.expression)
        if not name:
            raise UnknownFieldError("cada proyección calculada requiere alias")
        return name

    def _output_names(self, select: Sequence[SelectItem]) -> tuple[str, ...]:
        """Devuelve nombres únicos y deterministas para las proyecciones."""
        counts: dict[str, int] = {}
        output: list[str] = []
        for item in select:
            base = self._output_name(item)
            index = counts.get(base, 0)
            output.append(base if index == 0 else f"{base}_{index}")
            counts[base] = index + 1
        return tuple(output)
