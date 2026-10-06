"""Signed access boundary for artifact storage.

Clients never receive long-lived storage credentials and never receive the HMAC
signing key. Instead, the service issues a :class:`SignedAccess` grant scoped to
a single ``(tenant, object_key, method)`` triple with a hard expiry, integrity-
protected by an HMAC that only the issuer/verifier hold privately. Such a grant
is safe to hand to a browser because:

  * it authorizes exactly one operation on exactly one object;
  * it stops working at a known absolute expiry;
  * it cannot be forged or extended without the private signing key;
  * the signing key is never present on the :class:`SignedAccess` object and is
    never written to logs.

This is the only artifact the storage layer ever returns to a browser-facing
caller. Ephemeral backend credentials (see
:mod:`apps.dataviz_service.storage.credentials`) are resolved server-side and
never reach the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.storage.access")

_DEFAULT_MAX_TTL_SECONDS = 3600


class AccessMethod(StrEnum):
    """Operation a signed grant permits."""

    READ = "read"
    WRITE = "write"


class SignedAccessError(SanitizedServiceError):
    """Raised when a signed access grant fails verification."""

    def __init__(self, message: str = "Signed access grant is invalid or expired.") -> None:
        super().__init__(message, status_code=403, error_code="SIGNED_ACCESS_INVALID")


class SignedAccess(BaseModel):
    """A scoped, time-limited, integrity-protected access grant.

    ``token`` carries no secret: it encodes ``(tenant_id, object_key, method,
    expires_at)`` plus an HMAC tag computed with the issuer's private key. The
    signing key itself never appears on this object, is never serialized into
    ``model_dump``, and must never be logged.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Tenant the grant is bound to")
    object_key: str = Field(..., description="Object the grant addresses")
    method: AccessMethod = Field(..., description="Operation the grant permits")
    expires_at: datetime = Field(..., description="Absolute expiry of the grant")
    token: str = Field(..., description="Opaque HMAC-signed grant token (no secret embedded)")


class SignedAccessIssuer:
    """Issues and verifies :class:`SignedAccess` grants using a private HMAC key.

    The ``signing_key`` never leaves this object: it is not stored on issued
    grants, not returned to callers, and not written to logs. Logs only carry
    the non-secret ``tenant_id`` / ``method`` / ``object_key`` / ``expires_at``.
    """

    def __init__(
        self,
        *,
        signing_key: bytes,
        clock: Callable[[], datetime] = datetime.utcnow,
        max_ttl_seconds: int = _DEFAULT_MAX_TTL_SECONDS,
    ) -> None:
        if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) < 16:
            raise ValueError("signing_key must be bytes of at least 16 bytes")
        if max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be positive")
        self._key = bytes(signing_key)
        self._clock = clock
        self._max_ttl = timedelta(seconds=max_ttl_seconds)

    def issue(
        self,
        *,
        tenant_id: str,
        object_key: str,
        method: AccessMethod,
        expires_at: datetime,
    ) -> SignedAccess:
        """Issue a grant. Raises :class:`SignedAccessError` for non-future or excessive ttl."""
        now = self._clock()
        if expires_at <= now:
            raise SignedAccessError("Signed access expiry must be in the future.")
        if expires_at - now > self._max_ttl:
            raise SignedAccessError(
                f"Signed access ttl exceeds maximum of {int(self._max_ttl.total_seconds())}s."
            )
        token = self._sign(tenant_id, object_key, method, expires_at)
        logger.info(
            "Issued signed access: tenant=%s method=%s object=%s expires=%s",
            tenant_id,
            method.value,
            object_key,
            expires_at.isoformat(),
        )
        return SignedAccess(
            tenant_id=tenant_id,
            object_key=object_key,
            method=method,
            expires_at=expires_at,
            token=token,
        )

    def verify(self, access: SignedAccess) -> None:
        """Raise :class:`SignedAccessError` unless ``access`` is currently valid."""
        now = self._clock()
        if access.expires_at <= now:
            raise SignedAccessError("Signed access grant has expired.")
        expected = self._sign(access.tenant_id, access.object_key, access.method, access.expires_at)
        # Constant-time comparison to avoid timing oracles on the tag.
        if not hmac.compare_digest(expected, access.token):
            raise SignedAccessError("Signed access token signature mismatch.")
        logger.info(
            "Verified signed access: tenant=%s method=%s object=%s",
            access.tenant_id,
            access.method.value,
            access.object_key,
        )

    def _sign(
        self,
        tenant_id: str,
        object_key: str,
        method: AccessMethod,
        expires_at: datetime,
    ) -> str:
        # The signing key is used here and ONLY here. It is never concatenated
        # into the token payload, never returned, and never logged.
        payload = "\n".join([tenant_id, object_key, method.value, expires_at.isoformat()]).encode(
            "utf-8"
        )
        payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii")
        tag = hmac.new(self._key, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{payload_b64}.{tag}"
