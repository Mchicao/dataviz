"""Persistencia productiva PostgreSQL para la plataforma DataVIZ."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg2.errors import ForeignKeyViolation, UniqueViolation
from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from apps.dataviz_service.agent_tools.proposal import (
    Proposal,
    proposal_identity_payload,
    proposal_sha256,
)
from apps.dataviz_service.contracts import (
    IdempotencyConflictError,
    Job,
    JobStatus,
    Project,
    ResourceNotFoundError,
    Tenant,
    Version,
)
from apps.dataviz_service.governance.publication import (
    ApprovalRecord,
    PublicationApprovalError,
    PublicationCandidate,
    PublicationConflictError,
    PublicationKind,
    PublicationRecord,
    PublicationSnapshot,
    publication_diff_sha256,
    publication_version_checksum,
    publication_version_diff_payload,
    publication_version_payload,
)
from apps.dataviz_service.platform.contracts import QuotaExceededError
from apps.dataviz_service.runtime.errors import (
    QueryInputTooLargeError,
    RuntimeMaterializationError,
    SemanticRLSUnavailableError,
)
from apps.dataviz_service.runtime.executor import QueryExecutor
from apps.dataviz_service.runtime.materializer import materialize_runtime_results
from core.authoring.custom_visual_cost import estimate_presentation_operations
from core.authoring.interaction_operations import InteractionOperation, materialize_interactions
from core.authoring.operations import Operation, materialize
from core.authoring.presentation_operations import PresentationOperation, materialize_presentation
from core.compilers.render_plan import DataVIZRenderPlanCompiler, RenderPlan
from core.contracts.dataset import Dataset
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import PresentationIR
from core.contracts.query_ast import QuerySpec
from core.contracts.semantic_ir import SemanticModel

_MIGRATIONS = Path(__file__).with_name("migrations")
_MIGRATION_LOCK_NAME = "dataviz_schema_migrations"


def _migration_manifest() -> dict[int, str]:
    """Return the exact on-disk migration manifest keyed by numeric version."""
    manifest: dict[int, str] = {}
    for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:4])
        if version in manifest:
            raise RuntimeError(f"duplicate migration version {version:04d}")
        manifest[version] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


DEFAULT_MAX_QUERY_ROWS = 10_000


class PrivilegedDatabaseRoleError(RuntimeError):
    """The runtime/worker pool authenticated with a privileged PostgreSQL role."""


def _validate_cross_ir_consistency(
    semantic: SemanticModel,
    presentation: PresentationIR,
    interaction: InteractionIR,
) -> None:
    """Validate cross-IR referential integrity across the canonical three IRs."""
    if presentation.doc_id != interaction.doc_id:
        raise PublicationConflictError(
            "PresentationIR and InteractionIR belong to different documents."
        )
    pres_page_map = {p.page_id: p for p in presentation.pages}
    inter_page_ids = {p.page_id for p in interaction.pages}
    orphan_pages = inter_page_ids - set(pres_page_map.keys())
    if orphan_pages:
        raise ValueError(
            f"InteractionIR references nonexistent presentation page(s): {sorted(orphan_pages)}"
        )
    for inter_page in interaction.pages:
        pres_page = pres_page_map[inter_page.page_id]
        page_visual_ids = {v.visual_id for v in pres_page.visuals}
        for f in inter_page.filters:
            orphan_v = set(f.target_visual_ids) - page_visual_ids
            if orphan_v:
                raise ValueError(
                    f"Filter {f.filter_id!r} references nonexistent visual(s): {sorted(orphan_v)}"
                )
        for cf in inter_page.cross_filters:
            if cf.source_visual_id not in page_visual_ids:
                raise ValueError(
                    f"Cross filter references nonexistent source visual {cf.source_visual_id!r}"
                )
            orphan_cv = set(cf.target_visual_ids) - page_visual_ids
            if orphan_cv:
                raise ValueError(
                    f"Cross filter references nonexistent target visual(s): {sorted(orphan_cv)}"
                )
        for d in inter_page.drills:
            if d.target_visual_id not in page_visual_ids:
                raise ValueError(
                    f"Drill references nonexistent target visual {d.target_visual_id!r}"
                )
            if d.drill_through_page_id and d.drill_through_page_id not in pres_page_map:
                raise ValueError(
                    f"Drill references nonexistent drill_through_page_id {d.drill_through_page_id!r}"
                )
        for t in inter_page.tooltips:
            if t.source_visual_id not in page_visual_ids:
                raise ValueError(
                    f"Tooltip references nonexistent source visual {t.source_visual_id!r}"
                )

    available_names = (
        {f.name for e in semantic.entities for f in e.fields}
        | {m.name for m in semantic.metrics}
        | {p.name for p in semantic.parameters}
    )
    if available_names:
        for page in presentation.pages:
            for visual in page.visuals:
                for binding in visual.bindings:
                    if binding.field_name and binding.field_name not in available_names:
                        raise ValueError(
                            f"Visual {visual.visual_id!r} references nonexistent semantic field or metric {binding.field_name!r}"
                        )


def _publication_ir_payload(
    semantic_raw: Any, presentation_raw: Any, interaction_raw: Any
) -> tuple[dict[str, Any], str]:
    """Validate and canonicalize the exact three IRs that define a version."""
    if not all(isinstance(value, Mapping) for value in (semantic_raw, presentation_raw, interaction_raw)):
        raise PublicationConflictError(
            "The target version does not contain a complete canonical three-IR snapshot."
        )
    try:
        semantic = SemanticModel.from_dict(semantic_raw)
        presentation = PresentationIR.from_dict(presentation_raw)
        interaction = InteractionIR.from_dict(interaction_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationConflictError(
            "The target version contains an invalid canonical IR snapshot."
        ) from exc
    try:
        _validate_cross_ir_consistency(semantic, presentation, interaction)
    except (PublicationConflictError, ValueError) as exc:
        raise PublicationConflictError(
            f"Canonical IR cross-references are inconsistent: {exc}"
        ) from exc
    payload = publication_version_payload(
        semantic.to_dict(), presentation.to_dict(), interaction.to_dict()
    )
    checksum = publication_version_checksum(
        payload["semantic"], payload["presentation"], payload["interaction"]
    )
    return payload, checksum


def _candidate_identity(candidate: PublicationCandidate) -> tuple[Any, ...]:
    return (
        candidate.tenant_id,
        candidate.organization_id,
        candidate.project_id,
        candidate.target_version,
        candidate.expected_published_version,
        candidate.kind.value,
        candidate.target_checksum,
        candidate.diff_sha256,
        candidate.requested_by,
    )


def _authoring_request_sha256(
    *,
    project_id: str,
    base_version: int,
    base_checksum: str,
    semantic_ops: list[Operation],
    presentation_ops: list[PresentationOperation],
    interaction_ops: list[InteractionOperation],
    message: str,
) -> str:
    payload = {
        "project_id": project_id,
        "base_version": base_version,
        "base_checksum": base_checksum,
        "semantic_ops": [operation.to_dict() for operation in semantic_ops],
        "presentation_ops": [operation.to_dict() for operation in presentation_ops],
        "interaction_ops": [operation.to_dict() for operation in interaction_ops],
        "message": message,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _authoring_rollback_request_sha256(
    *,
    project_id: str,
    base_version: int,
    base_checksum: str,
    target_version: int,
    message: str,
) -> str:
    payload = {
        "action": "rollback",
        "project_id": project_id,
        "base_version": base_version,
        "base_checksum": base_checksum,
        "target_version": target_version,
        "message": message,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _load_semantic_model_for_analytics(raw_model: Any) -> SemanticModel | None:
    """Validate the canonical model and reject semantic RLS until it is enforceable."""
    if raw_model is None:
        return None
    if not isinstance(raw_model, Mapping):
        raise RuntimeMaterializationError("Persisted semantic model is invalid.")
    try:
        model = SemanticModel.from_dict(raw_model)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeMaterializationError("Persisted semantic model is invalid.") from exc
    if model.rls_intents:
        raise SemanticRLSUnavailableError(
            "Persisted semantic row-level security cannot be safely enforced."
        )
    return model


def _validate_persisted_canonical_triple(row: Mapping[str, Any]) -> None:
    """Revalidate the durable canonical three-IR checksum before serving reads.

    Reuses the canonical cross-IR coherence validator so a tampered triple
    fails closed even when the SQL write fence was bypassed by an admin.
    Rows predating the three-IR contract (semantic model without the full
    triple) keep the semantic-only validation of
    ``_load_semantic_model_for_analytics``.
    """
    if row["semantic_model"] is None:
        return
    if row["presentation_ir"] is None or row["interaction_ir"] is None:
        return
    _, checksum = _publication_ir_payload(
        row["semantic_model"], row["presentation_ir"], row["interaction_ir"]
    )
    if checksum != row["checksum"]:
        raise RuntimeMaterializationError(
            "Persisted canonical version checksum does not match its three-IR content."
        )


# Classifies the requested datasource and returns its payload in a single
# statement/snapshot (no count-then-fetch TOCTOU). CASE branches are evaluated
# lazily by PostgreSQL, so jsonb_array_length only runs once jsonb_typeof has
# proven `rows` is an array. The payload column stays SQL NULL unless the
# status is 'ready', so oversized or malformed rows never reach Python.
_QUERY_DATASET_SQL = """
SELECT v.semantic_model, v.presentation_ir, v.interaction_ir, v.checksum,
       c.status,
       CASE WHEN c.status = 'ready' THEN c.dataset END AS dataset
FROM versions v
CROSS JOIN LATERAL (
    SELECT
        CASE
            WHEN NOT (v.datasets ? %s) THEN 'missing'
            WHEN jsonb_typeof(v.datasets -> %s) <> 'object' THEN 'malformed'
            WHEN jsonb_typeof((v.datasets -> %s) -> 'rows') IS DISTINCT FROM 'array'
                THEN 'malformed'
            WHEN jsonb_array_length((v.datasets -> %s) -> 'rows') > %s THEN 'oversized'
            ELSE 'ready'
        END AS status,
        v.datasets -> %s AS dataset
    ) c
