import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN GLOBAL (DEPRECATED: Usar detección dinámica) ---
DEFAULT_REDSHIFT_SERVER = "10.190.65.25"
DEFAULT_REDSHIFT_DB = "rds_dwh_qa"


def cargar_librerias_tom(dll_folder_path):
    """
    Carga las DLLs de Microsoft Analysis Services (TOM) desde la carpeta bin local.
    """
    abs_path = os.path.abspath(dll_folder_path)

    print(f"DEBUG: Loading TOM libraries from {abs_path}")
    if not os.path.exists(abs_path):
        print(f"Advertencia: No se encuentra la carpeta de DLLs en: {abs_path}")
        # raise FileNotFoundError(f"No se encuentra la carpeta de DLLs en: {abs_path}")

    if abs_path not in sys.path:
        sys.path.append(abs_path)
        print(f"DEBUG: Added {abs_path} to sys.path")

    # Configurar PYTHONNET_PYDLL si no está seteado, para evitar BadPythonDllException
    if "PYTHONNET_PYDLL" not in os.environ:
        print("DEBUG: PYTHONNET_PYDLL not set, attempting auto-discovery...")
        base_dir = getattr(sys, "base_prefix", sys.prefix)
        major = sys.version_info.major
        minor = sys.version_info.minor
        dll_name = f"python{major}{minor}.dll"

        dll_path = os.path.join(base_dir, dll_name)
        if not os.path.exists(dll_path):
            import glob

            dlls = glob.glob(os.path.join(base_dir, "python3*.dll"))
            if dlls:
                dll_path = dlls[0]

        if dll_path and os.path.exists(dll_path):
            os.environ["PYTHONNET_PYDLL"] = dll_path
            print(f"DEBUG: Set PYTHONNET_PYDLL to {dll_path}")

    try:
        import clr  # pythonnet

        print("DEBUG: clr (pythonnet) imported successfully")

        clr.AddReference("Microsoft.AnalysisServices.Tabular")
        clr.AddReference("Microsoft.AnalysisServices.Core")
        print("DEBUG: Microsoft.AnalysisServices references added")
        return True
    except ImportError:
        print("Advertencia: 'pythonnet' no instalado. Funcionalidad TOM deshabilitada.")
        return False
    except Exception as e:
        print(f"Advertencia: Error al cargar DLLs de Microsoft: {e}")
        return False


# Path relativo desde core/ a bin/
DEFAULT_DLL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin")

# Tableau ``remote-type`` codes observed in TWB metadata-records.  Keep this
# mapping independent from pythonnet so it can be tested without loading TOM.
TABLEAU_REMOTE_TYPE_NAMES = {
    "2": "Int64",
    "3": "Int64",
    "5": "Double",
    "7": "DateTime",
    "11": "Boolean",
    "20": "Int64",
    "129": "String",
    "130": "String",
    "131": "Double",
    "133": "DateTime",
}


def _collect_derived_date_columns(
    root: ET.Element,
) -> tuple[dict[str, str], dict[str, str]]:
    """Recopila derivaciones de fecha que parten de columnas físicas Tableau."""
    calculated_names = {
        column.get("name", "").strip("[]")
        for column in root.findall(".//column")
        if column.find("calculation") is not None
    }
    derived_year_columns: dict[str, str] = {}
    derived_month_columns: dict[str, str] = {}
    for instance in root.findall(".//column-instance"):
        derivation = instance.get("derivation", "").lower()
        base_column = instance.get("column", "").strip("[]")
        if not base_column or base_column in calculated_names:
            continue
        if derivation == "year":
            derived_year_columns[f"{base_column} Year"] = base_column
        elif derivation == "month":
            derived_month_columns[f"{base_column} Month"] = base_column
    return derived_year_columns, derived_month_columns


def _filter_derived_date_columns(
    derived_columns: dict[str, str], physical_columns: set[str]
) -> dict[str, str]:
    """DATE-DERIVE-02: Limita derivados a columnas físicas de la fuente."""
    return {
        derived_name: base_column
        for derived_name, base_column in derived_columns.items()
        if base_column in physical_columns
    }


def tableau_remote_type_name(remote_type: str | None) -> str:
    """Return the TOM ``DataType`` member for a Tableau remote-type code."""
    return TABLEAU_REMOTE_TYPE_NAMES.get(str(remote_type or ""), "String")


