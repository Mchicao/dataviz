"""
Calculation Extractor - Extrae campos calculados y parámetros de Tableau.

Este módulo analiza los campos calculados para exponer su lógica
en la UI y facilitar la migración a Power BI DAX.
"""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum

from core.dax_converter import suggest_dax


class CalculationType(Enum):
    """Tipo de campo calculado."""

    SIMPLE = "simple"  # Fórmula simple (ej: [col1] + [col2])
    CONDITIONAL = "conditional"  # IF/CASE/IIF
    AGGREGATION = "aggregation"  # SUM, AVG, COUNT, etc.
    DATE = "date"  # DATEDIFF, DATEPART, etc.
    STRING = "string"  # CONTAINS, LEFT, RIGHT, etc.
    LOD = "lod"  # FIXED, INCLUDE, EXCLUDE
    TABLE_CALC = "table_calc"  # RUNNING, WINDOW, INDEX, etc.
    BIN = "bin"  # Tableau bin generated from a source column
    PARAMETER = "parameter"  # Parámetro
    UNKNOWN = "unknown"


@dataclass
class CalculatedField:
    """Representa un campo calculado de Tableau."""

    name: str
    caption: str
    formula: str
    datatype: str
    calc_type: CalculationType
    referenced_columns: list[str] = field(default_factory=list)
    is_used: bool = False
    complexity_score: int = 1  # 1-10, usado para priorizar migración
    dax_suggestion: str | None = None  # Sugerencia de conversión a DAX
    source_datasource: str | None = None
    used_by_worksheets: list[str] = field(default_factory=list)
    referenced_parameters: list[str] = field(default_factory=list)

    @property
    def formula_preview(self) -> str:
        """Vista previa corta de la fórmula."""
        if len(self.formula) <= 80:
            return self.formula
        return self.formula[:77] + "..."


@dataclass
class ParameterInfo:
    """Representa un parámetro de Tableau."""

    name: str
    caption: str
    datatype: str
    default_value: str | None = None
    allowable_values: list[str] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None
    source_datasource: str | None = None
    used_by_worksheets: list[str] = field(default_factory=list)
    referenced_by: list[str] = field(default_factory=list)


@dataclass
class WorkbookCalculations:
    """Resultado del análisis de cálculos del workbook."""

    file_path: str
    calculated_fields: list[CalculatedField] = field(default_factory=list)
    parameters: list[ParameterInfo] = field(default_factory=list)
    worksheet_field_usage: dict[str, list[str]] = field(default_factory=dict)
    unresolved_worksheet_fields: dict[str, list[str]] = field(default_factory=dict)
    parameter_filter_usage: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_calculations(self) -> int:
        return len(self.calculated_fields)

    @property
    def conditional_count(self) -> int:
        return sum(1 for c in self.calculated_fields if c.calc_type == CalculationType.CONDITIONAL)

    @property
    def lod_count(self) -> int:
        return sum(1 for c in self.calculated_fields if c.calc_type == CalculationType.LOD)

    @property
    def table_calc_count(self) -> int:
        return sum(1 for c in self.calculated_fields if c.calc_type == CalculationType.TABLE_CALC)

    @property
    def bin_count(self) -> int:
        return sum(1 for c in self.calculated_fields if c.calc_type == CalculationType.BIN)


