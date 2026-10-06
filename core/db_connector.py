"""
Abstracción de conexión a múltiples bases de datos.
Soporta: Redshift (psycopg2), PostgreSQL (psycopg2), SQL Server (pyodbc),
Oracle (oracledb opcional)

NOTE: This module is a LEGACY/DEMO abstraction maintained for Streamlit UI
compatibility. Production services use canonical connectors in core.connectors.
Canonical product paths must never silently reinterpret connector identity.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from threading import Event, Thread
from typing import Any

from core.security.credential_safety import sanitize_text
from core.sql_dialect import get_dialect


class ConnectorError(RuntimeError):
    """Controlled connector failure with backend and operation diagnostics."""

    def __init__(self, message: str, *, backend: str, operation: str) -> None:
        safe_message, _ = sanitize_text(str(message), source=f"{backend}.{operation}")
        super().__init__(f"{backend} {operation} failed: {safe_message}")
        self.backend = backend
        self.operation = operation


class UnsupportedBackendError(ConnectorError):
    """Raised when a backend is not part of the connector contract."""


class QueryCancelled(ConnectorError):
    """Raised when a query is cancelled cooperatively."""


class QueryExecutionError(ConnectorError):
    """Raised when a driver reports a query failure."""


def _cancelled(cancel: Callable[[], bool] | Any | None) -> bool:
    if cancel is None:
        return False
    if callable(cancel):
        return bool(cancel())
    event_checker = getattr(cancel, "is_set", None)
    if callable(event_checker):
        return bool(event_checker())
    checker = getattr(cancel, "is_cancelled", None)
    if callable(checker):
        return bool(checker())
    raise TypeError("cancel must be callable, expose is_cancelled(), or be None")


def _cancel_driver(cursor: Any, connection: Any) -> bool:
    for target in (cursor, connection):
        for name in ("cancel", "cancel_query"):
            method = getattr(target, name, None)
            if callable(method):
                method()
                return True
    return False


def _close_driver(cursor: Any, connection: Any) -> None:
    for target in (connection, cursor):
        method = getattr(target, "close", None)
        if callable(method):
            method()
            return


def _watch_driver_cancel(
    cancel: Callable[[], bool] | Any,
    cursor: Any,
    connection: Any,
    stop: Event,
) -> None:
    while not stop.wait(0.01):
        if _cancelled(cancel):
            try:
                if not _cancel_driver(cursor, connection):
                    _close_driver(cursor, connection)
            except Exception:
                # The executing thread reports the driver error with context.
                pass
            return


def _dialect_code(driver: "DatabaseDriver") -> str:
    return "postgres" if driver == DatabaseDriver.POSTGRESQL else driver.value


class DatabaseDriver(Enum):
    REDSHIFT = "redshift"
    POSTGRESQL = "postgresql"
    SQLSERVER = "sqlserver"
    ORACLE = "oracle"


@dataclass
class DatabaseConfig:
    """Configuración de conexión a base de datos."""

    server: str
    port: int
    database: str
    username: str
    password: str
    driver: DatabaseDriver = DatabaseDriver.REDSHIFT
    trusted: bool = False

    @classmethod
    def from_dict(cls, config: dict) -> "DatabaseConfig":
        """Crea config desde diccionario."""
        driver_str = str(config.get("driver", "redshift")).lower().strip()
        if driver_str in {"redshift", "amazon redshift", "amazon-redshift"}:
            driver = DatabaseDriver.REDSHIFT
        elif driver_str in {"postgres", "postgresql"}:
            driver = DatabaseDriver.POSTGRESQL
        elif driver_str in {"sqlserver", "sql server", "mssql"}:
            driver = DatabaseDriver.SQLSERVER
        elif driver_str in {"oracle", "oracle db", "oracledb"}:
            driver = DatabaseDriver.ORACLE
        else:
            raise ValueError(f"unsupported database driver: {driver_str!r}")

        default_ports = {
            DatabaseDriver.REDSHIFT: 5439,
            DatabaseDriver.POSTGRESQL: 5432,
            DatabaseDriver.SQLSERVER: 1433,
            DatabaseDriver.ORACLE: 1521,
        }

        return cls(
            server=config["server"],
            port=int(config.get("port", default_ports[driver])),
            database=config["database"],
            username=config.get("username", ""),
            password=config.get("password", ""),
            driver=driver,
            trusted=config.get("trusted", False),
        )


@dataclass
class TableValidationResult:
    """Resultado de validación de una tabla."""

    schema: str
    table: str
    exists: bool
    has_data: bool
    row_count: int | None = None
    column_count: int | None = None
    columns: list[str] | None = None
    sample_row_count: int | None = None  # Conteo rápido (LIMIT-based estimate)
    error: str | None = None

    @property
    def status_emoji(self) -> str:
        if not self.exists:
            return "❌"
        elif not self.has_data:
            return "⚠️"
        else:
            return "✅"

    @property
    def status_text(self) -> str:
        if not self.exists:
            return "NO EXISTE"
        elif not self.has_data:
            return "SIN DATOS"
        else:
            return "OK"


@dataclass
class StructureComparisonResult:
    """Resultado de comparar estructura entre dos tablas."""

    source_schema: str
    target_schema: str
    table: str
    columns_match: bool
    source_columns: list[str]
    target_columns: list[str]
    missing_in_target: list[str]
    extra_in_target: list[str]
    source_row_count: int | None = None
    target_row_count: int | None = None
    row_count_diff_percent: float | None = None

    @property
    def status_emoji(self) -> str:
        if not self.columns_match:
            return "❌"
        elif self.row_count_diff_percent and abs(self.row_count_diff_percent) > 50:
            return "⚠️"
        else:
            return "✅"


class DatabaseConnector:
    """Conector abstracto para múltiples bases de datos."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection = None

    def connect(self):
        """Establece conexión con la base de datos."""
        if self.config.driver == DatabaseDriver.ORACLE:
            # Import diferido: python-oracledb es opcional y no forma parte
            # de las dependencias del proyecto.
            try:
                import oracledb  # type: ignore[reportMissingImports]
            except ImportError as error:
                raise UnsupportedBackendError(
                    f"python-oracledb is not installed; install it with 'uv add oracledb': {error}",
                    backend=self.backend,
                    operation="connect",
                ) from error

            try:
                dsn = f"{self.config.server}:{self.config.port}/{self.config.database}"
                self._connection = oracledb.connect(
                    user=self.config.username,
                    password=self.config.password,
                    dsn=dsn,
                )
            except Exception as error:
                raise ConnectorError(
                    str(error), backend=self.config.driver.value, operation="connect"
                ) from error
        elif self.config.driver == DatabaseDriver.SQLSERVER:
            try:
                import pyodbc  # type: ignore[reportMissingImports]
            except ImportError as error:
                raise ConnectorError(
                    f"pyodbc is not installed: {error}",
                    backend=self.backend,
                    operation="connect",
                ) from error

            try:
                if self.config.trusted:
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={self.config.server},{self.config.port};"
                        f"DATABASE={self.config.database};"
                        f"Trusted_Connection=yes;"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={self.config.server},{self.config.port};"
                        f"DATABASE={self.config.database};"
                        f"UID={self.config.username};"
                        f"PWD={self.config.password}"
                    )
                self._connection = pyodbc.connect(conn_str)
            except Exception as first_error:
                # Fallback to legacy SQL Server driver
                if self.config.trusted:
                    conn_str = (
                        f"DRIVER={{SQL Server}};"
                        f"SERVER={self.config.server},{self.config.port};"
                        f"DATABASE={self.config.database};"
                        f"Trusted_Connection=yes;"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{SQL Server}};"
                        f"SERVER={self.config.server},{self.config.port};"
                        f"DATABASE={self.config.database};"
                        f"UID={self.config.username};"
                        f"PWD={self.config.password}"
                    )
                try:
                    self._connection = pyodbc.connect(conn_str)
                except Exception as second_error:
                    raise ConnectorError(
                        f"ODBC Driver 17: {first_error}; legacy ODBC: {second_error}",
                        backend=self.config.driver.value,
                        operation="connect",
                    ) from second_error
        else:
            # Redshift y PostgreSQL usan psycopg2
            try:
                import psycopg2
            except ImportError as error:
                raise ConnectorError(
                    f"psycopg2 is not installed: {error}",
                    backend=self.backend,
                    operation="connect",
                ) from error

            try:
                self._connection = psycopg2.connect(
                    host=self.config.server,
                    port=self.config.port,
                    database=self.config.database,
                    user=self.config.username,
                    password=self.config.password,
                )
            except Exception as error:
                raise ConnectorError(
                    str(error), backend=self.config.driver.value, operation="connect"
                ) from error
        return self

    def close(self):
        """Cierra la conexión."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def execute_query(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
        cancel: Callable[[], bool] | Any | None = None,
    ) -> list[tuple]:
        """Ejecuta query y retorna resultados."""
        if _cancelled(cancel):
            raise QueryCancelled(
                "cancelled before execution", backend=self.backend, operation="query"
            )
        if self._connection is None:
            self.connect()
        connection = self._connection
        if connection is None:
            raise ConnectorError(
                "connection was not established", backend=self.backend, operation="query"
            )
        if _cancelled(cancel):
            raise QueryCancelled(
                "cancelled before cursor execution", backend=self.backend, operation="query"
            )
        cursor = None
        stop_cancel_watch = Event()
        cancel_watch: Thread | None = None
        try:
            cursor = connection.cursor()
            if cancel is not None:
                cancel_watch = Thread(
                    target=_watch_driver_cancel,
                    args=(cancel, cursor, connection, stop_cancel_watch),
                    daemon=True,
                )
                cancel_watch.start()
            if params is not None:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            if _cancelled(cancel):
                try:
                    _cancel_driver(cursor, connection)
                except Exception as error:
                    raise QueryCancelled(
                        f"cancelled after execution; driver cancel failed: {error}",
                        backend=self.backend,
                        operation="query",
                    ) from error
                raise QueryCancelled(
                    "cancelled after execution", backend=self.backend, operation="query"
                )
            rows = cursor.fetchall()
            if _cancelled(cancel):
                try:
                    _cancel_driver(cursor, connection)
                except Exception as error:
                    raise QueryCancelled(
                        f"cancelled after fetching; driver cancel failed: {error}",
                        backend=self.backend,
                        operation="query",
                    ) from error
                raise QueryCancelled(
                    "cancelled after fetching", backend=self.backend, operation="query"
                )
            return rows
        except QueryCancelled:
            raise
        except Exception as error:
            if _cancelled(cancel):
                raise QueryCancelled(
                    f"cancelled during execution: {error}",
                    backend=self.backend,
                    operation="query",
                ) from error
            raise QueryExecutionError(
                str(error), backend=self.backend, operation="query"
            ) from error
        finally:
            stop_cancel_watch.set()
            if cancel_watch is not None:
                cancel_watch.join(timeout=0.2)
            if cursor is not None:
                cursor.close()

    @property
    def backend(self) -> str:
        return self.config.driver.value

    def execute_scalar(
        self,
        query: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
        cancel: Callable[[], bool] | Any | None = None,
    ) -> Any:
        """Ejecuta query y retorna un solo valor."""
        result = self.execute_query(query, params, cancel)
        return result[0][0] if result else None

    def table_exists(self, schema: str, table: str) -> bool:
        """Verifica si una tabla existe en el esquema."""
        if self.config.driver == DatabaseDriver.SQLSERVER:
            query = """
                SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """
            count = self.execute_scalar(query, (schema, table))
        else:
            query = """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            """
            count = self.execute_scalar(query, (schema, table))
        return count > 0

    def table_has_data(self, schema: str, table: str) -> bool:
        """Verifica si una tabla tiene al menos una fila."""
        try:
            dialect = get_dialect(_dialect_code(self.config.driver))
            name = dialect.quote_qualified_name(schema, table)
            query = (
                f"SELECT TOP 1 1 FROM {name}"
                if self.config.driver == DatabaseDriver.SQLSERVER
                else f"SELECT 1 FROM {name} LIMIT 1"
            )
            result = self.execute_query(query)
            return len(result) > 0
        except QueryCancelled:
            raise
        except ConnectorError:
            return False

    def get_row_count_estimate(
        self, schema: str, table: str, max_count: int = 100000
    ) -> int | None:
        """
        Obtiene conteo estimado de filas.
        Para tablas grandes, usa estadísticas del sistema si están disponibles.
        """
        if not isinstance(max_count, int) or isinstance(max_count, bool) or max_count < 1:
            raise ValueError("max_count must be a positive integer")
        try:
            dialect = get_dialect(_dialect_code(self.config.driver))
            name = dialect.quote_qualified_name(schema, table)
            if self.config.driver == DatabaseDriver.REDSHIFT:
                # Redshift: usar SVV_TABLE_INFO para estimación rápida
                query = """
                    SELECT tbl_rows
                    FROM svv_table_info
                    WHERE schema = %s AND "table" = %s
                """
                result = self.execute_scalar(query, (schema, table))
                if result:
                    return int(result)

            # Fallback: contar directamente (con límite para evitar timeout)
            query = (
                f"SELECT COUNT(*) FROM (SELECT TOP {max_count} 1 FROM {name}) sub"
                if self.config.driver == DatabaseDriver.SQLSERVER
                else f"SELECT COUNT(*) FROM (SELECT 1 FROM {name} LIMIT {max_count}) sub"
            )
            count = self.execute_scalar(query)
            return count
        except QueryCancelled:
            raise
        except Exception as e:
            print(f"[WARN] No se pudo obtener row count para {schema}.{table}: {e}")
            return None

    def get_table_columns(self, schema: str, table: str) -> list[str]:
        """Obtiene lista de columnas de una tabla."""
        if self.config.driver == DatabaseDriver.SQLSERVER:
            query = """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                ORDER BY ORDINAL_POSITION
            """
            result = self.execute_query(query, (schema, table))
        else:
            query = """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
            """
            result = self.execute_query(query, (schema, table))
        return [row[0] for row in result]

    def get_schema_tables(self, schema: str) -> list[str]:
        """Obtiene lista de todas las tablas en un esquema."""
        if self.config.driver == DatabaseDriver.SQLSERVER:
            query = """
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
            """
            result = self.execute_query(query, (schema,))
        else:
            query = """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """
            result = self.execute_query(query, (schema,))
        return [row[0] for row in result]

    def validate_table(self, schema: str, table: str) -> TableValidationResult:
        """Valida una tabla completamente."""
        try:
            exists = self.table_exists(schema, table)
            if not exists:
                return TableValidationResult(
                    schema=schema, table=table, exists=False, has_data=False
                )

            has_data = self.table_has_data(schema, table)
            columns = self.get_table_columns(schema, table)
            row_count = self.get_row_count_estimate(schema, table) if has_data else 0

            return TableValidationResult(
                schema=schema,
                table=table,
                exists=True,
                has_data=has_data,
                row_count=row_count,
                column_count=len(columns),
                columns=columns,
            )
        except QueryCancelled:
            raise
        except Exception as e:
            return TableValidationResult(
                schema=schema, table=table, exists=False, has_data=False, error=str(e)
            )

    def compare_table_structure(
        self, source_schema: str, target_schema: str, table: str
    ) -> StructureComparisonResult:
        """
        Compara la estructura de una tabla entre dos esquemas.
        Incluye: columnas, conteo de filas, diferencias.
        """
        source_cols = self.get_table_columns(source_schema, table)
        target_cols = self.get_table_columns(target_schema, table)

        source_set = set(source_cols)
        target_set = set(target_cols)

        missing = sorted(source_set - target_set)
        extra = sorted(target_set - source_set)

        source_count = self.get_row_count_estimate(source_schema, table)
        target_count = self.get_row_count_estimate(target_schema, table)

        diff_percent = None
        if source_count and target_count and source_count > 0:
            diff_percent = ((target_count - source_count) / source_count) * 100

        return StructureComparisonResult(
            source_schema=source_schema,
            target_schema=target_schema,
            table=table,
            columns_match=(len(missing) == 0 and len(extra) == 0),
            source_columns=source_cols,
            target_columns=target_cols,
            missing_in_target=missing,
            extra_in_target=extra,
            source_row_count=source_count,
            target_row_count=target_count,
            row_count_diff_percent=diff_percent,
        )


def suggest_fallback_drivers(driver: DatabaseDriver) -> list[DatabaseDriver]:
    """
    Sugiere drivers de respaldo equivalentes para el driver dado.
    Redshift y PostgreSQL comparten psycopg2 y son mutuamente intercambiables;
    SQL Server y Oracle no tienen fallback.

    NOTE: Driver fallback is strictly a legacy interactive UI helper for main_app.py.
    Canonical product paths (core.connectors, apps.dataviz_service) must never silently
    reinterpret or substitute product connector identity between Redshift and PostgreSQL.
    """
    if driver == DatabaseDriver.REDSHIFT:
        return [DatabaseDriver.POSTGRESQL]
    if driver == DatabaseDriver.POSTGRESQL:
        return [DatabaseDriver.REDSHIFT]
    return []


def _driver_token(entry: str | DatabaseDriver) -> str:
    """Normaliza una entrada de driver a su token canónico en minúsculas."""
    return entry.value if isinstance(entry, DatabaseDriver) else str(entry).lower().strip()


def connect_with_fallback(
    config: dict,
    drivers: Sequence[str | DatabaseDriver],
    probe_query: str = "SELECT 1",
) -> tuple[dict, str, list[dict]]:
    """
    Intenta una query de prueba contra cada driver en orden.

    Por cada driver construye una copia de ``config`` con ese driver y ejecuta
    ``probe_query`` vía ``core.query_executor.execute_query``. La primera
    respuesta con estado ``success`` corta el recorrido; errores y excepciones
    se registran y se continúa con el siguiente driver.

    Retorna ``(config_del_driver_exitoso_o_original, driver_exitoso_o"",
    lista_de_errores [{"driver", "message"}])``. All error messages are sanitized.
    """
    # Import local para evitar ciclo de importación con core.query_executor.
    from core import query_executor

    errors: list[dict] = []
    for entry in drivers:
        token = _driver_token(entry)
        candidate = {**config, "driver": token}
        try:
            result = query_executor.execute_query(probe_query, candidate)
        except Exception as error:
            safe_msg, _ = sanitize_text(str(error), source="connect_with_fallback")
            errors.append({"driver": token, "message": safe_msg})
            continue
        if isinstance(result, Mapping) and result.get("status") == "success":
            return candidate, token, errors
        if isinstance(result, Mapping):
            message = str(result.get("message") or result.get("error") or "unknown probe failure")
        else:
            message = f"unexpected result type: {type(result).__name__}"
        safe_msg, _ = sanitize_text(message, source="connect_with_fallback")
        errors.append({"driver": token, "message": safe_msg})
    return config, "", errors


def test_connection(config: dict) -> tuple[bool, str]:
    """
    Prueba la conexión a la base de datos.
    Retorna (success, message) con sanitización de credenciales.
    """
    try:
        db_config = DatabaseConfig.from_dict(config)
        with DatabaseConnector(db_config) as conn:
            result = conn.execute_scalar("SELECT 1")
            if result == 1:
                return True, "Conexión exitosa"
            else:
                return False, "Conexión establecida pero respuesta inesperada"
    except ImportError as e:
        safe_msg, _ = sanitize_text(str(e), source="test_connection")
        return False, f"Driver no instalado: {safe_msg}"
    except Exception as e:
        safe_msg, _ = sanitize_text(str(e), source="test_connection")
        return False, f"Error de conexión: {safe_msg}"


# ============ CLI para pruebas ============
if __name__ == "__main__":
    import os
    import sys

    # Ejemplo de uso
    config = {
        "server": os.getenv("DB_SERVER", "localhost"),
        "port": int(os.getenv("DB_PORT", 5439)),
        "driver": "redshift",
        "database": os.getenv("DB_NAME", "dev_db"),
        "username": os.getenv("DB_USER", "admin"),
        "password": os.getenv("DB_PASSWORD", "secret"),
    }

    print("[TEST] Probando conexión a Redshift...")
    success, msg = test_connection(config)
    print(f"{'✅' if success else '❌'} {msg}")

    if success and len(sys.argv) > 1:
        schema = sys.argv[1]
        print(f"\n[TEST] Tablas en esquema '{schema}':")
        db_config = DatabaseConfig.from_dict(config)
        with DatabaseConnector(db_config) as conn:
            tables = conn.get_schema_tables(schema)
            for t in tables[:10]:
                print(f"  - {t}")
            if len(tables) > 10:
                print(f"  ... y {len(tables) - 10} más")
