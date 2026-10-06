"""Row-Level Security (RLS) contract validator boundary for DataVIZ.

Verifies that dataset RLS roles, DAX filters, and SQL predicates enforce strict
tenant isolation boundaries and do not bypass tenant binding parameters.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.rls")


class RLSContractError(SanitizedServiceError):
    """Raised when an RLS role or predicate expression violates security contracts."""

    def __init__(self, message: str = "Row-Level Security contract violation detected.") -> None:
        super().__init__(message, status_code=400, error_code="RLS_CONTRACT_VIOLATION")


# DAX functions commonly used for identity-driven RLS
_SAFE_RLS_DAX_FUNCTIONS = re.compile(
    r"(?i)\b(USERPRINCIPALNAME|USERNAME|CUSTOMDATA|USEROBJECTID)\s*\(\s*\)"
)

# Detect static true predicates or comment overrides (e.g., `1=1`, `TRUE()`, `TRUE`, `--`, `/*`, `//`)
_BYPASS_PREDICATE_RE = re.compile(
    r"(?i)\b(1\s*=\s*1|0\s*=\s*0|TRUE\s*\(\s*\)|\bTRUE\b|'a'\s*=\s*'a'|'\w+'\s*=\s*'\w+'|\d+\s*=\s*\d+)\b|--|/\*|//"
)


class RLSRoleContract(BaseModel):
    """Contract definition for a Row-Level Security role."""

    model_config = ConfigDict(frozen=True)

    role_name: str = Field(..., description="Name of the security role")
    table_name: str = Field(..., description="Target table for the security filter")
    filter_expression: str = Field(..., description="DAX or SQL filter predicate")
    tenant_key_column: str = Field(default="tenant_id", description="Target tenant column name")


class RLSContractValidator:
    """Stateless validator for DataVIZ RLS contract definitions and expressions."""

    @classmethod
    def validate_role_contract(cls, contract: RLSRoleContract, expected_tenant_id: str) -> None:
        """Validate an RLS role contract for tenant isolation and expression safety.

        Raises :class:`RLSContractError` if the contract is invalid or unsafe.
        """
        if not expected_tenant_id or not expected_tenant_id.strip():
            raise RLSContractError("Expected tenant ID must not be empty.")

        expr = contract.filter_expression.strip()

        if not expr:
            raise RLSContractError(
                f"RLS role '{contract.role_name}' filter expression cannot be empty."
            )

        # Reject null bytes
        if "\x00" in expr:
            logger.warning(
                "RLS boundary: null byte in filter expression for role '%s'", contract.role_name
            )
            raise RLSContractError("Null bytes in RLS filter expression are forbidden.")

        # Check for bypass predicates
        if _BYPASS_PREDICATE_RE.search(expr):
            logger.warning(
                "RLS boundary: bypass predicate detected in role '%s'", contract.role_name
            )
            raise RLSContractError(
                f"RLS role '{contract.role_name}' contains illegal bypass predicate."
            )

        # Ensure tenant column is referenced in filter expression
        col_pattern = re.compile(re.escape(contract.tenant_key_column), re.IGNORECASE)
        if not col_pattern.search(expr):
            logger.warning(
                "RLS boundary: tenant column '%s' not referenced in role '%s'",
                contract.tenant_key_column,
                contract.role_name,
            )
            raise RLSContractError(
                f"RLS role '{contract.role_name}' must reference tenant column '{contract.tenant_key_column}'."
            )

    @classmethod
    def validate_sql_tenant_predicate(
        cls, sql_predicate: str, tenant_column: str = "tenant_id"
    ) -> str:
        """Validate a SQL RLS predicate clause for tenant filtering.

        Returns clean predicate string or raises :class:`RLSContractError`.
        """
        if not isinstance(sql_predicate, str) or not sql_predicate.strip():
            raise RLSContractError("SQL predicate must not be empty.")

        cleaned = sql_predicate.strip()

        if _BYPASS_PREDICATE_RE.search(cleaned):
            logger.warning("RLS boundary: SQL predicate contains bypass pattern")
            raise RLSContractError("Bypass pattern (1=1 / comments) detected in SQL RLS predicate.")

        col_pattern = re.compile(re.escape(tenant_column), re.IGNORECASE)
        if not col_pattern.search(cleaned):
            raise RLSContractError(f"SQL predicate must filter by '{tenant_column}'.")

        return cleaned
