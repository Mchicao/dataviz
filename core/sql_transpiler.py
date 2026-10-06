"""
SQL Transpiler - Convierte SQL entre dialectos usando sqlglot.

Este módulo reemplaza/complementa la lógica manual de sql_dialect.py
con un transpilador robusto que maneja casos edge automáticamente.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SQLTranspilationError(ValueError):
    """Raised when SQL cannot be parsed or a backend is unsupported."""

    def __init__(self, message: str, *, source: str, target: str) -> None:
        super().__init__(f"SQL transpilation {source}->{target} failed: {message}")
        self.source = source
        self.target = target


_DIALECT_ALIASES = {
    "sqlserver": "tsql",
    "sql server": "tsql",
    "mssql": "tsql",
    "postgresql": "postgres",
    "amazon redshift": "redshift",
    "amazon-redshift": "redshift",
    "google bigquery": "bigquery",
    "snowflake": "snowflake",
}
_SUPPORTED_DIALECTS = {
    "bigquery",
    "duckdb",
    "hive",
    "mysql",
    "oracle",
    "postgres",
    "redshift",
    "snowflake",
    "spark",
    "sqlite",
    "tsql",
}


def _normalize_dialect(value: str) -> str:
    key = value.lower().strip()
    key = _DIALECT_ALIASES.get(key, key)
    if key not in _SUPPORTED_DIALECTS:
        raise SQLTranspilationError(
            f"unsupported SQL dialect: {value!r}", source=value, target=value
        )
    return key


def transpile_sql(
    sql: str,
    source_dialect: str = "tsql",
    target_dialect: str = "redshift",
    pretty: bool = True,
) -> str:
    """
    Transpila SQL de un dialecto a otro.

    Args:
        sql: Query SQL original
        source_dialect: Dialecto origen (tsql, postgres, mysql, oracle, etc.)
        target_dialect: Dialecto destino (redshift, postgres, bigquery, etc.)
        pretty: Si formatear el SQL resultante

    Returns:
        SQL transpilado al dialecto destino
    """
    source = _normalize_dialect(source_dialect)
    target = _normalize_dialect(target_dialect)
    try:
        statements = sqlglot.parse(sql, read=source)
        if len(statements) != 1:
            raise SQLTranspilationError(
                "multiple SQL statements are not supported",
                source=source,
                target=target,
            )
        result = sqlglot.transpile(sql, read=source, write=target, pretty=pretty)
    except SQLTranspilationError:
        raise
    except Exception as error:
        raise SQLTranspilationError(str(error), source=source, target=target) from error
    return result[0] if result else sql


def clean_tableau_sql_for_redshift(sql: str) -> str:
    """
    Limpia SQL generado por Tableau para ejecutar en Redshift.

    Maneja:
    - [brackets] → "double quotes"
    - TOP N → LIMIT N
    - Funciones de fecha T-SQL → funciones Redshift
    - ISNULL → COALESCE
    """
    return transpile_sql(sql, source_dialect="tsql", target_dialect="redshift")


def extract_columns_from_select(sql: str) -> list[str]:
    """
    Extrae nombres de columnas de un SELECT.

    Args:
        sql: Query SELECT

    Returns:
        Lista de nombres de columna
    """
    try:
        parsed = sqlglot.parse_one(sql)
        columns = []
        for expression in parsed.find_all(exp.Column):
            col_name = expression.name
            if col_name:
                columns.append(col_name)
        return list(dict.fromkeys(columns))
    except Exception:
        return []


def validate_sql_syntax(sql: str, dialect: str = "redshift") -> tuple[bool, str]:
    """
    Valida la sintaxis de un query SQL.

    Args:
        sql: Query a validar
        dialect: Dialecto para validar

    Returns:
        Tuple (es_valido, mensaje_error)
    """
    try:
        normalized = _normalize_dialect(dialect)
        statements = sqlglot.parse(sql, read=normalized)
        if len(statements) != 1:
            raise SQLTranspilationError(
                "multiple SQL statements are not supported",
                source=normalized,
                target=normalized,
            )
        return True, ""
    except (SQLTranspilationError, ParseError, ValueError) as e:
        return False, str(e)


def convert_dialect_code(tableau_class: str) -> str:
    """
    Convierte el código de conexión de Tableau al nombre de dialecto de sqlglot.

    Args:
        tableau_class: Valor del atributo class de <connection>

    Returns:
        Nombre del dialecto para sqlglot
    """
    mapping = {
        "redshift": "redshift",
        "amazon redshift": "redshift",
        "amazon-redshift": "redshift",
        "postgres": "postgres",
        "postgresql": "postgres",
        "sqlserver": "tsql",
        "sql server": "tsql",
        "mssql": "tsql",
        "mysql": "mysql",
        "oracle": "oracle",
        "bigquery": "bigquery",
        "google bigquery": "bigquery",
        "snowflake": "snowflake",
    }
    key = tableau_class.lower().strip() if tableau_class else "redshift"
    dialect = mapping.get(key, key)
    return _normalize_dialect(dialect)


if __name__ == "__main__":
    # Ejemplo de uso
    test_sql = """
    SELECT TOP 10
        [nombre],
        [fecha_creacion],
        ISNULL([valor], 0) as valor
    FROM [esquema].[mi_tabla]
    WHERE [activo] = 1
    """

    print("SQL Original (T-SQL):")
    print(test_sql)
    print("\nSQL Convertido (Redshift):")
    print(clean_tableau_sql_for_redshift(test_sql))
