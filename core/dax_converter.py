"""
DAX Converter - Sugiere conversiones de fórmulas Tableau a DAX.

Este módulo analiza fórmulas de Tableau y genera sugerencias de
cómo implementarlas en Power BI DAX/M.
"""

import re
from dataclasses import dataclass, field


@dataclass
class ConversionDiagnostic:
    """Diagnóstico estructurado de conversión Tableau → DAX/PBIR.

    - status: "supported" (equivalencia comprobable), "manual" (degradación
      conocida, requiere intervención), "unsupported" (sin equivalencia).
    - category: tipo de elemento traducido.
    - dax: expresión DAX si status="supported", None en caso contrario.
    """

    status: str
    category: str
    original: str
    dax: str | None = None
    reason: str = ""
    manual_steps: list[str] = field(default_factory=list)


# Mapeo de funciones Tableau → DAX
FUNCTION_MAP = {
    # Agregaciones
    "SUM": "SUM",
    "AVG": "AVERAGE",
    "COUNT": "COUNT",
    "COUNTD": "DISTINCTCOUNT",
    "MIN": "MIN",
    "MAX": "MAX",
    "MEDIAN": "MEDIAN",
    "STDEV": "STDEV.P",
    "VAR": "VAR.P",
    # Condicionales
    "IF": "IF",
    "IIF": "IF",  # IIF(cond, true, false) → IF(cond, true, false)
    "CASE": "SWITCH",  # Similar pero diferente sintaxis
    "IFNULL": "IF(ISBLANK({expr}), {default}, {expr})",
    "ZN": "IF(ISBLANK({expr}), 0, {expr})",
    "ISNULL": "ISBLANK",
    # Texto
    "LEN": "LEN",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
    "MID": "MID",
    "UPPER": "UPPER",
    "LOWER": "LOWER",
    "TRIM": "TRIM",
    "LTRIM": "TRIM",
    "RTRIM": "TRIM",
    "CONTAINS": "CONTAINSSTRING",
    "STARTSWITH": "STARTSWITH",  # Mismo nombre pero diferente sintaxis
    "ENDSWITH": "ENDSWITH",
    "FIND": "SEARCH",
    "REPLACE": "SUBSTITUTE",
    "SPLIT": "---MANUAL---",  # No hay equivalente directo
    # Fecha
    "TODAY": "TODAY",
    "NOW": "NOW",
    "YEAR": "YEAR",
    "MONTH": "MONTH",
    "DAY": "DAY",
    "DATEADD": "DATEADD",  # Similar pero diferente sintaxis
    "DATEDIFF": "DATEDIFF",
    "DATETRUNC": "---MANUAL---",  # Requiere lógica custom
    "DATEPART": "---MANUAL---",  # Usar YEAR/MONTH/DAY según el caso
    "MAKETIME": "TIME",
    "MAKEDATE": "DATE",
    # Matemáticas
    "ABS": "ABS",
    "ROUND": "ROUND",
    "CEILING": "CEILING",
    "FLOOR": "FLOOR",
    "POWER": "POWER",
    "SQRT": "SQRT",
    "LOG": "LOG",
    "EXP": "EXP",
    "MOD": "MOD",
    "SIGN": "SIGN",
    # Lookup/LOD - Requieren manejo especial
    "FIXED": "=== LOD FIXED ===",
    "INCLUDE": "=== LOD INCLUDE ===",
    "EXCLUDE": "=== LOD EXCLUDE ===",
    "LOOKUP": "=== TABLE CALC ===",
    "RUNNING_SUM": "=== TABLE CALC ===",
    "RUNNING_AVG": "=== TABLE CALC ===",
    "WINDOW_SUM": "=== TABLE CALC ===",
    "WINDOW_AVG": "=== TABLE CALC ===",
    "INDEX": "=== TABLE CALC ===",
    "FIRST": "=== TABLE CALC ===",
    "LAST": "=== TABLE CALC ===",
}


