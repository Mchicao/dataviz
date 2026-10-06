"""Production MCP stdio transport for DataVIZ.

A stdio process represents exactly one already-authenticated OIDC principal.
The bearer token is read from process configuration, verified by the same
``JWTVerifier`` used by the HTTP service, and discarded after the immutable
``Principal`` is created.  Tool payloads never contain identity, tenant, roles,
scopes or credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from apps.dataviz_service.knowledge.postgres_registry import PostgresKnowledgeRegistry
from apps.dataviz_service.knowledge.read import KnowledgeReadService
from apps.dataviz_service.mcp.adapter import DataVizMCPAdapter, MCPToolError
from apps.dataviz_service.runtime.connectors.target_sources import to_registry_dict
from apps.dataviz_service.security.authorization import AuthorizationService
from apps.dataviz_service.security.identity import JWTVerifier
from apps.dataviz_service.storage.postgres import PostgresStore

MCP_SERVER_VERSION = "1.0.0"


@dataclass(frozen=True)
class MCPSettings:
    """Minimal production configuration for the isolated stdio process."""

    database_url: str = field(repr=False)
    jwks_by_issuer: dict[str, dict[str, Any]]
    audiences: tuple[str, ...]
    oidc_token: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> MCPSettings:
        jwks_raw = _secret("DATAVIZ_JWKS_JSON")
        try:
            jwks = json.loads(jwks_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("DATAVIZ_JWKS_JSON must be valid JSON") from exc
        if not isinstance(jwks, dict) or not jwks:
            raise ValueError("DATAVIZ_JWKS_JSON must map issuers to JWKS documents")
        normalized: dict[str, dict[str, Any]] = {}
        for issuer, document in jwks.items():
            if not isinstance(issuer, str) or not issuer.strip() or not isinstance(document, dict):
                raise ValueError("DATAVIZ_JWKS_JSON must map issuers to JWKS documents")
            normalized[issuer.strip()] = document
        audiences = tuple(
            dict.fromkeys(value.strip() for value in _secret("DATAVIZ_OIDC_AUDIENCES").split(",") if value.strip())
        )
        if not audiences or "*" in audiences:
            raise ValueError("DATAVIZ_OIDC_AUDIENCES must contain explicit values")
        return cls(
            database_url=_secret("DATAVIZ_DATABASE_URL"),
            jwks_by_issuer=normalized,
            audiences=audiences,
            oidc_token=_secret("DATAVIZ_MCP_OIDC_TOKEN"),
        )


def _secret(name: str) -> str:
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if bool(direct) == bool(file_name):
        raise ValueError(f"Set exactly one of {name} or {name}_FILE")
    value = Path(file_name).read_text(encoding="utf-8").strip() if file_name else direct
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def build_production_adapter(settings: MCPSettings) -> tuple[DataVizMCPAdapter, PostgresStore]:
    """Build the adapter from canonical OIDC/RBAC/PostgreSQL/Knowledge services."""

    verifier = JWTVerifier(
        jwks_by_issuer=settings.jwks_by_issuer,
        trusted_audiences=set(settings.audiences),
    )
    principal = verifier.verify(settings.oidc_token)
    # Exactly like the production HTTP API/worker, MCP refuses a privileged DB
    # login so PostgreSQL FORCE RLS remains an actual security boundary.
    store = PostgresStore(
        settings.database_url,
        require_restricted_runtime_login=True,
    )
    authorization = AuthorizationService(store)
    knowledge_repository = PostgresKnowledgeRegistry(store)
    knowledge = KnowledgeReadService(
        authorization=authorization,
        repository=knowledge_repository,
    )
    return (
        DataVizMCPAdapter(
            principal=principal,
            store=store,
            authorization=authorization,
            knowledge=knowledge,
            knowledge_repository=knowledge_repository,
            connector_registry=to_registry_dict,
        ),
        store,
    )


def _invoke(adapter: DataVizMCPAdapter, name: str, **kwargs: Any) -> Any:
    try:
        return adapter.call(name, **kwargs)
    except MCPToolError as exc:
        # Only the adapter's controlled code/message crosses the MCP protocol.
        raise RuntimeError(f"{exc.error_code}: {exc.message}") from None


def build_server(adapter: DataVizMCPAdapter) -> MCPServer:
    """Register the exact production tool allowlist on the official MCP SDK."""

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    additive_write = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    )

    server = MCPServer(
        name="dataviz",
        title="DataVIZ",
        description=(
            "Governed DataVIZ authoring adapter. Read canonical BI content and Knowledge, "
            "validate/preview typed operations, and request durable proposals."
        ),
        instructions=(
            "Mutations are proposals only. This server cannot approve, apply, publish, rollback, "
            "execute arbitrary SQL/Python, retrieve credentials, or modify RBAC."
        ),
        version=MCP_SERVER_VERSION,
    )

    @server.tool(
        name="inspect_project",
        title="Inspect project",
        description="Inspect the authorized canonical project head.",
        annotations=read_only,
    )
    def inspect_project(project_id: str) -> dict[str, Any]:
        return _invoke(adapter, "inspect_project", project_id=project_id)

    @server.tool(
        name="inspect_semantic_element",
        title="Inspect semantic element",
        description="Inspect one semantic entity/metric/parameter/filter/relationship by stable name.",
        annotations=read_only,
    )
    def inspect_semantic_element(project_id: str, target: str, name: str) -> dict[str, Any]:
        return _invoke(
            adapter,
            "inspect_semantic_element",
            project_id=project_id,
            target=target,
            name=name,
        )

    @server.tool(
        name="list_versions",
        title="List versions",
        description="List authorized durable version metadata.",
        annotations=read_only,
    )
    def list_versions(project_id: str) -> dict[str, Any]:
        return _invoke(adapter, "list_versions", project_id=project_id)

    @server.tool(
        name="knowledge_list_assets",
        title="List Knowledge assets",
        description="List authorized durable Knowledge assets bound to this project.",
        annotations=read_only,
    )
    def knowledge_list_assets(
        project_id: str,
        asset_kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return _invoke(
            adapter,
            "knowledge_list_assets",
            project_id=project_id,
            asset_kind=asset_kind,
            limit=limit,
            offset=offset,
        )

    @server.tool(
        name="knowledge_get_asset",
        title="Get Knowledge asset",
        description="Read one authorized durable Knowledge asset bound to this project.",
        annotations=read_only,
    )
    def knowledge_get_asset(project_id: str, asset_id: str) -> dict[str, Any]:
        return _invoke(
            adapter,
            "knowledge_get_asset",
            project_id=project_id,
            asset_id=asset_id,
        )

    @server.tool(
        name="knowledge_get_metric",
        title="Get governed metric",
        description="Read authorized metric governance, bounded lineage and prior insights.",
        annotations=read_only,
    )
    def knowledge_get_metric(project_id: str, metric_id: str) -> dict[str, Any]:
        return _invoke(
            adapter,
            "knowledge_get_metric",
            project_id=project_id,
            metric_id=metric_id,
        )

    @server.tool(
        name="connector_capabilities",
        title="Connector capabilities",
        description="Discover actual connector capabilities; roadmap sources remain unsupported.",
        annotations=read_only,
    )
    def connector_capabilities(project_id: str) -> dict[str, Any]:
        return _invoke(adapter, "connector_capabilities", project_id=project_id)

    @server.tool(
        name="validate_operations",
        title="Validate authoring operations",
        description="Validate a closed batch of typed Semantic/Presentation/Interaction operations.",
        annotations=read_only,
    )
    def validate_operations(
        project_id: str,
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            adapter,
            "validate_operations",
            project_id=project_id,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )

    @server.tool(
        name="preview_operations",
        title="Preview authoring operations",
        description="Preview typed operations against the current canonical head without applying them.",
        annotations=read_only,
    )
    def preview_operations(
        project_id: str,
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            adapter,
            "preview_operations",
            project_id=project_id,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )

    @server.tool(
        name="request_authoring_proposal",
        title="Request authoring proposal",
        description=(
            "Persist a pending typed authoring proposal for human review. Does not apply or publish."
        ),
        annotations=additive_write,
    )
    def request_authoring_proposal(
        project_id: str,
        message: str = "",
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return _invoke(
            adapter,
            "request_authoring_proposal",
            project_id=project_id,
            message=message,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )

    return server


def main() -> None:
    """Run the production MCP server over stdio only."""

    settings = MCPSettings.from_environment()
    adapter, store = build_production_adapter(settings)
    try:
        build_server(adapter).run(transport="stdio")
    finally:
        store.close()


__all__ = ["MCPSettings", "MCP_SERVER_VERSION", "build_production_adapter", "build_server", "main"]
