"""OIDC identity boundary for the DataVIZ SaaS service.

This module owns the complete OIDC boundary. :class:`JWTVerifier` validates the
cryptographic signature against startup-configured JWKS and then delegates the
semantic claim checks to :class:`OIDCBoundary`:

  * `iss` MUST be one of the configured trusted issuers.
  * `aud` MUST intersect the configured trusted audiences.
  * `exp` MUST NOT be in the past (within an allowed clock-skew window).
  * `nbf`, when present, MUST have elapsed.
  * `iat`, when present, MUST NOT be dated in the future beyond the skew window.
  * `sub` and the private `tenant_id` claim MUST be present and non-empty.

A successfully validated claim set yields an immutable :class:`Principal` that the
authorization layer can rely on. Any boundary violation raises
:class:`OIDCTokenError`, a controlled :class:`SanitizedServiceError` (HTTP 401).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.dataviz_service.contracts import SanitizedServiceError, Scope

logger = logging.getLogger("dataviz_service.security.identity")

# Mapping from OIDC `scope` tokens to the service's coarse Scope enum.
# Unknown tokens are ignored (the coarse scope layer remains deny-by-default).
_SCOPE_TOKEN_TO_ENUM: dict[str, Scope] = {
    "dataviz:read": Scope.READ,
    "dataviz:write": Scope.WRITE,
    "jobs:run": Scope.RUN,
    "admin:all": Scope.ADMIN,
}

#: Algorithms explicitly forbidden in OIDC JWT headers to prevent algorithm confusion attacks.
FORBIDDEN_ALGORITHMS: set[str] = {"none", "NONE", "None"}


class OIDCTokenError(SanitizedServiceError):
    """Raised when an OIDC token's claims violate the configured trust boundary."""

    def __init__(self, message: str = "OIDC token failed boundary validation.") -> None:
        super().__init__(message, status_code=401, error_code="OIDC_TOKEN_INVALID")


