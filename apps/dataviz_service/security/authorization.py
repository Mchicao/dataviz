"""Hierarchical deny-by-default RBAC for the DataVIZ SaaS service.

Resource model
--------------
Resources live in a three-level hierarchy scoped within a tenant::

    Organization  ->  Project  ->  Version

A :class:`RoleAssignment` binds a principal to a :class:`Role` at one of these
:class:`ResourceScope` levels. A role granted at a higher level *cascades* to all
descendants within the same organization:

  * ``ORGANIZATION``-scoped role  -> applies to the org and all its projects/versions.
  * ``PROJECT``-scoped role       -> applies to the project and all its versions.
  * ``VERSION``-scoped role       -> applies only to that version.

Authorization is **deny-by-default**: any request that cannot be matched to a
covering assignment with sufficient permissions is rejected with
:class:`AccessDeniedError`. Cross-tenant access is rejected unconditionally,
even if an assignment with a matching principal id exists in a foreign tenant,
because assignments are themselves tenant-scoped.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.dataviz_service.contracts import AccessDeniedError
from apps.dataviz_service.security.identity import Principal
from core.contracts.permissions import ACTION_PERMISSION, AuthoringAction, Permission

logger = logging.getLogger("dataviz_service.security.authorization")


class ResourceScope(StrEnum):
    """Level of the resource hierarchy at which a role is granted."""

    ORGANIZATION = "organization"
    PROJECT = "project"
    VERSION = "version"


class Role(StrEnum):
    """RBAC roles, ordered from least to most privileged."""

    VIEWER = "viewer"
    EDITOR = "editor"
    OWNER = "owner"


# Permission matrix: each role grants exactly the permissions listed here.
# Adding a permission to a role is a security-sensitive change; review carefully.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.READ, Permission.CONSUME}),
    Role.EDITOR: frozenset({
        Permission.READ,
        Permission.WRITE,
        Permission.CONSUME,
        Permission.AUTHOR,
        Permission.REQUEST_PUBLISH,
    }),
    Role.OWNER: frozenset(set(Permission)),
}


class ResourceRef(BaseModel):
    """Reference to a tenant resource at a specific hierarchy level.

    The deepest populated identifier determines the effective :attr:`scope`:
    a populated ``version_id`` implies a VERSION-scoped resource, a populated
    ``project_id`` (without version) a PROJECT-scoped resource, and so on.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Tenant that owns the resource")
    organization_id: str = Field(..., description="Organization that owns the resource")
    project_id: str | None = Field(default=None, description="Project id, when applicable")
    version_id: str | None = Field(default=None, description="Version id, when applicable")

    @field_validator("tenant_id", "organization_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Resource tenant/organization id must not be empty")
        return v.strip()

    @field_validator("project_id", "version_id")
    @classmethod
    def _clean_optional_id(cls, v: str | None) -> str | None:
        if v is not None:
            stripped = v.strip()
            return stripped if stripped else None
        return None

    @property
    def scope(self) -> ResourceScope:
        if self.version_id:
            return ResourceScope.VERSION
        if self.project_id:
            return ResourceScope.PROJECT
        return ResourceScope.ORGANIZATION


class RoleAssignment(BaseModel):
    """A role granted to a principal on a resource at a given scope.

    The ``scope``/``resource_id`` pair identifies the granted resource: an
    ``ORGANIZATION`` assignment references an organization id, a ``PROJECT``
    assignment a project id, and a ``VERSION`` assignment a version id.
    """

    model_config = ConfigDict(frozen=True)

    principal_id: str = Field(..., description="Subject id of the grantee")
    tenant_id: str = Field(..., description="Tenant the assignment lives in")
    role: Role = Field(..., description="Granted role")
    scope: ResourceScope = Field(..., description="Hierarchy level of the granted resource")
    resource_id: str = Field(..., description="Identifier of the granted resource")

    @field_validator("principal_id", "tenant_id", "resource_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Assignment identifier must not be empty")
        return v.strip()


class PermissionAssignment(BaseModel):
    """A direct atomic permission grant at one resource scope."""

    model_config = ConfigDict(frozen=True)

    principal_id: str = Field(..., description="Subject id of the grantee")
    tenant_id: str = Field(..., description="Tenant the assignment lives in")
    permission: Permission = Field(..., description="Granted atomic permission")
    scope: ResourceScope = Field(..., description="Hierarchy level of the granted resource")
    resource_id: str = Field(..., description="Identifier of the granted resource")

    @field_validator("principal_id", "tenant_id", "resource_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Permission assignment identifier must not be empty")
        return v.strip()


class AuthorizationRepository(Protocol):
    """Storage contract shared by production PostgreSQL and explicit test fakes."""

    def authorization_grants(
        self,
        tenant_id: str,
        principal_id: str,
        *,
        organization_id: str,
        project_id: str | None,
        version_id: str | None,
    ) -> tuple[set[str], set[str]]: ...

    def upsert_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, role: str
    ) -> None: ...

    def delete_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str
    ) -> None: ...

    def upsert_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None: ...

    def delete_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None: ...


