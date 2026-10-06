"""
Módulo para análisis y auditoría avanzada de migraciones Tableau -> Power BI
Integra funcionalidades de los scripts de análisis independientes
"""

import json
import logging
import os
import re
from pathlib import Path

import pandas as pd

from core import config_manager as config

logger = logging.getLogger(__name__)


def obtener_conexiones_tableau(ruta_archivo_tableau: str) -> list[dict]:
    """
    Obtiene información detallada de los datasources definidos en el archivo Tableau.
    Versión mejorada para máxima compatibilidad con diferentes versiones de Tableau.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    conexiones = []
    try:
        tree = None
        # Abrir archivo (TWB o TWBX)
        if str(ruta_archivo_tableau).lower().endswith(".twbx"):
            with zipfile.ZipFile(ruta_archivo_tableau, "r") as z:
                archivos_twb = [f for f in z.namelist() if f.endswith(".twb")]
                if not archivos_twb:
                    return []
                with z.open(archivos_twb[0]) as f:
                    tree = ET.parse(f)
        else:
            tree = ET.parse(ruta_archivo_tableau)

        root = tree.getroot()

        # Buscar todos los elementos <datasource>
        # Algunos están en raíz /datasources/datasource, otros dispersos. .// es más seguro.
        for ds in root.findall(".//datasource"):
            # Ignorar parámetros de Tableau
            ds_name = ds.get("caption") or ds.get("name")
            if not ds_name or "Parameters" in ds_name:
                continue

            nombre_interno = ds.get("name", "")

            # Buscar información de conexión
            info_conn = {
                "Fuente_Datos_Tableau": ds_name,
                "DataSource_Name_Internal": nombre_interno,
                "Tipo": "Unknown",
                "Nombre_Tabla_Redshift": None,
                "Query_SQL_Completa": "",
                "Server": "",
                "Database": "",
                "Schema": "",
            }

            # 1. Intentar obtener de <connection>
            conn = ds.find(".//connection")
            if conn is not None:
                info_conn["Server"] = conn.get("server") or conn.get("host") or ""
                info_conn["Database"] = conn.get("dbname") or conn.get("database") or ""
                info_conn["Schema"] = conn.get("schema") or ""

                # 2. Buscar <relation> para el nombre de la tabla o SQL
                relation = conn.find(".//relation")
                if relation is not None:
                    rel_type = relation.get("type")
                    info_conn["Tipo"] = rel_type

                    if rel_type == "text":
                        # Custom SQL
                        info_conn["Nombre_Tabla_Redshift"] = "CUSTOM SQL"
                        info_conn["Query_SQL_Completa"] = (
                            relation.text.strip() if relation.text else ""
                        )
                    else:
                        # Tabla normal
                        # En .twb el nombre de la tabla suele estar en 'table'
                        tabla_raw = relation.get("table") or relation.get("name")
                        if tabla_raw:
                            # Preservar formato [schema].[table] completo, solo limpiar brackets
                            tabla_limpia = tabla_raw.replace("[", "").replace("]", "")
                            info_conn["Nombre_Tabla_Redshift"] = tabla_limpia

                            # Si tiene punto, extraer el schema también
                            if "." in tabla_limpia:
                                parts = tabla_limpia.split(".")
                                if len(parts) >= 2:
                                    info_conn["Schema"] = parts[0]

            # Si no hay tabla pero el nombre indica algo, usarlo como fallback
            if not info_conn["Nombre_Tabla_Redshift"] and ds_name:
                # Ver si el caption de la fuente parece un nombre de tabla
                if "_" in ds_name and " " not in ds_name:
                    info_conn["Nombre_Tabla_Redshift"] = ds_name

            conexiones.append(info_conn)

    except Exception as exc:
        logger.error(f"Error obteniendo conexiones: {exc}")

    return conexiones


def extraer_conexiones_tableau(ruta_archivo_tableau: str) -> str:
    """
    Extrae todas las conexiones y tablas SQL de un archivo Tableau y genera
    el Excel histórico utilizado por la app.
    """
    logger.info(f"Extrayendo conexiones de: {Path(ruta_archivo_tableau).name}")

    try:
        conexiones = obtener_conexiones_tableau(ruta_archivo_tableau)

        if not conexiones:
            logger.warning("No se encontraron conexiones a tablas")
            return "⚠️ No se encontraron conexiones a tablas. Revisa si es un extracto puro (.hyper) sin conexión viva."

        df = pd.DataFrame(conexiones)
        df = df.drop_duplicates(
            subset=["Fuente_Datos_Tableau", "Nombre_Tabla_Redshift"], keep="first"
        )
        output_file = config.PROJECT_ROOT / "Inventario_Tablas_Redshift.xlsx"
        df.to_excel(output_file, index=False)

        logger.info(f"Éxito. {len(df)} tablas/consultas encontradas (filtradas)")
        return (
            f"✅ ÉXITO. Se encontraron {len(df)} tablas/consultas (Extract.Extract filtradas).\n"
            f"📊 Reporte guardado en: {output_file.name}\n"
            f"📁 Ubicación: {output_file.parent}"
        )

    except Exception as e:
        error_msg = f"❌ Error: {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def mapear_fuentes_pbi(carpeta_semantic_model: str) -> str:
    """
    Extrae las fuentes reales de Redshift desde un modelo Power BI.
    Similar a 'mapear_fuentes_pbi.py'

    Args:
        carpeta_semantic_model: Ruta a la carpeta .SemanticModel

    Returns:
        Mensaje de resultado con ruta del Excel generado
    """
    logger.info(f"Radiografía de modelo PBI: {Path(carpeta_semantic_model).name}")

    def encontrar_carpeta_tablas(ruta_base: str) -> tuple:
        """Busca dónde están las tablas"""
        rutas_posibles = [
            os.path.join(ruta_base, "tables"),
            os.path.join(ruta_base, "definition", "tables"),
        ]

        for r in rutas_posibles:
            if os.path.exists(r):
                logger.debug(f"Carpeta de tablas encontrada: {r}")
                return r, "TMDL"

        # Si no encuentra carpetas, busca el archivo único model.bim
        archivo_bim = os.path.join(ruta_base, "model.bim")
        if os.path.exists(archivo_bim):
            logger.debug("Encontrado archivo único model.bim")
            return archivo_bim, "BIM"

        return None, None

    def extraer_fuente_tmdl(contenido: str) -> tuple:
        """Extrae info de archivos .tmdl"""
        origen = "Desconocido"
        schema = "Desconocido"
        tipo = "Tabla Directa"

        # Buscar Schema y Tabla
        match_item = re.search(r'Item="([^"]+)"', contenido)
        match_name = re.search(r'Name="([^"]+)"', contenido)
        match_schema = re.search(r'Schema="([^"]+)"', contenido)

        # Buscar SQL
        if "Value.NativeQuery" in contenido or "select " in contenido.lower():
            tipo = "Custom SQL"
            match_sql = re.search(
                r'select\s+.*?\s+from\s+([^\s"]+)', contenido, re.IGNORECASE | re.DOTALL
            )
            if match_sql:
                origen = match_sql.group(1)

        if match_item:
            origen = match_item.group(1)
        elif match_name and tipo != "Custom SQL":
            origen = match_name.group(1)
        if match_schema:
            schema = match_schema.group(1)

        full_name = f"{schema}.{origen}" if schema != "Desconocido" else origen
        return full_name, tipo

    def extraer_fuente_json(json_data: dict) -> list[dict]:
        """Extrae info de estructura JSON (model.bim)"""
        lista_tablas = []
        for t in json_data.get("model", {}).get("tables", []):
            nombre_pbi = t.get("name")
            if nombre_pbi.startswith("DateTable") or nombre_pbi.startswith("LocalDate"):
                continue

            origen = "Desconocido"
            tipo = "Import"

            # Navegar hasta la partición M
            try:
                partitions = t.get("partitions", [])
                if partitions:
                    expression = partitions[0].get("source", {}).get("expression", "")
                    if isinstance(expression, list):
                        expression = "".join(expression)

                    # Regex básico sobre el código M
                    if 'Schema="' in expression:
                        sch = re.search(r'Schema="([^"]+)"', expression).group(1)
                        itm = re.search(r'Item="([^"]+)"', expression).group(1)
                        origen = f"{sch}.{itm}"
                    elif 'Item="' in expression:
                        origen = re.search(r'Item="([^"]+)"', expression).group(1)
            except Exception:
                pass

            lista_tablas.append(
                {
                    "Nombre_en_PowerBI": nombre_pbi,
                    "Fuente_Real_Redshift": origen,
                    "Tipo_Conexion": tipo,
                }
            )
        return lista_tablas

    try:
        ruta_objetivo, modo = encontrar_carpeta_tablas(carpeta_semantic_model)

        datos = []

        if not ruta_objetivo:
            error_msg = f"❌ ERROR CRÍTICO: No se encontraron definiciones de tablas en {carpeta_semantic_model}"
            logger.error(error_msg)
            return error_msg

        # MODO A: Archivos TMDL (Carpetas)
        if modo == "TMDL":
            archivos = [f for f in os.listdir(ruta_objetivo) if f.endswith(".tmdl")]
            logger.info(f"Procesando {len(archivos)} archivos .tmdl")

            for archivo in archivos:
                path = os.path.join(ruta_objetivo, archivo)
                nombre_pbi = archivo.replace(".tmdl", "")

                if nombre_pbi.startswith("LocalDate") or nombre_pbi.startswith(
                    "DateTable"
                ):
                    continue

                try:
                    with open(path, encoding="utf-8") as f:
                        contenido = f.read()
                        fuente, tipo = extraer_fuente_tmdl(contenido)
                        datos.append(
                            {
                                "Nombre_en_PowerBI": nombre_pbi,
                                "Fuente_Real_Redshift": fuente,
                                "Tipo_Conexion": tipo,
                            }
                        )
                except Exception as e:
                    logger.warning(f"Error leyendo {archivo}: {e}")

        # MODO B: Archivo BIM (JSON único)
        elif modo == "BIM":
            logger.info("Procesando archivo JSON...")
            with open(ruta_objetivo, encoding="utf-8") as f:
                data = json.load(f)
                datos = extraer_fuente_json(data)

        # Exportar
        if datos:
            df = pd.DataFrame(datos)
            output = config.PROJECT_ROOT / "Mapa_Real_PBI_Redshift.xlsx"
            df.to_excel(output, index=False)
            logger.info(f"Radiografía completada. Archivo: {output.name}")
            return f"✅ EXCEL GENERADO: {output.name}\n{len(df)} tablas mapeadas."
        else:
            logger.warning("No se extrajeron datos")
            return "⚠️ No se extrajeron datos."

    except Exception as e:
        error_msg = f"❌ Error: {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def comparar_modelos(ruta_excel_tableau: str, carpeta_semantic_model: str) -> str:
    """
    Compara modelos Tableau vs Power BI y genera reporte de GAP.
    Similar a 'auditar_avance.py'

    Args:
        ruta_excel_tableau: Ruta al Excel con inventario de Tableau
        carpeta_semantic_model: Ruta a la carpeta .SemanticModel de Power BI

    Returns:
        Mensaje de resultado con ruta del reporte generado
    """
    logger.info("Iniciando comparación Tableau vs Power BI...")

    try:
        # 1. Cargar Tableau
        if not os.path.exists(ruta_excel_tableau):
            error_msg = f"❌ No encuentro el Excel de Tableau en: {ruta_excel_tableau}"
            logger.error(error_msg)
            return error_msg

        df_tableau = pd.read_excel(ruta_excel_tableau)
        logger.info(f"Tableau: {len(df_tableau)} tablas detectadas")

        # 2. Escanear Power BI (usar función de smart_mapping)
        from smart_mapping import escanear_modelo_pbi

        modelo_pbi = escanear_modelo_pbi(carpeta_semantic_model)

        if not modelo_pbi:
            error_msg = "❌ No se encontraron tablas en la carpeta del Power BI."
            logger.error(error_msg)
            return error_msg

        # Convertir a DataFrame
        tablas_pbi = []
        for tabla, cols in modelo_pbi.items():
            tablas_pbi.append({"Nombre_Tabla_PBI": tabla})
        df_pbi = pd.DataFrame(tablas_pbi)

        logger.info(f"Power BI: {len(df_pbi)} tablas detectadas")

        # 3. Normalizar nombres para cruzar (minúsculas)
        if "Nombre_Tabla_Redshift" in df_tableau.columns:
            df_tableau["Key"] = (
                df_tableau["Nombre_Tabla_Redshift"].astype(str).str.lower().str.strip()
            )
        else:
            error_msg = (
                "❌ El Excel de Tableau no tiene la columna 'Nombre_Tabla_Redshift'"
            )
            logger.error(error_msg)
            return error_msg

        df_pbi["Key"] = df_pbi["Nombre_Tabla_PBI"].astype(str).str.lower().str.strip()

        # 4. Generar Reporte (Left Join)
        df_merge = pd.merge(df_tableau, df_pbi, on="Key", how="left", indicator=True)
        df_merge["ESTADO"] = df_merge["_merge"].apply(
            lambda x: "✅ MIGRADA" if x == "both" else "⚠️ FALTA"
        )

        # Columnas finales
        cols_finales = ["ESTADO"]
        for col in [
            "Fuente_Datos_Tableau",
            "Nombre_Tabla_Redshift",
            "Nombre_Tabla_PBI",
            "Query_SQL_Completa",
        ]:
            if col in df_merge.columns:
                cols_finales.append(col)

        df_final = df_merge[cols_finales]

        output_file = config.PROJECT_ROOT / "Reporte_GAP_Tableau_vs_PBI.xlsx"
        df_final.to_excel(output_file, index=False)

        # Estadísticas
        estado_counts = df_final["ESTADO"].value_counts()
        logger.info(f"Reporte generado: {output_file.name}")
        logger.info(
            f"Migradas: {estado_counts.get('✅ MIGRADA', 0)}, Faltantes: {estado_counts.get('⚠️ FALTA', 0)}"
        )

        return (
            f"✅ REPORTE GENERADO: {output_file.name}\n"
            f"Migradas: {estado_counts.get('✅ MIGRADA', 0)}\n"
            f"Faltantes: {estado_counts.get('⚠️ FALTA', 0)}"
        )

    except Exception as e:
        error_msg = f"❌ Error: {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


def extraer_hojas_tableau(ruta_archivo_tableau: str) -> list[dict[str, any]]:
    """
    Extrae todas las hojas/vistas (worksheets) de un archivo Tableau con información detallada.
    FASE 2 y 3: Detecta tipos de visuales y mapea campos usados.

    Args:
        ruta_archivo_tableau: Ruta al archivo .twb o .twbx

    Returns:
        Lista de diccionarios con información detallada de cada hoja
    """
    import xml.etree.ElementTree as ET
    import zipfile

    logger.info(
        f"Extrayendo hojas/vistas detalladas de: {Path(ruta_archivo_tableau).name}"
    )

    try:
        tree = None

        # Abrir archivo (TWB o TWBX)
        if ruta_archivo_tableau.lower().endswith(".twbx"):
            with zipfile.ZipFile(ruta_archivo_tableau, "r") as z:
                archivos_twb = [f for f in z.namelist() if f.endswith(".twb")]
                if not archivos_twb:
                    logger.error("No hay .twb dentro del .twbx")
                    return []
                with z.open(archivos_twb[0]) as f:
                    tree = ET.parse(f)
        else:
            tree = ET.parse(ruta_archivo_tableau)

        root = tree.getroot()

        elementos_migrables = []

        # 0. Mapear Internal Name -> Caption
        mapa_datasources = {}
        for ds in root.findall(".//datasource"):
            internal_name = ds.get("name")
            caption = ds.get("caption") or internal_name
            if internal_name:
                mapa_datasources[internal_name] = caption

        # 1. Mapear todos los worksheets primero para acceso rápido
        mapa_worksheets = {}
        all_worksheets = root.findall(".//worksheet")
        for ws in all_worksheets:
            name = ws.get("name")
            if name:
                mapa_worksheets[name] = ws

        # 2. Extraer DASHBOARDS (Estos serán las PÁGINAS de Power BI)
        dashboards = root.findall(".//dashboard")

        worksheets_en_dashboards = set()

        for dashboard in dashboards:
            dash_name = dashboard.get("name") or "Dashboard Sin Nombre"

            # Buscar qué worksheets contiene este dashboard
            zonas = dashboard.findall(".//zone")
            hojas_del_dashboard = []

            for zona in zonas:
                # Las zonas que contienen worksheets tienen type='text' (a veces) o param='worksheet_name'
                # La forma más segura es buscar el atributo 'name' que coincida con un worksheet
                ws_name = zona.get("name")
                if ws_name and ws_name in mapa_worksheets:
                    if ws_name not in [
                        h["Nombre_Hoja"] for h in hojas_del_dashboard
                    ]:  # Evitar duplicados en el mismo dash
                        info_ws = _extraer_info_worksheet(
                            mapa_worksheets[ws_name], mapa_datasources
                        )
                        if info_ws:
                            hojas_del_dashboard.append(info_ws)
                            worksheets_en_dashboards.add(ws_name)

            if hojas_del_dashboard:
                elementos_migrables.append(
                    {
                        "Tipo": "Dashboard",
                        "Nombre": dash_name,
                        "Hojas": hojas_del_dashboard,
                    }
                )

        # 3. Extraer WORKSHEETS SUELTOS (Que no están en ningún dashboard)
        # Se tratarán como páginas individuales
        for name, ws_xml in mapa_worksheets.items():
            if name not in worksheets_en_dashboards:
                # Filtros de exclusión (ocultas, tooltips, etc)
                if ws_xml.get("hidden") == "true":
                    continue
                if name.startswith(("tooltip", "Tooltip", "__", "TOOLTIP")):
                    continue

                info_ws = _extraer_info_worksheet(ws_xml, mapa_datasources)
                if info_ws:
                    elementos_migrables.append(
                        {
                            "Tipo": "Hoja",
                            "Nombre": name,
                            "Hojas": [
                                info_ws
                            ],  # Lista de 1 elemento para mantener estructura uniforme
                        }
                    )

        logger.info(
            f"Encontrados {len(elementos_migrables)} elementos migrables (Dashboards + Hojas sueltas)"
        )
        return elementos_migrables

    except Exception as e:
        logger.error(f"Error extrayendo hojas: {e}", exc_info=True)
        return []


def _extraer_info_worksheet(
    worksheet, mapa_datasources: dict[str, str] = None
) -> dict | None:
    """Extrae info detallada de un worksheet XML"""
    nombre = worksheet.get("name") or worksheet.get("caption") or "Sin nombre"

    # 3. Hojas vacías o sin contenido visual
    tiene_contenido = (
        worksheet.find(".//mark") is not None
        or worksheet.find(".//visualization") is not None
    )
    if not tiene_contenido:
        return None

    # =========================================================================
    # MEJORA 1: DETECCIÓN DE TIPO DE VISUAL (Inspirado en VisualMapper)
    # =========================================================================
    mark_type = "unknown"

    # Estrategia A: <style><graph type='...'> (Más directo y fiable para tipo de gráfico)
    graph_style = worksheet.find(".//style/graph")
    if graph_style is not None:
        mark_type = graph_style.get("type")
        logger.debug(f"Mark type from style/graph: {mark_type}")

    # Estrategia B: style-rule (Legacy)
    if mark_type == "unknown":
        style_rules = worksheet.findall(
            './/style/style-rule[@element="mark"]/format[@attr="mark-type"]'
        )
        if style_rules:
            mark_type = style_rules[0].get("value", "unknown")

    # Estrategia C: Inferir de pane (Legacy)
    if mark_type in ["unknown", "automatic"]:
        pane_marks = worksheet.findall(".//panes/pane/mark")
        if pane_marks:
            val = pane_marks[0].get("class")
            if val and val != "automatic":
                mark_type = val.lower()

    # =========================================================================
    # MEJORA 2: EXTRACCIÓN DE CAMPOS Y ROLES
    # =========================================================================
    campos_filas = []
    campos_columnas = []
    campos_medidas = []

    # 2.1 Identificar fuente de datos PRINCIPAL
    datasource_name = "Desconocido"
    ds_dep_principal = worksheet.find(".//datasource-dependencies")
    if ds_dep_principal is not None:
        # Nombre interno del datasource
        internal_name = ds_dep_principal.get("datasource", "")
        if mapa_datasources and internal_name in mapa_datasources:
            datasource_name = mapa_datasources[internal_name]
        else:
            datasource_name = internal_name

    # 2.2 Extraer TODOS los campos usados, categorizándolos por ROI (Role of Interest)
    # No solo por donde están colocados (rows/cols), sino por su naturaleza (measure/dimension)
    for dep in worksheet.findall(".//datasource-dependencies"):
        for col_ref in dep.findall(".//column"):
            col_name = col_ref.get("name", "")
            col_role = col_ref.get("role", "")
            col_type = col_ref.get("datatype", "")

            if not col_name:
                continue

            # Limpiar nombre
            col_name_clean = col_name.replace("[", "").replace("]", "")

            # Clasificación robusta
            is_measure = col_role == "measure" or col_type in ["real", "integer"]
            if col_type == "string":
                is_measure = False  # Strings nunca son medidas numéricas directas

            # Evitar duplicados
            if is_measure:
                if col_name_clean not in campos_medidas:
                    campos_medidas.append(col_name_clean)
            else:
                # Dimensiones: se asignarán a filas/columnas más adelante
                if (
                    col_name_clean not in campos_filas
                    and col_name_clean not in campos_columnas
                ):
                    campos_filas.append(col_name_clean)

    # 2.3 Leer los shelves <rows> y <cols> explícitos del worksheet
    # Esto determina qué campos van en Filas vs Columnas del visual
    rows_elem = worksheet.find(".//rows")
    cols_elem = worksheet.find(".//cols")

    # Detectar si hay contenido real en rows/cols
    has_rows_content = (
        rows_elem is not None and rows_elem.text and rows_elem.text.strip()
    )
    has_cols_content = (
        cols_elem is not None and cols_elem.text and cols_elem.text.strip()
    )

    # Log para debug
    logger.debug(
        f"Worksheet {nombre}: rows={has_rows_content}, cols={has_cols_content}"
    )

    campos_en_rows = []
    campos_en_cols = []

    def extraer_campos_de_shelf(shelf_text):
        """Extrae nombres de campos del formato [datasource].[campo:tipo]"""
        campos = []
        if not shelf_text:
            return campos
        # Formato típico: [federated.abc123].[none:campo_nombre:nk]
        import re

        # Buscar patrones como [xxx].[none:nombre_campo:xxx] o [xxx].[nombre_campo]
        matches = re.findall(r"\[(?:[^\]]*)\]\.\[(?:none:)?([^:\]]+)", shelf_text)
        for m in matches:
            if m and not m.startswith("usr:"):  # Ignorar campos calculados complejos
                campos.append(m)
        return campos

    if has_rows_content:
        campos_en_rows = extraer_campos_de_shelf(rows_elem.text)
        logger.debug(f"  campos_en_rows: {campos_en_rows}")

    if has_cols_content:
        campos_en_cols = extraer_campos_de_shelf(cols_elem.text)
        logger.debug(f"  campos_en_cols: {campos_en_cols}")

    # Reasignar campos_filas y campos_columnas basándose en los shelves
    if campos_en_rows or campos_en_cols:
        # Usar los campos extraídos directamente de los shelves
        if campos_en_rows:
            campos_filas = campos_en_rows
        if campos_en_cols:
            campos_columnas = campos_en_cols

    # Normalizar mark_type
    mark_type_normalized = mark_type.lower()

    # =========================================================================
    # LÓGICA DE DETECCIÓN DE TIPO VISUAL (Basada en investigación)
    # =========================================================================
    # Cuando mark_class es 'Automatic', Tableau decide basándose en:
    # - rows + cols + medida → Text Table (Matrix)
    # - solo rows + medida → Bar Chart (generalmente)
    # - Pero en este workbook, TODO parece ser tablas de texto/matrices
    # =========================================================================

    if mark_type_normalized in ["automatic", "auto", "unknown"]:
        # REGLA PRINCIPAL: Si hay contenido en <cols>, ES UNA MATRIZ
        if has_cols_content:
            mark_type_normalized = "text"  # Matrix/Pivot
            logger.debug("  -> Tipo detectado: MATRIX (cols tiene contenido)")
        elif has_rows_content:
            # Solo rows = también es tabla de texto en la mayoría de casos
            mark_type_normalized = "text"  # Text Table
            logger.debug("  -> Tipo detectado: TEXT TABLE (solo rows)")
        elif campos_medidas:
            # Sin rows/cols pero con medidas = podría ser KPI o card
            mark_type_normalized = "text"
        else:
            mark_type_normalized = "text"  # Default seguro

    return {
        "Nombre_Hoja": nombre,
        "Tipo_Marca": mark_type,
        "Tipo_Visual": mark_type_normalized,
        "Fuente_Datos": datasource_name,
        "Campos_Filas": campos_filas,  # Dimensiones para Rows
        "Campos_Columnas": campos_columnas,  # Dimensiones para Columns
        "Campos_Medidas": campos_medidas,  # Valores numéricos
        "Filtros": _extraer_filtros_hoja_static(worksheet),
    }


def _extraer_filtros_hoja_static(worksheet_xml) -> list[dict]:
    """Helper para extraer filtros de una hoja (xml element)"""
    filtros = []
    try:
        for filter_elem in worksheet_xml.findall(".//filter"):
            field = filter_elem.get("column")
            class_type = filter_elem.get("class")
            if field:
                filtros.append(
                    {
                        "Campo": field.replace("[", "").replace("]", ""),
                        "Tipo": class_type,
                        "Valores": [],  # Simplificado
                    }
                )
    except Exception:
        pass
    return filtros
