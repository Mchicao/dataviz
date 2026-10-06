"""Generador de consultas DAX ``EVALUATE`` por visual del RenderPlan.

Lee un plan de render (:class:`~core.compilers.render_plan.RenderPlan`) y una
IR semÃƒÂ¡ntica neutra (:class:`~core.contracts.semantic_ir.SemanticModel`) desde
sus JSON canÃƒÂ³nicos y genera, para cada visual con medida calculable, una
consulta DAX estÃƒÂ¡ndar que calcula la medida del visual aplicando sus filtros.

La medida se referencia por su nombre tal como estÃƒÂ¡ declarada en la IR
semÃƒÂ¡ntica: este mÃƒÂ³dulo nunca reescribe el DAX de una medida ni ejecuta nada,
sÃƒÂ³lo produce texto DAX vÃƒÂ¡lido. Los visuales sin medida resoluble (rol
``value`` sin prefijo ``measure:`` o medida desconocida en el modelo) se
omiten de la lista; el orÃƒÂ¡culo de fidelidad los clasifica aparte.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.compilers.render_plan import RenderPlan, VisualSpec
from core.contracts.query_ast import Predicate
from core.contracts.semantic_ir import Expression, SemanticModel


@dataclass(frozen=True)
class VisualDaxQuery:
    """Consulta DAX generada para un visual individual del RenderPlan."""

    visual_id: str
    measure: str
    dax: str


#: Operadores binarios neutros -> sÃƒÂ­mbolo DAX.
_BINARY_DAX: dict[str, str] = {
    "add": "+",
    "sub": "-",
    "mul": "*",
    "div": "/",
    "concat": "&",
    "eq": "=",
    "ne": "<>",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
    "and": "&&",
    "or": "||",
}

#: Operadores de predicado comparativos (gramÃƒÂ¡tica de ``query_ast``) -> DAX.
_PREDICATE_SYMBOLS: dict[str, str] = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

#: Agregaciones neutras -> funciones DAX.
_AGG_DAX: dict[str, str] = {
    "sum": "SUM",
    "avg": "AVERAGE",
    "count": "COUNT",
    "count_distinct": "DISTINCTCOUNT",
    "min": "MIN",
    "max": "MAX",
    "median": "MEDIAN",
}

#: Funciones escalares neutras con equivalente DAX directo; lo fuera del mapa
#: se rechaza explÃƒÂ­citamente en lugar de emitir DAX invÃƒÂ¡lido.
_FUNC_DAX: dict[str, str] = {
    "abs": "ABS",
    "round": "ROUND",
    "floor": "FLOOR",
    "ceil": "CEILING",
    "coalesce": "COALESCE",
    "is_blank": "ISBLANK",
    "lower": "LOWER",
    "upper": "UPPER",
    "substring": "MID",
    "length": "LEN",
    "trim": "TRIM",
    "year": "YEAR",
    "month": "MONTH",
    "day": "DAY",
    "quarter": "QUARTER",
    "date_add": "DATEADD",
    "date_diff": "DATEDIFF",
    "today": "TODAY",
    "now": "NOW",
    "contains": "CONTAINSSTRING",
    "to_string": "FORMAT",
}


def _quote(text: str) -> str:
    """Escapa un literal de cadena DAX entre comillas simples."""
    return "'" + text.replace("'", "''") + "'"


def _quote_string(text: str) -> str:
    """Escapa un literal de texto DAX entre comillas dobles."""
    return '"' + text.replace('"', '""') + '"'

def _literal(value: object) -> str:
    """Renderiza un escalar neutro como literal DAX."""
    if value is None:
        return "BLANK()"
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, (int, float)):
        return repr(value)
    return _quote_string(str(value))


def _field_ref(expr: Expression) -> str:
    """Referencia de columna DAX ``'Tabla'[Campo]`` (o ``[Campo]`` sin entidad)."""
    table = _quote(expr.entity) if expr.entity else ""
    return f"{table}[{expr.name}]"


def _expression(expr: Expression) -> str:
    """Renderiza una expresiÃƒÂ³n neutra como texto DAX.

    Raises:
        ValueError: si la expresiÃƒÂ³n usa un nodo sin representaciÃƒÂ³n DAX
            estÃƒÂ¡ndar (LOD, window, lookup_map, set_membership o principal).
    """
    kind = expr.kind
    if kind == "literal":
        return _literal(expr.value)
    if kind == "field_ref":
        return _field_ref(expr)
    if kind in {"parameter_ref", "measure_ref"}:
        return f"[{expr.name}]"
    if kind == "unary":
        operand = _expression(expr.children[0])
        return f"-{operand}" if expr.op == "neg" else f"NOT {operand}"
    if kind == "binary":
        left, right = (_expression(c) for c in expr.children)
        return f"({left} {_BINARY_DAX[expr.op]} {right})"
    if kind == "func":
        try:
            name = _FUNC_DAX[expr.name]
        except KeyError as exc:
            raise ValueError(f"funciÃƒÂ³n escalar sin equivalente DAX: {expr.name!r}") from exc
        args = ", ".join(_expression(c) for c in expr.children)
        return f"{name}({args})"
    if kind == "agg":
        agg = f"{_AGG_DAX[expr.name]}({_expression(expr.children[0])})"
        if len(expr.children) == 2:
            return f"CALCULATE({agg}, {_expression(expr.children[1])})"
        return agg
    if kind == "modify":
        body = _expression(expr.children[0])
        filters = ", ".join(_expression(f) for f in expr.children[1:])
        return f"CALCULATE({body}, {filters})" if filters else body
    if kind == "conditional":
        cond, true_expr, false_expr = (_expression(c) for c in expr.children)
        return f"IF({cond}, {true_expr}, {false_expr})"
    raise ValueError(f"expresiÃƒÂ³n no representable en DAX: kind={kind!r}")


def _predicate(pred: Predicate, default_entity: str = "") -> str:
    """Traduce un predicado neutro del visual a una condiciÃƒÂ³n booleana DAX."""
    expr = pred.expression
    if expr.kind == "field_ref" and not expr.entity and default_entity:
        column = f"{_quote(default_entity)}[{expr.name}]"
    else:
        column = _expression(expr)
    op = pred.op
    if op in {"in", "not_in"}:
        values = ", ".join(_literal(v) for v in pred.values)
        condition = f"{column} IN {{{values}}}"
        return condition if op == "in" else f"NOT {condition}"
    if op == "between":
        low, high = (_literal(v) for v in pred.values)
        return f"{column} >= {low} && {column} <= {high}"
    if op in {"is_null", "is_not_null"}:
        condition = f"ISBLANK({column})"
        return condition if op == "is_null" else f"NOT {condition}"
    if op in {"contains", "not_contains", "starts_with", "ends_with"}:
        needle = _literal(pred.values[0])
        if op in {"starts_with", "ends_with"}:
            side = "LEFT" if op == "starts_with" else "RIGHT"
            condition = f"{side}({column}, LEN({needle})) = {needle}"
        else:
            condition = f"CONTAINSSTRING({column}, {needle})"
            if op == "not_contains":
                condition = f"NOT {condition}"
        return condition
    return f"{column} {_PREDICATE_SYMBOLS[op]} {_literal(pred.values[0])}"


def _grouping_columns(
    visual: VisualSpec,
    case_surrogates: dict[tuple[str, str], str] | None = None,
) -> list[str]:
    """Columnas de dimensiÃƒÂ³n del visual, sin duplicados y en orden estable.

    Fuentes: ``query.group_by`` (referencias de campo calificadas) y los roles
    de datos distintos de ``value`` cuyo valor referencia una entidad
    (prefijo ``entity:``). Las referencias sin entidad se califican con la
    tabla del ``from_datasource`` del visual para producir DAX ejecutable.
    """
    columns: list[str] = []
    case_surrogates = case_surrogates or {}
    if visual.query is not None:
        for expr in visual.query.group_by:
            if expr.kind == "field_ref":
                entity = expr.entity or visual.query.from_datasource
                field_name = case_surrogates.get((entity, expr.name), expr.name)
                if entity:
                    reference = f"{_quote(entity)}[{field_name}]"
                else:
                    reference = f"[{field_name}]"
                columns.append(reference)
    for role, ref in visual.data_roles.items():
        if role == "value" or not ref.startswith("entity:"):
            continue
        qualified = ref.split(":", 1)[1]
        table, _, field = qualified.rpartition(".")
        if table and field:
            field = case_surrogates.get((table, field), field)
            columns.append(f"{_quote(table)}[{field}]")
    return list(dict.fromkeys(columns))



def _forecast_period_dax(column: str, period: str) -> tuple[str, str]:
    """Return DAX expressions for period start and exclusive period end."""
    if period == "day":
        return column, f"({column} + 1)"
    if period == "week":
        start = f"({column} - WEEKDAY({column}, 2) + 1)"
        return start, "(__g0_start + 7)"
    if period == "month":
        return f"DATE(YEAR({column}), MONTH({column}), 1)", "EDATE(__g0_start, 1)"
    if period == "quarter":
        start = f"DATE(YEAR({column}), 1 + 3 * INT((MONTH({column}) - 1) / 3), 1)"
        return start, "EDATE(__g0_start, 3)"
    if period == "year":
        return f"DATE(YEAR({column}), 1, 1)", "DATE(YEAR(__g0_start) + 1, 1, 1)"
    raise ValueError(f"forecast period without DAX grouping support: {period!r}")


def _forecast_visual_dax(visual: VisualSpec, measure: str, filters: list[str]) -> str | None:
    """Build a period-granular historical probe for a forecast visual.

    Forecast estimates themselves remain outside this DAX parity channel. This
    query validates the historical series at the same temporal grain declared
    by ForecastSpec, instead of accidentally comparing raw daily groups.
    """
    forecast = visual.forecast
    query = visual.query
    if forecast is None or query is None:
        return None
    time_expr = next(
        (expr for expr in query.group_by if expr.kind == "field_ref" and expr.name == forecast.time_field),
        None,
    )
    # The generic multi-dimensional forecast case needs a wider grouping table;
    # do not silently emit a semantically different query.
    if time_expr is None or len(query.group_by) != 1:
        return None
    if not time_expr.entity and query.from_datasource:
        time_expr = Expression(kind="field_ref", entity=query.from_datasource, name=time_expr.name)
    column = _field_ref(time_expr)
    period_start, period_end = _forecast_period_dax(column, forecast.period)
    base_table = _quote(time_expr.entity or query.from_datasource)
    periods = f'DISTINCT(SELECTCOLUMNS({base_table}, "__g0_period", {period_start}))'
    if filters:
        periods = f"CALCULATETABLE({periods}, {', '.join(filters)})"
    period_vars = "VAR __g0_periods = " + periods
    if forecast.ignore_last:
        period_vars = (
            "VAR __g0_all_periods = " + periods
            + f" VAR __g0_periods = TOPN(MAX(0, COUNTROWS(__g0_all_periods) - {forecast.ignore_last}), "
            + "__g0_all_periods, [__g0_period], ASC)"
        )
    value_filters = [*filters, f"FILTER(ALL({column}), {column} >= __g0_start && {column} < {period_end})"]
    value_expression = f"CALCULATE([{measure}], {', '.join(value_filters)})"
    return (
        "EVALUATE " + period_vars
        + " RETURN SELECTCOLUMNS("
        + 'ADDCOLUMNS(__g0_periods, "__g0_value", VAR __g0_start = [__g0_period] RETURN '
        + value_expression
        + f'), "{forecast.time_field}", [__g0_period], "value", [__g0_value])'
    )

def _visual_dax(
    visual: VisualSpec,
    measure: str,
    case_surrogates: dict[tuple[str, str], str] | None = None,
) -> str:
    """Construye el ``EVALUATE`` estÃƒÂ¡ndar para un visual con medida conocida."""
    filters = (
        [_predicate(p, visual.query.from_datasource) for p in visual.query.filters]
        if visual.query is not None
        else []
    )
    forecast_dax = _forecast_visual_dax(visual, measure, filters)
    if forecast_dax is not None:
        return forecast_dax
    value_expression = f"[{measure}]"
    if filters:
        value_expression = f"CALCULATE({value_expression}, {', '.join(filters)})"
    columns = _grouping_columns(visual, case_surrogates)
    if columns:
        base_table = columns[0].split("[", 1)[0]
        grouping_table = (
            f"CALCULATETABLE({base_table}, {', '.join(filters)})" if filters else base_table
        )
        column_list = ", ".join(columns)
        table_expression = (
            f'SUMMARIZE({grouping_table}, {column_list}, "value", {value_expression})'
        )
        return f"EVALUATE {table_expression}"
    return f'EVALUATE ROW("value", {value_expression})'


def _resolve_visual_measure(visual: VisualSpec, known_measures: set[str]) -> str | None:
    """Resuelve la medida del visual: rol ``value`` primero, luego el ``select``.

    Los planes derivados de Tableau suelen materializar la medida en el
    ``query.select`` (``measure_ref``) en lugar del rol ``value``; se aceptan
    ambas fuentes de forma genÃƒÂ©rica, sin codificar demos.
    """
    reference = visual.data_roles.get("value", "")
    if reference.startswith("measure:"):
        measure = reference.split(":", 1)[1]
        if measure in known_measures:
            return measure
    if visual.query is not None:
        for item in visual.query.select:
            expression = getattr(item, "expression", None)
            if (
                expression is not None
                and expression.kind == "measure_ref"
                and expression.name in known_measures
            ):
                return expression.name
    return None


def generate_dax_queries_from_objects(
    plan: RenderPlan,
    model: SemanticModel,
    *,
    case_surrogates: dict[tuple[str, str], str] | None = None,
) -> list[VisualDaxQuery]:
    """Genera consultas DAX desde objetos :class:`RenderPlan`/`SemanticModel` ya cargados.

    Args:
        plan: Plan de render cargado.
        model: IR semÃƒÂ¡ntica cargada.

    Returns:
        Consultas en el orden del plan; los visuales cuya medida no puede
        resolverse o cuya consulta no es representable en DAX estÃƒÂ¡ndar quedan
        excluidos (el orÃƒÂ¡culo de fidelidad los clasifica aparte).
    """
    known_measures = {metric.name for metric in model.metrics}
    queries: list[VisualDaxQuery] = []
    for visual in plan.visuals:
        measure = _resolve_visual_measure(visual, known_measures)
        if measure is None:
            continue
        try:
            dax = _visual_dax(visual, measure, case_surrogates)
        except (ValueError, KeyError):
            # ExpresiÃƒÂ³n sin representaciÃƒÂ³n DAX estÃƒÂ¡ndar: se excluye el visual
            # en lugar de emitir una consulta invÃƒÂ¡lida.
            continue
        queries.append(
            VisualDaxQuery(
                visual_id=visual.name,
                measure=measure,
                dax=dax,
            )
        )
    return queries


def generate_dax_queries(render_plan_path: Path, semantic_ir_path: Path) -> list[VisualDaxQuery]:
    """Genera una consulta DAX ``EVALUATE`` por cada visual con medida calculable.

    Args:
        render_plan_path: Ruta al JSON canÃƒÂ³nico del :class:`RenderPlan`.
        semantic_ir_path: Ruta al JSON canÃƒÂ³nico del :class:`SemanticModel`.

    Returns:
        Consultas en el orden del plan; los visuales cuya medida no puede
        resolverse quedan excluidos (el orÃƒÂ¡culo los clasifica aparte).
    """
    plan = RenderPlan.from_json(render_plan_path.read_text(encoding="utf-8"))
    model = SemanticModel.from_json(semantic_ir_path.read_text(encoding="utf-8"))
    return generate_dax_queries_from_objects(plan, model)


__all__ = ["VisualDaxQuery", "generate_dax_queries", "generate_dax_queries_from_objects"]