def calculation_is_row_level(calculation) -> bool:
    """Indica si un cálculo Tableau debe materializarse como columna DAX.

    Todo cálculo sin agregación, LOD, ventana ni parámetro se evalúa por fila,
    incluso cuando devuelve números o booleanos. Clasificarlo como medida
    cambia el contexto y puede inflar resultados al agregarlo.
    """
    formula = str(getattr(calculation, "formula", "")).upper()
    has_aggregation = bool(
        re.search(r"\b(SUM|AVG|COUNT|COUNTD|MIN|MAX|MEDIAN)\s*\(", formula)
    )
    has_window_or_lod = bool(
        re.search(
            r"\{\s*(FIXED|INCLUDE|EXCLUDE)\b|"
            r"\b(RUNNING_|WINDOW_|LOOKUP|INDEX|FIRST|LAST)\w*\s*\(",
            formula,
        )
    )
    has_parameter = "[PARAMETERS]." in formula
    return not has_aggregation and not has_window_or_lod and not has_parameter


def target_compatibility_level(legacy_mode: bool) -> int:
    """Devuelve el nivel tabular compatible con el destino de la migración."""
    return 1567 if legacy_mode else 1600


def _geographic_data_category(col_name: str) -> str | None:
    """Infiere la categoría de datos geográfica de Power BI por nombre de columna.

    Heurística genérica (no por demo): reconoce nombres comunes de campos
    geográficos en español e inglés para que los visuales de mapa puedan
    geocodificar correctamente. Devuelve None si no hay coincidencia.
    """
    n = col_name.lower().strip()
    # Tableau genera campos lat/long sintéticos con sufijo "(generated)".
    n = n.replace(" (generated)", "")
    # Mapeos nombre normalizado -> DataCategory de Power BI (valores PascalCase
    # válidos en TMDL, sin espacios ni barras).
    if n in {"country", "country/region", "pais", "país", "nacion", "nación"}:
        return "Country"
    if n in {"state", "estado", "province", "provincia", "region", "región"}:
        return "StateOrProvince"
    if n in {"city", "ciudad", "municipio", "town"}:
        return "City"
    if n in {"postal code", "zip", "zip code", "código postal", "codigo postal"}:
        return "PostalCode"
    if n in {"latitude", "latitud", "lat"}:
        return "Latitude"
    if n in {"longitude", "longitud", "long", "lng"}:
        return "Longitude"
    if n in {"continent", "continente"}:
        return "Continent"
    return None


def get_m_connector(connection_class: str) -> str:
    """Retorna la función M correspondiente al tipo de conexión."""
    mapping = {
        "redshift": "AmazonRedshift.Database",
        "postgres": "PostgreSQL.Database",
        "sqlserver": "Sql.Database",
        "mysql": "MySQL.Database",
        "oracle": "Oracle.Database",
        "snowflake": "Snowflake.Databases",
    }
    return mapping.get(connection_class, "Sql.Database")  # Default to SQL Server


def generate_m_expression(
    connection_class: str,
    server: str,
    db: str,
    schema: str,
    table: str,
    custom_sql: str | None = None,
) -> str:
    """Genera la expresión M (Power Query) para la partición."""

    server = server or "SERVER_PLACEHOLDER"
    db = db or "DB_PLACEHOLDER"
    connector_func = get_m_connector(connection_class)

    if custom_sql:
        # Limpieza básica de SQL
        sql_clean = (
            custom_sql.replace('"', '""').replace("\r\n", " #(lf) ").replace("\n", " #(lf) ")
        )

        # Redshift/Postgres soportan EnableFolding=true en algunos drivers
        options = ", [EnableFolding=true]" if connection_class in ["redshift", "postgres"] else ""

        # Usar Table.FirstN para imponer LIMIT 1000 de forma agnóstica al dialecto SQL
        return f'let Origen = Value.NativeQuery({connector_func}("{server}","{db}"), "{sql_clean}", null{options}), Limited = Table.FirstN(Origen, 1000) in Limited'

    else:
        # Navegación estándar
        if connection_class == "redshift" or connection_class == "postgres":
            return f'''let
    Source = {connector_func}("{server}","{db}"),
    Schema = Source{{[Name="{schema}"]}}[Data],
    Table = Schema{{[Name="{table}"]}}[Data],
    Limited = Table.FirstN(Table, 1000)
 in
    Limited'''
        else:
            # SQL Server / Genérico
            return f'''let
    Source = {connector_func}("{server}","{db}"),
    Table = Source{{[Schema="{schema}", Item="{table}"]}}[Data],
    Limited = Table.FirstN(Table, 1000)
 in
    Limited'''


