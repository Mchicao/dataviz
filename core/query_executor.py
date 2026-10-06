"""Ruta única para ejecutar consultas AST y conservar su diagnóstico.

NOTE: Raw SQL string execution via execute_query(str, ...) is quarantined for
legacy/local demo compatibility only. Production services use canonical
connectors with QuerySpec AST.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Event
from typing import Any

from apps.dataviz_service.runtime.executor import QueryExecutor
from core.contracts.dataset import Dataset
from core.contracts.query_ast import QuerySpec
from core.contracts.semantic_ir import SemanticModel
from core.result_parser import parse_query_result
from core.security.credential_safety import sanitize_text


def _is_cancelled(cancel: Event | Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    return cancel.is_set() if isinstance(cancel, Event) else bool(cancel())


def _diagnostic(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _cancelled_result() -> dict[str, object]:
    """Return the stable response used for cooperative cancellation."""
    return {
        "status": "cancelled",
        "error": "query_cancelled",
        "diagnostics": [_diagnostic("query_cancelled", "La consulta fue cancelada.")],
    }


def execute_ast_query(
    query: QuerySpec,
    dataset: Dataset,
    *,
    model: SemanticModel | None = None,
    parameters: Mapping[str, Any] | None = None,
    cancel: Event | Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Ejecuta el AST compartido y devuelve una respuesta estable.

    La cancelación es cooperativa en los límites de ejecución. Las selecciones
    se convierten en predicados una sola vez antes de delegar al runtime.
    """
    if _is_cancelled(cancel):
        return _cancelled_result()

    effective_query = QuerySpec(
        from_datasource=query.from_datasource,
        select=query.select,
        filters=[*query.filters, *query.selections],
        group_by=query.group_by,
        sort_by=query.sort_by,
        limit=query.limit,
        # Parameters are already merged below, with runtime overrides taking
        # precedence over values serialized in the AST. Keeping the original
        # mapping here would make QueryExecutor apply the AST defaults again.
        parameters={},
        selections=[],
    )
    try:
        result = QueryExecutor(
            model=model,
            parameters=query.parameters,
            runtime_parameters=parameters,
        ).execute(effective_query, dataset)
    except Exception as exc:  # runtime errors become transport-safe diagnostics
        safe_msg, _ = sanitize_text(str(exc), source="query_execution")
        return {
            "status": "error",
            "error": "query_execution_error",
            "message": safe_msg,
            "diagnostics": [_diagnostic(type(exc).__name__, safe_msg)],
        }

    if _is_cancelled(cancel):
        return _cancelled_result()
    return parse_query_result(result)


def execute_query(
    query: QuerySpec | str,
    config: Mapping[str, Any],
    *,
    dataset: Dataset | None = None,
    model: SemanticModel | None = None,
    parameters: Mapping[str, Any] | None = None,
    cancel: Event | Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Compatibilidad mínima: AST unificado o SQL legado explícito.

    Raw SQL string queries are quarantined to single-statement read-only SELECT
    queries with transport-safe error sanitization.
    """
    if _is_cancelled(cancel):
        return _cancelled_result()
    if isinstance(query, QuerySpec):
        source = dataset or config.get("dataset")
        if not isinstance(source, Dataset):
            return {
                "status": "error",
                "error": "dataset_unavailable",
                "diagnostics": [
                    _diagnostic("dataset_unavailable", "El dataset de consulta no está disponible.")
                ],
            }
        return execute_ast_query(
            query,
            source,
            model=model or config.get("semantic_model"),
            parameters=parameters,
            cancel=cancel,
        )

    # Quarantine raw-SQL execution: enforce single-statement read-only SELECT queries.
    if isinstance(query, str):
        cleaned = query.strip()
        inner_no_trailing = cleaned.rstrip(";").strip()
        if ";" in inner_no_trailing or not cleaned:
            return {
                "status": "error",
                "error": "query_not_allowed",
                "message": "Raw SQL execution is quarantined to single-statement read-only SELECT queries.",
                "diagnostics": [
                    _diagnostic("query_not_allowed", "Raw SQL execution is quarantined to single-statement read-only SELECT queries.")
                ],
            }
        first_token = inner_no_trailing.split()[0].lower() if inner_no_trailing.split() else ""
        if first_token not in {"select", "with"}:
            return {
                "status": "error",
                "error": "read_only_violation",
                "message": f"Raw SQL statement '{first_token.upper()}' rejected: only read-only SELECT is allowed.",
                "diagnostics": [
                    _diagnostic("read_only_violation", f"Raw SQL statement '{first_token.upper()}' rejected: only read-only SELECT is allowed.")
                ],
            }

    try:
        import psycopg2
    except ImportError:
        return {
            "status": "error",
            "error": "psycopg2_unavailable",
            "message": "Librería psycopg2 no encontrada.",
            "diagnostics": [_diagnostic("psycopg2_unavailable", "Librería psycopg2 no encontrada.")],
        }

    conn = cursor = None
    try:
        conn = psycopg2.connect(
            host=config.get("server"),
            port=config.get("port", 5439),
            user=config.get("username"),
            password=config.get("password"),
            dbname=config.get("database"),
            connect_timeout=10,
        )
        cursor = conn.cursor()
        cursor.execute(query)
        if cursor.description:
            columns = [description[0] for description in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            return {
                "status": "success",
                "columns": columns,
                "data": rows,
                "row_count": len(rows),
                "diagnostics": [],
            }
        conn.commit()
        return {
            "status": "success",
            "message": "Query executed successfully (no rows)",
            "diagnostics": [],
        }
    except Exception as exc:
        safe_msg, _ = sanitize_text(str(exc), source="query_execution")
        return {
            "status": "error",
            "error": "query_execution_error",
            "message": safe_msg,
            "diagnostics": [_diagnostic(type(exc).__name__, safe_msg)],
        }
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def validate_columns_sql(
    schema: str, table: str, columns: list[str], config: dict[str, Any]
) -> dict[str, object]:
    """Genera y ejecuta un SELECT de validación para compatibilidad heredada."""
    cols = ", ".join(columns)
    return execute_query(f"SELECT {cols} FROM {schema}.{table} LIMIT 1", config)


__all__ = ["execute_ast_query", "execute_query", "validate_columns_sql"]