WHERE v.tenant_id = %s AND v.id = %s
"""


def _serialize_datasets(datasets: Mapping[str, Dataset] | None) -> dict[str, Any]:
    return {
        name: {
            "name": dataset.name,
            "columns": list(dataset.columns),
            "rows": [dict(row) for row in dataset.rows],
        }
        for name, dataset in (datasets or {}).items()
    }


def _deserialize_datasets(raw: Mapping[str, Any] | None) -> dict[str, Dataset]:
    if not raw:
        return {}
    return {
        name: Dataset.from_records(
            name=data.get("name", name),
            records=data.get("rows", []),
            columns=data.get("columns"),
        )
        for name, data in raw.items()
        if isinstance(data, Mapping)
    }


class PostgresStore:
    """Repositorio transaccional con aislamiento redundante por tenant.

    Cada transacción tenant-scoped configura ``app.tenant_id`` para activar
    RLS y cada consulta incluye además ``tenant_id`` explícitamente. El pool no
    conserva el contexto porque ``set_config(..., true)`` es local al commit.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_connections: int = 1,
        max_connections: int = 10,
        max_query_rows: int = DEFAULT_MAX_QUERY_ROWS,
        require_restricted_runtime_login: bool = False,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")
        if min_connections < 1 or max_connections < min_connections:
            raise ValueError("Invalid PostgreSQL pool bounds")
        if max_query_rows < 1:
            raise ValueError("max_query_rows must be positive")
        self._pool = ThreadedConnectionPool(min_connections, max_connections, dsn=dsn)
        self._max_query_rows = max_query_rows
        if require_restricted_runtime_login:
            try:
                self._assert_restricted_runtime_login()
            except Exception:
                self._pool.closeall()
                raise

    def _assert_restricted_runtime_login(self) -> None:
        """Fail startup unless the authenticated DB login is truly runtime-only.

        Direct role flags are insufficient: membership in a privileged role,
        ownership of the public schema/tables, or effective CREATE privileges
        can all recover DDL/RLS-bypass authority. The API/worker therefore
        rejects any such posture and requires a separate migration credential.
        """
        connection = self._pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls,
                              r.rolcreaterole, r.rolcreatedb, r.rolreplication,
                              has_schema_privilege(r.rolname, 'public', 'CREATE'),
                              has_database_privilege(r.rolname, current_database(), 'CREATE'),
                              EXISTS (
                                  SELECT 1 FROM pg_namespace n
                                  WHERE n.nspname='public' AND n.nspowner=r.oid
                              ),
                              EXISTS (
                                  SELECT 1
                                  FROM pg_class c
                                  JOIN pg_namespace n ON n.oid=c.relnamespace
                                  WHERE n.nspname='public'
                                    AND c.relkind IN ('r','p','v','m','S','f')
                                    AND c.relowner=r.oid
                              ),
                              EXISTS (
                                  SELECT 1 FROM pg_roles inherited
                                  WHERE inherited.oid <> r.oid
                                    AND (inherited.rolsuper
                                         OR inherited.rolbypassrls
                                         OR inherited.rolcreaterole
                                         OR inherited.rolcreatedb
                                         OR inherited.rolreplication)
                                    AND pg_has_role(r.rolname, inherited.oid, 'MEMBER')
                              )
                       FROM pg_roles r
                       WHERE r.rolname = session_user"""
                )
                row = cursor.fetchone()
        finally:
            self._pool.putconn(connection)
        if row is None:
            raise PrivilegedDatabaseRoleError("PostgreSQL session_user is not a known login role")
        (
            rolname,
            can_login,
            is_super,
            bypass_rls,
            create_role,
            create_db,
            replication,
            schema_create,
            database_create,
            owns_schema,
            owns_objects,
            privileged_membership,
        ) = row
        if (
            not can_login
            or is_super
            or bypass_rls
            or create_role
            or create_db
            or replication
            or schema_create
            or database_create
            or owns_schema
            or owns_objects
            or privileged_membership
        ):
            raise PrivilegedDatabaseRoleError(
                f"PostgreSQL role {rolname!r} is not a restricted runtime login; "
                "requires LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB "
                "NOREPLICATION, no privileged memberships/ownership, and no effective "
                "CREATE privilege. Use a separate admin DSN for migrations."
            )

    def close(self) -> None:
        """Cierra todas las conexiones del pool."""
        self._pool.closeall()

    @contextmanager
    def transaction(self, tenant_id: str) -> Iterator[Any]:
        """Abre una transacción RLS tenant-scoped y revierte ante cualquier error."""
        connection = self._pool.getconn()
        try:
            with connection:
                with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                    cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                    yield cursor
        finally:
            self._pool.putconn(connection)

    def ready(self) -> bool:
        """Check connectivity and exact migration-manifest agreement."""
        expected = _migration_manifest()
        connection = self._pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.schema_migrations')")
                if cursor.fetchone()[0] is None:
                    return False
                cursor.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema='public'
                             AND table_name='schema_migrations'
                             AND column_name='sha256'
                       )"""
                )
                if not cursor.fetchone()[0]:
                    return False
                cursor.execute("SELECT version, sha256 FROM schema_migrations ORDER BY version")
                actual = {int(version): sha256 for version, sha256 in cursor.fetchall()}
                return actual == expected
        except Exception:
            return False
        finally:
            self._pool.putconn(connection)

    def migrate(self) -> None:
        """Apply the exact numbered migration manifest under a DB advisory lock.

        Migration hashes are persisted once migration 0012 introduces the
        ``sha256`` column. Existing pre-0012 rows are backfilled exactly once;
        subsequent file drift fails closed instead of being silently accepted.
        """
        expected = _migration_manifest()
        paths = {int(path.name[:4]): path for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))}
        connection = self._pool.getconn()
        locked = False
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (_MIGRATION_LOCK_NAME,))
                locked = True
                cursor.execute("SELECT to_regclass('public.schema_migrations')")
                schema_exists = cursor.fetchone()[0] is not None
                applied: set[int] = set()
                has_hash_column = False
                if schema_exists:
                    cursor.execute("SELECT version FROM schema_migrations")
                    applied = {int(row[0]) for row in cursor.fetchall()}
                    cursor.execute(
                        """SELECT EXISTS (
                               SELECT 1 FROM information_schema.columns
                               WHERE table_schema='public'
                                 AND table_name='schema_migrations'
                                 AND column_name='sha256'
                           )"""
                    )
                    has_hash_column = bool(cursor.fetchone()[0])
                    if has_hash_column:
                        cursor.execute("SELECT version, sha256 FROM schema_migrations")
                        for version, digest in cursor.fetchall():
                            version = int(version)
                            expected_digest = expected.get(version)
                            if expected_digest is None:
                                raise RuntimeError(f"database contains unknown migration {version:04d}")
                            if digest is not None and digest != expected_digest:
                                raise RuntimeError(f"migration {version:04d} checksum drift detected")

                for version, path in paths.items():
                    if version in applied:
                        continue
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,))
                    if cursor.fetchone() is None:
                        raise RuntimeError(f"migration {version:04d} did not record completion")
                    applied.add(version)

                cursor.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema='public'
                             AND table_name='schema_migrations'
                             AND column_name='sha256'
                       )"""
                )
                has_hash_column = bool(cursor.fetchone()[0])
                if not has_hash_column:
                    raise RuntimeError("schema_migrations.sha256 is required after migration 0012")

                for version, digest in expected.items():
                    cursor.execute(
                        "UPDATE schema_migrations SET sha256=%s WHERE version=%s AND sha256 IS NULL",
                        (digest, version),
                    )
                    cursor.execute("SELECT sha256 FROM schema_migrations WHERE version=%s", (version,))
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError(f"migration {version:04d} is missing after migration run")
                    if row[0] != digest:
                        raise RuntimeError(f"migration {version:04d} checksum drift detected")

                cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
                actual_versions = {int(row[0]) for row in cursor.fetchall()}
                if actual_versions != set(expected):
                    raise RuntimeError("database migration set does not match the application manifest")
        finally:
            if locked:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_MIGRATION_LOCK_NAME,))
                except Exception:
                    pass
            connection.autocommit = False
            self._pool.putconn(connection)

    def get_tenant(self, tenant_id: str) -> Tenant:
        with self.transaction(tenant_id) as cursor:
            cursor.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Tenant", tenant_id)
        return Tenant.model_validate(row)

    def create_tenant(self, tenant: Tenant) -> Tenant:
        """Aprovisiona un tenant explícitamente; nunca se invoca desde una lectura."""
        with self.transaction(tenant.id) as cursor:
            cursor.execute(
                """INSERT INTO tenants(id, name, status, quotas, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (tenant.id, tenant.name, tenant.status, Json(tenant.quotas), tenant.created_at),
            )
        return tenant

    def list_projects(self, tenant_id: str) -> list[Project]:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT id, tenant_id, name, description, created_at FROM projects "
                "WHERE tenant_id = %s ORDER BY created_at, id",
                (tenant_id,),
            )
            rows = cursor.fetchall()
        return [Project.model_validate(row) for row in rows]

    def get_project(self, tenant_id: str, project_id: str) -> Project:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT id, tenant_id, name, description, created_at FROM projects "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, project_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Project", project_id)
        return Project.model_validate(row)

    def create_project(self, project: Project) -> Project:
        with self.transaction(project.tenant_id) as cursor:
            cursor.execute(
                """INSERT INTO projects(tenant_id, id, name, description, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    project.tenant_id,
                    project.id,
                    project.name,
                    project.description,
                    project.created_at,
                ),
            )
        return project

    def get_version(self, tenant_id: str, version_id: str) -> Version:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT id, project_id, version_number, checksum, is_active, created_at
                   FROM versions WHERE tenant_id = %s AND id = %s""",
                (tenant_id, version_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Version", version_id)
        return Version.model_validate(row)

    def create_version(
        self,
        tenant_id: str,
        version: Version,
        *,
        render_plan: Mapping[str, Any] | None = None,
        runtime_results: Mapping[str, Any] | None = None,
    ) -> Version:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """INSERT INTO versions(
                       tenant_id, project_id, id, version_number, checksum, is_active,
                       render_plan, runtime_results, created_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tenant_id,
                    version.project_id,
                    version.id,
                    version.version_number,
                    version.checksum,
                    version.is_active,
                    Json(render_plan) if render_plan is not None else None,
                    Json(runtime_results) if runtime_results is not None else None,
                    version.created_at,
                ),
            )
        return version

    def get_authoring_state(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        """Return the durable project head plus lightweight version history.

        The browser receives the canonical three IRs from PostgreSQL. RenderPlan
        and any legacy presentation snapshot are deliberately excluded because
        they are derived/cache state, not authoring authority.
        """
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT published_version FROM projects WHERE tenant_id=%s AND id=%s",
                (tenant_id, project_id),
            )
            project = cursor.fetchone()
            if project is None:
                raise ResourceNotFoundError("Project", project_id)
            cursor.execute(
                """SELECT id, version_number, checksum, semantic_model,
                          presentation_ir, interaction_ir, parent_version_id,
                          created_by, message, created_at
                   FROM versions
                   WHERE tenant_id=%s AND project_id=%s
                   ORDER BY version_number""",
                (tenant_id, project_id),
            )
            rows = cursor.fetchall()
        if not rows:
            raise ResourceNotFoundError("Project version", project_id)
        head = rows[-1]
        payload, checksum = _publication_ir_payload(
            head["semantic_model"], head["presentation_ir"], head["interaction_ir"]
        )
        if head["checksum"] != checksum:
            raise IdempotencyConflictError(
                "The durable authoring head checksum does not match its canonical three-IR content."
            )
        return {
            "project_id": project_id,
            "published_version": project["published_version"],
            "head": {
                "id": head["id"],
                "version_number": head["version_number"],
                "checksum": checksum,
                "semantic": payload["semantic"],
                "presentation": payload["presentation"],
                "interaction": payload["interaction"],
                "parent_version_id": head.get("parent_version_id"),
                "created_by": head.get("created_by"),
                "message": head.get("message"),
                "created_at": head["created_at"],
            },
            "history": [
                {
                    "id": row["id"],
                    "version_number": row["version_number"],
                    "checksum": row["checksum"],
                    "parent_version_id": row.get("parent_version_id"),
                    "created_by": row.get("created_by"),
                    "message": row.get("message"),
                    "created_at": row["created_at"],
                    **(
                        {
                            "semantic": _publication_ir_payload(
                                row["semantic_model"], row["presentation_ir"], row["interaction_ir"]
                            )[0]["semantic"],
                            "presentation": _publication_ir_payload(
                                row["semantic_model"], row["presentation_ir"], row["interaction_ir"]
                            )[0]["presentation"],
                            "interaction": _publication_ir_payload(
                                row["semantic_model"], row["presentation_ir"], row["interaction_ir"]
                            )[0]["interaction"],
                        }
                        if (
                            row.get("semantic_model") is not None
                            and row.get("presentation_ir") is not None
                            and row.get("interaction_ir") is not None
                        )
                        else {}
                    ),
                }
                for row in rows
            ],
        }

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
    ) -> Version:
        """CAS one typed human-authoring batch into a durable three-IR draft.

        A project-row lock serializes competing head mutations. Lost-response
        replay returns the same result through ``authoring_mutations``; the same
        idempotency key with different immutable content is a conflict. The
        ledger stores only a request hash and result ref, never a second IR.
        """
        if not principal_id.strip():
            raise IdempotencyConflictError("An authenticated author is required.")
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise IdempotencyConflictError("A valid authoring idempotency key is required.")
        if not isinstance(base_version, int) or isinstance(base_version, bool) or base_version < 1:
            raise IdempotencyConflictError("base_version must be a positive integer.")
        if len(base_checksum) != 64 or any(ch not in "0123456789abcdef" for ch in base_checksum):
            raise IdempotencyConflictError("base_checksum must be a lowercase SHA-256 hex string.")
        if len(message) > 500:
            raise IdempotencyConflictError("Authoring message must not exceed 500 characters.")
        if any(operation.target == "rls_intent" for operation in semantic_ops) and not can_manage_rls:
            raise IdempotencyConflictError(
                "General authoring cannot mutate semantic RLS policy without a dedicated security capability."
            )
        request_sha = _authoring_request_sha256(
            project_id=project_id,
            base_version=base_version,
            base_checksum=base_checksum,
            semantic_ops=semantic_ops,
            presentation_ops=presentation_ops,
            interaction_ops=interaction_ops,
            message=message,
        )
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT id FROM projects WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, project_id),
            )
            if cursor.fetchone() is None:
                raise ResourceNotFoundError("Project", project_id)

            cursor.execute(
                """SELECT request_sha256, result_version_id
                   FROM authoring_mutations
                   WHERE tenant_id=%s AND project_id=%s AND idempotency_key=%s""",
                (tenant_id, project_id, idempotency_key),
            )
            replay = cursor.fetchone()
            if replay is not None:
                if replay["request_sha256"] != request_sha:
                    raise IdempotencyConflictError(
                        "The authoring idempotency key is already bound to different immutable content."
                    )
                cursor.execute(
                    """SELECT id, project_id, version_number, checksum, is_active, created_at
                       FROM versions WHERE tenant_id=%s AND id=%s""",
                    (tenant_id, replay["result_version_id"]),
                )
                version_row = cursor.fetchone()
                if version_row is None:
                    raise IdempotencyConflictError(
                        "Authoring replay references a missing durable version."
                    )
                return Version.model_validate(version_row)

            cursor.execute(
                """SELECT * FROM versions
                   WHERE tenant_id=%s AND project_id=%s
                   ORDER BY version_number DESC LIMIT 1 FOR UPDATE""",
                (tenant_id, project_id),
            )
            head = cursor.fetchone()
            if head is None:
                raise ResourceNotFoundError("Project version", project_id)
            base_payload, authoritative_checksum = _publication_ir_payload(
                head["semantic_model"], head["presentation_ir"], head["interaction_ir"]
            )
            if (
                head["checksum"] != authoritative_checksum
                or head["version_number"] != base_version
                or base_checksum != authoritative_checksum
            ):
                raise IdempotencyConflictError("The authoring base is stale or has been tampered with.")

            semantic = materialize(SemanticModel.from_dict(base_payload["semantic"]), semantic_ops)
            presentation = materialize_presentation(
                PresentationIR.from_dict(base_payload["presentation"]), presentation_ops
            )
            interaction = materialize_interactions(
                InteractionIR.from_dict(base_payload["interaction"]), interaction_ops
            )
            _validate_cross_ir_consistency(semantic, presentation, interaction)
            candidate = publication_version_payload(
                semantic.to_dict(), presentation.to_dict(), interaction.to_dict()
            )
            candidate_checksum = publication_version_checksum(
                candidate["semantic"], candidate["presentation"], candidate["interaction"]
            )
            next_number = int(head["version_number"]) + 1
            draft_id = f"author-{hashlib.sha256(f'{project_id}:{idempotency_key}'.encode()).hexdigest()[:32]}"

            inherited_datasets = head.get("datasets")
            inherited_scan = head.get("credential_scan")

            # RenderPlan is derived from the canonical durable three-IR snapshot.
            # Never inherit a plan or runtime result produced for the parent version.
            compiled_plan_dict = DataVIZRenderPlanCompiler().compile(
                semantic, presentation, interaction
            ).body
            compiled_plan = RenderPlan.from_dict(compiled_plan_dict)
            inherited_plan = compiled_plan_dict
            inherited_results = None
            if inherited_datasets:
                try:
                    deserialized_ds = _deserialize_datasets(inherited_datasets)
                    inherited_results = materialize_runtime_results(
                        compiled_plan, semantic, deserialized_ds
                    )
                except RuntimeMaterializationError:
                    # Authoring remains valid even if inherited datasets cannot satisfy
                    # the new plan. Drop stale results instead of serving parent data.
                    inherited_results = None

            cursor.execute(
                """INSERT INTO versions(
                       tenant_id, project_id, id, version_number, checksum, is_active,
                       semantic_model, presentation_ir, interaction_ir,
                       parent_version_id, created_by, message, created_at,
                       render_plan, runtime_results, datasets, credential_scan
                   ) VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
                   RETURNING id, project_id, version_number, checksum, is_active, created_at""",
                (
                    tenant_id,
                    project_id,
                    draft_id,
                    next_number,
                    candidate_checksum,
                    Json(candidate["semantic"]),
                    Json(candidate["presentation"]),
                    Json(candidate["interaction"]),
                    head["id"],
                    principal_id,
                    message or None,
                    Json(inherited_plan) if inherited_plan is not None else None,
                    Json(inherited_results) if inherited_results is not None else None,
                    Json(inherited_datasets) if inherited_datasets is not None else None,
                    Json(inherited_scan) if inherited_scan is not None else None,
                ),
            )
            version_row = cursor.fetchone()
            cursor.execute(
                """INSERT INTO authoring_mutations(
                       tenant_id, project_id, idempotency_key, request_sha256,
                       base_version, base_checksum, result_version_id, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tenant_id,
                    project_id,
                    idempotency_key,
                    request_sha,
                    base_version,
                    base_checksum,
                    draft_id,
                    principal_id,
                ),
            )
            return Version.model_validate(version_row)

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
    ) -> Version:
        """Create a new durable draft whose three IRs equal an earlier version.

        History is append-only: rollback never rewrites or reactivates an old row.
        The current project head remains the CAS base and becomes the new draft's
        parent; the rollback target is copied only after its stored checksum is
        re-derived from the canonical three IRs.
        """
        if not principal_id.strip():
            raise IdempotencyConflictError("An authenticated author is required.")
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise IdempotencyConflictError("A valid authoring idempotency key is required.")
        if not isinstance(base_version, int) or isinstance(base_version, bool) or base_version < 1:
            raise IdempotencyConflictError("base_version must be a positive integer.")
        if not isinstance(target_version, int) or isinstance(target_version, bool) or target_version < 1:
            raise IdempotencyConflictError("target_version must be a positive integer.")
        if target_version >= base_version:
            raise IdempotencyConflictError("Rollback target must precede the current authoring base.")
        if len(base_checksum) != 64 or any(ch not in "0123456789abcdef" for ch in base_checksum):
            raise IdempotencyConflictError("base_checksum must be a lowercase SHA-256 hex string.")
        if len(message) > 500:
            raise IdempotencyConflictError("Authoring message must not exceed 500 characters.")
        request_sha = _authoring_rollback_request_sha256(
            project_id=project_id,
            base_version=base_version,
            base_checksum=base_checksum,
            target_version=target_version,
            message=message,
        )
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT id FROM projects WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, project_id),
            )
            if cursor.fetchone() is None:
                raise ResourceNotFoundError("Project", project_id)

            cursor.execute(
                """SELECT request_sha256, result_version_id
                   FROM authoring_mutations
                   WHERE tenant_id=%s AND project_id=%s AND idempotency_key=%s""",
                (tenant_id, project_id, idempotency_key),
            )
            replay = cursor.fetchone()
            if replay is not None:
                if replay["request_sha256"] != request_sha:
                    raise IdempotencyConflictError(
                        "The authoring idempotency key is already bound to different immutable content."
                    )
                cursor.execute(
                    """SELECT id, project_id, version_number, checksum, is_active, created_at
                       FROM versions WHERE tenant_id=%s AND id=%s""",
                    (tenant_id, replay["result_version_id"]),
                )
                version_row = cursor.fetchone()
                if version_row is None:
                    raise IdempotencyConflictError(
                        "Authoring replay references a missing durable version."
                    )
                return Version.model_validate(version_row)

            cursor.execute(
                """SELECT * FROM versions
                   WHERE tenant_id=%s AND project_id=%s
                   ORDER BY version_number DESC LIMIT 1 FOR UPDATE""",
                (tenant_id, project_id),
            )
            head = cursor.fetchone()
            if head is None:
                raise ResourceNotFoundError("Project version", project_id)
            _, authoritative_checksum = _publication_ir_payload(
                head["semantic_model"], head["presentation_ir"], head["interaction_ir"]
            )
            if (
                head["checksum"] != authoritative_checksum
                or head["version_number"] != base_version
                or base_checksum != authoritative_checksum
            ):
                raise IdempotencyConflictError("The authoring base is stale or has been tampered with.")

            cursor.execute(
                """SELECT id, checksum, semantic_model, presentation_ir, interaction_ir
                   FROM versions
                   WHERE tenant_id=%s AND project_id=%s AND version_number=%s""",
                (tenant_id, project_id, target_version),
            )
            target = cursor.fetchone()
            if target is None:
                raise ResourceNotFoundError("Project version", str(target_version))
            target_payload, target_checksum = _publication_ir_payload(
                target["semantic_model"], target["presentation_ir"], target["interaction_ir"]
            )
            if target["checksum"] != target_checksum:
                raise IdempotencyConflictError(
                    "The rollback target checksum does not match its canonical three-IR content."
                )

            head_semantic = SemanticModel.from_dict(head["semantic_model"])
            target_semantic = SemanticModel.from_dict(target_payload["semantic"])
            head_rls = getattr(head_semantic, "rls_intents", [])
            target_rls = getattr(target_semantic, "rls_intents", [])
            if head_rls != target_rls and not can_manage_rls:
                raise IdempotencyConflictError(
                    "Rollback cannot mutate semantic RLS policy without a dedicated security capability."
                )

            next_number = int(head["version_number"]) + 1
            draft_id = f"rollback-{hashlib.sha256(f'{project_id}:{idempotency_key}'.encode()).hexdigest()[:32]}"
            rollback_message = message or f"Rollback to version {target_version}"

            rollback_datasets = target.get("datasets") or head.get("datasets")
            rollback_plan = target.get("render_plan") or head.get("render_plan")
            rollback_results = target.get("runtime_results") or head.get("runtime_results")
            rollback_scan = target.get("credential_scan") or head.get("credential_scan")

            cursor.execute(
                """INSERT INTO versions(
                       tenant_id, project_id, id, version_number, checksum, is_active,
                       semantic_model, presentation_ir, interaction_ir,
                       parent_version_id, created_by, message, created_at,
                       render_plan, runtime_results, datasets, credential_scan
                   ) VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
                   RETURNING id, project_id, version_number, checksum, is_active, created_at""",
                (
                    tenant_id,
                    project_id,
                    draft_id,
                    next_number,
                    target_checksum,
                    Json(target_payload["semantic"]),
                    Json(target_payload["presentation"]),
                    Json(target_payload["interaction"]),
                    head["id"],
                    principal_id,
                    rollback_message,
                    Json(rollback_plan) if rollback_plan is not None else None,
                    Json(rollback_results) if rollback_results is not None else None,
                    Json(rollback_datasets) if rollback_datasets is not None else None,
                    Json(rollback_scan) if rollback_scan is not None else None,
                ),
            )
            version_row = cursor.fetchone()
            cursor.execute(
                """INSERT INTO authoring_mutations(
                       tenant_id, project_id, idempotency_key, request_sha256,
                       base_version, base_checksum, result_version_id, created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    tenant_id,
                    project_id,
                    idempotency_key,
                    request_sha,
                    base_version,
                    base_checksum,
                    draft_id,
                    principal_id,
                ),
            )
            return Version.model_validate(version_row)

    def materialize_version(
        self,
        tenant_id: str,
        version_id: str,
        *,
        render_plan: Mapping[str, Any],
        runtime_results: Mapping[str, Any] | None = None,
        datasets: Mapping[str, Dataset] | None = None,
        semantic_model: SemanticModel | None = None,
        presentation_ir: PresentationIR | None = None,
        interaction_ir: InteractionIR | None = None,
        credential_scan: Mapping[str, Any] | None = None,
    ) -> None:
        """Persiste plan, resultados y datasets consultables tenant-scoped."""
        serialized_datasets = _serialize_datasets(datasets)
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT v.id, v.version_number, v.checksum,
                          v.semantic_model, v.presentation_ir, v.interaction_ir,
                          v.parent_version_id, v.created_by,
                          p.published_version
                   FROM versions v
                   JOIN projects p ON p.tenant_id = v.tenant_id AND p.id = v.project_id
                   WHERE v.tenant_id = %s AND v.id = %s
                   FOR UPDATE""",
                (tenant_id, version_id),
            )
            target_v = cursor.fetchone()
            if target_v is None:
                raise ResourceNotFoundError("Version", version_id)

            has_canonical_ir = (
                target_v["semantic_model"] is not None
                or target_v["presentation_ir"] is not None
                or target_v["interaction_ir"] is not None
            )
            is_authored = (target_v["parent_version_id"] is not None or target_v["created_by"] is not None)
            is_published = (
                target_v["published_version"] is not None
                and target_v["version_number"] == target_v["published_version"]
            )

            if has_canonical_ir or is_authored or is_published:
                if semantic_model is not None or presentation_ir is not None or interaction_ir is not None:
                    raise IdempotencyConflictError(
                        "Canonical content of a durable or published version cannot be overwritten by materialization."
                    )
                cursor.execute(
                    """UPDATE versions SET render_plan=%s, runtime_results=%s, datasets=%s, credential_scan=%s
                       WHERE tenant_id=%s AND id=%s""",
                    (
                        Json(render_plan),
                        Json(runtime_results) if runtime_results is not None else None,
                        Json(serialized_datasets),
                        Json(credential_scan) if credential_scan is not None else None,
                        tenant_id,
                        version_id,
                    ),
                )
            else:
                if semantic_model is not None and presentation_ir is not None and interaction_ir is not None:
                    _validate_cross_ir_consistency(semantic_model, presentation_ir, interaction_ir)
                calculated_checksum = (
                    publication_version_checksum(
                        semantic_model.to_dict(), presentation_ir.to_dict(), interaction_ir.to_dict()
                    )
                    if semantic_model is not None and presentation_ir is not None and interaction_ir is not None
                    else None
                )
                cursor.execute(
                    """UPDATE versions SET render_plan=%s, runtime_results=%s, datasets=%s,
                           semantic_model=%s, presentation_ir=%s, interaction_ir=%s, credential_scan=%s,
                           checksum=COALESCE(%s, checksum)
                       WHERE tenant_id=%s AND id=%s""",
                    (
                        Json(render_plan),
                        Json(runtime_results) if runtime_results is not None else None,
                        Json(serialized_datasets),
                        Json(semantic_model.to_dict()) if semantic_model is not None else None,
                        Json(presentation_ir.to_dict()) if presentation_ir is not None else None,
                        Json(interaction_ir.to_dict()) if interaction_ir is not None else None,
                        Json(credential_scan) if credential_scan is not None else None,
                        calculated_checksum,
                        tenant_id,
                        version_id,
                    ),
                )
            if cursor.rowcount != 1:
                raise ResourceNotFoundError("Version", version_id)

    def execute_query(self, tenant_id: str, version_id: str, query: QuerySpec) -> Dataset:
        """Runs an AST query against the version's persisted dataset.

        The query can only read the version visible inside the tenant that
        opened the transaction. A single parameterized statement extracts only
        the requested datasource and classifies missing/malformed/oversized/
        ready inside PostgreSQL: rows only cross into Python when they fit
        under ``max_query_rows``. The executor evaluates the closed AST in
        memory; no SQL is generated and no request-derived names are
        interpolated.
        """
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                _QUERY_DATASET_SQL,
                (
                    query.from_datasource,
                    query.from_datasource,
                    query.from_datasource,
                    query.from_datasource,
                    self._max_query_rows,
                    query.from_datasource,
                    tenant_id,
                    version_id,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Version", version_id)
        _validate_persisted_canonical_triple(row)
        model = _load_semantic_model_for_analytics(row["semantic_model"])
        status = row["status"]
        if status == "missing":
            raise NotImplementedError("query datasource is not materialized")
        if status == "oversized":
            raise QueryInputTooLargeError(
                f"Dataset {query.from_datasource!r} exceeds the max_query_rows cap "
                f"({self._max_query_rows})."
            )
        if status != "ready":
            raise RuntimeMaterializationError(
                f"Dataset {query.from_datasource!r} has an invalid persisted materialization."
            )
        try:
            raw_dataset = row["dataset"]
            dataset = Dataset(
                name=str(raw_dataset["name"]),
                columns=tuple(str(column) for column in raw_dataset["columns"]),
                rows=tuple(dict(item) for item in raw_dataset["rows"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeMaterializationError(
                f"Dataset {query.from_datasource!r} has an invalid persisted materialization."
            ) from exc
        return QueryExecutor(model).execute(query, dataset)

    def get_runtime_payload(self, tenant_id: str, version_id: str) -> dict[str, Any]:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT render_plan, runtime_results, credential_scan, semantic_model,
                          presentation_ir, interaction_ir, checksum
                   FROM versions
                   WHERE tenant_id = %s AND id = %s""",
                (tenant_id, version_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Version", version_id)
        _validate_persisted_canonical_triple(row)
        _load_semantic_model_for_analytics(row["semantic_model"])
        return {
            "plan": row["render_plan"],
            "results": row["runtime_results"],
            "credential_scan": row["credential_scan"],
        }

    def can_run_version_jobs(
        self, tenant_id: str, principal_id: str, project_id: str, version_id: str
    ) -> bool:
        """Return whether the principal can execute/cancel jobs for the version."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT EXISTS (
                       SELECT 1
                       FROM versions v
                       JOIN role_assignments r
                         ON r.tenant_id = v.tenant_id
                        AND r.principal_id = %s
                        AND r.role IN ('editor','owner')
                        AND (
                            (r.scope = 'version' AND r.resource_id = v.id)
                            OR (r.scope = 'project' AND r.resource_id = v.project_id)
                        )
                       WHERE v.tenant_id = %s AND v.id = %s AND v.project_id = %s
                   ) AS allowed""",
                (principal_id, tenant_id, version_id, project_id),
            )
            row = cursor.fetchone()
        return bool(row and row["allowed"])

    def can_read_version(self, tenant_id: str, principal_id: str, version_id: str) -> bool:
        """Evalúa RBAC persistente para lectura de una versión."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT EXISTS (
                       SELECT 1
                       FROM versions v
                       JOIN role_assignments r
                         ON r.tenant_id = v.tenant_id
                        AND r.principal_id = %s
                        AND r.role IN ('viewer','editor','owner')
                        AND (
                            (r.scope = 'version' AND r.resource_id = v.id)
                            OR (r.scope = 'project' AND r.resource_id = v.project_id)
                        )
                       WHERE v.tenant_id = %s AND v.id = %s
                   ) AS allowed""",
                (principal_id, tenant_id, version_id),
            )
            row = cursor.fetchone()
        return bool(row and row["allowed"])

    def authorization_grants(
        self,
        tenant_id: str,
        principal_id: str,
        *,
        organization_id: str,
        project_id: str | None,
        version_id: str | None,
    ) -> tuple[set[str], set[str]]:
        """Return durable hierarchical role + atomic permission grants."""
        candidates = [("organization", organization_id)]
        if project_id is not None:
            candidates.append(("project", project_id))
        if version_id is not None:
            candidates.append(("version", version_id))
        roles: set[str] = set()
        permissions: set[str] = set()
        with self.transaction(tenant_id) as cursor:
            for scope, resource_id in candidates:
                cursor.execute(
                    """SELECT role FROM role_assignments
                       WHERE tenant_id=%s AND principal_id=%s AND scope=%s AND resource_id=%s""",
                    (tenant_id, principal_id, scope, resource_id),
                )
                roles.update(row["role"] for row in cursor.fetchall())
                cursor.execute(
                    """SELECT permission FROM permission_assignments
                       WHERE tenant_id=%s AND principal_id=%s AND scope=%s AND resource_id=%s""",
                    (tenant_id, principal_id, scope, resource_id),
                )
                permissions.update(row["permission"] for row in cursor.fetchall())
        return roles, permissions

    def upsert_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, role: str
    ) -> None:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """INSERT INTO role_assignments(tenant_id,principal_id,scope,resource_id,role)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,principal_id,scope,resource_id)
                   DO UPDATE SET role=EXCLUDED.role""",
                (tenant_id, principal_id, scope, resource_id, role),
            )

    def delete_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str
    ) -> None:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """DELETE FROM role_assignments
                   WHERE tenant_id=%s AND principal_id=%s AND scope=%s AND resource_id=%s""",
                (tenant_id, principal_id, scope, resource_id),
            )

    def upsert_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """INSERT INTO permission_assignments(
                       tenant_id,principal_id,scope,resource_id,permission)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (tenant_id, principal_id, scope, resource_id, permission),
            )

    def delete_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """DELETE FROM permission_assignments
                   WHERE tenant_id=%s AND principal_id=%s AND scope=%s
                     AND resource_id=%s AND permission=%s""",
                (tenant_id, principal_id, scope, resource_id, permission),
            )

    def consume_api_request(self, tenant_id: str) -> None:
        """Consume límites minuto/día atómicamente entre todas las réplicas."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT COALESCE(q.requests_per_minute, 120) AS minute_limit,
                          COALESCE(q.requests_per_day, 10000) AS day_limit
                   FROM tenants t LEFT JOIN api_quotas q ON q.tenant_id = t.id
                   WHERE t.id = %s""",
                (tenant_id,),
            )
            quota = cursor.fetchone()
            if quota is None:
                raise ResourceNotFoundError("Tenant", tenant_id)
            for kind, truncation, limit in (
                ("minute", "minute", quota["minute_limit"]),
                ("day", "day", quota["day_limit"]),
            ):
                cursor.execute(
                    """INSERT INTO api_usage(tenant_id,bucket_kind,bucket_start,request_count)
                       VALUES (%s,%s,date_trunc(%s,now()),1)
                       ON CONFLICT (tenant_id,bucket_kind,bucket_start)
                       DO UPDATE SET request_count=api_usage.request_count+1
                       WHERE %s = 0 OR api_usage.request_count < %s
                       RETURNING request_count""",
                    (tenant_id, kind, truncation, limit, limit),
                )
                if cursor.fetchone() is None:
                    raise QuotaExceededError(f"The {kind} request quota was exceeded.")

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
        max_attempts: int = 3,
    ) -> tuple[Job, bool]:
        """Create one durable job per idempotency key under a tenant lock.

        Exact replays are resolved before quota checks. Reusing the same key for
        a different immutable request fails closed instead of returning an
        unrelated prior job.
        """
        with self.transaction(tenant_id) as cursor:
            cursor.execute("SELECT id FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,))
            if cursor.fetchone() is None:
                raise ResourceNotFoundError("Tenant", tenant_id)

            cursor.execute(
                "SELECT * FROM jobs WHERE tenant_id = %s AND idempotency_key = %s",
                (tenant_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                equivalent = (
                    existing["project_id"] == project_id
                    and existing["version_id"] == version_id
                    and existing["job_type"] == job_type
                    and dict(existing["input"] or {}) == dict(input_payload)
                    and int(existing["max_attempts"]) == int(max_attempts)
                )
                if not equivalent:
                    raise IdempotencyConflictError(
                        "The idempotency key was already used for a different job request."
                    )
                return self._job(existing), False

            cursor.execute(
                """SELECT COALESCE(q.concurrent_jobs, 3) AS job_limit,
                          count(j.id) AS active_jobs
                    FROM tenants t
                    LEFT JOIN api_quotas q ON q.tenant_id=t.id
                    LEFT JOIN jobs j ON j.tenant_id=t.id AND j.status='RUNNING'
                    WHERE t.id=%s GROUP BY q.concurrent_jobs""",
                (tenant_id,),
            )
            quota = cursor.fetchone()
            if quota is None:
                raise ResourceNotFoundError("Tenant", tenant_id)
            job_limit = int(quota["job_limit"])
            if int(quota["active_jobs"]) >= job_limit:
                raise QuotaExceededError(f"Concurrent job quota reached ({job_limit}).")

            if job_type in ("TABLEAU_TO_PBIP", "POWER_BI_IMPORT"):
                cursor.execute(
                    """SELECT v.id, v.version_number, v.semantic_model, v.presentation_ir, v.interaction_ir,
                              v.parent_version_id, v.created_by, p.published_version
                       FROM versions v
                       JOIN projects p ON p.tenant_id = v.tenant_id AND p.id = v.project_id
                       WHERE v.tenant_id = %s AND v.project_id = %s AND v.id = %s""",
                    (tenant_id, project_id, version_id),
                )
                v_target = cursor.fetchone()
                if v_target is not None:
                    has_canonical_ir = (
                        v_target["semantic_model"] is not None
                        or v_target["presentation_ir"] is not None
                        or v_target["interaction_ir"] is not None
                    )
                    is_authored = (
                        v_target["parent_version_id"] is not None or v_target["created_by"] is not None
                    )
                    is_published = (
                        v_target["published_version"] is not None
                        and v_target["version_number"] == v_target["published_version"]
                    )
                    if has_canonical_ir or is_authored or is_published:
                        raise IdempotencyConflictError(
                            "Cannot enqueue import job for an already populated or authored version."
                        )

            cursor.execute(
                """INSERT INTO jobs(
                       tenant_id,id,project_id,version_id,job_type,input,status,
                       idempotency_key,max_attempts
                   ) VALUES (%s,%s,%s,%s,%s,%s,'PENDING',%s,%s)
                   RETURNING *""",
                (
                    tenant_id,
                    job_id,
                    project_id,
                    version_id,
                    job_type,
                    Json(input_payload),
                    idempotency_key,
                    max_attempts,
                ),
            )
            row = cursor.fetchone()
        return self._job(row), True

    def get_job(self, tenant_id: str, job_id: str) -> Job:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM jobs WHERE tenant_id = %s AND id = %s",
                (tenant_id, job_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Job", job_id)
        return self._job(row)

    def get_job_input(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT input FROM jobs WHERE tenant_id = %s AND id = %s",
                (tenant_id, job_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Job", job_id)
        return dict(row["input"])

    def get_job_checkpoint(self, tenant_id: str, job_id: str) -> Mapping[str, Any] | None:
        """Devuelve el último checkpoint persistido del job, o ``None``.

        Habilita checkpoint/restart: tras perder el lease, reencolar y
        reclamar, el worker reanuda la ejecución desde el último paso
        completado en lugar de reiniciar desde cero.
        """
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT checkpoint FROM jobs WHERE tenant_id = %s AND id = %s",
                (tenant_id, job_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Job", job_id)
        checkpoint = row["checkpoint"]
        return dict(checkpoint) if checkpoint is not None else None

    def count_jobs_by_status(self, tenant_id: str, status: str) -> int:
        """Cuenta jobs del tenant en un estado dado (observabilidad del queue)."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT count(*) AS n FROM jobs WHERE tenant_id = %s AND status = %s",
                (tenant_id, status),
            )
            row = cursor.fetchone()
        return int(row["n"]) if row else 0

    def claim_job(self, tenant_id: str, *, lease_seconds: int = 300) -> tuple[Job, str] | None:
        """Claim one pending job while enforcing the tenant RUNNING quota."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self.transaction(tenant_id) as cursor:
            cursor.execute("SELECT id FROM tenants WHERE id = %s FOR UPDATE", (tenant_id,))
            if cursor.fetchone() is None:
                raise ResourceNotFoundError("Tenant", tenant_id)
            cursor.execute(
                """SELECT COALESCE(q.concurrent_jobs, 3) AS job_limit,
                          count(j.id) AS active_jobs
                    FROM tenants t
                    LEFT JOIN api_quotas q ON q.tenant_id=t.id
                    LEFT JOIN jobs j ON j.tenant_id=t.id AND j.status='RUNNING'
                    WHERE t.id=%s GROUP BY q.concurrent_jobs""",
                (tenant_id,),
            )
            quota = cursor.fetchone()
            if quota is None:
                raise ResourceNotFoundError("Tenant", tenant_id)
            if int(quota["active_jobs"]) >= int(quota["job_limit"]):
                return None

            cursor.execute(
                """SELECT * FROM jobs
                   WHERE tenant_id = %s AND status = 'PENDING' AND attempt < max_attempts
                   ORDER BY created_at, id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                (tenant_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            cursor.execute(
                """UPDATE jobs SET status='RUNNING', lease_token=%s,
                   lease_expires_at=%s, attempt=attempt+1,
                   started_at=COALESCE(started_at, now())
                   WHERE tenant_id=%s AND id=%s RETURNING *""",
                (token, expires, tenant_id, row["id"]),
            )
            claimed = cursor.fetchone()
        return self._job(claimed), token

    def heartbeat_job(
        self, tenant_id: str, job_id: str, lease_token: str, *, lease_seconds: int = 300
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs SET lease_expires_at = %s
                   WHERE tenant_id = %s AND id = %s AND status = 'RUNNING'
                     AND lease_token = %s AND lease_expires_at > now()""",
                (expires, tenant_id, job_id, lease_token),
            )
            return cursor.rowcount == 1

    def checkpoint_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        checkpoint: Mapping[str, Any],
    ) -> bool:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs SET checkpoint = %s
                   WHERE tenant_id = %s AND id = %s AND status = 'RUNNING'
                     AND lease_token = %s AND lease_expires_at > now()""",
                (Json(checkpoint), tenant_id, job_id, lease_token),
            )
            return cursor.rowcount == 1

    def complete_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        result: Mapping[str, Any],
    ) -> bool:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs SET status='COMPLETED', result=%s, completed_at=now(),
                       lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND id=%s AND status='RUNNING'
                     AND lease_token=%s AND lease_expires_at > now()""",
                (Json(result), tenant_id, job_id, lease_token),
            )
            return cursor.rowcount == 1

    def complete_version_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        project_id: str,
        version_id: str,
        result: Mapping[str, Any],
        materialization: Mapping[str, Any],
    ) -> bool:
        """Atomically fence a version write and terminal job completion.

        The job transition is attempted first with the live lease, target
        project/version and RUNNING state in its WHERE clause. If the fence
        fails, no version mutation occurs. The version update then happens in
        the same PostgreSQL transaction; any failure rolls back the job
        transition as well.
        """
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs
                   SET status='COMPLETED', result=%s, completed_at=now(),
                       lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND id=%s AND status='RUNNING'
                     AND lease_token=%s AND lease_expires_at > clock_timestamp()
                     AND project_id=%s AND version_id=%s
                   RETURNING id""",
                (Json(result), tenant_id, job_id, lease_token, project_id, version_id),
            )
            if cursor.fetchone() is None:
                return False

            # Validate the staged payload only after the durable lease fence has
            # succeeded. A cancelled/expired worker is not authoritative and
            # must be rejected without inspecting or materializing its payload.
            # Any validation failure below raises inside this transaction, which
            # rolls the terminal job transition back together with the version
            # mutation.
            render_plan = materialization.get("render_plan")
            runtime_results = materialization.get("runtime_results")
            datasets = materialization.get("datasets")
            semantic_model = materialization.get("semantic_model")
            presentation_ir = materialization.get("presentation_ir")
            interaction_ir = materialization.get("interaction_ir")
            credential_scan = materialization.get("credential_scan")
            if not isinstance(render_plan, Mapping):
                raise ValueError("render_plan materialization is required")
            if runtime_results is not None and not isinstance(runtime_results, Mapping):
                raise ValueError("runtime_results materialization is invalid")
            if datasets is not None and not isinstance(datasets, Mapping):
                raise ValueError("datasets materialization is invalid")
            if semantic_model is not None and not isinstance(semantic_model, SemanticModel):
                raise ValueError("semantic_model materialization is invalid")
            if presentation_ir is not None and not isinstance(presentation_ir, PresentationIR):
                raise ValueError("presentation_ir materialization is invalid")
            if interaction_ir is not None and not isinstance(interaction_ir, InteractionIR):
                raise ValueError("interaction_ir materialization is invalid")
            if (semantic_model is None) != (presentation_ir is None) or (
                semantic_model is None
            ) != (interaction_ir is None):
                raise ValueError("canonical version materialization requires all three IRs together")
            # Cross-IR coherence (doc ids, page/visual references, semantic
            # bindings) is delegated to the canonical validator below instead
            # of a weaker partial duplicate here.
            if credential_scan is not None and not isinstance(credential_scan, Mapping):
                raise ValueError("credential_scan materialization is invalid")
            typed_datasets: dict[str, Dataset] = {}
            for name, dataset in (datasets or {}).items():
                if not isinstance(dataset, Dataset):
                    raise ValueError("datasets materialization contains a non-Dataset value")
                typed_datasets[str(name)] = dataset
            serialized_datasets = _serialize_datasets(typed_datasets)

            cursor.execute(
                """SELECT v.id, v.version_number, v.checksum,
                          v.semantic_model, v.presentation_ir, v.interaction_ir,
                          v.parent_version_id, v.created_by,
                          p.published_version
                   FROM versions v
                   JOIN projects p ON p.tenant_id = v.tenant_id AND p.id = v.project_id
                   WHERE v.tenant_id = %s AND v.project_id = %s AND v.id = %s
                   FOR UPDATE""",
                (tenant_id, project_id, version_id),
            )
            target_v = cursor.fetchone()
            if target_v is None:
                raise ResourceNotFoundError("Version", version_id)

            has_canonical_ir = (
                target_v["semantic_model"] is not None
                or target_v["presentation_ir"] is not None
                or target_v["interaction_ir"] is not None
            )
            is_authored = (target_v["parent_version_id"] is not None or target_v["created_by"] is not None)
            is_published = (
                target_v["published_version"] is not None
                and target_v["version_number"] == target_v["published_version"]
            )

            if has_canonical_ir or is_authored or is_published:
                if semantic_model is not None or presentation_ir is not None or interaction_ir is not None:
                    raise IdempotencyConflictError(
                        "Canonical content of a durable or published version cannot be overwritten by job completion."
                    )
                cursor.execute(
                    """UPDATE versions
                       SET render_plan=%s, runtime_results=%s, datasets=%s, credential_scan=%s
                       WHERE tenant_id=%s AND project_id=%s AND id=%s""",
                    (
                        Json(render_plan),
                        Json(runtime_results) if runtime_results is not None else None,
                        Json(serialized_datasets),
                        Json(credential_scan) if credential_scan is not None else None,
                        tenant_id,
                        project_id,
                        version_id,
                    ),
                )
            else:
                if semantic_model is not None and presentation_ir is not None and interaction_ir is not None:
                    _validate_cross_ir_consistency(semantic_model, presentation_ir, interaction_ir)
                calculated_checksum = (
                    publication_version_checksum(
                        semantic_model.to_dict(), presentation_ir.to_dict(), interaction_ir.to_dict()
                    )
                    if semantic_model is not None and presentation_ir is not None and interaction_ir is not None
                    else None
                )
                cursor.execute(
                    """UPDATE versions
                       SET render_plan=%s, runtime_results=%s, datasets=%s,
                           semantic_model=%s, presentation_ir=%s, interaction_ir=%s,
                           credential_scan=%s, checksum=COALESCE(%s, checksum)
                       WHERE tenant_id=%s AND project_id=%s AND id=%s""",
                    (
                        Json(render_plan),
                        Json(runtime_results) if runtime_results is not None else None,
                        Json(serialized_datasets),
                        Json(semantic_model.to_dict()) if semantic_model is not None else None,
                        Json(presentation_ir.to_dict()) if presentation_ir is not None else None,
                        Json(interaction_ir.to_dict()) if interaction_ir is not None else None,
                        Json(credential_scan) if credential_scan is not None else None,
                        calculated_checksum,
                        tenant_id,
                        project_id,
                        version_id,
                    ),
                )
            if cursor.rowcount != 1:
                raise ResourceNotFoundError("Version", version_id)
        return True

    def fail_job(
        self,
        tenant_id: str,
        job_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> bool:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs
                   SET status = CASE WHEN %s AND attempt < max_attempts THEN 'PENDING'
                                     ELSE 'FAILED' END,
                       error_code=%s, error_message=%s,
                       completed_at=CASE WHEN %s AND attempt < max_attempts
                                         THEN NULL ELSE now() END,
                       lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND id=%s AND status='RUNNING'
                     AND lease_token=%s""",
                (
                    retryable,
                    error_code,
                    error_message,
                    retryable,
                    tenant_id,
                    job_id,
                    lease_token,
                ),
            )
            return cursor.rowcount == 1

    def cancel_job(self, tenant_id: str, job_id: str) -> bool:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs SET status='CANCELLED', completed_at=now(),
                       lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND id=%s AND status IN ('PENDING','RUNNING')""",
                (tenant_id, job_id),
            )
            return cursor.rowcount == 1

    def recover_expired_jobs(self, tenant_id: str) -> tuple[int, int]:
        """Reencola leases vencidos con presupuesto y falla los agotados."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """UPDATE jobs SET status='PENDING', lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND status='RUNNING' AND lease_expires_at <= now()
                     AND attempt < max_attempts""",
                (tenant_id,),
            )
            requeued = cursor.rowcount
            cursor.execute(
                """UPDATE jobs SET status='FAILED', completed_at=now(),
                       error_code='ATTEMPTS_EXHAUSTED',
                       error_message='The execution retry budget was exhausted.',
                       lease_token=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND status='RUNNING' AND lease_expires_at <= now()
                     AND attempt >= max_attempts""",
                (tenant_id,),
            )
            failed = cursor.rowcount
        return requeued, failed

    @staticmethod
    def _job(row: Mapping[str, Any] | None) -> Job:
        if row is None:
            raise RuntimeError("PostgreSQL did not return a job row")
        return Job.model_validate(
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "project_id": row["project_id"],
                "version_id": row["version_id"],
                "job_type": row["job_type"],
                "status": JobStatus(row["status"]),
                "idempotency_key": row["idempotency_key"],
                "created_at": row["created_at"],
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "error_code": row.get("error_code"),
                "error_message": row.get("error_message"),
            }
        )

    # --- Durable publication governance ---------------------------------

    def resolve_publication_snapshot(
        self,
        resource: Any,
        target_version: int,
        *,
        base_version: int | None,
    ) -> PublicationSnapshot:
        """Resolve and lock a publication identity from the durable three-IR version."""
        project_id = getattr(resource, "project_id", None)
        if not project_id:
            raise ValueError("publication resource must identify a project")
        tenant_id = resource.tenant_id
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT version_number, checksum, semantic_model, presentation_ir, interaction_ir
                   FROM versions
                   WHERE tenant_id=%s AND project_id=%s AND version_number=%s
                   FOR SHARE""",
                (tenant_id, project_id, target_version),
            )
            target = cursor.fetchone()
            if target is None:
                raise PublicationConflictError("Authoritative target version does not exist.")
            target_payload, target_checksum = _publication_ir_payload(
                target["semantic_model"], target["presentation_ir"], target["interaction_ir"]
            )
            if target["checksum"] != target_checksum:
                raise PublicationConflictError(
                    "The durable target checksum does not match its canonical three-IR content."
                )
            base_payload: dict[str, Any] | None = None
            if base_version is not None:
                cursor.execute(
                    """SELECT version_number, checksum, semantic_model, presentation_ir, interaction_ir
                       FROM versions
                       WHERE tenant_id=%s AND project_id=%s AND version_number=%s
                       FOR SHARE""",
                    (tenant_id, project_id, base_version),
                )
                base = cursor.fetchone()
                if base is None:
                    raise PublicationConflictError("Authoritative base version does not exist.")
                base_payload, base_checksum = _publication_ir_payload(
                    base["semantic_model"], base["presentation_ir"], base["interaction_ir"]
                )
                if base["checksum"] != base_checksum:
                    raise PublicationConflictError(
                        "The durable base checksum does not match its canonical three-IR content."
                    )
        return PublicationSnapshot(
            tenant_id=tenant_id,
            organization_id=resource.organization_id,
            project_id=project_id,
            target_version=target_version,
            checksum=target_checksum,
            diff_payload=publication_version_diff_payload(base_payload, target_payload),
        )

    def save_publication_candidate(
        self, candidate: PublicationCandidate
    ) -> PublicationCandidate:
        """Persist one immutable candidate; an ID collision must be equivalent."""
        try:
            with self.transaction(candidate.tenant_id) as cursor:
                cursor.execute(
                    """INSERT INTO publication_candidates(
                           id, tenant_id, organization_id, project_id, target_version,
                           expected_published_version, kind, target_checksum, diff_sha256,
                           requested_by, requested_at
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,id) DO NOTHING
                       RETURNING *""",
                    (
                        candidate.id,
                        candidate.tenant_id,
                        candidate.organization_id,
                        candidate.project_id,
                        candidate.target_version,
                        candidate.expected_published_version,
                        candidate.kind.value,
                        candidate.target_checksum,
                        candidate.diff_sha256,
                        candidate.requested_by,
                        candidate.requested_at,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        "SELECT * FROM publication_candidates WHERE tenant_id=%s AND id=%s",
                        (candidate.tenant_id, candidate.id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("PostgreSQL did not return a publication candidate")
                    existing = self._publication_candidate(row)
                    if _candidate_identity(existing) != _candidate_identity(candidate):
                        raise IdempotencyConflictError(
                            "Publication candidate id already exists with a different immutable identity."
                        )
                    return existing
        except ForeignKeyViolation as exc:
            raise PublicationConflictError(
                "Publication target does not reference an existing durable project/version."
            ) from exc
        return self._publication_candidate(row)

    def get_publication_candidate(
        self, tenant_id: str, candidate_id: str
    ) -> PublicationCandidate:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM publication_candidates WHERE tenant_id=%s AND id=%s",
                (tenant_id, candidate_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("publication_candidate", candidate_id)
        return self._publication_candidate(row)

    def save_publication_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        """Derive approval identity from the durable candidate; requester cannot self-approve."""
        with self.transaction(approval.tenant_id) as cursor:
            cursor.execute(
                """INSERT INTO publication_approvals(
                       id, candidate_id, tenant_id, organization_id, project_id,
                       target_version, kind, target_checksum, diff_sha256,
                       approved_by, approved_at
                   )
                   SELECT %s, c.id, c.tenant_id, c.organization_id, c.project_id,
                          c.target_version, c.kind, c.target_checksum, c.diff_sha256,
                          %s, %s
                   FROM publication_candidates c
                   WHERE c.tenant_id=%s AND c.id=%s AND c.requested_by <> %s
                   ON CONFLICT DO NOTHING
                   RETURNING *""",
                (
                    approval.id,
                    approval.approved_by,
                    approval.approved_at,
                    approval.tenant_id,
                    approval.candidate_id,
                    approval.approved_by,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """SELECT a.* FROM publication_approvals a
                       WHERE a.tenant_id=%s AND a.candidate_id=%s""",
                    (approval.tenant_id, approval.candidate_id),
                )
                row = cursor.fetchone()
                if row is not None:
                    return self._publication_approval(row)
                cursor.execute(
                    "SELECT requested_by FROM publication_candidates WHERE tenant_id=%s AND id=%s",
                    (approval.tenant_id, approval.candidate_id),
                )
                candidate = cursor.fetchone()
                if candidate is None:
                    raise ResourceNotFoundError("publication_candidate", approval.candidate_id)
                if candidate["requested_by"] == approval.approved_by:
                    raise PublicationApprovalError(
                        "The publication requester cannot approve the same candidate."
                    )
                raise IdempotencyConflictError(
                    "Publication approval identity conflicts with an existing approval."
                )
        return self._publication_approval(row)

    def get_publication_approval(self, tenant_id: str, approval_id: str) -> ApprovalRecord:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM publication_approvals WHERE tenant_id=%s AND id=%s",
                (tenant_id, approval_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("publication_approval", approval_id)
        return self._publication_approval(row)

    def activate_publication(
        self,
        tenant_id: str,
        *,
        candidate_id: str,
        approval_id: str,
        published_by: str,
    ) -> PublicationRecord:
        """Revalidate canonical content and move the publication pointer atomically."""
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM publication_candidates WHERE tenant_id=%s AND id=%s",
                (tenant_id, candidate_id),
            )
            candidate_row = cursor.fetchone()
            if candidate_row is None:
                raise ResourceNotFoundError("publication_candidate", candidate_id)
            candidate = self._publication_candidate(candidate_row)

            cursor.execute(
                "SELECT * FROM publication_approvals WHERE tenant_id=%s AND id=%s",
                (tenant_id, approval_id),
            )
            approval_row = cursor.fetchone()
            if approval_row is None:
                raise ResourceNotFoundError("publication_approval", approval_id)
            approval = self._publication_approval(approval_row)
            if approval.candidate_id != candidate.id:
                raise PublicationApprovalError(
                    "Approval belongs to a different publication candidate."
                )
            if (
                approval.target_checksum != candidate.target_checksum
                or approval.diff_sha256 != candidate.diff_sha256
                or approval.target_version != candidate.target_version
                or approval.kind is not candidate.kind
            ):
                raise PublicationApprovalError(
                    "Approval immutable identity does not match the publication candidate."
                )

            cursor.execute(
                """SELECT published_version, published_record_id FROM projects
                   WHERE tenant_id=%s AND id=%s FOR UPDATE""",
                (tenant_id, candidate.project_id),
            )
            project = cursor.fetchone()
            if project is None:
                raise ResourceNotFoundError("Project", candidate.project_id)

            # Lost-response replay is idempotent. The project row lock ensures a
            # concurrent first activation commits before a waiter evaluates this.
            cursor.execute(
                "SELECT * FROM publication_records WHERE tenant_id=%s AND approval_id=%s",
                (tenant_id, approval_id),
            )
            replay = cursor.fetchone()
            if replay is not None:
                record = self._publication_record(replay)
                if record.candidate_id != candidate_id or record.published_by != published_by:
                    raise IdempotencyConflictError(
                        "Publication approval was already consumed by a different activation."
                    )
                return record

            current = project["published_version"]
            if current != candidate.expected_published_version:
                raise PublicationConflictError(
                    "Publication candidate is stale because the published version changed after review."
                )

            cursor.execute(
                """SELECT checksum, semantic_model, presentation_ir, interaction_ir
                   FROM versions
                   WHERE tenant_id=%s AND project_id=%s AND version_number=%s
                   FOR SHARE""",
                (tenant_id, candidate.project_id, candidate.target_version),
            )
            target = cursor.fetchone()
            if target is None:
                raise PublicationConflictError("Authoritative target version does not exist.")
            target_payload, target_checksum = _publication_ir_payload(
                target["semantic_model"], target["presentation_ir"], target["interaction_ir"]
            )
            if target["checksum"] != target_checksum or candidate.target_checksum != target_checksum:
                raise PublicationConflictError(
                    "The authoritative target version changed after the candidate was reviewed."
                )

            base_payload: dict[str, Any] | None = None
            if current is not None:
                cursor.execute(
                    """SELECT checksum, semantic_model, presentation_ir, interaction_ir
                       FROM versions
                       WHERE tenant_id=%s AND project_id=%s AND version_number=%s
                       FOR SHARE""",
                    (tenant_id, candidate.project_id, current),
                )
                base = cursor.fetchone()
                if base is None:
                    raise PublicationConflictError("Authoritative published base version does not exist.")
                base_payload, base_checksum = _publication_ir_payload(
                    base["semantic_model"], base["presentation_ir"], base["interaction_ir"]
                )
                if base["checksum"] != base_checksum:
                    raise PublicationConflictError(
                        "The authoritative published base version is inconsistent."
                    )
            diff_hash = publication_diff_sha256(
                publication_version_diff_payload(base_payload, target_payload)
            )
            if diff_hash != candidate.diff_sha256:
                raise PublicationConflictError(
                    "The authoritative reviewed diff changed after candidate creation."
                )
            if candidate.kind is PublicationKind.ROLLBACK:
                cursor.execute(
                    """SELECT 1 FROM publication_records
                       WHERE tenant_id=%s AND project_id=%s AND target_version=%s
                       LIMIT 1""",
                    (tenant_id, candidate.project_id, candidate.target_version),
                )
                if cursor.fetchone() is None:
                    raise PublicationConflictError(
                        "Rollback target was never published by this authority."
                    )

            record_id = str(__import__("uuid").uuid4())
            try:
                cursor.execute(
                    """INSERT INTO publication_records(
                           id, candidate_id, approval_id, tenant_id, organization_id,
                           project_id, previous_version, target_version, kind,
                           target_checksum, diff_sha256, approved_by, approved_at,
                           published_by, published_at
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                       RETURNING *""",
                    (
                        record_id,
                        candidate.id,
                        approval.id,
                        tenant_id,
                        candidate.organization_id,
                        candidate.project_id,
                        current,
                        candidate.target_version,
                        candidate.kind.value,
                        candidate.target_checksum,
                        candidate.diff_sha256,
                        approval.approved_by,
                        approval.approved_at,
                        published_by,
                    ),
                )
            except UniqueViolation as exc:
                raise IdempotencyConflictError(
                    "Publication approval has already been consumed."
                ) from exc
            record_row = cursor.fetchone()
            cursor.execute(
                """UPDATE projects SET published_version=%s, published_record_id=%s
                   WHERE tenant_id=%s AND id=%s
                     AND published_version IS NOT DISTINCT FROM %s""",
                (
                    candidate.target_version,
                    record_id,
                    tenant_id,
                    candidate.project_id,
                    current,
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationConflictError(
                    "Project publication pointer changed during activation."
                )
            return self._publication_record(record_row)

    def get_published_version(self, tenant_id: str, project_id: str) -> int | None:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT published_version FROM projects WHERE tenant_id=%s AND id=%s",
                (tenant_id, project_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResourceNotFoundError("Project", project_id)
        return row["published_version"]

    def get_publication_history(
        self, tenant_id: str, project_id: str
    ) -> tuple[PublicationRecord, ...]:
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                """SELECT * FROM publication_records
                   WHERE tenant_id=%s AND project_id=%s
                   ORDER BY published_at, id""",
                (tenant_id, project_id),
            )
            rows = cursor.fetchall()
        return tuple(self._publication_record(row) for row in rows)

    def save_agent_proposal(self, proposal: Proposal) -> Proposal:
        """Persist one immutable, validated three-IR authoring proposal.

        The proposal row is review state, not semantic authority. Its candidate
        snapshot is only eligible to become canonical through
        ``approve_agent_proposal_to_draft`` below.
        """
        if proposal.status != "pending":
            raise IdempotencyConflictError("Only pending agent proposals can be persisted.")
        if not proposal.author.strip():
            raise IdempotencyConflictError("A durable proposal requires an authenticated author.")
        if any(operation.target == "rls_intent" for operation in proposal.ops):
            raise IdempotencyConflictError(
                "Agent proposals cannot mutate semantic RLS policy without a dedicated security capability."
            )
        if not all(
            value is not None
            for value in (
                proposal.candidate_semantic,
                proposal.candidate_presentation,
                proposal.candidate_interaction,
            )
        ):
            raise IdempotencyConflictError("A durable proposal requires a complete three-IR candidate.")
        assert proposal.candidate_semantic is not None
        assert proposal.candidate_presentation is not None
        assert proposal.candidate_interaction is not None
        if (
            proposal.candidate_presentation.doc_id != proposal.doc_id
            or proposal.candidate_interaction.doc_id != proposal.doc_id
        ):
            raise IdempotencyConflictError("Proposal document identity does not match its candidate IRs.")

        identity = proposal_identity_payload(proposal)
        digest = proposal_sha256(proposal)
        candidate = publication_version_payload(
            proposal.candidate_semantic.to_dict(),
            proposal.candidate_presentation.to_dict(),
            proposal.candidate_interaction.to_dict(),
        )
        candidate_checksum = publication_version_checksum(
            candidate["semantic"], candidate["presentation"], candidate["interaction"]
        )
        if identity.get("candidate_checksum") != candidate_checksum:
            raise IdempotencyConflictError("Proposal candidate checksum is inconsistent with its identity.")
        payload = {
            "identity": identity,
            "candidate": candidate,
            "candidate_checksum": candidate_checksum,
        }
        with self.transaction(proposal.tenant_id) as cursor:
            cursor.execute(
                """SELECT id, checksum, semantic_model, presentation_ir, interaction_ir
                   FROM versions
                   WHERE tenant_id=%s AND project_id=%s AND version_number=%s
                   FOR SHARE""",
                (proposal.tenant_id, proposal.project_id, proposal.base_version),
            )
            base = cursor.fetchone()
            if base is None:
                raise ResourceNotFoundError("Version", str(proposal.base_version))
            _, authoritative_checksum = _publication_ir_payload(
                base["semantic_model"], base["presentation_ir"], base["interaction_ir"]
            )
            if base["checksum"] != authoritative_checksum:
                raise IdempotencyConflictError("The proposal base version has an invalid canonical checksum.")
            if proposal.base_checksum != authoritative_checksum or proposal.head_checksum != authoritative_checksum:
                raise IdempotencyConflictError("The proposal is not bound to the authoritative base checksum.")
            base_semantic = SemanticModel.from_dict(base["semantic_model"])
            base_presentation = PresentationIR.from_dict(base["presentation_ir"])
            base_interaction = InteractionIR.from_dict(base["interaction_ir"])
            expected_semantic = materialize(base_semantic, list(proposal.ops))
            expected_presentation = materialize_presentation(
                base_presentation, list(proposal.presentation_ops)
            )
            expected_interaction = materialize_interactions(
                base_interaction, list(proposal.interaction_ops)
            )
            if (
                expected_semantic != proposal.candidate_semantic
                or expected_presentation != proposal.candidate_presentation
                or expected_interaction != proposal.candidate_interaction
            ):
                raise IdempotencyConflictError(
                    "The proposal candidate does not match its typed operations."
                )
            cursor.execute(
                """SELECT version_number, checksum FROM versions
                   WHERE tenant_id=%s AND project_id=%s
                   ORDER BY version_number DESC LIMIT 1 FOR SHARE""",
                (proposal.tenant_id, proposal.project_id),
            )
            head = cursor.fetchone()
            if head is None or head["version_number"] != proposal.base_version or head["checksum"] != proposal.head_checksum:
                raise IdempotencyConflictError("The proposal was created against a stale project head.")

            cursor.execute(
                """SELECT proposal_sha256, proposal_payload FROM agent_proposals
                   WHERE tenant_id=%s AND proposal_id=%s""",
                (proposal.tenant_id, proposal.proposal_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if existing["proposal_sha256"] == digest and existing["proposal_payload"] == payload:
                    return proposal
                raise IdempotencyConflictError(
                    "The proposal_id is already bound to different immutable content."
                )
            cursor.execute(
                """INSERT INTO agent_proposals(
                       tenant_id, proposal_id, doc_id, project_id, proposal_type,
                       base_version, base_checksum, head_checksum, proposal_payload,
                       proposal_sha256, status, author, created_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)""",
                (
                    proposal.tenant_id,
                    proposal.proposal_id,
                    proposal.doc_id,
                    proposal.project_id,
                    proposal.proposal_type,
                    proposal.base_version,
                    proposal.base_checksum,
                    proposal.head_checksum,
                    Json(payload),
                    digest,
                    proposal.author,
                    proposal.created_at,
                ),
            )
        return proposal

    def approve_agent_proposal_to_draft(
        self,
        tenant_id: str,
        proposal_id: str,
        *,
        approver_id: str,
        confirm_destructive: bool = False,
        confirm_high_cost: bool = False,
    ) -> Version:
        """Atomically consume one proposal and materialize its canonical draft.

        The proposal decision and new durable version commit in the same
        PostgreSQL transaction. Lost-response replay by the same approver is
        idempotent; a different approver or a changed head conflicts.
        ``confirm_high_cost`` gates proposals whose stored operations declare
        high-cost custom visuals (recalculated server-side, never trusted from
        the review-time report).
        """
        if not approver_id.strip():
            raise IdempotencyConflictError("An authenticated approver is required.")
        if not isinstance(confirm_destructive, bool):
            raise TypeError("confirm_destructive must be a bool")
        if not isinstance(confirm_high_cost, bool):
            raise TypeError("confirm_high_cost must be a bool")
        with self.transaction(tenant_id) as cursor:
            cursor.execute(
                "SELECT * FROM agent_proposals WHERE tenant_id=%s AND proposal_id=%s FOR UPDATE",
                (tenant_id, proposal_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ResourceNotFoundError("Agent proposal", proposal_id)
            if row["project_id"] is None or row["proposal_type"] != "authoring":
                raise IdempotencyConflictError("Legacy proposal lacks durable authoring identity.")
            if row["author"] == approver_id:
                raise IdempotencyConflictError("A proposal author cannot approve their own proposal.")
            if row["status"] == "approved":
                if row["decided_by"] != approver_id or not row["draft_version_id"]:
                    raise IdempotencyConflictError("The proposal was already decided by another principal.")
                cursor.execute(
                    """SELECT id, project_id, version_number, checksum, is_active, created_at
                       FROM versions WHERE tenant_id=%s AND id=%s""",
                    (tenant_id, row["draft_version_id"]),
                )
                replay = cursor.fetchone()
                if replay is None:
                    raise IdempotencyConflictError("Approved proposal is missing its durable draft.")
                return Version.model_validate(replay)
            if row["status"] != "pending":
                raise IdempotencyConflictError("The proposal is no longer pending.")

            payload = row["proposal_payload"]
            if not isinstance(payload, Mapping) or not isinstance(payload.get("identity"), Mapping):
                raise IdempotencyConflictError("Stored proposal payload is invalid.")
            canonical_identity = json.dumps(
                payload["identity"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if hashlib.sha256(canonical_identity).hexdigest() != row["proposal_sha256"]:
                raise IdempotencyConflictError("Stored proposal immutable identity was tampered with.")
            identity = payload["identity"]
            op_groups = (
                identity.get("semantic_ops", []),
                identity.get("presentation_ops", []),
                identity.get("interaction_ops", []),
            )
            destructive = any(
                isinstance(op, Mapping) and op.get("kind") == "remove"
                for operations in op_groups
                for op in operations
            )
            if destructive and not confirm_destructive:
                raise IdempotencyConflictError(
                    "Destructive proposal requires explicit human confirmation."
                )

            # Costo de custom visuals recalculado server-side desde las
            # presentation_ops almacenadas. La identidad ya se verificó contra
            # proposal_sha256, así que este recálculo es anti-tamper: el reporte
            # visto en revisión nunca es la autoridad.
            raw_presentation_ops = identity.get("presentation_ops", [])
            if not isinstance(raw_presentation_ops, list):
                raise IdempotencyConflictError("Stored proposal operations are invalid.")
            try:
                stored_presentation_ops = [
                    PresentationOperation.from_dict(op) for op in raw_presentation_ops
                ]
                high_cost = estimate_presentation_operations(
                    stored_presentation_ops
                ).requires_user_confirmation
            except (TypeError, ValueError) as exc:
                raise IdempotencyConflictError("Stored proposal operations are invalid.") from exc
            if high_cost and not confirm_high_cost:
                raise IdempotencyConflictError(
                    "High-cost custom visual proposal requires explicit human "
                    "confirmation (confirm_high_cost)."
                )

            cursor.execute(
                "SELECT id FROM projects WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, row["project_id"]),
            )
            if cursor.fetchone() is None:
                raise ResourceNotFoundError("Project", row["project_id"])
            cursor.execute(
                """SELECT * FROM versions WHERE tenant_id=%s AND project_id=%s
                   ORDER BY version_number DESC LIMIT 1 FOR UPDATE""",
                (tenant_id, row["project_id"]),
            )
            head = cursor.fetchone()
            if head is None or head["version_number"] != row["base_version"]:
                raise IdempotencyConflictError("The proposal base version is stale.")
            _, head_checksum = _publication_ir_payload(
                head["semantic_model"], head["presentation_ir"], head["interaction_ir"]
            )
            if (
                head["checksum"] != head_checksum
                or row["base_checksum"] != head_checksum
                or row["head_checksum"] != head_checksum
            ):
                raise IdempotencyConflictError("The authoritative proposal base changed after review.")

            candidate = payload.get("candidate")
            if not isinstance(candidate, Mapping):
                raise IdempotencyConflictError("Stored proposal candidate is invalid.")
            candidate_payload, candidate_checksum = _publication_ir_payload(
                candidate.get("semantic"), candidate.get("presentation"), candidate.get("interaction")
            )
            if (
                candidate_checksum != payload.get("candidate_checksum")
                or candidate_checksum != identity.get("candidate_checksum")
            ):
                raise IdempotencyConflictError("Stored proposal candidate was tampered with.")
            draft_id = f"agent-{proposal_id}"
            next_number = int(head["version_number"]) + 1
            cursor.execute(
                """INSERT INTO versions(
                       tenant_id, project_id, id, version_number, checksum, is_active,
                       semantic_model, presentation_ir, interaction_ir, created_at
                   ) VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s,now())
                   RETURNING id, project_id, version_number, checksum, is_active, created_at""",
                (
                    tenant_id,
                    row["project_id"],
                    draft_id,
                    next_number,
                    candidate_checksum,
                    Json(candidate_payload["semantic"]),
                    Json(candidate_payload["presentation"]),
                    Json(candidate_payload["interaction"]),
                ),
            )
            version_row = cursor.fetchone()
            cursor.execute(
                """UPDATE agent_proposals
                   SET status='approved', decided_by=%s, decided_at=now(), draft_version_id=%s
                   WHERE tenant_id=%s AND proposal_id=%s AND status='pending'""",
                (approver_id, draft_id, tenant_id, proposal_id),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflictError("Proposal decision raced with another approver.")
            return Version.model_validate(version_row)

    @staticmethod
    def _publication_candidate(row: Mapping[str, Any]) -> PublicationCandidate:
        return PublicationCandidate(
            id=row["id"],
            tenant_id=row["tenant_id"],
            organization_id=row["organization_id"],
            project_id=row["project_id"],
            target_version=row["target_version"],
            expected_published_version=row["expected_published_version"],
            kind=row["kind"],
            target_checksum=row["target_checksum"],
            diff_sha256=row["diff_sha256"],
            requested_by=row["requested_by"],
            requested_at=row["requested_at"],
        )

    @staticmethod
    def _publication_approval(row: Mapping[str, Any]) -> ApprovalRecord:
        return ApprovalRecord(
            id=row["id"],
            candidate_id=row["candidate_id"],
            tenant_id=row["tenant_id"],
            organization_id=row["organization_id"],
            project_id=row["project_id"],
            target_version=row["target_version"],
            kind=row["kind"],
            target_checksum=row["target_checksum"],
            diff_sha256=row["diff_sha256"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
        )

    @staticmethod
    def _publication_record(row: Mapping[str, Any]) -> PublicationRecord:
        return PublicationRecord(
            id=row["id"],
            candidate_id=row["candidate_id"],
            approval_id=row["approval_id"],
            tenant_id=row["tenant_id"],
            organization_id=row["organization_id"],
            project_id=row["project_id"],
            previous_version=row["previous_version"],
            target_version=row["target_version"],
            kind=row["kind"],
            target_checksum=row["target_checksum"],
            diff_sha256=row["diff_sha256"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            published_by=row["published_by"],
            published_at=row["published_at"],
        )

    def schema_version(self) -> int | None:
        connection = self._pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT max(version) FROM schema_migrations")
                row = cursor.fetchone()
                return row[0] if row else None
        finally:
            self._pool.putconn(connection)
