"""
SQL Query Optimizer - Genera consultas SQL optimizadas basadas en columnas usadas.

Este módulo analiza un TWB y genera consultas SQL que solo incluyen
las columnas realmente usadas, reduciendo la carga en la base de datos.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.sql_dialect import get_dialect
from core.twb_column_auditor import audit_tableau_file


@dataclass
class OptimizedQuery:
    """Representa una consulta SQL optimizada."""

    datasource_name: str
    schema: str
    table: str
    original_columns: int
    used_columns: int
    columns: list[str]
    sql: str
    savings_percent: float
    datasource_id: str  # ID interno (name)
    db_type: str | None = None  # Tipo de BD detectado: 'redshift', 'sqlserver', etc.
    parameters: tuple[Any, ...] = ()


def generate_optimized_queries(file_path: str) -> list[OptimizedQuery]:
    """
    Genera consultas SQL optimizadas para cada datasource del workbook.

    Args:
        file_path: Ruta al archivo .twb o .twbx

    Returns:
        Lista de OptimizedQuery con las consultas generadas
    """
    return generate_optimized_queries_with_filters(file_path, None)


def generate_optimized_queries_with_filters(
    file_path: str, filters_dict: dict | None = None
) -> list[OptimizedQuery]:
    """Genera consultas con inyección de filtros sugeridos."""
    audit = audit_tableau_file(file_path)
    queries = []

    # Deduplicar datasources
    seen = set()
    for ds in audit.datasources:
        if ds.name in seen:
            continue
        seen.add(ds.name)

        # Solo generar si hay columnas usadas y tiene tabla
        if ds.used_columns == 0 or not ds.table:
            continue

        # Obtener columnas usadas (EXCLUYENDO CALCULADAS)
        used_cols = [c.name for c in ds.columns if c.is_used and not c.is_calculated]

        if not used_cols:
            continue

        # Limpiar nombre de tabla (quitar corchetes de SQL Server, usar sintaxis Redshift/Postgres)
        table_name = ds.table.strip('[]`"') if ds.table else ""
        schema_name = ds.schema.strip('[]`"') if ds.schema else ""
        if not table_name:
            continue  # Sin tabla, no podemos generar SQL

        # Limpiar nombres de columna (quitar corchetes y namespaces XML)
        clean_cols = []
        for col in used_cols:
            clean = col.strip('[]`"')
            # Quitar namespace XML (none:campo:nk -> campo)
            if ":" in clean:
                parts = clean.split(":")
                clean = parts[1] if len(parts) >= 2 else parts[0]
            clean_cols.append(clean)

        # Obtener dialecto SQL basado en tipo de BD detectado
        dialect = get_dialect(ds.connection_class)
        schema_table = dialect.quote_qualified_name(schema_name, table_name)
        columns_sql = ",\n    ".join(dialect.quote_identifier(column) for column in clean_cols)
        db_info = (
            f"-- Base de datos: {dialect.name}"
            if ds.connection_class
            else "-- Base de datos: No detectada (usando sintaxis Redshift)"
        )

        # Inyectar filtros si existen
        where_clause = "-- WHERE [agregar filtros según análisis de filtros]"

        # Intentar buscar filtros para este datasource (por ID o Caption)
        suggested_where = ""
        if filters_dict:
            if ds.name in filters_dict:
                suggested_where = filters_dict[ds.name]
            elif ds.caption in filters_dict:
                suggested_where = filters_dict[ds.caption]

        parameters: tuple[Any, ...] = ()
        if isinstance(suggested_where, Mapping):
            filter_sql = suggested_where.get("sql")
            raw_parameters = suggested_where.get("params", ())
            if not isinstance(filter_sql, str) or not filter_sql.strip():
                raise ValueError("filter mappings require a non-empty sql value")
            if isinstance(raw_parameters, (str, bytes)):
                raise TypeError("filter params must be a sequence of driver values")
            where_clause = f"WHERE {filter_sql}"
            parameters = tuple(raw_parameters)
        elif isinstance(suggested_where, tuple) and len(suggested_where) == 2:
            filter_sql, raw_parameters = suggested_where
            if not isinstance(filter_sql, str) or not filter_sql.strip():
                raise ValueError("filter tuples require a non-empty sql value")
            if isinstance(raw_parameters, (str, bytes)):
                raise TypeError("filter params must be a sequence of driver values")
            where_clause = f"WHERE {filter_sql}"
            parameters = tuple(raw_parameters)
        elif suggested_where:
            raise TypeError("filters must provide SQL and driver parameters")

        sql = f"""-- Consulta optimizada para: {ds.caption}
{db_info}
-- Columnas originales: {ds.total_columns}, Usadas: {ds.used_columns}
-- Ahorro: {100 - ds.usage_percent:.1f}% menos columnas