class InMemoryAuthorizationRepository:
    """Explicit test double; never constructed by production bootstrapping."""

    def __init__(self) -> None:
        self.roles: dict[tuple[str, str, str, str], str] = {}
        self.permissions: set[tuple[str, str, str, str, str]] = set()

    @staticmethod
    def _covered(
        scope: str,
        resource_id: str,
        organization_id: str,
        project_id: str | None,
        version_id: str | None,
    ) -> bool:
        return (
            (scope == ResourceScope.ORGANIZATION.value and resource_id == organization_id)
            or (scope == ResourceScope.PROJECT.value and project_id == resource_id)
            or (scope == ResourceScope.VERSION.value and version_id == resource_id)
        )

    def authorization_grants(
        self,
        tenant_id: str,
        principal_id: str,
        *,
        organization_id: str,
        project_id: str | None,
        version_id: str | None,
    ) -> tuple[set[str], set[str]]:
        roles = {
            role
            for (tenant, principal, scope, resource_id), role in self.roles.items()
            if tenant == tenant_id
            and principal == principal_id
            and self._covered(scope, resource_id, organization_id, project_id, version_id)
        }
        permissions = {
            permission
            for tenant, principal, scope, resource_id, permission in self.permissions
            if tenant == tenant_id
            and principal == principal_id
            and self._covered(scope, resource_id, organization_id, project_id, version_id)
        }
        return roles, permissions

    def upsert_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, role: str
    ) -> None:
        self.roles[(tenant_id, principal_id, scope, resource_id)] = role

    def delete_role_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str
    ) -> None:
        self.roles.pop((tenant_id, principal_id, scope, resource_id), None)

    def upsert_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None:
        self.permissions.add((tenant_id, principal_id, scope, resource_id, permission))

    def delete_permission_assignment(
        self, tenant_id: str, principal_id: str, scope: str, resource_id: str, permission: str
    ) -> None:
        self.permissions.discard((tenant_id, principal_id, scope, resource_id, permission))


class AuthorizationService:
    """Evaluates deny-by-default RBAC over a set of :class:`RoleAssignment` records.

    The service is intentionally storage-agnostic: it keeps assignments in memory
    and exposes ``grant``/``revoke`` so a persistence layer can mirror state.
    Authorization decisions are pure functions of (principal, resource, assignments).
    """

    def __init__(self, repository: AuthorizationRepository) -> None:
        self._repository = repository

    def grant(self, assignment: RoleAssignment) -> None:
        """Add or replace a role assignment for a principal on a resource."""
        self._repository.upsert_role_assignment(
            assignment.tenant_id,
            assignment.principal_id,
            assignment.scope.value,
            assignment.resource_id,
            assignment.role.value,
        )

    def grant_permission(self, assignment: PermissionAssignment) -> None:
        """Grant one atomic permission without requiring a composed role."""
        self._repository.upsert_permission_assignment(
            assignment.tenant_id,
            assignment.principal_id,
            assignment.scope.value,
            assignment.resource_id,
            assignment.permission.value,
        )

    def revoke_permission(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        permission: Permission,
        scope: ResourceScope,
        resource_id: str,
    ) -> None:
        """Revoke one direct atomic permission grant."""
        self._repository.delete_permission_assignment(
            tenant_id, principal_id, scope.value, resource_id, permission.value
        )

    def revoke(
        self,
        *,
        principal_id: str,
        tenant_id: str,
        scope: ResourceScope,
        resource_id: str,
    ) -> None:
        """Remove a role assignment. Silently no-ops if none exists."""
        self._repository.delete_role_assignment(tenant_id, principal_id, scope.value, resource_id)

    def authorize_action(
        self, principal: Principal, action: AuthoringAction, resource: ResourceRef
    ) -> None:
        """Authorize a user-visible authoring action through its canonical atomic permission."""
        self.authorize(principal, ACTION_PERMISSION[action], resource)

    def authorize(
        self, principal: Principal, permission: Permission, resource: ResourceRef
    ) -> None:
        """Raise :class:`AccessDeniedError` unless ``principal`` may perform ``permission``.

        Enforced in order:
          1. Cross-tenant hard deny.
          2. Effective roles computed via hierarchy coverage.
          3. Permission must appear in the union of granted role permissions.
        """
        if not self.is_authorized(principal, permission, resource):
            logger.warning(
                "Authorization denied: principal=%s tenant=%s permission=%s resource=%s",
                principal.id,
                principal.tenant_id,
                permission.value,
                (resource.organization_id, resource.project_id, resource.version_id),
            )
            raise AccessDeniedError("Access denied: insufficient role for this resource.")

    def is_authorized(
        self, principal: Principal, permission: Permission, resource: ResourceRef
    ) -> bool:
        """Return ``True`` iff ``principal`` may perform ``permission`` on ``resource``."""
        # 1. Hard tenant isolation: the principal's tenant must own the resource.
        if principal.tenant_id != resource.tenant_id:
            return False

        role_values, permission_values = self._repository.authorization_grants(
            principal.tenant_id,
            principal.id,
            organization_id=resource.organization_id,
            project_id=resource.project_id,
            version_id=resource.version_id,
        )
        # 2. Direct atomic grants are first-class and inherit through the same hierarchy.
        if permission.value in permission_values:
            return True

        # 3. Roles remain convenience bundles over atomic permissions.
        for role_value in role_values:
            role = Role(role_value)
            if permission in ROLE_PERMISSIONS[role]:
                return True
        return False
