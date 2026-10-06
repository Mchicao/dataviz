"""Compile a Power BI :class:`SourceAST` into the accepted neutral IR.

Walks a Power BI-origin Source AST tree (produced by :mod:`.importer`) and builds
the neutral :class:`core.contracts.semantic_ir.SemanticModel` -- the IR accepted
by the destination compilers. Mapping is structural and best-effort:

* Tables  -> :class:`Entity` (columns -> :class:`Field`)
* Measures -> :class:`Metric` (DAX parsed into :class:`Expression` where the
  grammar covers it; raw DAX preserved verbatim in ``description`` otherwise)
* Relationships -> :class:`Relationship`
* Expressions (parameters) -> :class:`Parameter`

Nothing is silently dropped: a measure whose DAX cannot be represented in the
neutral closed grammar keeps its raw expression as a :class:`Metric.description`
note, and an ``approximation`` is recorded in :attr:`SemanticModel.description`.

Design (Ponytail): stdlib + :mod:`core.contracts` only. No destination runtime
imports. The DAX mini-parser handles the common aggregation / arithmetic /
literal / reference shapes; everything else degrades safely.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import cast

from core.contracts.provenance import OriginKind
from core.contracts.semantic_ir import (
    MODEL_SCHEMA_VERSION,
    Cardinality,
    CrossFilterDirection,
    DataType,
    Entity,
    Expression,
    Field,
    Grain,
    Metric,
    Parameter,
    Relationship,
    SemanticModel,
)
from core.contracts.source_ast import NodeKind, SourceAST, SourceNode

# --- TMDL / TOM data type -> neutral DataType --------------------------------

_TYPE_MAP: dict[str, DataType] = {
    "string": DataType.STRING,
    "integer": DataType.INTEGER,
    "int64": DataType.INTEGER,
    "int32": DataType.INTEGER,
    "decimal": DataType.DECIMAL,
    "double": DataType.DECIMAL,
    "boolean": DataType.BOOLEAN,
    "datetime": DataType.DATETIME,
    "date": DataType.DATE,
    "time": DataType.TIME,
    "binary": DataType.BINARY,
    "variant": DataType.VARIANT,
    "unknown": DataType.UNKNOWN,
}

#: Aggregation DAX name -> neutral agg name.
_AGG_MAP: dict[str, str] = {
    "sum": "sum",
    "average": "avg",
    "avg": "avg",
    "min": "min",
    "max": "max",
    "count": "count",
    "distinctcount": "count_distinct",
    "countrows": "count",
}

#: TMDL camelCase cardinality -> neutral snake_case cardinality.
_CARDINALITY_MAP: dict[str, str] = {
    "onetoone": "one_to_one",
    "onetomany": "one_to_many",
    "manytoone": "many_to_one",
    "manytomany": "many_to_many",
}


def _data_type(token: str) -> DataType:
    """Map a TMDL/TOM type token to a neutral :class:`DataType`."""
    if not isinstance(token, str):
        return DataType.UNKNOWN
    mapped = _TYPE_MAP.get(token.strip().lower())
    return mapped if mapped is not None else DataType.UNKNOWN


def _normalize_cardinality(token: str) -> str:
    """Normalize a cardinality token to a neutral value (defaults many_to_one)."""
    return _CARDINALITY_MAP.get(token.lower() if isinstance(token, str) else "", "many_to_one")


def _cross_filter(token: str) -> str:
    """Normalize a cross-filter token to ``single`` or ``both``."""
    low = token.lower() if isinstance(token, str) else ""
    return "both" if "both" in low else "single"


# --- Minimal DAX -> Expression parser ----------------------------------------
#
# Tokenizes and parses a subset of DAX sufficient for the common measure
# shapes produced by Power BI Desktop: aggregations over a column, column /
# measure references, arithmetic, concatenation and literals. Anything outside
# this subset raises :class:`_DaxUnmapped` so the caller can preserve the raw
# DAX losslessly instead of fabricating an incorrect expression.

_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<str>"(?:[^"]|"")*")        |   # double-quoted string
        (?P<num>\d+\.\d+|\.\d+|\d+)     |   # number
        (?P<ref>'[^']*'\[[^\]]+\])      |   # 'Table'[Col]
        (?P<uref>[A-Za-z_][A-Za-z0-9_]*\[[^\]]+\])  |   # Table[Col] (unquoted)
        (?P<col>\[[^\]]+\])             |   # [Col] / [Measure]
        (?P<id>[A-Za-z_][A-Za-z0-9_]*)  |   # identifier / function / TRUE/FALSE
        (?P<op>[+\-*/&()])
    )
    """,
    re.VERBOSE,
)


