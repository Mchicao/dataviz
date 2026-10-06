"""Parser formal Tableau -> expresión semántica neutral."""

from __future__ import annotations

import re
from collections.abc import Sequence

from lark import Lark, Transformer, v_args

from core.contracts.semantic_ir import DataType, Expression

_GRAMMAR = r"""
?start: expr
?expr: if_expr | case_expr | or_expr
if_expr: "IF"i expr "THEN"i expr elseif_clause* else_clause? "END"i
elseif_clause: "ELSEIF"i expr "THEN"i expr
else_clause: "ELSE"i expr
case_expr: "CASE"i expr case_clause+ else_clause? "END"i
case_clause: "WHEN"i expr "THEN"i expr
?or_expr: and_expr ("OR"i and_expr)*
?and_expr: comparison ("AND"i comparison)*
?comparison: sum_expr (COMP_OP sum_expr)?
?sum_expr: product (ADD_OP product)*
?product: unary (MUL_OP unary)*
?unary: "NOT"i unary       -> not_expr
      | "-" unary           -> neg_expr
      | atom
?atom: parameter_ref
     | field_ref
     | lod_expr
     | function_call
     | NUMBER                -> number
     | STRING                -> string
     | "TRUE"i              -> true
     | "FALSE"i             -> false
     | "NULL"i              -> null
     | "(" expr ")"
parameter_ref: "[" "Parameters"i "]" "." "[" FIELD_NAME "]"
field_ref: "[" FIELD_NAME "]"
lod_expr: "{" LOD_KIND [field_ref ("," field_ref)*] ":" expr "}"
function_call: NAME "(" [expr ("," expr)*] ")"
COMP_OP: "<>" | "!=" | "<=" | ">=" | "=" | "<" | ">"
ADD_OP: "+" | "-"
MUL_OP: "*" | "/"
LOD_KIND: "FIXED"i | "INCLUDE"i | "EXCLUDE"i
NAME: /[A-Za-z_][A-Za-z0-9_]*/
FIELD_NAME: /[^\]]+/
STRING: /'(?:[^'\\]|\\.)*'/ | /"(?:[^"\\]|\\.)*"/
%import common.SIGNED_NUMBER -> NUMBER
%import common.WS
%ignore WS
"""

_PARSER = Lark(_GRAMMAR, parser="lalr", start="start")
_COMMENT_RE = re.compile(r"//[^\r\n]*")


def _binary(op: str, left: Expression, right: Expression) -> Expression:
    mapped = {
        "+": "add",
        "-": "sub",
        "*": "mul",
        "/": "div",
        "=": "eq",
        "!=": "ne",
        "<>": "ne",
        "<": "lt",
        "<=": "le",
        ">": "gt",
        ">=": "ge",
        "AND": "and",
        "OR": "or",
    }[op.upper()]
    return Expression(kind="binary", op=mapped, children=(left, right))


def _fold(items: Sequence[object]) -> Expression:
    result = items[0]
    assert isinstance(result, Expression)
    for index in range(1, len(items), 2):
        right = items[index + 1]
        assert isinstance(right, Expression)
        result = _binary(str(items[index]), result, right)
    return result