# Guías de conversión para casos complejos
CONVERSION_GUIDES = {
    "LOD FIXED": """
**LOD FIXED → DAX CALCULATE con ALL**

Tableau:
```
{FIXED [Customer] : SUM([Sales])}
```

DAX:
```dax
Customer Sales =
CALCULATE(
    SUM(Table[Sales]),
    ALLEXCEPT(Table, Table[Customer])
)
```
""",
    "LOD INCLUDE": """
**LOD INCLUDE → DAX con contexto adicional**

Tableau:
```
{INCLUDE [Region] : AVG([Profit])}
```

DAX (en medida):
```dax
Avg Profit by Region =
AVERAGEX(
    VALUES(Table[Region]),
    CALCULATE(SUM(Table[Profit]))
)
```
""",
    "TABLE CALC": """
**Table Calculations → DAX con funciones EARLIER/RANKX**

Running Sum:
```dax
Running Sales =
CALCULATE(
    SUM(Table[Sales]),
    FILTER(
        ALL(Table[Date]),
        Table[Date] <= MAX(Table[Date])
    )
)
```

Index/Row Number:
```dax
Row Number =
RANKX(ALL(Table), Table[ID])
```
""",
}


# DATEDIFF: Tableau pone el intervalo primero como string, DAX lo pone último como enum.
_DATEDIFF_INTERVALS = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
}

# Funciones sin equivalencia DAX comprobable (inventory: No Soportado / no listadas).
# Producirían referencias DAX inválidas si se dejan pasar.
_UNSUPPORTED_FUNCTIONS = ("DATETRUNC", "STDEV", "VAR", "FIND")

_DATEPART_FUNCTIONS = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "week": "WEEKNUM",
    "weekday": "WEEKDAY",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
}

_TABLEAU_RESIDUAL_PATTERNS = (
    r"\bELSEIF\b",
    r"\bTHEN\b",
    r"\bEND\b",
    r"\b(CASE|WHEN)\b",
    r"\b(IIF|STR|DATEPART|DATETRUNC|IFNULL|ZN)\s*\(",
    r"\b(FIXED|INCLUDE|EXCLUDE)\b",
    # DAX-RES-05: FIRST()/LAST() son exactos; Table.FirstN es Power Query M.
    r"(?<!\.)\b(?:RUNNING_\w*|WINDOW_\w*|LOOKUP|INDEX|FIRST|LAST)\s*\(",
)


def contains_tableau_residual_syntax(expression: str) -> bool:
    """Detecta tokens Tableau que no son válidos como expresión DAX.

    Args:
        expression: Expresión supuestamente convertida a DAX.

    Returns:
        ``True`` cuando queda sintaxis Tableau fuera de literales de texto.
    """
    # DAX-RES-01: ignorar palabras contenidas dentro de literales DAX.
    without_strings = re.sub(r'"(?:[^"]|"")*"', '""', expression)
    without_identifiers = re.sub(r"\[[^\]]+\]", "[]", without_strings)
    return any(
        re.search(pattern, without_identifiers, re.IGNORECASE)
        for pattern in _TABLEAU_RESIDUAL_PATTERNS
    )


def _convert_tableau_if_chain(expression: str) -> str | None:
    """Convierte un IF/ELSEIF Tableau plano a SWITCH(TRUE())."""
    if not re.match(r"^IF\b", expression, re.IGNORECASE):
        return None
    if not re.search(r"\bEND\s*$", expression, re.IGNORECASE):
        return None

    clauses = re.findall(
        r"(?:^IF|\bELSEIF)\s+(.+?)\s+THEN\s+(.+?)"
        r"(?=\s+ELSEIF\b|\s+ELSE\b|\s+END\s*$)",
        expression,
        re.IGNORECASE | re.DOTALL,
    )
    if not clauses:
        return None

    else_match = re.search(
        r"\bELSE\s+(.+?)\s+END\s*$",
        expression,
        re.IGNORECASE | re.DOTALL,
    )
    else_expression = else_match.group(1).strip() if else_match else "BLANK()"
    switch_lines = ["SWITCH(", "    TRUE(),"]
    for condition, result in clauses:
        switch_lines.append(f"    {condition.strip()}, {result.strip()},")
    switch_lines.append(f"    {else_expression}")
    switch_lines.append(")")
    return "\n".join(switch_lines)


