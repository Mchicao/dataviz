"""Conversión auditable de medidas Tableau a funciones Python/pandas."""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PythonMeasureSpec:
    """Define una medida que debe conservar una contraparte Python."""

    name: str
    formula: str
    table: str


@dataclass(frozen=True)
class PythonMeasureDiagnostic:
    """Resultado explícito de convertir una fórmula Tableau a Python."""

    status: str
    original: str
    expression: str | None = None
    reason: str = ""


_AGGREGATIONS = {
    "SUM": "sum()",
    "AVG": "mean()",
    "COUNT": "count()",
    "COUNTD": "nunique(dropna=True)",
    "MIN": "min()",
    "MAX": "max()",
    "MEDIAN": "median()",
}


def _split_boolean(expression: str, operator: str) -> list[str]:
    """PY-MEASURE-17: Separa AND/OR sólo fuera de paréntesis y literales."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    index = 0
    marker = operator.upper()
    while index < len(expression):
        character = expression[index]
        if quote:
            if character == quote:
                quote = None
            index += 1
            continue
        if character in "'\"":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0 and expression[index : index + len(marker)].upper() == marker:
            before = expression[index - 1] if index else " "
            after_index = index + len(marker)
            after = expression[after_index] if after_index < len(expression) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                parts.append(expression[start:index].strip())
                start = after_index
                index = after_index
                continue
        index += 1
    if parts:
        parts.append(expression[start:].strip())
    return parts


def _strip_outer_parentheses(expression: str) -> str:
    """PY-MEASURE-18: Elimina paréntesis que envuelven toda la condición."""
    result = expression.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        closes_at_end = True
        for index, character in enumerate(result):
            depth += character == "("
            depth -= character == ")"
            if depth == 0 and index < len(result) - 1:
                closes_at_end = False
                break
        if not closes_at_end:
            break
        result = result[1:-1].strip()
    return result


def _tableau_operand(
    df: Any,
    expression: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """PY-MEASURE-19: Evalúa un operando escalar o una columna Tableau."""
    value = _strip_outer_parentheses(expression)
    parameter = re.fullmatch(r"\[Parameters\]\.\[([^\]]+)\]", value, re.I)
    if parameter:
        return (parameters or {}).get(parameter.group(1))
    string_column = re.fullmatch(r"STR\s*\(\s*\[([^\]]+)\]\s*\)", value, re.I)
    if string_column:
        return df.get(string_column.group(1)).astype("string")
    column = re.fullmatch(r"\[([^\]]+)\]", value)
    if column:
        return df.get(column.group(1))
    left = re.fullmatch(r"LEFT\s*\(\s*\[([^\]]+)\]\s*,\s*(\d+)\s*\)", value, re.I)
    if left:
        return df.get(left.group(1)).astype("string").str[: int(left.group(2))]
    regexp_extract = re.fullmatch(
        r"REGEXP_EXTRACT_NTH\s*\(\s*(.+?)\s*,\s*(['\"])(.*?)\2\s*,\s*(\d+)\s*\)",
        value,
        re.I | re.S,
    )
    if regexp_extract:
        # PY-MEASURE-29: Tableau numera los grupos de captura desde uno.
        raw_subject, _quote, pattern, raw_group = regexp_extract.groups()
        subject = _tableau_operand(df, raw_subject, parameters)
        match = re.search(pattern, str(subject))
        return match.group(int(raw_group)) if match is not None else None
    if value.upper() == "NULL":
        return None
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value


def _tableau_condition(
    df: Any,
    expression: str,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """PY-MEASURE-20: Evalúa condiciones Tableau con semántica vectorial pandas."""
    condition = _strip_outer_parentheses(re.sub(r"//[^\r\n]*", "", expression).strip())
    for operator, reducer in (("OR", "or"), ("AND", "and")):
        parts = _split_boolean(condition, operator)
        if parts:
            result = _tableau_condition(df, parts[0], parameters)
            for part in parts[1:]:
                other = _tableau_condition(df, part, parameters)
                result = (result | other) if reducer == "or" else (result & other)
            return result
    null_match = re.fullmatch(r"ISNULL\s*\(\s*\[([^\]]+)\]\s*\)", condition, re.I)
    if null_match:
        return df.get(null_match.group(1)).isna()
    contains = re.fullmatch(
        r"CONTAINS\s*\(\s*\[([^\]]+)\]\s*,\s*(['\"])(.*?)\2\s*\)",
        condition,
        re.I | re.S,
    )
    if contains:
        return (
            df.get(contains.group(1))
            .astype("string")
            .str.contains(contains.group(3), regex=False, na=False)
        )
    comparison = re.fullmatch(r"(.+?)\s*(<>|!=|<=|>=|=|<|>)\s*(.+)", condition, re.S)
    if comparison:
        left, operator, right = comparison.groups()
        lhs = _tableau_operand(df, left, parameters)
        rhs = _tableau_operand(df, right, parameters)
        operations = {
            "=": lambda: lhs == rhs,
            "!=": lambda: lhs != rhs,
            "<>": lambda: lhs != rhs,
            "<=": lambda: lhs <= rhs,
            ">=": lambda: lhs >= rhs,
            "<": lambda: lhs < rhs,
            ">": lambda: lhs > rhs,
        }
        res = operations[operator]()
        if hasattr(res, "fillna"):
            res = res.fillna(False)
        return res
    raise ValueError(f"Condición Tableau no soportada: {condition}")


def _tableau_if(
    df: Any,
    condition: str,
    when_true: Any,
    when_false: Any,
    parameters: dict[str, Any] | None = None,
) -> Any:
    """PY-MEASURE-21: Aplica IF Tableau tanto a escalares como a Series."""
    mask = _tableau_condition(df, condition, parameters)
    if hasattr(mask, "where"):
        true_value = when_true if hasattr(when_true, "where") else None
        if true_value is None:
            true_value = df.index.to_series().map(lambda _index: when_true)
        false_value = when_false if hasattr(when_false, "where") else when_false
        return true_value.where(mask.fillna(False), false_value)
    return when_true if mask else when_false


def _translate_tableau_value(expression: str) -> str:
    """PY-MEASURE-22: Convierte ramas IF en valores Python o Series pandas."""
    value = re.sub(r"//[^\r\n]*", "", expression).strip()
    nested = _translate_tableau_if(value)
    if nested is not None:
        return nested
    if value.upper() == "NULL" or not value:
        return "None"
    column = re.fullmatch(r"\[([^\]]+)\]", value)
    if column:
        return f"df.get({column.group(1)!r})"
    return value


def _translate_tableau_if(expression: str) -> str | None:
    """PY-MEASURE-23: Traduce IF/ELSEIF anidados a llamadas vectoriales."""
    source = re.sub(r"//[^\r\n]*", "", expression).strip()
    if not re.match(r"^IF\s*", source, re.I):
        return None
    tokens = list(re.finditer(r"\b(IF|THEN|ELSEIF|ELSE|END)\b", source, re.I))
    if not tokens or tokens[0].group(1).upper() != "IF":
        return None
    depth = 1
    then_token = None
    boundary = None
    closing = None
    for token in tokens[1:]:
        keyword = token.group(1).upper()
        if keyword == "IF":
            depth += 1
        elif keyword == "END":
            depth -= 1
            if depth == 0:
                closing = token
                break
        elif keyword == "THEN" and depth == 1 and then_token is None:
            then_token = token
        elif keyword in {"ELSEIF", "ELSE"} and depth == 1 and then_token is not None:
            boundary = token
            break
    if then_token is None:
        return None
    condition = source[tokens[0].end() : then_token.start()].strip()
    if boundary is None and closing is not None:
        when_true = source[then_token.end() : closing.start()].strip()
        return (
            f"_tableau_if(df, {condition!r}, {_translate_tableau_value(when_true)}, "
            "None, parameters)"
        )
    if boundary is None:
        return None
    when_true = source[then_token.end() : boundary.start()].strip()
    if boundary.group(1).upper() == "ELSEIF":
        when_false = _translate_tableau_if("IF " + source[boundary.end() :])
        if when_false is None:
            return None
    else:
        depth = 1
        boundary_index = tokens.index(boundary)
        for token in tokens[boundary_index + 1 :]:
            keyword = token.group(1).upper()
            depth += keyword == "IF"
            depth -= keyword == "END"
            if depth == 0:
                closing = token
                break
        if closing is None:
            return None
        when_false = _translate_tableau_value(source[boundary.end() : closing.start()])
    return (
        f"_tableau_if(df, {condition!r}, {_translate_tableau_value(when_true)}, "
        f"{when_false}, parameters)"
    )


def _translate_tableau_case(expression: str) -> str | None:
    """PY-MEASURE-30: Traduce CASE escalar conservando IF anidados por rama."""
    source = re.sub(r"//[^\r\n]*", "", expression).strip()
    header = re.match(r"^CASE\s+(.+?)\s+WHEN\s+", source, re.I | re.S)
    if header is None:
        return None
    selector = header.group(1).strip()
    cursor = header.end() - len(header.group(0).rsplit("WHEN", 1)[-1]) - 4
    branches: list[tuple[str, str]] = []
    fallback = "None"

    while re.match(r"WHEN\b", source[cursor:], re.I):
        when_match = re.match(r"WHEN\s+(.+?)\s+THEN\s+", source[cursor:], re.I | re.S)
        if when_match is None:
            return None
        branch_key = when_match.group(1).strip()
        value_start = cursor + when_match.end()
        depth = 0
        boundary: re.Match[str] | None = None
        for token in re.finditer(r"\b(IF|END|WHEN|ELSE)\b", source[value_start:], re.I):
            keyword = token.group(1).upper()
            if keyword == "IF":
                depth += 1
            elif keyword == "END":
                if depth:
                    depth -= 1
                else:
                    boundary = token
                    break
            elif keyword in {"WHEN", "ELSE"} and depth == 0:
                boundary = token
                break
        if boundary is None:
            return None
        value_end = value_start + boundary.start()
        branches.append((branch_key, source[value_start:value_end].strip()))
        cursor = value_start + boundary.start()
        if boundary.group(1).upper() == "ELSE":
            final_end = re.search(r"\bEND\s*$", source[cursor:], re.I)
            if final_end is None:
                return None
            fallback = _translate_tableau_value(
                source[cursor + boundary.end() : cursor + final_end.start()]
            )
            cursor += final_end.end()
            break
        if boundary.group(1).upper() == "END":
            cursor += boundary.end()
            break

    if not branches:
        return None
    translated = fallback
    for branch_key, branch_value in reversed(branches):
        condition = f"{selector} = {branch_key}"
        translated = (
            f"_tableau_if(df, {condition!r}, "
            f"{_translate_tableau_value(branch_value)}, {translated}, parameters)"
        )
    return translated


def convert_tableau_measure_to_python(formula: str) -> PythonMeasureDiagnostic:
    """Convierte agregaciones escalares Tableau a una expresión pandas ejecutable."""
    expression = formula.strip()

    # PY-MEASURE-31: contar separadores es la equivalencia exacta de esta
    # composición Tableau, sin ejecutar expresiones regulares arbitrarias.
    regexp_level = re.fullmatch(
        r"LEN\s*\(\s*REGEXP_REPLACE\s*\(\s*\[Parameters\]\.\[([^\]]+)\]\s*,\s*"
        r"(['\"])\[\^\\\|\]\2\s*,\s*(['\"])\3\s*\)\s*\)\s*\+\s*1",
        expression,
        re.I | re.S,
    )
    if regexp_level:
        expression = f"str(parameters.get({regexp_level.group(1)!r}, '')).count('|') + 1"

    # PY-MEASURE-25: offset cero no cambia la marca evaluada.
    lookup_zero = re.fullmatch(
        r"LOOKUP\s*\(\s*(.+)\s*,\s*0\s*\)",
        expression,
        re.IGNORECASE | re.DOTALL,
    )
    if lookup_zero:
        expression = lookup_zero.group(1).strip()

    has_aggregate = re.search(
        r"\b(?:SUM|AVG|MIN|MAX|COUNTD|COUNT|MEDIAN|WINDOW_MIN|WINDOW_MAX)\s*\(",
        expression,
        re.I,
    )
    row_level_if = None if has_aggregate else _translate_tableau_if(expression)
    translated_row_level = row_level_if is not None
    if row_level_if is not None:
        expression = row_level_if

    date_header_field = re.search(
        r"['\"]Datos hasta\s+['\"].*?MAX\s*\(\s*\[([^\]]+)\]\s*\)",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if date_header_field and all(
        re.search(rf"DATEPART\s*\(\s*['\"]{part}['\"]", expression, re.IGNORECASE)
        for part in ("day", "month", "year")
    ):
        expression = (
            f"'Datos hasta ' + df.get({date_header_field.group(1)!r}).max().strftime('%d-%m-%Y')"
        )

    case_index_match = re.fullmatch(
        r"CASE\s+INDEX\s*\(\s*\)(.*?)END",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if case_index_match:
        branches = re.findall(
            r"WHEN\s+(-?\d+)\s+THEN\s+(['\"])(.*?)\2",
            case_index_match.group(1),
            flags=re.IGNORECASE | re.DOTALL,
        )
        mapping = {int(index): label for index, _quote, label in branches}
        expression = (
            f"(df.reset_index(drop=True).index.to_series(index=df.index) + 1).map({mapping!r})"
        )

    if re.fullmatch(r"INDEX\s*\(\s*\)", expression, flags=re.IGNORECASE):
        expression = "df.reset_index(drop=True).index.to_series(index=df.index) + 1"

    lod_match = re.fullmatch(
        r"\{\s*(FIXED|INCLUDE|EXCLUDE)(?:\s+(.+?))?\s*:\s*"
        r"(SUM|AVG|MIN|MAX|COUNT|COUNTD)\s*\(\s*\[([^\]]+)\]\s*\)\s*\}",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if lod_match:
        lod_kind, raw_dimensions, aggregation, value_column = lod_match.groups()
        dimensions = re.findall(r"\[([^\]]+)\]", raw_dimensions or "")
        aggregation_method = {
            "AVG": "mean",
            "COUNTD": "nunique",
        }.get(aggregation.upper(), aggregation.lower())
        if lod_kind.upper() == "EXCLUDE" or (lod_kind.upper() == "FIXED" and not dimensions):
            scalar_call = _AGGREGATIONS[aggregation.upper()]
            expression = f"df.get({value_column!r}).{scalar_call}"
        elif dimensions:
            groupers = ", ".join(f"df.get({dimension!r})" for dimension in dimensions)
            expression = (
                f"df.get({value_column!r}).groupby([{groupers}], dropna=False)"
                f".transform({aggregation_method!r})"
            )

    parameter_match = re.fullmatch(
        r"PARAMETER\(\s*(['\"])(.*?)\1\s*,\s*(.*?)\s*\)",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if parameter_match:
        _quote, parameter_name, default_value = parameter_match.groups()
        expression = f"parameters.get({parameter_name!r}, {default_value})"

    # PY-MEASURE-26: comparación simple parámetro↔columna, incluida STR(columna).
    operand = r"(?:\[Parameters\]\.\[[^\]]+\]|STR\s*\(\s*\[[^\]]+\]\s*\)|\[[^\]]+\])"
    if "[Parameters]." in expression and re.fullmatch(
        rf"\s*{operand}\s*(?:<>|!=|<=|>=|=|<|>)\s*{operand}\s*",
        expression,
        re.IGNORECASE | re.DOTALL,
    ):
        expression = f"_tableau_condition(df, {expression!r}, parameters)"
        translated_row_level = True

    case_match = re.fullmatch(
        r"CASE\s+\[Parameters\]\.\[([^\]]+)\](.*?)END",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if case_match:
        parameter_name, case_body = case_match.groups()
        branches = re.findall(
            r"WHEN\s+([^\s]+)\s+THEN\s+\[([^\]]+)\]",
            case_body,
            flags=re.IGNORECASE,
        )
        if branches:
            expression = "None"
            for raw_value, column_name in reversed(branches):
                value = (
                    raw_value
                    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw_value)
                    else repr(raw_value.strip("'\""))
                )
                expression = (
                    f"df.get({column_name!r}) if parameters.get({parameter_name!r}) == "
                    f"{value} else ({expression})"
                )

    if re.match(r"^CASE\b", expression, re.I):
        translated_case = _translate_tableau_case(expression)
        if translated_case is not None:
            expression = translated_case
            translated_row_level = True

    # PY-MEASURE-08: dentro de la función por grupo, WINDOW_MIN/MAX de una
    # agregación escalar equivale a comparar con esa misma agregación.
    expression = re.sub(
        r"WINDOW_(?:MIN|MAX)\s*\(\s*((?:SUM|AVG|MIN|MAX|COUNTD|COUNT)\s*\(\s*\[[^\]]+\]\s*\))\s*\)",
        r"\1",
        expression,
        flags=re.IGNORECASE,
    )
    for function, pandas_call in _AGGREGATIONS.items():
        pattern = rf"\b{function}\s*\(\s*\[([^\]]+)\]\s*\)"
        expression = re.sub(
            pattern,
            lambda match, call=pandas_call: f"df.get({match.group(1)!r}).{call}",
            expression,
            flags=re.IGNORECASE,
        )

    expression = re.sub(
        r"\bAVG\s*\(\s*(-?\d+(?:\.\d+)?)\s*\)",
        lambda match: str(float(match.group(1))),
        expression,
        flags=re.IGNORECASE,
    )

    if_match = re.fullmatch(
        r"IF\s+(.*?)\s+THEN\s+(.*?)\s+ELSE\s+(.*?)\s+END",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if if_match:
        condition, when_true, when_false = (part.strip() for part in if_match.groups())
        condition = re.sub(r"(?<![<>=!])=(?!=)", "==", condition)
        when_true = re.sub(r"\bNULL\b", "None", when_true, flags=re.IGNORECASE)
        when_false = re.sub(r"\bNULL\b", "None", when_false, flags=re.IGNORECASE)
        expression = f"({when_true} if {condition} else {when_false})"

    # PY-MEASURE-04: los booleanos Tableau simples tienen equivalente directo.
    expression = re.sub(r"\bTRUE\b", "True", expression, flags=re.IGNORECASE)
    expression = re.sub(r"\bFALSE\b", "False", expression, flags=re.IGNORECASE)
    if not translated_row_level and re.search(r"\[(?!df\.get\()[^\]]+\]", expression):
        dependencies = sorted(set(re.findall(r"\[(Calculation_[^\]]+)\]", formula)))
        if dependencies:
            return PythonMeasureDiagnostic(
                status="unsupported",
                original=formula,
                reason=(
                    "Dependencia de cálculo no materializada en el DataFrame: "
                    + ", ".join(dependencies)
                ),
            )
        return PythonMeasureDiagnostic(
            status="unsupported",
            original=formula,
            reason="Quedaron referencias Tableau sin traducir",
        )

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        return PythonMeasureDiagnostic(
            status="unsupported",
            original=formula,
            reason=f"La expresión Python no compila: {error.msg}",
        )

    unknown_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in {"df", "parameters", "str", "_tableau_condition", "_tableau_if"}
    }
    if unknown_names:
        return PythonMeasureDiagnostic(
            status="unsupported",
            original=formula,
            reason="Funciones o identificadores no soportados: " + ", ".join(sorted(unknown_names)),
        )
    return PythonMeasureDiagnostic(
        status="supported",
        original=formula,
        expression=expression,
    )


def _python_identifier(value: str) -> str:
    """Normaliza un nombre de medida a identificador Python estable."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    identifier = re.sub(r"\W+", "_", normalized).strip("_").lower() or "measure"
    return f"measure_{identifier}" if identifier[0].isdigit() else identifier


