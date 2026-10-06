"""PostgreSQL production connector slice behind the canonical connector plane.

Capabilities (runtime-backed, truthfully declared):

* **connection** -- network connection via ``psycopg2``. Credentials are an
  opaque :class:`~core.contracts.connector.CredentialRef`: the connector never
  receives, stores, marshals or logs the secret value. An injected resolver
  maps the ref to an ephemeral password at connect time and the connector drops
  it before any other call returns.
* **metadata discovery** -- bounded listing of schemas/tables/columns from
  ``information_schema`` (every statement carries a hard ``LIMIT``).
* **typed/bounded query** -- a :class:`~core.contracts.query_ast.QuerySpec` is
  planned through the existing safe :class:`QueryPlanner` and compiled to
  parameterized SQL. Raw SQL is structurally impossible: every identifier is
  allowlisted/quoted and every value is a ``%s`` bound parameter. Aggregation
  and any non-``field_ref`` expression tree are **fail-closed unsupported**,
  matching ``pushdown.aggregation=false``.

Credential policy (ADR-0001 / D008): the password is resolved, passed directly
to the driver as a keyword argument, and never retained. Runtime errors pass
through :func:`core.security.credential_safety.sanitize_text` before surfacing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import monotonic as _monotonic
from typing import Any, ClassVar

from apps.dataviz_service.runtime.connectors.local import (
    CancelToken,
    ConnectorLimits,
    _cancel_checker,
)
from apps.dataviz_service.runtime.query_planner import QueryPlanner
from core.contracts.connector import (
    SCHEMA_VERSION,
    AuthKind,
    AuthSupport,
    CapabilitySupport,
    ConnectorCapabilityContract,
    CredentialRef,
    ErrorCategory,
    ErrorSemantics,
    ExecutionMode,
    LimitsSupport,
    PushdownSupport,
    QueryKind,
    QuerySupport,
    SchemaDiscoveryLevel,
)
from core.contracts.query_ast import ALLOWED_OPS, QueryPolicy, QuerySpec
from core.contracts.semantic_ir import Expression
from core.security.credential_safety import sanitize_text

#: Result columns of the bounded column-discovery query.
_COLUMN_DISCOVERY_SQL = """
    SELECT column_name, data_type, udt_name
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position
    LIMIT %s
"""

#: Neutral type mapping for PostgreSQL ``information_schema`` ``data_type``.
_NEUTRAL_BY_PG_TYPE: dict[str, str] = {
    "boolean": "boolean",
    "bigint": "integer",
    "integer": "integer",
    "smallint": "integer",
    "serial": "integer",
    "bigserial": "integer",
    "numeric": "decimal",
    "decimal": "decimal",
    "real": "float",
    "double precision": "float",
    "date": "date",
    "timestamp without time zone": "datetime",
    "timestamp with time zone": "datetime",
    "time without time zone": "datetime",
    "character varying": "string",
    "character": "string",
    "text": "string",
    "name": "string",
    "uuid": "string",
    "json": "string",
    "jsonb": "string",
    "bytea": "binary",
    "ARRAY": "string",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""Strict unquoted SQL identifier allowlist (schema/table/column)."""

_LIKE_NEEDS_ESCAPE = re.compile(r"[\\%_]")

#: Default row cap applied by :meth:`PostgresConnector.query` when the caller
#: passes no explicit :class:`ConnectorLimits`. Bounded by default.
_DEFAULT_MAX_ROWS = 10_000


class PostgresConnectorError(Exception):
    """Controlled connector failure with backend and operation context.

    The message is sanitized and never contains credential values.
    """

    def __init__(self, message: str, *, backend: str = "postgresql", operation: str) -> None:
        super().__init__(f"{backend} {operation} failed: {message}")
        self.backend = backend
        self.operation = operation


class CredentialResolutionError(PostgresConnectorError):
    """Raised when the secret resolver fails or returns an unusable secret."""


class PostgresUnsupportedQueryError(PostgresConnectorError):
    """Raised when a QuerySpec cannot be compiled into the bounded SQL subset."""