SELECT
    {columns_sql}
FROM {schema_table}
{where_clause}
;"""

        savings = 100 - ds.usage_percent if ds.total_columns > 0 else 0

        queries.append(
            OptimizedQuery(
                datasource_name=ds.caption,
                schema=schema_name,
                table=table_name,
                original_columns=ds.total_columns,
                used_columns=ds.used_columns,
                columns=clean_cols,
                sql=sql,
                savings_percent=savings,
                datasource_id=ds.name,
                db_type=ds.connection_class,
                parameters=parameters,
            )
        )

    # Ordenar por mayor ahorro
    queries.sort(key=lambda x: x.savings_percent, reverse=True)

    return queries


def generate_create_view_sql(query: OptimizedQuery, view_suffix: str = "_optimized") -> str:
    """
    Genera un CREATE VIEW para reemplazar la tabla original.

    Args:
        query: OptimizedQuery con la información del datasource
        view_suffix: Sufijo para el nombre de la vista

    Returns:
        SQL para crear la vista optimizada
    """
    dialect = get_dialect(query.db_type)
    table_name = query.table.strip('[]`"')
    schema_name = query.schema.strip('[]`"')
    view_name = f"{table_name}{view_suffix}"
    schema_table = dialect.quote_qualified_name(schema_name, table_name)
    schema_view = dialect.quote_qualified_name(schema_name, view_name)
    columns_sql = ",\n    ".join(dialect.quote_identifier(column) for column in query.columns)
    create_view = (
        "CREATE OR ALTER VIEW" if dialect.code == "sqlserver" else "CREATE OR REPLACE VIEW"
    )

    return f"""-- Vista optimizada para Tableau
-- Reduce {query.original_columns} columnas a {query.used_columns} ({query.savings_percent:.1f}% menos)

{create_view} {schema_view} AS
SELECT
    {columns_sql}
FROM {schema_table};

-- Para usar en Tableau:
-- 1. Editar conexión del datasource
-- 2. Cambiar tabla de {query.table} a {view_name}
-- 3. Regenerar extracto
"""


def rewrite_select_star(sql_query: str, used_columns: list[str]) -> str:
    """
    Simula o reescribe una consulta 'SELECT *' para usar solo columnas específicas.
    Útil para el 'Auto-Fix' en migraciones directas.

    Args:
        sql_query: La consulta original (presumiblemente SELECT * FROM ...)
        used_columns: Lista de nombres de columnas que se deben manter.

    Returns:
        SQL reescrito con SELECT col1, col2, ...
    """
    import re

    # 1. Detectar patrón SELECT *
    # Esto es una heurística básica. Para parsing robusto se usa sqlglot en otras partes,
    # pero aquí queremos una sustitución rápida de texto.
    match = re.search(r"SELECT\s+\*\s+FROM", sql_query, re.IGNORECASE)

    if match:
        columns_sql = ", ".join(used_columns)
        # Reemplazar "SELECT *" por "SELECT col1, col2" manteniendo el resto (FROM ...)
        new_sql = re.sub(
            r"SELECT\s+\*\s+", f"SELECT {columns_sql} ", sql_query, count=1, flags=re.IGNORECASE
        )
        return new_sql

    # Si no es SELECT *, devolvemos original o intentamos inyección más compleja (fuera de alcance actual)
    return sql_query


def export_all_queries(file_path: str, output_path: str) -> int:
    """
    Exporta todas las consultas optimizadas a un archivo SQL.

    Args:
        file_path: Ruta al archivo .twb o .twbx
        output_path: Ruta donde guardar el archivo SQL

    Returns:
        Número de consultas generadas
    """
    queries = generate_optimized_queries(file_path)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("-- =====================================================\n")
        f.write("-- CONSULTAS SQL OPTIMIZADAS PARA TABLEAU\n")
        f.write("-- Generado por BI Bridge Studio\n")
        f.write("-- =====================================================\n\n")

        for i, q in enumerate(queries, 1):
            f.write(f"-- [{i}] {q.datasource_name}\n")
            f.write(f"-- Ahorro: {q.savings_percent:.1f}%\n")
            f.write("-" * 60 + "\n\n")
            f.write(q.sql)
            f.write("\n\n")
            f.write(generate_create_view_sql(q))
            f.write("\n" + "=" * 60 + "\n\n")

    return len(queries)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.sql_query_optimizer <archivo.twb>")
        sys.exit(1)

    file_path = sys.argv[1]
    output_path = file_path.replace(".twb", "_optimized_queries.sql")

    count = export_all_queries(file_path, output_path)
    print(f"✅ Generadas {count} consultas optimizadas en: {output_path}")