class JWTVerifier:
    """Verifica criptográficamente JWT OIDC contra JWKS configurados.

    Los JWKS se cargan al iniciar el proceso; ninguna URL controlada por una
    solicitud llega a esta frontera. Esto evita convertir la autenticación en
    un cliente SSRF y permite rotación atómica de configuración.
    """

    def __init__(
        self,
        *,
        jwks_by_issuer: Mapping[str, Mapping[str, Any]],
        trusted_audiences: set[str],
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        clock_skew_seconds: int = 30,
    ) -> None:
        if not jwks_by_issuer:
            raise ValueError("jwks_by_issuer must not be empty")
        if not trusted_audiences:
            raise ValueError("trusted_audiences must not be empty")
        for alg in algorithms:
            if alg in FORBIDDEN_ALGORITHMS or alg.upper().startswith("HS"):
                raise ValueError(f"Algorithm '{alg}' is explicitly forbidden.")

        self._keys = {
            issuer: tuple(jwt.PyJWKSet.from_dict(dict(jwks)).keys)
            for issuer, jwks in jwks_by_issuer.items()
        }
        self._audiences = frozenset(trusted_audiences)
        self._algorithms = tuple(algorithms)
        self._leeway = clock_skew_seconds

    def verify(self, token: str) -> Principal:
        """Verifica firma/claims y devuelve el principal autenticado."""
        if not isinstance(token, str) or not token.strip():
            raise OIDCTokenError("Token string must not be empty.")

        raw_token = token.strip()

        try:
            header = jwt.get_unverified_header(raw_token)
            alg = header.get("alg")
            if (
                not isinstance(alg, str)
                or alg in FORBIDDEN_ALGORITHMS
                or alg not in self._algorithms
            ):
                logger.warning("OIDC JWT algorithm not permitted: %s", alg)
                raise OIDCTokenError()

            unverified = jwt.decode(raw_token, options={"verify_signature": False})
            issuer = unverified.get("iss")
            if not isinstance(issuer, str) or not issuer or issuer not in self._keys:
                logger.warning("OIDC boundary: untrusted or missing issuer in unverified token")
                raise OIDCTokenError()

            keys = self._keys.get(issuer, ())
            kid = header.get("kid")
            if kid is not None:
                if not isinstance(kid, str):
                    logger.warning(
                        "OIDC boundary: invalid kid header type '%s'", type(kid).__name__
                    )
                    raise OIDCTokenError()
                candidates = [key for key in keys if key.key_id == kid]
            else:
                candidates = list(keys)

            if len(candidates) != 1:
                logger.warning(
                    "OIDC key lookup mismatch for issuer '%s', kid '%s': found %d candidates",
                    issuer,
                    kid,
                    len(candidates),
                )
                raise OIDCTokenError()

            claims = jwt.decode(
                raw_token,
                key=candidates[0].key,
                algorithms=list(self._algorithms),
                audience=list(self._audiences),
                issuer=issuer,
                leeway=self._leeway,
                options={"require": ["sub", "iss", "aud", "exp", "tenant_id"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            logger.warning("OIDC JWT signature validation failed: %s", type(exc).__name__)
            raise OIDCTokenError() from exc

        normalized = dict(claims)
        for name in ("exp", "nbf", "iat"):
            value = normalized.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                normalized[name] = datetime.utcfromtimestamp(value)
        boundary = OIDCBoundary(
            trusted_issuers={issuer},
            trusted_audiences=set(self._audiences),
            clock_skew_seconds=self._leeway,
        )
        return boundary.validate(normalized)


class Principal(BaseModel):
    """Authenticated identity produced by a validated OIDC token.

    Attributes:
        id: Subject identifier (`sub`).
        tenant_id: Tenant boundary the principal belongs to.
        issuer: Token issuer (`iss`) that vouched for this identity.
        audiences: Audiences (`aud`) the token was issued for.
        scopes: Coarse API scopes parsed from the `scope` claim.
        roles: Identity-provider roles (informational; app RBAC is separate).
        email: Optional email claim.
        expires_at: Token expiry timestamp.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., description="Subject identifier (sub)")
    tenant_id: str = Field(..., description="Tenant boundary of the principal")
    issuer: str = Field(..., description="Token issuer")
    audiences: frozenset[str] = Field(..., description="Token audiences")
    scopes: frozenset[Scope] = Field(default_factory=frozenset, description="Coarse API scopes")
    roles: frozenset[str] = Field(
        default_factory=frozenset, description="IdP roles (informational)"
    )
    email: str | None = Field(default=None, description="Optional email claim")
    expires_at: datetime = Field(..., description="Token expiry timestamp")

    @field_validator("id", "tenant_id", "issuer")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Identifier field must not be empty")
        return v.strip()


class OIDCBoundary:
    """Validates raw OIDC claim sets against a trusted issuer/audience configuration.

    This class performs the semantic half of validation and is also useful in
    isolated tests. Production entrypoints must use :class:`JWTVerifier`, which
    performs signature verification before invoking this boundary.
    """

    def __init__(
        self,
        *,
        trusted_issuers: set[str],
        trusted_audiences: set[str],
        clock_skew_seconds: int = 0,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        if not trusted_issuers:
            raise ValueError("trusted_issuers must not be empty")
        if not trusted_audiences:
            raise ValueError("trusted_audiences must not be empty")
        if clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must not be negative")
        self._trusted_issuers = frozenset(trusted_issuers)
        self._trusted_audiences = frozenset(trusted_audiences)
        self._skew = timedelta(seconds=clock_skew_seconds)
        self._clock = clock

    def validate(self, claims: Mapping[str, Any]) -> Principal:
        """Validate ``claims`` and return a :class:`Principal`.

        Raises :class:`OIDCTokenError` on any boundary violation.
        """
        sub = self._require_string(claims, "sub")
        iss = self._require_string(claims, "iss")
        aud_raw = claims.get("aud")
        exp_raw = claims.get("exp")
        tenant_id_raw = claims.get("tenant_id")

        # --- Issuer boundary ---
        if iss not in self._trusted_issuers:
            logger.warning("OIDC boundary: untrusted issuer '%s'", iss)
            raise OIDCTokenError()

        # --- Audience boundary ---
        audiences = self._coerce_audiences(aud_raw)
        if not (audiences & self._trusted_audiences):
            logger.warning("OIDC boundary: audience mismatch (%s)", sorted(audiences))
            raise OIDCTokenError()

        # --- Temporal boundary ---
        now = self._clock()
        exp = self._require_datetime(exp_raw, "exp")
        if exp + self._skew < now:
            logger.warning("OIDC boundary: token expired (exp=%s, now=%s)", exp, now)
            raise OIDCTokenError()

        nbf_raw = claims.get("nbf")
        if nbf_raw is not None:
            nbf = self._coerce_datetime(nbf_raw, "nbf")
            if now + self._skew < nbf:
                logger.warning("OIDC boundary: token not yet valid (nbf=%s, now=%s)", nbf, now)
                raise OIDCTokenError()

        iat_raw = claims.get("iat")
        if iat_raw is not None:
            iat = self._coerce_datetime(iat_raw, "iat")
            if iat > now + self._skew:
                logger.warning(
                    "OIDC boundary: token issued in the future (iat=%s, now=%s)", iat, now
                )
                raise OIDCTokenError()

        # --- Tenant claim boundary ---
        if not isinstance(tenant_id_raw, str) or not tenant_id_raw.strip():
            logger.warning("OIDC boundary: missing or empty tenant_id claim")
            raise OIDCTokenError()
        tenant_id = tenant_id_raw.strip()

        scopes = self._parse_scopes(claims.get("scope"))
        roles = self._parse_roles(claims.get("roles"))
        email = claims.get("email")
        if email is not None and not isinstance(email, str):
            email = None

        return Principal(
            id=sub.strip(),
            tenant_id=tenant_id,
            issuer=iss,
            audiences=audiences,
            scopes=scopes,
            roles=roles,
            email=email,
            expires_at=exp,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_string(claims: Mapping[str, Any], key: str) -> str:
        v = claims.get(key)
        if not isinstance(v, str) or not v.strip():
            raise OIDCTokenError(f"OIDC boundary: missing or empty '{key}' claim.")
        return v

    @staticmethod
    def _coerce_audiences(aud_raw: Any) -> frozenset[str]:
        if isinstance(aud_raw, str) and aud_raw.strip():
            return frozenset({aud_raw.strip()})
        if isinstance(aud_raw, list) and aud_raw:
            values = {a.strip() for a in aud_raw if isinstance(a, str) and a.strip()}
            if values:
                return frozenset(values)
        raise OIDCTokenError("OIDC boundary: missing or empty 'aud' claim.")

    @staticmethod
    def _require_datetime(value: Any, key: str) -> datetime:
        if isinstance(value, datetime):
            return value
        raise OIDCTokenError(f"OIDC boundary: '{key}' claim must be a datetime.")

    @classmethod
    def _coerce_datetime(cls, value: Any, key: str) -> datetime:
        return cls._require_datetime(value, key)

    @staticmethod
    def _parse_scopes(scope_raw: Any) -> frozenset[Scope]:
        if not isinstance(scope_raw, str):
            return frozenset()
        mapped = {
            _SCOPE_TOKEN_TO_ENUM[token]
            for token in scope_raw.split()
            if token in _SCOPE_TOKEN_TO_ENUM
        }
        return frozenset(mapped)

    @staticmethod
    def _parse_roles(roles_raw: Any) -> frozenset[str]:
        if isinstance(roles_raw, list):
            return frozenset(r.strip() for r in roles_raw if isinstance(r, str) and r.strip())
        if isinstance(roles_raw, str) and roles_raw.strip():
            return frozenset(r.strip() for r in roles_raw.split() if r.strip())
        return frozenset()