@v_args(inline=True)
class _ToExpression(Transformer):
    def number(self, token):  # noqa: ANN001
        text = str(token)
        value = float(text) if "." in text else int(text)
        return Expression(kind="literal", value=value)

    def string(self, token):  # noqa: ANN001
        raw = str(token)
        quote = raw[0]
        value = raw[1:-1].replace(f"\\{quote}", quote).replace("\\\\", "\\")
        return Expression(kind="literal", value=value)

    def true(self) -> Expression:
        return Expression(kind="literal", value=True, data_type=DataType.BOOLEAN)

    def false(self) -> Expression:
        return Expression(kind="literal", value=False, data_type=DataType.BOOLEAN)

    def null(self) -> Expression:
        return Expression(kind="literal", value=None)

    def field_ref(self, name) -> Expression:  # noqa: ANN001
        return Expression(kind="field_ref", name=str(name))

    def parameter_ref(self, name) -> Expression:  # noqa: ANN001
        return Expression(kind="parameter_ref", name=str(name))

    def not_expr(self, value: Expression) -> Expression:
        return Expression(kind="unary", op="not", children=(value,))

    def neg_expr(self, value: Expression) -> Expression:
        return Expression(kind="unary", op="neg", children=(value,))

    def comparison(self, *items: object) -> Expression:
        return _fold(items)

    def sum_expr(self, *items: object) -> Expression:
        return _fold(items)

    def product(self, *items: object) -> Expression:
        return _fold(items)

    def and_expr(self, *items: Expression) -> Expression:
        result = items[0]
        for item in items[1:]:
            result = _binary("AND", result, item)
        return result

    def or_expr(self, *items: Expression) -> Expression:
        result = items[0]
        for item in items[1:]:
            result = _binary("OR", result, item)
        return result

    def else_clause(self, value: Expression) -> tuple[str, Expression]:
        return ("else", value)

    def elseif_clause(
        self, condition: Expression, value: Expression
    ) -> tuple[str, Expression, Expression]:
        return ("elseif", condition, value)

    def if_expr(self, condition: Expression, value: Expression, *tail: object) -> Expression:
        fallback = Expression(kind="literal", value=None)
        branches: list[tuple[Expression, Expression]] = [(condition, value)]
        for item in tail:
            if isinstance(item, tuple) and item[0] == "elseif":
                branches.append((item[1], item[2]))
            elif isinstance(item, tuple) and item[0] == "else":
                fallback = item[1]
        for branch_condition, branch_value in reversed(branches):
            fallback = Expression(
                kind="conditional",
                children=(branch_condition, branch_value, fallback),
            )
        return fallback

    def case_clause(self, key: Expression, value: Expression) -> tuple[Expression, Expression]:
        return (key, value)

    def case_expr(self, selector: Expression, *items: object) -> Expression:
        fallback = Expression(kind="literal", value=None)
        branches: list[tuple[Expression, Expression]] = []
        for item in items:
            if isinstance(item, tuple) and item and item[0] == "else":
                fallback = item[1]
            else:
                branches.append(item)  # type: ignore[arg-type]
        for key, value in reversed(branches):
            fallback = Expression(
                kind="conditional",
                children=(_binary("=", selector, key), value, fallback),
            )
        return fallback

    def function_call(self, name, *arguments: Expression) -> Expression:  # noqa: ANN001
        function = str(name).lower()
        aggregate = {"countd": "count_distinct"}.get(function, function)
        if aggregate in {"sum", "avg", "count", "count_distinct", "min", "max", "median"}:
            return Expression(kind="agg", name=aggregate, children=tuple(arguments))
        if function.startswith("window_"):
            return Expression(
                kind="window", name=function.removeprefix("window_"), children=tuple(arguments)
            )
        if function == "lookup":
            return Expression(kind="window", name="lookup", children=tuple(arguments))
        if function == "size":
            return Expression(kind="window", name="size")
        mapped = {
            "ifnull": "coalesce",
            "len": "length",
            "regexp_extract_nth": "regexp_extract",
            "str": "to_string",
        }.get(function, function)
        return Expression(kind="func", name=mapped, children=tuple(arguments))

    def lod_expr(self, kind, *items: Expression) -> Expression:  # noqa: ANN001
        expressions = tuple(item for item in items if isinstance(item, Expression))
        if not expressions:
            raise ValueError("LOD requires a body")
        return Expression(
            kind="lod", name=str(kind).lower(), children=(expressions[-1], *expressions[:-1])
        )


def parse_tableau_formula(formula: str) -> Expression:
    """Parsea una fórmula completa o falla; nunca produce BLANK silencioso."""
    cleaned = _COMMENT_RE.sub("", formula).strip()
    if not cleaned:
        raise ValueError("Tableau formula is empty")
    return _ToExpression().transform(_PARSER.parse(cleaned))


__all__ = ["parse_tableau_formula"]