def _sanitize(message: str) -> str:
    """Redact credential-shaped fragments from a driver/error message."""
    cleaned, _ = sanitize_text(message, source="postgres_connector")
    return cleaned


def _default_connect(config: PostgresConfig, password: str) -> Any:
    """Connect through psycopg2; the password travels as a keyword argument only."""
    import psycopg2

    return psycopg2.connect(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.username,
        password=password,
        connect_timeout=int(config.connect_timeout_seconds),
    )


@dataclass(frozen=True)
class PostgresConfig:
    """Non-secret connection identity.

    There is **no password field**. The secret lives in the credential manager
    and is referenced by :attr:`credential_ref` (opaque, restricted alphabet).
    """

    host: str
    port: int
    database: str
    username: str
    credential_ref: CredentialRef
    connect_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("PostgresConfig.host is required")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not (1 <= self.port <= 65535):
            raise ValueError(f"PostgresConfig.port must be 1..65535, got {self.port!r}")
        if not isinstance(self.database, str) or not self.database.strip():
            raise ValueError("PostgresConfig.database is required")
        if not isinstance(self.username, str) or not self.username.strip():
            raise ValueError("PostgresConfig.username is required")
        timeout = self.connect_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError(f"PostgresConfig.connect_timeout_seconds must be positive, got {timeout!r}")
        if not isinstance(self.credential_ref, CredentialRef):
            raise TypeError("PostgresConfig.credential_ref must be a CredentialRef")

    def to_dict(self) -> dict[str, Any]:
        """Serializable identity -- opaque ref, never a secret value."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "credential_ref": self.credential_ref.to_dict(),
            "connect_timeout_seconds": self.connect_timeout_seconds,
        }


@dataclass(frozen=True)
class PostgresColumnInfo:
    """One discovered column with its neutral type."""

    name: str
    neutral_type: str
    source_type: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "neutral_type": self.neutral_type, "source_type": self.source_type}


@dataclass(frozen=True)
class PostgresTableInfo:
    """One discovered table (or view) inside a schema."""

    schema_name: str
    name: str
    columns: tuple[PostgresColumnInfo, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": self.schema_name,
            "name": self.name,
            "columns": [column.to_dict() for column in self.columns],
        }


@dataclass(frozen=True)
class PostgresDiscovery:
    """Bounded metadata snapshot produced by :meth:`PostgresConnector.discover`."""

    tables: tuple[PostgresTableInfo, ...] = ()

    @property
    def table_count(self) -> int:
        return len(self.tables)

    def get_table(self, schema_name: str, name: str) -> PostgresTableInfo | None:
        for table in self.tables:
            if table.schema_name == schema_name and table.name == name:
                return table
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"tables": [table.to_dict() for table in self.tables]}


@dataclass(frozen=True)
class PostgresQueryResult:
    """Bounded query result: column order + normalized rows + stream statistics.

    Attributes:
        column_names: Output column order (select items, alias when present).
        rows: One plain dict per row, keyed by ``column_names``.
        stopped_reason: One of the canonical stop reasons (``complete``,
            ``row_limit``, ``time_limit``, ``cancelled``).
        elapsed_seconds: Wall-clock seconds consumed by the bounded read.
        rows_emitted: Total rows counted at runtime.
    """

    column_names: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    stopped_reason: str = "complete"
    elapsed_seconds: float = 0.0
    rows_emitted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_names": list(self.column_names),
            "rows": list(self.rows),
            "stopped_reason": self.stopped_reason,
            "elapsed_seconds": self.elapsed_seconds,
            "rows_emitted": self.rows_emitted,
        }


#: Callable that resolves an opaque ref to the ephemeral connection secret.
SecretResolver = Callable[[CredentialRef], str]


def _neutral_type(data_type: Any, udt_name: Any) -> str:
    """Map a PostgreSQL type onto the closed set of neutral types."""
    source = str(data_type or udt_name or "").lower().strip()
    return _NEUTRAL_BY_PG_TYPE.get(source, "string")


def _quote_identifier(identifier: str, *, label: str) -> str:
    """Validate and double-quote one SQL identifier (fail-closed)."""
    parts = identifier.split(".")
    if not parts or any(not _IDENTIFIER_RE.fullmatch(part) for part in parts):
        raise PostgresUnsupportedQueryError(
            f"invalid {label} identifier {identifier!r}; only [A-Za-z_][A-Za-z0-9_]* "
            "segments are allowed (no raw SQL)",
            operation="compile",
        )
    return ".".join(f'"{part}"' for part in parts)


def _quote_column(column: str) -> str:
    """Quote a single column name (no qualified segments accepted)."""
    if "." in column:
        raise PostgresUnsupportedQueryError(
            f"column reference {column!r} must be unqualified", operation="compile"
        )
    return _quote_identifier(column, label="column")


def _like_value_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so LIKE patterns stay literal."""
    return _LIKE_NEEDS_ESCAPE.sub(lambda m: "\\" + m.group(0), value)