def _resolve_local_data_file(
    workbook_path: str | Path, declared_filename: str
) -> Path | None:
    """Resuelve datos locales de Tableau sin depender de rutas del autor.

    DATA-SOURCE-01: limita la búsqueda al directorio del libro y su padre, y sólo
    acepta una coincidencia normalizada inequívoca.
    """
    if not declared_filename:
        return None

    workbook_dir = Path(workbook_path).resolve().parent
    portable_name = declared_filename.replace("\\", "/").rsplit("/", 1)[-1]
    declared_path = Path(declared_filename).expanduser()
    exact_candidates = [
        declared_path if declared_path.is_absolute() else None,
        workbook_dir / declared_filename,
        workbook_dir / portable_name,
    ]
    for candidate in exact_candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()

    target_suffix = Path(portable_name).suffix.casefold()
    target_key = re.sub(r"[^a-z0-9]+", "", portable_name.casefold())
    matches: set[Path] = set()
    for directory in (workbook_dir, workbook_dir.parent):
        try:
            entries = directory.iterdir()
        except OSError:
            continue
        for entry in entries:
            if (
                entry.is_file()
                and entry.suffix.casefold() == target_suffix
                and re.sub(r"[^a-z0-9]+", "", entry.name.casefold()) == target_key
            ):
                matches.add(entry.resolve())

    if len(matches) > 1:
        candidates = ", ".join(sorted(str(path) for path in matches))
        raise FileNotFoundError(
            "La referencia de datos de Tableau es ambigua; "
            f"coinciden varios archivos: {candidates}"
        )
    return next(iter(matches), None)


def _reserve_unique_table_name(
    base_name: str, datasource_name: str, used_names: set[str]
) -> str:
    """Reserva un nombre TOM estable y legible, incluso entre datasources."""
    base = base_name.strip() or "Table"
    existing = {name.casefold() for name in used_names}
    if base.casefold() not in existing:
        used_names.add(base)
        return base

    # TOM-NAME-01: conserva el nombre físico y desambigua con la fuente lógica.
    suffix = re.sub(r"[^0-9A-Za-z_]+", "_", datasource_name).strip("_")
    suffix = suffix or "Datasource"
    candidate = f"{base}__{suffix}"
    sequence = 2
    while candidate.casefold() in existing:
        candidate = f"{base}__{suffix}_{sequence}"
        sequence += 1
    used_names.add(candidate)
    return candidate


def _register_datasource_table_aliases(
    mapping: dict[str, object], table: object, *datasource_names: str | None
) -> None:
    """Registra nombres internos y captions Tableau contra una tabla TOM."""
    # DS-ALIAS-01: los nodos duplicados se omiten, pero sus cálculos aún los referencian.
    for datasource_name in datasource_names:
        if datasource_name:
            mapping[datasource_name.strip("[]")] = table


