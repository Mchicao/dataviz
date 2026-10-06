"""Honest connector capability registry and commercial connector roadmap.

Only connectors with a production/runtime implementation may advertise
``implementation=supported`` or operational query/pushdown/execution modes.
Roadmap entries are still useful for product planning and credential UX, but
they are structurally ``unsupported`` so callers cannot mistake them for an
available connector.
"""

from __future__ import annotations

from apps.dataviz_service.runtime.connectors.local import (
    CSV_CAPABILITY_CONTRACT,
    PARQUET_CAPABILITY_CONTRACT,
)
from core.connectors.excel import EXCEL_CAPABILITY_CONTRACT
from core.connectors.json import JSON_CAPABILITY_CONTRACT
from core.connectors.postgres import POSTGRESQL_CAPABILITY_CONTRACT
from core.contracts.connector import (
    SCHEMA_VERSION,
    AuthKind,
    AuthSupport,
    CapabilitySupport,
    ConnectorCapabilityContract,
    QueryKind,
    QuerySupport,
    SchemaDiscoveryLevel,
)


def _planned_source(
    connector_id: str,
    *,
    auth: tuple[AuthKind, ...],
    query_kind: QueryKind = QueryKind.NONE,
    description: str,
) -> ConnectorCapabilityContract:
    """Describe a roadmap connector without claiming runtime capabilities."""

    return ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id=connector_id,
        implementation=CapabilitySupport.UNSUPPORTED,
        schema_discovery=SchemaDiscoveryLevel.NONE,
        supported_types=(),
        auth=AuthSupport(kinds=auth),
        query=QuerySupport(kind=query_kind, support=CapabilitySupport.UNSUPPORTED),
        execution_modes=(),
        requires_network=False,
        description=description,
    )


TARGET_POSTGRESQL = POSTGRESQL_CAPABILITY_CONTRACT
TARGET_SQLSERVER = _planned_source(
    "sqlserver",
    auth=(AuthKind.PASSWORD_REF, AuthKind.TOKEN_REF),
    query_kind=QueryKind.SQL,
    description="Roadmap: SQL Server connector; runtime implementation not available yet.",
)
TARGET_MYSQL = _planned_source(
    "mysql",
    auth=(AuthKind.PASSWORD_REF,),
    query_kind=QueryKind.SQL,
    description="Roadmap: MySQL connector; runtime implementation not available yet.",
)
TARGET_ORACLE = _planned_source(
    "oracle",
    auth=(AuthKind.PASSWORD_REF,),
    query_kind=QueryKind.SQL,
    description="Roadmap: Oracle connector; runtime implementation not available yet.",
)
TARGET_REDSHIFT = _planned_source(
    "redshift",
    auth=(AuthKind.PASSWORD_REF, AuthKind.TOKEN_REF),
    query_kind=QueryKind.SQL,
    description="Roadmap: Amazon Redshift connector; runtime implementation not available yet.",
)
TARGET_SNOWFLAKE = _planned_source(
    "snowflake",
    auth=(AuthKind.PASSWORD_REF, AuthKind.TOKEN_REF),
    query_kind=QueryKind.SQL,
    description="Roadmap: Snowflake connector; runtime implementation not available yet.",
)
TARGET_BIGQUERY = _planned_source(
    "bigquery",
    auth=(AuthKind.TOKEN_REF,),
    query_kind=QueryKind.SQL,
    description="Roadmap: BigQuery connector; runtime implementation not available yet.",
)

TARGET_EXCEL = EXCEL_CAPABILITY_CONTRACT
TARGET_JSON = JSON_CAPABILITY_CONTRACT
TARGET_S3 = _planned_source(
    "s3",
    auth=(AuthKind.TOKEN_REF,),
    description="Roadmap: S3/object-storage connector using opaque credential references.",
)
TARGET_REST_API = _planned_source(
    "rest_api",
    auth=(AuthKind.API_KEY_REF, AuthKind.TOKEN_REF),
    query_kind=QueryKind.API,
    description="Roadmap: bounded REST/API framework with opaque credentials; not implemented yet.",
)
TARGET_HTTP = _planned_source(
    "http",
    auth=(AuthKind.API_KEY_REF, AuthKind.TOKEN_REF),
    query_kind=QueryKind.API,
    description="Legacy roadmap alias for bounded HTTP APIs; not implemented yet.",
)
TARGET_DATABRICKS = _planned_source(
    "databricks",
    auth=(AuthKind.TOKEN_REF,),
    query_kind=QueryKind.SQL,
    description="Roadmap: Databricks SQL connector; runtime implementation not available yet.",
)
TARGET_ATHENA = _planned_source(
    "athena",
    auth=(AuthKind.TOKEN_REF,),
    query_kind=QueryKind.SQL,
    description="Roadmap: Amazon Athena connector; runtime implementation not available yet.",
)
TARGET_FABRIC_ONELAKE = _planned_source(
    "fabric_onelake",
    auth=(AuthKind.TOKEN_REF,),
    description="Roadmap: Microsoft Fabric/OneLake connector; runtime implementation not available yet.",
)

TARGET_SOURCE_CONTRACTS: dict[str, ConnectorCapabilityContract] = {
    "csv": CSV_CAPABILITY_CONTRACT,
    "parquet": PARQUET_CAPABILITY_CONTRACT,
    "postgresql": TARGET_POSTGRESQL,
    "sqlserver": TARGET_SQLSERVER,
    "mysql": TARGET_MYSQL,
    "oracle": TARGET_ORACLE,
    "redshift": TARGET_REDSHIFT,
    "snowflake": TARGET_SNOWFLAKE,
    "bigquery": TARGET_BIGQUERY,
    "excel": TARGET_EXCEL,
    "json": TARGET_JSON,
    "s3": TARGET_S3,
    "http": TARGET_HTTP,
    "rest_api": TARGET_REST_API,
    "databricks": TARGET_DATABRICKS,
    "athena": TARGET_ATHENA,
    "fabric_onelake": TARGET_FABRIC_ONELAKE,
}


def to_registry_dict() -> dict[str, object]:
    """Serialize the registry deterministically for API/UI discovery."""

    return {
        "schema_version": SCHEMA_VERSION,
        "sources": {
            connector_id: contract.to_dict()
            for connector_id, contract in sorted(TARGET_SOURCE_CONTRACTS.items())
        },
    }


__all__ = [
    "TARGET_ATHENA",
    "TARGET_BIGQUERY",
    "TARGET_DATABRICKS",
    "TARGET_EXCEL",
    "TARGET_FABRIC_ONELAKE",
    "TARGET_HTTP",
    "TARGET_JSON",
    "TARGET_MYSQL",
    "TARGET_ORACLE",
    "TARGET_POSTGRESQL",
    "TARGET_REDSHIFT",
    "TARGET_REST_API",
    "TARGET_S3",
    "TARGET_SNOWFLAKE",
    "TARGET_SOURCE_CONTRACTS",
    "TARGET_SQLSERVER",
    "to_registry_dict",
]