def _convert_simple_lod(formula: str, safe_table: str) -> str | None:
    """Convierte LOD simples; los casos ambiguos no generan DAX inválido."""
    match = re.match(
        r"^\{\s*(FIXED|INCLUDE|EXCLUDE)\s*(.*?)\s*:\s*"
        r"(SUM|AVG|MIN|MAX|COUNT|COUNTD|MEDIAN)\s*\(\s*\[([^\]]+)\]\s*\)\s*\}$",
        formula,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return None

    lod_kind, dimensions_text, aggregate, value_field = match.groups()
    dimensions = re.findall(r"\[([^\]]+)\]", dimensions_text)
    dax_aggregate = {"AVG": "AVERAGE", "COUNTD": "DISTINCTCOUNT"}.get(
        aggregate.upper(), aggregate.upper()
    )
    aggregate_expr = f"{dax_aggregate}('{safe_table}'[{value_field}])"
    dimension_refs = [f"'{safe_table}'[{dimension}]" for dimension in dimensions]

    if lod_kind.upper() == "FIXED":
        if not dimension_refs:
            return f"CALCULATE({aggregate_expr}, ALL('{safe_table}'))"
        return (
            f"CALCULATE({aggregate_expr}, ALLEXCEPT('{safe_table}', {', '.join(dimension_refs)}))"
        )
    if lod_kind.upper() == "EXCLUDE":
        if not dimension_refs:
            return aggregate_expr
        return f"CALCULATE({aggregate_expr}, REMOVEFILTERS({', '.join(dimension_refs)}))"
    if len(dimension_refs) == 1:
        return f"SUMX(VALUES({dimension_refs[0]}), CALCULATE({aggregate_expr}))"
    return None


def convert_tableau_to_dax(
    tableau_formula: str,
    table_name: str = "Table",
    known_measures: set[str] | None = None,
    known_columns: set[str] | None = None,
) -> str | None:
    """
    Traduce una formula de Tableau a DAX funcional, usando el nombre real
    de la tabla del modelo semantico.
    """
    known_measures = known_measures or set()
    known_columns = known_columns or set()
    safe_table = table_name.replace("'", "''")

    formula = tableau_formula.strip()

    # DAX-TABLE-01: LOOKUP(expr, 0) devuelve la expresión de la marca actual.
    lookup_zero = re.fullmatch(
        r"LOOKUP\s*\(\s*(.+)\s*,\s*0\s*\)",
        formula,
        re.IGNORECASE | re.DOTALL,
    )
    if lookup_zero:
        formula = lookup_zero.group(1).strip()

    if formula.startswith("{") and formula.endswith("}"):
        return _convert_simple_lod(formula, safe_table)

    if any(
        keyword in formula.upper()
        for keyword in ("RUNNING_", "INDEX()", "FIRST()", "LAST()", "LOOKUP(")
    ):
        return None
    # WINDOW_SUM/WINDOW_AVG no tienen equivalente DAX; WINDOW_MAX/WINDOW_MIN
    # se degradan a MAXX/MINX (paso 4) y no deben bloquearse aquí.
    if re.search(r"\bWINDOW_(SUM|AVG)\s*\(", formula, re.IGNORECASE):
        return None

    # Funciones sin equivalencia DAX comprobable → None (evita DAX inválido).
    for func in _UNSUPPORTED_FUNCTIONS:
        if re.search(rf"\b{func}\s*\(", formula, re.IGNORECASE):
            return None

    # AVG(0) -> 0
    if re.match(r"^AVG\s*\(\s*0\s*\)$", formula, re.IGNORECASE):
        return "0"

    # Literal numerico
    if re.match(r"^-?\d+(\.\d+)?$", formula):
        return formula

    # Literal string
    if re.match(r"^['\"].*['\"]$", formula):
        return formula

    # [ColumnName] simple
    if re.match(r"^\[[^\]]+\]$", formula):
        col_name = formula.strip("[]")
        return f"'{safe_table}'[{col_name}]"

    work = formula

    # Tableau acepta literales con comillas simples; DAX usa comillas dobles
    # para texto. Se hace antes de generar referencias 'Tabla'[Columna].
    work = re.sub(r"'([^']*)'", lambda match: f'"{match.group(1)}"', work)

    # DAX-RES-02: DATEPART con granularidad comprobable usa funciones DAX nativas.
    datepart_arg = r"[^,()]*(?:\([^()]*\)[^,()]*)*"

    def replace_datepart(match: re.Match[str]) -> str:
        interval = match.group(1).casefold()
        dax_function = _DATEPART_FUNCTIONS.get(interval)
        if dax_function is None:
            return match.group(0)
        return f"{dax_function}({match.group(2).strip()})"

    work = re.sub(
        rf'\bDATEPART\s*\(\s*["\']([\w-]+)["\']\s*,\s*({datepart_arg})\s*\)',
        replace_datepart,
        work,
        flags=re.IGNORECASE,
    )

    # DAX-RES-03: STR escalar se expresa con CONVERT(..., STRING).
    str_arg = r"[^()]*(?:\([^()]*\)[^()]*)*"
    work = re.sub(
        rf"\bSTR\s*\(\s*({str_arg})\s*\)",
        r"CONVERT(\1, STRING)",
        work,
        flags=re.IGNORECASE,
    )
    work = re.sub(r"\bIIF\s*\(", "IF(", work, flags=re.IGNORECASE)

    # DAX no permite agregar una medida con SUM([Measure]); una medida ya
    # devuelve un escalar en el contexto actual.
    for measure_name in sorted(known_measures, key=len, reverse=True):
        work = re.sub(
            rf"\bSUM\s*\(\s*\[{re.escape(measure_name)}\]\s*\)",
            f"[{measure_name}]",
            work,
            flags=re.IGNORECASE,
        )

    # 1. [Parameters].[Parameter N] -> [Parameter N]
    work = re.sub(r"\[Parameters\]\.\[([^\]]+)\]", r"[\1]", work)

    # SPLIT(texto, delimitador, índice) -> PATHITEM(SUBSTITUTE(...), índice, TEXT).
    # PATHITEM usa índices basados en 1 igual que Tableau.
    work = re.sub(
        r'\bSPLIT\s*\(\s*(\[[^\]]+\])\s*,\s*("[^"]*")\s*,\s*(\d+)\s*\)',
        r'PATHITEM(SUBSTITUTE(\1, \2, "|"), \3, TEXT)',
        work,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bSPLIT\s*\(", work, re.IGNORECASE):
        return None

    # 2. CASE ... WHEN ... THEN ... END -> SWITCH(TRUE(), ...)
    case_match = re.match(r"^CASE\s+(.+?)\s+END\s*$", work, re.IGNORECASE | re.DOTALL)
    if case_match:
        inner = case_match.group(1).strip()
        clauses = re.findall(
            r"WHEN\s+(.+?)\s+THEN\s+(.+?)(?=\s+WHEN|\s+ELSE|\s*$)",
            inner,
            re.IGNORECASE | re.DOTALL,
        )
        else_match = re.search(r"ELSE\s+(.+?)\s+END\s*$", inner, re.IGNORECASE | re.DOTALL)
        else_expr = else_match.group(1).strip() if else_match else "BLANK()"
        first_when = re.search(r"\s+WHEN\s+", inner, re.IGNORECASE)
        expr_full = inner[: first_when.start()].strip() if first_when else ""

        switch_lines = ["SWITCH(", "    TRUE(),"]
        for cond, result in clauses:
            cond_clean = cond.strip()
            result_clean = result.strip()
            switch_lines.append(f"    {expr_full} = {cond_clean}, {result_clean},")
        switch_lines.append(f"    {else_expr}")
        switch_lines.append(")")
        work = "\n".join(switch_lines)

    # 3. IF ... THEN ... ELSE ... END -> IF(cond, true, false)
    if re.search(r"\bELSEIF\b", work, re.IGNORECASE):
        converted_chain = _convert_tableau_if_chain(work)
        if converted_chain is None:
            return None
        work = converted_chain

    if_pat = re.compile(
        r"^IF\s+(.+?)\s+THEN\s+(.+?)\s+ELSE\s+(.+?)\s+END\s*$",
        re.IGNORECASE | re.DOTALL,
    )
    if_m = if_pat.match(work)
    if if_m:
        cond = if_m.group(1).strip()
        true_part = if_m.group(2).strip()
        false_part = if_m.group(3).strip()
        false_part = re.sub(r"\bNULL\b", "BLANK()", false_part, flags=re.IGNORECASE)
        work = f"IF(\n    {cond},\n    {true_part},\n    {false_part}\n)"

    # 4. WINDOW_MAX/WINDOW_MIN -> MAXX/MINX con ALLSELECTED
    def replace_window(m):
        func_name = m.group(1).upper()
        inner_expr = m.group(2)
        dax_func = "MAXX" if func_name == "WINDOW_MAX" else "MINX"
        sum_m = re.match(r"SUM\s*\(\s*\[([^\]]+)\]\s*\)", inner_expr, re.IGNORECASE)
        if sum_m:
            col_ref = sum_m.group(1).strip()
            if col_ref.startswith("Calculation_") or col_ref in known_measures:
                return f"{dax_func}(ALLSELECTED('{safe_table}'), [{col_ref}])"
            return f"{dax_func}(ALLSELECTED('{safe_table}'), CALCULATE(SUM('{safe_table}'[{col_ref}])))"
        return f"{dax_func}(ALLSELECTED('{safe_table}'), {inner_expr})"

    work = re.sub(
        r"(WINDOW_MAX|WINDOW_MIN)\s*\(([^)]+(?:\([^)]*\))*[^)]*)\)",
        replace_window,
        work,
        flags=re.IGNORECASE,
    )

    # 5. Funciones con mapeo de nombre directo (inventory: Soportado).
    #    Agregaciones + texto + ISNULL que no requieren reestructuración.
    for t_func, dax_func in [
        ("SUM", "SUM"),
        ("AVG", "AVERAGE"),
        ("COUNTD", "DISTINCTCOUNT"),
        ("COUNT", "COUNT"),
        ("MEDIAN", "MEDIAN"),
        ("CONTAINS", "CONTAINSSTRING"),
        ("REPLACE", "SUBSTITUTE"),
        ("LTRIM", "TRIM"),
        ("RTRIM", "TRIM"),
        ("ISNULL", "ISBLANK"),
    ]:
        work = re.sub(rf"\b{t_func}\s*\(", f"{dax_func}(", work, flags=re.IGNORECASE)

    # 5b. STARTSWITH/ENDSWITH → expresión DAX equivalente (no son funciones DAX).
    work = re.sub(
        r"\bSTARTSWITH\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)",
        r"LEFT(\1, LEN(\2)) = \2",
        work,
        flags=re.IGNORECASE,
    )
    work = re.sub(
        r"\bENDSWITH\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)",
        r"RIGHT(\1, LEN(\2)) = \2",
        work,
        flags=re.IGNORECASE,
    )
    # Si quedan STARTSWITH/ENDSWITH sin convertir (args complejos) → None.
    if re.search(r"\b(STARTSWITH|ENDSWITH)\s*\(", work, re.IGNORECASE):
        return None

    # 5c. DATEDIFF: reordenar args de Tableau ('day', a, b) → DAX (a, b, DAY).
    #     Permite un nivel de anidamiento en los args (e.g. TODAY()).
    _datediff_arg = r"[^,()]*(?:\([^()]*\)[^,()]*)*"

    def _replace_datediff(m):
        interval = m.group(1).lower()
        dax_int = _DATEDIFF_INTERVALS.get(interval)
        if dax_int is None:
            return m.group(0)
        return f"DATEDIFF({m.group(2)}, {m.group(3)}, {dax_int})"

    work = re.sub(
        rf"\bDATEDIFF\s*\(\s*['\"](\w+)['\"]\s*,\s*({_datediff_arg})\s*,\s*({_datediff_arg})\s*\)",
        _replace_datediff,
        work,
        flags=re.IGNORECASE,
    )
    # DATEDIFF con intervalo no soportado o args complejos → None.
    if re.search(r"\bDATEDIFF\s*\(\s*['\"]", work, re.IGNORECASE):
        return None

    # 6. IFNULL -> IF(ISBLANK(...))
    work = re.sub(
        r"IFNULL\s*\(([^,]+),\s*([^)]+)\)",
        r"IF(ISBLANK(\1), \2, \1)",
        work,
        flags=re.IGNORECASE,
    )

    # 7. ZN -> IF(ISBLANK(...), 0, ...)
    work = re.sub(
        r"ZN\s*\(([^)]+)\)",
        r"IF(ISBLANK(\1), 0, \1)",
        work,
        flags=re.IGNORECASE,
    )

    # 8. NULL -> BLANK()
    work = re.sub(r"\bNULL\b", "BLANK()", work, flags=re.IGNORECASE)

    # 9. Resolver [Name] -> [Measure] o 'Table'[Column]
    parameterized_scalar = (
        "[PARAMETERS]." in formula.upper() and not formula.lstrip().upper().startswith("CASE ")
    )

    def resolve_ref(m):
        ref_name = m.group(1)
        if ref_name in known_measures or ref_name.startswith("Parameter"):
            return f"[{ref_name}]"
        if ref_name.startswith("Calculation_"):
            return f"[{ref_name}]"
        if parameterized_scalar and ref_name in known_columns:
            return f"SELECTEDVALUE('{safe_table}'[{ref_name}])"
        return f"'{safe_table}'[{ref_name}]"

    work = re.sub(r"\[([^\]]+)\]", resolve_ref, work)

    # Los cálculos CASE de Tableau son fila-a-fila, pero aquí se materializan
    # como medidas. Las ramas que devuelven columnas numéricas deben agregarse.
    if formula.lstrip().upper().startswith("CASE "):
        work = re.sub(
            rf"(,\s*)('{re.escape(safe_table)}'\[[^\]]+\])(?=\s*,|\s*$)",
            r"\1SUM(\2)",
            work,
            flags=re.MULTILINE,
        )

    # 10. Operadores logicos
    work = re.sub(r"\s+AND\s+", " && ", work, flags=re.IGNORECASE)
    work = re.sub(r"\s+OR\s+", " || ", work, flags=re.IGNORECASE)
    work = work.replace("<>", "!=")

    converted = work.strip()
    if contains_tableau_residual_syntax(converted):
        return None
    return converted


def convert_tableau_to_dax_diagnostic(
    tableau_formula: str,
    table_name: str = "Table",
    known_measures: set[str] | None = None,
    known_columns: set[str] | None = None,
) -> ConversionDiagnostic:
    """Convierte una fórmula Tableau a DAX devolviendo un diagnóstico estructurado.

    Los casos no equivalentes reciben status "manual" o "unsupported" con la
    razón y los pasos manuales correspondientes — nunca se emite DAX inválido.
    """
    formula = tableau_formula.strip()
    formula_upper = formula.upper()

    if formula.startswith("{") and formula.endswith("}"):
        category = "lod"
    elif any(
        kw in formula_upper
        for kw in ("RUNNING_", "WINDOW_", "INDEX()", "FIRST()", "LAST()", "LOOKUP(")
    ):
        category = "table_calc"
    else:
        category = "calculation"

    dax = convert_tableau_to_dax(tableau_formula, table_name, known_measures, known_columns)

    if dax is not None:
        return ConversionDiagnostic(
            status="supported",
            category=category,
            original=tableau_formula,
            dax=dax,
        )

    if category == "table_calc":
        return ConversionDiagnostic(
            status="unsupported",
            category=category,
            original=tableau_formula,
            reason="Table calculation sin equivalente DAX directo",
            manual_steps=[
                "Identificar el compute-using (partitioning/addressing) de Tableau",
                "Recrear la lógica con CALCULATE + FILTER/ALL",
                "Considerar RANKX para cálculos basados en filas",
            ],
        )

    if category == "lod":
        return ConversionDiagnostic(
            status="manual",
            category=category,
            original=tableau_formula,
            reason="Expresión LOD demasiado compleja para conversión automática",
            manual_steps=[
                "Identificar la(s) dimensión(es) de agrupación en la cláusula LOD",
                "Usar CALCULATE con ALLEXCEPT/REMOVEFILTERS para comportamiento similar",
                "Verificar resultados con datos de prueba",
            ],
        )

    for func in _UNSUPPORTED_FUNCTIONS:
        if re.search(rf"\b{func}\s*\(", formula, re.IGNORECASE):
            return ConversionDiagnostic(
                status="unsupported",
                category=category,
                original=tableau_formula,
                reason=f"Función {func} sin equivalente DAX comprobable",
                manual_steps=[f"Reemplazar {func} manualmente por una expresión DAX equivalente"],
            )

    return ConversionDiagnostic(
        status="manual",
        category=category,
        original=tableau_formula,
        reason="Expresión no convertida automáticamente",
        manual_steps=["Revisar la fórmula Tableau y crear una expresión DAX equivalente"],
    )


def diagnose_filter(
    field: str,
    kind: str,
    values: list[str] | None = None,
    min_value: str | None = None,
    max_value: str | None = None,
    is_action: bool = False,
) -> ConversionDiagnostic:
    """Diagnostica la traducibilidad de un filtro Tableau a PBIR.

    - Categórico con valores explícitos → supported (filterConfig In).
    - Rango/fecha → manual (requiere resolución de tipo/granularidad en Desktop).
    - Acción → unsupported (Power BI usa filtrado cruzado nativo).
    """
    values = values or []

    if is_action:
        return ConversionDiagnostic(
            status="unsupported",
            category="filter",
            original=f"Action filter: {field}",
            reason="Action filters se omiten; Power BI usa filtrado cruzado nativo",
            manual_steps=[
                "Configurar el filtrado cruzado entre visuales en Power BI Desktop",
            ],
        )

    if kind == "categorical" and values:
        return ConversionDiagnostic(
            status="supported",
            category="filter",
            original=f"Categorical filter on {field} ({len(values)} values)",
            dax=f"filterConfig.In({field}, {values})",
        )

    kind_label = kind or "unknown"
    return ConversionDiagnostic(
        status="manual",
        category="filter",
        original=f"{kind_label} filter on {field} (min={min_value}, max={max_value})",
        reason=f"Filtros {kind_label} requieren resolución de tipo y granularidad en Desktop",
        manual_steps=[
            f"Crear un filtro sobre {field} en Power BI Desktop",
            "Configurar el tipo de filtro adecuado (básico/rango/fecha relativa)",
        ],
    )


def suggest_dax(tableau_formula: str) -> dict:
    """
    Analiza una fórmula de Tableau y sugiere conversión a DAX.

    Args:
        tableau_formula: Fórmula original de Tableau

    Returns:
        Dict con:
        - dax_suggestion: Fórmula DAX sugerida (si es posible)
        - complexity: "easy", "medium", "hard", "expert"
        - notes: Notas adicionales
        - manual_steps: Pasos manuales si requiere conversión manual
    """
    formula_upper = tableau_formula.upper()
    result = {
        "original": tableau_formula,
        "dax_suggestion": None,
        "complexity": "easy",
        "notes": [],
        "manual_steps": [],
        "guide": None,
        "translation_status": "supported",
    }

    # NUEVO: Detectar alta complejidad condicional (>5 ramas)
    elseif_count = formula_upper.count("ELSEIF") + formula_upper.count("ELSE IF")
    if elseif_count >= 5:
        result["complexity"] = "expert"
        result["notes"] = [
            "⚠️ Alta complejidad condicional detectada (>5 ramas)",
            "✅ RECOMENDACIÓN: Materializar como Tabla de Dimensión (Lookup Table)",
        ]
        result["manual_steps"] = [
            "1. Crear tabla en BD con todas las combinaciones posibles",
            "2. Usar LOOKUPVALUE en DAX para mapear",
            "3. Beneficio: Mantenimiento centralizado, mejor rendimiento",
        ]
        result["guide"] = """
**Alta Complejidad Condicional → Lookup Table**

En lugar de traducir este IF/CASE complejo a DAX, es mejor práctica:

1. **Crear tabla de mapping en BD**:
```sql
CREATE TABLE dim_mapping AS
SELECT DISTINCT
    original_value,
    mapped_value
FROM (VALUES
    ('Valor1', 'Resultado1'),
    ('Valor2', 'Resultado2'),
    ...
) AS t(original_value, mapped_value);
```

2. **En Power BI usar LOOKUPVALUE**:
```dax
Resultado =
LOOKUPVALUE(
    dim_mapping[mapped_value],
    dim_mapping[original_value],
    Table[Campo]
)
```

**Beneficios**:
- Lógica centralizada en BD (un solo lugar para modificar)
- Mejor rendimiento (lookup vs evaluación condicional)
- Más fácil de mantener y auditar
"""
        result["translation_status"] = "manual"
        return result

    # Detectar LOD
    if re.search(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", formula_upper):
        result["complexity"] = "hard"
        result["notes"].append("LOD expressions require CALCULATE with context modifiers in DAX")
        result["guide"] = CONVERSION_GUIDES.get("LOD FIXED", "")
        result["manual_steps"] = [
            "Identify the grouping dimension(s) in the FIXED clause",
            "Use CALCULATE with ALLEXCEPT for similar behavior",
            "Test with sample data to verify results match",
        ]
        result["dax_suggestion"] = _convert_simple_lod(tableau_formula.strip(), "Table")
        result["translation_status"] = (
            "supported" if result["dax_suggestion"] is not None else "manual"
        )
        return result

    # Detectar Table Calculations
    table_calc_keywords = [
        "RUNNING_",
        "WINDOW_",
        "INDEX()",
        "FIRST()",
        "LAST()",
        "LOOKUP(",
    ]
    if any(kw in formula_upper for kw in table_calc_keywords):
        result["complexity"] = "hard"
        result["notes"].append("Table calculations require careful context handling in DAX")
        result["guide"] = CONVERSION_GUIDES.get("TABLE CALC", "")
        result["manual_steps"] = [
            "Understand the compute using (partitioning/addressing) in Tableau",
            "Recreate the logic using CALCULATE with FILTER/ALL",
            "Consider using RANKX for row-based calculations",
        ]
        result["translation_status"] = "manual"
        return result

    # Intentar conversión directa de funciones
    suggested = tableau_formula
    functions_found = []

    for tableau_func, dax_func in FUNCTION_MAP.items():
        pattern = rf"\b{tableau_func}\s*\("
        if re.search(pattern, formula_upper):
            functions_found.append((tableau_func, dax_func))

            if "---MANUAL---" in dax_func:
                result["complexity"] = "medium"
                result["notes"].append(f"Function {tableau_func} requires manual conversion")
            elif "===" in dax_func:
                result["complexity"] = "hard"
            else:
                # Reemplazar función
                suggested = re.sub(
                    rf"\b{tableau_func}\s*\(",
                    f"{dax_func}(",
                    suggested,
                    flags=re.IGNORECASE,
                )

    # Reemplazar referencias de columna [col] → Table[col] o medida
    suggested = re.sub(r"\[([^\]]+)\]", r"Table[\1]", suggested)

    # Reemplazar operadores lógicos
    suggested = suggested.replace(" AND ", " && ")
    suggested = suggested.replace(" OR ", " || ")
    suggested = suggested.replace("<>", "!=")

    result["dax_suggestion"] = suggested

    # Determinar complejidad basado en la fórmula
    if result["complexity"] == "easy":
        if "IF " in formula_upper or "CASE " in formula_upper:
            result["complexity"] = "medium"
        if formula_upper.count("IF") > 3:
            result["complexity"] = "medium"
            result["notes"].append("Consider using SWITCH for multiple conditions")

    return result


def generate_dax_report(calculations: list[dict]) -> str:
    """Genera un reporte de sugerencias DAX."""
    lines = []
    lines.append("# Sugerencias de Conversión Tableau → DAX")
    lines.append("")

    easy = [c for c in calculations if c["complexity"] == "easy"]
    medium = [c for c in calculations if c["complexity"] == "medium"]
    hard = [c for c in calculations if c["complexity"] == "hard"]

    lines.append(f"**Fácil**: {len(easy)} | **Medio**: {len(medium)} | **Difícil**: {len(hard)}")
    lines.append("")

    # Mostrar conversiones fáciles
    if easy:
        lines.append("## ✅ Conversiones Directas")
        for calc in easy[:5]:
            lines.append(f"\n### {calc.get('name', 'Calculation')}")
            lines.append(
                f"**Tableau**: `{calc['original'][:80]}...`"
                if len(calc["original"]) > 80
                else f"**Tableau**: `{calc['original']}`"
            )
            lines.append(f"**DAX**: `{calc['dax_suggestion']}`")

    # Mostrar complejas
    if hard:
        lines.append("\n## ⚠️ Requieren Atención Manual")
        for calc in hard[:3]:
            lines.append(f"\n### {calc.get('name', 'Calculation')}")
            lines.append(f"**Complejidad**: {calc['complexity']}")
            for note in calc.get("notes", []):
                lines.append(f"- {note}")
            if calc.get("guide"):
                lines.append(calc["guide"])

    return "\n".join(lines)


# CLI
if __name__ == "__main__":
    # Ejemplos de prueba
    test_formulas = [
        "SUM([Sales])",
        "IF [Status] = 'Active' THEN 1 ELSE 0 END",
        "{FIXED [Customer] : SUM([Sales])}",
        "DATEDIFF('day', [Order Date], TODAY())",
        "RUNNING_SUM(SUM([Profit]))",
    ]

    print("=== DAX Converter Demo ===\n")
    for formula in test_formulas:
        result = suggest_dax(formula)
        print(f"Tableau: {formula}")
        print(f"Complexity: {result['complexity']}")
        if result["dax_suggestion"]:
            print(f"DAX: {result['dax_suggestion']}")
        for note in result["notes"]:
            print(f"Note: {note}")
        print()
