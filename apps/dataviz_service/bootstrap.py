"""ConfiguraciÃ³n de arranque explÃ­cita para API y migraciones DataVIZ."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.dataviz_service.job_dispatch import make_dispatcher, production_handlers
from apps.dataviz_service.job_worker import JobHandler
from apps.dataviz_service.security.identity import JWTVerifier
from apps.dataviz_service.server import create_app
from apps.dataviz_service.storage.postgres import PostgresStore
from apps.dataviz_service.storage.s3 import S3ObjectBackend


@dataclass(frozen=True)
class ServiceSettings:
    """ConfiguraciÃ³n productiva validada antes de aceptar trÃ¡fico."""

    database_url: str
    jwks_by_issuer: dict[str, dict[str, Any]]
    audiences: tuple[str, ...]
    cors_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_allow_http: bool = False
    port: int = 8000

    @classmethod
    def from_environment(cls) -> ServiceSettings:
        database_url = _secret("DATAVIZ_DATABASE_URL")
        jwks_raw = _secret("DATAVIZ_JWKS_JSON")
        try:
            jwks = json.loads(jwks_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("DATAVIZ_JWKS_JSON must be valid JSON") from exc
        if not isinstance(jwks, dict) or not jwks:
            raise ValueError("DATAVIZ_JWKS_JSON must map issuers to JWKS documents")
        audiences = _csv("DATAVIZ_OIDC_AUDIENCES")
        origins = _csv("DATAVIZ_CORS_ORIGINS")
        hosts = _csv("DATAVIZ_ALLOWED_HOSTS")
        s3_endpoint = _secret("DATAVIZ_S3_ENDPOINT_URL")
        s3_bucket = _secret("DATAVIZ_S3_BUCKET")
        s3_access_key = _secret("DATAVIZ_S3_ACCESS_KEY_ID")
        s3_secret_key = _secret("DATAVIZ_S3_SECRET_ACCESS_KEY")
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(
            database_url=database_url,
            jwks_by_issuer=jwks,
            audiences=audiences,
            cors_origins=origins,
            allowed_hosts=hosts,
            s3_endpoint_url=s3_endpoint,
            s3_bucket=s3_bucket,
            s3_access_key_id=s3_access_key,
            s3_secret_access_key=s3_secret_key,
            s3_allow_http=os.environ.get("DATAVIZ_S3_ALLOW_HTTP", "false").lower() == "true",
            port=port,
        )


def build_application(settings: ServiceSettings | None = None):
    """Construye la aplicaciÃ³n productiva y conserva el pool en su estado.

    The API server must run with a restricted runtime login
    (NOSUPERUSER NOBYPASSRLS); startup fails closed otherwise so RLS stays a
    real PostgreSQL authority boundary. Schema migrations use the separate
    admin path (``build_migration_store``).
    """
    effective = settings or ServiceSettings.from_environment()
    store = PostgresStore(
        effective.database_url, require_restricted_runtime_login=True
    )
    object_store = S3ObjectBackend(
        bucket=effective.s3_bucket,
        endpoint_url=effective.s3_endpoint_url,
        access_key_id=effective.s3_access_key_id,
        secret_access_key=effective.s3_secret_access_key,
        allow_http=effective.s3_allow_http,
    )
    verifier = JWTVerifier(
        jwks_by_issuer=effective.jwks_by_issuer,
        trusted_audiences=set(effective.audiences),
    )
    app = create_app(
        store=store,
        verifier=verifier,
        allowed_origins=effective.cors_origins,
        allowed_hosts=effective.allowed_hosts,
        readiness_checks=(object_store.ready,),
    )
    app.state.store = store
    app.state.object_store = object_store
    return app


@dataclass(frozen=True)
class WorkerSettings:
    """ConfiguraciÃ³n validada del proceso worker self-hosted.

    El worker no sirve HTTP ni verifica JWT: solo necesita PostgreSQL y S3.
    Los tenants son una allowlist explÃ­cita; descubrir tenants globalmente
    exigirÃ­a saltarse FORCE RLS, asÃ­ que una lista ausente o vacÃ­a falla
    cerrado en el arranque.
    """

    database_url: str
    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_allow_http: bool
    tenants: tuple[str, ...]
    pbi_tools_path: str | None = None
    idle_seconds: float = 5.0
    lease_seconds: int = 300
    heartbeat_seconds: int = 30

    @classmethod
    def from_environment(cls) -> WorkerSettings:
        tenants = _tenant_csv("DATAVIZ_WORKER_TENANTS")
        idle_seconds = float(os.environ.get("DATAVIZ_WORKER_IDLE_SECONDS", "5"))
        if not 0 < idle_seconds <= 60:
            raise ValueError("DATAVIZ_WORKER_IDLE_SECONDS must be in (0, 60]")
        pbi_tools_path = os.environ.get("DATAVIZ_PBI_TOOLS_PATH", "").strip() or None
        if pbi_tools_path is not None and not Path(pbi_tools_path).is_file():
            raise ValueError("DATAVIZ_PBI_TOOLS_PATH must point to an existing file")
        return cls(
            database_url=_secret("DATAVIZ_DATABASE_URL"),
            s3_endpoint_url=_secret("DATAVIZ_S3_ENDPOINT_URL"),
            s3_bucket=_secret("DATAVIZ_S3_BUCKET"),
            s3_access_key_id=_secret("DATAVIZ_S3_ACCESS_KEY_ID"),
            s3_secret_access_key=_secret("DATAVIZ_S3_SECRET_ACCESS_KEY"),
            s3_allow_http=os.environ.get("DATAVIZ_S3_ALLOW_HTTP", "false").lower() == "true",
            tenants=tenants,
            pbi_tools_path=pbi_tools_path,
            idle_seconds=idle_seconds,
        )


def build_worker(settings: WorkerSettings) -> tuple[PostgresStore, JobHandler]:
    """Compila PostgresStore + S3ObjectBackend + dispatcher de producciÃ³n.

    Like the API server, the worker must authenticate with a restricted
    runtime login (NOSUPERUSER NOBYPASSRLS) and fails closed otherwise.
    """
    store = PostgresStore(
        settings.database_url, require_restricted_runtime_login=True
    )
    object_store = S3ObjectBackend(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
        allow_http=settings.s3_allow_http,
    )
    return store, make_dispatcher(
        production_handlers(object_store, pbi_tools_path=settings.pbi_tools_path)
    )


@dataclass(frozen=True)
class MigrationSettings:
    """Administrative configuration available only to the migration process."""

    admin_database_url: str

    @classmethod
    def from_environment(cls) -> MigrationSettings:
        return cls(admin_database_url=_secret("DATAVIZ_ADMIN_DATABASE_URL"))


def build_migration_store(settings: MigrationSettings) -> PostgresStore:
    """Build the admin-only store used exclusively for schema migrations.

    There is deliberately no fallback to the runtime DSN: a deployment that
    does not provision a separate admin credential fails closed.
    """
    return PostgresStore(settings.admin_database_url)


def _tenant_csv(name: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(v.strip() for v in os.environ.get(name, "").split(",") if v.strip()))
    if not values or "*" in values:
        raise ValueError(f"{name} must list explicit tenant ids")
    return values


def _csv(name: str) -> tuple[str, ...]:
    values = tuple(value.strip() for value in _secret(name).split(",") if value.strip())
    if not values or "*" in values:
        raise ValueError(f"{name} must contain explicit values")
    return values


def _secret(name: str) -> str:
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if bool(direct) == bool(file_name):
        raise ValueError(f"Set exactly one of {name} or {name}_FILE")
    value = Path(file_name).read_text(encoding="utf-8").strip() if file_name else direct
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()
