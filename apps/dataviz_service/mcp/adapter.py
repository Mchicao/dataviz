"""Thin, authenticated MCP adapter over DataVIZ's existing authorities.

This module deliberately owns no content, identity, RBAC, proposal or Knowledge
state.  It derives every tenant/author from an already verified ``Principal``
and delegates durable reads/writes to the existing PostgreSQL-backed services.
The only write exposed here is creation of a *pending* authoring proposal.
Approval, apply, publish, rollback, SQL/code execution, secret retrieval and
RBAC mutation are structurally absent from the tool allowlist.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel

from apps.dataviz_service.agent_tools.proposal import Proposal
from apps.dataviz_service.contracts import (
    AccessDeniedError,
    IdempotencyConflictError,
    ResourceNotFoundError,
    SanitizedServiceError,
    Scope,
)
from apps.dataviz_service.governance.publication import publication_version_checksum
from apps.dataviz_service.knowledge.read import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    KnowledgeReadService,
)
from apps.dataviz_service.security.authorization import (
    AuthorizationService,
    Permission,
    ResourceRef,
)
from apps.dataviz_service.security.identity import Principal
from core.authoring.custom_visual_cost import estimate_presentation_operations
from core.authoring.diff import preview
from core.authoring.interaction_operations import InteractionOperation, materialize_interactions
from core.authoring.operations import Operation, materialize
from core.authoring.presentation_operations import PresentationOperation, materialize_presentation
from core.contracts.coherence import validate_cross_ir_coherence
from core.contracts.custom_visual import spec_from_properties
from core.contracts.interaction_ir import InteractionIR
from core.contracts.knowledge import AssetKind
from core.contracts.presentation_ir import (
    PagePresentation,
    PresentationIR,
    VisualIntentKind,
    VisualPresentation,
)
from core.contracts.semantic_ir import SemanticModel

logger = logging.getLogger("dataviz_service.mcp")

MCP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "inspect_project",
        "inspect_semantic_element",
        "list_versions",
        "knowledge_list_assets",
        "knowledge_get_asset",
        "knowledge_get_metric",
        "connector_capabilities",
        "validate_operations",
        "preview_operations",
        "request_authoring_proposal",
    }
)

# These identity/capability fields are always server-derived.  Keeping them out
# of every public tool signature already prevents normal MCP clients from
# sending them; this guard also rejects hand-crafted calls to ``adapter.call``.
_CALLER_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "organization_id",
        "actor",
        "author",
        "approver",
        "approver_id",
        "role",
        "roles",
        "scope",
        "scopes",
        "capability",
        "capabilities",
    }
)

# Server-derived cost and confirmation parameters. Custom-visual budgets and
# high-cost acknowledgement are computed server-side from the decoded
# operations; they are never accepted from an MCP caller.
_SERVER_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "render_unit_budget",
        "query_cell_budget",
        "acknowledge_high_cost",
        "acknowledgement",
        "acknowledgment",
        "confirm_high_cost",
    }
)

# Carriers that must never cross the MCP authoring boundary.  These are checked
# recursively before the canonical typed decoders run.  The typed Presentation
# and Interaction contracts additionally reject executable SQL/secret material
# embedded in nested property values.
_FORBIDDEN_CARRIER_KEYS: frozenset[str] = frozenset(
    {
        "raw_sql",
        "sql",
        "query",
        "native_query",
        "raw_query",
        "python_code",
        "script",
        "secret",
        "password",
        "token",
        "api_key",
        "access_token",
        "refresh_token",
        "connection_string",
        "dsn",
        "credentials",
    }
)

_OUTPUT_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "secret",
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "connection_string",
        "secret_access_key",
        "session_token",
        "credentials",
    }
)

_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|connection[_-]?string)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_USERINFO_RE = re.compile(r"://[^\s/@]+:[^\s/@]+@")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----", re.IGNORECASE)

_MAX_OPERATION_COUNT = 200
_MAX_MESSAGE_LENGTH = 500

_SEMANTIC_SECTION: dict[str, str] = {
    "entity": "entities",
    "metric": "metrics",
    "parameter": "parameters",
    "filter": "filters",
    "relationship": "relationships",
    "rls_intent": "rls_intents",
}


class MCPStore(Protocol):
    """Small existing-store surface needed by MCP; no approval/publication API."""

    def consume_api_request(self, tenant_id: str) -> None: ...

    def get_authoring_state(self, tenant_id: str, project_id: str) -> dict[str, Any]: ...

    def get_version(self, tenant_id: str, version_id: str) -> Any: ...

    def save_agent_proposal(self, proposal: Proposal) -> Proposal: ...


class MCPKnowledgeRepository(Protocol):
    """Read-only Knowledge repository extension used to bind assets to a project."""

    def get_asset_version_ref(self, tenant_id: str, asset_id: str) -> str | None: ...


class MCPToolError(SanitizedServiceError):
    """External-safe MCP error. Internal exception text is never copied here."""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        super().__init__(message, status_code=status_code, error_code=error_code)


@dataclass(frozen=True)
class _CanonicalHead:
    project_id: str
    version_id: str
    version_number: int
    checksum: str
    semantic: SemanticModel
    presentation: PresentationIR
    interaction: InteractionIR

    @property
    def doc_id(self) -> str:
        return self.presentation.doc_id


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return value


def _scan_forbidden_carriers(value: Any, *, path: str = "operations") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key.casefold() in _FORBIDDEN_CARRIER_KEYS:
                raise ValueError(f"{path} contains a forbidden carrier key")
            _scan_forbidden_carriers(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden_carriers(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        _SECRET_ASSIGNMENT_RE.search(value)
        or _USERINFO_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
    ):
        raise ValueError(f"{path} contains secret-bearing material")


def _assert_output_safe(value: Any) -> Any:
    """Validate JSON compatibility and fail closed on secret-bearing output keys."""

    payload = _json_safe(value)

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                folded = str(key).casefold()
                if folded in _OUTPUT_SECRET_KEYS:
                    raise MCPToolError(
                        "The tool result was blocked by the output safety policy.",
                        "MCP_OUTPUT_BLOCKED",
                        500,
                    )
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and (
            _SECRET_ASSIGNMENT_RE.search(item)
            or _USERINFO_RE.search(item)
            or _PRIVATE_KEY_RE.search(item)
        ):
            raise MCPToolError(
                "The tool result was blocked by the output safety policy.",
                "MCP_OUTPUT_BLOCKED",
                500,
            )

    walk(payload)
    # ``allow_nan=False`` is the final JSON-safety assertion for RPC output.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload


def _semantic_diff_view(diff: Any) -> dict[str, Any]:
    return {
        "is_empty": bool(diff.is_empty),
        "added": [{"target": change.target, "name": change.name} for change in diff.added],
        "removed": [{"target": change.target, "name": change.name} for change in diff.removed],
        "changed": [{"target": change.target, "name": change.name} for change in diff.changed],
    }


class DataVizMCPAdapter:
    """Authenticated, fail-closed adapter used by every MCP transport.

    ``principal`` must already have crossed the canonical OIDC verifier.  No tool
    accepts tenant, actor, role, scope or capability arguments.  Durable
    proposal creation delegates to ``PostgresStore.save_agent_proposal``;
    approval/apply/publication methods are intentionally absent from this class.
    """

    def __init__(
        self,
        *,
        principal: Principal,
        store: MCPStore,
        authorization: AuthorizationService,
        knowledge: KnowledgeReadService,
        knowledge_repository: MCPKnowledgeRepository,
        connector_registry: Callable[[], dict[str, Any]],
    ) -> None:
        if not isinstance(principal, Principal):
            raise TypeError("MCP principal must be a verified Principal")
        self._principal = principal
        self._store = store
        self._authorization = authorization
        self._knowledge = knowledge
        self._knowledge_repository = knowledge_repository
        self._connector_registry = connector_registry

    @property
    def principal(self) -> Principal:
        return self._principal

    def call(self, name: str, /, **kwargs: Any) -> Any:
        """Invoke exactly one allowlisted MCP tool and sanitize all external errors."""

        if not isinstance(name, str) or name not in MCP_TOOL_NAMES:
            raise MCPToolError("Tool is not available.", "MCP_TOOL_NOT_ALLOWED", 404)
        if set(kwargs) & _CALLER_IDENTITY_FIELDS:
            raise MCPToolError(
                "Caller-supplied identity or capability fields are not accepted.",
                "MCP_IDENTITY_SPOOF_REJECTED",
                400,
            )
        if set(kwargs) & _SERVER_DERIVED_FIELDS:
            raise MCPToolError(
                "Server-derived budget or confirmation parameters are not accepted.",
                "MCP_SERVER_DERIVED_REJECTED",
                400,
            )
        try:
            self._assert_principal_active()
            result = getattr(self, name)(**kwargs)
            return _assert_output_safe(result)
        except MCPToolError:
            raise
        except AccessDeniedError as exc:
            raise MCPToolError("Access denied.", "MCP_ACCESS_DENIED", 403) from exc
        except ResourceNotFoundError as exc:
            # Do not distinguish an inaccessible resource from an absent one.
            raise MCPToolError("Resource is not available.", "MCP_RESOURCE_UNAVAILABLE", 404) from exc
        except IdempotencyConflictError as exc:
            raise MCPToolError(
                "The request conflicts with the current durable state.",
                "MCP_STATE_CONFLICT",
                409,
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise MCPToolError("Invalid tool arguments.", "MCP_INVALID_ARGUMENT", 400) from exc
        except SanitizedServiceError as exc:
            raise MCPToolError("The request was rejected.", "MCP_REQUEST_REJECTED", 400) from exc
        except Exception as exc:  # noqa: BLE001 - external RPC boundary must sanitize everything
            logger.error("MCP tool %s failed with %s", name, type(exc).__name__)
            raise MCPToolError("The MCP tool failed.", "MCP_INTERNAL_ERROR", 500) from exc

    def _assert_principal_active(self) -> None:
        """Fail closed once the OIDC principal used to start stdio has expired."""

        expires_at = self._principal.expires_at
        if expires_at.tzinfo is None or expires_at.tzinfo.utcoffset(expires_at) is None:
            # ``JWTVerifier`` currently normalizes NumericDate claims with
            # ``utcfromtimestamp``.  Treat that legacy-naive value as UTC rather
            # than silently extending the session or changing the shared identity
            # contract from the MCP adapter.
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise MCPToolError(
                "Authentication has expired.",
                "MCP_AUTH_EXPIRED",
                401,
            )

    def _resource(self, project_id: str) -> ResourceRef:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id is required")
        clean = project_id.strip()
        return ResourceRef(
            tenant_id=self._principal.tenant_id,
            organization_id=self._principal.tenant_id,
            project_id=clean,
        )

    def _authorize(self, project_id: str, permission: Permission, *, write_scope: bool) -> ResourceRef:
        self._store.consume_api_request(self._principal.tenant_id)
        expected_scope = Scope.WRITE if write_scope else Scope.READ
        if Scope.ADMIN not in self._principal.scopes and expected_scope not in self._principal.scopes:
            raise AccessDeniedError()
        resource = self._resource(project_id)
        self._authorization.authorize(self._principal, permission, resource)
        return resource

    def _load_head(self, project_id: str, *, authoring: bool = False) -> _CanonicalHead:
        permission = Permission.AUTHOR if authoring else Permission.READ
        self._authorize(project_id, permission, write_scope=authoring)
        state = self._store.get_authoring_state(self._principal.tenant_id, project_id.strip())
        head = state.get("head")
        if not isinstance(head, Mapping):
            raise ValueError("authoring head is missing")
        semantic_raw = head.get("semantic")
        presentation_raw = head.get("presentation")
        interaction_raw = head.get("interaction")
        if not all(isinstance(value, Mapping) for value in (semantic_raw, presentation_raw, interaction_raw)):
            raise ValueError("canonical three-IR head is incomplete")
        semantic = SemanticModel.from_dict(semantic_raw)
        presentation = PresentationIR.from_dict(presentation_raw)
        interaction = InteractionIR.from_dict(interaction_raw)
        validate_cross_ir_coherence(semantic, presentation, interaction)
        checksum = head.get("checksum")
        version_id = head.get("id")
        version_number = head.get("version_number")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError("canonical head checksum is invalid")
        if not isinstance(version_id, str) or not version_id:
            raise ValueError("canonical head version id is invalid")
        if not isinstance(version_number, int) or isinstance(version_number, bool) or version_number < 1:
            raise ValueError("canonical head version number is invalid")
        derived = publication_version_checksum(
            semantic.to_dict(), presentation.to_dict(), interaction.to_dict()
        )
        if checksum != derived:
            raise IdempotencyConflictError("canonical head checksum mismatch")
        return _CanonicalHead(
            project_id=project_id.strip(),
            version_id=version_id,
            version_number=version_number,
            checksum=checksum,
            semantic=semantic,
            presentation=presentation,
            interaction=interaction,
        )

    @staticmethod
    def _decode_operations(
        semantic_ops: list[dict[str, Any]] | None,
        presentation_ops: list[dict[str, Any]] | None,
        interaction_ops: list[dict[str, Any]] | None,
    ) -> tuple[list[Operation], list[PresentationOperation], list[InteractionOperation]]:
        raw_groups: tuple[object, ...] = (
            semantic_ops or [],
            presentation_ops or [],
            interaction_ops or [],
        )
        if any(not isinstance(group, list) for group in raw_groups):
            raise TypeError("operation groups must be arrays")
        _scan_forbidden_carriers(raw_groups)
        total = sum(len(group) for group in raw_groups if isinstance(group, list))
        if total < 1:
            raise ValueError("at least one typed operation is required")
        if total > _MAX_OPERATION_COUNT:
            raise ValueError("too many operations")
        semantic = [Operation.from_dict(item) for item in raw_groups[0]]  # type: ignore[arg-type]
        presentation = [
            PresentationOperation.from_dict(item) for item in raw_groups[1]  # type: ignore[arg-type]
        ]
        for operation in presentation:
            if operation.kind == "remove":
                continue
            payload = operation.payload
            if isinstance(payload, VisualPresentation):
                visuals = (payload,)
            elif isinstance(payload, PagePresentation):
                visuals = payload.visuals
            else:
                continue
            if any(
                visual.intent == VisualIntentKind.CUSTOM_VISUAL
                and spec_from_properties(visual.properties) is None
                for visual in visuals
            ):
                raise ValueError(
                    "MCP-authored custom_visual operations require custom_visual_spec"
                )
        interaction = [
            InteractionOperation.from_dict(item) for item in raw_groups[2]  # type: ignore[arg-type]
        ]
        if any(operation.target == "rls_intent" for operation in semantic):
            raise AccessDeniedError("Agents cannot mutate semantic RLS policy.")
        return semantic, presentation, interaction

    @staticmethod
    def _candidate(
        head: _CanonicalHead,
        semantic_ops: list[Operation],
        presentation_ops: list[PresentationOperation],
        interaction_ops: list[InteractionOperation],
    ) -> tuple[SemanticModel, PresentationIR, InteractionIR, str]:
        semantic = materialize(head.semantic, semantic_ops)
        presentation = materialize_presentation(head.presentation, presentation_ops)
        interaction = materialize_interactions(head.interaction, interaction_ops)
        validate_cross_ir_coherence(semantic, presentation, interaction)
        checksum = publication_version_checksum(
            semantic.to_dict(), presentation.to_dict(), interaction.to_dict()
        )
        return semantic, presentation, interaction, checksum

    # --- read-only inspection -----------------------------------------

    def inspect_project(self, *, project_id: str) -> dict[str, Any]:
        head = self._load_head(project_id)
        model = head.semantic
        return {
            "project_id": head.project_id,
            "doc_id": head.doc_id,
            "head": {
                "id": head.version_id,
                "version_number": head.version_number,
                "checksum": head.checksum,
            },
            "semantic": {
                "name": model.name,
                "schema_version": model.schema_version,
                "counts": {
                    attribute: len(getattr(model, attribute))
                    for attribute in _SEMANTIC_SECTION.values()
                },
            },
            "presentation": {
                "schema_version": head.presentation.schema_version,
                "page_count": len(head.presentation.pages),
                "pages": [
                    {"page_id": page.page_id, "name": page.name, "visual_count": len(page.visuals)}
                    for page in head.presentation.pages
                ],
            },
            "interaction": {
                "schema_version": head.interaction.schema_version,
                "page_count": len(head.interaction.pages),
                "global_filter_count": len(head.interaction.global_filters),
            },
        }

    def inspect_semantic_element(self, *, project_id: str, target: str, name: str) -> dict[str, Any]:
        head = self._load_head(project_id)
        if target not in _SEMANTIC_SECTION:
            raise ValueError("unknown semantic target")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        section = getattr(head.semantic, _SEMANTIC_SECTION[target])
        element = next((item for item in section if item.name == name.strip()), None)
        if element is None:
            raise ResourceNotFoundError(target, name.strip())
        return {
            "project_id": head.project_id,
            "version_number": head.version_number,
            "target": target,
            "name": name.strip(),
            "data": element,
        }

    def list_versions(self, *, project_id: str) -> dict[str, Any]:
        self._authorize(project_id, Permission.READ, write_scope=False)
        state = self._store.get_authoring_state(self._principal.tenant_id, project_id.strip())
        history = state.get("history")
        if not isinstance(history, list):
            raise ValueError("version history is invalid")
        return {
            "project_id": project_id.strip(),
            "published_version": state.get("published_version"),
            "versions": [
                {
                    "id": row.get("id"),
                    "version_number": row.get("version_number"),
                    "checksum": row.get("checksum"),
                    "parent_version_id": row.get("parent_version_id"),
                    "created_by": row.get("created_by"),
                    "message": row.get("message"),
                    "created_at": row.get("created_at"),
                }
                for row in history
                if isinstance(row, Mapping)
            ],
        }

    # --- Knowledge: existing durable read service, project-filtered ---

    def _version_belongs_to_project(self, version_id: str | None, project_id: str) -> bool:
        if not version_id:
            return False
        try:
            version = self._store.get_version(self._principal.tenant_id, version_id)
        except ResourceNotFoundError:
            return False
        return getattr(version, "project_id", None) == project_id

    def knowledge_list_assets(
        self,
        *,
        project_id: str,
        asset_kind: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List a project-scoped page without leaking tenant-wide pagination semantics.

        ``KnowledgeReadService`` deliberately exposes tenant-scoped durable reads.
        MCP adds a narrower project authorization boundary, so filtering one
        already-paginated tenant page would be incorrect: an authorized project
        asset can sit behind foreign-project rows and disappear from the result.
        Scan bounded Knowledge pages through the existing read service until the
        requested project-relative page (plus one ``has_more`` sentinel) is known.
        No SQL or alternate Knowledge authority is introduced here.
        """

        resource = self._authorize(project_id, Permission.READ, write_scope=False)
        kind = None if asset_kind is None else AssetKind(asset_kind)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise TypeError("offset must be an integer")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        effective_limit = max(1, min(limit, MAX_LIST_LIMIT))
        wanted = offset + effective_limit + 1
        matches: list[Any] = []
        scan_offset = 0
        last_envelope = None

        while len(matches) < wanted:
            envelope = self._knowledge.list_assets(
                self._principal,
                resource,
                asset_kind=kind,
                limit=MAX_LIST_LIMIT,
                offset=scan_offset,
            )
            last_envelope = envelope
            for asset in envelope.items:
                version_id = self._knowledge_repository.get_asset_version_ref(
                    self._principal.tenant_id, asset.asset_id
                )
                if self._version_belongs_to_project(version_id, project_id.strip()):
                    matches.append(asset)
                    if len(matches) >= wanted:
                        break
            if len(matches) >= wanted or not envelope.has_more:
                break
            consumed = len(envelope.items)
            if consumed < 1:
                raise ValueError("knowledge pagination did not make progress")
            scan_offset += consumed

        if last_envelope is None:
            raise ValueError("knowledge listing did not return an envelope")
        page = matches[offset : offset + effective_limit]
        return {
            "schema_version": last_envelope.schema_version,
            "as_of": last_envelope.as_of,
            "project_id": project_id.strip(),
            "asset_kind": last_envelope.asset_kind,
            "limit": effective_limit,
            "offset": offset,
            "has_more": len(matches) > offset + effective_limit,
            "items": page,
        }

    def knowledge_get_asset(self, *, project_id: str, asset_id: str) -> dict[str, Any]:
        resource = self._authorize(project_id, Permission.READ, write_scope=False)
        version_id = self._knowledge_repository.get_asset_version_ref(
            self._principal.tenant_id, asset_id
        )
        if not self._version_belongs_to_project(version_id, project_id.strip()):
            raise ResourceNotFoundError("asset", asset_id)
        envelope = self._knowledge.get_asset(
            self._principal,
            resource,
            asset_id=asset_id,
        )
        return {
            "schema_version": envelope.schema_version,
            "as_of": envelope.as_of,
            "project_id": project_id.strip(),
            "asset": envelope.asset,
        }

    def knowledge_get_metric(self, *, project_id: str, metric_id: str) -> dict[str, Any]:
        resource = self._authorize(project_id, Permission.READ, write_scope=False)
        envelope = self._knowledge.get_metric(
            self._principal,
            resource,
            metric_id=metric_id,
        )
        version_id = envelope.metric.get("canonical_version_id")
        if not isinstance(version_id, str) or not self._version_belongs_to_project(
            version_id, project_id.strip()
        ):
            raise ResourceNotFoundError("metric", metric_id)
        return {
            "schema_version": envelope.schema_version,
            "as_of": envelope.as_of,
            "project_id": project_id.strip(),
            "metric": envelope.metric,
            "used_by": envelope.used_by,
            "business_rules": envelope.business_rules,
            "insights": envelope.insights,
            "lineage_depth": envelope.lineage_depth,
            "lineage_truncated": envelope.lineage_truncated,
        }

    def connector_capabilities(self, *, project_id: str) -> dict[str, Any]:
        self._authorize(project_id, Permission.READ, write_scope=False)
        return self._connector_registry()

    # --- typed authoring: validation/preview/proposal only ------------

    def _prepare_operations(
        self,
        project_id: str,
        *,
        semantic_ops: list[dict[str, Any]] | None,
        presentation_ops: list[dict[str, Any]] | None,
        interaction_ops: list[dict[str, Any]] | None,
    ) -> tuple[
        _CanonicalHead,
        list[Operation],
        list[PresentationOperation],
        list[InteractionOperation],
        SemanticModel,
        PresentationIR,
        InteractionIR,
        str,
    ]:
        head = self._load_head(project_id, authoring=True)
        semantic, presentation, interaction = self._decode_operations(
            semantic_ops, presentation_ops, interaction_ops
        )
        candidate_semantic, candidate_presentation, candidate_interaction, checksum = self._candidate(
            head, semantic, presentation, interaction
        )
        return (
            head,
            semantic,
            presentation,
            interaction,
            candidate_semantic,
            candidate_presentation,
            candidate_interaction,
            checksum,
        )

    def validate_operations(
        self,
        *,
        project_id: str,
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare_operations(
            project_id,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )
        head, semantic, presentation, interaction, *_, candidate_checksum = prepared
        return {
            "ok": True,
            "project_id": head.project_id,
            "base_version": head.version_number,
            "base_checksum": head.checksum,
            "candidate_checksum": candidate_checksum,
            "operation_counts": {
                "semantic": len(semantic),
                "presentation": len(presentation),
                "interaction": len(interaction),
            },
            "cost_report": estimate_presentation_operations(presentation).to_dict(),
        }

    def preview_operations(
        self,
        *,
        project_id: str,
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare_operations(
            project_id,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )
        head, semantic, presentation, interaction, *_, candidate_checksum = prepared
        semantic_diff = preview(head.semantic, semantic)
        return {
            "project_id": head.project_id,
            "base_version": head.version_number,
            "base_checksum": head.checksum,
            "candidate_checksum": candidate_checksum,
            "semantic_diff": _semantic_diff_view(semantic_diff),
            "presentation_operations": [operation.to_dict() for operation in presentation],
            "interaction_operations": [operation.to_dict() for operation in interaction],
            "cost_report": estimate_presentation_operations(presentation).to_dict(),
        }

    def request_authoring_proposal(
        self,
        *,
        project_id: str,
        message: str = "",
        semantic_ops: list[dict[str, Any]] | None = None,
        presentation_ops: list[dict[str, Any]] | None = None,
        interaction_ops: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(message, str) or len(message) > _MAX_MESSAGE_LENGTH:
            raise ValueError("invalid proposal message")
        prepared = self._prepare_operations(
            project_id,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
        )
        (
            head,
            semantic,
            presentation,
            interaction,
            candidate_semantic,
            candidate_presentation,
            candidate_interaction,
            _,
        ) = prepared
        proposal = Proposal(
            proposal_id=uuid4().hex,
            doc_id=head.doc_id,
            tenant_id=self._principal.tenant_id,
            project_id=head.project_id,
            base_version=head.version_number,
            base_checksum=head.checksum,
            head_checksum=head.checksum,
            message=message,
            ops=tuple(semantic),
            presentation_ops=tuple(presentation),
            interaction_ops=tuple(interaction),
            diff=preview(head.semantic, semantic),
            created_at=datetime.now(UTC).isoformat(),
            author=self._principal.id,
            candidate_semantic=candidate_semantic,
            candidate_presentation=candidate_presentation,
            candidate_interaction=candidate_interaction,
        )
        persisted = self._store.save_agent_proposal(proposal)
        return {
            "persisted": True,
            "cost_report": estimate_presentation_operations(presentation).to_dict(),
            **persisted.summary(),
        }


__all__ = ["DataVizMCPAdapter", "MCPStore", "MCPToolError", "MCP_TOOL_NAMES"]
