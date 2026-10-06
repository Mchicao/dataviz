"""
Filter Analyzer - Analiza filtros usados en Tableau para sugerir restricciones en origen.

Este módulo detecta qué filtros se usan en las hojas del workbook
y sugiere cláusulas WHERE para reducir los datos descargados de la BD.
"""

import re  # Importado a nivel global
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class FilterInfo:
    """Información sobre un filtro detectado."""

    column: str
    filter_type: str  # categorical, range, date
    values: list[str] = field(default_factory=list)
    min_value: str = ""
    max_value: str = ""
    sheets_using: list[str] = field(default_factory=list)


@dataclass
class FilterAnalysis:
    """Resultado del análisis de filtros."""

    datasource_name: str
    filters: list[FilterInfo]
    suggested_where: str
    potential_reduction: str  # Descripción del ahorro potencial


def analyze_filters(file_path: str) -> list[FilterAnalysis]:
    """
    Analiza los filtros usados en un workbook de Tableau.

    Args:
        file_path: Ruta al archivo .twb o .twbx

    Returns:
        Lista de FilterAnalysis por datasource
    """
    # Si es TWBX, extraer TWB
    if file_path.endswith(".twbx"):
        from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx

        with open(file_path, "rb") as f:
            twb_path, temp_dir = extract_twb_from_twbx(f)
        try:
            return _analyze_twb_filters(twb_path)
        finally:
            cleanup_temp_dir(temp_dir)
    else:
        return _analyze_twb_filters(file_path)


