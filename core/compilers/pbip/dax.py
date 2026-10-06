"""Neutral ``Expression`` grammar -> DAX string translator.

Pure function: walks the closed neutral expression grammar declared in
:mod:`core.contracts.semantic_ir` and emits a best-effort DAX string plus a
list of human-readable approximation notes for nodes that cannot be mapped
1:1 to native DAX (e.g. an aggregation whose operand is not a bare column).

Diseño (Ponytail): sólo depende de la stdlib y del contrato neutral. No importa
destinos concretos (PBIR / TOM / motores DAX) ni reparséa Tableau; traduce el
IR neutral ya aceptado. Cada nota de aproximación es explícita para que el
adaptador PBIP la conserve y la haga visible en los artefactos generados.

Boundary: stdlib + ``core.contracts`` only.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from core.contracts.semantic_ir import DataType, walk_expression

if TYPE_CHECKING:
    from core.contracts.semantic_ir import Expression

#: Aggregaciones neutrales -> función DAX nativa (operando debe ser columna).
_AGG_TO_DAX: dict[str, str] = {
    "sum": "SUM",
    "avg": "AVERAGE",
    "count": "COUNT",
    "count_distinct": "DISTINCTCOUNT",
    "min": "MIN",
    "max": "MAX",
    "median": "MEDIAN",
}

#: Funciones escalares neutrales -> función DAX equivalente.
_SCALAR_FUNC_TO_DAX: dict[str, str] = {
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
}

#: Operadores binarios neutrales -> token DAX.
_BINARY_OP_TO_DAX: dict[str, str] = {
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


def quote_table(name: str) -> str:
    """Envuelve ``name`` entre comillas simples (identificador de tabla DAX).

    Escapa ``'`` interno duplicándolo, convención de DAX/TMDL.
    """
    return "'" + name.replace("'", "''") + "'"


def bracket_column(name: str) -> str:
    """Envuelve ``name`` entre corchetes (columna/medida DAX).

    Elimina corchetes preexistentes y escapa ``]`` interno duplicándolo.
    """
    clean = name
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]
    return "[" + clean.replace("]", "]]") + "]"


def _literal_to_dax(value: object) -> tuple[str, list[str]]:
    """Traduce un literal neutral a su forma DAX."""
    notes: list[str] = []
    if value is None:
        return "BLANK()", notes
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE", notes
    if isinstance(value, (int, float)):
        return str(value), notes
    text = str(value).replace('"', '""')
    return f'"{text}"', notes


def _field_ref_to_dax(entity: str, name: str) -> str:
    """Traduce una referencia a campo: ``'Table'[Col]`` o ``[Col]`` si no califica."""
    if entity:
        return f"{quote_table(entity)}{bracket_column(name)}"
    return bracket_column(name)


def _with_default_entity(expr: Expression, entity: str) -> Expression:
    children = tuple(_with_default_entity(child, entity) for child in expr.children)
    resolved = replace(expr, children=children) if children != expr.children else expr
    if entity and resolved.kind == "field_ref" and not resolved.entity:
        return replace(resolved, entity=entity)
    if entity and resolved.kind in {"agg", "window"} and not resolved.entity:
        return replace(resolved, entity=entity)
    return resolved


def expression_to_dax(expr: Expression, *, default_entity: str = "") -> tuple[str, list[str]]:
    """Traduce un :class:`Expression` neutral a DAX, devolviendo ``(dax, notas)``.

    Recorre el árbol recursivamente. Las notas (lista de cadenas) describen
    cada nodo que no admite una traducción 1:1 a DAX nativo para que el
    adaptador PBIP lo registre como aproximación explícita. La traducción es
    determinista: el mismo árbol produce siempre el mismo texto DAX.
    """
    notes: list[str] = []
    dax = _translate(_with_default_entity(expr, default_entity), notes)
    return dax, notes


def _translate(expr: Expression, notes: list[str]) -> str:
    kind = expr.kind

    if kind == "literal":
        text, lit_notes = _literal_to_dax(expr.value)
        notes.extend(lit_notes)
        return text

    if kind == "opaque":
        # Expression.__post_init__ restricts opaque expressions to DAX only.
        # Preserve the original body exactly; never quote or reinterpret it.
        return str(expr.value)

    if kind == "field_ref":
        return _field_ref_to_dax(expr.entity, expr.name)

    if kind in {"parameter_ref", "measure_ref"}:
        # Los parámetros y medidas se materializan como medidas DAX: [Nombre].
        return bracket_column(expr.name)

    if kind == "principal":
        return "USERPRINCIPALNAME()"

    if kind == "unary":
        operand = _translate(expr.children[0], notes)
        if expr.op == "neg":
            return f"-{operand}"
        return f"NOT {operand}"

    if kind == "binary":
        left_expr, right_expr = expr.children
        left = _translate(left_expr, notes)
        right = _translate(right_expr, notes)
        if expr.op in {"eq", "ne", "lt", "le", "gt", "ge"}:
            numeric_types = {DataType.INTEGER, DataType.DECIMAL}
            if left_expr.data_type in numeric_types and right_expr.data_type is DataType.STRING:
                fmt = "0" if left_expr.data_type is DataType.INTEGER else "0.################"
                left = f'FORMAT({left}, "{fmt}")'
                notes.append("integer/string comparison coerced by formatting numeric left operand")
            elif right_expr.data_type in numeric_types and left_expr.data_type is DataType.STRING:
                fmt = "0" if right_expr.data_type is DataType.INTEGER else "0.################"
                right = f'FORMAT({right}, "{fmt}")'
                notes.append("integer/string comparison coerced by formatting numeric right operand")
        token = _BINARY_OP_TO_DAX[expr.op]
        return f"({left} {token} {right})"

    if kind == "func":
        return _func_to_dax(expr, notes)

    if kind == "agg":
        return _agg_to_dax(expr, notes)

    if kind == "modify":
        return _modify_to_dax(expr, notes)

    if kind == "conditional":
        condition, when_true, when_false = (_translate(child, notes) for child in expr.children)
        return f"IF({condition}, {when_true}, {when_false})"

    if kind == "lookup_map":
        source = bracket_column(expr.name)
        pairs = []
        for key, value in expr.value.items():
            key_dax, _ = _literal_to_dax(key)
            value_dax, _ = _literal_to_dax(value)
            pairs.extend((key_dax, value_dax))
        return f"SWITCH({source}, {', '.join((*pairs, source))})"

    if kind == "set_membership":
        members = expr.value if isinstance(expr.value, (list, tuple)) else None
        if members is not None:
            notes.append(
                f"initial set membership {expr.name!r} materialized; interactive mutation is not preserved"
            )
            if not members:
                return "BLANK()"
            values = ", ".join(_literal_to_dax(member)[0] for member in members)
            return f"IF({bracket_column(expr.entity)} IN {{{values}}}, TRUE(), BLANK())"
        notes.append(
            f"set din?mico {expr.name!r}: PBIP no conserva la selecci?n interactiva inicial"
        )
        return "FALSE()"

    if kind == "window":
        return _window_to_dax(expr, notes)

    if kind == "lod":
        return _lod_to_dax(expr, notes)

    # La gramática es cerrada; si llegamos aquí el contrato ya validó kind.
    raise AssertionError(f"expression kind no cubierto: {kind!r}")


def _func_to_dax(expr: Expression, notes: list[str]) -> str:
    """Traduce una función escalar neutra a su equivalente DAX.

    Algunas funciones neutrales tienen aridad distinta en DAX (p. ej. ``round``
    necesita decimales, ``floor``/``ceil`` requieren significancia). En esos
    casos se emite una forma razonable y se registra una nota de aproximación.
    """
    name = expr.name
    args = [_translate(child, notes) for child in expr.children]
    dax_name = _SCALAR_FUNC_TO_DAX.get(name)
    if dax_name is None:
        if name == "to_string":
            return f'FORMAT({args[0]}, "")'
        if name == "split" and len(args) == 3:
            return f'PATHITEM(SUBSTITUTE({args[0]}, {args[1]}, "|"), {args[2]}, TEXT)'
        if name in {"regexp_extract", "regexp_replace"}:
            notes.append(f"{name} no tiene equivalente DAX nativo; se emite BLANK explícito")
            return "BLANK()"
        notes.append(f"función escalar sin equivalente DAX directo: {name!r}")
        # Best-effort: usar el nombre neutral en mayúsculas como llamada DAX.
        dax_name = name.upper()
    if name == "round":
        if len(args) < 2:
            args.append("0")
            notes.append("round sin decimales: se asume ROUND(x, 0)")
    elif name in {"floor", "ceil"}:
        if len(args) < 2:
            args.append("1")
            notes.append(f"{name} sin significancia: se asume significancia 1")
    elif name == "cast":
        # cast(value, tipo): DAX no tiene cast genérico; se aproxima con las
        # funciones VALUE/FORMAT/DATEVALUE según el tipo declarado.
        notes.append("cast aproximado: DAX no tiene operador cast genérico")
    return f"{dax_name}({', '.join(args)})"


def _window_to_dax(expr: Expression, notes: list[str]) -> str:
    entities = {
        node.entity
        for node in walk_expression(expr)
        if node.kind in {"field_ref", "agg"} and node.entity
    }
    if expr.entity:
        entities.add(expr.entity)
    selected_table = quote_table(next(iter(entities))) if len(entities) == 1 else ""
    if expr.name == "size":
        if selected_table:
            metadata = expr.value if isinstance(expr.value, dict) else {}
            order_fields = metadata.get("order_fields")
            if isinstance(order_fields, (list, tuple)) and order_fields:
                fields = ", ".join(
                    f"{selected_table}{bracket_column(str(field))}" for field in order_fields
                )
                return f"COUNTROWS(SUMMARIZE(ALLSELECTED({selected_table}), {fields}))"
            notes.append("SIZE Tableau aproximado con COUNTROWS(ALLSELECTED(tabla))")
            return f"COUNTROWS(ALLSELECTED({selected_table}))"
        notes.append("SIZE Tableau sin entidad de grano; se emite BLANK expl?cito")
        return "BLANK()"
    if expr.name == "lookup" and len(expr.children) >= 2:
        offset = expr.children[1]
        if offset.kind == "literal" and offset.value == 0:
            return _translate(expr.children[0], notes)
    if expr.name in {"min", "max", "sum"} and expr.children:
        inner = _translate(expr.children[0], notes)
        if not selected_table:
            notes.append(
                f"WINDOW_{expr.name.upper()} sin entidad de grano; se conserva la expresión interna"
            )
            return inner
        notes.append(f"WINDOW_{expr.name.upper()} aproximado sobre ALLSELECTED")
        if expr.name == "sum":
            return f"CALCULATE({inner}, ALLSELECTED({selected_table}))"
        return f"{expr.name.upper()}X(ALLSELECTED({selected_table}), CALCULATE({inner}))"
    notes.append(f"window {expr.name!r} sin equivalente DAX exacto")
    return "BLANK()"


def _lod_to_dax(expr: Expression, notes: list[str]) -> str:
    dimension_nodes = list(expr.children[1:])
    unsupported_dimensions = [child for child in dimension_nodes if child.kind != "field_ref"]
    if unsupported_dimensions:
        notes.append(
            f"LOD {expr.name!r} has a non-column dimension; emitted BLANK instead of dropping semantics"
        )
        return "BLANK()"

    body = _translate(expr.children[0], notes)
    dimensions = [_translate(child, notes) for child in dimension_nodes]
    if expr.name == "fixed":
        if not dimensions:
            return f"CALCULATE({body}, REMOVEFILTERS())"
        entities = {child.entity for child in dimension_nodes if child.entity}
        if len(entities) == 1:
            table = quote_table(next(iter(entities)))
            return f"CALCULATE({body}, ALLEXCEPT({table}, {', '.join(dimensions)}))"
        notes.append("LOD 'fixed' con dimensiones sin una tabla ?nica; aproximaci?n expl?cita")
        return body
    if expr.name == "exclude" and dimensions:
        filters = ", ".join(f"REMOVEFILTERS({dimension})" for dimension in dimensions)
        return f"CALCULATE({body}, {filters})"
    if expr.name == "include" and dimensions:
        notes.append("LOD 'include' has no exact PBIP grain mapping; emitted BLANK")
        return "BLANK()"
    notes.append(f"LOD {expr.name!r} requiere contexto de grano PBIP; aproximaci?n expl?cita")
    return body

def _agg_to_dax(expr: Expression, notes: list[str]) -> str:
    """Traduce una agregación neutra a DAX nativo cuando es posible.

    Caso nativo: ``agg(name, field_ref)`` -> ``NAME('Table'[Col])``.
    Si el operando no es una referencia a columna simple, la agregación en DAX
    requeriría un iterador (SUMX/...) con grano que el IR neutral no expone:
    se emite ``BLANK()`` con anotación y se registra la aproximación para que
    el resultado no sea silenciosamente incorrecto.
    """
    operand = expr.children[0]
    dax_name = _AGG_TO_DAX.get(expr.name, expr.name.upper())
    inner = _translate(operand, notes)
    filter_inner = ""
    if len(expr.children) == 2:
        filter_inner = _translate(expr.children[1], notes)

    if operand.kind == "set_membership" and expr.name == "count":
        if not expr.entity:
            raise ValueError("COUNT over set membership requires a grain entity")
        members = operand.value if isinstance(operand.value, (list, tuple)) else None
        if members is not None:
            notes.append(
                f"COUNT over initial set membership {operand.name!r} materialized; interactive mutation is not preserved"
            )
            if not members:
                core = "0"
            else:
                table = quote_table(expr.entity)
                values = ", ".join(_literal_to_dax(member)[0] for member in members)
                target = f"{table}{bracket_column(operand.entity)}"
                core = f"COUNTROWS(FILTER({table}, {target} IN {{{values}}}))"
        else:
            notes.append(
                "COUNT over dynamic set approximated with COUNTROWS; set selection is not preserved"
            )
            core = f"COUNTROWS({quote_table(expr.entity)})"
    elif operand.kind == "field_ref":
        core = f"{dax_name}({inner})"
    elif operand.kind in {"measure_ref", "agg", "lod", "window"}:
        notes.append(
            f"agregación {expr.name!r} exterior conservada como expresión ya agregada"
        )
        core = inner
    else:
        entities = {
            node.entity
            for node in walk_expression(operand)
            if node.kind == "field_ref" and node.entity
        }
        if not entities and expr.entity:
            entities.add(expr.entity)
        iterator = {
            "sum": "SUMX",
            "avg": "AVERAGEX",
            "min": "MINX",
            "max": "MAXX",
            "median": "MEDIANX",
            "count": "COUNTX",
        }.get(expr.name)
        if len(entities) != 1 or iterator is None:
            raise ValueError(f"agregación {expr.name!r} sobre expresión sin grano de entidad único")
        core = f"{iterator}({quote_table(next(iter(entities)))}, {inner})"

    if filter_inner:
        # agg con predicado de filtro interno -> CALCULATE(agg, filtro).
        return f"CALCULATE({core}, {filter_inner})"
    return core


def _modify_to_dax(expr: Expression, notes: list[str]) -> str:
    """Traduce un contexto modificado: ``CALCULATE(body, f1, f2, ...)``."""
    if not expr.children:
        raise AssertionError("modify requiere un cuerpo")
    body = _translate(expr.children[0], notes)
    filters = [_translate(child, notes) for child in expr.children[1:]]
    if not filters:
        return body
    return f"CALCULATE({body}, {', '.join(filters)})"


__all__ = [
    "bracket_column",
    "expression_to_dax",
    "quote_table",
]