def _expression_is_field_ref(expression: Expression) -> bool:
    return expression.kind == "field_ref" and not expression.children


def _compile_predicate(op: str, expression: Expression, values: Sequence[Any]) -> tuple[str, list[Any]]:
    """Compile one allowlisted predicate into SQL + bound params.

    ``values`` are always bound parameters -- never interpolated into SQL.
    """
    if op not in ALLOWED_OPS:
        raise PostgresUnsupportedQueryError(
            f"operator {op!r} is not in the allowlist {sorted(ALLOWED_OPS)}",
            operation="compile",
        )
    if not _expression_is_field_ref(expression):
        raise PostgresUnsupportedQueryError(
            "predicates must reference a plain field_ref", operation="compile"
        )
    column = _quote_column(expression.name)
    values_list = list(values)

    if op == "eq":
        return f"{column} = %s", values_list
    if op == "ne":
        return f"{column} <> %s", values_list
    if op == "gt":
        return f"{column} > %s", values_list
    if op == "gte":
        return f"{column} >= %s", values_list
    if op == "lt":
        return f"{column} < %s", values_list
    if op == "lte":
        return f"{column} <= %s", values_list
    if op in {"in", "not_in"}:
        placeholders = ", ".join(["%s"] * len(values_list))
        keyword = "IN" if op == "in" else "NOT IN"
        return f"{column} {keyword} ({placeholders})", values_list
    if op == "between":
        if len(values_list) != 2:
            raise PostgresUnsupportedQueryError("between requires exactly two values", operation="compile")
        return f"{column} BETWEEN %s AND %s", values_list
    if op == "is_null":
        return f"{column} IS NULL", []
    if op == "is_not_null":
        return f"{column} IS NOT NULL", []

    text_ops: dict[str, str] = {
        "contains": "LIKE",
        "not_contains": "NOT LIKE",
        "starts_with": "LIKE",
        "not_starts_with": "NOT LIKE",
        "ends_with": "LIKE",
        "not_ends_with": "NOT LIKE",
    }
    like_op = text_ops.get(op)
    if like_op is not None:
        if len(values_list) != 1 or values_list[0] is None:
            raise PostgresUnsupportedQueryError(f"{op} requires exactly one non-null value", operation="compile")
        escaped = _like_value_escape(str(values_list[0]))
        if op in {"contains", "not_contains"}:
            pattern = f"%{escaped}%"
        elif op in {"starts_with", "not_starts_with"}:
            pattern = f"{escaped}%"
        else:  # ends_with / not_ends_with
            pattern = f"%{escaped}"
        return f"{column} {like_op} %s ESCAPE '\\'", [pattern]
    raise PostgresUnsupportedQueryError(
        f"operator {op!r} is not supported by the postgres query compiler", operation="compile"
    )


def _output_column_names(spec: QuerySpec) -> tuple[str, ...]:
    """Deterministic output column names: alias when present, else field name."""
    names: list[str] = []
    for item in spec.select:
        if item.alias:
            names.append(item.alias)
        elif _expression_is_field_ref(item.expression):
            names.append(item.expression.name)
        else:
            names.append("column")
    return tuple(names)