def write_python_measure_module(
    destination: Path,
    specs: list[PythonMeasureSpec],
) -> list[dict[str, Any]]:
    """Escribe una función por medida y un manifiesto de cobertura verificable."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    names_seen: dict[str, int] = {}
    manifest: list[dict[str, Any]] = []
    parameter_defaults: dict[str, Any] = {}
    for spec in specs:
        match = re.fullmatch(
            r"PARAMETER\(\s*(['\"])(.*?)\1\s*,\s*(.*?)\s*\)",
            spec.formula,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            try:
                parameter_defaults[match.group(2)] = ast.literal_eval(match.group(3))
            except (SyntaxError, ValueError):
                continue
    source_lines = [
        '"""Medidas Python generadas desde Tableau; no editar manualmente."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "import pandas as pd",
        "from core.python_measure_converter import _tableau_condition, _tableau_if",
        "",
        f"DEFAULT_PARAMETERS = {parameter_defaults!r}",
        "",
    ]

    for index, spec in enumerate(specs, start=1):
        base_name = _python_identifier(spec.name)
        duplicate_index = names_seen.get(base_name, 0)
        names_seen[base_name] = duplicate_index + 1
        function_name = base_name if duplicate_index == 0 else f"{base_name}_{duplicate_index + 1}"
        diagnostic = convert_tableau_measure_to_python(spec.formula)
        manifest.append(
            {
                "name": spec.name,
                "table": spec.table,
                "formula": spec.formula,
                "function": function_name,
                "status": diagnostic.status,
                "reason": diagnostic.reason,
            }
        )
        source_lines.extend(
            [
                f"def {function_name}(df: pd.DataFrame, parameters: dict[str, Any] | None = None) -> Any:",
                f'    """PY-MEASURE-{index:03d}: Versión Python de {spec.name}."""',
                "    parameters = {**DEFAULT_PARAMETERS, **(parameters or {})}",
            ]
        )
        if diagnostic.expression is not None:
            source_lines.append(f"    return {diagnostic.expression}")
        else:
            message = diagnostic.reason or "Conversión Python no soportada"
            source_lines.append(f"    raise NotImplementedError({message!r})")
        source_lines.append("")

    destination.write_text("\n".join(source_lines), encoding="utf-8")
    manifest_path = destination.with_name("measures.manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
