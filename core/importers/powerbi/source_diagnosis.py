"""Diagnóstico neutral de orígenes declarados en artefactos Power BI.

El importador nunca abre conexiones ni recibe credenciales. Este módulo sólo
extrae identidad y metadatos no secretos, clasifica continuidad de migración y
mapea de forma conservadora a los IDs del contrato canónico de conectores.

Compatibilidad: ``status`` conserva los estados históricos
``pending_credentials`` / ``declared``. Los campos nuevos ``connection_state``
y ``source_support`` distinguen entre un conector reconocido pendiente y un
tipo de fuente todavía no soportado, sin aproximarlo a otro origen.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.contracts.connector import SCHEMA_VERSION as CONNECTOR_SCHEMA_VERSION
from core.contracts.source_ast import NodeKind, SourceNode
from core.security.credential_safety import sanitize_json_value

STATUS_PENDING_CREDENTIALS = "pending_credentials"
STATUS_DECLARED = "declared"
CAUSE_CREDENTIALS_NOT_AVAILABLE = "credentials_not_available"
CAUSE_UNSUPPORTED_SOURCE_TYPE = "unsupported_source_type"

CONNECTION_STATE_PENDING = "pending_connection"
CONNECTION_STATE_DECLARED = "declared"
CONNECTION_STATE_UNSUPPORTED = "unsupported"

SOURCE_SUPPORT_RECOGNIZED = "recognized"
SOURCE_SUPPORT_UNSUPPORTED = "unsupported"
SOURCE_SUPPORT_NOT_APPLICABLE = "not_applicable"

EXTERNAL_CONNECTION_FUNCTIONS: frozenset[str] = frozenset(
    {
        "Sql.Database",
        "Sql.Databases",
        "Value.NativeQuery",
        "Odbc.DataSource",
        "Odbc.Query",
        "OleDb.DataSource",
        "OleDb.Query",
        "Web.Contents",
        "WebAction.Request",
        "File.Contents",
        "Folder.Files",
        "Folder.Contents",
        "SharePoint.Contents",
        "SharePoint.Files",
        "SharePoint.Tables",
        "Snowflake.Databases",
        "PostgreSQL.Database",
        "MySQL.Database",
        "Oracle.Database",
        "Teradata.Database",
        "IBM.Db2.Database",
        "AnalysisServices.Database",
        "AnalysisServices.Databases",
        "Salesforce.Data",
    }
)

_NON_CONNECTION_PARTITION_KINDS: frozenset[str] = frozenset(
    {"m", "calculated", "calculationgroup", "entity", "policyrange", ""}
)
_DOTTED_CALL_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)\s*\(")

#: Query-parameter names treated as credentials and always dropped from URLs
#: persisted in ``connection_ref``. Mirrors the key list of
#: :mod:`core.security.credential_safety` plus common auth parameter names.
_SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization",
        "sig",
        "sas_token",
        "signature",
    }
)


def _sanitize_url_identity(url: str) -> str:
    """Return ``url`` stripped of userinfo and credential query parameters.

    Keeps scheme, host, port, path fragment and benign query parameters so the
    endpoint identity stays useful, while URL-embedded credentials (the
    ``user:pass@host`` form and secret query parameters) can never survive.
    Non-URL values pass through untouched (the M literal may be a plain
    server/path whose colons must not be mangled).
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    # Drop userinfo entirely: even a username can be credential material and
    # the host/port already identify the endpoint.
    netloc = parts.hostname or ""
    if not netloc:
        return url
    if parts.port is not None:
        try:
            netloc = f"{netloc}:{parts.port}"
        except ValueError:
            # Out-of-range port literal: keep the raw host part only.
            pass
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, urlencode(kept), parts.fragment))


def _sanitized_ref_value(value: str) -> str:
    """Apply URL-identity sanitization to a single ``connection_ref`` value."""
    return _sanitize_url_identity(value) if "://" in value else value


def _find_connection_functions(m_text: str) -> list[str]:
    found = _DOTTED_CALL_RE.findall(m_text or "")
    return list(dict.fromkeys(fn for fn in found if fn in EXTERNAL_CONNECTION_FUNCTIONS))


def _first_string_literals(fn: str, m_text: str, count: int = 2) -> list[str]:
    match = re.search(re.escape(fn) + r"\s*\(", m_text or "")
    if match is None:
        return []
    tail = m_text[match.end() :]
    literals: list[str] = []
    for lit in re.findall(r'"((?:[^"]|"")*)"', tail):
        literals.append(lit.replace('""', '"'))
        if len(literals) >= count:
            break
    return literals


def _connection_ref(fn: str, m_text: str) -> dict[str, str] | None:
    """Extracts only non-secret connection identity from a known M call."""
    if fn in {"Sql.Database", "PostgreSQL.Database"}:
        args = _first_string_literals(fn, m_text)
        if not args:
            return None
        ref: dict[str, str] = {"server": args[0]}
        if len(args) > 1:
            ref["database"] = args[1]
        return ref
    label = {
        "Web.Contents": "url",
        "WebAction.Request": "url",
        "File.Contents": "path",
        "SharePoint.Contents": "url",
        "SharePoint.Files": "url",
    }.get(fn, "ref")
    args = _first_string_literals(fn, m_text, count=1)
    return {label: args[0]} if args else None


def _safe_connection_ref(fn: str, m_text: str) -> dict[str, str] | None:
    """Connection identity with any secret-bearing literal redacted.

    ``_connection_ref`` may capture a credential passed inline in M (e.g. a
    password as the second ``Sql.Database`` argument or a tokenized URL).
    Redact at the source so even direct ``classify_partition`` callers never
    receive a secret value.
    """
    ref = _connection_ref(fn, m_text)
    if ref is None:
        return None
    ref = {key: _sanitized_ref_value(value) for key, value in ref.items()}
    safe_ref, _report = sanitize_json_value(ref, source="connection_ref")
    return safe_ref if isinstance(safe_ref, dict) else None


