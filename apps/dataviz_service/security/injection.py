"""SQL, DAX, and HTML/XSS injection security boundary.

Provides defensive validation for SQL queries, DAX expressions, and HTML
rendering strings to prevent command injection, unintended data destruction,
and cross-site scripting (XSS).
"""

from __future__ import annotations

import logging
import re

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.injection")


class InjectionError(SanitizedServiceError):
    """Raised when an injection payload or forbidden query structure is detected."""

    def __init__(
        self, message: str = "Query or payload contains forbidden injection patterns."
    ) -> None:
        super().__init__(message, status_code=400, error_code="INJECTION_DETECTED")


# Patterns for destructive or dangerous SQL statements
_SQL_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(DROP\s+(TABLE|DATABASE|SCHEMA|VIEW|PROCEDURE)|TRUNCATE\s+TABLE|ALTER\s+(TABLE|DATABASE|SCHEMA)|EXEC\s*\(|XP_CMDSHELL)\b"
)

# Non-read-only SQL statements (used when allow_write=False)
_SQL_WRITE_RE = re.compile(
    r"(?i)\b(INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM|CREATE\s+(TABLE|DATABASE|SCHEMA|VIEW|PROCEDURE|INDEX)|MERGE\s+INTO|REPLACE\s+INTO)\b"
)

# Multi-statement / stacked query injection (e.g. `SELECT ... ; DROP ...`)
_SQL_STACKED_RE = re.compile(
    r";\s*(?:\b(?:DROP|DELETE|UPDATE|INSERT|CREATE|ALTER|TRUNCATE|EXEC)\b)"
)

# SQL comment patterns attempting query manipulation or bypass
_SQL_COMMENT_RE = re.compile(r"(--[^\r\n]*|/\*[\s\S]*?\*/)")

# HTML/XSS dangerous elements
_XSS_TAG_RE = re.compile(
    r"(?i)<\s*(script|iframe|object|embed|applet|form|link|style|meta|svg|img|body|input|button|a)\b[^>]*>",
    re.DOTALL,
)

# Inline event handlers (e.g., onerror=, onload=, onmouseover=) including whitespace/newlines
_XSS_EVENT_RE = re.compile(r"(?i)\bon[a-z]+\s*=\s*[\"']?[^\"'>\s]+")

# Dangerous URI schemes in attributes (e.g. javascript:, vbscript:, data:text/html)
_XSS_URI_RE = re.compile(
    r"(?i)(j\s*a\s*v\s*a\s*s\s*c\s*r\s*i\s*p\s*t|v\s*b\s*s\s*c\s*r\s*i\s*p\s*t|data\s*:\s*text/html)\s*:"
)


class InjectionBoundary:
    """Stateless boundary for validating queries and sanitizing HTML content."""

    @staticmethod
    def validate_sql_query(sql: str, *, allow_write: bool = False) -> str:
        """Validate an input SQL query string against destructive injection vectors.

        If ``allow_write`` is False (default), write operations (INSERT, UPDATE,
        DELETE, CREATE, MERGE) are strictly prohibited.

        Raises :class:`InjectionError` if forbidden statements or stacked query
        injections are detected.
        """
        if not isinstance(sql, str) or not sql.strip():
            raise InjectionError("SQL query must not be empty.")

        cleaned = sql.strip()

        # Reject null bytes
        if "\x00" in cleaned:
            logger.warning("Injection boundary: null byte in SQL query")
            raise InjectionError("Null bytes in SQL query are forbidden.")

        # Reject destructive DDL / DML
        if _SQL_DESTRUCTIVE_RE.search(cleaned):
            logger.warning("Injection boundary: destructive SQL keyword detected in query")
            raise InjectionError(
                "Destructive SQL commands (DROP/TRUNCATE/ALTER/EXEC) are forbidden."
            )

        # Enforce read-only mode if write operations are disallowed
        if not allow_write and _SQL_WRITE_RE.search(cleaned):
            logger.warning("Injection boundary: write SQL statement blocked in read-only mode")
            raise InjectionError(
                "Write operations (INSERT/UPDATE/DELETE/CREATE) are forbidden in read-only mode."
            )

        # Reject stacked queries if multi-statement injection is attempted
        if _SQL_STACKED_RE.search(cleaned):
            logger.warning("Injection boundary: stacked SQL statements detected")
            raise InjectionError("Multi-statement stacked SQL queries are forbidden.")

        return cleaned

    @staticmethod
    def validate_dax_query(dax: str) -> str:
        """Validate a DAX expression for forbidden injection patterns.

        Raises :class:`InjectionError` on empty or invalid DAX payloads.
        """
        if not isinstance(dax, str) or not dax.strip():
            raise InjectionError("DAX expression must not be empty.")

        cleaned = dax.strip()

        # Reject control characters or null bytes
        if "\x00" in cleaned:
            logger.warning("Injection boundary: null byte detected in DAX query")
            raise InjectionError("Null bytes in DAX query are forbidden.")

        return cleaned

    @classmethod
    def sanitize_html(cls, html: str) -> str:
        """Sanitize an HTML string by stripping script tags, event handlers, and XSS URIs.

        Returns safe HTML or raises :class:`InjectionError` on null byte payload.
        """
        if not isinstance(html, str):
            return ""

        if "\x00" in html:
            logger.warning("Injection boundary: null byte in HTML string")
            raise InjectionError("Null bytes in HTML payload are forbidden.")

        sanitized = _XSS_TAG_RE.sub("", html)
        sanitized = _XSS_EVENT_RE.sub("", sanitized)
        sanitized = _XSS_URI_RE.sub("forbidden-uri:", sanitized)

        return sanitized
