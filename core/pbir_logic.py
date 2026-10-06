"""
Módulo para crear páginas PBIR (Power BI Enhanced Report Format) en proyectos Power BI.
Crea páginas automáticamente basadas en hojas de Tableau.

Portado desde la versión alternativa.
"""

import csv
import json
import logging
import os
import unicodedata
from pathlib import Path

from . import visual_creator
from .tableau_parser import TableauWorkbookIR

logger = logging.getLogger(__name__)


def _cargar_miembros_categoricos(
    carpeta_semantic_model: str | None,
    max_miembros: int = 36,
) -> dict[str, dict[str, list[str]]]:
    """Perfila dominios pequeños de los CSV exportados para trellis y filtros.

    Las columnas que superan ``max_miembros`` se descartan durante el barrido;
    así no se retienen cardinalidades altas ni se carga el CSV completo.
    """
    if not carpeta_semantic_model:
        return {}
    data_dir = Path(carpeta_semantic_model) / "Data"
    if not data_dir.is_dir():
        return {}

    result: dict[str, dict[str, list[str]]] = {}
    for csv_path in sorted(data_dir.glob("*.csv")):
        domains: dict[str, set[str] | None] = {}
        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                continue
            domains = {str(column): set() for column in reader.fieldnames}
            for row in reader:
                for column, value in row.items():
                    domain = domains.get(str(column))
                    if domain is None:
                        continue
                    normalized = "%null%" if value is None or value == "" else str(value)
                    domain.add(normalized)
                    if len(domain) > max_miembros:
                        domains[str(column)] = None

        low_cardinality = {
            column: sorted(values, key=str.casefold)
            for column, values in domains.items()
            if values is not None and len(values) > 1
        }
        if low_cardinality:
            result[csv_path.stem] = low_cardinality
    return result


def _normalizar_nombre(nombre: str) -> str:
    """Normaliza un nombre para comparación (minúsculas, sin acentos)."""
    if not nombre:
        return ""
    return (
        unicodedata.normalize("NFD", nombre)
        .encode("ascii", "ignore")
        .decode("utf-8")
        .lower()
        .strip()
    )


def _obtener_nombres_paginas_existentes(carpeta_report: str) -> list[str]:
    """Obtiene lista de nombres de páginas ya existentes en el reporte."""
    nombres = []
    carpeta_pages = os.path.join(carpeta_report, "definition", "pages")
    if not os.path.exists(carpeta_pages):
        return []

    for item in os.listdir(carpeta_pages):
        page_dir = os.path.join(carpeta_pages, item)
        if os.path.isdir(page_dir):
            page_json_path = os.path.join(page_dir, "page.json")
            if os.path.exists(page_json_path):
                try:
                    with open(page_json_path, encoding="utf-8") as f:
                        data = json.load(f)
                        if "displayName" in data:
                            nombres.append(data["displayName"])
                        elif "name" in data:
                            nombres.append(data["name"])
                except Exception:
                    pass
    return nombres


def verificar_formato_pbir(carpeta_report: str) -> bool:
    """Verifica si el proyecto usa formato PBIR."""
    carpeta_definition = os.path.join(carpeta_report, "definition")
    archivo_report_json = os.path.join(carpeta_report, "report.json")

    if os.path.exists(carpeta_definition) and not os.path.exists(archivo_report_json):
        return True

    archivo_definition_pbir = os.path.join(carpeta_report, "definition.pbir")
    if os.path.exists(archivo_definition_pbir):
        return True

    return False