def ejecutar_migracion_tom(
    ruta_xml_tableau, carpeta_salida_modelo, dll_path=DEFAULT_DLL_PATH, legacy_mode=False
):
    """
    Motor principal: Lee XML de Tableau -> Crea objetos TOM -> Guarda TMDL o BIM (Legacy).
    """
    print(f"DEBUG: ejecutar_migracion_tom called using bin at {dll_path}")
    print(f"DEBUG: Output Dir: {carpeta_salida_modelo}")
    print(f"DEBUG: Legacy Mode: {legacy_mode}")

    # 1. Cargar dependencias .NET
    tom_loaded = cargar_librerias_tom(dll_path)
    if not tom_loaded:
        # Fallback o error controlado
        print("TOM no cargado. Abortando generación de modelo semántico.")
        return 0

    # Importar clases de Microsoft (dentro de try/catch implícito por la carga anterior)
    try:
        from Microsoft.AnalysisServices.Tabular import (
            CalculatedColumn,
            Database,
            DataColumn,
            DataType,
            JsonSerializer,
            Measure,
            Model,
            ModeType,
            MPartitionSource,
            Partition,
            PowerBIDataSourceVersion,
            Table,
            TmdlSerializer,
        )
    except ImportError:
        return 0

    # Funciones auxiliares internas
    def parse_schema_table(table_attr):
        if not table_attr:
            return "public", "Unknown"
        # [schema].[table]
        match = re.search(r"\[(.*?)\].\[(.*?)\]", table_attr)
        if match:
            return match.group(1), match.group(2)
        # schema.table
        if "." in table_attr and "[" not in table_attr:
            parts = table_attr.split(".")
            return parts[0], parts[1]
        return "public", table_attr.replace("[", "").replace("]", "")

    # Mapeo de tipos Tableau ``remote-type`` -> TOM DataType.
    TYPE_MAP = {
        code: getattr(DataType, member)
        for code, member in TABLEAU_REMOTE_TYPE_NAMES.items()
    }

    # --- INICIO LÓGICA DE NEGOCIO ---
    if not os.path.exists(ruta_xml_tableau):
        raise FileNotFoundError(f"No existe el archivo de entrada: {ruta_xml_tableau}")

    # 1. Crear Base de Datos Virtual TOM
    database = Database()
    database.Name = "SemanticModel"
    database.ID = "SemanticModel"
    # Desktop normal conserva compatibilidad 1600 con su caché local. PBIRS
    # requiere el nivel legado 1567 y se solicita explícitamente por modo.
    database.CompatibilityLevel = target_compatibility_level(legacy_mode)

    model = Model()
    model.Name = "Model"
    model.Culture = "es-CL"
    # Set Power BI V3 data source version for Power BI Desktop compatibility
    model.DefaultPowerBIDataSourceVersion = PowerBIDataSourceVersion.PowerBI_V3
    database.Model = model
    logger.info("TOM inicializado; leyendo workbook %s", ruta_xml_tableau)

    # 2. Leer XML
    try:
        tree = ET.parse(ruta_xml_tableau)
        root = tree.getroot()
        logger.info("Workbook XML leído; iniciando inventario de datasources")
    except Exception as e:
        raise ValueError(f"Error parseando XML: {e}")

    # Helper to simplify table names
    def simplify_table_name(raw: str) -> str:
        """Extract clean table name from verbose Tableau datasource name."""
        if not raw:
            return ""
        # Remove brackets
        clean = raw.replace("[", "").replace("]", "")
        # If format is "table (schemaTable) (schema)" - extract first part
        if " (" in clean:
            clean = clean.split(" (")[0]
        # If format is schema.table, take table part
        if "." in clean:
            parts = clean.split(".")
            clean = parts[-1]  # Take last part (table name)
        return clean.strip()

    datasources = root.findall(".//datasource")
    derived_year_columns, derived_month_columns = _collect_derived_date_columns(root)
    tablas_procesadas = set()
    table_entity_names: set[str] = set()
    tables_by_datasource: dict[str, object] = {}
    tables_by_logical_name: dict[str, object] = {}
    tablas_creadas_count = 0

    for ds in datasources:
        raw_name = ds.get("caption", ds.get("name"))
        if not raw_name:
            continue

        table_name = simplify_table_name(raw_name)
        if not table_name or "Parameters" in table_name or "Extract" in table_name:
            continue

        if table_name in tablas_procesadas:
            existing_table = tables_by_logical_name.get(table_name.casefold())
            if existing_table is not None:
                _register_datasource_table_aliases(
                    tables_by_datasource,
                    existing_table,
                    ds.get("name"),
                    ds.get("caption"),
                )
            continue
        tablas_procesadas.add(table_name)
        logger.info("Materializando datasource Tableau: %s", table_name)

        # Detectar conexión
        connection_elem = ds.find(".//connection")
        hyper_connection = next(
            (
                connection
                for connection in ds.findall(".//connection")
                if connection.get("class") == "hyper" and connection.get("dbname")
            ),
            None,
        )
        excel_connection = next(
            (
                connection
                for connection in ds.findall(".//connection")
                if connection.get("class") == "excel-direct"
                and connection.get("filename")
            ),
            None,
        )
        server = DEFAULT_REDSHIFT_SERVER
        db_name = DEFAULT_REDSHIFT_DB
        conn_class = "redshift"  # Default

        if connection_elem is not None:
            conn_class = connection_elem.get("class", "redshift")
            server = connection_elem.get("server", server)
            db_name = connection_elem.get("dbname", db_name)

        # Determinar Schema y Nombre Real (antes de crear objeto TOM)
        # Federated Tableau datasources start with a ``collection`` relation.
        # Use its first concrete, non-extract table instead of serialising the
        # empty wrapper as the Power BI table ``Unknown``.
        relation = next(
            (
                item
                for item in ds.findall(".//relation")
                if item.get("type") in {"table", "text"}
                and "[Extract]." not in (item.get("table") or "")
            ),
            ds.find(".//connection/relation"),
        )

        schema_real = "public"
        table_real = table_name

        if relation is not None:
            if relation.get("type") == "text":
                # Custom SQL no tiene schema directo fácil, usamos nombre simplificado
                pass
            else:
                # Tabla estándar: extraer schema real
                s, t = parse_schema_table(relation.get("table", ""))
                if s and s != "Extract":
                    schema_real = s
                if t:
                    table_real = t.rstrip("$")

        # Construir nombre de Tabla TMDL (Entity Name)
        # Si tenemos schema real, lo prependeamos: schema_table
        if schema_real != "public":
            full_table_name = f"{schema_real}_{table_real}"
        else:
            full_table_name = table_real

        # Limpieza final de nombre tabla
        full_table_name = full_table_name.replace(".", "_").replace(" ", "_")
        full_table_name = _reserve_unique_table_name(
            full_table_name, table_name, table_entity_names
        )

        # Crear Objeto Tabla TOM
        t_obj = Table()
        t_obj.Name = full_table_name
        logger.info("Tabla TOM creada: %s", full_table_name)
        tables_by_logical_name[table_name.casefold()] = t_obj
        _register_datasource_table_aliases(
            tables_by_datasource,
            t_obj,
            ds.get("name"),
            ds.get("caption"),
        )

        # Crear Partición
        part = Partition()
        logger.info("Objeto Partition creado: %s", full_table_name)
        part.Name = full_table_name # Use full name for partition too
        logger.info("Nombre Partition asignado: %s", full_table_name)
        part.Mode = ModeType.Import  # CRITICAL: Required for PBIRS compatibility
        logger.info("Modo Partition asignado: %s", full_table_name)
        part.Source = MPartitionSource()
        logger.info("MPartitionSource asignado: %s", full_table_name)

        # Determinar Source Expression
        # (relation variable ya fue obtenida arriba)

        hyper_path = None
        if hyper_connection is not None:
            candidate = Path(ruta_xml_tableau).resolve().parent / hyper_connection.get(
                "dbname", ""
            )
            if candidate.exists():
                hyper_path = candidate

        hyper_columns: set[str] | None = None
        if hyper_path is not None:
            from core.hyper_extract import export_hyper_table, generate_csv_m_expression

            data_folder = Path(carpeta_salida_modelo) / "Data"
            csv_path = data_folder / f"{full_table_name}.csv"
            hyper_export = export_hyper_table(
                hyper_path, csv_path, preferred_table=table_real
            )
            hyper_columns = set(hyper_export.column_types)
            part.Source.Expression = generate_csv_m_expression(
                hyper_export.path,
                hyper_export.column_types,
                derived_year_columns=_filter_derived_date_columns(
                    derived_year_columns, hyper_columns
                ),
                derived_month_columns=_filter_derived_date_columns(
                    derived_month_columns, hyper_columns
                ),
            )
        elif excel_connection is not None:
            from core.hyper_extract import export_excel_table, generate_csv_m_expression

            filename = excel_connection.get("filename", "")
            excel_path = _resolve_local_data_file(ruta_xml_tableau, filename)
            if excel_path is None:
                raise FileNotFoundError(
                    "No se encontró el Excel empaquetado de Tableau: "
                    f"{filename or '<referencia vacía>'}"
                )
            data_folder = Path(carpeta_salida_modelo) / "Data"
            csv_path = data_folder / f"{full_table_name}.csv"
            excel_export = export_excel_table(
                excel_path, csv_path, preferred_sheet=table_real
            )
            hyper_columns = set(excel_export.column_types)
            part.Source.Expression = generate_csv_m_expression(
                excel_export.path,
                excel_export.column_types,
                derived_year_columns=_filter_derived_date_columns(
                    derived_year_columns, hyper_columns
                ),
                derived_month_columns=_filter_derived_date_columns(
                    derived_month_columns, hyper_columns
                ),
            )
        elif relation is not None and relation.get("type") == "text":
            # SQL Personalizado
            part.Source.Expression = generate_m_expression(
                conn_class, server, db_name, "", "", custom_sql=relation.text
            )
        else:
            # Tabla Estándar - Usamos variables calculadas arriba
            if schema_real == "Extract":
                 # Fallback por si acaso, aunque ya filtramos arriba
                 schema_real = "public"

            part.Source.Expression = generate_m_expression(
                conn_class, server, db_name, schema_real, table_real
            )

        logger.info("Partición M preparada: %s", full_table_name)
        t_obj.Partitions.Add(part)
        logger.info("Partición agregada a tabla: %s", full_table_name)

        # Procesar Columnas
        metadata_records = ds.findall(".//metadata-record")
        has_cols = False
        existing_cols = set()

        for meta in metadata_records:
            if meta.get("class") == "column":
                remote_name = meta.find("remote-name")
                remote_type = meta.find("remote-type")
                parent_name = meta.find("parent-name")

                if remote_name is not None and parent_name is not None:
                    if parent_name.text != "[Extract]":
                        col_name_raw = remote_name.text
                        if hyper_columns is not None and col_name_raw not in hyper_columns:
                            continue
                        col_name = (
                            col_name_raw.replace(".", " ").strip() if col_name_raw else "Unknown"
                        )
                        if col_name in existing_cols:
                            continue
                        existing_cols.add(col_name)

                        r_type_val = remote_type.text if remote_type is not None else "130"
                        tom_type = TYPE_MAP.get(r_type_val, DataType.String)

                        c_obj = DataColumn()
                        c_obj.Name = col_name
                        c_obj.DataType = tom_type
                        c_obj.SourceColumn = col_name_raw
                        if tableau_remote_type_name(r_type_val) in {"Int64", "Double"}:
                            c_obj.FormatString = "#,0.##"
                        # Categoría de datos geográfica para que Power BI pueda
                        # geocodificar visuales de mapa (burujas/regiones).
                        cat = _geographic_data_category(col_name)
                        if cat is not None:
                            c_obj.DataCategory = cat
                        t_obj.Columns.Add(c_obj)
                        has_cols = True
                        if len(existing_cols) % 20 == 0:
                            logger.info(
                                "Columnas TOM agregadas: %s count=%d",
                                full_table_name,
                                len(existing_cols),
                            )

        for derived_name, base_column in derived_year_columns.items():
            if hyper_columns is None or base_column not in hyper_columns:
                continue
            if derived_name in existing_cols:
                continue
            c_obj = DataColumn()
            c_obj.Name = derived_name
            c_obj.DataType = DataType.Int64
            c_obj.SourceColumn = derived_name
            t_obj.Columns.Add(c_obj)
            existing_cols.add(derived_name)
            has_cols = True

        for derived_name, base_column in derived_month_columns.items():
            if hyper_columns is None or base_column not in hyper_columns:
                continue
            if derived_name in existing_cols:
                continue
            c_obj = DataColumn()
            c_obj.Name = derived_name
            c_obj.DataType = DataType.DateTime
            c_obj.SourceColumn = derived_name
            t_obj.Columns.Add(c_obj)
            existing_cols.add(derived_name)
            has_cols = True

        if has_cols:
            logger.info("Agregando tabla al modelo: %s", full_table_name)
            model.Tables.Add(t_obj)
            tablas_creadas_count += 1
            logger.info(
                "Datasource TOM listo: %s (%d columnas)",
                full_table_name,
                len(existing_cols),
            )

    # 3. Materializar cálculos, parámetros y campos worksheet-local como medidas.
    try:
        from core.calculation_extractor import (
            CalculationType,
            extract_calculations,
        )
        from core.dax_converter import (
            ConversionDiagnostic,
            convert_tableau_to_dax_diagnostic,
            diagnose_filter,
        )
        from core.python_measure_converter import (
            PythonMeasureSpec,
            write_python_measure_module,
        )

        extracted = extract_calculations(ruta_xml_tableau)

        primary_table = next(
            (t for t in model.Tables if t.Name and t.Name != "Parameters"), None
        )
        if primary_table is None:
            raise RuntimeError("No hay tabla principal para alojar medidas")

        table_name = primary_table.Name
        safe_table = table_name.replace("'", "''")

        # Recolectar nombres de columnas existentes en la tabla principal.
        existing_columns = set()
        for col in primary_table.Columns:
            existing_columns.add(col.Name)

        # Nombres de medidas que se materializarán (para resolver refs cruzadas).
        measure_names: set[str] = {
            calc.name
            for calc in extracted.calculated_fields
            if calc.calc_type != CalculationType.BIN and not calculation_is_row_level(calc)
        }
        measure_names.update(param.name for param in extracted.parameters)

        # Tableau puede declarar el mismo cálculo en el datasource global y
        # otra vez en una dependencia local de hoja. Power BI no admite una
        # medida y una columna con idéntico nombre en la misma tabla. Si una
        # de las declaraciones es una dimensión fila-a-fila, ésa prevalece.
        calculated_column_keys = {
            (
                tables_by_datasource.get(
                    str(calc.source_datasource).strip("[]"), primary_table
                ).Name,
                calc.name,
            )
            for calc in extracted.calculated_fields
            if calculation_is_row_level(calc)
        }
        calculated_column_names = {
            calc.name
            for calc in extracted.calculated_fields
            if calculation_is_row_level(calc)
        }

        # Diagnósticos de conversión (cálculos, LOD, parámetros, filtros).
        conversion_diagnostics: list[ConversionDiagnostic] = []
        python_measure_specs: list[PythonMeasureSpec] = []

        # --- 3a. Cálculos globales del workbook ---
        for calc in sorted(
            extracted.calculated_fields,
            key=lambda calculation: not calculation_is_row_level(calculation),
        ):
            if calc.calc_type == CalculationType.BIN:
                continue

            calculation_table = tables_by_datasource.get(
                str(calc.source_datasource).strip("[]"), primary_table
            )
            calculation_table_name = calculation_table.Name
            calculation_safe_table = calculation_table_name.replace("'", "''")
            calculation_columns = {column.Name for column in calculation_table.Columns}
            calculation_measures = {measure.Name for measure in calculation_table.Measures}

            if (
                not calculation_is_row_level(calc)
                and (
                    (calculation_table_name, calc.name) in calculated_column_keys
                    or calc.name in calculated_column_names
                )
            ):
                logger.info(
                    "Se omite medida duplicada de cálculo Tableau; prevalece columna: %s",
                    calc.name,
                )
                continue
            if calc.name in calculation_columns or calc.name in calculation_measures:
                logger.info(
                    "Se omite cálculo Tableau duplicado en tabla %s: %s",
                    calculation_table_name,
                    calc.name,
                )
                continue

            diag = convert_tableau_to_dax_diagnostic(
                calc.formula,
                table_name=calculation_safe_table,
                known_measures=measure_names,
                known_columns=calculation_columns,
            )
            conversion_diagnostics.append(diag)

            if calculation_is_row_level(calc) and diag.dax:
                calculated_column = CalculatedColumn()
                calculated_column.Name = calc.name
                calculated_column.Expression = diag.dax
                calculated_column.DataType = (
                    DataType.String
                    if calc.datatype in {"string", "text"}
                    else DataType.DateTime
                    if calc.datatype in {"date", "datetime"}
                    else DataType.Double
                )
                if calc.datatype not in {"string", "text", "date", "datetime"}:
                    calculated_column.FormatString = "#,0.##"
                calculation_table.Columns.Add(calculated_column)
                calculation_columns.add(calc.name)
                continue

            measure = Measure()
            measure.Name = calc.name
            measure_names.add(calc.name)
            measure.Expression = diag.dax or "BLANK()"
            if diag.status != "supported":
                measure.Description = (
                    f"[{diag.status}] {diag.reason}. Fórmula Tableau: {calc.formula}"
                )
            number_format = next(
                (
                    format_elem.get("value", "")
                    for format_elem in root.findall(".//format[@attr='text-format']")
                    if calc.name in format_elem.get("field", "")
                ),
                "",
            )
            if number_format:
                measure.FormatString = (
                    number_format[1:] if number_format.startswith("c") else number_format
                )
            calculation_table.Measures.Add(measure)
            python_measure_specs.append(
                PythonMeasureSpec(
                    name=calc.name,
                    formula=calc.formula,
                    table=calculation_table_name,
                )
            )

        # --- 3b. Parámetros como medidas ---
        for param in extracted.parameters:
            options_table_name = f"{param.name} Options"
            if param.allowable_values:
                parameter_element = root.find(
                    f".//datasource[@name='Parameters']/column[@name='[{param.name}]']"
                )
                aliases = {
                    alias.get("key", ""): alias.get("value", alias.get("key", ""))
                    for alias in (
                        parameter_element.findall("./aliases/alias")
                        if parameter_element is not None
                        else []
                    )
                }
                options_table = Table()
                options_table.Name = options_table_name

                value_column = DataColumn()
                value_column.Name = "Value"
                value_column.SourceColumn = "Value"
                value_column.DataType = (
                    DataType.Int64
                    if param.datatype in ("integer", "int")
                    else DataType.String
                )
                options_table.Columns.Add(value_column)

                label_column = DataColumn()
                label_column.Name = "Label"
                label_column.SourceColumn = "Label"
                label_column.DataType = DataType.String
                options_table.Columns.Add(label_column)

                rows = []
                for value in param.allowable_values:
                    label = aliases.get(value, value).replace('"', '""')
                    value_literal = (
                        value
                        if param.datatype in ("integer", "int", "real", "float", "double")
                        else f'"{value.replace(chr(34), chr(34) * 2)}"'
                    )
                    rows.append(f'{{{value_literal}, "{label}"}}')
                options_partition = Partition()
                options_partition.Name = options_table_name
                options_partition.Mode = ModeType.Import
                options_partition.Source = MPartitionSource()
                value_m_type = (
                    "Int64.Type"
                    if param.datatype in ("integer", "int")
                    else "type number"
                    if param.datatype in ("real", "float", "double")
                    else "text"
                )
                options_partition.Source.Expression = (
                    f"#table(type table [Value={value_m_type}, Label=text], {{"
                    + ", ".join(rows)
                    + "})"
                )
                options_table.Partitions.Add(options_partition)
                model.Tables.Add(options_table)

            measure = Measure()
            measure.Name = param.name
            measure_names.add(param.name)

            default = (param.default_value or "").strip().strip("'\"")
            if param.allowable_values:
                default_literal = default if default else "BLANK()"
                measure.Expression = (
                    f"SELECTEDVALUE('{options_table_name}'[Value], {default_literal})"
                )
            elif param.datatype in ("integer", "real", "float", "double") and default:
                try:
                    float(default)
                    measure.Expression = default
                except ValueError:
                    measure.Expression = f'"{default}"'
            elif default:
                measure.Expression = f'"{default}"'
            else:
                measure.Expression = '""'
            measure.Description = (
                f"Parámetro Tableau '{param.caption}' "
                f"(tipo: {param.datatype}). Valor por defecto: {default or 'n/a'}."
            )
            primary_table.Measures.Add(measure)
            python_default = (
                default
                if param.datatype in ("integer", "real", "float", "double") and default
                else repr(default)
            )
            python_measure_specs.append(
                PythonMeasureSpec(
                    name=param.name,
                    formula=f"PARAMETER({param.name!r}, {python_default})",
                    table=table_name,
                )
            )

            conversion_diagnostics.append(
                ConversionDiagnostic(
                    status="manual",
                    category="parameter",
                    original=f"Parameter {param.name}",
                    dax=measure.Expression,
                    reason="Parámetro materializado como medida; "
                    "Power BI no tiene control interactivo equivalente",
                    manual_steps=[
                        "Crear un slicer o parámetro What-If en Power BI Desktop",
                        "Vincular el slicer a la medida del parámetro",
                    ],
                )
            )

        # --- 3c. Cálculos worksheet-local (AVG(0) y similares) ---
        local_calcs_seen: set[str] = set()
        for ws in root.findall(".//worksheet"):
            for col_elem in ws.findall(".//datasource-dependencies//column"):
                calc_elem = col_elem.find("calculation")
                if calc_elem is None:
                    continue
                local_name = col_elem.get("name", "").strip("[]")
                if not local_name or local_name in local_calcs_seen:
                    continue
                if (
                    local_name in measure_names
                    or local_name in existing_columns
                    or local_name in calculated_column_names
                ):
                    continue
                local_formula = calc_elem.get("formula", "").strip()
                if not local_formula:
                    continue

                local_calcs_seen.add(local_name)
                local_diag = convert_tableau_to_dax_diagnostic(
                    local_formula,
                    table_name=safe_table,
                    known_measures=measure_names,
                    known_columns=existing_columns,
                )
                conversion_diagnostics.append(local_diag)
                if local_diag.dax is None:
                    continue

                measure = Measure()
                measure.Name = local_name
                measure.Expression = local_diag.dax
                measure.Description = (
                    f"Cálculo worksheet-local. Fórmula Tableau: {local_formula}"
                )
                primary_table.Measures.Add(measure)
                measure_names.add(local_name)
                python_measure_specs.append(
                    PythonMeasureSpec(
                        name=local_name,
                        formula=local_formula,
                        table=table_name,
                    )
                )

        # --- 3d. Diagnóstico de filtros (categóricos vs. rango/acción) ---
        for ws_elem in root.findall(".//worksheet"):
            for filter_elem in ws_elem.findall(".//filter"):
                column = filter_elem.get("column", "")
                if not column:
                    continue
                range_elem = filter_elem.find(".//range")
                kind = "range" if range_elem is not None else "categorical"
                f_values: list[str] = []
                for member in filter_elem.findall(".//groupfilter/groupfilter"):
                    val = member.get("member", "")
                    if val:
                        clean_val = val.strip('"')
                        if not clean_val.startswith("%"):
                            f_values.append(clean_val)
                f_min = range_elem.get("from") if range_elem is not None else None
                f_max = range_elem.get("to") if range_elem is not None else None

                f_diag = diagnose_filter(column, kind, f_values, f_min, f_max)
                conversion_diagnostics.append(f_diag)
                if f_diag.status != "supported":
                    logger.info(
                        "Filtro no categórico conservado para revisión: %s (%s)",
                        column,
                        f_diag.status,
                    )

        # Resumen de diagnósticos
        supported = sum(1 for d in conversion_diagnostics if d.status == "supported")
        manual = sum(1 for d in conversion_diagnostics if d.status == "manual")
        unsupported = sum(
            1 for d in conversion_diagnostics if d.status == "unsupported"
        )
        logger.info(
            "Diagnóstico de conversión: %d soportados, %d manuales, %d no soportados",
            supported,
            manual,
            unsupported,
        )
        python_manifest = write_python_measure_module(
            Path(carpeta_salida_modelo).parent / "PythonMeasures" / "measures.py",
            python_measure_specs,
        )
        python_supported = sum(
            1 for item in python_manifest if item["status"] == "supported"
        )
        logger.info(
            "Versiones Python de medidas: %d generadas, %d ejecutables, %d manuales",
            len(python_manifest),
            python_supported,
            len(python_manifest) - python_supported,
        )

    except Exception as e:  # pragma: no cover
        logger.warning(f"No se pudieron materializar cálculos/parámetros: {e}")

    # 4. Guardar Resultado
    try:
        if legacy_mode:
            # Modo PBIRS: Guardar como model.bim JSON monolítico
            model_bim_path = os.path.join(carpeta_salida_modelo, "model.bim")
            print(f"📁 Modo Legacy: Guardando model.bim en {model_bim_path}")

            # Serializar a string primero
            content = JsonSerializer.SerializeDatabase(database)

            # Escribir a archivo
            with open(model_bim_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Limpiar carpeta definition si existe para evitar conflictos
            def_path = os.path.join(carpeta_salida_modelo, "definition")
            if os.path.exists(def_path):
                import shutil

                shutil.rmtree(def_path)
        else:
            # Modo Cloud: Guardar como TMDL (carpetas)
            target_definition = os.path.join(carpeta_salida_modelo, "definition")
            if not os.path.exists(target_definition):
                os.makedirs(target_definition)

            print(f"[TMDL] Guardando en {target_definition}")
            TmdlSerializer.SerializeDatabaseToFolder(database, target_definition)

    except Exception as e:
        # Re-raise to show in logs
        raise RuntimeError(
            f"CRITICAL ERROR saving model ({'Legacy' if legacy_mode else 'TMDL'}): {e}"
        )

    return tablas_creadas_count
