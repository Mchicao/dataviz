"""Canonical atomic permissions used by DataVIZ authoring and service guards."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Atomic actions. Roles are convenience bundles; authorization is permission-based."""

    # Backward-compatible coarse service permissions.
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    MANAGE = "manage"

    # Gate-1 authoring permissions (D020).
    CONSUME = "consume"
    AUTHOR = "author"
    REQUEST_PUBLISH = "request_publish"
    PUBLISH = "publish"
    REVERT = "revert"
    MANAGE_CONNECTIONS = "manage_connections"
    MANAGE_SECURITY = "manage_security"
    MANAGE_RLS = "manage_rls"


class AuthoringAction(StrEnum):
    """User-visible actions protected by Gate-1 atomic permissions."""

    CONSUME = "consume"
    AUTHOR = "author"
    REQUEST_PUBLISH = "request_publish"
    PUBLISH = "publish"
    REVERT = "revert"
    MANAGE_CONNECTIONS = "manage_connections"
    MANAGE_SECURITY = "manage_security"
    MANAGE_RLS = "manage_rls"


ACTION_PERMISSION: dict[AuthoringAction, Permission] = {
    action: Permission(action.value) for action in AuthoringAction
}

AUTHORING_PERMISSIONS: frozenset[Permission] = frozenset({
    Permission.CONSUME,
    Permission.AUTHOR,
    Permission.REQUEST_PUBLISH,
    Permission.PUBLISH,
    Permission.REVERT,
    Permission.MANAGE_CONNECTIONS,
    Permission.MANAGE_SECURITY,
    Permission.MANAGE_RLS,
})

__all__ = ["ACTION_PERMISSION", "AUTHORING_PERMISSIONS", "AuthoringAction", "Permission"]
