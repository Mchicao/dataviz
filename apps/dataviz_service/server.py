"""Aplicación ASGI productiva de DataVIZ."""

from __future__ import annotations

import dataclasses
import logging
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.status import (
    HTTP_403_FORBIDDEN,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from apps.dataviz_service.contracts import (
    AccessDeniedError,
    IdempotencyConflictError,
    Job,
    JobStatus,
    ResourceNotFoundError,
    SanitizedServiceError,
    Scope,
)
from apps.dataviz_service.governance.publication import PublicationService
from apps.dataviz_service.knowledge.read import DEFAULT_LIST_LIMIT, KnowledgeReadService
from apps.dataviz_service.runtime.connectors.target_sources import to_registry_dict
from apps.dataviz_service.runtime.errors import (
    OpaqueDAXExecutionUnavailableError,
    QueryInputTooLargeError,
    RuntimeMaterializationError,
    SemanticRLSUnavailableError,
)
from apps.dataviz_service.runtime.query_planner import PolicyViolationError, QueryPlanner
from apps.dataviz_service.security.authorization import (
    AuthorizationRepository,
    AuthorizationService,
    ResourceRef,
)
from apps.dataviz_service.security.identity import JWTVerifier, OIDCTokenError, Principal
from core.authoring.interaction_operations import InteractionOperation
from core.authoring.operations import Operation
from core.authoring.presentation_operations import PresentationOperation
from core.contracts.dataset import Dataset
from core.contracts.knowledge import AssetKind
from core.contracts.permissions import AuthoringAction, Permission
from core.contracts.query_ast import QuerySpec

logger = logging.getLogger("dataviz_service.server")

#: The only job type accepted by the API in this slice. Job routing happens at
#: the worker dispatcher boundary; the request body cannot name a handler.
JOB_TYPE_TABLEAU_TO_PBIP = "TABLEAU_TO_PBIP"
JOB_TYPE_POWER_BI_IMPORT = "POWER_BI_IMPORT"


class RuntimeStore(Protocol):
    """Puerto mínimo que necesita la API de consumo."""

    def ready(self) -> bool: ...

    def can_read_version(self, tenant_id: str, principal_id: str, version_id: str) -> bool: ...

    def can_run_version_jobs(
        self, tenant_id: str, principal_id: str, project_id: str, version_id: str
    ) -> bool: ...

    def get_runtime_payload(
        self, tenant_id: str, version_id: str, *, principal: Principal | None = None,
    ) -> dict[str, Any]: ...

    def consume_api_request(self, tenant_id: str) -> None: ...

    def execute_query(
        self, tenant_id: str, version_id: str, query: QuerySpec,
        *, principal: Principal | None = None,
    ) -> Dataset | dict[str, Any]: ...

    def enqueue_job(
        self,
        *,
        tenant_id: str,
        job_id: str,
        project_id: str,
        version_id: str,
        job_type: str,
        input_payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> tuple[Job, bool]: ...

    def get_job(self, tenant_id: str, job_id: str) -> Job: ...

    def cancel_job(self, tenant_id: str, job_id: str) -> bool: ...

    def get_authoring_state(self, tenant_id: str, project_id: str) -> dict[str, Any]: ...

    def apply_authoring_operations_to_draft(
        self,
        tenant_id: str,
        project_id: str,
        *,
        principal_id: str,
        idempotency_key: str,
        base_version: int,
        base_checksum: str,
        semantic_ops: list[Operation],
        presentation_ops: list[PresentationOperation],
        interaction_ops: list[InteractionOperation],
        message: str = "",
        can_manage_rls: bool = False,
    ) -> Any: ...

    def rollback_authoring_to_draft(
        self,
        tenant_id: str,
        project_id: str,
        *,
        principal_id: str,
        idempotency_key: str,
        base_version: int,
        base_checksum: str,
        target_version: int,
        message: str = "",
        can_manage_rls: bool = False,
    ) -> Any: ...


class JobSubmitRequest(BaseModel):
    """Closed submission contract for staged migration inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_key: str = Field(max_length=512)
    source_name: str = Field(max_length=255)
    source_kind: Literal["tableau", "power_bi"] = "tableau"
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("source_key", "source_name")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("display_name")
    @classmethod
    def _blank_display_name_is_none(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()


class AuthoringDraftRequest(BaseModel):
    """Wire envelope for one CAS-bound typed authoring mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_version: int = Field(gt=0)
    base_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_ops: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    presentation_ops: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    interaction_ops: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    message: str = Field(default="", max_length=500)


class AuthoringRollbackRequest(BaseModel):
    """CAS-bound request to copy an earlier canonical three-IR version into a new draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_version: int = Field(gt=0)
    base_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_version: int = Field(gt=0)
    message: str = Field(default="", max_length=500)


class PublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_version: int = Field(gt=0)


class PublicationActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1, max_length=200)


class JsonModelEncoder:
    """Convierte contratos Pydantic anidados y dataclasses en valores JSON nativos."""

    @classmethod
    def encode(cls, value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if dataclasses.is_dataclass(value):
            return {
                field.name: cls.encode(getattr(value, field.name))
                for field in dataclasses.fields(value)
            }
        if hasattr(value, "to_dict"):
            return cls.encode(value.to_dict())
        if isinstance(value, dict):
            return {str(key): cls.encode(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls.encode(item) for item in value]
        return value


def create_app(
    *,
    store: RuntimeStore,
    verifier: JWTVerifier,
    allowed_origins: Sequence[str],
    allowed_hosts: Sequence[str],
    readiness_checks: Sequence[Callable[[], bool]] = (),
    knowledge: KnowledgeReadService | None = None,
) -> Starlette:
    """Construye la aplicación fail-closed con dependencias explícitas."""
    origins = tuple(origin for origin in allowed_origins if origin and origin != "*")
    hosts = tuple(host for host in allowed_hosts if host and host != "*")
    if not origins:
        raise ValueError("At least one explicit CORS origin is required")
    if not hosts:
        raise ValueError("At least one explicit trusted host is required")
    authorization = AuthorizationService(cast(AuthorizationRepository, store))
    publication = PublicationService(authorization, cast(Any, store), cast(Any, store))

    knowledge_service = knowledge
    if knowledge_service is None:
        # Production wiring: enable the read-only Knowledge surface only when
        # the store is the real PostgreSQL store. Test fakes stay unregistered,
        # so the routes are absent (404) unless the caller injects the service.
        from apps.dataviz_service.storage.postgres import PostgresStore

        if isinstance(store, PostgresStore):
            from apps.dataviz_service.knowledge.postgres_registry import PostgresKnowledgeRegistry

            knowledge_service = KnowledgeReadService(
                authorization=authorization,
                repository=PostgresKnowledgeRegistry(store),
            )

    def project_resource(principal: Principal, project_id: str) -> ResourceRef:
        # Organizations are not yet first-class durable resources in the
        # platform schema. Tenant is therefore the server-derived top scope;
        # callers never supply this identity field.
        return ResourceRef(
            tenant_id=principal.tenant_id,
            organization_id=principal.tenant_id,
            project_id=project_id,
        )

    def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    def ready(_: Request) -> JSONResponse:
        if not store.ready() or not all(check() for check in readiness_checks):
            return JSONResponse({"status": "not_ready"}, status_code=HTTP_503_SERVICE_UNAVAILABLE)
        return JSONResponse({"status": "ready"})

    async def connector_registry(request: Request) -> Response:
        """Expose connector capabilities only; never configurations or secrets."""
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        return JSONResponse(to_registry_dict())

    def runtime(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        store.consume_api_request(principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        version_id = request.path_params["version_id"]
        if not store.can_read_version(principal.tenant_id, principal.id, version_id):
            # No distingue recurso inexistente de recurso ajeno: evita enumeración.
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        try:
            payload = store.get_runtime_payload(principal.tenant_id, version_id, principal=principal)
        except SemanticRLSUnavailableError:
            return _error(HTTP_403_FORBIDDEN, "SEMANTIC_RLS_UNAVAILABLE")
        except QueryInputTooLargeError:
            return _error(HTTP_413_CONTENT_TOO_LARGE, "QUERY_INPUT_TOO_LARGE")
        except OpaqueDAXExecutionUnavailableError:
            return _error(409, "OPAQUE_DAX_EXECUTION_UNAVAILABLE")
        except RuntimeMaterializationError:
            logger.exception("Persisted materialization invalid version_id=%s", version_id)
            return _error(500, "RUNTIME_MATERIALIZATION_ERROR")
        if payload.get("plan") is None or payload.get("results") is None:
            return _error(409, "RUNTIME_NOT_MATERIALIZED")
        # credential_scan es opcional: puede no existir si el job se ejecutó
        # antes de la integración de seguridad. Si existe, se reenvía al frontend.
        credential_scan = payload.get("credential_scan")
        body = {"plan": payload["plan"], "results": payload["results"]}
        if credential_scan is not None:
            body["credential_scan"] = credential_scan
        return JSONResponse(JsonModelEncoder.encode(body), headers={
            "Cache-Control": "private, no-store", "Vary": "Authorization",
        })

    async def query(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        # The psycopg2-backed store is synchronous: run every blocking call in
        # the threadpool so a slow query cannot stall unrelated requests that
        # share the event loop.
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        version_id = request.path_params["version_id"]
        if not await run_in_threadpool(
            store.can_read_version, principal.tenant_id, principal.id, version_id
        ):
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        try:
            raw_body = await request.json()
            if not isinstance(raw_body, dict):
                return _error(400, "INVALID_QUERY")
            query_spec = QuerySpec.from_dict(raw_body)
        except Exception:
            return _error(400, "INVALID_QUERY")

        try:
            planner = QueryPlanner()
            planned = planner.plan(query_spec)
        except PolicyViolationError:
            return _error(400, "POLICY_VIOLATION")

        if not hasattr(store, "execute_query"):
            return _error(409, "RUNTIME_NOT_MATERIALIZED")

        try:
            result = await run_in_threadpool(
                store.execute_query, principal.tenant_id, version_id, planned.query,
                principal=principal,
            )
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        except (NotImplementedError, AttributeError):
            return _error(409, "RUNTIME_NOT_MATERIALIZED")
        except SemanticRLSUnavailableError:
            return _error(HTTP_403_FORBIDDEN, "SEMANTIC_RLS_UNAVAILABLE")
        except QueryInputTooLargeError:
            return _error(HTTP_413_CONTENT_TOO_LARGE, "QUERY_INPUT_TOO_LARGE")
        except OpaqueDAXExecutionUnavailableError:
            return _error(409, "OPAQUE_DAX_EXECUTION_UNAVAILABLE")
        except RuntimeMaterializationError:
            logger.exception("Persisted materialization invalid version_id=%s", version_id)
            return _error(500, "RUNTIME_MATERIALIZATION_ERROR")
        except Exception as exc:
            if isinstance(exc, SanitizedServiceError):
                raise exc
            logger.exception("Query execution error version_id=%s", version_id)
            return _error(400, "QUERY_EXECUTION_ERROR")

        return JSONResponse(JsonModelEncoder.encode(result), headers={
            "Cache-Control": "private, no-store", "Vary": "Authorization",
        })

    async def authoring_state(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        project_id = request.path_params["project_id"]
        resource = project_resource(principal, project_id)
        try:
            await run_in_threadpool(authorization.authorize, principal, Permission.READ, resource)
            state = await run_in_threadpool(
                store.get_authoring_state, principal.tenant_id, project_id
            )
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        return JSONResponse(JsonModelEncoder.encode(state))

    async def create_authoring_draft(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.WRITE not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        idempotency_key = request.headers.get("idempotency-key", "").strip()
        if not idempotency_key or len(idempotency_key) > 255:
            return _error(400, "INVALID_IDEMPOTENCY_KEY")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return _error(400, "INVALID_AUTHORING_REQUEST")
            payload = AuthoringDraftRequest.model_validate(body)
            semantic_ops = [Operation.from_dict(item) for item in payload.semantic_ops]
            presentation_ops = [
                PresentationOperation.from_dict(item) for item in payload.presentation_ops
            ]
            interaction_ops = [
                InteractionOperation.from_dict(item) for item in payload.interaction_ops
            ]
        except (KeyError, TypeError, ValueError):
            return _error(400, "INVALID_AUTHORING_REQUEST")
        if not semantic_ops and not presentation_ops and not interaction_ops:
            return _error(400, "EMPTY_AUTHORING_REQUEST")
        project_id = request.path_params["project_id"]
        resource = project_resource(principal, project_id)
        await run_in_threadpool(
            authorization.authorize_action, principal, AuthoringAction.AUTHOR, resource
        )
        can_manage_rls = (
            authorization.is_authorized(principal, Permission.MANAGE_SECURITY, resource)
            or authorization.is_authorized(principal, Permission.MANAGE_RLS, resource)
        )
        try:
            version = await run_in_threadpool(
                store.apply_authoring_operations_to_draft,
                principal.tenant_id,
                project_id,
                principal_id=principal.id,
                idempotency_key=idempotency_key,
                base_version=payload.base_version,
                base_checksum=payload.base_checksum,
                semantic_ops=semantic_ops,
                presentation_ops=presentation_ops,
                interaction_ops=interaction_ops,
                message=payload.message,
                can_manage_rls=can_manage_rls,
            )
        except IdempotencyConflictError as exc:
            msg = str(exc)
            if "security capability" in msg.lower() or "rls" in msg.lower():
                return _error(HTTP_403_FORBIDDEN, "SEMANTIC_RLS_FORBIDDEN", msg)
            return _error(409, "AUTHORING_CONFLICT", msg)
        except (ValueError, KeyError, TypeError) as exc:
            return _error(400, "INVALID_AUTHORING_REQUEST", str(exc))
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        return JSONResponse(JsonModelEncoder.encode(version), status_code=201)

    async def rollback_authoring(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.WRITE not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        idempotency_key = request.headers.get("idempotency-key", "").strip()
        if not idempotency_key or len(idempotency_key) > 255:
            return _error(400, "INVALID_IDEMPOTENCY_KEY")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return _error(400, "INVALID_AUTHORING_ROLLBACK")
            payload = AuthoringRollbackRequest.model_validate(body)
        except (KeyError, TypeError, ValueError):
            return _error(400, "INVALID_AUTHORING_ROLLBACK")
        project_id = request.path_params["project_id"]
        resource = project_resource(principal, project_id)
        await run_in_threadpool(
            authorization.authorize_action, principal, AuthoringAction.REVERT, resource
        )
        can_manage_rls = (
            authorization.is_authorized(principal, Permission.MANAGE_SECURITY, resource)
            or authorization.is_authorized(principal, Permission.MANAGE_RLS, resource)
        )
        try:
            version = await run_in_threadpool(
                store.rollback_authoring_to_draft,
                principal.tenant_id,
                project_id,
                principal_id=principal.id,
                idempotency_key=idempotency_key,
                base_version=payload.base_version,
                base_checksum=payload.base_checksum,
                target_version=payload.target_version,
                message=payload.message,
                can_manage_rls=can_manage_rls,
            )
        except IdempotencyConflictError as exc:
            msg = str(exc)
            if "security capability" in msg.lower() or "rls" in msg.lower():
                return _error(HTTP_403_FORBIDDEN, "SEMANTIC_RLS_FORBIDDEN", msg)
            return _error(409, "AUTHORING_CONFLICT", msg)
        except (ValueError, KeyError, TypeError) as exc:
            return _error(400, "INVALID_AUTHORING_ROLLBACK", str(exc))
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        return JSONResponse(JsonModelEncoder.encode(version), status_code=201)

    async def request_publication(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.WRITE not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        try:
            raw = await request.json()
            payload = PublicationRequest.model_validate(raw)
        except Exception:
            return _error(400, "INVALID_PUBLICATION_REQUEST")
        project_id = request.path_params["project_id"]
        candidate = await run_in_threadpool(
            publication.request_publication,
            principal,
            project_resource(principal, project_id),
            target_version=payload.target_version,
        )
        return JSONResponse(JsonModelEncoder.encode(candidate), status_code=201)

    async def approve_publication(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.WRITE not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        approval = await run_in_threadpool(
            publication.approve, principal, request.path_params["candidate_id"]
        )
        return JSONResponse(JsonModelEncoder.encode(approval), status_code=201)

    async def activate_publication(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.WRITE not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        try:
            raw = await request.json()
            payload = PublicationActivationRequest.model_validate(raw)
        except Exception:
            return _error(400, "INVALID_PUBLICATION_ACTIVATION")
        record = await run_in_threadpool(
            publication.publish,
            principal,
            request.path_params["candidate_id"],
            approval_id=payload.approval_id,
        )
        return JSONResponse(JsonModelEncoder.encode(record))


    async def submit_job(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        await run_in_threadpool(store.consume_api_request, principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.RUN not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        idempotency_key = request.headers.get("idempotency-key", "").strip()
        if not idempotency_key or len(idempotency_key) > 255:
            return _error(400, "INVALID_IDEMPOTENCY_KEY")
        try:
            raw_body = await request.json()
            if not isinstance(raw_body, dict):
                return _error(400, "INVALID_JOB_REQUEST")
            payload = JobSubmitRequest.model_validate(raw_body)
        except Exception:
            return _error(400, "INVALID_JOB_REQUEST")
        project_id = request.path_params["project_id"]
        version_id = request.path_params["version_id"]
        allowed = await run_in_threadpool(
            store.can_run_version_jobs,
            principal.tenant_id,
            principal.id,
            project_id,
            version_id,
        )
        if not allowed:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        job_type = (
            JOB_TYPE_POWER_BI_IMPORT
            if payload.source_kind == "power_bi"
            else JOB_TYPE_TABLEAU_TO_PBIP
        )
        job_payload = payload.model_dump(mode="json")
        job_payload.pop("source_kind", None)
        job, created = await run_in_threadpool(
            store.enqueue_job,
            tenant_id=principal.tenant_id,
            job_id=str(uuid.uuid4()),
            project_id=project_id,
            version_id=version_id,
            job_type=job_type,
            input_payload=job_payload,
            idempotency_key=idempotency_key,
        )
        return JSONResponse(JsonModelEncoder.encode(job), status_code=201 if created else 200)

    def job_status(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        store.consume_api_request(principal.tenant_id)
        if not principal.scopes & {Scope.READ, Scope.RUN, Scope.ADMIN}:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        job_id = request.path_params["job_id"]
        try:
            job = store.get_job(principal.tenant_id, job_id)
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        if not store.can_read_version(principal.tenant_id, principal.id, job.version_id):
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        return JSONResponse(JsonModelEncoder.encode(job))

    def cancel_job(request: Request) -> Response:
        principal = _authenticate(request, verifier)
        store.consume_api_request(principal.tenant_id)
        if Scope.ADMIN not in principal.scopes and Scope.RUN not in principal.scopes:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        job_id = request.path_params["job_id"]
        try:
            job = store.get_job(principal.tenant_id, job_id)
        except ResourceNotFoundError:
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        if not store.can_run_version_jobs(
            principal.tenant_id, principal.id, job.project_id, job.version_id
        ):
            return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
        if job.status == JobStatus.CANCELLED:
            return JSONResponse(JsonModelEncoder.encode(job))
        if not store.cancel_job(principal.tenant_id, job_id):
            return _error(409, "JOB_NOT_CANCELABLE")
        return JSONResponse(JsonModelEncoder.encode(store.get_job(principal.tenant_id, job_id)))

    async def sanitized_error_handler(_: Request, exc: Exception) -> JSONResponse:
        correlation_id = str(uuid.uuid4())
        if isinstance(exc, SanitizedServiceError):
            return JSONResponse(
                {"error": exc.error_code, "correlation_id": exc.correlation_id},
                status_code=exc.status_code,
            )
        logger.exception("Unhandled API error correlation_id=%s", correlation_id)
        return JSONResponse(
            {"error": "INTERNAL_SERVER_ERROR", "correlation_id": correlation_id},
            status_code=500,
        )

    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=list(hosts)),
        Middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            max_age=600,
        ),
    ]
    routes = [
        Route("/healthz", health, methods=["GET"]),
        Route("/readyz", ready, methods=["GET"]),
        Route("/api/connectors", connector_registry, methods=["GET"]),
        Route("/api/versions/{version_id:str}/runtime", runtime, methods=["GET"]),
        Route("/api/versions/{version_id:str}/query", query, methods=["POST"]),
        Route(
            "/api/projects/{project_id:str}/authoring",
            authoring_state,
            methods=["GET"],
        ),
        Route(
            "/api/projects/{project_id:str}/authoring/drafts",
            create_authoring_draft,
            methods=["POST"],
        ),
        Route(
            "/api/projects/{project_id:str}/authoring/rollback",
            rollback_authoring,
            methods=["POST"],
        ),
        Route(
            "/api/projects/{project_id:str}/publication-requests",
            request_publication,
            methods=["POST"],
        ),
        Route(
            "/api/publication-candidates/{candidate_id:str}/approve",
            approve_publication,
            methods=["POST"],
        ),
        Route(
            "/api/publication-candidates/{candidate_id:str}/activate",
            activate_publication,
            methods=["POST"],
        ),
        Route(
            "/api/projects/{project_id:str}/versions/{version_id:str}/jobs",
            submit_job,
            methods=["POST"],
        ),
        Route("/api/jobs/{job_id:str}", job_status, methods=["GET"]),
        Route("/api/jobs/{job_id:str}/cancel", cancel_job, methods=["POST"]),
    ]
    if knowledge_service is not None:
        async def knowledge_assets(request: Request) -> Response:
            """List tenant/project-authorized Knowledge assets (read-only)."""
            principal = _authenticate(request, verifier)
            await run_in_threadpool(store.consume_api_request, principal.tenant_id)
            if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            params = request.query_params
            raw_kind = params.get("asset_kind")
            try:
                asset_kind = AssetKind(raw_kind) if raw_kind else None
            except ValueError:
                return _error(400, "INVALID_ASSET_KIND")
            try:
                limit = int(params.get("limit", DEFAULT_LIST_LIMIT))
                offset = int(params.get("offset", "0"))
            except ValueError:
                return _error(400, "INVALID_PAGINATION")
            project_id = request.path_params["project_id"]
            resource = project_resource(principal, project_id)
            try:
                envelope = await run_in_threadpool(
                    knowledge_service.list_assets,
                    principal,
                    resource,
                    asset_kind=asset_kind,
                    limit=limit,
                    offset=offset,
                )
            except AccessDeniedError:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            except ValueError:
                return _error(400, "INVALID_PAGINATION")
            return JSONResponse(JsonModelEncoder.encode(envelope))

        async def knowledge_asset(request: Request) -> Response:
            """Fetch one tenant/project-authorized Knowledge asset (read-only)."""
            principal = _authenticate(request, verifier)
            await run_in_threadpool(store.consume_api_request, principal.tenant_id)
            if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            project_id = request.path_params["project_id"]
            resource = project_resource(principal, project_id)
            try:
                envelope = await run_in_threadpool(
                    knowledge_service.get_asset,
                    principal,
                    resource,
                    asset_id=request.path_params["asset_id"],
                )
            except AccessDeniedError:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            except ResourceNotFoundError:
                return _error(404, "RESOURCE_NOT_FOUND")
            return JSONResponse(JsonModelEncoder.encode(envelope))

        async def knowledge_metric(request: Request) -> Response:
            """Fetch metric governance with bounded depth-1 lineage (read-only)."""
            principal = _authenticate(request, verifier)
            await run_in_threadpool(store.consume_api_request, principal.tenant_id)
            if Scope.ADMIN not in principal.scopes and Scope.READ not in principal.scopes:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            project_id = request.path_params["project_id"]
            resource = project_resource(principal, project_id)
            try:
                envelope = await run_in_threadpool(
                    knowledge_service.get_metric,
                    principal,
                    resource,
                    metric_id=request.path_params["metric_id"],
                )
            except AccessDeniedError:
                return _error(HTTP_403_FORBIDDEN, "ACCESS_DENIED")
            except ResourceNotFoundError:
                return _error(404, "RESOURCE_NOT_FOUND")
            return JSONResponse(JsonModelEncoder.encode(envelope))

        routes.extend(
            [
                Route(
                    "/api/projects/{project_id:str}/knowledge/assets",
                    knowledge_assets,
                    methods=["GET"],
                ),
                Route(
                    "/api/projects/{project_id:str}/knowledge/assets/{asset_id:str}",
                    knowledge_asset,
                    methods=["GET"],
                ),
                Route(
                    "/api/projects/{project_id:str}/knowledge/metrics/{metric_id:str}",
                    knowledge_metric,
                    methods=["GET"],
                ),
            ]
        )
    return Starlette(
        debug=False,
        routes=routes,
        middleware=middleware,
        exception_handlers={
            SanitizedServiceError: sanitized_error_handler,
            Exception: sanitized_error_handler,
        },
    )


def _authenticate(request: Request, verifier: JWTVerifier) -> Principal:
    value = request.headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise OIDCTokenError()
    return verifier.verify(token.strip())


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status_code)