def _connector_for_file_ref(m_text: str) -> str | None:
    args = _first_string_literals("File.Contents", m_text, count=1)
    if not args:
        return None
    suffix = PurePosixPath(args[0].replace("\\", "/")).suffix.casefold()
    return {".csv": "csv", ".parquet": "parquet"}.get(suffix)


def _canonical_connector_id(functions: list[str], m_text: str) -> str | None:
    """Maps only unambiguous functions to current canonical contract IDs."""
    for fn in functions:
        if fn in {"Sql.Database", "Sql.Databases"}:
            return "sqlserver"
        if fn == "PostgreSQL.Database":
            return "postgresql"
        if fn in {"Web.Contents", "WebAction.Request"}:
            return "http"
        if fn == "File.Contents":
            return _connector_for_file_ref(m_text)
    return None


def _migration_fields(*, requires_connection: bool, connector_id: str | None) -> dict[str, Any]:
    if not requires_connection:
        return {
            "connector_id": None,
            "connector_contract_version": None,
            "source_support": SOURCE_SUPPORT_NOT_APPLICABLE,
            "connection_state": CONNECTION_STATE_DECLARED,
            "manual_review_required": False,
            "manual_review_cause": None,
        }
    if connector_id is not None:
        return {
            "connector_id": connector_id,
            "connector_contract_version": CONNECTOR_SCHEMA_VERSION,
            "source_support": SOURCE_SUPPORT_RECOGNIZED,
            "connection_state": CONNECTION_STATE_PENDING,
            "manual_review_required": True,
            "manual_review_cause": CAUSE_CREDENTIALS_NOT_AVAILABLE,
        }
    return {
        "connector_id": None,
        "connector_contract_version": None,
        "source_support": SOURCE_SUPPORT_UNSUPPORTED,
        "connection_state": CONNECTION_STATE_UNSUPPORTED,
        "manual_review_required": True,
        "manual_review_cause": CAUSE_UNSUPPORTED_SOURCE_TYPE,
    }


def classify_partition(
    *,
    name: str,
    table: str,
    source_kind: str,
    m_text: str,
) -> dict[str, Any]:
    """Clasifica una partición sin ejecutar M ni inventar acceso al origen."""
    kind = (source_kind or "").strip()
    functions = _find_connection_functions(m_text) if kind.lower() == "m" else []
    external_kind = bool(kind) and kind.lower() not in _NON_CONNECTION_PARTITION_KINDS
    requires_connection = external_kind or bool(functions)
    connector_id = _canonical_connector_id(functions, m_text) if functions else None
    first_identity_fn = next((fn for fn in functions if fn != "Value.NativeQuery"), "")

    entry: dict[str, Any] = {
        "datasource": f"{table}/{name}",
        "element": "partition",
        "table": table,
        "kind": kind,
        "requires_connection": requires_connection,
        "connection_functions": functions,
        "native_query": "Value.NativeQuery" in functions,
        "connection_ref": (
            _safe_connection_ref(first_identity_fn, m_text) if first_identity_fn else None
        ),
        **_migration_fields(requires_connection=requires_connection, connector_id=connector_id),
    }
    if requires_connection:
        entry["status"] = STATUS_PENDING_CREDENTIALS
        entry["cause"] = CAUSE_CREDENTIALS_NOT_AVAILABLE
    else:
        entry["status"] = STATUS_DECLARED
        entry["cause"] = None
    return entry


def _data_source_block_entry(node: SourceNode) -> dict[str, Any]:
    return {
        "datasource": node.name,
        "element": node.origin_tag,
        "table": None,
        "kind": str(node.attributes.get("kind", "")),
        "requires_connection": True,
        "connection_functions": [],
        "native_query": False,
        "connection_ref": None,
        "status": STATUS_PENDING_CREDENTIALS,
        "cause": CAUSE_CREDENTIALS_NOT_AVAILABLE,
        **_migration_fields(requires_connection=True, connector_id=None),
    }


def diagnose_sources(root: SourceNode) -> list[dict[str, Any]]:
    """Recorre el árbol importado y emite una entrada determinista por origen."""
    entries: list[dict[str, Any]] = []

    def visit(node: SourceNode) -> None:
        if node.origin_tag == "partition":
            entries.append(
                classify_partition(
                    name=node.name,
                    table=str(node.attributes.get("table", "")),
                    source_kind=str(node.attributes.get("source_kind", "")),
                    m_text=str(node.attributes.get("source_expression", "")),
                )
            )
        elif node.kind is NodeKind.UNKNOWN and node.origin_tag in (
            "structured_data_source",
            "provider_data_source",
            "data_source",
        ):
            entries.append(_data_source_block_entry(node))
        for child in node.children:
            visit(child)

    visit(root)
    return entries


__all__ = [
    "CAUSE_CREDENTIALS_NOT_AVAILABLE",
    "CAUSE_UNSUPPORTED_SOURCE_TYPE",
    "CONNECTION_STATE_DECLARED",
    "CONNECTION_STATE_PENDING",
    "CONNECTION_STATE_UNSUPPORTED",
    "EXTERNAL_CONNECTION_FUNCTIONS",
    "SOURCE_SUPPORT_NOT_APPLICABLE",
    "SOURCE_SUPPORT_RECOGNIZED",
    "SOURCE_SUPPORT_UNSUPPORTED",
    "STATUS_DECLARED",
    "STATUS_PENDING_CREDENTIALS",
    "classify_partition",
    "diagnose_sources",
]
