"""
SQL Dialect - Maneja sintaxis SQL específica por base de datos.

Detecta el tipo de BD desde el atributo class de la conexión Tableau
y genera SQL con la sintaxis correcta para ese motor.
"""

from dataclasses import dataclass


class UnsupportedDialectError(ValueError):
    """Raised when a requested SQL backend has no declared dialect contract."""


_COMMON_SQL_KEYWORDS = frozenset(
    {
        "all",
        "and",
        "as",
        "by",
        "case",
        "column",
        "count",
        "create",
        "date",
        "delete",
        "from",
        "group",
        "having",
        "insert",
        "join",
        "limit",
        "order",
        "select",
        "table",
        "then",
        "union",
        "update",
        "user",
        "when",
        "where",
        "with",
    }
)


def _remove_outer_quotes(identifier: str) -> str:
    """Remove one matching Tableau-style identifier wrapper."""
    if len(identifier) >= 2 and (identifier[0], identifier[-1]) in {
        ("[", "]"),
        ("`", "`"),
        ('"', '"'),
    }:
        return identifier[1:-1]
    return identifier


@dataclass
class SQLDialect:
    """Representa la sintaxis SQL específica de un motor de base de datos."""

    name: str  # Nombre legible: "Amazon Redshift", "PostgreSQL", etc.
    code: str  # Código interno: "redshift", "postgres", etc.
    open_quote: str  # Caracter de apertura para identificadores: '"' o '['
    close_quote: str  # Caracter de cierre: '"' o ']'
    supports_ilike: bool = True  # Soporta ILIKE para case-insensitive
    date_format: str = "YYYY-MM-DD"  # Formato de fecha por defecto

    def quote_identifier(self, identifier: str) -> str:
        """
        Rodea un identificador con los caracteres de quote apropiados.
        Solo aplica quotes si es necesario (espacios, palabras reservadas, etc).
        """
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("SQL identifiers must be non-empty strings")
        if "\x00" in identifier:
            raise ValueError("SQL identifiers cannot contain NUL bytes")
        value = identifier.strip()
        if (
            self.code in ("redshift", "postgres")
            and value.islower()
            and value.isidentifier()
            and value not in _COMMON_SQL_KEYWORDS
        ):
            return value
        escaped = value.replace(self.close_quote, self.close_quote * 2)
        return f"{self.open_quote}{escaped}{self.close_quote}"

    def quote_qualified_name(self, *parts: str) -> str:
        """Quote each part of a qualified name without treating dots as data."""
        values = [part.strip() for part in parts if part and part.strip()]
        if not values:
            raise ValueError("a qualified SQL name needs at least one part")
        return ".".join(self.quote_identifier(_remove_outer_quotes(part)) for part in values)

    def format_column_list(self, columns: list[str], indent: str = "    ") -> str:
        """Formatea una lista de columnas para SELECT."""
        return f",\n{indent}".join(columns)


# Diccionario de dialectos soportados
DIALECTS: dict[str, SQLDialect] = {
    # Amazon Redshift (basado en PostgreSQL 8.x)
    "redshift": SQLDialect(
        name="Amazon Redshift",
        code="redshift",
        open_quote='"',
        close_quote='"',
        supports_ilike=True,
        date_format="YYYY-MM-DD",
    ),
    # PostgreSQL
    "postgres": SQLDialect(
        name="PostgreSQL",
        code="postgres",
        open_quote='"',
        close_quote='"',
        supports_ilike=True,
        date_format="YYYY-MM-DD",
    ),
    # Microsoft SQL Server
    "sqlserver": SQLDialect(
        name="SQL Server",
        code="sqlserver",
        open_quote="[",
        close_quote="]",
        supports_ilike=False,  # Usa COLLATE para case-insensitive
        date_format="YYYY-MM-DD",
    ),
    # MySQL
    "mysql": SQLDialect(
        name="MySQL",
        code="mysql",
        open_quote="`",
        close_quote="`",
        supports_ilike=False,  # Depende del collation
        date_format="%Y-%m-%d",
    ),
    # Oracle
    "oracle": SQLDialect(
        name="Oracle",
        code="oracle",
        open_quote='"',
        close_quote='"',
        supports_ilike=False,
        date_format="YYYY-MM-DD",
    ),
    # Google BigQuery
    "bigquery": SQLDialect(
        name="Google BigQuery",
        code="bigquery",
        open_quote="`",
        close_quote="`",
        supports_ilike=False,
        date_format="%Y-%m-%d",
    ),
    # Snowflake
    "snowflake": SQLDialect(
        name="Snowflake",
        code="snowflake",
        open_quote='"',
        close_quote='"',
        supports_ilike=True,
        date_format="YYYY-MM-DD",
    ),
    # Hyper (extractos de Tableau)
    "hyper": SQLDialect(
        name="Tableau Hyper",
        code="hyper",
        open_quote='"',
        close_quote='"',
        supports_ilike=False,
        date_format="YYYY-MM-DD",
    ),
}

# Dialecto por defecto si no se detecta
DEFAULT_DIALECT = DIALECTS["redshift"]


def get_dialect(connection_class: str | None, *, strict: bool = True) -> SQLDialect:
    """
    Obtiene el dialecto SQL basado en el class de conexión de Tableau.

    Args:
        connection_class: Valor del atributo class de <connection>, ej: 'redshift'

    Returns:
        SQLDialect correspondiente. A missing value uses the documented default.

    Raises:
        UnsupportedDialectError: If a non-empty backend is not declared.
    """
    if not connection_class:
        return DEFAULT_DIALECT

    # Normalizar a minúsculas
    key = connection_class.lower().strip()

    # Mapeos adicionales de nombres de conexión de Tableau
    tableau_mappings = {
        "amazon redshift": "redshift",
        "postgres": "postgres",
        "postgresql": "postgres",
        "sql server": "sqlserver",
        "mssql": "sqlserver",
        "amazon-redshift": "redshift",
        "google bigquery": "bigquery",
        "snowflake": "snowflake",
    }

    if key in tableau_mappings:
        key = tableau_mappings[key]

    dialect = DIALECTS.get(key)
    if dialect is not None:
        return dialect
    if strict:
        raise UnsupportedDialectError(f"unsupported SQL backend: {connection_class!r}")
    return DEFAULT_DIALECT


def format_sql_select(
    columns: list[str],
    schema: str,
    table: str,
    where_clause: str | None = None,
    dialect: SQLDialect | None = None,
    comment_header: str | None = None,
) -> str:
    """
    Genera un SELECT SQL con la sintaxis del dialecto especificado.

    Args:
        columns: Lista de nombres de columna
        schema: Nombre del schema
        table: Nombre de la tabla
        where_clause: Cláusula WHERE opcional (sin el WHERE)
        dialect: Dialecto SQL a usar
        comment_header: Comentario opcional para agregar al inicio

    Returns:
        SQL formateado como string
    """
    if dialect is None:
        dialect = DEFAULT_DIALECT

    if not columns:
        raise ValueError("SELECT requires at least one column")

    # Formatear columnas
    cols_sql = dialect.format_column_list([dialect.quote_identifier(column) for column in columns])

    # Construir FROM
    from_clause = dialect.quote_qualified_name(schema, table)

    # Construir SQL
    lines = []

    if comment_header:
        lines.append(f"-- {comment_header}")

    lines.append("SELECT")
    lines.append(f"    {cols_sql}")
    lines.append(f"FROM {from_clause}")

    if where_clause:
        lines.append(f"WHERE {where_clause}")

    lines.append(";")

    return "\n".join(lines)
