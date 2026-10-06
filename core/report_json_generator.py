"""
Generador de report.json en formato legacy para compatibilidad con Power BI Report Server.
Este formato NO usa carpetas PBIR (definition/pages/) sino un único archivo JSON.
Formato corregido basado en análisis de PBIX reales exportados por Power BI Desktop.
"""

import json
import logging
import os
import shutil
import uuid
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)


def generate_legacy_report(
    report_folder: str,
    tableau_xml_path: str,
    semantic_model_name: str,
    project_name: str = "MigratedReport",
) -> str:
    """
    Genera un report.json en formato legacy compatible con PBIRS.

    Args:
        report_folder: Carpeta .Report del proyecto
        tableau_xml_path: Ruta al archivo .twb/.twbx
        semantic_model_name: Nombre del SemanticModel asociado
        project_name: Nombre del proyecto

    Returns:
        Ruta al archivo report.json generado
    """
    logger.info(f"Generando report.json legacy para {project_name}")

    # Parsear Tableau
    try:
        tree = ET.parse(tableau_xml_path)
        root = tree.getroot()
    except Exception as e:
        logger.error(f"Error parseando Tableau: {e}")
        return ""

    # Extraer worksheets y dashboards
    worksheets = root.findall(".//worksheet")
    dashboards = root.findall(".//dashboard")

    # Generar estructura de reporte en formato LEGACY
    sections = []

    # Procesar dashboards primero (páginas principales)
    ordinal = 0
    for dash in dashboards:
        dash_name = dash.get("name", "Dashboard")
        page_id = _gen_page_id()

        # Extraer zonas del dashboard
        zones = dash.findall(".//zone")
        visual_containers = []

        for zone in zones:
            ws_name = zone.get("name")
            if ws_name:
                ws = root.find(f".//worksheet[@name='{ws_name}']")
                if ws:
                    visual = _create_legacy_visual(ws, zone)
                    if visual:
                        visual_containers.append(visual)

        section = {
            "config": "{}",
            "displayName": dash_name,
            "displayOption": 3,
            "filters": "[]",
            "height": 720.00,
            "name": page_id,
            "ordinal": ordinal,
            "visualContainers": visual_containers,
            "width": 1280.00,
        }
        sections.append(section)
        ordinal += 1

    # Procesar worksheets sueltos
    dash_ws_names = set()
    for dash in dashboards:
        for zone in dash.findall(".//zone"):
            if zone.get("name"):
                dash_ws_names.add(zone.get("name"))

    for ws in worksheets:
        ws_name = ws.get("name")
        if ws_name and ws_name not in dash_ws_names and not ws_name.startswith("tooltip"):
            page_id = _gen_page_id()
            visual = _create_legacy_visual(ws, None)

            if visual:
                section = {
                    "config": "{}",
                    "displayName": ws_name,
                    "displayOption": 3,
                    "filters": "[]",
                    "height": 720.00,
                    "name": page_id,
                    "ordinal": ordinal,
                    "visualContainers": [visual],
                    "width": 1280.00,
                }
                sections.append(section)
                ordinal += 1

    # Construir config embebido (string JSON escapado)
    config_obj = {
        "version": "5.66",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU10",
                "type": 2,
                "version": {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"},
            }
        },
        "activeSectionIndex": 0,
        "defaultDrillFilterOtherVisuals": True,
        "linguisticSchemaSyncVersion": 2,
        "settings": {
            "useNewFilterPaneExperience": True,
            "allowChangeFilterTypes": True,
            "useStylableVisualContainerHeader": True,
            "queryLimitOption": 6,
            "exportDataMode": 1,
            "useDefaultAggregateDisplayName": True,
            "useEnhancedTooltips": True,
        },
    }

    # Construir report.json LEGACY (formato correcto)
    report_json = {
        "config": json.dumps(config_obj, ensure_ascii=False),
        "layoutOptimization": 0,
        "resourcePackages": [
            {
                "resourcePackage": {
                    "disabled": False,
                    "items": [
                        {"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": 202}
                    ],
                    "name": "SharedResources",
                    "type": 2,
                }
            }
        ],
        "sections": sections,
    }

    # Guardar
    output_path = os.path.join(report_folder, "report.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_json, f, indent=2, ensure_ascii=False)

    logger.info(f"report.json generado: {len(sections)} páginas")
    return output_path


def _gen_page_id() -> str:
    """Genera un ID de página en formato hexadecimal corto."""
    return uuid.uuid4().hex[:20]


def _gen_visual_id() -> str:
    """Genera un ID de visual en formato hexadecimal corto."""
    return uuid.uuid4().hex[:20]


def _clean_table_name(ds_elem: ET.Element) -> str:
    """
    Limpia el nombre de la tabla para coincidir con tom_engine.py.
    Prioriza 'caption', luego 'name'. Elimina caracteres invalidos.
    """
    raw_name = ds_elem.get("caption", ds_elem.get("name", ""))
    # Misma logica que tom_engine.py
    clean = raw_name.replace("[", "").replace("]", "").replace(".", "").strip()
    return clean[:50]  # Truncar por seguridad


def _clean_column_name(tableau_field: str) -> str:
    """
    Limpia el nombre del campo Tableau para coincidir con la columna en PBI.
    Ej: '[none:dias_admision:nk]' -> 'dias_admision'
    Ej: '[My Field]' -> 'My Field'
    """
    # 1. Quitar corchetes externos
    field = tableau_field.strip("[]")

    # 2. Manejar sintaxis interna de Tableau (none:..., :nk, etc)
    # Ej: none:campo:nk -> campo
    if ":" in field:
        parts = field.split(":")
        # Si tiene forma prefix:name:suffix (3 partes)
        if len(parts) >= 2:
            # Tomar la parte del medio o la que no sea 'none', 'nk', 'qk'
            candidates = [p for p in parts if p not in ("none", "nk", "qk", "ok")]
            if candidates:
                field = candidates[0]
            else:
                field = parts[0]  # Fallback

    # 3. Limpiar caracteres igual que tom_engine (replace . con espacio)
    field = field.replace(".", " ").strip()
    return field


def _extract_tableau_fields(ws: ET.Element) -> tuple[list[str], list[str], list[str], str]:
    """
    Extrae campos de un worksheet de Tableau respetando su ubicación (Shelf).

    Returns:
        tuple: (rows_shelf, cols_shelf, measures, table_name)
    """
    rows_shelf = []
    cols_shelf = []
    measures = []
    table_name = "Data"

    # Buscar datasource para obtener nombre de tabla
    datasources = ws.findall(".//datasource")
    if datasources:
        ds = datasources[0]
        ds_name = ds.get("name", "")
        if not ds_name.startswith("Parameters"):
            table_name = _clean_table_name(ds)

    # Función auxiliar mejorada
    def extract_from_shelf(text: str) -> list[str]:
        found = []
        if not text:
            return found

        # Formato: [datasource].[field] o [field] separados por ][ o combinados
        # Ejemplo complejo: [federated.1...].[none:Calculation...:nk]
        # Mejor estrategia: Split por "][" y limpiar

        # 1. Normalizar separadores
        text = text.replace("] [", "][")

        # 2. Split
        tokens = text.strip("[]").split("][")

        for token in tokens:
            # token es algo como "datasource].[field" o "field"
            if "].[" in f"[{token}]":
                parts = token.split("].[")
                col_part = parts[-1].strip("]")
            else:
                col_part = token

            clean = _clean_column_name(col_part)
            if clean and clean != "Measure Names":
                found.append(clean)
        return found

    # 1. Rows Shelf
    for row in ws.findall(".//rows"):
        rows_shelf.extend(extract_from_shelf(row.text))

    # 2. Cols Shelf
    for col in ws.findall(".//cols"):
        cols_shelf.extend(extract_from_shelf(col.text))

    # 3. Measures/Text (implícito en bindings o encodings)
    # Buscar en encoding (text, shape, color)
    for encoding in ws.findall(".//encoding"):
        field_elem = encoding.find(".//column")
        if field_elem is not None:
            raw_col = field_elem.get("name", "")
            # Limpieza rapida
            parts = raw_col.split("].[")
            col_part = parts[-1].strip("]") if len(parts) > 1 else raw_col.strip("[]")
            clean_col = _clean_column_name(col_part)

            # Si no está en rows ni cols, probablemente es una medida usada en el visual
            if clean_col and clean_col not in rows_shelf and clean_col not in cols_shelf:
                if clean_col != "Measure Names":
                    measures.append(clean_col)

    # Deduplicar preservando orden
    return (
        list(dict.fromkeys(rows_shelf)),
        list(dict.fromkeys(cols_shelf)),
        list(dict.fromkeys(measures)),
        table_name,
    )


def _create_data_bindings(
    pbi_type: str,
    rows_shelf: list[str],
    cols_shelf: list[str],
    measures: list[str],
    table_name: str,
) -> tuple[dict, dict]:
    """
    Crea projections y prototypeQuery mapeando Shelves de Tableau a Roles de PBI.
    """
    safe_table = table_name.replace("'", "").replace('"', "")
    alias = safe_table[0].lower() if safe_table else "t"

    projections = {}
    selects = []

    # Helper para agregar columna a selects
    def add_select(field_name, is_measure=False):
        # Evitar duplicados en Select
        ref_name = f"{safe_table}.{field_name}"
        if any(s["Name"] == ref_name for s in selects):
            return

        if is_measure:
            # Sum por defecto para medidas implícitas
            selects.append(
                {
                    "Aggregation": {
                        "Expression": {
                            "Column": {
                                "Expression": {"SourceRef": {"Source": alias}},
                                "Property": field_name,
                            }
                        },
                        "Function": 0,  # Sum
                    },
                    "Name": ref_name,
                    "NativeReferenceName": field_name,
                }
            )
        else:
            selects.append(
                {
                    "Column": {
                        "Expression": {"SourceRef": {"Source": alias}},
                        "Property": field_name,
                    },
                    "Name": ref_name,
                    "NativeReferenceName": field_name,
                }
            )

    # Helper para agregar proyección
    def add_projection(role, field_name, active=False):
        if role not in projections:
            projections[role] = []

        entry = {"queryRef": f"{safe_table}.{field_name}"}
        if active:
            entry["active"] = True
        projections[role].append(entry)

    # --- Lógica de Mapeo ---

    # 1. Matrices y Tablas (pivotTable, tableEx)
    if pbi_type in ["pivotTable", "tableEx"]:
        # Rows -> Rows
        for f in rows_shelf:
            add_select(f)
            add_projection("Rows", f, active=True)

        # Cols -> Columns
        for f in cols_shelf:
            add_select(f)
            add_projection("Columns", f, active=True)

        # Measures -> Values
        # Si no hay medidas explícitas, tal vez están en el texto
        if not measures and not rows_shelf and not cols_shelf:
            # Caso borde: nada detectado
            pass

        current_measures = measures if measures else []
        # Fallback: si tenemos tableEx y todo está en rows, mover algo a values?
        # No, tableEx usa Values para todo.

        if pbi_type == "tableEx":
            # TableEx (tabla plana): Todo va a "Values" normalmente
            # Pero internamente PBI usa "Values" para campos planos
            for f in rows_shelf + cols_shelf + current_measures:
                add_select(f)
                add_projection("Values", f)
        else:
            # PivotTable (Matriz)
            for m in current_measures:
                add_select(m, is_measure=True)
                add_projection("Values", m)

    # 2. Charts (Column, Line, Area, Bar)
    elif pbi_type in [
        "clusteredColumnChart",
        "lineChart",
        "areaChart",
        "clusteredBarChart",
        "scatterChart",
    ]:
        # Estrategia general:
        # Cols Shelf -> X Axis (Category)
        # Rows Shelf -> Y Axis (Values)
        # (O viceversa si es BarChart horizontal, pero PBI maneja Category/Y igual)

        # En Tableau: Cols = Eje X, Rows = Eje Y

        # Category (Eje X)
        x_fields = cols_shelf if cols_shelf else rows_shelf  # Fallback

        # Values (Eje Y)
        y_fields = rows_shelf if cols_shelf else measures  # Fallback

        # Si chart es horizontal (BarChart), a veces se invierte visualmente,
        # pero dataRoles suele ser Category=Y_Axis_Visual, Y=X_Axis_Visual.
        # Asumiremos standard: Cols -> Category, Rows -> Y (Metric)

        if x_fields:
            cat = x_fields[0]  # PBI charts usualmente 1 categoria principal
            add_select(cat)
            add_projection("Category", cat, active=True)

        if y_fields:
            # Filtrar lo que ya usamos en categoria
            metrics = [f for f in y_fields if f not in x_fields]
            if not metrics and measures:
                metrics = measures

            for m in metrics[:1]:  # Solo 1 métrica principal por ahora para simplificar
                add_select(m, is_measure=True)
                add_projection("Y", m)

        # Si tenemos Legend (Color), suele estar en measures o cols extra
        # Aquí podriamos implementar "Series"

    # 3. Card / KPI
    elif pbi_type == "card":
        # Usar la primera medida o campo disponible
        candidates = measures + rows_shelf + cols_shelf
        if candidates:
            f = candidates[0]
            add_select(f, is_measure=True)
            add_projection(
                "Data", f
            )  # Card usa 'Data' o 'Values' dependiendo versión, 'Data' es comun

    # Default fallback
    else:
        # Tratar como tabla simple
        for f in rows_shelf + cols_shelf + measures:
            add_select(f)
            add_projection("Values", f)

    # Construir Query
    if selects:
        prototype_query = {
            "Version": 2,
            "From": [{"Name": alias, "Entity": safe_table, "Type": 0}],
            "Select": selects,
        }
        return projections, prototype_query
    else:
        return {}, {}


def _create_legacy_visual(ws: ET.Element, zone: ET.Element | None) -> dict[str, Any] | None:
    """
    Crea una configuración de visual en formato LEGACY (visualContainers).
    Este formato usa 'config' como string JSON anidado.
    Incluye projections y prototypeQuery para data bindings.
    """
    ws_name = ws.get("name", "Visual")

    # Detectar tipo de marca
    # 1. Intentar desde style/graph (más preciso para tipos de hoja)
    graph_style = ws.find(".//style/graph")
    mark_type = "bar"  # Default

    if graph_style is not None and graph_style.get("type"):
        mark_type = graph_style.get("type")
    else:
        # 2. Fallback a mark class
        mark_elem = ws.find(".//mark")
        if mark_elem is not None:
            mark_type = mark_elem.get("class", "bar")

    # Mapear usando la lógica centralizada
    from core.visual_mapper import mapear_tipo_visual_tableau_a_pbi

    pbi_type = mapear_tipo_visual_tableau_a_pbi(mark_type)

    # Lógica de refinamiento específica para tablas
    # Si es 'text' o 'dataset', puede ser Table o Matrix dependiendo de las shelves
    rows_shelf, cols_shelf, measures, table_name = _extract_tableau_fields(ws)

    if pbi_type in ("tableEx", "table"):
        # En Power BI:
        # - Table (tableEx): Solo tiene columnas de valores, no headers jerárquicos.
        # - Matrix (pivotTable): Tiene filas y/o columnas jerárquicas.

        # En Tableau:
        # - Crosstab tiene dimensiones en Rows/Cols -> Matrix
        # - Text Table simple -> Table

        # Heurística: Si hay dimensiones en Columnas (Cols Shelf), es casi seguro una Matrix
        if cols_shelf:
            pbi_type = "pivotTable"
        # Si tiene Rows pero NO Cols, puede ser Table o Matrix.
        # PBI Matrix es mejor para jerarquias. Table es mejor para listados planos.
        elif len(rows_shelf) > 1:
            # Si hay multiples niveles en filas, mejor Matrix
            pbi_type = "pivotTable"

    # Extraer campos de Tableau (ya hecho arriba)
    # rows_shelf, cols_shelf, measures, table_name = _extract_tableau_fields(ws)

    # Crear data bindings
    projections, prototype_query = _create_data_bindings(
        pbi_type, rows_shelf, cols_shelf, measures, table_name
    )

    # Posición del visual - Tableau usa twips (~100x más grande)
    SCALE = 100.0
    x, y, w, h = 20, 100, 600, 400
    if zone is not None:
        x = float(zone.get("x", "2000")) / SCALE
        y = float(zone.get("y", "10000")) / SCALE
        w = max(100, float(zone.get("w", "60000")) / SCALE)
        h = max(80, float(zone.get("h", "40000")) / SCALE)

    visual_id = _gen_visual_id()

    # Config interno del visual con data bindings
    single_visual = {
        "visualType": pbi_type,
        "drillFilterOtherVisuals": True,
        "objects": {},
        "vcObjects": {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": f"'{ws_name}'"}}},
                    }
                }
            ]
        },
    }

    # Solo agregar data bindings si existen (Fix for PBI deserializeQuery crash)
    if prototype_query:
        single_visual["projections"] = projections
        single_visual["prototypeQuery"] = prototype_query

    visual_config = {
        "name": visual_id,
        "layouts": [
            {
                "id": 0,
                "position": {
                    "x": x,
                    "y": y,
                    "z": 0,
                    "width": w,
                    "height": h,
                    "tabOrder": 0,
                },
            }
        ],
        "singleVisual": single_visual,
    }

    # Formato LEGACY: config como string, propiedades planas
    return {
        "config": json.dumps(visual_config, ensure_ascii=False),
        "filters": "[]",
        "height": h,
        "width": w,
        "x": x,
        "y": y,
        "z": 0.00,
    }


def remove_pbir_structure(report_folder: str) -> None:
    """
    Elimina la estructura PBIR (carpetas definition/pages) si existe.
    Útil para asegurar compatibilidad con PBIRS.
    """
    pages_dir = os.path.join(report_folder, "definition", "pages")
    if os.path.exists(pages_dir):
        shutil.rmtree(pages_dir, ignore_errors=True)
        logger.info(f"Eliminada estructura PBIR: {pages_dir}")

    # Mantener definition.pbir pero sin pages
    definition_dir = os.path.join(report_folder, "definition")
    if os.path.exists(definition_dir) and not os.listdir(definition_dir):
        os.rmdir(definition_dir)
