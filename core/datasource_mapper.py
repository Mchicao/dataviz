"""
Módulo para mapear datasources de Tableau a tablas de Power BI
Resuelve el problema de placeholders generando conexiones reales
Ported from V2 Logic.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)


def extraer_tabla_redshift_de_datasource(datasource: dict) -> str | None:
    """
    Extrae el nombre de la tabla de Redshift desde un datasource de Tableau.
    Soporta formato plano (analisis_logic) y formato anidado.
    """
    # Formato plano (Nombre_Tabla_Redshift)
    nombre = datasource.get("Nombre_Tabla_Redshift")

    if not nombre:
        # Formato anidado antiguo
        tablas = datasource.get("tablas", [])
        for tabla in tablas:
            if tabla.get("tipo") == "table":
                nombre = tabla.get("nombre", "")
                break

    if not nombre or "Extract" in str(nombre):
        return None

    # Limpiar formato: [reds_base].[tmp_sap_fi_ar_resumen] -> reds_base_tmp_sap_fi_ar_resumen
    # 1. Quitar corchetes
    limpio = str(nombre).replace("[", "").replace("]", "")

    # 2. Si tiene punto (esquema.tabla), reemplazar por guion bajo
    if "." in limpio:
        return limpio.replace(".", "_")

    return limpio


def leer_tablas_semantic_model(carpeta_semantic_model: str) -> dict[str, list[str]]:
    """
    Lee las tablas disponibles en el semantic model de Power BI.
    Soporta carpetas tanto en la raíz como en definition/tables.
    """
    tablas = {}

    # Buscar carpeta de tablas
    posibles_rutas = [
        os.path.join(carpeta_semantic_model, "definition", "tables"),
        os.path.join(carpeta_semantic_model, "tables"),
    ]

    carpeta_tablas = None
    for ruta in posibles_rutas:
        if os.path.exists(ruta):
            carpeta_tablas = ruta
            break

    if not carpeta_tablas:
        logger.warning(f"No se encontró carpeta de tablas en: {carpeta_semantic_model}")
        return tablas

    # Leer archivos .tmdl
    for archivo in os.listdir(carpeta_tablas):
        if not archivo.endswith(".tmdl"):
            continue

        ruta_archivo = os.path.join(carpeta_tablas, archivo)
        nombre_tabla = archivo.replace(".tmdl", "")

        try:
            with open(ruta_archivo, encoding="utf-8") as f:
                contenido = f.read()

            # TMDL quotes names that contain spaces.  The old expression
            # stopped at the first space (``Order`` instead of ``Order Date``).
            columnas = []
            matches = re.findall(
                r'^\s*(?:column|measure)\s+(?:\'([^\']+)\'|"([^"]+)"|([^\s=]+))',
                contenido,
                re.MULTILINE,
            )
            for match in matches:
                col_name = next((name for name in match if name), "").strip()
                if col_name:
                    columnas.append(col_name)

            tablas[nombre_tabla] = columnas
            logger.debug(f"Tabla '{nombre_tabla}' encontrada con {len(columnas)} columnas")

        except Exception as e:
            logger.error(f"Error leyendo {archivo}: {e}")

    return tablas


def leer_tipos_objetos_semantic_model(carpeta_semantic_model: str) -> dict[str, dict[str, str]]:
    """Devuelve ``tabla -> objeto -> column|measure`` desde los TMDL."""
    tipos: dict[str, dict[str, str]] = {}
    posibles_rutas = [
        os.path.join(carpeta_semantic_model, "definition", "tables"),
        os.path.join(carpeta_semantic_model, "tables"),
    ]
    carpeta_tablas = next((ruta for ruta in posibles_rutas if os.path.exists(ruta)), None)
    if not carpeta_tablas:
        return tipos

    for archivo in os.listdir(carpeta_tablas):
        if not archivo.endswith(".tmdl"):
            continue
        ruta_archivo = os.path.join(carpeta_tablas, archivo)
        with open(ruta_archivo, encoding="utf-8") as f:
            contenido = f.read()
        objetos: dict[str, str] = {}
        matches = re.findall(
            r'^\s*(column|measure)\s+(?:\'([^\']+)\'|"([^"]+)"|([^\s=]+))',
            contenido,
            re.MULTILINE,
        )
        for kind, quoted, double_quoted, plain in matches:
            nombre = quoted or double_quoted or plain
            if nombre:
                nombre = nombre.strip()
                object_kind = kind.lower()
                escaped = re.escape(nombre)
                if object_kind == "column" and re.search(
                    rf'^\s*column\s+(?:\'{escaped}\'|"{escaped}"|{escaped})\s*=',
                    contenido,
                    re.MULTILINE,
                ):
                    object_kind = "calculatedcolumn"
                objetos[nombre] = object_kind
        tipos[archivo.removesuffix(".tmdl")] = objetos
    return tipos


def mapear_datasource_a_tabla_pbi(
    nombre_interno_ds: str,
    datasource_info: dict,
    tablas_pbi: dict[str, list[str]],
    mapeo_manual: dict[str, str] | None = None,
) -> str | None:
    """
    Mapea un datasource de Tableau a una tabla de Power BI.
    """
    caption = datasource_info.get("Fuente_Datos_Tableau") or datasource_info.get(
        "nombre", ""
    )

    # 1. Intentar mapeo manual primero
    if mapeo_manual:
        # Buscar por nombre interno
        if nombre_interno_ds in mapeo_manual:
            return mapeo_manual[nombre_interno_ds]

        # Buscar por caption (Fuente_Datos_Tableau)
        if caption in mapeo_manual:
            return mapeo_manual[caption]

    # 2. Extraer nombre de tabla de Redshift
    tabla_redshift = extraer_tabla_redshift_de_datasource(datasource_info)

    if not tabla_redshift:
        return None

    # TOM-NAME-02: prioriza la entidad que TOM desambiguó con el caption.
    source_label = str(caption).replace("[", "").replace("]", "")
    if " (" in source_label:
        source_label = source_label.split(" (", 1)[0]
    if "." in source_label:
        source_label = source_label.rsplit(".", 1)[-1]
    source_suffix = re.sub(r"[^0-9A-Za-z_]+", "_", source_label).strip("_")
    disambiguated = f"{tabla_redshift}__{source_suffix or 'Datasource'}"
    for tabla_pbi in tablas_pbi:
        if tabla_pbi.casefold() == disambiguated.casefold():
            return tabla_pbi

    # 3. Buscar coincidencia exacta en tablas PBI
    if tabla_redshift in tablas_pbi:
        return tabla_redshift

    # 4. Buscar coincidencia parcial (case-insensitive)
    tabla_lower = tabla_redshift.lower()
    for tabla_pbi in tablas_pbi.keys():
        if tabla_pbi.lower() == tabla_lower:
            return tabla_pbi

    # 5. Buscar por similitud (contiene)
    for tabla_pbi in tablas_pbi.keys():
        if (
            tabla_redshift.lower() in tabla_pbi.lower()
            or tabla_pbi.lower() in tabla_redshift.lower()
        ):
            return tabla_pbi

    return None


def crear_mapa_completo_datasources(
    datasources_tableau: list[dict],
    carpeta_semantic_model: str,
    mapeo_manual: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """
    Crea el mapeo completo entre datasources de Tableau y tablas de PBI.
    """
    # Leer tablas y tipos de objeto del semantic model
    tablas_pbi = leer_tablas_semantic_model(carpeta_semantic_model)
    tipos_por_tabla = leer_tipos_objetos_semantic_model(carpeta_semantic_model)

    if not tablas_pbi:
        return {}, {}

    logger.info(f"Tablas encontradas en PBI: {len(tablas_pbi)}")

    # Crear mapeo de datasources
    mapa_fuentes = {}

    for ds in datasources_tableau:
        # Adaptar nombres de llaves segun el origen
        nombre_interno = ds.get("DataSource_Name_Internal") or ds.get("nombre_interno", "")
        caption = ds.get("Fuente_Datos_Tableau") or ds.get("nombre", "")

        if not nombre_interno or "Parameters" in str(caption):
            continue

        tabla_pbi = mapear_datasource_a_tabla_pbi(nombre_interno, ds, tablas_pbi, mapeo_manual)

        if tabla_pbi:
            mapa_fuentes[nombre_interno] = tabla_pbi
            # También mapear por caption para redundancia si es necesario
            if caption and caption != nombre_interno:
                mapa_fuentes[caption] = tabla_pbi

    # Crear mapeo de columnas (por ahora, asumimos nombres iguales)
    columnas_por_tabla = {}
    for tabla_pbi, columnas in tablas_pbi.items():
        # Mapeo 1:1 (nombre_tableau: nombre_pbi), más metadatos internos
        # reservados para que PBIR distinga Column de Measure.
        columnas_por_tabla[tabla_pbi] = {col: col for col in columnas}
        for nombre, kind in tipos_por_tabla.get(tabla_pbi, {}).items():
            columnas_por_tabla[tabla_pbi][f"__kind__:{nombre.lower()}"] = kind

    logger.info(f"✓ Mapeo completado: {len(mapa_fuentes)} datasources mapeados")

    return mapa_fuentes, columnas_por_tabla


# Mapeo manual hardcoded para casos conocidos
MAPEO_CONOCIDO = {
    # Nombre interno o caption de Tableau: Nombre tabla PBI
    "tmp_sap_fi_ar_resumen": "tmp_sap_fi_ar_resumen",
    "sap_fi_ar_contable": "sap_fi_ar_contable",
    "vw_ar_medico_v5_vm": "vw_ar_medico_v5_vm",
    # Agregar más según sea necesario
}
