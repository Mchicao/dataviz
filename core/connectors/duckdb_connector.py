"""DuckDB analytical connector for local in-memory and file-backed workloads.

DuckDB is optional at import time. The connector imports the driver only when a
connection is opened, supports local CSV/Parquet views, exposes read-only SQL,
and reports database schema through ``information_schema``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from core.contracts.connector import (
    SCHEMA_VERSION,
    AuthKind,
    AuthSupport,
    CapabilitySupport,
    ConnectorCapabilityContract,
    ExecutionMode,
    LimitsSupport,
    PushdownSupport,
    QueryKind,
    QuerySupport,
    SchemaDiscoveryLevel,
)

_DEFAULT_ROW_LIMIT = 100_000
_READ_ONLY_PREFIX = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_INTEGER_TYPES = {
    "tinyint",
    "smallint",
    "integer",
    "bigint",
    "hugeint",
    "utinyint",
    "usmallint",
    "uinteger",
    "ubigint",
}
_FLOAT_TYPES = {"real", "float", "double"}


class DuckDBConnectorError(Exception):
    """Controlled DuckDB connector failure."""


class DuckDBUnsupportedQueryError(DuckDBConnectorError):
    """Raised when public query execution receives non-read-only SQL."""


@dataclass(frozen=True)
class DuckDBConfig:
    """Non-secret DuckDB connection configuration."""

    database: str | Path = ":memory:"
    read_only: bool = False

    def __post_init__(self) -> None:
        database = str(self.database)
        if not database.strip():
            raise ValueError("database must be ':memory:' or a non-empty file path")
        if "\x00" in database:
            raise ValueError("database path must not contain NUL")
        if database == ":memory:" and self.read_only:
            raise ValueError("read_only is not valid for an in-memory DuckDB database")
        object.__setattr__(self, "database", database)


@dataclass(frozen=True)
class DuckDBColumnInfo:
    """One discovered DuckDB column."""

    name: str
    neutral_type: str
    source_type: str
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "neutral_type": self.neutral_type,
            "source_type": self.source_type,
            "nullable": self.nullable,
        }


@dataclass(frozen=True)
class DuckDBTableInfo:
    """One discovered DuckDB table or view."""

    schema_name: str
    name: str
    table_type: str = ""
    columns: tuple[DuckDBColumnInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "name": self.name,
            "table_type": self.table_type,
            "columns": [column.to_dict() for column in self.columns],
        }


@dataclass(frozen=True)
class DuckDBDiscovery:
    """Schema snapshot returned by :meth:`DuckDBConnector.discover`."""

    tables: tuple[DuckDBTableInfo, ...] = ()

    @property
    def table_count(self) -> int:
        return len(self.tables)

    def get_table(self, schema_name: str, name: str) -> DuckDBTableInfo | None:
        for table in self.tables:
            if table.schema_name == schema_name and table.name == name:
                return table
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"tables": [table.to_dict() for table in self.tables]}


@dataclass(frozen=True)
class DuckDBQueryResult:
    """Column names plus materialized rows from one analytical read."""

    column_names: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def rows_emitted(self) -> int:
        return self.row_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_names": list(self.column_names),
            "rows": list(self.rows),
            "row_count": self.row_count,
        }


ConnectionFactory = Callable[[DuckDBConfig], Any]


def _neutral_type(source_type: str) -> str:
    normalized = source_type.strip().lower()
    base = normalized.split("(", 1)[0].strip()
    if base == "boolean":
        return "boolean"
    if base in _INTEGER_TYPES:
        return "integer"
    if base in _FLOAT_TYPES:
        return "float"
    if base in {"decimal", "numeric"}:
        return "decimal"
    if base == "date":
        return "date"
    if base.startswith(("timestamp", "datetime")):
        return "datetime"
    if base in {"blob", "bytea", "binary", "varbinary"}:
        return "binary"
    return "string"


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifier must be a non-empty string")
    if "\x00" in value:
        raise ValueError("identifier must not contain NUL")
    return '"' + value.replace('"', '""') + '"'


def _qualified_identifier(value: str) -> str:
    parts = value.split(".")
    if not 1 <= len(parts) <= 2 or any(not part.strip() for part in parts):
        raise ValueError("table name must be 'table' or 'schema.table'")
    return ".".join(_quote_identifier(part) for part in parts)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit must be a non-negative int, got {limit!r}")
    return limit


def _select_list(columns: Sequence[str] | None) -> str:
    if columns is None:
        return "*"
    if not columns:
        raise ValueError("columns must not be empty")
    return ", ".join(_quote_identifier(column) for column in columns)


def _validate_read_only_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise DuckDBUnsupportedQueryError("query must be a non-empty SQL string")
    statement = sql.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement or not _READ_ONLY_PREFIX.match(statement):
        raise DuckDBUnsupportedQueryError(
            "only one read-only SELECT/WITH statement is supported"
        )
    return statement


def _load_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as error:
        raise DuckDBConnectorError(
            "DuckDB support requires the optional 'duckdb' package"
        ) from error
    return duckdb


def _default_connection_factory(config: DuckDBConfig) -> Any:
    duckdb = _load_duckdb()
    try:
        return duckdb.connect(database=config.database, read_only=config.read_only)
    except Exception as error:  # noqa: BLE001 - normalize driver failures
        raise DuckDBConnectorError(f"failed to connect to DuckDB: {error}") from error


class DuckDBConnector:
    """Analytical connector over an in-memory or file-backed DuckDB database."""

    kind = "duckdb"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="duckdb",
        implementation=CapabilitySupport.SUPPORTED,
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=(
            "string",
            "boolean",
            "integer",
            "float",
            "decimal",
            "date",
            "datetime",
            "binary",
        ),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        query=QuerySupport(
            kind=QueryKind.SQL,
            support=CapabilitySupport.SUPPORTED,
            dialect="duckdb",
        ),
        pushdown=PushdownSupport(
            projection=True,
            filter=True,
            aggregation=True,
            limit=True,
            dialect="duckdb",
        ),
        streaming_row_chunkable=False,
        cancelable=False,
        limits=LimitsSupport(row_limit=True, time_limit=False, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description=(
            "Local DuckDB analytical engine for in-memory or file-backed databases, "
            "including CSV and Parquet scan views."
        ),
    )

    def __init__(
        self,
        database: str | Path | DuckDBConfig = ":memory:",
        *,
        read_only: bool = False,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if isinstance(database, DuckDBConfig):
            if read_only:
                raise ValueError("read_only must be configured on DuckDBConfig when config is passed")
            config = database
        else:
            config = DuckDBConfig(database=database, read_only=read_only)
        self._config = config
        self._connection_factory = connection_factory or _default_connection_factory
        self._connection: Any | None = None

    @property
    def config(self) -> DuckDBConfig:
        return self._config

    @property
    def database(self) -> str:
        return str(self._config.database)

    @property
    def connected(self) -> bool:
        return self._connection is not None

    @property
    def capability_contract(self) -> ConnectorCapabilityContract:
        return self.CAPABILITIES

    def connect(self) -> DuckDBConnector:
        """Open the configured database and return ``self``."""
        if self._connection is not None:
            return self
        try:
            self._connection = self._connection_factory(self._config)
        except DuckDBConnectorError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize injected driver failures
            raise DuckDBConnectorError(f"failed to connect to DuckDB: {error}") from error
        return self

    def close(self) -> None:
        """Close the active connection; repeated calls are harmless."""
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def __enter__(self) -> DuckDBConnector:
        return self.connect()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise DuckDBConnectorError("DuckDB connection is not established")
        return self._connection

    @staticmethod
    def _result(cursor: Any) -> DuckDBQueryResult:
        description = cursor.description or ()
        column_names = tuple(str(column[0]) for column in description)
        rows = tuple(
            dict(zip(column_names, row, strict=False)) for row in cursor.fetchall()
        )
        return DuckDBQueryResult(column_names=column_names, rows=rows)

    def register_parquet(self, table_name: str, path: str | Path) -> None:
        """Expose one local Parquet file as a DuckDB view."""
        self._register_file(table_name, path, extension=".parquet", reader="read_parquet")

    def register_csv(self, table_name: str, path: str | Path) -> None:
        """Expose one local CSV file as a DuckDB view with automatic inference."""
        self._register_file(table_name, path, extension=".csv", reader="read_csv_auto")

    def register_file(self, table_name: str, path: str | Path) -> None:
        """Expose a CSV or Parquet file based on its extension."""
        suffix = Path(path).suffix.lower()
        if suffix == ".parquet":
            self.register_parquet(table_name, path)
            return
        if suffix == ".csv":
            self.register_csv(table_name, path)
            return
        raise DuckDBConnectorError(
            f"unsupported analytical file extension {suffix!r}; expected .csv or .parquet"
        )

    def _register_file(
        self,
        table_name: str,
        path: str | Path,
        *,
        extension: str,
        reader: str,
    ) -> None:
        connection = self._require_connection()
        resolved = Path(path).resolve()
        if resolved.suffix.lower() != extension:
            raise DuckDBConnectorError(
                f"expected a {extension} file, got {resolved.suffix or '<no extension>'}"
            )
        if not resolved.is_file():
            raise DuckDBConnectorError(f"analytical file does not exist: {resolved}")
        view = _quote_identifier(table_name)
        source = _quote_literal(str(resolved))
        try:
            connection.execute(
                f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {reader}({source})",
                [],
            )
        except Exception as error:  # noqa: BLE001 - normalize driver/file failures
            raise DuckDBConnectorError(f"failed to register {extension} source: {error}") from error

    def query(
        self,
        sql: str,
        parameters: Sequence[Any] | None = None,
        *,
        limit: int = _DEFAULT_ROW_LIMIT,
    ) -> DuckDBQueryResult:
        """Execute one read-only SQL statement under an outer hard row bound."""
        connection = self._require_connection()
        statement = _validate_read_only_sql(sql)
        bounded_sql = f"SELECT * FROM ({statement}) AS __dataviz_query LIMIT ?"
        params = list(parameters or ())
        params.append(_validate_limit(limit))
        try:
            return self._result(connection.execute(bounded_sql, params))
        except DuckDBUnsupportedQueryError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize driver failures
            raise DuckDBConnectorError(f"DuckDB query failed: {error}") from error

    def query_table(
        self,
        table_name: str,
        *,
        columns: Sequence[str] | None = None,
        limit: int = _DEFAULT_ROW_LIMIT,
    ) -> DuckDBQueryResult:
        """Read a table/view with identifier quoting and a hard row bound."""
        connection = self._require_connection()
        sql = (
            f"SELECT {_select_list(columns)} FROM {_qualified_identifier(table_name)} "
            "LIMIT ?"
        )
        try:
            return self._result(connection.execute(sql, [_validate_limit(limit)]))
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize driver failures
            raise DuckDBConnectorError(f"DuckDB table query failed: {error}") from error

    def query_parquet(
        self,
        path: str | Path,
        *,
        columns: Sequence[str] | None = None,
        limit: int = _DEFAULT_ROW_LIMIT,
    ) -> DuckDBQueryResult:
        """Read a Parquet file through parameterized ``read_parquet``."""
        return self._query_file(path, "read_parquet", columns=columns, limit=limit)

    def query_csv(
        self,
        path: str | Path,
        *,
        columns: Sequence[str] | None = None,
        limit: int = _DEFAULT_ROW_LIMIT,
    ) -> DuckDBQueryResult:
        """Read a CSV file through parameterized ``read_csv_auto``."""
        return self._query_file(path, "read_csv_auto", columns=columns, limit=limit)

    def _query_file(
        self,
        path: str | Path,
        reader: str,
        *,
        columns: Sequence[str] | None,
        limit: int,
    ) -> DuckDBQueryResult:
        connection = self._require_connection()
        sql = f"SELECT {_select_list(columns)} FROM {reader}(?) LIMIT ?"
        params = [str(path), _validate_limit(limit)]
        try:
            return self._result(connection.execute(sql, params))
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize driver/file failures
            raise DuckDBConnectorError(f"DuckDB file query failed: {error}") from error

    def describe_file(
        self,
        path: str | Path,
        *,
        file_format: str,
    ) -> tuple[DuckDBColumnInfo, ...]:
        """Infer ordered metadata for a CSV or Parquet file without reading rows."""
        normalized = file_format.strip().lower()
        readers = {"csv": "read_csv_auto", "parquet": "read_parquet"}
        if normalized not in readers:
            raise ValueError("file_format must be 'csv' or 'parquet'")
        connection = self._require_connection()
        try:
            cursor = connection.execute(
                f"SELECT * FROM {readers[normalized]}(?) LIMIT 0",
                [str(path)],
            )
        except Exception as error:  # noqa: BLE001 - normalize driver/file failures
            raise DuckDBConnectorError(f"DuckDB file schema lookup failed: {error}") from error
        return tuple(
            DuckDBColumnInfo(
                name=str(column[0]),
                neutral_type=_neutral_type(str(column[1])),
                source_type=str(column[1]),
            )
            for column in (cursor.description or ())
        )

    def get_schema(
        self,
        table_name: str,
        *,
        schema_name: str = "main",
    ) -> tuple[DuckDBColumnInfo, ...]:
        """Return ordered column metadata for one table or view."""
        if not table_name.strip() or not schema_name.strip():
            raise ValueError("schema_name and table_name must be non-empty")
        connection = self._require_connection()
        try:
            rows = connection.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
                ORDER BY ordinal_position
                """,
                [schema_name, table_name],
            ).fetchall()
        except Exception as error:  # noqa: BLE001 - normalize metadata failures
            raise DuckDBConnectorError(f"DuckDB schema lookup failed: {error}") from error
        if not rows:
            raise DuckDBConnectorError(
                f"table or view not found: {schema_name}.{table_name}"
            )
        return tuple(
            DuckDBColumnInfo(
                name=str(column_name),
                neutral_type=_neutral_type(str(source_type)),
                source_type=str(source_type),
                nullable=str(nullable).upper() == "YES",
            )
            for column_name, source_type, nullable in rows
        )

    def discover(self, *, column_limit: int | None = None) -> DuckDBDiscovery:
        """Report tables/views and ordered columns from ``information_schema``."""
        if column_limit is not None:
            return self._discover_columns_bounded(column_limit)
        connection = self._require_connection()
        try:
            rows = connection.execute(
                """
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name
                """,
                [],
            ).fetchall()
            tables = tuple(
                DuckDBTableInfo(
                    schema_name=str(schema_name),
                    name=str(table_name),
                    table_type=str(table_type),
                    columns=self.get_schema(str(table_name), schema_name=str(schema_name)),
                )
                for schema_name, table_name, table_type in rows
            )
            return DuckDBDiscovery(tables=tables)
        except DuckDBConnectorError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize metadata failures
            raise DuckDBConnectorError(f"DuckDB schema discovery failed: {error}") from error

    def _discover_columns_bounded(self, column_limit: int) -> DuckDBDiscovery:
        """Compatibility path for callers that require a global metadata cap."""
        connection = self._require_connection()
        limit = _validate_limit(column_limit)
        try:
            rows = connection.execute(
                """
                SELECT table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name, ordinal_position
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        except Exception as error:  # noqa: BLE001 - normalize metadata failures
            raise DuckDBConnectorError(f"DuckDB schema discovery failed: {error}") from error
        grouped: dict[tuple[str, str], list[DuckDBColumnInfo]] = {}
        for schema_name, table_name, column_name, source_type in rows:
            grouped.setdefault((str(schema_name), str(table_name)), []).append(
                DuckDBColumnInfo(
                    name=str(column_name),
                    neutral_type=_neutral_type(str(source_type)),
                    source_type=str(source_type),
                )
            )
        return DuckDBDiscovery(
            tables=tuple(
                DuckDBTableInfo(schema_name=schema, name=table, columns=tuple(columns))
                for (schema, table), columns in grouped.items()
            )
        )


DUCKDB_CAPABILITY_CONTRACT = DuckDBConnector.CAPABILITIES


__all__ = [
    "DUCKDB_CAPABILITY_CONTRACT",
    "ConnectionFactory",
    "DuckDBColumnInfo",
    "DuckDBConfig",
    "DuckDBConnector",
    "DuckDBConnectorError",
    "DuckDBDiscovery",
    "DuckDBQueryResult",
    "DuckDBTableInfo",
    "DuckDBUnsupportedQueryError",
]
