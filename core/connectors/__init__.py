"""Production connector runtime slices behind the canonical connector plane.

The canonical capability contract lives in ``core.contracts.connector`` and the
shared bounded-streaming primitives live in the DataVIZ runtime connector
module. This package hosts **runtime implementations** that truthfully back the
``implementation=supported`` capability declarations:

* :mod:`core.connectors.postgres` -- PostgreSQL metadata discovery and a
  typed/bounded query path compiled from the safe ``QuerySpec`` AST (never raw
  SQL), with opaque :class:`~core.contracts.connector.CredentialRef`
  credentials resolved through an injected callback.
* :mod:`core.connectors.excel` -- deterministic sandboxed Excel ingestion
  (``openpyxl``, read-only, ``data_only``: no formulas or macros are executed).
* :mod:`core.connectors.json` -- deterministic sandboxed JSON array-of-objects
  ingestion.

Connectors whose runtime is not implemented here must stay **roadmap-only**
(``implementation=unsupported`` in their capability declarations) and are never
advertised by this package.
"""

from __future__ import annotations

from core.connectors.excel import (
    EXCEL_CAPABILITY_CONTRACT,
    ExcelConnectorError,
    LocalExcelConnector,
)
from core.connectors.json import (
    JSON_CAPABILITY_CONTRACT,
    JsonConnectorError,
    LocalJsonConnector,
)
from core.connectors.postgres import (
    POSTGRESQL_CAPABILITY_CONTRACT,
    CredentialResolutionError,
    PostgresColumnInfo,
    PostgresConfig,
    PostgresConnector,
    PostgresConnectorError,
    PostgresDiscovery,
    PostgresQueryResult,
    PostgresTableInfo,
    PostgresUnsupportedQueryError,
)

__all__ = [
    "EXCEL_CAPABILITY_CONTRACT",
    "JSON_CAPABILITY_CONTRACT",
    "POSTGRESQL_CAPABILITY_CONTRACT",
    "CredentialResolutionError",
    "ExcelConnectorError",
    "JsonConnectorError",
    "LocalExcelConnector",
    "LocalJsonConnector",
    "PostgresColumnInfo",
    "PostgresConfig",
    "PostgresConnector",
    "PostgresConnectorError",
    "PostgresDiscovery",
    "PostgresQueryResult",
    "PostgresTableInfo",
    "PostgresUnsupportedQueryError",
]