def crear_pagina_pbir(
    carpeta_report: str,
    nombre_pagina: str,
    descripcion_visual: str = "",
    hoja_tableau: dict | None = None,
    mapa_fuentes: dict[str, str] | None = None,
    columnas_por_tabla: dict[str, dict[str, str]] | None = None,
    measures_map: dict[str, str] | None = None,
    miembros_por_tabla: dict[str, dict[str, list[str]]] | None = None,
) -> str | None:
    """
    Crea una página PBIR en un proyecto Power BI.

    Returns:
        ID de la página creada (nombre carpeta) o None si falló
    """
    try:
        # Verificar carpeta definition
        carpeta_definition = os.path.join(carpeta_report, "definition")
        if not os.path.exists(carpeta_definition):
            os.makedirs(carpeta_definition, exist_ok=True)

        # Carpeta de páginas
        carpeta_pages = os.path.join(carpeta_definition, "pages")
        os.makedirs(carpeta_pages, exist_ok=True)

        # Verificar si ya existe una página con este displayName
        import secrets
        for existing_dir in os.listdir(carpeta_pages):
            if not os.path.isdir(os.path.join(carpeta_pages, existing_dir)):
                continue
            existing_page_json = os.path.join(carpeta_pages, existing_dir, "page.json")
            if os.path.exists(existing_page_json):
                try:
                    with open(existing_page_json, encoding="utf-8") as f:
                        existing_data = json.load(f)
                        if existing_data.get("displayName") == nombre_pagina:
                            logger.info(f"La página '{nombre_pagina}' ya existe, saltando...")
                            return existing_dir
                except Exception:
                    pass

        # Paso 3: Usar ID hexadecimal estándar (como Power BI Desktop)
        nombre_safe = secrets.token_hex(10)
        carpeta_pagina = os.path.join(carpeta_pages, nombre_safe)

        os.makedirs(carpeta_pagina, exist_ok=True)

        # Crear page.json
        page_json = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
            "name": nombre_safe,
            "displayName": nombre_pagina,
            "width": 1920,
            "height": 1080,
            "displayOption": "FitToPage",
        }

        try:
            with open(os.path.join(carpeta_pagina, "page.json"), "w", encoding="utf-8") as f:
                json.dump(page_json, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error escribiendo page.json para {nombre_pagina}: {e}")

        # Crear carpeta de visuals
        carpeta_visuals = os.path.join(carpeta_pagina, "visuals")
        os.makedirs(carpeta_visuals, exist_ok=True)

        # Crear visuals automáticamente
        if hoja_tableau:
            logger.info(f"Generando visuals para página {nombre_pagina}...")
            visual_creator.crear_visuales_para_pagina(
                carpeta_visuals,
                hoja_tableau,
                posicion=None,
                mapa_fuentes=mapa_fuentes,
                columnas_por_tabla=columnas_por_tabla,
                measures_map=measures_map,
                miembros_por_tabla=miembros_por_tabla,
            )
        else:
            _crear_visual_texto_fallback(carpeta_visuals, descripcion_visual, nombre_pagina)

        logger.info(f"Página PBIR creada: {nombre_pagina}")
        return nombre_safe

    except Exception as e:
        logger.error(f"Error creando página PBIR: {e}", exc_info=True)
        return None


def _crear_visual_texto_fallback(carpeta_visuals: str, descripcion_visual: str, nombre_pagina: str):
    """Crea un visual de texto como fallback."""
    import secrets

    visual_id = secrets.token_hex(10)
    visual_folder = os.path.join(carpeta_visuals, visual_id)
    os.makedirs(visual_folder, exist_ok=True)

    visual_texto = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
        "name": visual_id,
        "position": {"x": 0, "y": 0, "z": 0, "width": 800, "height": 400, "tabOrder": 0},
        "visual": {
            "visualType": "textbox",
            "objects": {
                "general": [
                    {
                        "properties": {
                            "paragraphs": [
                                {
                                    "textRuns": [
                                        {
                                            "value": descripcion_visual
                                            or f"Página migrada desde Tableau: {nombre_pagina}",
                                            "textStyle": {
                                                "fontFamily": "Segoe UI",
                                                "fontSize": "14px",
                                            },
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            },
        },
    }

    with open(os.path.join(visual_folder, "visual.json"), "w", encoding="utf-8") as f:
        json.dump(visual_texto, f, indent=2, ensure_ascii=False)


def _actualizar_pages_metadata(carpeta_pages: str, new_page_ids: list[str]):
    """Actualiza o crea pages.json preservando páginas existentes."""
    metadata_path = os.path.join(carpeta_pages, "pages.json")
    existing_ids = []

    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                data = json.load(f)
                existing_ids = data.get("pageOrder", [])
        except Exception:
            pass

    # Combinar listas sin duplicados
    all_ids = existing_ids.copy()
    for pid in new_page_ids:
        if pid not in all_ids:
            all_ids.append(pid)

    pages_metadata = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": all_ids,
        "activePageName": all_ids[0] if all_ids else "",
    }

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(pages_metadata, f, indent=2, ensure_ascii=False)


def crear_paginas_desde_tableau(
    carpeta_report: str,
    hojas_tableau: list[dict],
    carpeta_semantic_model: str = "",
    mapa_tablas_tmdl: dict[str, str] | None = None,
    ruta_tableau: str = "",
) -> str:
    """
    Crea páginas PBIR para cada hoja en el diccionario hojas_tableau.
    """
    if not os.path.exists(carpeta_report):
        return f"❌ Carpeta Report no existe: {carpeta_report}"

    # FAIL-SAFE: Si no hay hojas, crear una página de resumen de error
    if not hojas_tableau:
        logger.warning("No se detectaron hojas de Tableau válidas. Generando página Fail-Safe.")
        # Inventamos una 'hoja falsa' para que el proceso continúe y genere la estructura válida
        hojas_tableau = [{
            "Nombre": "Resumen de Migración",
            "Tipo_Visual": "text",
            "Fuente_Datos": "System",
            "Campos_Filas": [],
            "Campos_Columnas": [],
            "Campos_Medidas": [],
        }]
        # Creamos la carpeta de reporte si aun no existe
        if not os.path.exists(carpeta_report):
            os.makedirs(carpeta_report, exist_ok=True)

    # Crear carpeta definition si no existe
    carpeta_definition = os.path.join(carpeta_report, "definition")
    os.makedirs(carpeta_definition, exist_ok=True)

    # Crear version.json (REQUERIDO por Power BI Desktop)
    # Schema from: https://github.com/microsoft/json-schemas/tree/main/fabric/item/report/definition/versionMetadata
    version_json_path = os.path.join(carpeta_definition, "version.json")
    if not os.path.exists(version_json_path):
        version_data = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        }
        with open(version_json_path, "w", encoding="utf-8") as f:
            json.dump(version_data, f, indent=2, ensure_ascii=False)
        logger.info("Creado version.json para PBIR")

    # Crear report.json (REQUERIDO por Power BI Desktop)
    # Schema 3.0.0: https://github.com/microsoft/json-schemas/tree/main/fabric/item/report/definition/report
    report_json_path = os.path.join(carpeta_definition, "report.json")
    if not os.path.exists(report_json_path):
        report_data = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU10",
                    "reportVersionAtImport": {
                        "visual": "1.8.97",
                        "report": "2.0.97",
                        "page": "1.3.97",
                    },
                    "type": "SharedResources",
                }
            },
            "objects": {
                "section": [
                    {"properties": {"verticalAlignment": {"expr": {"Literal": {"Value": "'Top'"}}}}}
                ],
                "outspacePane": [
                    {
                        "properties": {
                            "expanded": {
                                "expr": {
                                    "Literal": {
                                        "Value": "false"
                                    }
                                }
                            }
                        }
                    }
                ]
            },
            "resourcePackages": [
                {
                    "name": "SharedResources",
                    "type": "SharedResources",
                    "items": [
                        {
                            "name": "CY24SU10",
                            "path": "BaseThemes/CY24SU10.json",
                            "type": "BaseTheme",
                        }
                    ],
                }
            ],
            "settings": {
                "useStylableVisualContainerHeader": True,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": True,
                "allowChangeFilterTypes": True,
                "useEnhancedTooltips": True,
                "useDefaultAggregateDisplayName": True,
            },
        }
        with open(report_json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        logger.info("Creado report.json para PBIR (schema 3.0.0)")

    mapa_tablas_tmdl = mapa_tablas_tmdl or {}

    # --- MAPA DE MEDIDAS (cálculos/parámetros materializados en el modelo) ---
    # Permite que los bindings de visuales que referencian cálculos/parámetros
    # por su nombre interno se resuelvan contra las medidas del modelo.
    measures_map: dict[str, str] = {}
    if ruta_tableau:
        try:
            import xml.etree.ElementTree as ET

            from core import calculation_extractor

            # Cálculos y parámetros globales
            analysis = calculation_extractor.extract_calculations(ruta_tableau)
            for calc in analysis.calculated_fields:
                if calc.calc_type.value == "bin":
                    continue
                measures_map[calc.name] = calc.name
            for param in analysis.parameters:
                measures_map[param.name] = param.name

            # Cálculos worksheet-local (ej. Calculation_* con AVG(0))
            # que no aparecen en el datasource global pero que tom_engine
            # materializa desde el XML del worksheet.
            tree = ET.parse(ruta_tableau)
            root = tree.getroot()
            for ws in root.findall(".//worksheet"):
                for col_elem in ws.findall(".//datasource-dependencies//column"):
                    calc_elem = col_elem.find("calculation")
                    if calc_elem is not None:
                        local_name = col_elem.get("name", "").strip("[]")
                        if local_name and local_name not in measures_map:
                            measures_map[local_name] = local_name

        except Exception as e:
            logger.warning(f"Fallo construyendo measures_map: {e}")

    # --- NUEVA LÓGICA DE MAPEO (Legacy V2 Style) ---
    # Usar datasource_mapper si tenemos la ruta del archivo Tableau
    columnas_por_tabla = {}
    mapa_fuentes: dict[str, str] = {}

    # Intentar usar el sistema robusto si tenemos ruta_tableau
    if ruta_tableau and carpeta_semantic_model:
        try:
            from core import datasource_mapper, tableau_parser

            logger.info("Usando sistema de mapeo robusto (datasource_mapper)...")

            # Extraer datasources detallados usando el parser actualizado
            reader = tableau_parser.TableauReader(ruta_tableau)
            datasources_tableau = reader.get_detailed_datasources()

            # Crear mapeo completo
            mapa_fuentes, columnas_por_tabla = datasource_mapper.crear_mapa_completo_datasources(
                datasources_tableau, carpeta_semantic_model
            )

            logger.info(f"Mapeo robusto completado: {len(mapa_fuentes)} fuentes mapeadas")
        except Exception as e:
            logger.warning(f"Fallo en mapeo robusto: {e}. Usando fallback simple.")

    # Fallback: Sistema simple (TMDL directo) si no hay resultados o falló lo anterior
    if not mapa_fuentes and carpeta_semantic_model:
        # A) Intentar leer desde TMDL (carpeta definition/tables)
        columnas_por_tabla = visual_creator.obtener_metadatos_tablas_pbi(carpeta_semantic_model)

        # B) Fallback: Intentar leer desde model.bim si no hubo TMDL
        if not columnas_por_tabla:
            logger.info("No se encontraron metadatos TMDL, intentando leer model.bim...")
            columnas_por_tabla = visual_creator.obtener_metadatos_desde_bim(carpeta_semantic_model)

        for table_name in columnas_por_tabla.keys():
            normalized = _normalizar_nombre(table_name)
            mapa_fuentes[normalized] = table_name

    # Incorporar mapa externo (argumento)
    if mapa_tablas_tmdl:
        for redshift_name, tmdl_name in mapa_tablas_tmdl.items():
            mapa_fuentes[redshift_name.lower()] = tmdl_name
            normalized = _normalizar_nombre(redshift_name)
            mapa_fuentes[normalized] = tmdl_name

    # Depuración del mapeo
    logger.info(f"Fuentes disponibles en PBI: {list(columnas_por_tabla.keys())}")
    logger.info(f"Mapa de normalización construido: {mapa_fuentes}")

    # Fallback extremo: Si no hay mapa pero hay tablas, usar la primera por defecto
    if not mapa_fuentes and columnas_por_tabla:
        primera_tabla = next(iter(columnas_por_tabla.keys()))
        logger.warning(f"⚠️ No hay mapeo exacto. Asignando tabla por defecto: {primera_tabla}")
        mapa_fuentes["default"] = primera_tabla
        mapa_fuentes["extract"] = primera_tabla
        mapa_fuentes["data"] = primera_tabla

    # TRELLIS-02: los CSV ya exportados son la autoridad para dominios
    # categóricos pequeños; no se infieren miembros desde nombres de demo.
    miembros_por_tabla = _cargar_miembros_categoricos(carpeta_semantic_model)
    logger.info(
        "Dominios categóricos perfilados para %s tablas",
        len(miembros_por_tabla),
    )

    logger.info(f"Procesando {len(hojas_tableau)} worksheets para dashboard...")

    nombres_existentes = _obtener_nombres_paginas_existentes(carpeta_report)
    existentes_norm = {_normalizar_nombre(n) for n in nombres_existentes}

    creadas = 0
    omitidas = 0
    errores = 0
    page_ids = []

    # Cada item "Dashboard" genera una sola página con todos sus worksheets;
    # cada item "Hoja" genera una página independiente. Genérico, sin nombres de demo.
    for hoja in hojas_tableau:
        nombre = hoja.get("Nombre") or hoja.get("Nombre_Hoja", "Sin nombre")
        nombre_norm = _normalizar_nombre(nombre)

        if nombre_norm in existentes_norm:
            omitidas += 1
            continue

        tipo_visual = hoja.get("Tipo_Visual", hoja.get("Tipo_Marca", "Desconocido"))

        if hoja.get("Fuente_Datos") == "System":
            descripcion = "⚠️ **Migración Visual Incompleta**\\n\\n"
            descripcion += "No se pudieron extraer automáticamente los visuales de Tableau.\\n"
            descripcion += "Este es un proyecto base válido. Puedes conectar tus datos manualmente."
        else:
            descripcion = "Página migrada desde Tableau\\n\\n"
            descripcion += f"Hoja original: {nombre}\\n"
            descripcion += f"Tipo de visualización en Tableau: {tipo_visual}\\n"

        page_id = crear_pagina_pbir(
            carpeta_report, nombre, descripcion, hoja, mapa_fuentes, columnas_por_tabla,
            measures_map=measures_map,
            miembros_por_tabla=miembros_por_tabla,
        )

        if page_id:
            creadas += 1
            page_ids.append(page_id)
        else:
            errores += 1

    if page_ids:
        carpeta_pages = os.path.join(carpeta_report, "definition", "pages")
        _actualizar_pages_metadata(carpeta_pages, page_ids)

    resultado = f"✅ Páginas PBIR creadas: {creadas}"
    if omitidas > 0:
        resultado += f"\\nℹ️ Omitidas: {omitidas}"
    if errores > 0:
        resultado += f"\\n⚠️ Errores: {errores}"

    logger.info(f"Proceso completado: {creadas} creadas, {omitidas} omitidas, {errores} errores")
    return resultado


def crear_paginas_desde_ir(
    carpeta_report: str,
    workbook_ir: TableauWorkbookIR,
    worksheet_details: dict[str, dict] | None = None,
    carpeta_semantic_model: str = "",
    mapa_tablas_tmdl: dict[str, str] | None = None,
    ruta_tableau: str = "",
) -> str:
    """
    Crea una página PBIR por dashboard del IR tipado (Workbook→Dashboard→Zone→Worksheet).

    Genérico: deriva todo del IR, sin depender de nombres de demos. Cada dashboard
    genera una página con sus zones/worksheets; las hojas sueltas generan una página cada una.
    """
    items = workbook_ir.to_migration_items(worksheet_details or {})
    return crear_paginas_desde_tableau(
        carpeta_report,
        items,
        carpeta_semantic_model=carpeta_semantic_model,
        mapa_tablas_tmdl=mapa_tablas_tmdl,
        ruta_tableau=ruta_tableau,
    )
