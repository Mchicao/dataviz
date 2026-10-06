"""
Módulo para la generación de archivos TMDL (Tabular Model Definition Language).
Permite crear definiciones de tablas y medidas compatibles con Power BI Desktop.
"""

import logging
import uuid

from core import config_manager as config

logger = logging.getLogger(__name__)


def generar_uuid() -> str:
    """Genera un UUID para lineageTag."""
    return str(uuid.uuid4())


def crear_tabla_tmdl(
    nombre_tabla: str, columnas: list[str], conexion_info: dict
) -> str:
    """
    Genera el contenido de un archivo .tmdl para una tabla usando TABS.
    """
    nombre_safe = (
        f"'{nombre_tabla}'"
        if " " in nombre_tabla or (nombre_tabla and nombre_tabla[0].isdigit())
        else nombre_tabla
    )

    lineas = [f"table {nombre_safe}", f"\tlineageTag: {generar_uuid()}", ""]

    # Definición de columnas
    for col in columnas:
        lineas.append(f"\tcolumn {col}")
        dtype = "string"
        if any(
            keyword in col.lower()
            for keyword in ["monto", "total", "importe", "cantidad", "dias", "count"]
        ):
            dtype = "double"
        elif any(keyword in col.lower() for keyword in ["fecha", "date"]):
            dtype = "dateTime"

        lineas.append(f"\t\tdataType: {dtype}")
        lineas.append(f"\t\tlineageTag: {generar_uuid()}")
        lineas.append(f"\t\tsummarizeBy: {'sum' if dtype == 'double' else 'none'}")
        lineas.append(f"\t\tsourceColumn: {col}")
        lineas.append("")
        lineas.append("\t\tannotation SummarizationSetBy = Automatic")
        if dtype == "double":
            lineas.append('\t\tannotation PBI_FormatHint = {"isGeneralNumber":true}')
        lineas.append("")

    # Partición M
    server = conexion_info.get("Server") or "10.190.65.25"
    database = conexion_info.get("Database") or "rds_dwh_qa"
    schema = conexion_info.get("Schema") or "public"
    sql = conexion_info.get("Query_SQL_Completa")
    nombre_redshift = conexion_info.get("Nombre_Tabla_Redshift")

    lineas.append(f"\tpartition {nombre_safe} = m")
    lineas.append("\t\tmode: import")
    lineas.append("\t\tsource =")

    # Si estamos en modo desarrollo, añadimos un LIMIT para que el proyecto cargue rápido
    limit_clause = " LIMIT 1000" if config.DEV_MODE else ""

    if sql and sql != "CUSTOM SQL":
        # Caso 1: Custom SQL ya definido en Tableau
        # Limpiar punto y coma final si existe para que el LIMIT no falle
        sql_clean = sql.strip()
        if sql_clean.endswith(";"):
            sql_clean = sql_clean[:-1].strip()

        # Aplicamos limit si no tiene uno ya (muy básico el check)
        if config.DEV_MODE and "limit " not in sql_clean.lower():
            sql_clean += limit_clause

        sql_m = sql_clean.replace('"', '""').replace("\n", " #(lf) ").replace("\r", "")

        lineas.append(
            f'\t\t\tlet Origen = Value.NativeQuery(AmazonRedshift.Database("{server}","{database}"), "{sql_m}", null, [EnableFolding=true]) in Origen'
        )
    else:
        # Caso 2: Tabla estándar. Usamos NativeQuery "SELECT * FROM schema.table"
        # Priorizamos nombre_redshift exacto. nombre_tabla puede ser el alias interno de PBI.
        tabla_real = nombre_redshift

        # Si no hay nombre_redshift, intentamos limpiar nombre_tabla de parántesis si los tiene
        if not tabla_real:
            tabla_real = nombre_tabla.split(" ")[0]  # Quitamos (ciclo_ingresos...)

        # Aseguramos formato esquema.tabla
        if "." not in tabla_real and schema:
            tabla_full = f"{schema}.{tabla_real}"
        else:
            tabla_full = tabla_real

        sql_directo = f"SELECT * FROM {tabla_full}{limit_clause}"
        sql_m = sql_directo.replace('"', '""')

        lineas.append(
            f'\t\t\tlet Origen = Value.NativeQuery(AmazonRedshift.Database("{server}","{database}"), "{sql_m}", null, [EnableFolding=true]) in Origen'
        )

    lineas.append("")
    lineas.append("\tannotation PBI_ResultType = Table")

    return "\n".join(lineas)


def generar_medida_tmdl(
    nombre: str, formula: str, original_name: str = "", original_formula: str = ""
) -> str:
    """Genera bloque TMDL para una medida. Comentarios van ARRIBA para evitar errores de sangría."""
    lineas = []

    # Comentarios eliminados para debug de Power BI Nov 2025
    # if original_name:
    #     lineas.append(f"\t// Original Tableau Name: {original_name}")
    # if original_formula:
    #     formula_limpia = original_formula.replace("\n", " ").replace("\r", "")
    #     lineas.append(f"\t// Original Formula: {formula_limpia}")

    usar_bloque = "\n" in formula or len(formula) > 80

    if usar_bloque:
        lineas.append(f"\tmeasure '{nombre}' = ```")
        lineas.append(f"\t\t{formula.strip()}")
        lineas.append("\t\t```")
    else:
        lineas.append(f"\tmeasure '{nombre}' = {formula.strip()}")

    lineas.append(f"\t\tlineageTag: {generar_uuid()}")
    lineas.append("")

    return "\n".join(lineas)