class PostgresConnector:
    """Production PostgreSQL connector: bounded metadata + typed/bounded query.

    Args:
        config: Non-secret identity with an opaque ``credential_ref``.
        connection_factory: Overridable connect callable
            ``(config, password) -> connection``. Defaults to psycopg2; tests
            inject a fake to prove the secret never leaks.
    """

    kind = "postgresql"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="postgresql",
        implementation=CapabilitySupport.SUPPORTED,
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=("string", "boolean", "integer", "float", "decimal", "date", "datetime", "binary"),
        auth=AuthSupport(kinds=(AuthKind.PASSWORD_REF,)),
        query=QuerySupport(kind=QueryKind.SQL, support=CapabilitySupport.SUPPORTED, dialect="postgresql"),
        pushdown=PushdownSupport(projection=True, filter=True, aggregation=False, limit=True, dialect="postgresql"),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        error_semantics=ErrorSemantics(
            support=CapabilitySupport.SUPPORTED,
            categories=(
                ErrorCategory.AUTHENTICATION,
                ErrorCategory.AUTHORIZATION,
                ErrorCategory.NOT_FOUND,
                ErrorCategory.UNSUPPORTED,
                ErrorCategory.TIMEOUT,
                ErrorCategory.CANCELLED,
                ErrorCategory.UNAVAILABLE,
                ErrorCategory.SOURCE_ERROR,
            ),
            retryable=(ErrorCategory.TIMEOUT, ErrorCategory.UNAVAILABLE),
        ),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=True,
        description=(
            "PostgreSQL connector: password_ref auth via opaque CredentialRef, bounded "
            "metadata discovery and typed QuerySpec queries (parameterized SQL only)."
        ),
    )

    def __init__(
        self,
        config: PostgresConfig,
        *,
        connection_factory: Callable[[PostgresConfig, str], Any] | None = None,
    ) -> None:
        if not isinstance(config, PostgresConfig):
            raise TypeError("config must be a PostgresConfig")
        self._config = config
        self._connection_factory = connection_factory or _default_connect
        self._connection: Any = None
        self._planner = QueryPlanner()
        # The ephemeral secret is never retained anywhere on this object.
        self._secret_resolver: SecretResolver | None = None

    @property
    def config(self) -> PostgresConfig:
        """Non-secret configuration; the secret is never materialized here."""
        return self._config

    @property
    def capability_contract(self) -> ConnectorCapabilityContract:
        return self.CAPABILITIES

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self, secret_resolver: SecretResolver) -> PostgresConnector:
        """Open a connection resolving the opaque ref through ``secret_resolver``.

        The ephemeral password is passed straight to the driver as a keyword
        argument and is not retained on this object.
        """
        if self._connection is not None:
            return self
        if not callable(secret_resolver):
            raise CredentialResolutionError("secret_resolver must be a callable", operation="connect")
        self._secret_resolver = secret_resolver
        try:
            password = secret_resolver(self._config.credential_ref)
        except Exception as error:  # noqa: BLE001 - resolution failures are user-facing
            raise CredentialResolutionError(
                f"secret resolver failed for ref '{self._config.credential_ref.value}': {_sanitize(str(error))}",
                operation="connect",
            ) from error
        if not isinstance(password, str) or not password:
            raise CredentialResolutionError(
                f"secret resolver returned an unusable secret for ref '{self._config.credential_ref.value}'",
                operation="connect",
            )
        try:
            self._connection = self._connection_factory(self._config, password)
        except PostgresConnectorError:
            raise
        except Exception as error:  # noqa: BLE001 - driver failures are user-facing
            raise PostgresConnectorError(_sanitize(str(error)), operation="connect") from error
        finally:
            # The ephemeral password must not outlive the connect call.
            password = ""  # noqa: F841 - defensive drop
        return self

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise PostgresConnectorError(
                "connection is not established; call connect(secret_resolver) first", operation="query"
            )
        return self._connection

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        connection, self._connection = self._connection, None
        if connection is not None:
            method = getattr(connection, "close", None)
            if callable(method):
                method()

    def __enter__(self) -> PostgresConnector:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Metadata discovery (bounded)
    # ------------------------------------------------------------------

    def discover(
        self,
        *,
        schema_limit: int = 50,
        table_limit: int = 200,
        column_limit: int = 500,
    ) -> PostgresDiscovery:
        """List schemas, tables and columns with hard per-query limits."""
        for name, value in (
            ("schema_limit", schema_limit),
            ("table_limit", table_limit),
            ("column_limit", column_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int, got {value!r}")
        connection = self._require_connection()

        def fetch_all(sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute(sql, tuple(params))
                return list(cursor.fetchall())
            except Exception as error:  # noqa: BLE001 - driver failures are user-facing
                raise PostgresConnectorError(_sanitize(str(error)), operation="discover") from error
            finally:
                if cursor is not None:
                    cursor.close()

        schemas = [
            row[0]
            for row in fetch_all(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'
                ORDER BY schema_name
                LIMIT %s
                """,
                (schema_limit,),
            )
        ]
        tables: list[PostgresTableInfo] = []
        for schema in schemas:
            rows = fetch_all(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                ORDER BY table_name
                LIMIT %s
                """,
                (schema, table_limit),
            )
            for (table_name,) in rows:
                columns: list[PostgresColumnInfo] = []
                for column_name, data_type, udt_name in fetch_all(
                    _COLUMN_DISCOVERY_SQL, (schema, table_name, column_limit)
                ):
                    columns.append(
                        PostgresColumnInfo(
                            name=str(column_name),
                            neutral_type=_neutral_type(data_type, udt_name),
                            source_type=str(data_type or udt_name or ""),
                        )
                    )
                tables.append(PostgresTableInfo(schema_name=str(schema), name=str(table_name), columns=tuple(columns)))
        return PostgresDiscovery(tables=tuple(tables))

    # ------------------------------------------------------------------
    # Typed/bounded query (QuerySpec -> parameterized SQL)
    # ------------------------------------------------------------------

    def plan(self, spec: QuerySpec, policy: QueryPolicy | None = None) -> Any:
        """Plan ``spec`` through the canonical safe :class:`QueryPlanner`.

        A caller-supplied policy is enforced with a per-call planner; otherwise
        the connector's default planner applies (raw guards on, no allowlists).
        """
        planner = QueryPlanner(policy) if policy is not None else self._planner
        return planner.plan(spec)

    def compile(self, spec: QuerySpec) -> tuple[str, tuple[Any, ...]]:
        """Compile a planned ``QuerySpec`` into parameterized PostgreSQL SQL.

        Raises:
            PostgresUnsupportedQueryError: For any AST shape outside the bounded
                subset (aggregations, raw expressions, functional select items,
                unknown operators, malformed identifiers).

        The returned SQL contains **no user-supplied literals**: every value is
        a ``%s`` placeholder bound by the caller.
        """
        table = _quote_identifier(spec.from_datasource, label="datasource")
        if not spec.select:
            raise PostgresUnsupportedQueryError(
                "policy requires an explicit select list (SELECT * is not allowed)", operation="compile"
            )
        select_sql: list[str] = []
        for item in spec.select:
            if _expression_is_field_ref(item.expression):
                select_sql.append(_quote_column(item.expression.name))
            else:
                raise PostgresUnsupportedQueryError(
                    "only plain field_ref select items are supported "
                    "(aggregations and expressions are fail-closed unsupported)",
                    operation="compile",
                )
        where_clauses: list[str] = []
        params: list[Any] = []
        for predicate in [*spec.filters, *spec.selections]:
            fragment, fragment_params = _compile_predicate(
                predicate.op, predicate.expression, predicate.values
            )
            where_clauses.append(fragment)
            params.extend(fragment_params)

        sql = f"SELECT {', '.join(select_sql)} FROM {table}"
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        if spec.group_by:
            raise PostgresUnsupportedQueryError(
                "group_by is fail-closed unsupported (aggregation pushdown=false)", operation="compile"
            )
        order_clauses: list[str] = []
        for key in spec.sort_by:
            if not _expression_is_field_ref(key.expression):
                raise PostgresUnsupportedQueryError("sort_by must reference a plain field_ref", operation="compile")
            direction = "ASC" if key.direction == "asc" else "DESC"
            order_clauses.append(f"{_quote_column(key.expression.name)} {direction}")
        if order_clauses:
            sql += " ORDER BY " + ", ".join(order_clauses)
        if spec.limit is not None:
            if isinstance(spec.limit, bool) or not isinstance(spec.limit, int) or spec.limit < 0:
                raise PostgresUnsupportedQueryError(f"limit must be a non-negative int, got {spec.limit!r}", operation="compile")
            sql += " LIMIT %s"
            params.append(spec.limit)
        return sql, tuple(params)

    # ------------------------------------------------------------------
    # Execution (bounded streaming)
    # ------------------------------------------------------------------

    def query(
        self,
        spec: QuerySpec,
        *,
        policy: QueryPolicy | None = None,
        limits: ConnectorLimits | None = None,
        cancel: CancelToken | Callable[[], bool] | None = None,
    ) -> PostgresQueryResult:
        """Execute a typed query with bounded streaming.

        The plan compiles to parameterized SQL; rows are pulled in chunks
        (``fetchmany``), and row/time limits plus cooperative cancellation are
        enforced between chunks. Without explicit limits, a default row cap of
        :data:`_DEFAULT_MAX_ROWS` applies so the query stays bounded by default.
        """
        planned = self.plan(spec, policy)
        sql, params = self.compile(planned.query)
        connection = self._require_connection()
        effective_limits = limits or ConnectorLimits(max_rows=_DEFAULT_MAX_ROWS)
        cancelled = _cancel_checker(cancel)
        start = _monotonic()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            description = list(cursor.description or [])
            column_names = tuple(str(d[0]) for d in description)
            rows: list[dict[str, Any]] = []
            stop_reason = "complete"
            while True:
                if cancelled():
                    stop_reason = "cancelled"
                    break
                if effective_limits.max_seconds is not None and (
                    _monotonic() - start >= effective_limits.max_seconds
                ):
                    stop_reason = "time_limit"
                    break
                if effective_limits.max_rows is not None and len(rows) >= effective_limits.max_rows:
                    stop_reason = "row_limit"
                    break
                chunk = cursor.fetchmany(effective_limits.batch_size)
                if not chunk:
                    break
                remaining = (
                    None
                    if effective_limits.max_rows is None
                    else effective_limits.max_rows - len(rows)
                )
                if remaining is not None and len(chunk) > remaining:
                    chunk = chunk[:remaining]
                    stop_reason = "row_limit"
                for driver_row in chunk:
                    rows.append(dict(zip(column_names, driver_row, strict=False)))
                if remaining is not None and len(rows) >= effective_limits.max_rows:
                    stop_reason = "row_limit"
                    break
            return PostgresQueryResult(
                column_names=column_names,
                rows=tuple(rows),
                stopped_reason=stop_reason,
                elapsed_seconds=_monotonic() - start,
                rows_emitted=len(rows),
            )
        except PostgresConnectorError:
            raise
        except Exception as error:  # noqa: BLE001 - driver failures are user-facing
            raise PostgresConnectorError(_sanitize(str(error)), operation="query") from error
        finally:
            if cursor is not None:
                cursor.close()


#: Module-level alias (stable import surface for consumers/registry).
POSTGRESQL_CAPABILITY_CONTRACT = PostgresConnector.CAPABILITIES


__all__ = [
    "POSTGRESQL_CAPABILITY_CONTRACT",
    "CredentialResolutionError",
    "PostgresColumnInfo",
    "PostgresConfig",
    "PostgresConnector",
    "PostgresConnectorError",
    "PostgresDiscovery",
    "PostgresQueryResult",
    "PostgresTableInfo",
    "PostgresUnsupportedQueryError",
    "SecretResolver",
]