def classify_calculation(formula: str) -> CalculationType:
    """Clasifica el tipo de cálculo basado en la fórmula."""
    formula_upper = formula.upper()

    # LOD Expressions (más específicas primero)
    if re.search(r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b", formula_upper):
        return CalculationType.LOD

    # Table Calculations
    if any(
        kw in formula_upper
        for kw in ["RUNNING_", "WINDOW_", "INDEX()", "FIRST()", "LAST()", "LOOKUP("]
    ):
        return CalculationType.TABLE_CALC

    # Conditional
    if any(kw in formula_upper for kw in ["IF ", "IIF(", "CASE ", "WHEN ", "ELSEIF", "THEN "]):
        return CalculationType.CONDITIONAL

    # Aggregation
    if any(
        kw in formula_upper
        for kw in ["SUM(", "AVG(", "COUNT(", "MIN(", "MAX(", "MEDIAN(", "COUNTD("]
    ):
        return CalculationType.AGGREGATION

    # Date functions
    if any(
        kw in formula_upper
        for kw in [
            "DATEDIFF(",
            "DATEPART(",
            "DATEADD(",
            "DATETRUNC(",
            "TODAY()",
            "NOW()",
        ]
    ):
        return CalculationType.DATE

    # String functions
    if any(
        kw in formula_upper
        for kw in [
            "CONTAINS(",
            "LEFT(",
            "RIGHT(",
            "MID(",
            "LEN(",
            "UPPER(",
            "LOWER(",
            "TRIM(",
            "SPLIT(",
        ]
    ):
        return CalculationType.STRING

    return CalculationType.SIMPLE


def extract_referenced_columns(formula: str) -> list[str]:
    """Extrae los nombres de columnas referenciadas en la fórmula."""
    # Patrón para [nombre_columna]
    pattern = r"\[([^\]]+)\]"
    matches = re.findall(pattern, formula)
    # Deduplicar y ordenar
    return sorted(set(matches))


def extract_referenced_parameters(text: str) -> list[str]:
    """Extrae referencias calificadas a parámetros Tableau."""
    return sorted(set(re.findall(r"\[Parameters\]\.\[([^\]]+)\]", text)))


def _extract_parameter_filter_usage(root: ET.Element) -> dict[str, list[str]]:
    """Indexa referencias de parámetros usadas en filtros Tableau."""
    usage: dict[str, list[str]] = {}
    for groupfilter in root.findall(".//groupfilter"):
        text = " ".join(groupfilter.attrib.values())
        parameters = extract_referenced_parameters(text)
        label = f"groupfilter:{groupfilter.get('function', 'unknown')}"
        for parameter_name in parameters:
            usage.setdefault(parameter_name, []).append(label)
    return {name: sorted(set(labels)) for name, labels in usage.items()}


def calculate_complexity(formula: str, calc_type: CalculationType) -> int:
    """Calcula un score de complejidad 1-10."""
    score = 1

    # Basado en tipo
    type_scores = {
        CalculationType.SIMPLE: 1,
        CalculationType.STRING: 2,
        CalculationType.DATE: 3,
        CalculationType.AGGREGATION: 3,
        CalculationType.CONDITIONAL: 4,
        CalculationType.LOD: 7,
        CalculationType.TABLE_CALC: 8,
        CalculationType.BIN: 3,
    }
    score = type_scores.get(calc_type, 1)

    # Ajustar por longitud
    if len(formula) > 500:
        score += 2
    elif len(formula) > 200:
        score += 1

    # Ajustar por anidamiento
    nested_count = formula.count("(")
    if nested_count > 10:
        score += 2
    elif nested_count > 5:
        score += 1

    # Múltiples condiciones
    if formula.upper().count("ELSEIF") > 5:
        score += 1

    return min(score, 10)


def decode_formula(formula: str) -> str:
    """Decodifica entidades HTML en la fórmula."""
    # Tableau usa entidades HTML en el XML
    decoded = html.unescape(formula)
    # Reemplazar &#13;&#10; por newlines
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
    return decoded


def _normalize_worksheet_field(raw_field: str) -> str:
    """Normaliza una referencia Tableau encontrada en dependencias o shelves."""
    field_name = raw_field.strip().strip("[]")
    if ":" in field_name:
        parts = field_name.split(":")
        if len(parts) >= 3:
            field_name = parts[1]
    return field_name.strip("[]")


def _extract_worksheet_field_usage(root: ET.Element) -> dict[str, list[str]]:
    """Indexa referencias de campos por worksheet sin depender de TOM/pythonnet."""
    datasource_names = {
        value
        for datasource in root.findall(".//datasources/datasource")
        for value in (datasource.get("name"), datasource.get("caption"))
        if value
    }
    usage: dict[str, list[str]] = {}

    for worksheet in root.findall(".//worksheet"):
        worksheet_name = worksheet.get("name") or ""
        fields: set[str] = set()

        for column in worksheet.findall(".//datasource-dependencies/column"):
            name = column.get("name")
            if name:
                fields.add(_normalize_worksheet_field(name))

        fragments: list[str] = []
        for element in worksheet.iter():
            fragments.extend(value for value in element.attrib.values() if value)
            if element.text:
                fragments.append(element.text)

        for token in re.findall(r"\[([^\]]+)\]", " ".join(fragments)):
            normalized = _normalize_worksheet_field(token)
            if normalized and normalized not in datasource_names:
                fields.add(normalized)

        usage[worksheet_name] = sorted(field for field in fields if field)

    return usage


def _assign_worksheet_usage(result: WorkbookCalculations) -> None:
    """Completa uso de cálculos/parámetros y conserva referencias no materializadas."""
    calculation_by_name = {calculation.name: calculation for calculation in result.calculated_fields}
    parameter_by_name = {parameter.name: parameter for parameter in result.parameters}
    known_fields = set(calculation_by_name) | set(parameter_by_name)

    for calculation in result.calculated_fields:
        for parameter_name in calculation.referenced_parameters:
            if parameter_name in parameter_by_name:
                parameter_by_name[parameter_name].referenced_by.append(
                    f"calculation:{calculation.name}"
                )
    for parameter_name, references in result.parameter_filter_usage.items():
        if parameter_name in parameter_by_name:
            parameter_by_name[parameter_name].referenced_by.extend(references)

    for worksheet_name, fields in result.worksheet_field_usage.items():
        for field_name in fields:
            if field_name in calculation_by_name:
                calculation_by_name[field_name].used_by_worksheets.append(worksheet_name)
            elif field_name in parameter_by_name:
                parameter_by_name[field_name].used_by_worksheets.append(worksheet_name)

    for calculation in result.calculated_fields:
        calculation.used_by_worksheets = sorted(set(calculation.used_by_worksheets))
        calculation.is_used = bool(calculation.used_by_worksheets)
    for parameter in result.parameters:
        parameter.used_by_worksheets = sorted(set(parameter.used_by_worksheets))
        parameter.referenced_by = sorted(set(parameter.referenced_by))

    unresolved: dict[str, list[str]] = {}
    generated_prefixes = ("Calculation_", "Latitude", "Longitude")
    generated_names = {"Multiple Values"}
    for worksheet_name, fields in result.worksheet_field_usage.items():
        missing = sorted(
            field
            for field in fields
            if field not in known_fields
            and (field.startswith(generated_prefixes) or field in generated_names)
        )
        if missing:
            unresolved[worksheet_name] = missing
    result.unresolved_worksheet_fields = unresolved


def extract_calculations(file_path: str) -> WorkbookCalculations:
    """
    Extrae todos los campos calculados y parámetros de un workbook.

    Args:
        file_path: Ruta al archivo .twb o .twbx

    Returns:
        WorkbookCalculations con el análisis completo
    """
    # Extraer TWB si es TWBX
    if file_path.lower().endswith(".twbx"):
        from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx

        with open(file_path, "rb") as f:
            twb_path, temp_dir = extract_twb_from_twbx(f)
        try:
            return _extract_from_twb(twb_path, file_path)
        finally:
            cleanup_temp_dir(temp_dir)
    else:
        return _extract_from_twb(file_path, file_path)


def _extract_from_twb(twb_path: str, original_path: str) -> WorkbookCalculations:
    """Extrae cálculos de un archivo TWB."""
    tree = ET.parse(twb_path)
    root = tree.getroot()

    result = WorkbookCalculations(file_path=original_path)
    result.worksheet_field_usage = _extract_worksheet_field_usage(root)
    result.parameter_filter_usage = _extract_parameter_filter_usage(root)
    seen_calcs = set()
    seen_parameters = set()

    # Procesar cada datasource
    for ds in root.findall(".//datasources/datasource"):
        ds_name = ds.get("name", "")

        # Buscar parámetros (datasource especial)
        if "Parameters" in ds_name:
            for col in ds.findall(".//column"):
                param_name = col.get("name", "").strip("[]")
                if not param_name or param_name in seen_parameters:
                    continue
                seen_parameters.add(param_name)
                param_caption = col.get("caption", param_name)
                param_type = col.get("datatype", "string")

                # Buscar valor default y rango
                calc = col.find("calculation")
                default_val = None
                if calc is not None:
                    default_val = calc.get("formula", "").strip("'\"")

                # Buscar valores permitidos
                range_elem = col.find("range")
                allowable = []
                min_val = None
                max_val = None

                if range_elem is not None:
                    min_val = range_elem.get("min")
                    max_val = range_elem.get("max")

                members = col.find("members")
                if members is not None:
                    for member in members.findall("member"):
                        val = member.get("value", "")
                        if val:
                            allowable.append(val.strip("'\""))

                result.parameters.append(
                    ParameterInfo(
                        name=param_name,
                        caption=param_caption,
                        datatype=param_type,
                        default_value=default_val,
                        allowable_values=allowable,
                        min_value=min_val,
                        max_value=max_val,
                        source_datasource=ds_name,
                    )
                )
            continue

        # Buscar campos calculados
        for col in ds.findall(".//column"):
            # Tableau puede repetir las definiciones de parámetros en el datasource
            # normal; no deben entrar al inventario de cálculos.
            if col.get("param-domain-type"):
                continue
            calc_elem = col.find("calculation")
            if calc_elem is None:
                continue

            formula_raw = calc_elem.get("formula", "")
            if not formula_raw:
                continue

            col_name = col.get("name", "").strip("[]")

            # Evitar duplicados
            if col_name in seen_calcs:
                continue
            seen_calcs.add(col_name)

            col_caption = col.get("caption", col_name)
            col_type = col.get("datatype", "string")

            # Decodificar y analizar fórmula
            formula = decode_formula(formula_raw)
            calc_type = (
                CalculationType.BIN
                if calc_elem.get("class") == "bin"
                else classify_calculation(formula)
            )
            refs = extract_referenced_columns(formula)
            parameter_refs = extract_referenced_parameters(formula)
            for raw_parameter in calc_elem.get("size-parameter", "").split(","):
                parameter_refs.extend(extract_referenced_parameters(raw_parameter))
            parameter_refs = sorted(set(parameter_refs))
            complexity = calculate_complexity(formula, calc_type)

            # Las bins dependen de size-parameter y no son una medida DAX directa.
            dax_suggestion = None
            if calc_type != CalculationType.BIN:
                dax_suggestion = suggest_dax(formula).get("dax_suggestion")

            result.calculated_fields.append(
                CalculatedField(
                    name=col_name,
                    caption=col_caption,
                    formula=formula,
                    datatype=col_type,
                    calc_type=calc_type,
                    referenced_columns=refs,
                    complexity_score=complexity,
                    dax_suggestion=dax_suggestion,
                    source_datasource=ds_name,
                    referenced_parameters=parameter_refs,
                )
            )

    _assign_worksheet_usage(result)

    # Ordenar por complejidad (más complejos primero)
    result.calculated_fields.sort(key=lambda x: -x.complexity_score)

    return result


def generate_calculation_report(analysis: WorkbookCalculations) -> str:
    """Genera un reporte de texto del análisis."""
    lines = []
    lines.append("=" * 60)
    lines.append("ANÁLISIS DE CAMPOS CALCULADOS - BI Bridge Studio")
    lines.append("=" * 60)
    lines.append(f"Archivo: {analysis.file_path}")
    lines.append(f"Total Calculados: {analysis.total_calculations}")
    lines.append(f"  - Condicionales (IF/CASE): {analysis.conditional_count}")
    lines.append(f"  - LOD (FIXED/INCLUDE): {analysis.lod_count}")
    lines.append(f"  - Table Calcs: {analysis.table_calc_count}")
    lines.append(f"Parámetros: {len(analysis.parameters)}")
    lines.append("")

    # Top 10 más complejos
    lines.append("-" * 40)
    lines.append("TOP 10 MÁS COMPLEJOS (priorizar migración):")
    lines.append("-" * 40)

    for calc in analysis.calculated_fields[:10]:
        lines.append(f"\n📊 {calc.caption}")
        lines.append(f"   Tipo: {calc.calc_type.value} | Complejidad: {calc.complexity_score}/10")
        lines.append(f"   Fórmula: {calc.formula_preview}")
        if calc.referenced_columns:
            lines.append(f"   Referencias: {', '.join(calc.referenced_columns[:5])}")

    # Parámetros
    if analysis.parameters:
        lines.append("\n" + "-" * 40)
        lines.append("PARÁMETROS:")
        lines.append("-" * 40)
        for param in analysis.parameters:
            lines.append(f"\n🎛️ {param.caption}")
            lines.append(f"   Tipo: {param.datatype}")
            if param.default_value:
                lines.append(f"   Default: {param.default_value}")
            if param.allowable_values:
                values_preview = ", ".join(param.allowable_values[:5])
                lines.append(f"   Valores: {values_preview}...")

    return "\n".join(lines)


# CLI
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.calculation_extractor <archivo.twb>")
        sys.exit(1)

    analysis = extract_calculations(sys.argv[1])
    print(generate_calculation_report(analysis))