class _DaxUnmapped(Exception):
    """Raised when the DAX mini-parser cannot map an expression."""


def _tokenize_dax(text: str) -> list[tuple[str, str]]:
    """Tokenize ``text`` into ``(kind, value)`` pairs."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise _DaxUnmapped(f"unrecognized token at {text[pos : pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind is not None:
            val = m.group(kind)
            tokens.append((kind, val))
    return tokens


def _dax_literal(tok: tuple[str, str]) -> Expression:
    kind, val = tok
    if kind == "str":
        # Unescape doubled double-quotes inside DAX strings.
        return Expression(
            kind="literal", value=val[1:-1].replace('""', '"'), data_type=DataType.STRING
        )
    if kind == "num":
        if "." in val:
            return Expression(kind="literal", value=float(val), data_type=DataType.DECIMAL)
        return Expression(kind="literal", value=int(val), data_type=DataType.INTEGER)
    if kind == "id":
        up = val.upper()
        if up == "TRUE":
            return Expression(kind="literal", value=True, data_type=DataType.BOOLEAN)
        if up == "FALSE":
            return Expression(kind="literal", value=False, data_type=DataType.BOOLEAN)
        if up in {"BLANK", "NULL"}:
            return Expression(kind="literal", value=None, data_type=DataType.UNKNOWN)
    raise _DaxUnmapped(f"not a literal: {tok}")


def _dax_reference(tok: tuple[str, str], known_measures: set[str]) -> Expression:
    kind, val = tok
    if kind in ("ref", "uref"):  # 'Table'[Col] or Table[Col]
        bracket = val.index("[")
        # Unqualified field ref (entity resolved later by a consumer) keeps the
        # neutral model valid regardless of whether the table maps cleanly.
        return Expression(kind="field_ref", name=val[bracket + 1 : -1], entity="")
    if kind == "col":  # [Col] or [Measure]
        name = val[1:-1]
        if name in known_measures:
            return Expression(kind="measure_ref", name=name)
        # Unqualified column reference; resolved later by a consumer.
        return Expression(kind="field_ref", name=name, entity="")
    raise _DaxUnmapped(f"not a reference: {tok}")


def _parse_dax(text: str, known_measures: set[str]) -> Expression:
    """Parse a DAX expression into a neutral :class:`Expression`.

    Raises :class:`_DaxUnmapped` when the expression is outside the supported
    subset; the caller then preserves the raw DAX verbatim.
    """
    tokens = _tokenize_dax(text)
    if not tokens:
        raise _DaxUnmapped("empty expression")
    parser = _DaxParser(tokens, known_measures)
    expr = parser.parse_expression()
    if parser.pos != len(tokens):
        raise _DaxUnmapped(f"trailing tokens: {tokens[parser.pos :]}")
    return expr


class _DaxParser:
    """Recursive-descent parser: primary -> mul/div -> add/sub/concat."""

    def __init__(self, tokens: list[tuple[str, str]], known_measures: set[str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.known_measures = known_measures

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def advance(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_expression(self) -> Expression:
        return self.parse_additive()

    def parse_additive(self) -> Expression:
        left = self.parse_multiplicative()
        while True:
            tok = self.peek()
            if tok is None or tok[0] != "op" or tok[1] not in "+-&":
                break
            self.advance()
            right = self.parse_multiplicative()
            op = {"+": "add", "-": "sub", "&": "concat"}[tok[1]]
            left = Expression(kind="binary", op=op, children=(left, right))
        return left

    def parse_multiplicative(self) -> Expression:
        left = self.parse_primary()
        while True:
            tok = self.peek()
            if tok is None or tok[0] != "op" or tok[1] not in "*/":
                break
            self.advance()
            right = self.parse_primary()
            left = Expression(
                kind="binary", op="mul" if tok[1] == "*" else "div", children=(left, right)
            )
        return left

    def parse_primary(self) -> Expression:
        tok = self.peek()
        if tok is None:
            raise _DaxUnmapped("unexpected end of expression")
        kind, val = tok

        # Parenthesized sub-expression.
        if kind == "op" and val == "(":
            self.advance()
            inner = self.parse_expression()
            close = self.peek()
            if close is None or close != ("op", ")"):
                raise _DaxUnmapped("missing closing paren")
            self.advance()
            return inner

        # Unary minus / negation.
        if kind == "op" and val == "-":
            self.advance()
            return Expression(kind="unary", op="neg", children=(self.parse_primary(),))

        # Function call: id '(' args ')'.
        if (
            kind == "id"
            and self.pos + 1 < len(self.tokens)
            and self.tokens[self.pos + 1] == ("op", "(")
        ):
            return self.parse_function()

        # String / number / boolean literal.
        if kind in ("str", "num") or (
            kind == "id" and val.upper() in {"TRUE", "FALSE", "BLANK", "NULL"}
        ):
            self.advance()
            return _dax_literal(tok)

        # Column / measure references.
        if kind in ("ref", "uref", "col"):
            self.advance()
            return _dax_reference(tok, self.known_measures)

        raise _DaxUnmapped(f"unexpected primary token: {tok}")

    def parse_function(self) -> Expression:
        name_tok = self.advance()  # function name (id)
        self.advance()  # '('
        fname = name_tok[1].lower()
        args = self._parse_args()
        upper = name_tok[1].upper()
        if upper in {"DIVIDE"} and len(args) == 2:
            return Expression(kind="binary", op="div", children=(args[0], args[1]))
        if fname in _AGG_MAP:
            # COUNTROWS takes no column arg -> approximate to count(literal).
            if upper == "COUNTROWS":
                agg_name = _AGG_MAP["countrows"]
                return Expression(
                    kind="agg",
                    name=agg_name,
                    children=(args[0] if args else Expression(kind="literal", value=None),),
                )
            agg_name = _AGG_MAP[fname]
            if len(args) == 1:
                return Expression(kind="agg", name=agg_name, children=(args[0],))
            raise _DaxUnmapped(f"{upper} expects one argument, got {len(args)}")
        raise _DaxUnmapped(f"unsupported function: {name_tok[1]}")

    def _parse_args(self) -> list[Expression]:
        args: list[Expression] = []
        if self.peek() == ("op", ")"):
            self.advance()
            return args
        args.append(self.parse_expression())
        while self.peek() == ("op", ","):
            self.advance()
            args.append(self.parse_expression())
        close = self.peek()
        if close != ("op", ")"):
            raise _DaxUnmapped("missing closing paren in argument list")
        self.advance()
        return args


# --- Source AST -> SemanticModel --------------------------------------------


def _walk(node: SourceNode):
    """Yield ``node`` and all descendants (depth-first, pre-order)."""
    yield node
    for child in node.children:
        yield from _walk(child)


_COLUMN_REF_RE = re.compile(
    r"^\s*(?:'((?:''|[^'])+)'|([A-Za-z_][A-Za-z0-9_ ]*))\s*\[([^\]]+)\]\s*$"
)
_FUNCTION_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_.]*)\s*\((.*)\)\s*$", re.DOTALL)
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def _column_reference(text: str) -> tuple[str, str] | None:
    """Parse a qualified DAX column reference without guessing ownership."""
    match = _COLUMN_REF_RE.match(text or "")
    if match is None:
        return None
    table = (match.group(1) or match.group(2) or "").replace("''", "'")
    column = match.group(3).replace("]]", "]")
    return table, column


def _split_dax_arguments(text: str) -> list[str]:
    """Split one DAX argument list at top-level commas.

    The scanner understands parentheses, braces, brackets and both DAX string
    and quoted-identifier escaping. It intentionally does not evaluate DAX.
    """
    parts: list[str] = []
    start = 0
    paren = brace = bracket = 0
    in_string = False
    in_identifier = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                in_string = False
            index += 1
            continue
        if in_identifier:
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_identifier = False
            index += 1
            continue
        if char == '"':
            in_string = True
        elif char == "'":
            in_identifier = True
        elif char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "," and paren == 0 and brace == 0 and bracket == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _dax_function(text: str) -> tuple[str, list[str]] | None:
    match = _FUNCTION_RE.match(text or "")
    if match is None:
        return None
    return match.group(1).upper(), _split_dax_arguments(match.group(2))


def _dax_string_literal(text: str) -> str | None:
    value = text.strip()
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return None
    return value[1:-1].replace('""', '"')


def _table_constructor_value_type(text: str) -> DataType | None:
    value = text.strip()
    if not (value.startswith("{") and value.endswith("}")):
        return None
    items = _split_dax_arguments(value[1:-1])
    inferred = {_literal_type(item) for item in items}
    inferred.discard(None)
    return next(iter(inferred)) if len(inferred) == 1 else None


def _literal_type(text: str) -> DataType | None:
    token = text.strip()
    if _dax_string_literal(token) is not None:
        return DataType.STRING
    if token.upper() in {"TRUE", "FALSE", "TRUE()", "FALSE()"}:
        return DataType.BOOLEAN
    if _NUMBER_RE.fullmatch(token):
        return DataType.DECIMAL if "." in token else DataType.INTEGER
    return None


def _lookup_field_type(
    known_types: dict[tuple[str, str], DataType], table: str, column: str
) -> DataType | None:
    exact = known_types.get((table, column))
    if exact is not None:
        return exact
    folded = [
        data_type
        for (entity_name, field_name), data_type in known_types.items()
        if entity_name.casefold() == table.casefold() and field_name.casefold() == column.casefold()
    ]
    return folded[0] if len(folded) == 1 else None


def _infer_dax_value_type(
    expression: str,
    known_types: dict[tuple[str, str], DataType],
    *,
    table_source: str = "",
) -> DataType | None:
    """Infer a scalar type from a deliberately small, lossless DAX subset."""
    text = expression.strip()
    direct = _column_reference(text)
    if direct is not None:
        return _lookup_field_type(known_types, *direct)
    literal = _literal_type(text)
    if literal is not None:
        return literal
    if text.casefold() == "[value]" and table_source:
        return _table_constructor_value_type(table_source)
    call = _dax_function(text)
    if call is None:
        return None
    name, args = call
    if name in {"COUNT", "COUNTA", "COUNTAX", "COUNTROWS", "DISTINCTCOUNT", "RANKX"}:
        return DataType.INTEGER
    if name in {"CALCULATE", "CALCULATETABLE"} and args:
        return _infer_dax_value_type(args[0], known_types, table_source=table_source)
    if name in {"SUM", "AVERAGE", "AVERAGEX", "MIN", "MAX", "MINX", "MAXX", "VALUES", "SELECTEDVALUE"} and args:
        # Iterator variants carry their scalar expression as the last argument.
        target = args[-1] if name.endswith("X") and len(args) > 1 else args[0]
        return _infer_dax_value_type(target, known_types, table_source=table_source)
    if name in {"DIVIDE", "PERCENTILEX.INC", "PERCENTILEX.EXC"}:
        return DataType.DECIMAL
    if name in {"DATE", "DATEVALUE", "EDATE", "EOMONTH"}:
        return DataType.DATE
    if name in {"YEAR", "MONTH", "DAY", "WEEKDAY", "WEEKNUM"}:
        return DataType.INTEGER
    return None


def _calculated_table_aliases(expression: str) -> tuple[dict[str, str], str]:
    """Return explicit output aliases and the calculated-table base expression."""
    call = _dax_function(expression)
    if call is None:
        return {}, ""
    name, args = call
    if name not in {"SELECTCOLUMNS", "ADDCOLUMNS", "SUMMARIZE"} or not args:
        return {}, args[0] if args else ""
    aliases: dict[str, str] = {}
    # SELECTCOLUMNS/ADDCOLUMNS always use name/expression pairs after the base.
    if name in {"SELECTCOLUMNS", "ADDCOLUMNS"}:
        index = 1
        while index + 1 < len(args):
            alias = _dax_string_literal(args[index])
            if alias is not None:
                aliases[alias] = args[index + 1]
                index += 2
            else:
                index += 1
        return aliases, args[0]
    # SUMMARIZE can contain grouping columns before extension name/expression pairs.
    index = 1
    while index + 1 < len(args):
        alias = _dax_string_literal(args[index])
        if alias is not None:
            aliases[alias] = args[index + 1]
            index += 2
        else:
            index += 1
    return aliases, args[0]


class PowerBIAstCompiler:
    """Compile a Power BI :class:`SourceAST` into a neutral :class:`SemanticModel`.

    The compiler is best-effort: structural elements (tables, columns,
    relationships, parameters) are mapped fully; measure DAX is parsed into the
    neutral :class:`Expression` grammar when it fits the supported subset, and
    otherwise preserved verbatim in the metric description. Every degradation is
    recorded in the returned ``notes`` list (:meth:`compile_with_notes`).
    """

    def __init__(self) -> None:
        self._notes: list[str] = []

    def compile(self, ast: SourceAST) -> SemanticModel:
        """Compile ``ast`` into a :class:`SemanticModel` (best-effort)."""
        return self.compile_with_notes(ast)[0]

    def compile_with_notes(self, ast: SourceAST) -> tuple[SemanticModel, list[str]]:
        """Compile and return ``(model, notes)`` with recorded approximations."""
        self._notes = []
        nodes = list(_walk(ast.root))

        # First pass: collect entity names + measure names for ref resolution.
        entity_names = {
            n.name
            for n in nodes
            if n.kind is NodeKind.DATASOURCE
            and n.origin_tag == "table"
            and not n.attributes.get("neutral_parameter_table")
        }
        measure_names = {
            n.name
            for n in nodes
            if n.kind is NodeKind.CALCULATION
            and n.origin_tag == "measure"
            and not n.attributes.get("neutral_parameter_measure")
        }

        # Build entities (one per table node), collecting fields + measures.
        entities: list[Entity] = []
        per_table_metrics: dict[str, list[Metric]] = {}
        standalone_metrics: list[Metric] = []
        for node in nodes:
            if (
                node.kind is NodeKind.DATASOURCE
                and node.origin_tag == "table"
                and not node.attributes.get("neutral_parameter_table")
            ):
                entity, metrics = self._build_entity(node, measure_names)
                entities.append(entity)
                per_table_metrics[entity.name] = metrics

        entities = self._infer_calculated_table_field_types(entities, nodes)

        # Measures attached to the report-level / model-level (PBIT) also exist;
        # collect any CALCULATION measure nodes not already under a table.
        table_metric_names = {metric.name for metrics in per_table_metrics.values() for metric in metrics}
        for node in nodes:
            if (
                node.kind is NodeKind.CALCULATION
                and node.origin_tag == "measure"
                and not node.attributes.get("neutral_parameter_measure")
            ):
                if node.name not in table_metric_names:
                    metric = self._build_metric(node, measure_names)
                    if metric is not None:
                        standalone_metrics.append(metric)

        metrics: list[Metric] = []
        seen: set[str] = set()
        for metric in (m for ms in per_table_metrics.values() for m in ms):
            if metric.name not in seen:
                metrics.append(metric)
                seen.add(metric.name)
        # Deduplicate by name (table measures take precedence).
        for m in standalone_metrics:
            if m.name not in seen:
                metrics.append(m)
                seen.add(m.name)

        # Relationships (only those whose endpoints resolve to known entities).
        relationships = [
            rel
            for rel in (
                self._build_relationship(n, entity_names)
                for n in nodes
                if n.kind is NodeKind.RELATIONSHIP and n.origin_tag == "relationship"
            )
            if rel is not None
        ]

        # Parameters (model-level expressions with kind=parameter, or PBIT expr).
        parameters = [
            p
            for p in (self._build_parameter(n) for n in nodes if n.kind is NodeKind.PARAMETER)
            if p is not None
        ]

        model_name = ast.root.name or "powerbi_model"
        description = self._summarize_notes()
        model = SemanticModel(
            schema_version=MODEL_SCHEMA_VERSION,
            name=model_name,
            entities=entities,
            relationships=relationships,
            metrics=metrics,
            parameters=parameters,
            description=description,
        )
        return model, self._notes

    # -- element builders ------------------------------------------------

    def _infer_calculated_table_field_types(
        self, entities: list[Entity], nodes: list[SourceNode]
    ) -> list[Entity]:
        """Resolve inferred calculated-table column types from source evidence.

        Folder TMDL legitimately omits ``dataType`` for inferred columns of a
        calculated table. The authoritative type is recoverable from the DAX
        table expression for common Desktop-generated shapes. Unknowns stay
        unknown when the evidence is ambiguous.
        """
        known_types: dict[tuple[str, str], DataType] = {
            (entity.name, field.name): field.data_type
            for entity in entities
            for field in entity.fields
            if field.data_type not in {DataType.UNKNOWN, DataType.VARIANT}
        }
        table_nodes = {
            node.name: node
            for node in nodes
            if node.kind is NodeKind.DATASOURCE and node.origin_tag == "table"
        }
        current = entities
        # A calculated table may reference another calculated table, so allow a
        # bounded fixed-point pass while keeping every promotion evidence-based.
        for _ in range(max(1, len(current))):
            changed = False
            rebuilt: list[Entity] = []
            for entity in current:
                node = table_nodes.get(entity.name)
                partition_expression = ""
                if node is not None:
                    for child in node.children:
                        if (
                            child.origin_tag == "partition"
                            and str(child.attributes.get("source_kind", "")).casefold() == "calculated"
                        ):
                            partition_expression = str(
                                child.attributes.get("source_expression", "") or ""
                            )
                            if partition_expression:
                                break
                aliases, table_source = _calculated_table_aliases(partition_expression)
                fields: list[Field] = []
                for field in entity.fields:
                    if field.data_type not in {DataType.UNKNOWN, DataType.VARIANT}:
                        fields.append(field)
                        continue
                    inferred: DataType | None = None
                    provenance = ""
                    direct = _column_reference(field.source_column)
                    if direct is not None:
                        inferred = _lookup_field_type(known_types, *direct)
                        provenance = f"sourceColumn {field.source_column!r}"
                    if inferred is None and field.name in aliases:
                        inferred = _infer_dax_value_type(
                            aliases[field.name], known_types, table_source=table_source
                        )
                        provenance = f"calculated-table alias {aliases[field.name]!r}"
                    if inferred is None:
                        fields.append(field)
                        continue
                    promoted = replace(field, data_type=inferred)
                    fields.append(promoted)
                    known_types[(entity.name, field.name)] = inferred
                    self._notes.append(
                        f"calculated table {entity.name!r} field {field.name!r}: "
                        f"inferred {inferred.value} from {provenance}"
                    )
                    changed = True
                rebuilt.append(replace(entity, fields=fields))
            current = rebuilt
            if not changed:
                break
        return current

    def _build_entity(
        self, node: SourceNode, measure_names: set[str]
    ) -> tuple[Entity, list[Metric]]:
        """Build an :class:`Entity` from a table node and its child fields/measures."""
        fields: list[Field] = []
        key_fields: list[str] = []
        metrics: list[Metric] = []
        seen_cols: set[str] = set()
        for child in node.children:
            if child.kind in (NodeKind.FIELD, NodeKind.CALCULATION) and child.origin_tag in (
                "column",
                "calculated_column",
            ):
                if child.name in seen_cols:
                    continue
                seen_cols.add(child.name)
                field = self._build_field(child)
                fields.append(field)
                if field.is_key:
                    key_fields.append(field.name)
                summarize = str(child.attributes.get("summarize_by", "")).lower()
                if summarize not in {"", "none", "donotsummarize"}:
                    aggregation = _AGG_MAP.get(summarize)
                    if aggregation is not None and field.name not in measure_names:
                        metrics.append(
                            Metric(
                                name=field.name,
                                entity=node.name,
                                expression=Expression(
                                    kind="agg",
                                    name=aggregation,
                                    children=(
                                        Expression(
                                            kind="field_ref",
                                            entity=node.name,
                                            name=field.name,
                                        ),
                                    ),
                                ),
                                data_type=field.data_type,
                            )
                        )
            elif child.kind is NodeKind.CALCULATION and child.origin_tag == "measure":
                if child.attributes.get("neutral_parameter_measure"):
                    continue
                metric = self._build_metric(child, measure_names)
                if metric is not None:
                    metrics.append(metric)
        grain = Grain(fields=key_fields) if key_fields else None
        entity = Entity(
            name=node.name,
            fields=fields,
            grain=grain,
            description=node.attributes.get("description", ""),
        )
        return entity, metrics

    def _build_field(self, node: SourceNode) -> Field:
        attrs = node.attributes
        return Field(
            name=node.name,
            data_type=_data_type(attrs.get("tmdl_data_type", "")),
            is_key=bool(attrs.get("is_key", False)),
            nullable=True,
            hidden=bool(attrs.get("is_hidden", False)),
            source_column=attrs.get("source_column", node.name),
        )

    def _build_metric(self, node: SourceNode, measure_names: set[str]) -> Metric | None:
        raw = node.attributes.get("expression", "")
        data_type = _data_type(node.attributes.get("tmdl_data_type", ""))
        fmt = node.attributes.get("format_string", "")
        description = node.attributes.get("description", "")
        try:
            expr = (
                _parse_dax(str(raw), measure_names)
                if raw
                else Expression(kind="literal", value=None, data_type=DataType.UNKNOWN)
            )
        except _DaxUnmapped:
            # Preserve the source DAX as the single canonical expression authority.
            # This is intentionally a narrow, allowlisted escape hatch rather than
            # hiding executable semantics in Metric.description.
            self._notes.append(
                f"measure {node.name!r}: DAX not representable in neutral grammar; "
                "preserved as opaque DAX"
            )
            expr = Expression(
                kind="opaque",
                name="dax",
                value=str(raw),
                data_type=data_type if data_type is not DataType.UNKNOWN else DataType.VARIANT,
            )
        return Metric(
            name=node.name,
            expression=expr,
            data_type=data_type if data_type is not DataType.UNKNOWN else DataType.DECIMAL,
            format_string=fmt,
            description=description,
            hidden=bool(node.attributes.get("is_hidden", False)),
        )

    def _build_relationship(self, node: SourceNode, entity_names: set[str]) -> Relationship | None:
        attrs = node.attributes
        from_table = attrs.get("from_table", "")
        to_table = attrs.get("to_table", "")
        from_cols = attrs.get("from_columns", [])
        to_cols = attrs.get("to_columns", [])
        if isinstance(from_cols, str):
            from_cols = [from_cols]
        if isinstance(to_cols, str):
            to_cols = [to_cols]
        # Relationship endpoints must resolve to known entities; skip otherwise
        # so the neutral model stays referentially valid.
        if (
            not from_table
            or not to_table
            or not from_cols
            or not to_cols
            or from_table not in entity_names
            or to_table not in entity_names
        ):
            self._notes.append(
                f"relationship {node.name!r}: endpoints not in known entities, skipped"
            )
            return None
        return Relationship(
            name=node.name,
            from_entity=from_table,
            from_fields=list(from_cols),
            to_entity=to_table,
            to_fields=list(to_cols),
            cardinality=cast(Cardinality, _normalize_cardinality(attrs.get("cardinality", ""))),
            cross_filter=cast(
                CrossFilterDirection, _cross_filter(attrs.get("cross_filter", "single"))
            ),
            active=bool(attrs.get("is_active", True)),
        )

    def _build_parameter(self, node: SourceNode) -> Parameter | None:
        raw = node.attributes.get("expression", "")
        kind_token = str(node.attributes.get("kind", "")).lower()
        # Heuristic: only "parameter"-kind expressions become neutral parameters;
        # other model-level expressions (M queries) are noted and skipped to keep
        # the neutral model valid.
        if kind_token and kind_token != "parameter":
            self._notes.append(
                f"expression {node.name!r}: kind={kind_token!r} not a parameter; skipped"
            )
            return None
        if node.origin_tag == "neutral_parameter":
            data_type = _data_type(str(node.attributes.get("data_type", "")))
            return Parameter(
                name=node.name,
                data_type=data_type,
                is_set=bool(node.attributes.get("is_set", False)),
                default_value=node.attributes.get("default_value"),
                allowed_values=list(node.attributes.get("allowed_values", [])),
                description=str(node.attributes.get("description", "")),
            )
        description = f"[raw expression] {raw}" if raw else ""
        return Parameter(
            name=node.name,
            data_type=DataType.UNKNOWN,
            default_value=None,
            description=description,
        )

    def _summarize_notes(self) -> str:
        if not self._notes:
            return "Compiled from Power BI Source AST (best-effort)."
        head = "Compiled from Power BI Source AST (best-effort). Approximations:"
        return head + "\n - " + "\n - ".join(self._notes)


def compile_powerbi_ast(ast: SourceAST) -> SemanticModel:
    """Compile a Power BI :class:`SourceAST` into the accepted neutral IR.

    Args:
        ast: A :class:`SourceAST` with ``origin_kind == power_bi`` (typically
            produced by :func:`import_powerbi`).

    Returns:
        A validated :class:`SemanticModel` with provenance-free structural
        mapping of tables, columns, measures, relationships and parameters.
    """
    if ast.origin_kind is not OriginKind.POWER_BI:
        raise ValueError(
            f"compile_powerbi_ast expects a power_bi SourceAST, got {ast.origin_kind!r}"
        )
    return PowerBIAstCompiler().compile(ast)


__all__ = ["PowerBIAstCompiler", "compile_powerbi_ast"]
