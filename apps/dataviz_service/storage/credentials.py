"""Ephemeral credential resolver for backend authentication.

Long-term storage secrets (root account keys, permanent tokens) live outside the
service and are never loaded into it. When the service needs to authenticate to
a backend, it asks a :class:`CredentialResolver` for
:class:`EphemeralCredentials` that are short-lived and tenant/role scoped. These
credentials flow only between the resolver and the server-side backend client;
they are never logged, never embedded in the migration intermediate
representation, and never sent to the browser. The browser-facing artifact is
always a :class:`~apps.dataviz_service.storage.access.SignedAccess` grant.

Secret hygiene is enforced structurally, not by convention:

  * :class:`EphemeralCredentials` marks ``secret_access_key`` and
    ``session_token`` with ``repr=False`` and overrides ``__repr__``/``__str__``
    to a fully redacted form, so default logging and error rendering cannot leak
    the secret.
  * The resolver logs only ``tenant_id`` / ``scope`` / ``expires_at`` — never
    the secret or the session token.
  * :class:`StaticCredentialResolver` only ever returns the
    :class:`EphemeralCredentials` it was constructed with (with the expiry
    clamped to the requested ttl and the resolver's max ttl). It never holds or
    returns a long-term secret.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from apps.dataviz_service.contracts import SanitizedServiceError
from apps.dataviz_service.security.authorization import ResourceScope

logger = logging.getLogger("dataviz_service.storage.credentials")

_DEFAULT_MAX_TTL_SECONDS = 3600


class CredentialResolverError(SanitizedServiceError):
    """Raised when ephemeral credentials cannot be resolved or violate policy."""

    def __init__(self, message: str = "Credential resolution failed.") -> None:
        super().__init__(message, status_code=503, error_code="CREDENTIAL_RESOLVE_FAILED")


class EphemeralCredentials(BaseModel):
    """Short-lived backend credentials with secret fields redacted from repr.

    ``secret_access_key`` and ``session_token`` are the secret-bearing fields.
    They are marked ``repr=False`` and the model overrides ``__repr__`` and
    ``__str__`` to a redacted form so credentials cannot leak through default
    logging or string formatting. Programmatic access via ``model_dump()`` is
    intentionally not redacted: server-side backend clients must read the actual
    secret value to authenticate.
    """

    model_config = ConfigDict(frozen=True)

    access_key_id: str = Field(..., description="Short-lived public access key id")
    secret_access_key: str = Field(
        ..., repr=False, description="Short-lived secret; redacted from repr"
    )
    session_token: str | None = Field(
        default=None, repr=False, description="Short-lived session token; redacted from repr"
    )
    expires_at: datetime = Field(..., description="Absolute expiry of the credentials")

    def __repr__(self) -> str:
        return (
            f"EphemeralCredentials(access_key_id={self.access_key_id!r}, "
            f"expires_at={self.expires_at!r}, **redacted**)"
        )

    def __str__(self) -> str:
        return self.__repr__()


@runtime_checkable
class CredentialResolver(Protocol):
    """Interface for resolvers that produce ttl-clamped ephemeral credentials.

    Implementations wrap a real STS / credential broker. Resolved credentials
    are scoped to ``(tenant_id, scope)`` and must never outlive the resolver's
    configured maximum ttl.
    """

    def resolve(
        self,
        *,
        tenant_id: str,
        scope: ResourceScope,
        ttl_seconds: int,
    ) -> EphemeralCredentials:
        """Return short-lived credentials for ``tenant_id`` / ``scope``."""
        ...


class StaticCredentialResolver:
    """Test double that hands out pre-built ephemeral credentials.

    It never holds a long-term secret: it only ever returns the
    :class:`EphemeralCredentials` it was constructed with, with the expiry
    clamped to the minimum of the requested ttl and the resolver's max ttl.
    Use only in tests; production code depends on the
    :class:`CredentialResolver` protocol, never on this class.
    """

    def __init__(
        self,
        credentials: EphemeralCredentials,
        *,
        max_ttl_seconds: int = _DEFAULT_MAX_TTL_SECONDS,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        if max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be positive")
        self._creds = credentials
        self._max_ttl = timedelta(seconds=max_ttl_seconds)
        self._clock = clock

    def resolve(
        self,
        *,
        tenant_id: str,
        scope: ResourceScope,
        ttl_seconds: int,
    ) -> EphemeralCredentials:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise CredentialResolverError("tenant_id must not be empty")
        if ttl_seconds <= 0:
            raise CredentialResolverError("ttl_seconds must be positive")
        now = self._clock()
        # Clamp: never grant longer than the caller asked for, and never longer
        # than the resolver's hard maximum.
        clamped = min(timedelta(seconds=ttl_seconds), self._max_ttl)
        expires_at = min(self._creds.expires_at, now + clamped)
        if expires_at <= now:
            raise CredentialResolverError("Source ephemeral credentials have expired.")
        resolved = self._creds.model_copy(update={"expires_at": expires_at})
        logger.info(
            "Resolved ephemeral credentials: tenant=%s scope=%s expires=%s",
            tenant_id,
            scope.value,
            expires_at.isoformat(),
        )
        return resolved
