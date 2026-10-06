"""SSRF (Server-Side Request Forgery) protection boundary.

Validates external destination URLs before outgoing requests are initiated.
Rejects loopback, private RFC-1918, link-local, multicast, broadcast, and cloud
metadata addresses (e.g., 169.254.169.254), as well as unauthorized URL schemes.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.ssrf")


class SSRFError(SanitizedServiceError):
    """Raised when a URL violates the SSRF security boundary."""

    def __init__(self, message: str = "URL violates SSRF security boundary.") -> None:
        super().__init__(message, status_code=400, error_code="SSRF_VIOLATION")


# Blocked IPv4 / IPv6 network ranges
BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),  # Unique local
    ipaddress.ip_network("fe80::/10"),  # Link-local
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6 block
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 block
)

BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "loopback",
    "0",
    "0.0.0.0",
    "metadata.google.internal",
    "169.254.169.254",
    "metadata",
    "instance-data",
}


class SSRFBoundary:
    """Stateless validator for outgoing HTTP request targets."""

    def __init__(
        self,
        *,
        allowed_schemes: tuple[str, ...] = ("http", "https"),
        allowed_domains: set[str] | None = None,
        allow_dns_resolution: bool = False,
    ) -> None:
        self._allowed_schemes = set(s.lower() for s in allowed_schemes)
        self._allowed_domains = set(d.lower() for d in allowed_domains) if allowed_domains else None
        self._allow_dns = allow_dns_resolution

    def validate_url(self, url: str) -> str:
        """Validate ``url`` against SSRF rules. Returns clean URL or raises :class:`SSRFError`."""
        if not isinstance(url, str) or not url.strip():
            raise SSRFError("URL must not be empty.")

        cleaned_url = url.strip()
        parsed = urlparse(cleaned_url)

        if parsed.scheme.lower() not in self._allowed_schemes:
            logger.warning("SSRF boundary: forbidden URL scheme '%s'", parsed.scheme)
            raise SSRFError(f"Forbidden URL scheme '{parsed.scheme}'.")

        hostname = parsed.hostname
        if not hostname:
            raise SSRFError("URL hostname could not be parsed.")

        # Normalize hostname by stripping trailing dots and lowercasing
        hostname_lower = hostname.strip().rstrip(".").lower()

        if (
            hostname_lower in BLOCKED_HOSTNAMES
            or hostname_lower.endswith(".local")
            or hostname_lower.endswith(".internal")
        ):
            logger.warning("SSRF boundary: blocked hostname '%s'", hostname_lower)
            raise SSRFError("Forbidden destination host.")

        # Check explicit domain allowlist if configured
        if self._allowed_domains is not None:
            if not any(
                hostname_lower == domain or hostname_lower.endswith(f".{domain}")
                for domain in self._allowed_domains
            ):
                logger.warning("SSRF boundary: hostname '%s' not in allowlist", hostname_lower)
                raise SSRFError("Host not permitted by destination allowlist.")

        # Check IP address directly
        try:
            ip_obj = ipaddress.ip_address(hostname_lower)
            self._check_ip(ip_obj)
        except ValueError:
            # Hostname is a domain name, resolve DNS if configured
            if self._allow_dns:
                self._resolve_and_check_dns(hostname_lower)

        return cleaned_url

    def _check_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        """Verify IP address does not lie in any blocked network range."""
        # Unwrap IPv4-mapped IPv6 address (e.g., ::ffff:127.0.0.1 -> 127.0.0.1)
        effective_ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            effective_ip = ip.ipv4_mapped

        for network in BLOCKED_NETWORKS:
            # Check matching address family
            if effective_ip.version == network.version:
                if effective_ip in network:
                    logger.warning(
                        "SSRF boundary: IP address '%s' in blocked network '%s'",
                        effective_ip,
                        network,
                    )
                    raise SSRFError(f"Destination IP '{effective_ip}' is in a restricted range.")
            elif isinstance(ip, ipaddress.IPv6Address) and network.version == 6:
                if ip in network:
                    logger.warning(
                        "SSRF boundary: IPv6 address '%s' in blocked network '%s'", ip, network
                    )
                    raise SSRFError(f"Destination IP '{ip}' is in a restricted range.")

    def _resolve_and_check_dns(self, hostname: str) -> None:
        """Resolve DNS and check that none of the resolved IPs are in blocked ranges."""
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                ip_obj = ipaddress.ip_address(ip_str)
                self._check_ip(ip_obj)
        except socket.gaierror as exc:
            logger.warning("SSRF boundary: DNS resolution failed for '%s'", hostname)
            raise SSRFError("DNS resolution failed for target host.") from exc
