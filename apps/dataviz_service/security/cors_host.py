"""CORS and Host header allowlist validation security boundary.

Protects against host header injection, HTTP response splitting via CRLF in headers,
and unauthorized cross-origin requests.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.cors_host")


class CorsHostError(SanitizedServiceError):
    """Raised when an Origin or Host header violates allowlist rules."""

    def __init__(self, message: str = "Origin or Host header is not permitted.") -> None:
        super().__init__(message, status_code=403, error_code="INVALID_CORS_OR_HOST")


class CorsHostBoundary:
    """Stateless validator for CORS origins and Host headers."""

    @staticmethod
    def validate_origin(origin: str, allowed_origins: Sequence[str]) -> str:
        """Validate an HTTP ``Origin`` header against ``allowed_origins``.

        Raises :class:`CorsHostError` if the origin is invalid or not allowed.
        """
        if not isinstance(origin, str) or not origin.strip():
            raise CorsHostError("Origin header must not be empty.")

        cleaned = origin.strip()

        # Reject CRLF characters to prevent HTTP header injection / response splitting
        if "\r" in cleaned or "\n" in cleaned:
            logger.warning("CorsHost boundary: CRLF detected in Origin header")
            raise CorsHostError("CRLF characters in header are forbidden.")

        valid_set = set(allowed_origins)

        if cleaned not in valid_set:
            logger.warning(
                "CorsHost boundary: Origin '%s' not in allowed set %s", cleaned, valid_set
            )
            raise CorsHostError("Origin is not allowed by CORS policy.")

        return cleaned

    @staticmethod
    def validate_host(host: str, allowed_hosts: Sequence[str]) -> str:
        """Validate an HTTP ``Host`` header against ``allowed_hosts``.

        Raises :class:`CorsHostError` if the host header is missing, invalid, or unallowed.
        """
        if not isinstance(host, str) or not host.strip():
            raise CorsHostError("Host header must not be empty.")

        cleaned = host.strip()

        if "\r" in cleaned or "\n" in cleaned:
            logger.warning("CorsHost boundary: CRLF detected in Host header")
            raise CorsHostError("CRLF characters in Host header are forbidden.")

        # Reject userinfo '@' or URI components in Host header
        if "@" in host or "/" in host or "\\" in host or " " in host or "\t" in host:
            logger.warning(
                "CorsHost boundary: malformed or injection payload in Host header '%s'", host
            )
            raise CorsHostError("Host header contains forbidden characters (@, /, \\, or space).")

        cleaned = host.strip()

        # Strip port number for hostname comparison if present
        host_no_port = cleaned.split(":")[0].lower() if ":" in cleaned else cleaned.lower()
        valid_set = set(h.lower() for h in allowed_hosts)

        if host_no_port not in valid_set and cleaned.lower() not in valid_set:
            logger.warning("CorsHost boundary: Host '%s' not in trusted host set", cleaned)
            raise CorsHostError("Host header is not in the trusted host list.")

        return cleaned