def _analyze_twb_filters(twb_path: str) -> list[FilterAnalysis]:
    """Analiza filtros en un archivo TWB."""
    tree = ET.parse(twb_path)
    root = tree.getroot()

    # Recopilar filtros por datasource
    ds_filters: dict[str, dict[str, FilterInfo]] = defaultdict(dict)

    # Mapeo de nombres internos a captions legibles
    name_to_caption = {}

    # Pre-cargar fórmulas de campos calculados y mapeos
    calc_formulas = {}
    for ds in root.findall(".//datasource"):
        for col in ds.findall(".//column"):
            name = col.get("name", "")
            caption = col.get("caption", "")

            # Guardar mapeo name -> caption
            if name and caption:
                clean_key = name.strip("[]")
                name_to_caption[clean_key] = caption
                name_to_caption[name] = caption

            # Cargar fórmulas
            calc = col.find("calculation")
            if calc is not None:
                formula = calc.get("formula", "")
                if name:
                    calc_formulas[name.strip("[]")] = formula
                if caption:
                    calc_formulas[caption] = formula

    def clean_tableau_column_name(raw: str, mapping: dict[str, str] | None = None) -> str:
        """Limpia nombres usando mapeo si existe, o heurísticas."""
        # 0. Si es Measure Names, devolverlo explícitamente
        if "Measure Names" in raw:
            return "Measure Names"

        # 1. Intentar mapeo directo
        if mapping and raw in mapping:
            return mapping[raw]

        clean = raw.strip("[]")
        if mapping and clean in mapping:
            return mapping[clean]

        # 2. Heurísticas base
        name = clean

        # Quitar prefijo de datasource [ds].[col]
        if "]." in name:
            name = name.split("].")[-1].strip("[]")

        # Quitar namespace interno none:col:nk -> col
        if ":" in name:
            parts = name.split(":")
            # Special case for measures like sum:Name:qk
            if parts[0].lower() in ("sum", "avg", "min", "max", "count"):
                # Es una agregación
                if len(parts) >= 2:
                    base_name = parts[1]
                    if mapping and base_name in mapping:
                        base_name = mapping[base_name]
                    return f"{parts[0].upper()}({base_name})"

            # Formato típico: namespace:column:type -> tomar [1]
            if len(parts) >= 2:
                name = parts[1]

        return name

    def parse_formula_to_where(formula: str) -> str:
        """Extrae lógica WHERE de una fórmula IF/CASE de Tableau."""
        pattern = r"\[(.*?)\]\s*=\s*'([^']*)'\s*THEN\s*'([^']*)'"
        matches = re.findall(pattern, formula, re.IGNORECASE)

        if not matches:
            return ""

        col_vals = defaultdict(set)
        for col, val, result in matches:
            if "considerar" in result.lower() and "no" not in result.lower():
                clean_col = clean_tableau_column_name(col, name_to_caption)
                col_vals[clean_col].add(val)

        if not col_vals:
            return ""

        conditions = []
        for col, vals in col_vals.items():
            v_list = sorted(vals)
            if len(v_list) > 1:
                v_str = ", ".join([f"'{v}'" for v in v_list])
                conditions.append(f"{col} IN ({v_str})")
            else:
                conditions.append(f"{col} = '{v_list[0]}'")
        return " AND ".join(conditions)

    # 1. Escanear todos los cálculos para encontrar patrones "Considerar"
    for name, formula in calc_formulas.items():
        if "considerar" in name.lower() or "considerar" in formula.lower():
            # (Lógica simplificada: se detectará en el loop de columnas)
            pass

    # Re-escaneo con contexto de Datasource
    for ds in root.findall(".//datasource"):
        ds_name = ds.get("name") or ds.get("caption")
        if not ds_name:
            continue

        for col in ds.findall(".//column"):
            caption = col.get("caption", col.get("name", "")).strip("[]")
            calc = col.find("calculation")
            if calc is not None:
                formula = calc.get("formula", "")
                if "considerar" in formula.lower() or "considerar" in caption.lower():
                    parsed = parse_formula_to_where(formula)
                    if parsed:
                        key = f"{ds_name}:FILTER_{caption}"
                        if key not in ds_filters[ds_name]:
                            ds_filters[ds_name][key] = FilterInfo(
                                column=caption,
                                filter_type="calculated_sql",
                                values=[parsed],
                                sheets_using=["(Calculated Logic)"],
                            )

    for ws in root.findall(".//worksheet"):
        ws_name = ws.get("name", "")

        # Buscar filtros en la hoja
        for filter_elem in ws.findall(".//filter"):
            column = filter_elem.get("column", "")
            ds_name = filter_elem.get("datasource", "")

            if not column:
                continue

            if not ds_name and column.startswith("["):
                parts = column.split("].[")
                if len(parts) >= 1:
                    ds_name = parts[0].strip("[]")

            if not ds_name:
                ds_name = "unknown"

            # Limpiar nombre de columna usando función robusta y MAPEO
            column_clean = clean_tableau_column_name(column, name_to_caption)

            # Analizar si es campo calculado con fórmula útil
            suggested_logic = ""
            raw_key = column.strip("[]")  # Clave para buscar en formulas
            # Intentar buscar por nombre raw o caption clean
            if raw_key in calc_formulas:
                formula = calc_formulas[raw_key]
                parsed = parse_formula_to_where(formula)
                if parsed:
                    suggested_logic = parsed
            elif column_clean in calc_formulas:
                formula = calc_formulas[column_clean]
                parsed = parse_formula_to_where(formula)
                if parsed:
                    suggested_logic = parsed

            # Determinar tipo de filtro
            filter_type = "categorical"
            values = []
            min_val = ""
            max_val = ""

            # Buscar valores categóricos
            for member in filter_elem.findall(".//groupfilter/groupfilter"):
                val = member.get("member")
                if val:
                    clean_val = val.strip('"')
                    if not clean_val.startswith("%") and not clean_val.endswith("%"):
                        values.append(clean_val)

            range_elem = filter_elem.find(".//range")
            if range_elem is not None:
                filter_type = "range"
                min_val = range_elem.get("from", "")
                max_val = range_elem.get("to", "")

            if "date" in column.lower() or "fecha" in column.lower():
                filter_type = "date"

            # Agregar o actualizar filtro
            key = f"{ds_name}:{column_clean}"
            if key not in ds_filters[ds_name]:
                if suggested_logic:
                    filter_type = "calculated_sql"
                    values = [suggested_logic]

                ds_filters[ds_name][key] = FilterInfo(
                    column=column_clean,
                    filter_type=filter_type,
                    values=values,
                    min_value=min_val,
                    max_value=max_val,
                    sheets_using=[ws_name],
                )
            else:
                ds_filters[ds_name][key].sheets_using.append(ws_name)
                for v in values:
                    if v not in ds_filters[ds_name][key].values:
                        ds_filters[ds_name][key].values.append(v)

    # Generar análisis por datasource
    results = []
    for ds_name, filters_dict in ds_filters.items():
        filters = list(filters_dict.values())

        if not filters:
            continue

        where_clauses = []
        seen_conditions = set()

        for f in filters:
            condition = ""
            col_name = clean_tableau_column_name(f.column, name_to_caption)

            # --- MEJORA: Ignorar filtros internos de Tableau como Measure Names ---
            if "Measure Names" in col_name or "Nombres de medidas" in col_name:
                # No afectan a nivel de fila SQL
                continue

            if f.filter_type == "calculated_sql" and f.values:
                condition = f.values[0]
            elif f.filter_type == "categorical" and f.values:
                vals = ", ".join([f"'{v}'" for v in f.values[:10]])
                if len(f.values) > 10:
                    vals += f" -- +{len(f.values) - 10} más"
                condition = f"{col_name} IN ({vals})"
            elif f.filter_type == "date" and (f.min_value or f.max_value):
                if f.min_value and f.max_value:
                    condition = f"{col_name} BETWEEN '{f.min_value}' AND '{f.max_value}'"
                elif f.min_value:
                    condition = f"{col_name} >= '{f.min_value}'"
                elif f.max_value:
                    condition = f"{col_name} <= '{f.max_value}'"
            elif f.filter_type == "range" and (f.min_value or f.max_value):
                condition = f"{col_name} BETWEEN {f.min_value} AND {f.max_value}"

            if condition and condition not in seen_conditions:
                where_clauses.append(condition)
                seen_conditions.add(condition)

        suggested_where = (
            " AND\n    ".join(where_clauses)
            if where_clauses
            else "-- No se detectaron filtros específicos"
        )

        date_filters = [f for f in filters if f.filter_type == "date"]
        if date_filters:
            potential = "🔴 ALTO - Filtros de fecha detectados."
        elif len(where_clauses) > 2:
            potential = "🟡 MEDIO - Múltiples filtros detectados."
        else:
            potential = "🟢 BAJO - Pocos filtros."

        results.append(
            FilterAnalysis(
                datasource_name=ds_name,
                filters=filters,
                suggested_where=suggested_where,
                potential_reduction=potential,
            )
        )

    return results


def get_common_date_filters(analyses: list[FilterAnalysis]) -> dict[str, set[str]]:
    """
    Encuentra filtros de fecha comunes entre todos los datasources.

    Útil para detectar si todos los reportes filtran por el mismo rango de fechas
    y así sugerir una restricción global en la BD.
    """
    date_columns = defaultdict(set)

    for analysis in analyses:
        for f in analysis.filters:
            if f.filter_type == "date":
                if f.min_value:
                    date_columns[f.column].add(f"min:{f.min_value}")
                if f.max_value:
                    date_columns[f.column].add(f"max:{f.max_value}")

    return dict(date_columns)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.filter_analyzer <archivo.twb>")
        sys.exit(1)

    analyses = analyze_filters(sys.argv[1])

    for a in analyses:
        print(f"\n{'=' * 60}")
        print(f"Datasource: {a.datasource_name}")
        print(f"Filtros detectados: {len(a.filters)}")
        print(f"Reducción potencial: {a.potential_reduction}")
        print("\nWHERE sugerido:")
        print(f"    {a.suggested_where}")
